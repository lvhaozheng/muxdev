"""Minimal durable RunEngine for trusted Agent delivery."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from ..domain import AttemptFeedback, CapabilityGrant, StageExecutionInput, StageExecutionResult
from ..core.redaction import redact
from ..models import (
    WorkflowDefinition,
    WorkflowStage,
)
from ..models.evidence import (
    AnyEvidenceRecord,
    ArtifactEvidence,
    CheckEvidence,
    InteractionEvidence,
    ReviewEvidence,
    RuntimeEvidence,
    canonical_hash,
)
from ..providers import get_runtime_provider
from ..services.context import build_context_pack
from ..services.router import ProviderRouter
from ..storage.control import ControlStore, migrate_workspace
from ..workflows import (
    execution_waves,
    load_workflow,
    should_run_when,
)
from .result_validation import validate_stage_output
from .run_finalization import RunFinalizationMixin, RunResult
from .run_setup import (
    prepare_run_subject,
    resumable_session_id,
)
from .run_reservation import RunReservationMixin
from .recovery import (
    RecoveryContextMixin,
    WorkspaceCheckpoint,
    build_attempt_feedback,
    classify_provider_failure,
    claim_recovery_action,
    finish_recovery_action,
)
from .policy_snapshot import (
    freeze_policy_snapshot,
    freeze_preflight_failure,
)
from .delivery_standards import prepare_delivery_run_inputs, review_standards_for_role
from .supervisor import ProcessSupervisor
from .stage_attempt import StageAttemptMixin
from .interactions import InteractionPendingError, InteractiveStageMixin, response_payload
from .team_runtime import TeamRuntimeMixin


PROFILE_SETTINGS = {
    "lite": {"timeout_seconds": 180, "retries": 0, "max_cost_usd": 0.25},
    "standard": {"timeout_seconds": 300, "retries": 1, "max_cost_usd": 0.5},
    "strict": {"timeout_seconds": 600, "retries": 1, "max_cost_usd": 2.0},
}
MAX_PARALLEL_WORKERS = 4


def _workflow_roles(workflow: WorkflowDefinition) -> tuple[str, ...]:
    return tuple(dict.fromkeys(stage.role for stage in workflow.stages if stage.role))


@dataclass(frozen=True)
class PreparedStage:
    run_id: str
    stage: WorkflowStage
    provider: str
    worktree: Path
    run_dir: Path
    subject_digest: str
    adapter: Any
    stage_input: StageExecutionInput
    retries: int
    context_manifest: Mapping[str, object]
    checkpoint: WorkspaceCheckpoint | None = None


def new_run_id() -> str:
    return f"run_{int(time.time() * 1000)}_{uuid4().hex[:12]}"


class RunEngine(
    RunReservationMixin, TeamRuntimeMixin, InteractiveStageMixin, RecoveryContextMixin,
    StageAttemptMixin, RunFinalizationMixin,
):
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
        delivery_context: Mapping[str, object] | None = None,
        defer_apply: bool = False,
        worktree_path: Path | None = None,
        base_workspace_manifest: Mapping[str, object] | None = None,
    ) -> RunResult:
        run_id = run_id or new_run_id()
        policy, workflow_definition, role_providers, max_cost_usd = self._resolve_run_settings(
            workflow_name, profile, dict(role_providers or {}), max_cost_usd
        )
        policy, workflow_definition, delivery_standard = prepare_delivery_run_inputs(
            policy, workflow_definition, delivery_context
        )
        run_dir = self._run_dir(run_id)
        reserved = self.store.get_run(run_id)
        workspace_snapshot, worktree = prepare_run_subject(
            self.workspace, run_id, run_dir,
            worktree_path=worktree_path, base_workspace_manifest=base_workspace_manifest,
        )
        self._create_or_validate_run(
            reserved,
            run_id=run_id,
            task=task,
            workflow_name=workflow_name,
            profile=profile,
            provider=provider or "auto",
            policy=policy,
            workflow=workflow_definition,
            role_providers=role_providers,
            max_cost_usd=max_cost_usd,
            delivery_context=delivery_context,
            defer_apply=defer_apply,
            run_dir=run_dir,
            worktree=worktree,
        )
        route = ProviderRouter(self.store).route(
            run_id,
            preferred=provider,
            profile=profile,
            max_cost_usd=max_cost_usd,
            roles=_workflow_roles(workflow_definition),
            role_providers=role_providers,
        )
        try:
            role_errors = route.get("role_errors")
            if isinstance(role_errors, list) and role_errors:
                raise ValueError("; ".join(str(item) for item in role_errors))
            policy_snapshot = freeze_policy_snapshot(
                self.workspace,
                self.store,
                run_id=run_id,
                workflow=workflow_definition,
                profile=profile,
                policy=policy,
                provider_request=provider or "auto",
                route=route,
                role_providers=role_providers,
                workspace_snapshot=workspace_snapshot,
                run_dir=run_dir,
                delivery_standard=delivery_standard,
            )
        except Exception as exc:
            policy_snapshot = freeze_preflight_failure(
                self.workspace,
                self.store,
                run_id=run_id,
                workflow=workflow_definition,
                profile=profile,
                policy=policy,
                provider_request=provider or "auto",
                route=route,
                workspace_snapshot=workspace_snapshot,
                run_dir=run_dir,
                delivery_standard=delivery_standard,
            )
            self._runtime_failure(
                run_id,
                "preflight",
                "Agent 团队预检失败，执行尚未开始。",
                code="provider_configuration_invalid",
                kind="provider_failure",
                errors=[redact(str(exc))[:1000]],
            )
            self.store.update_run(run_id, status="blocked")
            return self._finalize(run_id, run_dir, worktree, route, started=time.monotonic())
        self.store.append_event(run_id, "run.policy_snapshot.ready", {
            "snapshot_hash": canonical_hash(policy_snapshot.model_dump(mode="json")),
            "stages": sorted(policy_snapshot.stage_capabilities),
        })
        self.store.append_event(
            run_id,
            "team.resolved",
            self._team_payload(workflow_definition, route, role_providers),
        )
        routing_failure = self._routing_preflight_failure(
            run_id, run_dir, worktree, route, profile
        )
        if routing_failure:
            return routing_failure
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

    def _routing_preflight_failure(
        self,
        run_id: str,
        run_dir: Path,
        worktree: Path,
        route: Mapping[str, object],
        profile: str,
    ) -> RunResult | None:
        if not route.get("main_provider"):
            self.store.update_run(run_id, status="blocked")
            self._runtime_failure(
                run_id,
                "routing",
                "没有 Provider 通过能力、认证与成本预检。",
                code="provider_configuration_invalid",
                kind="provider_failure",
                errors=[
                    f"{item.get('provider')}: {', '.join(item.get('reasons', []))}"
                    for item in route.get("candidates", [])
                    if isinstance(item, dict) and not item.get("eligible")
                ][:20],
            )
        elif profile in {"standard", "strict"} and (
            not route.get("reviewer_provider")
            or route.get("reviewer_provider") == route.get("main_provider")
        ):
            self.store.update_run(run_id, status="blocked")
            self._runtime_failure(
                run_id,
                "reviewer_preflight",
                "No independent reviewer is configured for this delivery profile.",
                code="independent_reviewer_unavailable",
                kind="independent_reviewer",
                errors=["Configure a certified reviewer Provider different from the executor."],
            )
        else:
            return None
        return self._finalize(
            run_id, run_dir, worktree, route, started=time.monotonic()
        )

    def resume(self, run_id: str, *, action: str = "auto") -> RunResult:
        if action not in {"auto", "fix-output", "retry", "switch-provider", "interaction"}:
            raise ValueError(
                "resume action must be auto, fix-output, retry, switch-provider, or interaction"
            )
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
        self.store.append_event(
            run_id,
            "interaction.resume" if action == "interaction" else "recovery.requested",
            {"action": action},
        )
        if str(run.get("profile")) in {"standard", "strict"} and (
            not route.get("reviewer_provider") or route.get("reviewer_provider") == route.get("main_provider")
        ):
            self._runtime_failure(
                run_id, "reviewer_preflight",
                "No independent reviewer is configured in this Run's frozen policy.",
                code="independent_reviewer_unavailable", kind="independent_reviewer",
                errors=["Configure an independent reviewer and create a new Run."],
            )
            return self._finalize(run_id, run_dir, worktree, route, started=time.monotonic())
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
        run = self.store.get_run(run_id)
        if not run:
            raise FileNotFoundError(run_id)
        if run.get("status") == "aborted":
            return
        from ..providers.acp import cancel_acp_run

        protocol_cancelled = cancel_acp_run(run_id)
        if protocol_cancelled:
            deadline = time.monotonic() + 2
            while ProcessSupervisor.active_pids(run_id) and time.monotonic() < deadline:
                time.sleep(0.05)
        pids = ProcessSupervisor().cancel(run_id)
        remaining = ProcessSupervisor.active_pids(run_id)
        self._append_evidence(RuntimeEvidence(
            record_id=_record_id(), run_id=run_id, stage_id=str(run.get("current_stage") or "cancel"),
            requirement_id="runtime_health", subject_digest=self._subject_digest_from_run(run_id),
            producer="muxdev.runtime.supervisor", event_type="process_cancelled",
            status="passed" if not remaining else "failed",
            details={
                "protocol_cancelled": protocol_cancelled,
                "process_ids": list(pids),
                "remaining_process_ids": list(remaining),
            },
        ))
        if remaining:
            raise RuntimeError(f"run {run_id} still has child processes after cancellation: {remaining}")
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
        snapshot = self._policy_snapshot(run_id)
        frozen_workflow = snapshot.workflow_definition
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
        for wave in execution_waves(workflow):
            stages, paused = self._ready_wave(run_id, workflow, wave, completed, profile, context)
            if paused:
                return self._finalize(run_id, run_dir, worktree, route, started=started, cost_usd=cost)
            delta, failed = self._run_wave(
                run_id, task, stages, worktree, run_dir, route, role_providers, context
            )
            cost += delta
            if failed:
                break
            if cost > max_cost_usd:
                self._runtime_failure(run_id, ",".join(wave), "Run exceeded the configured cost limit.")
                break
        needs_repair = bool((context.get("review") or {}).get("has_blockers")) or self._has_failed_checks(
            run_id, worktree
        )
        if str(run["workflow"]) == "change" and needs_repair:
            cost = self._repair_change(
                run_id, task, workflow, run_dir, worktree, route, role_providers, context, cost=cost, max_cost=max_cost_usd
            )
        return self._finalize(run_id, run_dir, worktree, route, started=started, cost_usd=cost)

    def _ready_wave(self, run_id, workflow, wave, completed, profile, context):
        stages: list[WorkflowStage] = []
        by_id = {stage.id: stage for stage in workflow.stages}
        for stage_id in wave:
            stage = by_id[stage_id]
            if stage_id in completed:
                continue
            if not self._required_stage(profile, stage_id) or not should_run_when(stage.when, context):
                self.store.upsert_stage(run_id, stage_id, role=stage.role, provider=None, status="skipped")
                continue
            if stage_id == "fix":
                continue
            pending = [
                item for item in self.store.interactions(run_id, pending_only=True)
                if item.get("stage_id") == stage_id
            ]
            if pending:
                self.store.update_run(
                    run_id, status="awaiting_approval", current_stage=stage_id
                )
                return [], True
            if stage.type == "human_gate":
                prompt = stage.approval_reason or "Approval required"
                if self._pause_for_human(run_id, stage_id, profile, prompt):
                    return [], True
                self.store.upsert_stage(run_id, stage_id, role=None, provider=None, status="completed")
                continue
            stages.append(stage)
        return stages, False

    def _run_wave(self, run_id, task, stages, worktree, run_dir, route, role_providers, context):
        if not stages:
            return 0.0, False
        providers = [self._stage_provider(stage.role, route, role_providers) for stage in stages]
        try:
            outcomes = (
                self._execute_readonly_fanout(run_id, task, stages, providers, worktree, run_dir)
                if len(stages) > 1 and all(stage.read_only for stage in stages)
                else [self._serial_stage_outcome(run_id, task, stage, worktree, provider, run_dir)
                      for stage, provider in zip(stages, providers, strict=True)]
            )
        except Exception as exc:
            for stage, provider in zip(stages, providers, strict=True):
                self._runtime_failure(run_id, stage.id, f"Fan-out preparation failed: {exc}")
                self._worker_failed(run_id, stage, provider, exc)
                self.store.upsert_stage(
                    run_id,
                    stage.id,
                    role=stage.role,
                    provider=provider,
                    status="failed",
                    result={"error": redact(str(exc))[:1000]},
                )
            return 0.0, True
        cost = 0.0
        failed = False
        for stage, provider, result, parsed, error in outcomes:
            if error is not None:
                if isinstance(error, InteractionPendingError):
                    failed = True
                    continue
                failure = classify_provider_failure(None, exception=error)
                self._runtime_failure(
                    run_id, stage.id, failure.summary,
                    code=failure.code, kind=failure.kind, errors=list(failure.details),
                )
                self._worker_failed(run_id, stage, provider, error)
                self.store.upsert_stage(
                    run_id,
                    stage.id,
                    role=stage.role,
                    provider=provider,
                    status="failed",
                    result={"error": redact(str(error))[:1000]},
                )
                failed = True
                continue
            cost += result.cost_usd
            self._merge_stage_context(context, stage, parsed, result.summary)
            if result.returncode != 0:
                failure = classify_provider_failure(result)
                self._runtime_failure(
                    run_id, stage.id, failure.summary,
                    code=failure.code, kind=failure.kind, errors=list(failure.details),
                )
                failed = True
            elif any(
                item["stage_id"] == stage.id and item["status"] == "failed"
                for item in self.store.stages(run_id)
            ):
                failed = True
        return cost, failed

    @staticmethod
    def _merge_stage_context(context, stage, parsed, summary):
        context[stage.id] = parsed or {"summary": summary}
        if isinstance(parsed, dict) and stage.role in {"review", "secure"}:
            parsed["has_blockers"] = any(
                isinstance(item, dict) and item.get("severity") == "high" for item in parsed.get("findings", [])
            )
            if stage.role == "review":
                context["review"] = parsed
            elif parsed["has_blockers"] and isinstance(context.get("review"), dict):
                context["review"]["has_blockers"] = True

    @staticmethod
    def _validate_output(schema: str | None, content: str) -> tuple[dict[str, Any], bool]:
        """Compatibility wrapper around the diagnostic-preserving validator."""
        parsed, validation = validate_stage_output(schema, content)
        return parsed, validation.valid

    def _serial_stage_outcome(self, run_id, task, stage, worktree, provider, run_dir):
        try:
            result, parsed = self._execute_stage(run_id, task, stage, worktree, provider, run_dir)
        except Exception as exc:
            return stage, provider, None, {}, exc
        return stage, provider, result, parsed, None

    def _execute_readonly_fanout(self, run_id, task, stages, providers, worktree, run_dir):
        prepared = [
            self._prepare_stage(run_id, task, stage, worktree, provider, run_dir)
            for stage, provider in zip(stages, providers, strict=True)
        ]
        self.store.update_run(
            run_id, status="running", current_stage="fanout:" + ",".join(item.stage.id for item in prepared)
        )
        self.store.append_event(run_id, "supervisor.fanout.started", {
            "stages": [item.stage.id for item in prepared],
            "subject_digest": prepared[0].subject_digest,
            "merge_order": [item.stage.id for item in prepared],
        })
        outcomes = []
        with ThreadPoolExecutor(
            max_workers=min(MAX_PARALLEL_WORKERS, len(prepared)),
            thread_name_prefix="muxdev-worker",
        ) as executor:
            futures = [
                executor.submit(item.adapter.execute, item.stage_input)
                for item in prepared
            ]
            for item, future in zip(prepared, futures, strict=True):
                try:
                    try:
                        initial_output, initial_error = future.result(), None
                    except Exception as exc:
                        initial_output, initial_error = None, exc
                    item, output = self._execute_prepared(
                        item, initial_output=initial_output, initial_error=initial_error
                    )
                    output, parsed = self._commit_stage(item, output)
                except Exception as exc:
                    outcomes.append((item.stage, item.provider, None, {}, exc))
                else:
                    outcomes.append((item.stage, item.provider, output, parsed, None))
        self.store.append_event(run_id, "supervisor.fanout.completed", {
            "stages": [item.stage.id for item in prepared],
            "statuses": ["failed" if outcome[4] else "completed" for outcome in outcomes],
        })
        return outcomes

    def _execute_stage(self, run_id, task, stage, worktree, provider: str, run_dir: Path):
        prepared = self._prepare_stage(run_id, task, stage, worktree, provider, run_dir)
        feedback = prepared.stage_input.feedback
        if (
            self._requested_recovery_action(run_id) == "fix-output"
            and feedback is not None
            and feedback.failure_kind == "output_contract"
        ):
            prior = StageExecutionResult(
                artifact_name=f"{stage.id}.prior-output.json",
                content=feedback.prior_output_excerpt or "{}",
                summary="Retrying the previous structured output without rerunning the implementation.",
                stage_id=stage.id,
                provider=prepared.provider,
            )
            return self._commit_stage(prepared, prior)
        if feedback is not None and self._has_recovery_request(run_id):
            requested = self._requested_recovery_action(run_id)
            action = "switch-provider" if requested == "switch-provider" else "retry"
            attempt_id = claim_recovery_action(
                self.store, run_id, stage_id=stage.id, action=action,
                provider=prepared.provider, feedback=feedback,
            )
            if not attempt_id:
                raise RuntimeError("Run recovery budget is exhausted; create a new Run after addressing the diagnosis.")
            try:
                output = prepared.adapter.execute(prepared.stage_input)
            except Exception as exc:
                finish_recovery_action(
                    self.store, run_id, attempt_id=attempt_id, stage_id=stage.id,
                    action=action, provider=prepared.provider, status="failed",
                    failure_kind=feedback.failure_kind, message=f"{type(exc).__name__}: {exc}",
                )
                raise
            finish_recovery_action(
                self.store, run_id, attempt_id=attempt_id, stage_id=stage.id,
                action=action, provider=prepared.provider,
                status="succeeded" if output.returncode == 0 else "failed",
                failure_kind=feedback.failure_kind, message=output.summary,
            )
            if output.returncode == 0:
                prepared, output = self._handle_stage_interactions(prepared, output)
            return self._commit_stage(prepared, output)
        prepared, output = self._execute_prepared(prepared)
        return self._commit_stage(prepared, output)

    def _execute_prepared(
        self,
        prepared: PreparedStage,
        *,
        initial_output: StageExecutionResult | None = None,
        initial_error: BaseException | None = None,
    ) -> tuple[PreparedStage, StageExecutionResult]:
        output, error = initial_output, initial_error
        if output is None and error is None:
            try:
                output = prepared.adapter.execute(prepared.stage_input)
            except Exception as exc:
                error = exc
        if output is not None and output.returncode == 0:
            return self._handle_stage_interactions(prepared, output)
        failure = classify_provider_failure(output, exception=error)
        failure_kind = failure.kind
        feedback = build_attempt_feedback(
            output,
            prior_attempt=prepared.stage_input.attempt,
            failure_kind=failure_kind,
            workspace_diff_digest=self._subject_digest(prepared.worktree),
            error_codes=(failure.code,),
            exception=error,
        )
        requested = self._requested_recovery_action(prepared.run_id)
        current_failure_reported = False
        if failure.transient and requested != "switch-provider":
            self._worker_attempt_failed(
                prepared, failure.summary,
                returncode=output.returncode if output is not None else None,
            )
            current_failure_reported = True
            recovered = self._retry_prepared(prepared, feedback, action="retry")
            if recovered is not None:
                prepared, retried = recovered
                output = _merge_usage(output, retried) if output is not None else retried
                current_failure_reported = False
                if output.returncode == 0:
                    return self._handle_stage_interactions(prepared, output)
                retry_failure = classify_provider_failure(output)
                feedback = build_attempt_feedback(
                    output,
                    prior_attempt=prepared.stage_input.attempt,
                    failure_kind=retry_failure.kind,
                    workspace_diff_digest=self._subject_digest(prepared.worktree),
                    error_codes=(retry_failure.code, "provider_retry_failed"),
                    history=({"failure_kind": failure_kind, "output_digest": feedback.prior_output_digest},),
                )
        fallback = self._fallback_provider(
            prepared.run_id, prepared.provider, role=prepared.stage.role
        )
        if fallback and (failure.transient or requested == "switch-provider"):
            if output is not None and not current_failure_reported:
                retry_failure = classify_provider_failure(output)
                self._worker_attempt_failed(
                    prepared, retry_failure.summary, returncode=output.returncode
                )
            recovered = self._retry_prepared(prepared, feedback, action="switch-provider", provider=fallback)
            if recovered is not None:
                fallback_prepared, fallback_output = recovered
                merged = (
                    _merge_usage(output, fallback_output)
                    if output is not None else fallback_output
                )
                if merged.returncode == 0:
                    return self._handle_stage_interactions(fallback_prepared, merged)
                return fallback_prepared, merged
        if output is not None:
            return prepared, output
        assert error is not None
        raise error

    def _retry_prepared(
        self,
        prepared: PreparedStage,
        feedback: AttemptFeedback,
        *,
        action: str,
        provider: str | None = None,
    ) -> tuple[PreparedStage, StageExecutionResult] | None:
        provider = provider or prepared.provider
        attempt_id = claim_recovery_action(
            self.store, prepared.run_id, stage_id=prepared.stage.id,
            action=action, provider=provider, feedback=feedback,
        )
        if not attempt_id:
            return None
        if prepared.checkpoint:
            prepared.checkpoint.restore()
        snapshot = self._policy_snapshot(prepared.run_id)
        adapter = get_runtime_provider(
            provider, workspace=self.workspace, definition=snapshot.provider_definitions.get(provider)
        )
        stage_input = replace(
            prepared.stage_input,
            provider=provider,
            feedback=feedback,
            attempt=prepared.stage_input.attempt + 1,
        )
        self.store.append_event(
            prepared.run_id,
            "worker.started",
            {
                "worker_id": self._worker_id(
                    prepared.run_id, prepared.stage.id, stage_input.attempt
                ),
                "stage_id": prepared.stage.id,
                "role": prepared.stage.role,
                "provider": provider,
                "attempt": stage_input.attempt,
                "read_only": bool(prepared.stage.read_only),
                "status": "running",
            },
            stage_id=prepared.stage.id,
        )
        try:
            output = adapter.execute(stage_input)
        except Exception as exc:
            finish_recovery_action(
                self.store, prepared.run_id, attempt_id=attempt_id, stage_id=prepared.stage.id,
                action=action, provider=provider, status="failed", failure_kind=feedback.failure_kind,
                message=f"{type(exc).__name__}: {exc}",
            )
            failed_prepared = replace(
                prepared, provider=provider, adapter=adapter, stage_input=stage_input
            )
            return failed_prepared, StageExecutionResult(
                artifact_name=f"{prepared.stage.id}-provider-error.log",
                content="",
                summary=redact(f"{type(exc).__name__}: {exc}")[:1000],
                stage_id=prepared.stage.id,
                provider=provider,
                status="failed",
                returncode=1,
                stderr_content=f"{type(exc).__name__}: {exc}",
            )
        success = output.returncode == 0
        finish_recovery_action(
            self.store, prepared.run_id, attempt_id=attempt_id, stage_id=prepared.stage.id,
            action=action, provider=provider, status="succeeded" if success else "failed",
            failure_kind=feedback.failure_kind,
            message=output.summary,
        )
        return replace(prepared, provider=provider, adapter=adapter, stage_input=stage_input), output

    def _prepare_stage(self, run_id, task, stage, worktree, provider: str, run_dir: Path) -> PreparedStage:
        existing = next((item for item in self.store.stages(run_id) if item["stage_id"] == stage.id), None)
        requested_action = self._requested_recovery_action(run_id)
        if requested_action == "switch-provider" and existing and existing.get("status") == "failed":
            provider = self._fallback_provider(run_id, provider, role=stage.role) or provider
        attempt = int(existing.get("attempt") or 0) + 1 if existing else 1
        self.store.update_run(run_id, status="running", current_stage=stage.id)
        self.store.upsert_stage(
            run_id, stage.id, role=stage.role, provider=provider, status="running", attempt=attempt
        )
        subject = self._subject_digest(worktree)
        snapshot = self._policy_snapshot(run_id)
        raw_grant = snapshot.stage_capabilities.get(stage.id)
        if not isinstance(raw_grant, dict):
            raise RuntimeError(f"frozen capability grant is missing for stage {stage.id}")
        grant = CapabilityGrant.from_dict(raw_grant)
        checkpoint = None
        if grant.write_workspace:
            checkpoint = WorkspaceCheckpoint.create(
                worktree, run_dir / "recovery", stage_id=f"{stage.id}-{attempt}"
            )
        skills = tuple(snapshot.stage_skills.get(stage.id, []))
        self.store.append_event(run_id, "runtime.capability_grant", {
            "stage_id": stage.id,
            "provider": provider,
            "digest": grant.digest,
            "grant": grant.to_dict(),
        }, stage_id=stage.id)
        provider_definition = snapshot.provider_definitions.get(provider)
        adapter = get_runtime_provider(provider, workspace=self.workspace, definition=provider_definition)
        profile = str((self.store.get_run(run_id) or {}).get("profile") or "standard")
        settings = PROFILE_SETTINGS[profile]
        context_pack = build_context_pack(
            self.workspace, worktree, self.store, run_id=run_id, task=task
        )
        context_path = run_dir / "artifacts" / stage.id / "context-pack.json"
        context_path.parent.mkdir(parents=True, exist_ok=True)
        context_path.write_text(
            json.dumps({"manifest": context_pack.manifest, "text": context_pack.text}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.store.add_artifact(
            run_id, name=context_path.name, path=context_path, kind="context_pack",
            stage_id=stage.id, media_type="application/json",
        )
        run = self.store.get_run(run_id) or {}
        run_metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
        previous_session_id = resumable_session_id(
            run_metadata, stage_role=stage.role, provider=provider, can_write=grant.write_workspace
        )
        interaction_responses = [
            response_payload(item)
            for item in self.store.interactions(run_id)
            if item.get("stage_id") == stage.id
            and item.get("kind") == "clarification"
            and item.get("status") != "pending"
        ]
        stage_input = StageExecutionInput(
            run_id=run_id, stage_id=stage.id, role=stage.role, task=task, worktree=worktree,
            context={
                "subject_digest": subject,
                "context_pack": context_pack.text,
                "context_manifest": context_pack.manifest,
                "previous_provider_session_id": previous_session_id,
                "interaction_responses": interaction_responses,
                "delivery_standard": snapshot.delivery_standard,
                "review_standards": review_standards_for_role(
                    snapshot.delivery_standard, stage.role
                ),
            },
            capabilities=grant,
            provider=provider,
            policy={
                "output_schema": stage.output_schema,
                "timeout_seconds": settings["timeout_seconds"],
                "verification_commands": [item.model_dump(mode="json") for item in stage.verification_commands],
            },
            feedback=self._feedback_for_stage(run_id, stage.id, existing, subject),
            skills=skills,
            attempt=attempt,
        )
        self.store.append_event(
            run_id,
            "worker.started",
            {
                "worker_id": self._worker_id(run_id, stage.id, attempt),
                "stage_id": stage.id,
                "role": stage.role,
                "provider": provider,
                "attempt": attempt,
                "read_only": bool(stage.read_only),
                "status": "running",
            },
            stage_id=stage.id,
        )
        return PreparedStage(
            run_id=run_id, stage=stage, provider=provider, worktree=worktree, run_dir=run_dir,
            subject_digest=subject, adapter=adapter, stage_input=stage_input,
            retries=max(1, int(settings["retries"])),
            context_manifest=context_pack.manifest,
            checkpoint=checkpoint,
        )

    def _worker_failed(self, run_id, stage, provider, error) -> None:
        existing = next(
            (item for item in self.store.stages(run_id) if item["stage_id"] == stage.id),
            None,
        )
        attempt = int((existing or {}).get("attempt") or 1)
        self.store.append_event(
            run_id,
            "worker.failed",
            {
                "worker_id": self._worker_id(run_id, stage.id, attempt),
                "stage_id": stage.id,
                "role": stage.role,
                "provider": provider,
                "attempt": attempt,
                "status": "failed",
                "summary": redact(f"{type(error).__name__}: {error}")[:1000],
            },
            stage_id=stage.id,
        )

    def _worker_attempt_failed(
        self,
        prepared: PreparedStage,
        summary: str,
        *,
        returncode: int | None = None,
    ) -> None:
        self.store.append_event(
            prepared.run_id,
            "worker.failed",
            {
                "worker_id": self._worker_id(
                    prepared.run_id,
                    prepared.stage.id,
                    prepared.stage_input.attempt,
                ),
                "stage_id": prepared.stage.id,
                "role": prepared.stage.role,
                "provider": prepared.provider,
                "attempt": prepared.stage_input.attempt,
                "status": "failed",
                "returncode": returncode,
                "summary": redact(summary)[:1000],
            },
            stage_id=prepared.stage.id,
        )

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
        profile = str((self.store.get_run(run_id) or {}).get("profile") or "standard")
        for loop in range(1, 3):
            if not (
                bool((context.get("review") or {}).get("has_blockers"))
                or self._has_failed_checks(run_id, worktree)
            ):
                break
            context["loop"] = loop
            provider = self._stage_provider(by_id["fix"].role, route, role_providers)
            feedback = self._feedback_for_stage(
                run_id, "fix", None, self._subject_digest(worktree)
            ) or build_attempt_feedback(
                None, prior_attempt=loop, failure_kind="review_blocker",
                workspace_diff_digest=self._subject_digest(worktree),
                error_codes=("quality_gate_failed",),
            )
            recovery_id = claim_recovery_action(
                self.store, run_id, stage_id="fix", action="repair",
                provider=provider, feedback=feedback,
            )
            if not recovery_id:
                return cost
            loop_failed = False
            for stage_id in ("fix", "test"):
                stage = by_id[stage_id]
                provider = self._stage_provider(stage.role, route, role_providers)
                try:
                    output, parsed = self._execute_stage(run_id, task, stage, worktree, provider, run_dir)
                except Exception as exc:
                    self._runtime_failure(run_id, stage_id, f"Repair stage failed: {exc}")
                    loop_failed = True
                    break
                cost += output.cost_usd
                self._merge_stage_context(context, stage, parsed, output.summary)
                if output.returncode != 0 or cost > max_cost:
                    self._runtime_failure(run_id, stage_id, "Repair loop failed or exceeded the cost limit.")
                    loop_failed = True
                    break
            if loop_failed:
                finish_recovery_action(
                    self.store, run_id, attempt_id=recovery_id, stage_id="fix",
                    action="repair", provider=provider, status="failed",
                    failure_kind=feedback.failure_kind, message="Fix or test stage failed.",
                )
                return cost
            review_stages = [by_id["review"]]
            if profile == "strict":
                review_stages.append(by_id["security_review"])
            delta, failed = self._run_wave(
                run_id, task, review_stages, worktree, run_dir, route, role_providers, context
            )
            cost += delta
            if failed or cost > max_cost:
                self._runtime_failure(run_id, "repair_review", "Repair review failed or exceeded the cost limit.")
                finish_recovery_action(
                    self.store, run_id, attempt_id=recovery_id, stage_id="fix",
                    action="repair", provider=provider, status="failed",
                    failure_kind=feedback.failure_kind, message="Repair review failed.",
                )
                return cost
            repaired = not (
                bool((context.get("review") or {}).get("has_blockers"))
                or self._has_failed_checks(run_id, worktree)
            )
            finish_recovery_action(
                self.store, run_id, attempt_id=recovery_id, stage_id="fix",
                action="repair", provider=provider, status="succeeded" if repaired else "failed",
                failure_kind=feedback.failure_kind,
                message="All checks and reviews passed." if repaired else "Quality blockers remain.",
            )
        return cost

    def _run_dir(self, run_id: str) -> Path:
        path = self.workspace / ".muxdev" / "runs" / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path.resolve()


def _record_id() -> str:
    return f"ev_{uuid4().hex}"


def _merge_usage(first: StageExecutionResult, second: StageExecutionResult) -> StageExecutionResult:
    return replace(
        second,
        cost_usd=first.cost_usd + second.cost_usd,
        tokens=first.tokens + second.tokens,
    )


def _evidence_from_payload(payload: Mapping[str, Any]) -> AnyEvidenceRecord:
    classes = {
        "artifact": ArtifactEvidence,
        "check": CheckEvidence,
        "review": ReviewEvidence,
        "interaction": InteractionEvidence,
        "runtime": RuntimeEvidence,
    }
    return classes[str(payload["kind"])].model_validate(payload)
