"""Minimal durable RunEngine for trusted Agent delivery."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from ..domain import StageExecutionInput
from ..core.platforms import hidden_subprocess_kwargs
from ..models import ChangeResult, PlanResult, RunStatus, WorkflowDefinition
from ..models.evidence import (
    AnyEvidenceRecord,
    ArtifactEvidence,
    CheckEvidence,
    EvidencePolicy,
    EvidenceReport,
    InteractionEvidence,
    ReviewEvidence,
    ReviewFinding,
    RuntimeEvidence,
    canonical_hash,
)
from ..providers import get_runtime_provider
from ..services.evidence_policy import load_evidence_policy
from ..services.gate import evaluate_gate
from ..services.repo_map import build_repo_map
from ..services.router import ProviderRouter
from ..services.skills import resolve_stage_skills
from ..storage.control import ControlStore, migrate_workspace
from ..workflows import load_workflow, ordered_stage_ids, should_run_when
from .result_validation import FORBIDDEN_DECISION_FIELDS, validate_review_result, validate_test_result
from .workspace import apply_changes, diff_text
from .worktree import WorktreeManager


PROFILE_SETTINGS = {
    "lite": {"timeout_seconds": 180, "retries": 0, "max_cost_usd": 0.25},
    "standard": {"timeout_seconds": 300, "retries": 1, "max_cost_usd": 0.5},
    "strict": {"timeout_seconds": 600, "retries": 1, "max_cost_usd": 2.0},
}


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: RunStatus
    run_dir: Path
    report_path: Path


def new_run_id() -> str:
    return f"run_{int(time.time() * 1000)}_{uuid4().hex[:12]}"


class RunEngine:
    """Execute four fixed workflows and derive all gate facts at runtime."""

    def __init__(self, workspace: Path | str, *, store: ControlStore | None = None) -> None:
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        migrate_workspace(self.workspace)
        self.store = store or ControlStore(self.workspace)

    def run(
        self,
        task: str,
        *,
        provider: str | None = "mock",
        workflow_name: str = "change",
        profile: str = "standard",
        role_providers: Mapping[str, str] | None = None,
        max_cost_usd: float = 0.5,
        run_id: str | None = None,
    ) -> RunResult:
        if workflow_name not in {"change", "design", "review", "test"}:
            raise ValueError("workflow must be change, design, review, or test")
        if profile not in {"lite", "standard", "strict"}:
            raise ValueError("profile must be lite, standard, or strict")
        run_id = run_id or new_run_id()
        policy = load_evidence_policy(self.workspace, workflow=workflow_name, profile=profile)
        workflow_definition = load_workflow(workflow_name)
        role_providers = dict(role_providers or {})
        max_cost_usd = min(max_cost_usd, float(PROFILE_SETTINGS[profile]["max_cost_usd"]))
        run_dir = self._run_dir(run_id)
        worktree = WorktreeManager(self.workspace).prepare(run_id, run_dir).path
        self.store.create_run(
            run_id=run_id,
            task=task,
            workflow=workflow_name,
            profile=profile,
            provider=provider or "auto",
            policy_hash=policy.policy_hash,
            metadata={
                "run_dir": str(run_dir),
                "worktree": str(worktree),
                "max_cost_usd": max_cost_usd,
                "policy": policy.model_dump(mode="json"),
                "workflow_definition": workflow_definition.model_dump(mode="json"),
                "role_providers": role_providers,
            },
        )
        route = ProviderRouter(self.store).route(run_id, preferred=provider, profile=profile, max_cost_usd=max_cost_usd)
        if not route.get("main_provider"):
            self.store.update_run(run_id, status="blocked")
            self._runtime_failure(run_id, "routing", "No eligible provider passed capability/authentication filters.")
            return self._finalize(run_id, run_dir, worktree, route, started=time.monotonic())
        job_id = self.store.start_job(run_id, kind="execute", payload={"workflow": workflow_name, "profile": profile})
        try:
            result = self._execute(
                run_id, task, run_dir, worktree, route, role_providers,
                max_cost_usd=max_cost_usd, started=time.monotonic(),
            )
        except Exception:
            self.store.finish_job(job_id, status="failed")
            raise
        self.store.finish_job(job_id, status="succeeded")
        return result

    def resume(self, run_id: str) -> RunResult:
        run = self.store.get_run(run_id)
        if not run:
            raise FileNotFoundError(run_id)
        if run["status"] == "aborted":
            raise RuntimeError("cancelled runs cannot be resumed")
        metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
        run_dir = Path(str(metadata.get("run_dir") or self._run_dir(run_id)))
        worktree = Path(str(metadata.get("worktree") or run_dir / "worktree"))
        route_row = ProviderRouter(self.store).explain(run_id)
        route = route_row.get("payload") if isinstance(route_row.get("payload"), dict) else {}
        running = [item for item in self.store.stages(run_id) if item["status"] == "running"]
        if running and route.get("main_provider") not in {"mock", "replay"}:
            self._runtime_failure(run_id, str(running[0]["stage_id"]), "Opaque provider stage requires reconciliation after interruption.")
            return self._finalize(run_id, run_dir, worktree, route, started=time.monotonic())
        job_id = self.store.start_job(run_id, kind="resume", payload={"prior_status": run["status"]})
        role_providers = metadata.get("role_providers") if isinstance(metadata.get("role_providers"), dict) else {}
        try:
            result = self._execute(
                run_id, str(run["task"]), run_dir, worktree, route, role_providers,
                max_cost_usd=float(metadata.get("max_cost_usd") or 0.5), started=time.monotonic(),
            )
        except Exception:
            self.store.finish_job(job_id, status="failed")
            raise
        self.store.finish_job(job_id, status="succeeded")
        return result

    def cancel(self, run_id: str) -> None:
        if not self.store.get_run(run_id):
            raise FileNotFoundError(run_id)
        self.store.cancel_run(run_id)

    def _execute(
        self,
        run_id: str,
        task: str,
        run_dir: Path,
        worktree: Path,
        route: Mapping[str, object],
        role_providers: Mapping[str, str],
        *,
        max_cost_usd: float,
        started: float,
    ) -> RunResult:
        run = self.store.get_run(run_id) or {}
        metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
        frozen_workflow = metadata.get("workflow_definition") if isinstance(metadata, dict) else None
        workflow = (
            WorkflowDefinition.model_validate(frozen_workflow)
            if isinstance(frozen_workflow, dict)
            else load_workflow(str(run["workflow"]))
        )
        completed = {str(item["stage_id"]) for item in self.store.stages(run_id) if item["status"] == "completed"}
        profile = str(run["profile"])
        context: dict[str, object] = {"loop": 0, "profile": {profile: True}}
        cost = sum(float((item.get("result") or {}).get("cost_usd") or 0) for item in self.store.stages(run_id))
        self.store.update_run(run_id, status="running")
        for stage_id in ordered_stage_ids(workflow):
            stage = next(item for item in workflow.stages if item.id == stage_id)
            if stage_id in completed:
                continue
            if not self._required_stage(profile, stage_id):
                self.store.upsert_stage(run_id, stage_id, role=stage.role, provider=None, status="skipped")
                continue
            if not should_run_when(stage.when, context):
                self.store.upsert_stage(run_id, stage_id, role=stage.role, provider=None, status="skipped")
                continue
            if stage_id == "fix":
                continue
            if stage.type == "human_gate":
                if self._pause_for_human(run_id, stage_id, str(run["profile"]), stage.approval_reason or "Approval required"):
                    return self._finalize(run_id, run_dir, worktree, route, started=started)
                self.store.upsert_stage(run_id, stage_id, role=None, provider=None, status="completed")
                continue
            selected = self._stage_provider(stage.role, route, role_providers)
            try:
                result, parsed = self._execute_stage(run_id, task, stage, worktree, selected, run_dir)
            except Exception as exc:
                self._runtime_failure(run_id, stage_id, f"Stage execution failed: {exc}")
                self.store.upsert_stage(
                    run_id, stage_id, role=stage.role, provider=selected, status="failed", result={"error": str(exc)}
                )
                break
            cost += result.cost_usd
            context[stage_id] = parsed or {"summary": result.summary}
            if isinstance(parsed, dict) and stage.role in {"review", "secure"}:
                parsed["has_blockers"] = any(
                    isinstance(item, dict) and item.get("severity") == "high" for item in parsed.get("findings", [])
                )
                if stage.role == "review":
                    context["review"] = parsed
            if result.returncode != 0:
                self._runtime_failure(run_id, stage_id, f"Provider exited with {result.returncode}.")
                break
            if cost > max_cost_usd:
                self._runtime_failure(run_id, stage_id, "Run exceeded the configured cost limit.")
                break
        if str(run["workflow"]) == "change" and bool((context.get("review") or {}).get("has_blockers")):
            cost = self._repair_change(
                run_id, task, workflow, run_dir, worktree, route, role_providers, context, cost=cost, max_cost=max_cost_usd
            )
        return self._finalize(run_id, run_dir, worktree, route, started=started, cost_usd=cost)

    def _execute_stage(self, run_id, task, stage, worktree, provider: str, run_dir: Path):
        self.store.update_run(run_id, status="running", current_stage=stage.id)
        self.store.upsert_stage(run_id, stage.id, role=stage.role, provider=provider, status="running")
        subject = self._subject_digest(worktree)
        skills = resolve_stage_skills(
            self.workspace,
            stage.default_skills,
            role=stage.role,
            stage_id=stage.id,
            allow_write=stage.allow_write,
            allow_shell=stage.allow_shell,
        )
        adapter = get_runtime_provider(provider, workspace=self.workspace)
        profile = str((self.store.get_run(run_id) or {}).get("profile") or "standard")
        settings = PROFILE_SETTINGS[profile]
        stage_input = StageExecutionInput(
                run_id=run_id,
                stage_id=stage.id,
                role=stage.role,
                task=task,
                worktree=worktree,
                context={"subject_digest": subject, "repo_map": build_repo_map(worktree, task)},
                capabilities={"read_only": stage.read_only, "write": stage.allow_write, "shell": stage.allow_shell},
                provider=provider,
                policy={"output_schema": stage.output_schema, "timeout_seconds": settings["timeout_seconds"]},
                skills=skills,
            )
        output = self._execute_provider(adapter, stage_input, retries=int(settings["retries"]) if stage.read_only else 0)
        artifact = self._write_stage_artifact(run_id, stage.id, output.artifact_name, output.content, run_dir)
        parsed, valid = self._validate_output(stage.output_schema, output.content)
        status = "completed" if output.returncode == 0 and valid else "failed"
        self.store.upsert_stage(
            run_id,
            stage.id,
            role=stage.role,
            provider=provider,
            status=status,
            result={"summary": output.summary, "cost_usd": output.cost_usd, "returncode": output.returncode, "parsed": parsed},
        )
        self._record_stage_evidence(
            run_id, stage.id, stage.role, provider, artifact, output, parsed, valid, subject, worktree
        )
        return output, parsed

    @staticmethod
    def _execute_provider(adapter, stage_input: StageExecutionInput, *, retries: int):
        for attempt in range(1, retries + 2):
            try:
                result = adapter.execute(stage_input.__class__(**{**stage_input.__dict__, "attempt": attempt}))
            except Exception:
                if attempt > retries:
                    raise
                continue
            if result.returncode == 0 or attempt > retries:
                return result
        raise RuntimeError("provider retry loop exhausted")

    @staticmethod
    def _validate_output(schema: str | None, content: str) -> tuple[dict[str, Any], bool]:
        payload = _extract_json(content)
        if payload and FORBIDDEN_DECISION_FIELDS & set(payload):
            return {}, False
        if schema == "TestResult":
            result, validation = validate_test_result(payload, fallback_summary="Runtime did not receive a valid TestResult.")
            return result.model_dump(mode="json"), validation.valid
        if schema == "ReviewResult":
            result, validation = validate_review_result(payload)
            return result.model_dump(mode="json"), validation.valid
        if schema in {"PlanResult", "ChangeResult"}:
            model = PlanResult if schema == "PlanResult" else ChangeResult
            try:
                parsed = model.model_validate(payload)
            except (TypeError, ValueError):
                return {}, False
            return parsed.model_dump(mode="json"), True
        return payload or {}, True

    def _record_stage_evidence(
        self, run_id, stage_id, role, provider, artifact, output, parsed, valid, subject, worktree
    ):
        requirements = {item.id for item in self._policy(run_id).requirements}
        if role in {"plan", "architect", "test_strategy"} and "plan_artifact" in requirements:
            self._append_evidence(ArtifactEvidence(
                record_id=_record_id(), run_id=run_id, stage_id=stage_id, requirement_id="plan_artifact",
                subject_digest=subject, producer="muxdev.runtime", path=str(artifact["path"]),
                digest=str(artifact["digest"]), size=int(artifact["size"]), media_type=str(artifact["media_type"]),
            ))
        if role == "test" and "deterministic_check" in requirements:
            checks = parsed.get("checks") if isinstance(parsed.get("checks"), list) else []
            if not checks:
                checks = [{"argv": [], "exit_code": None, "status": "unavailable", "summary": output.summary}]
            for check in checks:
                argv = [str(item) for item in check.get("argv", [])]
                observed = self._run_check(argv, worktree, run_id)
                claimed_exit = check.get("exit_code")
                claimed_status = str(check.get("status") or "")
                status_matches = (
                    claimed_exit == observed["exit_code"]
                    and ((claimed_status == "passed") == (observed["exit_code"] == 0))
                )
                self._append_evidence(CheckEvidence(
                    record_id=_record_id(), run_id=run_id, stage_id=stage_id, requirement_id="deterministic_check",
                    subject_digest=subject, producer="muxdev.runtime.command", argv=argv, cwd=".",
                    cwd_digest=canonical_hash({"worktree": str(worktree.resolve())}),
                    exit_code=int(observed["exit_code"]),
                    declared_exit_code=int(claimed_exit) if claimed_exit is not None else None,
                    declared_status=claimed_status if claimed_status in {"passed", "failed", "skipped", "unavailable"} else None,
                    duration_ms=int(observed["duration_ms"]),
                    stdout_digest=str(observed["stdout_digest"]), stderr_digest=str(observed["stderr_digest"]),
                    stdout_summary=str(observed["stdout_summary"]), stderr_summary=str(observed["stderr_summary"]),
                    summary=str(check.get("summary") or output.summary), reproducible=bool(argv),
                    integrity_valid=valid and status_matches,
                ))
        if role in {"review", "secure"} and ({"review", "independent_review"} & requirements):
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
            self._runtime_failure(run_id, stage_id, "Skill output violated the workflow output contract.")

    def _run_check(self, argv: list[str], cwd: Path, run_id: str) -> dict[str, object]:
        if not argv:
            return _check_observation(127, "", "No reproducible argv was provided.", 0)
        profile = str((self.store.get_run(run_id) or {}).get("profile") or "standard")
        started = time.monotonic()
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=float(PROFILE_SETTINGS[profile]["timeout_seconds"]),
                check=False,
                **hidden_subprocess_kwargs(),
            )
            return _check_observation(
                completed.returncode,
                completed.stdout or "",
                completed.stderr or "",
                int((time.monotonic() - started) * 1000),
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            return _check_observation(124, stdout, stderr + "\nRuntime check timed out.", int((time.monotonic() - started) * 1000))
        except OSError as exc:
            return _check_observation(127, "", str(exc), int((time.monotonic() - started) * 1000))

    def _finalize(self, run_id, run_dir, worktree, route, *, started: float, cost_usd: float = 0.0) -> RunResult:
        subject_digest = self._subject_digest(worktree)
        diff_path = run_dir / "diff.patch"
        diff_path.write_text(diff_text(worktree), encoding="utf-8")
        artifact = self.store.add_artifact(run_id, name="diff.patch", path=diff_path, kind="diff", media_type="text/x-diff")
        requirements = {item.id for item in self._policy(run_id).requirements}
        if "change_artifact" in requirements:
            self._append_evidence(ArtifactEvidence(
                record_id=_record_id(), run_id=run_id, requirement_id="change_artifact", subject_digest=subject_digest,
                producer="muxdev.runtime", path=str(diff_path), digest=str(artifact["digest"]), size=int(artifact["size"]),
                media_type="text/x-diff",
            ))
        if "runtime_health" in requirements and not self._has_runtime_evidence(run_id):
            self._append_evidence(RuntimeEvidence(
                record_id=_record_id(), run_id=run_id, requirement_id="runtime_health", subject_digest=subject_digest,
                producer="muxdev.runtime", event_type="run_health", status="passed", details={},
            ))
        records = self._records(run_id)
        decision = evaluate_gate(self._policy(run_id), records, subject_digest=subject_digest)
        chain_valid, chain_errors = self.store.verify_event_chain(run_id)
        if not chain_valid and decision.status == "PASS":
            self._runtime_failure(run_id, "finalize", "; ".join(chain_errors))
            records = self._records(run_id)
            decision = evaluate_gate(self._policy(run_id), records, subject_digest=subject_digest)
        chain_events = self.store.events(run_id)
        chain_head = chain_events[-1] if chain_events else {}
        report = EvidenceReport(
            run_id=run_id,
            subject={"name": run_id, "digest": subject_digest, "artifacts": [{"name": artifact["name"], "digest": artifact["digest"]}]},
            policy=self._policy(run_id), records=records, decision=decision,
            integrity={
                "valid": chain_valid,
                "event_chain_errors": chain_errors,
                "event_chain_sequence": chain_head.get("sequence", 0),
                "event_chain_head": chain_head.get("event_hash"),
                "records_hash": canonical_hash([item.model_dump(mode="json") for item in records]),
            },
            routing=dict(route), reviewer={"main": route.get("main_provider"), "reviewer": route.get("reviewer_provider")},
        )
        report_path = run_dir / "evidence-report.json"
        report_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        self.store.add_artifact(run_id, name=report_path.name, path=report_path, kind="evidence_report", media_type="application/json")
        if decision.status == "PASS":
            applied = apply_changes(worktree, self.workspace)
            self.store.append_event(run_id, "workspace.applied", {"files": applied})
            status = RunStatus.COMPLETED
        elif decision.status == "WAITING_HUMAN":
            status = RunStatus.AWAITING_APPROVAL
        else:
            status = RunStatus.BLOCKED
        self.store.update_run(run_id, status=str(status), current_stage=None)
        main = str(route.get("main_provider") or "unknown")
        independent_valid = not any(getattr(item, "independent", True) is False for item in records if item.kind == "review")
        self.store.record_outcome(run_id, main, {
            "success": status == RunStatus.COMPLETED, "gate_status": decision.status,
            "integrity_valid": chain_valid, "independent_valid": independent_valid,
            "cost_usd": cost_usd, "latency_seconds": round(time.monotonic() - started, 3),
        })
        return RunResult(run_id, status, run_dir, report_path)

    def _pause_for_human(self, run_id: str, stage_id: str, profile: str, prompt: str) -> bool:
        if profile != "strict":
            return False
        existing = [item for item in self.store.interactions(run_id) if item["stage_id"] == stage_id]
        if not existing:
            self.store.request_interaction(
                run_id, kind="approval", requirement_id="human_approval", prompt=prompt, stage_id=stage_id
            )
            self._append_interaction_evidence(run_id, stage_id, "pending")
            return True
        status = str(existing[-1]["status"])
        self._replace_interaction_evidence(run_id, stage_id, status)
        if status == "rejected":
            self._runtime_failure(run_id, stage_id, "Required human approval was rejected.")
            return True
        return status != "approved"

    def _append_interaction_evidence(self, run_id: str, stage_id: str, decision: str) -> None:
        self._append_evidence(InteractionEvidence(
            record_id=_record_id(), run_id=run_id, stage_id=stage_id, requirement_id="human_approval",
            subject_digest=self._subject_digest_from_run(run_id), producer="muxdev.runtime.interaction",
            interaction_type="approval" if decision != "rejected" else "rejection", decision=decision,
        ))

    def _replace_interaction_evidence(self, run_id: str, stage_id: str, status: str) -> None:
        decision = {"approved": "approved", "rejected": "rejected"}.get(status, "pending")
        self._append_interaction_evidence(run_id, stage_id, decision)

    def _runtime_failure(self, run_id: str, stage_id: str, message: str) -> None:
        self._append_evidence(RuntimeEvidence(
            record_id=_record_id(), run_id=run_id, stage_id=stage_id, requirement_id="runtime_health",
            subject_digest=self._subject_digest_from_run(run_id), producer="muxdev.runtime",
            event_type="policy_or_execution_failure", status="failed", details={"message": message},
        ))

    def _write_stage_artifact(self, run_id: str, stage_id: str, name: str, content: str, run_dir: Path):
        safe = Path(name).name or f"{stage_id}.txt"
        path = run_dir / "artifacts" / stage_id / safe
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return self.store.add_artifact(run_id, name=safe, path=path, kind="stage_output", stage_id=stage_id, media_type="text/plain")

    def _append_evidence(self, record: AnyEvidenceRecord) -> None:
        self.store.append_event(
            record.run_id, "evidence.record", record.model_dump(mode="json"), stage_id=record.stage_id, event_id=record.record_id
        )

    def _records(self, run_id: str) -> list[AnyEvidenceRecord]:
        records: list[AnyEvidenceRecord] = []
        for event in self.store.events(run_id):
            if event["type"] == "evidence.record":
                records.append(_evidence_from_payload(event["payload"]))
        return records

    def _has_runtime_evidence(self, run_id: str) -> bool:
        return any(item.kind == "runtime" for item in self._records(run_id))

    def _policy(self, run_id: str):
        run = self.store.get_run(run_id) or {}
        metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
        frozen = metadata.get("policy") if isinstance(metadata, dict) else None
        if isinstance(frozen, dict):
            policy = EvidencePolicy.model_validate(frozen)
            if policy.policy_hash != run.get("policy_hash"):
                raise ValueError("frozen EvidencePolicy does not match the run policy hash")
            return policy
        return load_evidence_policy(self.workspace, workflow=str(run["workflow"]), profile=str(run["profile"]))

    def _subject_digest(self, worktree: Path) -> str:
        return canonical_hash({"diff": diff_text(worktree)})

    def _subject_digest_from_run(self, run_id: str) -> str:
        run = self.store.get_run(run_id) or {}
        metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
        return self._subject_digest(Path(str(metadata.get("worktree") or self.workspace)))

    @staticmethod
    def _stage_provider(role: str | None, route: Mapping[str, object], overrides: Mapping[str, str]) -> str:
        if role and role in overrides:
            return overrides[role]
        if role in {"review", "secure"} and route.get("reviewer_provider"):
            return str(route["reviewer_provider"])
        return str(route["main_provider"])

    def _executor_identity(self, run_id: str, route: Mapping[str, object]) -> str:
        stages = [
            item for item in self.store.stages(run_id)
            if item.get("provider") and item.get("role") not in {"review", "secure"}
        ]
        code = [item for item in stages if item.get("role") == "code"]
        selected = (code or stages)[-1] if (code or stages) else None
        return str(selected.get("provider")) if selected else str(route.get("main_provider") or "")

    @staticmethod
    def _required_stage(profile: str, stage_id: str) -> bool:
        return not (profile == "lite" and stage_id in {"plan", "test_plan", "approve_plan"})

    def _repair_change(
        self,
        run_id,
        task,
        workflow,
        run_dir,
        worktree,
        route,
        role_providers,
        context,
        *,
        cost: float,
        max_cost: float,
    ) -> float:
        by_id = {stage.id: stage for stage in workflow.stages}
        for loop in range(1, 3):
            if not bool((context.get("review") or {}).get("has_blockers")):
                break
            context["loop"] = loop
            for stage_id in ("fix", "test", "review"):
                stage = by_id[stage_id]
                provider = self._stage_provider(stage.role, route, role_providers)
                try:
                    output, parsed = self._execute_stage(run_id, task, stage, worktree, provider, run_dir)
                except Exception as exc:
                    self._runtime_failure(run_id, stage_id, f"Repair stage failed: {exc}")
                    return cost
                cost += output.cost_usd
                if stage_id == "review":
                    parsed["has_blockers"] = any(
                        isinstance(item, dict) and item.get("severity") == "high" for item in parsed.get("findings", [])
                    )
                    context["review"] = parsed
                if output.returncode != 0 or cost > max_cost:
                    self._runtime_failure(run_id, stage_id, "Repair loop failed or exceeded the cost limit.")
                    return cost
        return cost

    def _run_dir(self, run_id: str) -> Path:
        path = self.workspace / ".muxdev" / "runs" / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path.resolve()


def _record_id() -> str:
    return f"ev_{uuid4().hex}"


def _check_observation(exit_code: int, stdout: str, stderr: str, duration_ms: int) -> dict[str, object]:
    return {
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "stdout_digest": canonical_hash(stdout),
        "stderr_digest": canonical_hash(stderr),
        "stdout_summary": stdout[-2_000:],
        "stderr_summary": stderr[-2_000:],
    }


def _extract_json(content: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for index, char in enumerate(content):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _evidence_from_payload(payload: Mapping[str, Any]) -> AnyEvidenceRecord:
    classes = {
        "artifact": ArtifactEvidence,
        "check": CheckEvidence,
        "review": ReviewEvidence,
        "interaction": InteractionEvidence,
        "runtime": RuntimeEvidence,
    }
    return classes[str(payload["kind"])].model_validate(payload)
