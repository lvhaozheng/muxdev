"""Stage commit, contract repair, and runtime-owned evidence observations."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from ..core.redaction import redact
from ..domain import CapabilityGrant, StageExecutionResult
from ..models import ExecutedCheck, VerificationCommand, WorkflowDefinition
from ..models.evidence import (
    ArtifactEvidence,
    CheckEvidence,
    ReviewEvidence,
    ReviewFinding,
    RuntimeEvidence,
    canonical_hash,
)
from ..services.router import ProviderRouter
from .recovery import WorkspaceCheckpoint, build_attempt_feedback, claim_recovery_action, finish_recovery_action
from .result_validation import ContractValidation, validate_stage_output
from .supervisor import ProcessSupervisor
from .workspace import snapshot_workspace

if TYPE_CHECKING:
    from .engine import PreparedStage


class StageAttemptMixin:
    def _commit_stage(self, prepared: PreparedStage, output: StageExecutionResult):
        stage = prepared.stage
        final_attempt = prepared.stage_input.attempt
        parsed, validation = validate_stage_output(stage.output_schema, output.content)
        if output.returncode == 0 and not validation.valid:
            output, parsed, validation, final_attempt = self._repair_invalid_output(
                prepared, output, validation
            )
        artifact = self._write_stage_artifact(
            prepared.run_id, stage.id, output.artifact_name, output.content, prepared.run_dir
        )
        self._write_provider_stream_artifacts(prepared, output)
        if stage.read_only and self._subject_digest(prepared.worktree) != prepared.subject_digest:
            validation = ContractValidation(
                validation.schema,
                False,
                (*validation.errors, "read-only worker modified the shared subject"),
                validation.parsed,
            )
            self._runtime_failure(
                prepared.run_id, stage.id, "Read-only worker modified the shared subject during execution.",
                code="read_only_subject_modified", kind="policy",
            )
        valid = validation.valid
        status = "completed" if output.returncode == 0 and valid else "failed"
        self.store.upsert_stage(
            prepared.run_id,
            stage.id,
            role=stage.role,
            provider=prepared.provider,
            status=status,
            attempt=final_attempt,
            result={
                "summary": output.summary,
                "cost_usd": output.cost_usd,
                "returncode": output.returncode,
                "parsed": parsed,
                "protocol": output.protocol,
                "event_count": output.event_count,
                "session_id": output.session_id,
                "timed_out": output.timed_out,
                "cancelled": output.cancelled,
                "stdout_truncated": output.stdout_truncated,
                "stderr_truncated": output.stderr_truncated,
                "contract_schema": validation.schema,
                "contract_errors": list(validation.errors),
                "output_excerpt": redact(output.content)[:8_192],
                "output_digest": canonical_hash(output.content),
                "capability_grant": prepared.stage_input.capabilities.to_dict(),
                "provider_events": [
                    {"kind": item.kind, "status": item.status, "details": dict(item.details or {})}
                    for item in output.events
                ],
                "context": prepared.context_manifest,
            },
        )
        self.store.append_event(
            prepared.run_id,
            "worker.completed" if status == "completed" else "worker.failed",
            {
                "worker_id": self._worker_id(
                    prepared.run_id, stage.id, final_attempt
                ),
                "stage_id": stage.id,
                "role": stage.role,
                "provider": prepared.provider,
                "attempt": final_attempt,
                "status": status,
                "returncode": output.returncode,
                "summary": redact(output.summary)[:1000],
                "contract_errors": [redact(item)[:1000] for item in validation.errors[:5]],
            },
            stage_id=stage.id,
        )
        for event in output.events:
            self.store.append_event(
                prepared.run_id,
                "provider.event",
                {"kind": event.kind, "status": event.status, "details": dict(event.details or {})},
                stage_id=stage.id,
            )
            if event.kind == "mcp.tool_call" and event.status in {"observed", "verified"}:
                details = dict(event.details or {})
                reference = str(details.get("tool_ref") or "")
                requirement_ids = {"mcp_tool_observation", f"mcp:{reference}"}
                if event.status == "verified":
                    requirement_ids.add(f"mcp_verified:{reference}")
                for requirement in self._policy(prepared.run_id).requirements:
                    if requirement.id not in requirement_ids:
                        continue
                    self._append_evidence(RuntimeEvidence(
                        record_id=_record_id(), run_id=prepared.run_id, stage_id=stage.id,
                        requirement_id=requirement.id, subject_digest=prepared.subject_digest,
                        producer="muxdev.runtime.provider_event", event_type="mcp_tool_call",
                        status="passed", details={**details, "observability": event.status},
                    ))
        if "runtime_health" in {item.id for item in self._policy(prepared.run_id).requirements}:
            self._append_evidence(RuntimeEvidence(
                record_id=_record_id(), run_id=prepared.run_id, stage_id=stage.id,
                requirement_id="runtime_health", subject_digest=prepared.subject_digest,
                producer="muxdev.runtime.context", event_type="context_prepared",
                details=dict(prepared.context_manifest),
            ))
            if output.returncode == 0 and valid:
                self._append_evidence(RuntimeEvidence(
                    record_id=_record_id(), run_id=prepared.run_id, stage_id=stage.id, requirement_id="runtime_health", subject_digest=prepared.subject_digest, producer="muxdev.runtime", event_type="stage_completed", status="passed", details={"provider": prepared.provider, "returncode": output.returncode},
                ))
        self._record_stage_evidence(
            prepared.run_id, stage.id, stage.role, prepared.provider, artifact, output, parsed, validation,
            prepared.subject_digest, prepared.worktree
        )
        return output, parsed

    def _repair_invalid_output(
        self,
        prepared: PreparedStage,
        output: StageExecutionResult,
        validation: ContractValidation,
    ) -> tuple[StageExecutionResult, dict[str, Any], ContractValidation, int]:
        frozen = WorkspaceCheckpoint.create(
            prepared.worktree, prepared.run_dir / "recovery", stage_id=f"{prepared.stage.id}-output"
        )
        feedback = build_attempt_feedback(
            output,
            prior_attempt=prepared.stage_input.attempt,
            failure_kind="output_contract",
            workspace_diff_digest=self._subject_digest(prepared.worktree),
            validation=validation,
            error_codes=("output_contract_invalid",),
        )
        attempt_id = claim_recovery_action(
            self.store, prepared.run_id, stage_id=prepared.stage.id,
            action="fix-output", provider=prepared.provider, feedback=feedback,
        )
        if not attempt_id:
            return output, {}, validation, prepared.stage_input.attempt
        self._worker_attempt_failed(
            prepared,
            "Provider output violated the declared structured-output contract.",
            returncode=output.returncode,
        )
        self.store.append_event(prepared.run_id, "recovery.session", {
            "attempt_id": attempt_id,
            "stage_id": prepared.stage.id,
            "previous_session_id": output.session_id,
            "resumed": False,
            "reason": (
                "provider adapter cannot resume the session under a reduced capability grant"
                if output.session_id else "previous attempt did not expose a resumable session"
            ),
        }, stage_id=prepared.stage.id)
        repair_input = replace(
            prepared.stage_input,
            capabilities=CapabilityGrant(read_workspace=True),
            context={"subject_digest": self._subject_digest(prepared.worktree)},
            policy={
                "output_schema": prepared.stage.output_schema,
                "timeout_seconds": min(
                    120, int(prepared.stage_input.policy.get("timeout_seconds") or 120)
                ),
                "output_repair": True,
            },
            feedback=feedback,
            skills=(),
            attempt=prepared.stage_input.attempt + 1,
        )
        self._append_worker_started(prepared, repair_input, read_only=True)
        try:
            correction = prepared.adapter.execute(repair_input)
            parsed, corrected = validate_stage_output(prepared.stage.output_schema, correction.content)
        except Exception as exc:
            correction = StageExecutionResult(
                artifact_name=f"{prepared.stage.id}.output-repair.json",
                content="",
                summary=f"Output correction failed: {exc}",
                stage_id=prepared.stage.id,
                provider=prepared.provider,
                status="failed",
                returncode=1,
                stderr_content=str(exc),
            )
            parsed = {}
            corrected = ContractValidation(
                validation.schema, False, (f"output correction failed: {type(exc).__name__}: {exc}",), None
            )
        subject_changed = snapshot_workspace(prepared.worktree).digest != frozen.snapshot.digest
        if subject_changed:
            frozen.restore()
            corrected = ContractValidation(
                corrected.schema,
                False,
                (*corrected.errors, "output correction attempted to modify the frozen worktree"),
                corrected.parsed,
            )
        correction_ok = correction.returncode == 0 and corrected.valid and not subject_changed
        finish_recovery_action(
            self.store, prepared.run_id, attempt_id=attempt_id, stage_id=prepared.stage.id,
            action="fix-output", provider=prepared.provider,
            status="succeeded" if correction_ok else "failed", failure_kind="output_contract",
            message=correction.summary if correction_ok else "; ".join(corrected.errors),
        )
        combined = _combine_results(output, correction)
        if correction_ok:
            return combined, parsed, corrected, repair_input.attempt
        return self._rerun_after_output_repair_failure(
            prepared, frozen, combined, correction, corrected, feedback, repair_input.attempt
        )

    def _rerun_after_output_repair_failure(
        self,
        prepared,
        frozen,
        combined,
        correction,
        corrected,
        prior_feedback,
        prior_attempt,
    ):
        retry_feedback = build_attempt_feedback(
            correction,
            prior_attempt=prior_attempt,
            failure_kind="output_contract",
            workspace_diff_digest=self._subject_digest(prepared.worktree),
            validation=corrected,
            error_codes=("output_contract_repair_failed",),
            history=({"failure_kind": "output_contract", "output_digest": prior_feedback.prior_output_digest},),
            instruction="Rerun the complete stage, address both prior contract failures, and return valid JSON.",
        )
        retry_id = claim_recovery_action(
            self.store, prepared.run_id, stage_id=prepared.stage.id,
            action="retry", provider=prepared.provider, feedback=retry_feedback,
        )
        if not retry_id:
            return combined, {}, corrected, prior_attempt
        correction_prepared = replace(prepared, stage_input=replace(
            prepared.stage_input, attempt=prior_attempt
        ))
        self._worker_attempt_failed(
            correction_prepared,
            "; ".join(corrected.errors) or correction.summary,
            returncode=correction.returncode,
        )
        (prepared.checkpoint or frozen).restore()
        full_input = replace(
            prepared.stage_input,
            feedback=retry_feedback,
            attempt=prior_attempt + 1,
        )
        self._append_worker_started(prepared, full_input, read_only=False)
        try:
            rerun = prepared.adapter.execute(full_input)
            rerun_parsed, rerun_validation = validate_stage_output(
                prepared.stage.output_schema, rerun.content
            )
        except Exception as exc:
            rerun = StageExecutionResult(
                artifact_name=f"{prepared.stage.id}.retry.json", content="",
                summary=f"Full stage retry failed: {exc}", stage_id=prepared.stage.id,
                provider=prepared.provider, status="failed", returncode=1, stderr_content=str(exc),
            )
            rerun_parsed = {}
            rerun_validation = ContractValidation(
                corrected.schema, False, (f"full stage retry failed: {type(exc).__name__}: {exc}",), None
            )
        rerun_ok = rerun.returncode == 0 and rerun_validation.valid
        finish_recovery_action(
            self.store, prepared.run_id, attempt_id=retry_id, stage_id=prepared.stage.id,
            action="retry", provider=prepared.provider,
            status="succeeded" if rerun_ok else "failed", failure_kind="output_contract",
            message=rerun.summary if rerun_ok else "; ".join(rerun_validation.errors),
        )
        return (
            _combine_results(combined, rerun),
            rerun_parsed,
            rerun_validation,
            full_input.attempt,
        )

    def _append_worker_started(self, prepared, stage_input, *, read_only: bool) -> None:
        self.store.append_event(
            prepared.run_id,
            "worker.started",
            {
                "worker_id": self._worker_id(
                    prepared.run_id, prepared.stage.id, stage_input.attempt
                ),
                "stage_id": prepared.stage.id,
                "role": prepared.stage.role,
                "provider": prepared.provider,
                "attempt": stage_input.attempt,
                "read_only": read_only,
                "status": "running",
            },
            stage_id=prepared.stage.id,
        )

    def _record_stage_evidence(
        self, run_id, stage_id, role, provider, artifact, output, parsed, validation, subject, worktree
    ):
        valid = validation.valid
        requirements = {item.id for item in self._policy(run_id).requirements}
        if role in {"plan", "architect", "test_strategy"} and "plan_artifact" in requirements:
            self._append_evidence(ArtifactEvidence(
                record_id=_record_id(), run_id=run_id, stage_id=stage_id, requirement_id="plan_artifact",
                subject_digest=subject, producer="muxdev.runtime", path=str(artifact["path"]),
                digest=str(artifact["digest"]), size=int(artifact["size"]), media_type=str(artifact["media_type"]),
            ))
        if role == "test" and "deterministic_check" in requirements:
            commands = self._verification_commands(run_id, stage_id)
            if not commands:
                unavailable = ExecutedCheck(
                    id="unavailable",
                    argv=[],
                    cwd=".",
                    cwd_digest=canonical_hash({"worktree": str(worktree.resolve())}),
                    exit_code=127,
                    duration_ms=0,
                    stdout_digest=canonical_hash(""),
                    stderr_digest=canonical_hash("unavailable"),
                    stderr_summary="No trusted VerificationCommand is configured.",
                    reproducible=False,
                )
                self._append_evidence(_check_evidence(
                    unavailable, run_id=run_id, stage_id=stage_id, subject=subject,
                    summary="Trusted verification is unavailable.",
                ))
            for command in commands:
                check_subject = self._subject_digest(worktree)
                observed = self._run_check(command, worktree, run_id, stage_id)
                subject_unchanged = self._subject_digest(worktree) == check_subject
                if not subject_unchanged:
                    self._runtime_failure(
                        run_id, stage_id, "Runtime verification command modified the delivery subject."
                    )
                self._append_evidence(_check_evidence(
                    observed,
                    run_id=run_id,
                    stage_id=stage_id,
                    subject=subject,
                    summary=f"Runtime executed policy command {command.id}.",
                    integrity_valid=subject_unchanged,
                ))
        review_requirements = {"review", "independent_review", "security_review"} & requirements
        if role in {"review", "secure"} and review_requirements:
            if role == "secure" and "security_review" in requirements:
                requirement_id = "security_review"
            else:
                requirement_id = "independent_review" if "independent_review" in requirements else "review"
            route = ProviderRouter(self.store).explain(run_id)
            route_payload = route.get("payload") if isinstance(route.get("payload"), dict) else {}
            executor = self._executor_identity(run_id, route_payload)
            findings = [ReviewFinding(
                finding_id=str(item.get("finding_id") or item.get("id") or _record_id()),
                category=str(item.get("category") or "correctness"), severity=str(item.get("severity") or "medium"),
                message=str(item.get("message") or "Review finding"), file=item.get("file"), line=item.get("line"),
                remediation=str(item.get("remediation") or "Address the finding."), resolved=bool(item.get("resolved")),
            ) for item in parsed.get("findings", []) if isinstance(item, dict)]
            target = str(parsed.get("target_subject") or subject)
            if target == "runtime-bound":
                target = subject
            self._append_evidence(ReviewEvidence(
                record_id=_record_id(), run_id=run_id, stage_id=stage_id, requirement_id=requirement_id,
                subject_digest=subject, producer="muxdev.runtime.review", target_digest=target,
                reviewer=provider, executor=executor, independent=provider != executor, findings=findings,
                residual_risk=str(parsed.get("residual_risk") or ""), integrity_valid=valid,
            ))
        if not valid:
            self._runtime_failure(
                run_id,
                stage_id,
                "Model output violated the workflow output contract.",
                code="output_contract_invalid",
                kind="output_contract",
                errors=list(validation.errors),
            )

    def _run_check(
        self,
        command: VerificationCommand,
        worktree: Path,
        run_id: str,
        stage_id: str,
    ) -> ExecutedCheck:
        cwd = (worktree / command.cwd).resolve()
        if cwd != worktree.resolve() and worktree.resolve() not in cwd.parents:
            return _check_observation(
                command, 127, "", "Verification cwd escaped the worktree.", 0, cwd_digest="invalid"
            )
        cwd_digest = snapshot_workspace(cwd).digest if cwd.is_dir() else "missing"
        try:
            completed = ProcessSupervisor().run(
                command.argv,
                cwd=cwd,
                timeout_seconds=command.timeout_seconds,
                run_id=run_id,
                stage_id=stage_id,
            )
            observed = _check_observation(
                command,
                completed.returncode,
                completed.stdout,
                completed.stderr,
                completed.duration_ms,
                cwd_digest=cwd_digest,
            )
            self._write_check_artifacts(run_id, stage_id, command.id, completed.stdout, completed.stderr)
            return observed
        except OSError as exc:
            return _check_observation(command, 127, "", str(exc), 0, cwd_digest=cwd_digest)

    def _verification_commands(self, run_id: str, stage_id: str) -> list[VerificationCommand]:
        workflow = WorkflowDefinition.model_validate(self._policy_snapshot(run_id).workflow_definition)
        stage = next((item for item in workflow.stages if item.id == stage_id), None)
        return list(stage.verification_commands) if stage else []

    def _write_check_artifacts(
        self,
        run_id: str,
        stage_id: str,
        check_id: str,
        stdout: str,
        stderr: str,
    ) -> None:
        run = self.store.get_run(run_id) or {}
        metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
        run_dir = Path(str(metadata.get("run_dir") or self._run_dir(run_id)))
        safe_id = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in check_id)
        output_dir = run_dir / "artifacts" / stage_id
        output_dir.mkdir(parents=True, exist_ok=True)
        for stream_name, content in (("stdout", stdout), ("stderr", stderr)):
            path = output_dir / f"{safe_id}.{stream_name}.log"
            path.write_text(content, encoding="utf-8")
            self.store.add_artifact(
                run_id,
                name=path.name,
                path=path,
                kind=f"verification_{stream_name}",
                stage_id=stage_id,
                media_type="text/plain",
            )

    def _write_provider_stream_artifacts(self, prepared: PreparedStage, output: Any) -> None:
        output_dir = prepared.run_dir / "artifacts" / prepared.stage.id
        output_dir.mkdir(parents=True, exist_ok=True)
        for stream_name, content in (
            ("stdout", output.stdout_content),
            ("stderr", output.stderr_content),
        ):
            if content is None:
                continue
            path = output_dir / f"provider.{stream_name}.log"
            path.write_text(content, encoding="utf-8")
            self.store.add_artifact(
                prepared.run_id,
                name=path.name,
                path=path,
                kind=f"provider_{stream_name}",
                stage_id=prepared.stage.id,
                media_type="text/plain",
            )



def _record_id() -> str:
    return f"ev_{uuid4().hex}"


def _check_observation(
    command: VerificationCommand,
    exit_code: int,
    stdout: str,
    stderr: str,
    duration_ms: int,
    *,
    cwd_digest: str,
) -> ExecutedCheck:
    return ExecutedCheck(
        id=command.id,
        argv=list(command.argv),
        cwd=command.cwd,
        exit_code=exit_code,
        duration_ms=duration_ms,
        cwd_digest=cwd_digest,
        stdout_digest=canonical_hash(stdout),
        stderr_digest=canonical_hash(stderr),
        stdout_summary=stdout[-2_000:],
        stderr_summary=stderr[-2_000:],
    )


def _check_evidence(
    executed: ExecutedCheck,
    *,
    run_id: str,
    stage_id: str,
    subject: str,
    summary: str,
    integrity_valid: bool = True,
) -> CheckEvidence:
    """The sole conversion from a runtime observation to trusted check evidence."""
    return CheckEvidence(
        record_id=_record_id(),
        run_id=run_id,
        stage_id=stage_id,
        requirement_id="deterministic_check",
        subject_digest=subject,
        producer="muxdev.runtime.command",
        argv=executed.argv,
        cwd=executed.cwd,
        cwd_digest=executed.cwd_digest,
        exit_code=executed.exit_code,
        duration_ms=executed.duration_ms,
        stdout_digest=executed.stdout_digest,
        stderr_digest=executed.stderr_digest,
        stdout_summary=executed.stdout_summary,
        stderr_summary=executed.stderr_summary,
        summary=summary,
        reproducible=executed.reproducible,
        integrity_valid=integrity_valid,
    )


def _combine_results(first: StageExecutionResult, second: StageExecutionResult) -> StageExecutionResult:
    return replace(
        second,
        cost_usd=first.cost_usd + second.cost_usd,
        tokens=first.tokens + second.tokens,
    )
