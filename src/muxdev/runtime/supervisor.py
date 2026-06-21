"""Workflow supervisor runtime.

The supervisor is the central execution loop for M1-M7: it creates isolated run
state, prepares a worktree, loads a workflow, dispatches stages to provider
adapters, applies safety policy gates, writes the blackboard/trace, and emits a
final report. CLI, TUI, and tests call this layer instead of reimplementing
workflow semantics.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from ..models import ApprovalStatus, PolicyDecision, ProviderActionKind, ProviderActionStatus, ReviewBlocker, ReviewResult, RunStatus, StageStatus, TestResult
from ..clients.stream import StreamAdapter, StreamEventType
from ..core.platforms import hidden_subprocess_kwargs
from ..core.redaction import redact
from ..context import memory_refs as _memory_refs
from ..context import task_with_context_packet as _task_with_context_packet
from ..context import task_with_memory_context as _task_with_memory_context
from ..context import write_context_packet as _write_context_packet
from ..config.runtime import load_runtime_config, normalize_role
from ..domain import new_run_id
from ..providers.adapters import ProviderAdapter, ProviderStageOutput, extract_json_object, get_runtime_provider
from ..providers.planner import ProviderPlanner
from ..services.dashboard_run import write_run_dashboard
from ..services.design import write_design_pack, write_user_design_document
from ..services.advanced_parallel import planned_stage_writes_from_automation, record_parallel_conflicts, write_parallel_conflict_report
from ..services.provider_learning import refresh_provider_learning
from ..services.provider_scores import recommend_provider
from ..services.prompt_templates import render_stage_prompt
from ..services.evidence import write_evidence_run
from ..services.reports import generate_final_report
from ..services.semantic_merge import review_semantic_merge
from ..services.session_capsules import write_session_capsule
from ..services.skills import resolve_active_skills
from ..core.safety import SafetyPolicy, SafetyPolicyEngine
from ..storage import Blackboard, RunStore, TraceWriter, append_ledger_event, canonical_hash, sha256_file, sha256_text
from ..storage.contracts import (
    artifact_descriptor,
    write_blind_validator_panel,
    write_role_result_contract,
    write_stage_contract,
)
from ..workflows import execution_batches, load_workflow, ordered_stage_ids, should_run_when
from .stage_attempt import provider_actions_from_output as _provider_actions_from_output
from .stage_attempt import provider_attempt_status as _provider_attempt_status
from .stage_attempt import provider_failure_kind as _provider_failure_kind
from .stage_attempt import next_provider_attempt as _next_provider_attempt
from .stage_attempt import run_provider_stage as _run_provider_stage
from .stage_attempt import run_provider_stage_with_attempts as _run_provider_stage_with_attempts
from .langgraph_engine import LangGraphWorkflowEngine
from .worktree import WorktreeManager


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: RunStatus
    run_dir: Path
    report_path: Path | None

class SupervisorRuntime:
    """High-level runtime facade for starting, resuming, and retrying runs."""

    def __init__(
        self,
        workspace: Path,
        *,
        runs_dir: Path | None = None,
        state_db: Path | None = None,
        worktrees_root: Path | None = None,
        write_dashboards: bool = True,
    ):
        self.workspace = workspace
        self.store = RunStore(workspace, runs_dir=runs_dir)
        self.state_db = state_db
        self.worktrees_root = worktrees_root
        self.write_dashboards = write_dashboards

    def _blackboard(self, run_dir: Path) -> Blackboard:
        return Blackboard(run_dir, db_path=self.state_db)

    def _write_run_dashboard(self, run_dir: Path, run_id: str, *, blackboard: Blackboard | None = None) -> Path | None:
        if not self.write_dashboards:
            return None
        return write_run_dashboard(self.workspace, run_dir, run_id, blackboard=blackboard)

    def run(
        self,
        task: str,
        *,
        provider: str = "mock",
        workflow_name: str = "software-dev",
        require_approval: set[str] | None = None,
        max_cost_usd: float = 0.5,
        role_providers: dict[str, str] | None = None,
        run_id: str | None = None,
        profile: str | None = None,
        gate: str | None = None,
        skills: list[dict[str, object]] | None = None,
        ci_block_on_approval: bool = False,
        depth: str | None = None,
        topology: str | None = None,
        automation: dict[str, object] | None = None,
    ) -> RunResult:
        """Create a fresh run and execute its workflow until completion or pause."""
        run_id = run_id or new_run_id()
        run_dir = self.store.create_run_dir(run_id)
        worktree = WorktreeManager(self.workspace, self.worktrees_root).prepare(run_id, run_dir)
        blackboard = self._blackboard(run_dir)
        trace = TraceWriter(run_dir, run_id)
        policy = SafetyPolicyEngine(
            SafetyPolicy(
                max_cost_usd=max_cost_usd,
                approval_types=require_approval or set(),
                strict_approval=bool(require_approval) and gate not in {"auto", "safe"},
            )
        )
        workflow = load_workflow(workflow_name)
        # Role-specific providers are optional overrides. The default provider
        # remains the fallback for any workflow role that has no explicit choice.
        role_providers = {key: value for key, value in (role_providers or {}).items() if value}
        skills = _merge_skill_payloads(
            skills or [],
            _resolve_workflow_role_skills(self.workspace, task=task, workflow=workflow, provider=provider),
        )
        provider_impls = {provider: get_runtime_provider(provider)}
        for role_provider in role_providers.values():
            provider_impls.setdefault(role_provider, get_runtime_provider(role_provider))

        task_context = {
            "workflow_name": workflow_name,
            "profile": profile,
            "gate": gate,
            "depth": depth,
            "topology": topology,
            "skills": skills,
            "role_providers": role_providers,
            "ci_block_on_approval": ci_block_on_approval,
            "automation": automation or {},
            "safety_policy": {
                "approval_types": sorted(policy.policy.approval_types),
                "max_cost_usd": policy.policy.max_cost_usd,
                "strict_approval": policy.policy.strict_approval,
            },
        }
        (run_dir / "task.md").write_text(redact(task) + "\n", encoding="utf-8")
        (run_dir / "workflow.yaml").write_text(workflow.model_dump_json(indent=2), encoding="utf-8")
        (run_dir / "task_context.json").write_text(redact(json.dumps(task_context, ensure_ascii=False, indent=2)) + "\n", encoding="utf-8")
        blackboard.create_run(
            run_id=run_id,
            task=redact(task),
            workflow=workflow.name,
            provider=provider,
            workspace=self.workspace,
            worktree=worktree.path,
        )
        for role in sorted({stage.role for stage in workflow.stages if stage.role}):
            blackboard.upsert_agent(run_id, role, role_providers.get(role, provider))
        blackboard.add_artifact(run_id, None, "task.md", run_dir / "task.md", "task")
        blackboard.add_artifact(run_id, None, "task_context.json", run_dir / "task_context.json", "context")
        _record_ledger(blackboard, run_dir, run_id, "run_started", payload={"workflow": workflow.name, "provider": provider})
        trace.write(
            "run_started",
            provider=provider,
            worktree=str(worktree.path),
            strategy=worktree.strategy,
            profile=profile,
            gate=gate,
            depth=depth,
            topology=topology,
            intent=(automation or {}).get("intent"),
            skills=[skill.get("name") for skill in skills],
        )
        blackboard.set_run_status(run_id, RunStatus.RUNNING)
        if _task_requires_clarification(task):
            action_id = blackboard.create_provider_action(
                run_id=run_id,
                stage_id="clarify",
                provider="muxdev",
                role="requirements",
                kind=str(ProviderActionKind.CLARIFICATION_REQUIRED),
                prompt_text="Please clarify the goal, target behavior, and acceptance criteria before implementation continues.",
                input_kind="text",
                auto_policy="manual",
            )
            blackboard.upsert_stage(run_id, "clarify", role="requirements", status=StageStatus.RUNNING, summary="waiting for clarification")
            blackboard.set_run_status(run_id, RunStatus.AWAITING_PROVIDER_ACTION)
            trace.write("clarification_requested", stage="clarify", action_id=action_id)
            self._write_run_dashboard(run_dir, run_id, blackboard=blackboard)
            blackboard.close()
            return RunResult(run_id, RunStatus.AWAITING_PROVIDER_ACTION, run_dir, None)
        result = self._execute_workflow(
            run_id=run_id,
            run_dir=run_dir,
            blackboard=blackboard,
            trace=trace,
            task=task,
            provider=provider,
            workflow_name=workflow_name,
            provider_impls=provider_impls,
            policy=policy,
            worktree=worktree.path,
            role_providers=role_providers,
            skills=skills,
            ci_block_on_approval=ci_block_on_approval,
            automation=automation or {},
            close_blackboard=True,
        )
        self._write_run_dashboard(run_dir, run_id)
        return result

    def resume(
        self,
        run_id: str,
        *,
        max_cost_usd: float = 0.5,
        on_missing_worktree: str = "report",
    ) -> RunResult:
        """Continue an existing run after approval, interruption, or retry."""
        run_dir = self.store.find_run_dir(run_id)
        blackboard = self._blackboard(run_dir)
        trace = TraceWriter(run_dir, run_id)
        try:
            run = blackboard.get_run(run_id)
            pending = blackboard.list_approvals(status=str(ApprovalStatus.PENDING), run_id=run_id)
            if pending:
                trace.write("resume_waiting_approval", approvals=[row["approval_id"] for row in pending])
                self._write_run_dashboard(run_dir, run_id, blackboard=blackboard)
                return RunResult(run_id, RunStatus.AWAITING_APPROVAL, run_dir, None)
            pending_actions = blackboard.list_provider_actions(status=str(ProviderActionStatus.PENDING), run_id=run_id)
            if pending_actions:
                blackboard.set_run_status(run_id, RunStatus.AWAITING_PROVIDER_ACTION)
                trace.write("resume_waiting_provider_action", actions=[row["action_id"] for row in pending_actions])
                self._write_run_dashboard(run_dir, run_id, blackboard=blackboard)
                return RunResult(run_id, RunStatus.AWAITING_PROVIDER_ACTION, run_dir, None)
            worktree = Path(run["worktree"])
            if not worktree.exists():
                message = f"worktree missing: {worktree}"
                blackboard.add_error(run_id, None, "missing_worktree", message)
                trace.write("resume_missing_worktree", worktree=str(worktree), action=on_missing_worktree)
                if on_missing_worktree == "abort":
                    blackboard.set_run_status(run_id, RunStatus.ABORTED)
                    self._write_run_dashboard(run_dir, run_id, blackboard=blackboard)
                    return RunResult(run_id, RunStatus.ABORTED, run_dir, None)
                report_path = generate_final_report(run_dir, run_id, blackboard)
                self._write_run_dashboard(run_dir, run_id, blackboard=blackboard)
                return RunResult(run_id, RunStatus.BLOCKED, run_dir, report_path)
            provider = str(run["provider"])
            task_context = _read_task_context(run_dir)
            role_providers = {
                str(key): str(value)
                for key, value in (task_context.get("role_providers", {}) if isinstance(task_context.get("role_providers"), dict) else {}).items()
                if value
            }
            skills = task_context.get("skills", []) if isinstance(task_context.get("skills"), list) else []
            automation = task_context.get("automation", {}) if isinstance(task_context.get("automation"), dict) else {}
            stored_policy = task_context.get("safety_policy", {}) if isinstance(task_context.get("safety_policy"), dict) else {}
            stored_approvals = stored_policy.get("approval_types", []) if isinstance(stored_policy, dict) else []
            approval_types = {str(item) for item in stored_approvals} if isinstance(stored_approvals, list) else set()
            policy = SafetyPolicyEngine(
                SafetyPolicy(
                    max_cost_usd=max_cost_usd,
                    approval_types=approval_types,
                    strict_approval=bool(stored_policy.get("strict_approval")),
                )
            )
            provider_impls = {provider: get_runtime_provider(provider)}
            for role_provider in role_providers.values():
                provider_impls.setdefault(role_provider, get_runtime_provider(role_provider))
            blackboard.set_run_status(run_id, RunStatus.RUNNING)
            trace.write("run_resumed", provider=provider, worktree=str(worktree), skills=[skill.get("name") for skill in skills if isinstance(skill, dict)])
            result = self._execute_workflow(
                run_id=run_id,
                run_dir=run_dir,
                blackboard=blackboard,
                trace=trace,
                task=str(run["task"]),
                provider=provider,
                workflow_name=str(task_context.get("workflow_name") or run["workflow"]),
                provider_impls=provider_impls,
                policy=policy,
                worktree=worktree,
                role_providers=role_providers,
                skills=skills,
                ci_block_on_approval=bool(task_context.get("ci_block_on_approval", False)),
                automation=automation,
                close_blackboard=False,
            )
            self._write_run_dashboard(run_dir, run_id, blackboard=blackboard)
            return result
        finally:
            blackboard.close()

    def retry(self, run_id: str, stage_id: str, *, max_cost_usd: float = 0.5) -> RunResult:
        """Reset one stage and then reuse the normal resume path."""
        run_dir = self.store.find_run_dir(run_id)
        blackboard = self._blackboard(run_dir)
        try:
            blackboard.reset_stage(run_id, stage_id)
        finally:
            blackboard.close()
        return self.resume(run_id, max_cost_usd=max_cost_usd)

    def _execute_workflow(
        self,
        *,
        run_id: str,
        run_dir: Path,
        blackboard: Blackboard,
        trace: TraceWriter,
        task: str,
        provider: str,
        workflow_name: str,
        provider_impls: dict[str, ProviderAdapter],
        policy: SafetyPolicyEngine,
        worktree: Path,
        role_providers: dict[str, str],
        skills: list[dict[str, object]],
        ci_block_on_approval: bool,
        automation: dict[str, object],
        close_blackboard: bool,
    ) -> RunResult:
        workflow = load_workflow(workflow_name)
        engine_name = _configured_workflow_engine(self.workspace, automation)
        if engine_name == "native":
            trace.write("native_runtime_selected", workflow=workflow.name)
            return self._execute_native_workflow(
                run_id=run_id,
                run_dir=run_dir,
                blackboard=blackboard,
                trace=trace,
                task=task,
                provider=provider,
                workflow_name=workflow_name,
                provider_impls=provider_impls,
                policy=policy,
                worktree=worktree,
                role_providers=role_providers,
                skills=skills,
                ci_block_on_approval=ci_block_on_approval,
                automation=automation,
                close_blackboard=close_blackboard,
            )
        return LangGraphWorkflowEngine(self.workspace).execute(
            workflow=workflow,
            run_id=run_id,
            task=task,
            trace=trace,
            native_executor=lambda: self._execute_native_workflow(
                run_id=run_id,
                run_dir=run_dir,
                blackboard=blackboard,
                trace=trace,
                task=task,
                provider=provider,
                workflow_name=workflow_name,
                provider_impls=provider_impls,
                policy=policy,
                worktree=worktree,
                role_providers=role_providers,
                skills=skills,
                ci_block_on_approval=ci_block_on_approval,
                automation=automation,
                close_blackboard=close_blackboard,
            ),
        )

    def _execute_native_workflow(
        self,
        *,
        run_id: str,
        run_dir: Path,
        blackboard: Blackboard,
        trace: TraceWriter,
        task: str,
        provider: str,
        workflow_name: str,
        provider_impls: dict[str, ProviderAdapter],
        policy: SafetyPolicyEngine,
        worktree: Path,
        role_providers: dict[str, str],
        skills: list[dict[str, object]],
        ci_block_on_approval: bool,
        automation: dict[str, object],
        close_blackboard: bool,
    ) -> RunResult:
        """Execute workflow stages in dependency order.

        The method keeps the serial path explicit because it is the safest and
        easiest-to-audit behavior. Only workflows proven safe by
        _can_use_parallel_runtime are delegated to the parallel executor.
        """
        workflow = load_workflow(workflow_name)
        bound_task = _task_with_memory_context(task, automation)
        task_hash = sha256_text(redact(task))
        workflow_hash = sha256_file(run_dir / "workflow.yaml") if (run_dir / "workflow.yaml").exists() else sha256_text(workflow.model_dump_json())
        policy_hash = _policy_hash(policy)

        context: dict[str, object] = _initial_workflow_context(blackboard, run_id, workflow)
        by_id = {stage.id: stage for stage in workflow.stages}
        report_path: Path | None = None
        try:
            # Resume/retry support starts by reading persisted stage state, so
            # reruns skip already completed or intentionally skipped stages.
            completed = {
                row["stage_id"]
                for row in blackboard.table_rows("stages", run_id=run_id)
                if row["status"] in {StageStatus.COMPLETED, StageStatus.SKIPPED}
            }
            if workflow.max_parallel > 1 and _can_use_parallel_runtime(workflow):
                return self._execute_parallel_workflow(
                    run_id=run_id,
                    run_dir=run_dir,
                    blackboard=blackboard,
                    trace=trace,
                    task=task,
                    provider=provider,
                    workflow=workflow,
                    provider_impls=provider_impls,
                    policy=policy,
                    worktree=worktree,
                    role_providers=role_providers,
                    skills=skills,
                    ci_block_on_approval=ci_block_on_approval,
                    automation=automation,
                    completed=completed,
                )

            ordered = ordered_stage_ids(workflow)
            index = 0
            while index < len(ordered):
                stage_id = ordered[index]
                stage = by_id[stage_id]
                if stage.id in completed:
                    trace.write("stage_resumed_skip", stage=stage.id)
                    index += 1
                    continue
                context["max_loops"] = _effective_max_loops(stage, automation)
                if not should_run_when(stage.when, context):
                    if _loop_stage_exhausted(stage, context):
                        max_loops = _effective_max_loops(stage, automation)
                        review_stage = _loop_review_stage(stage)
                        blackboard.add_error(run_id, stage.id, "review_blockers", f"{review_stage} blockers remain after {max_loops} revision loop(s)")
                        blackboard.set_run_status(run_id, RunStatus.BLOCKED)
                        trace.write("loop_blocked", stage=stage.id, review_stage=review_stage, loop=context.get("loop"), max_loops=max_loops, reason="review blockers remain")
                        trace.write("run_blocked", stage=stage.id, reason="review blockers remain", review_stage=review_stage, loop=context.get("loop"), max_loops=max_loops)
                        return RunResult(run_id, RunStatus.BLOCKED, run_dir, None)
                    blackboard.upsert_stage(run_id, stage.id, role=stage.role, status=StageStatus.SKIPPED, summary="when condition false")
                    trace.write("stage_skipped", stage=stage.id)
                    index += 1
                    continue
                blackboard.add_checkpoint(run_id, stage.id, "stage_started")
                blackboard.upsert_stage(run_id, stage.id, role=stage.role, status=StageStatus.RUNNING)
                trace.write("stage_started", stage=stage.id, role=stage.role, type=stage.type)
                stage_provider = _stage_provider_for(
                    blackboard,
                    trace,
                    stage_id=stage.id,
                    role=stage.role,
                    fallback=provider,
                    role_providers=role_providers,
                )
                snapshot = _record_stage_snapshot(blackboard, run_dir=run_dir, run_id=run_id, stage_id=stage.id, worktree=worktree)
                stage_contract_path, stage_contract_hash, _ = write_stage_contract(
                    run_dir,
                    run_id=run_id,
                    stage_id=stage.id,
                    role=stage.role,
                    provider=stage_provider,
                    task_hash=task_hash,
                    workflow_hash=workflow_hash,
                    pre_patch_hash=str(snapshot["patch_hash"]),
                )
                blackboard.add_stage_contract(run_id, stage.id, role=stage.role, provider=stage_provider, path=stage_contract_path, contract_hash=stage_contract_hash)
                blackboard.add_artifact(run_id, stage.id, stage_contract_path.name, stage_contract_path, "stage_contract")
                _record_ledger(
                    blackboard,
                    run_dir,
                    run_id,
                    "stage_contract_written",
                    stage_id=stage.id,
                    payload={"contract_hash": stage_contract_hash, "snapshot": snapshot},
                )

                if stage.type == "human_gate":
                    approval_type = stage.approval_type or "plan"
                    approval_reason = stage.approval_reason or ("approve generated plan" if approval_type == "plan" else f"approve {approval_type} gate")
                    if approval_type == "design":
                        _record_design_pack(blackboard, run_dir=run_dir, run_id=run_id, task=task, workflow=workflow.name, automation=automation)
                    subject = _approval_subject(
                        blackboard,
                        run_id=run_id,
                        approval_type=approval_type,
                        stage_id=stage.id,
                        policy_hash=policy_hash,
                        extra=_human_gate_subject_extra(blackboard, run_dir=run_dir, run_id=run_id, approval_type=approval_type),
                    )
                    if self._approval_gate(
                        blackboard,
                        trace,
                        run_id,
                        stage.id,
                        approval_type,
                        approval_reason,
                        policy,
                        subject=subject,
                    ):
                        blackboard.set_run_status(run_id, _approval_wait_status(ci_block_on_approval))
                        blackboard.upsert_stage(run_id, stage.id, role=stage.role, status=StageStatus.RUNNING, summary="waiting for approval")
                        return RunResult(run_id, _approval_wait_status(ci_block_on_approval), run_dir, None)
                    blackboard.upsert_stage(run_id, stage.id, role=stage.role, status=StageStatus.COMPLETED, summary="approval not required")
                    blackboard.add_checkpoint(run_id, stage.id, "stage_completed")
                    trace.write("stage_completed", stage=stage.id)
                    index += 1
                    continue

                if stage.allow_write:
                    subject = _approval_subject(
                        blackboard,
                        run_id=run_id,
                        approval_type="write",
                        stage_id=stage.id,
                        policy_hash=policy_hash,
                        extra={"pre_patch_hash": _worktree_patch_hash(worktree)},
                    )
                    if self._approval_gate(
                        blackboard,
                        trace,
                        run_id,
                        stage.id,
                        "write",
                        f"allow write operations for stage {stage.id}",
                        policy,
                        subject=subject,
                    ):
                        blackboard.set_run_status(run_id, _approval_wait_status(ci_block_on_approval))
                        return RunResult(run_id, _approval_wait_status(ci_block_on_approval), run_dir, None)

                if stage.allow_shell:
                    decision = policy.evaluate_shell("pytest")
                    trace.write(
                        "policy_decision",
                        stage=stage.id,
                        command="pytest",
                        decision=str(decision.decision),
                        reason=decision.reason,
                        standard=decision.standard.to_dict() if decision.standard else {},
                    )
                    if decision.decision == PolicyDecision.DENY:
                        blackboard.set_run_status(run_id, RunStatus.BLOCKED)
                        return RunResult(run_id, RunStatus.BLOCKED, run_dir, None)
                    if self._approval_gate(
                        blackboard,
                        trace,
                        run_id,
                        stage.id,
                        "shell",
                        "allow shell command: pytest",
                        policy,
                        subject=_approval_subject(
                            blackboard,
                            run_id=run_id,
                            approval_type="shell",
                            stage_id=stage.id,
                            policy_hash=policy_hash,
                            extra={"command": "pytest"},
                        ),
                    ):
                        blackboard.set_run_status(run_id, _approval_wait_status(ci_block_on_approval))
                        return RunResult(run_id, _approval_wait_status(ci_block_on_approval), run_dir, None)

                budget = policy.evaluate_budget(blackboard.usage_total_cost(run_id), 0.01)
                trace.write("budget_check", stage=stage.id, decision=str(budget.decision), reason=budget.reason)
                if budget.decision == PolicyDecision.DENY:
                    blackboard.set_run_status(run_id, RunStatus.PAUSED_BUDGET)
                    return RunResult(run_id, RunStatus.PAUSED_BUDGET, run_dir, None)

                provider_impl = provider_impls.setdefault(stage_provider, get_runtime_provider(stage_provider))
                stage_skills = _skills_for_stage(skills, role=stage.role, stage_id=stage.id)
                if stage_skills:
                    trace.write("skills_activated", stage=stage.id, role=stage.role, skills=[skill.get("name") for skill in stage_skills])
                context_packet_path, context_packet_hash = _write_context_packet(
                    blackboard,
                    run_dir=run_dir,
                    run_id=run_id,
                    stage_id=stage.id,
                    role=stage.role,
                    provider=stage_provider,
                    workflow=workflow.name,
                    task=task,
                    worktree=worktree,
                    skills=stage_skills,
                    automation=automation,
                    trace=trace,
                    context_sources=stage.context_sources,
                    rag_query=stage.rag_query,
                    loop_state=_loop_state(stage, context, automation),
                )
                output, attempt = _run_provider_stage_with_attempts(
                    blackboard,
                    trace,
                    run_id=run_id,
                    stage_id=stage.id,
                    role=stage.role,
                    provider=stage_provider,
                    provider_impl=provider_impl,
                    task=_task_with_context_packet(
                        _render_stage_task(bound_task, workflow_name=workflow.name, stage=stage, trace=trace),
                        context_packet_path,
                        context_packet_hash,
                    ),
                    worktree=worktree,
                    skills=stage_skills,
                    session_dir=run_dir / "provider_sessions",
                )
                artifact_path = run_dir / output.artifact_name
                artifact_path.parent.mkdir(parents=True, exist_ok=True)
                if not artifact_path.exists():
                    artifact_path.write_text(redact(output.content), encoding="utf-8")
                blackboard.add_usage(run_id, stage_provider, output.tokens, output.cost_usd)
                blackboard.add_artifact(run_id, stage.id, output.artifact_name, artifact_path, "stage_output")
                trace.write(
                    "provider_event",
                    stage=stage.id,
                    provider=stage_provider,
                    returncode=output.returncode,
                    artifact=str(artifact_path),
                )
                blackboard.complete_provider_attempt(
                    run_id,
                    stage.id,
                    provider=stage_provider,
                    attempt=attempt,
                    status=_provider_attempt_status(output),
                    failure_kind=_provider_failure_kind(output),
                    returncode=output.returncode,
                    summary=output.summary,
                    artifact_path=str(artifact_path),
                )
                action_ids = _record_provider_actions(
                    blackboard,
                    run_id=run_id,
                    stage_id=stage.id,
                    provider=stage_provider,
                    role=stage.role,
                    output=output,
                )
                if action_ids:
                    capsule_path = _record_session_capsule(
                        blackboard,
                        run_dir=run_dir,
                        run_id=run_id,
                        stage_id=stage.id,
                        role=stage.role,
                        provider=stage_provider,
                        worktree=worktree,
                        kind=_provider_failure_kind(output) or str(ProviderActionKind.PROVIDER_BLOCKED),
                        summary=output.summary,
                        snapshot_ref=str(snapshot["path"]),
                        artifact_path=artifact_path,
                        automation=automation,
                        provider_action_ids=action_ids,
                    )
                    blackboard.complete_provider_attempt(
                        run_id,
                        stage.id,
                        provider=stage_provider,
                        attempt=attempt,
                        status="provider_action",
                        failure_kind=_provider_failure_kind(output),
                        returncode=output.returncode,
                        summary=output.summary,
                        artifact_path=str(artifact_path),
                        capsule_path=str(capsule_path),
                    )
                    blackboard.set_run_status(run_id, RunStatus.AWAITING_PROVIDER_ACTION)
                    blackboard.upsert_stage(
                        run_id,
                        stage.id,
                        role=stage.role,
                        status=StageStatus.RUNNING,
                        output_path=str(artifact_path),
                        summary=f"waiting for provider action: {', '.join(action_ids)}",
                    )
                    trace.write("provider_action_requested", stage=stage.id, action_ids=action_ids, provider=stage_provider)
                    return RunResult(run_id, RunStatus.AWAITING_PROVIDER_ACTION, run_dir, None)
                if output.returncode != 0:
                    capsule_path = _record_session_capsule(
                        blackboard,
                        run_dir=run_dir,
                        run_id=run_id,
                        stage_id=stage.id,
                        role=stage.role,
                        provider=stage_provider,
                        worktree=worktree,
                        kind=_provider_failure_kind(output) or "provider_exit",
                        summary=output.summary,
                        snapshot_ref=str(snapshot["path"]),
                        artifact_path=artifact_path,
                        automation=automation,
                    )
                    blackboard.complete_provider_attempt(
                        run_id,
                        stage.id,
                        provider=stage_provider,
                        attempt=attempt,
                        status="failed",
                        failure_kind=_provider_failure_kind(output) or "provider_exit",
                        returncode=output.returncode,
                        summary=output.summary,
                        artifact_path=str(artifact_path),
                        capsule_path=str(capsule_path),
                    )
                    _record_role_result(
                        blackboard,
                        run_dir=run_dir,
                        run_id=run_id,
                        stage_id=stage.id,
                        role=stage.role,
                        provider=stage_provider,
                        decision="reject",
                        summary=output.summary,
                        findings=[{"severity": "high", "type": _provider_failure_kind(output) or "provider_exit", "summary": output.summary}],
                        artifact_path=artifact_path,
                        worktree=worktree,
                        snapshot_ref=str(snapshot["path"]),
                    )
                    blackboard.upsert_stage(
                        run_id,
                        stage.id,
                        role=stage.role,
                        status=StageStatus.FAILED,
                        output_path=str(artifact_path),
                        summary=output.summary,
                    )
                    blackboard.add_error(run_id, stage.id, _provider_failure_kind(output) or "provider_exit", output.summary)
                    blackboard.set_run_status(run_id, RunStatus.BLOCKED)
                    trace.write("stage_failed", stage=stage.id, returncode=output.returncode)
                    return RunResult(run_id, RunStatus.BLOCKED, run_dir, None)
                if stage.read_only and str(snapshot.get("diff_hash") or snapshot["patch_hash"]) != _worktree_patch_hash(worktree):
                    summary = f"read-only stage {stage.id} modified the worktree"
                    capsule_path = _record_session_capsule(
                        blackboard,
                        run_dir=run_dir,
                        run_id=run_id,
                        stage_id=stage.id,
                        role=stage.role,
                        provider=stage_provider,
                        worktree=worktree,
                        kind="read_only_write_violation",
                        summary=summary,
                        snapshot_ref=str(snapshot["path"]),
                        artifact_path=artifact_path,
                        automation=automation,
                    )
                    blackboard.complete_provider_attempt(
                        run_id,
                        stage.id,
                        provider=stage_provider,
                        attempt=attempt,
                        status="read_only_violation",
                        failure_kind="read_only_write_violation",
                        returncode=output.returncode,
                        summary=summary,
                        artifact_path=str(artifact_path),
                        capsule_path=str(capsule_path),
                    )
                    _record_role_result(
                        blackboard,
                        run_dir=run_dir,
                        run_id=run_id,
                        stage_id=stage.id,
                        role=stage.role,
                        provider=stage_provider,
                        decision="reject",
                        summary=summary,
                        findings=[{"severity": "high", "type": "read_only_write_violation", "summary": summary}],
                        artifact_path=artifact_path,
                        worktree=worktree,
                        snapshot_ref=str(snapshot["path"]),
                    )
                    blackboard.upsert_stage(run_id, stage.id, role=stage.role, status=StageStatus.FAILED, output_path=str(artifact_path), summary=summary)
                    blackboard.add_error(run_id, stage.id, "read_only_write_violation", summary)
                    blackboard.set_run_status(run_id, RunStatus.BLOCKED)
                    trace.write("stage_failed", stage=stage.id, reason="read_only_write_violation")
                    return RunResult(run_id, RunStatus.BLOCKED, run_dir, None)
                if workflow.name == "software-dev" and stage.id == "design":
                    try:
                        design_doc = _record_project_design_doc(
                            blackboard,
                            run_id=run_id,
                            stage_id=stage.id,
                            workspace=self.workspace,
                            worktree=worktree,
                            content=output.content,
                        )
                    except Exception as exc:
                        message = f"required design document was not created: {exc}"
                        blackboard.upsert_stage(run_id, stage.id, role=stage.role, status=StageStatus.FAILED, output_path=str(artifact_path), summary=message)
                        blackboard.add_error(run_id, stage.id, "missing_design_document", message)
                        blackboard.set_run_status(run_id, RunStatus.BLOCKED)
                        trace.write("stage_failed", stage=stage.id, reason="missing_design_document", error=redact(str(exc)))
                        return RunResult(run_id, RunStatus.BLOCKED, run_dir, None)
                    trace.write("project_design_doc_written", stage=stage.id, path=str(design_doc))
                findings: list[dict[str, object]] = []
                decision_text = "accept"
                if stage.id == "test":
                    parsed = extract_json_object(output.content) or {}
                    test_result = TestResult(
                        passed=bool(parsed.get("passed", True)),
                        command=str(parsed.get("command", "pytest")),
                        summary=str(parsed.get("summary", output.summary)),
                    )
                    blackboard.add_test_result(run_id, stage.id, test_result.passed, test_result.command, test_result.summary)
                    if not test_result.passed:
                        decision_text = "reject"
                        findings.append({"severity": "high", "type": "test_failure", "summary": test_result.summary})
                if _is_review_stage(stage):
                    review = _parse_review_result(output.content)
                    for blocker in review.blockers:
                        blackboard.add_review_blocker(
                            run_id,
                            stage.id,
                            type=blocker.type,
                            file=blocker.file,
                            line=blocker.line,
                            severity=blocker.severity,
                            suggestion=blocker.suggestion,
                        )
                    context[stage.id] = review.model_dump()
                    if stage.id == "review":
                        context["review"] = review.model_dump()
                    if review.has_blockers:
                        decision_text = "reject"
                        findings.extend(blocker.model_dump() for blocker in review.blockers)
                _record_role_result(
                    blackboard,
                    run_dir=run_dir,
                    run_id=run_id,
                    stage_id=stage.id,
                    role=stage.role,
                    provider=stage_provider,
                    decision=decision_text,
                    summary=output.summary,
                    findings=findings,
                    artifact_path=artifact_path,
                    worktree=worktree,
                    snapshot_ref=str(snapshot["path"]),
                )
                blackboard.upsert_stage(run_id, stage.id, role=stage.role, status=StageStatus.COMPLETED, output_path=str(artifact_path), summary=output.summary)
                blackboard.add_checkpoint(run_id, stage.id, "stage_completed")
                trace.write("stage_completed", stage=stage.id, output=str(artifact_path))
                completed.add(stage.id)
                if _stage_triggers_review_loop(stage, context):
                    previous_loop = int(context.get("loop", 0))
                    context["loop"] = int(context.get("loop", 0)) + 1
                    max_loops = _effective_max_loops(stage, automation)
                    review_stage = _loop_review_stage(stage)
                    if previous_loop == 0:
                        trace.write("loop_started", stage=stage.id, review_stage=review_stage, max_loops=max_loops, loop_kind="review_fix")
                    trace.write("review_loop_iteration", stage=stage.id, review_stage=review_stage, loop=context["loop"], max_loops=max_loops)
                    trace.write("loop_iteration_completed", stage=stage.id, review_stage=review_stage, loop=context["loop"], max_loops=max_loops, loop_kind="review_fix")
                    if stage.id == "fix":
                        trace.write("fix_loop_iteration", stage=stage.id, loop=context["loop"], max_loops=max_loops)
                    for reset_stage in _loop_reset_stages(stage):
                        if reset_stage in by_id:
                            blackboard.reset_stage(run_id, reset_stage)
                            completed.discard(reset_stage)
                    restart = stage.loop_restart_stage or ("test" if stage.id == "fix" and "test" in by_id else review_stage)
                    index = ordered.index(restart)
                    continue
                index += 1

            if int(context.get("loop", 0) or 0) > 0:
                trace.write("loop_stopped", loop=context.get("loop"), reason="workflow conditions satisfied")
            diff_path = self.write_diff(run_dir, worktree)
            blackboard.add_artifact(run_id, None, "diff.patch", diff_path, "diff")
            semantic = _record_semantic_merge_review(blackboard, run_dir=run_dir, run_id=run_id, task=task, patch_text=diff_path.read_text(encoding="utf-8", errors="replace"))
            if semantic.get("decision") == "reject":
                _refresh_provider_learning(blackboard, run_id)
                blackboard.set_run_status(run_id, RunStatus.BLOCKED)
                blackboard.add_error(run_id, None, "semantic_merge_reject", "semantic merge reviewer rejected the patch")
                _record_evidence_run(blackboard, run_dir=run_dir, run_id=run_id, trace=trace)
                trace.write("run_blocked", reason="semantic merge reviewer rejected the patch", semantic_review_hash=semantic.get("review_hash"))
                return RunResult(run_id, RunStatus.BLOCKED, run_dir, None)
            validator = _record_blind_validator(blackboard, run_dir=run_dir, run_id=run_id, task_hash=task_hash, patch_hash=sha256_file(diff_path))
            if validator.get("decision") == "reject":
                _refresh_provider_learning(blackboard, run_id)
                blackboard.set_run_status(run_id, RunStatus.BLOCKED)
                blackboard.add_error(run_id, None, "blind_validator_reject", "blind validator rejected the patch")
                _record_evidence_run(blackboard, run_dir=run_dir, run_id=run_id, trace=trace)
                trace.write("run_blocked", reason="blind validator rejected the patch", validator_hash=validator.get("validator_hash"))
                return RunResult(run_id, RunStatus.BLOCKED, run_dir, None)
            if self._approval_gate(
                blackboard,
                trace,
                run_id,
                None,
                "merge",
                "approve final merge gate",
                policy,
                subject=_approval_subject(
                        blackboard,
                        run_id=run_id,
                        approval_type="merge",
                        stage_id=None,
                        policy_hash=policy_hash,
                        extra={"patch_hash": sha256_file(diff_path), "validator_hash": validator.get("validator_hash"), "semantic_review_hash": semantic.get("review_hash")},
                    ),
                ):
                blackboard.set_run_status(run_id, _approval_wait_status(ci_block_on_approval))
                return RunResult(run_id, _approval_wait_status(ci_block_on_approval), run_dir, None)
            _apply_approved_worktree_changes(
                blackboard,
                trace,
                run_dir=run_dir,
                run_id=run_id,
                workspace=self.workspace,
                worktree=worktree,
            )
            blackboard.set_run_status(run_id, RunStatus.COMPLETED)
            if workflow.name in {"design", "design-lite", "design-v2"}:
                try:
                    _record_design_deliverables(
                        blackboard,
                        run_dir=run_dir,
                        run_id=run_id,
                        workspace=self.workspace,
                        task=task,
                        workflow=workflow.name,
                        automation=automation,
                    )
                except Exception as exc:
                    message = f"required design document was not created: {exc}"
                    blackboard.set_run_status(run_id, RunStatus.BLOCKED)
                    blackboard.add_error(run_id, None, "missing_design_document", message)
                    trace.write("run_blocked", reason="missing_design_document", error=redact(str(exc)))
                    return RunResult(run_id, RunStatus.BLOCKED, run_dir, None)
            _record_evidence_run(blackboard, run_dir=run_dir, run_id=run_id, trace=trace)
            report_path = generate_final_report(run_dir, run_id, blackboard)
            _refresh_provider_learning(blackboard, run_id)
            _record_ledger(blackboard, run_dir, run_id, "run_completed", payload={"report": str(report_path), "patch_hash": sha256_file(diff_path)})
            trace.write("run_completed", report=str(report_path))
            return RunResult(run_id, RunStatus.COMPLETED, run_dir, report_path)
        except Exception as exc:
            blackboard.set_run_status(run_id, RunStatus.BLOCKED)
            blackboard.add_error(run_id, None, "exception", str(exc))
            trace.write("error", error=redact(str(exc)))
            raise
        finally:
            if close_blackboard:
                blackboard.close()

    def _execute_parallel_workflow(
        self,
        *,
        run_id: str,
        run_dir: Path,
        blackboard: Blackboard,
        trace: TraceWriter,
        task: str,
        provider: str,
        workflow,
        provider_impls: dict[str, ProviderAdapter],
        policy: SafetyPolicyEngine,
        worktree: Path,
        role_providers: dict[str, str],
        skills: list[dict[str, object]],
        ci_block_on_approval: bool,
        automation: dict[str, object],
        completed: set[str],
    ) -> RunResult:
        by_id = {stage.id: stage for stage in workflow.stages}
        bound_task = _task_with_memory_context(task, automation)
        task_hash = sha256_text(redact(task))
        workflow_hash = sha256_file(run_dir / "workflow.yaml") if (run_dir / "workflow.yaml").exists() else sha256_text(workflow.model_dump_json())
        policy_hash = _policy_hash(policy)
        for batch in execution_batches(workflow):
            runnable = [by_id[stage_id] for stage_id in batch if stage_id not in completed]
            if not runnable:
                continue
            stage_providers: dict[str, str] = {}
            snapshots: dict[str, dict[str, object]] = {}
            trace.write("parallel_batch_started", stages=[stage.id for stage in runnable], max_parallel=workflow.max_parallel)
            planned_writes = planned_stage_writes_from_automation(automation, [stage.id for stage in runnable])
            if planned_writes:
                conflicts = record_parallel_conflicts(blackboard, run_id=run_id, stage_id="parallel_batch", stage_writes=planned_writes)
                if conflicts:
                    report = write_parallel_conflict_report(run_dir, run_id=run_id, conflicts=conflicts)
                    blackboard.add_artifact(run_id, None, report.name, report, "parallel_conflicts")
                    trace.write("parallel_conflicts_detected", stages=[stage.id for stage in runnable], conflicts=[row["conflict_id"] for row in conflicts])
                    if any(row.get("severity") == "high" for row in conflicts):
                        _refresh_provider_learning(blackboard, run_id)
                        blackboard.add_error(run_id, None, "parallel_conflict", "parallel-squad write conflict requires serialized handoff")
                        blackboard.set_run_status(run_id, RunStatus.BLOCKED)
                        return RunResult(run_id, RunStatus.BLOCKED, run_dir, None)
            for stage in runnable:
                blackboard.add_checkpoint(run_id, stage.id, "stage_started")
                blackboard.upsert_stage(run_id, stage.id, role=stage.role, status=StageStatus.RUNNING)
                trace.write("stage_started", stage=stage.id, role=stage.role, type=stage.type)
                stage_provider = _stage_provider_for(
                    blackboard,
                    trace,
                    stage_id=stage.id,
                    role=stage.role,
                    fallback=provider,
                    role_providers=role_providers,
                )
                stage_providers[stage.id] = stage_provider
                snapshot = _record_stage_snapshot(blackboard, run_dir=run_dir, run_id=run_id, stage_id=stage.id, worktree=worktree)
                snapshots[stage.id] = snapshot
                contract_path, contract_hash, _ = write_stage_contract(
                    run_dir,
                    run_id=run_id,
                    stage_id=stage.id,
                    role=stage.role,
                    provider=stage_provider,
                    task_hash=task_hash,
                    workflow_hash=workflow_hash,
                    pre_patch_hash=str(snapshot["patch_hash"]),
                )
                blackboard.add_stage_contract(run_id, stage.id, role=stage.role, provider=stage_provider, path=contract_path, contract_hash=contract_hash)
                blackboard.add_artifact(run_id, stage.id, contract_path.name, contract_path, "stage_contract")
                _record_ledger(blackboard, run_dir, run_id, "stage_contract_written", stage_id=stage.id, payload={"contract_hash": contract_hash})
                budget = policy.evaluate_budget(blackboard.usage_total_cost(run_id), 0.01)
                trace.write("budget_check", stage=stage.id, decision=str(budget.decision), reason=budget.reason)
                if budget.decision == PolicyDecision.DENY:
                    blackboard.set_run_status(run_id, RunStatus.PAUSED_BUDGET)
                    return RunResult(run_id, RunStatus.PAUSED_BUDGET, run_dir, None)

            futures = {}
            with ThreadPoolExecutor(max_workers=min(workflow.max_parallel, len(runnable))) as executor:
                for stage in runnable:
                    stage_provider = stage_providers[stage.id]
                    provider_impl = provider_impls.setdefault(stage_provider, get_runtime_provider(stage_provider))
                    stage_skills = _skills_for_stage(skills, role=stage.role, stage_id=stage.id)
                    if stage_skills:
                        trace.write("skills_activated", stage=stage.id, role=stage.role, skills=[skill.get("name") for skill in stage_skills])
                    context_packet_path, context_packet_hash = _write_context_packet(
                        blackboard,
                        run_dir=run_dir,
                        run_id=run_id,
                        stage_id=stage.id,
                        role=stage.role,
                        provider=stage_provider,
                        workflow=workflow.name,
                        task=task,
                        worktree=worktree,
                        skills=stage_skills,
                        automation=automation,
                        trace=trace,
                        context_sources=stage.context_sources,
                        rag_query=stage.rag_query,
                        loop_state={},
                    )
                    attempt = _next_provider_attempt(blackboard, run_id, stage.id, stage_provider)
                    blackboard.start_provider_attempt(run_id, stage.id, provider=stage_provider, role=stage.role, attempt=attempt)
                    trace.write("provider_attempt_started", stage=stage.id, provider=stage_provider, attempt=attempt)
                    future = executor.submit(
                        _run_provider_stage,
                        provider_impl,
                        stage_id=stage.id,
                        task=_task_with_context_packet(
                            _render_stage_task(bound_task, workflow_name=workflow.name, stage=stage, trace=trace),
                            context_packet_path,
                            context_packet_hash,
                        ),
                        worktree=worktree,
                        skills=stage_skills,
                        session_dir=run_dir / "provider_sessions",
                    )
                    futures[future] = (stage, stage_provider, attempt)
                for future in as_completed(futures):
                    stage, stage_provider, attempt = futures[future]
                    output = future.result()
                    artifact_path = run_dir / output.artifact_name
                    artifact_path.parent.mkdir(parents=True, exist_ok=True)
                    artifact_path.write_text(redact(output.content), encoding="utf-8")
                    blackboard.add_usage(run_id, stage_provider, output.tokens, output.cost_usd)
                    blackboard.add_artifact(run_id, stage.id, output.artifact_name, artifact_path, "stage_output")
                    trace.write(
                        "provider_event",
                        stage=stage.id,
                        provider=stage_provider,
                        returncode=output.returncode,
                        artifact=str(artifact_path),
                    )
                    blackboard.complete_provider_attempt(
                        run_id,
                        stage.id,
                        provider=stage_provider,
                        attempt=attempt,
                        status=_provider_attempt_status(output),
                        failure_kind=_provider_failure_kind(output),
                        returncode=output.returncode,
                        summary=output.summary,
                        artifact_path=str(artifact_path),
                    )
                    action_ids = _record_provider_actions(
                        blackboard,
                        run_id=run_id,
                        stage_id=stage.id,
                        provider=stage_provider,
                        role=stage.role,
                        output=output,
                    )
                    if action_ids:
                        capsule_path = _record_session_capsule(
                            blackboard,
                            run_dir=run_dir,
                            run_id=run_id,
                            stage_id=stage.id,
                            role=stage.role,
                            provider=stage_provider,
                            worktree=worktree,
                            kind=_provider_failure_kind(output) or str(ProviderActionKind.PROVIDER_BLOCKED),
                            summary=output.summary,
                            snapshot_ref=str(snapshots.get(stage.id, {}).get("path") or ""),
                            artifact_path=artifact_path,
                            automation=automation,
                            provider_action_ids=action_ids,
                        )
                        blackboard.complete_provider_attempt(
                            run_id,
                            stage.id,
                            provider=stage_provider,
                            attempt=attempt,
                            status="provider_action",
                            failure_kind=_provider_failure_kind(output),
                            returncode=output.returncode,
                            summary=output.summary,
                            artifact_path=str(artifact_path),
                            capsule_path=str(capsule_path),
                        )
                        blackboard.set_run_status(run_id, RunStatus.AWAITING_PROVIDER_ACTION)
                        blackboard.upsert_stage(
                            run_id,
                            stage.id,
                            role=stage.role,
                            status=StageStatus.RUNNING,
                            output_path=str(artifact_path),
                            summary=f"waiting for provider action: {', '.join(action_ids)}",
                        )
                        trace.write("provider_action_requested", stage=stage.id, action_ids=action_ids, provider=stage_provider)
                        _refresh_provider_learning(blackboard, run_id)
                        return RunResult(run_id, RunStatus.AWAITING_PROVIDER_ACTION, run_dir, None)
                    if output.returncode != 0:
                        capsule_path = _record_session_capsule(
                            blackboard,
                            run_dir=run_dir,
                            run_id=run_id,
                            stage_id=stage.id,
                            role=stage.role,
                            provider=stage_provider,
                            worktree=worktree,
                            kind=_provider_failure_kind(output) or "provider_exit",
                            summary=output.summary,
                            snapshot_ref=str(snapshots.get(stage.id, {}).get("path") or ""),
                            artifact_path=artifact_path,
                            automation=automation,
                        )
                        blackboard.complete_provider_attempt(
                            run_id,
                            stage.id,
                            provider=stage_provider,
                            attempt=attempt,
                            status="failed",
                            failure_kind=_provider_failure_kind(output) or "provider_exit",
                            returncode=output.returncode,
                            summary=output.summary,
                            artifact_path=str(artifact_path),
                            capsule_path=str(capsule_path),
                        )
                        _record_role_result(
                            blackboard,
                            run_dir=run_dir,
                            run_id=run_id,
                            stage_id=stage.id,
                            role=stage.role,
                            provider=stage_provider,
                            decision="reject",
                            summary=output.summary,
                            findings=[{"severity": "high", "type": _provider_failure_kind(output) or "provider_exit", "summary": output.summary}],
                            artifact_path=artifact_path,
                            worktree=worktree,
                            snapshot_ref=None,
                        )
                        blackboard.upsert_stage(
                            run_id,
                            stage.id,
                            role=stage.role,
                            status=StageStatus.FAILED,
                            output_path=str(artifact_path),
                            summary=output.summary,
                        )
                        blackboard.add_error(run_id, stage.id, _provider_failure_kind(output) or "provider_exit", output.summary)
                        blackboard.set_run_status(run_id, RunStatus.BLOCKED)
                        trace.write("stage_failed", stage=stage.id, returncode=output.returncode)
                        _refresh_provider_learning(blackboard, run_id)
                        return RunResult(run_id, RunStatus.BLOCKED, run_dir, None)
                    snapshot = snapshots.get(stage.id, {})
                    if stage.read_only and str(snapshot.get("diff_hash") or snapshot.get("patch_hash")) != _worktree_patch_hash(worktree):
                        summary = f"read-only stage {stage.id} modified the worktree"
                        capsule_path = _record_session_capsule(
                            blackboard,
                            run_dir=run_dir,
                            run_id=run_id,
                            stage_id=stage.id,
                            role=stage.role,
                            provider=stage_provider,
                            worktree=worktree,
                            kind="read_only_write_violation",
                            summary=summary,
                            snapshot_ref=str(snapshot.get("path") or ""),
                            artifact_path=artifact_path,
                            automation=automation,
                        )
                        blackboard.complete_provider_attempt(
                            run_id,
                            stage.id,
                            provider=stage_provider,
                            attempt=attempt,
                            status="read_only_violation",
                            failure_kind="read_only_write_violation",
                            returncode=output.returncode,
                            summary=summary,
                            artifact_path=str(artifact_path),
                            capsule_path=str(capsule_path),
                        )
                        _record_role_result(
                            blackboard,
                            run_dir=run_dir,
                            run_id=run_id,
                            stage_id=stage.id,
                            role=stage.role,
                            provider=stage_provider,
                            decision="reject",
                            summary=summary,
                            findings=[{"severity": "high", "type": "read_only_write_violation", "summary": summary}],
                            artifact_path=artifact_path,
                            worktree=worktree,
                            snapshot_ref=str(snapshot.get("path") or ""),
                        )
                        blackboard.upsert_stage(run_id, stage.id, role=stage.role, status=StageStatus.FAILED, output_path=str(artifact_path), summary=summary)
                        blackboard.add_error(run_id, stage.id, "read_only_write_violation", summary)
                        blackboard.set_run_status(run_id, RunStatus.BLOCKED)
                        trace.write("stage_failed", stage=stage.id, reason="read_only_write_violation")
                        _refresh_provider_learning(blackboard, run_id)
                        return RunResult(run_id, RunStatus.BLOCKED, run_dir, None)
                    _record_role_result(
                        blackboard,
                        run_dir=run_dir,
                        run_id=run_id,
                        stage_id=stage.id,
                        role=stage.role,
                        provider=stage_provider,
                        decision="accept",
                        summary=output.summary,
                        findings=[],
                        artifact_path=artifact_path,
                        worktree=worktree,
                        snapshot_ref=None,
                    )
                    blackboard.upsert_stage(
                        run_id,
                        stage.id,
                        role=stage.role,
                        status=StageStatus.COMPLETED,
                        output_path=str(artifact_path),
                        summary=output.summary,
                    )
                    blackboard.add_checkpoint(run_id, stage.id, "stage_completed")
                    trace.write("stage_completed", stage=stage.id, output=str(artifact_path))
            trace.write("parallel_batch_completed", stages=[stage.id for stage in runnable])

        diff_path = self.write_diff(run_dir, worktree)
        blackboard.add_artifact(run_id, None, "diff.patch", diff_path, "diff")
        semantic = _record_semantic_merge_review(blackboard, run_dir=run_dir, run_id=run_id, task=task, patch_text=diff_path.read_text(encoding="utf-8", errors="replace"))
        if semantic.get("decision") == "reject":
            _refresh_provider_learning(blackboard, run_id)
            blackboard.set_run_status(run_id, RunStatus.BLOCKED)
            blackboard.add_error(run_id, None, "semantic_merge_reject", "semantic merge reviewer rejected the patch")
            _record_evidence_run(blackboard, run_dir=run_dir, run_id=run_id, trace=trace)
            trace.write("run_blocked", reason="semantic merge reviewer rejected the patch", semantic_review_hash=semantic.get("review_hash"))
            return RunResult(run_id, RunStatus.BLOCKED, run_dir, None)
        validator = _record_blind_validator(blackboard, run_dir=run_dir, run_id=run_id, task_hash=task_hash, patch_hash=sha256_file(diff_path))
        if validator.get("decision") == "reject":
            _refresh_provider_learning(blackboard, run_id)
            blackboard.set_run_status(run_id, RunStatus.BLOCKED)
            blackboard.add_error(run_id, None, "blind_validator_reject", "blind validator rejected the patch")
            _record_evidence_run(blackboard, run_dir=run_dir, run_id=run_id, trace=trace)
            trace.write("run_blocked", reason="blind validator rejected the patch", validator_hash=validator.get("validator_hash"))
            return RunResult(run_id, RunStatus.BLOCKED, run_dir, None)
        if self._approval_gate(
            blackboard,
            trace,
            run_id,
            None,
            "merge",
            "approve final merge gate",
            policy,
            subject=_approval_subject(
                blackboard,
                run_id=run_id,
                approval_type="merge",
                stage_id=None,
                policy_hash=policy_hash,
                extra={"patch_hash": sha256_file(diff_path), "validator_hash": validator.get("validator_hash"), "semantic_review_hash": semantic.get("review_hash")},
            ),
        ):
            blackboard.set_run_status(run_id, _approval_wait_status(ci_block_on_approval))
            return RunResult(run_id, _approval_wait_status(ci_block_on_approval), run_dir, None)
        _apply_approved_worktree_changes(
            blackboard,
            trace,
            run_dir=run_dir,
            run_id=run_id,
            workspace=self.workspace,
            worktree=worktree,
        )
        blackboard.set_run_status(run_id, RunStatus.COMPLETED)
        if workflow.name in {"design", "design-lite"}:
            try:
                _record_design_deliverables(
                    blackboard,
                    run_dir=run_dir,
                    run_id=run_id,
                    workspace=self.workspace,
                    task=task,
                    workflow=workflow.name,
                    automation=automation,
                )
            except Exception as exc:
                message = f"required design document was not created: {exc}"
                blackboard.set_run_status(run_id, RunStatus.BLOCKED)
                blackboard.add_error(run_id, None, "missing_design_document", message)
                trace.write("run_blocked", reason="missing_design_document", error=redact(str(exc)))
                return RunResult(run_id, RunStatus.BLOCKED, run_dir, None)
        _record_evidence_run(blackboard, run_dir=run_dir, run_id=run_id, trace=trace)
        report_path = generate_final_report(run_dir, run_id, blackboard)
        _refresh_provider_learning(blackboard, run_id)
        _record_ledger(blackboard, run_dir, run_id, "run_completed", payload={"report": str(report_path), "patch_hash": sha256_file(diff_path)})
        trace.write("run_completed", report=str(report_path))
        return RunResult(run_id, RunStatus.COMPLETED, run_dir, report_path)

    @staticmethod
    def write_diff(run_dir: Path, worktree: Path) -> Path:
        diff = _worktree_diff_text(worktree)
        path = run_dir / "diff.patch"
        path.write_text(redact(diff), encoding="utf-8")
        return path

    @staticmethod
    def _approval_gate(
        blackboard: Blackboard,
        trace: TraceWriter,
        run_id: str,
        stage_id: str | None,
        approval_type: str,
        reason: str,
        policy: SafetyPolicyEngine,
        subject: dict[str, object] | None = None,
    ) -> bool:
        existing = blackboard.find_approval(run_id, stage_id, approval_type)
        subject_hash = canonical_hash(subject or {}) if subject else None
        stale_subject = False
        if existing and existing["status"] == ApprovalStatus.APPROVED:
            if subject_hash and existing.get("subject_hash") and existing.get("subject_hash") != subject_hash:
                stale_subject = True
                trace.write(
                    "approval_subject_stale",
                    stage=stage_id,
                    approval_id=existing["approval_id"],
                    approval_type=approval_type,
                    old_subject_hash=existing.get("subject_hash"),
                    new_subject_hash=subject_hash,
                )
            else:
                trace.write("approval_reused", stage=stage_id, approval_id=existing["approval_id"], approval_type=approval_type, subject_hash=subject_hash)
                return False
        elif existing and existing["status"] == ApprovalStatus.DENIED:
            if not subject_hash or not existing.get("subject_hash") or existing.get("subject_hash") == subject_hash:
                raise PermissionError(f"approval denied: {existing['approval_id']}")
        elif existing and existing["status"] == ApprovalStatus.PENDING:
            if not subject_hash or not existing.get("subject_hash") or existing.get("subject_hash") == subject_hash:
                trace.write("approval_still_pending", stage=stage_id, approval_id=existing["approval_id"], approval_type=approval_type, subject_hash=subject_hash)
                return True
            stale_subject = True
            trace.write(
                "approval_subject_stale",
                stage=stage_id,
                approval_id=existing["approval_id"],
                approval_type=approval_type,
                old_subject_hash=existing.get("subject_hash"),
                new_subject_hash=subject_hash,
            )
        decision = policy.evaluate_approval(approval_type, reason=reason, subject=subject or {})
        trace.write(
            "policy_decision",
            stage=stage_id,
            approval_type=approval_type,
            decision=str(decision.decision),
            reason=decision.reason,
            subject_hash=subject_hash,
            standard=decision.standard.to_dict() if decision.standard else {},
        )
        if not stale_subject and not existing and decision.decision == PolicyDecision.ALLOW:
            trace.write(
                "approval_auto_allowed",
                stage=stage_id,
                approval_type=approval_type,
                subject_hash=subject_hash,
                standard=decision.standard.to_dict() if decision.standard else {},
            )
            return False
        if not stale_subject and not existing and decision.decision == PolicyDecision.DENY:
            raise PermissionError(f"approval denied by policy: {approval_type}")
        approval_id = blackboard.create_approval(run_id, stage_id, approval_type, reason, subject=subject)
        trace.write("approval_requested", stage=stage_id, approval_id=approval_id, approval_type=approval_type, subject_hash=subject_hash)
        if stale_subject or decision.decision == PolicyDecision.APPROVE:
            return True
        blackboard.decide_approval(approval_id, ApprovalStatus.APPROVED)
        trace.write("approval_decided", stage=stage_id, approval_id=approval_id, status="approved", subject_hash=subject_hash)
        return False


def _worktree_diff_text(worktree: Path) -> str:
    result = subprocess.run(
        ["git", "diff", "--", "."],
        cwd=worktree,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **hidden_subprocess_kwargs(),
    )
    diff = result.stdout or ""
    diff += "".join(_untracked_file_diff(worktree, rel) for rel in _iter_untracked_files(worktree))
    return redact(diff)


def _task_requires_clarification(task: str) -> bool:
    normalized = " ".join(task.strip().lower().split())
    if not normalized:
        return True
    return normalized in {"?", "??", "clarify", "needs clarification", "unclear", "tbd"}


def _worktree_patch_hash(worktree: Path) -> str:
    return sha256_text(_worktree_diff_text(worktree))


def _apply_approved_worktree_changes(
    blackboard: Blackboard,
    trace: TraceWriter,
    *,
    run_dir: Path,
    run_id: str,
    workspace: Path,
    worktree: Path,
) -> Path:
    applied: list[str] = []
    for rel_path in _changed_worktree_files(worktree):
        if _is_workspace_apply_ignored(rel_path):
            continue
        source = worktree / Path(*rel_path.split("/"))
        if not source.is_file():
            continue
        destination = workspace / Path(*rel_path.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        applied.append(rel_path)

    manifest = {
        "contract_version": "muxdev.workspace_apply.v1",
        "run_id": run_id,
        "worktree": str(worktree),
        "workspace": str(workspace),
        "files": applied,
    }
    path = run_dir / "workspace_apply.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    blackboard.add_artifact(run_id, None, path.name, path, "workspace_apply")
    _record_ledger(blackboard, run_dir, run_id, "workspace_apply_completed", payload={"files": applied})
    trace.write("workspace_apply_completed", files=applied)
    return path


def _changed_worktree_files(worktree: Path) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMRT", "--", "."],
        cwd=worktree,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **hidden_subprocess_kwargs(),
    )
    paths: set[str] = set()
    if result.returncode == 0:
        paths.update(line.strip().replace("\\", "/") for line in (result.stdout or "").splitlines() if line.strip())
    paths.update(_iter_untracked_files(worktree))
    return sorted(path for path in paths if path and not _is_workspace_apply_ignored(path))


def _is_workspace_apply_ignored(rel_path: str) -> bool:
    normalized = rel_path.replace("\\", "/").lstrip("/")
    parts = [part for part in normalized.split("/") if part]
    if not parts or ".." in parts:
        return True
    ignored_roots = {".git", ".muxdev", ".pytest_cache", "__pycache__"}
    return any(part in ignored_roots or part.startswith("pytest-cache-files-") for part in parts)


def _policy_hash(policy: SafetyPolicyEngine) -> str:
    payload = {
        "approval_types": sorted(policy.policy.approval_types),
        "max_cost_usd": policy.policy.max_cost_usd,
        "shell_allow": sorted(policy.policy.shell_allow),
        "shell_deny": sorted(policy.policy.shell_deny),
        "strict_approval": policy.policy.strict_approval,
        "approval_risk_threshold": policy.policy.approval_risk_threshold,
    }
    return canonical_hash(payload)


def _record_ledger(
    blackboard: Blackboard,
    run_dir: Path,
    run_id: str,
    event_type: str,
    *,
    stage_id: str | None = None,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    event = append_ledger_event(run_dir, run_id=run_id, event_type=event_type, stage_id=stage_id, payload=payload)
    blackboard.add_ledger_event(event)
    return event


def _record_stage_snapshot(
    blackboard: Blackboard,
    *,
    run_dir: Path,
    run_id: str,
    stage_id: str,
    worktree: Path,
) -> dict[str, object]:
    snapshot_dir = run_dir / "snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    patch_path = snapshot_dir / f"{stage_id}.patch"
    diff_text = _worktree_diff_text(worktree)
    patch_path.write_text(diff_text, encoding="utf-8")
    patch_hash = sha256_file(patch_path)
    diff_hash = sha256_text(diff_text)
    meta_path = snapshot_dir / f"{stage_id}.snapshot.json"
    meta = {
        "contract_version": "muxdev.stage_snapshot.v1",
        "run_id": run_id,
        "stage_id": stage_id,
        "patch": str(patch_path),
        "patch_hash": patch_hash,
        "diff_hash": diff_hash,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    blackboard.add_snapshot(run_id, stage_id, path=patch_path, patch_hash=patch_hash)
    blackboard.add_artifact(run_id, stage_id, patch_path.name, patch_path, "stage_snapshot")
    blackboard.add_artifact(run_id, stage_id, meta_path.name, meta_path, "stage_snapshot")
    return {"path": str(patch_path), "meta": str(meta_path), "patch_hash": patch_hash, "diff_hash": diff_hash}


def _record_role_result(
    blackboard: Blackboard,
    *,
    run_dir: Path,
    run_id: str,
    stage_id: str,
    role: str | None,
    provider: str,
    decision: str,
    summary: str,
    findings: list[dict[str, object]],
    artifact_path: Path,
    worktree: Path,
    snapshot_ref: str | None,
) -> dict[str, object]:
    patch_hash = _worktree_patch_hash(worktree)
    evidence_refs = [artifact_descriptor(artifact_path, kind="stage_output")]
    evidence_hash = canonical_hash({"artifacts": evidence_refs, "patch_hash": patch_hash, "snapshot_ref": snapshot_ref})
    contract_path, contract_hash, contract_payload = write_role_result_contract(
        run_dir,
        run_id=run_id,
        stage_id=stage_id,
        role=role,
        provider=provider,
        decision=decision,
        summary=summary,
        findings=findings,
        evidence=evidence_refs,
        evidence_hash=evidence_hash,
        patch_hash=patch_hash,
    )
    blackboard.add_stage_contract(run_id, stage_id, role=role, provider=provider, path=contract_path, contract_hash=contract_hash, decision=decision)
    blackboard.add_artifact(run_id, stage_id, contract_path.name, contract_path, "role_result_contract")
    _record_ledger(
        blackboard,
        run_dir,
        run_id,
        "role_result_written",
        stage_id=stage_id,
        payload={"role_result_hash": contract_hash, "evidence_hash": evidence_hash, "decision": decision},
    )
    return contract_payload


def _record_blind_validator(
    blackboard: Blackboard,
    *,
    run_dir: Path,
    run_id: str,
    task_hash: str,
    patch_hash: str,
) -> dict[str, object]:
    path, digest, payload = write_blind_validator_panel(
        run_dir,
        run_id=run_id,
        task_hash=task_hash,
        patch_hash=patch_hash,
        test_results=_current_test_results(blackboard, run_id),
        review_blockers=_current_review_blockers(blackboard, run_id),
        errors=blackboard.table_rows("error_details", run_id=run_id),
    )
    blackboard.add_validator_panel(run_id, validator_id=str(payload["validator"]), decision=str(payload["decision"]), path=path, validator_hash=digest)
    blackboard.add_artifact(run_id, None, path.name, path, "blind_validator")
    _record_ledger(
        blackboard,
        run_dir,
        run_id,
        "blind_validator_completed",
        payload={"validator_hash": digest, "decision": payload["decision"]},
    )
    return payload


def _record_semantic_merge_review(
    blackboard: Blackboard,
    *,
    run_dir: Path,
    run_id: str,
    task: str,
    patch_text: str,
) -> dict[str, object]:
    path, digest, payload = review_semantic_merge(run_dir, run_id=run_id, patch_text=patch_text, task=task)
    blackboard.add_semantic_merge_review(
        run_id=run_id,
        decision=str(payload.get("decision") or "accept"),
        patch_hash=str(payload.get("patch_hash") or ""),
        findings=[item for item in payload.get("findings", []) if isinstance(item, dict)],
        path=path,
    )
    blackboard.add_artifact(run_id, None, path.name, path, "semantic_merge_review")
    _record_ledger(
        blackboard,
        run_dir,
        run_id,
        "semantic_merge_review_completed",
        payload={"review_hash": payload.get("review_hash"), "decision": payload.get("decision"), "artifact_hash": digest},
    )
    return payload


def _refresh_provider_learning(blackboard: Blackboard, run_id: str) -> None:
    try:
        refresh_provider_learning(blackboard, run_id=run_id)
    except Exception:
        pass


def _current_test_results(blackboard: Blackboard, run_id: str) -> list[dict[str, object]]:
    rows = blackboard.table_rows("test_results", run_id=run_id)
    if _latest_role_decision(blackboard, run_id, "test") == "accept":
        return [row for row in rows if bool(row.get("passed"))]
    return rows


def _current_review_blockers(blackboard: Blackboard, run_id: str) -> list[dict[str, object]]:
    rows = blackboard.table_rows("review_blockers", run_id=run_id)
    return [row for row in rows if _latest_role_decision(blackboard, run_id, str(row.get("stage_id") or "review")) != "accept"]


def _latest_role_decision(blackboard: Blackboard, run_id: str, stage_id: str) -> str | None:
    rows = [
        row
        for row in blackboard.table_rows("stage_contracts", run_id=run_id)
        if row.get("stage_id") == stage_id and row.get("decision")
    ]
    if not rows:
        return None
    return str(max(rows, key=lambda row: str(row.get("created_at") or ""))["decision"])


def _approval_subject(
    blackboard: Blackboard,
    *,
    run_id: str,
    approval_type: str,
    stage_id: str | None,
    policy_hash: str,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "contract_version": "muxdev.approval_subject.v1",
        "run_id": run_id,
        "stage_id": stage_id,
        "approval_type": approval_type,
        "policy_hash": policy_hash,
        "extra": extra or {},
    }


def _record_project_design_doc(
    blackboard: Blackboard,
    *,
    run_id: str,
    stage_id: str,
    workspace: Path,
    worktree: Path,
    content: str,
) -> Path:
    body = redact(content).strip()
    if not body:
        raise ValueError("design stage output was empty")
    user_path = write_user_design_document(
        workspace=workspace,
        run_id=run_id,
        task=_task_for_run(blackboard, run_id),
        workflow="software-dev",
        sections=[("Software Design", body)],
    )

    # Keep a copy in the execution worktree so diff/evidence tooling can still
    # reason about the generated deliverable without asking users to inspect it.
    design_dir = worktree / "docs" / "design"
    design_dir.mkdir(parents=True, exist_ok=True)
    path = design_dir / f"{run_id}-design.md"
    path.write_text(
        user_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    if not path.exists() or path.stat().st_size == 0:
        raise OSError(f"design document missing after write: {path}")
    blackboard.add_artifact(run_id, stage_id, "Design Document", user_path, "project_design_doc")
    blackboard.add_artifact(run_id, stage_id, "Execution Worktree Design Copy", path, "run_design_doc")
    return user_path


def _record_user_design_document_from_stage_outputs(
    blackboard: Blackboard,
    *,
    run_id: str,
    workspace: Path,
    task: str,
    workflow: str,
) -> Path:
    sections: list[tuple[str, str]] = []
    for row in blackboard.table_rows("artifacts", run_id=run_id):
        if row.get("kind") != "stage_output":
            continue
        path = Path(str(row.get("path") or ""))
        if not path.exists() or not path.is_file():
            continue
        title = str(row.get("stage_id") or path.stem).replace("_", " ").title()
        sections.append((title, path.read_text(encoding="utf-8", errors="replace")))
    if not sections:
        raise ValueError("no design stage outputs available")
    path = write_user_design_document(workspace=workspace, run_id=run_id, task=task, workflow=workflow, sections=sections)
    blackboard.add_artifact(run_id, None, "Design Document", path, "project_design_doc")
    return path


def _task_for_run(blackboard: Blackboard, run_id: str) -> str:
    try:
        return str(blackboard.get_run(run_id).get("task") or "")
    except Exception:
        return ""


def _latest_planning_hash(blackboard: Blackboard, run_id: str) -> str | None:
    planning_stages = {"plan", "design", "problem_statement", "requirements", "architecture_options", "system_design"}
    artifacts = [
        row
        for row in blackboard.table_rows("artifacts", run_id=run_id)
        if row.get("stage_id") in planning_stages and row.get("kind") == "stage_output"
    ]
    if not artifacts:
        return None
    path = Path(str(artifacts[-1]["path"]))
    return sha256_file(path) if path.exists() else None


def _untracked_file_diff(worktree: Path, rel_path: str) -> str:
    path = worktree / rel_path
    if not path.is_file():
        return ""
    content = path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()
    header = [
        f"diff --git a/{rel_path} b/{rel_path}",
        "new file mode 100644",
        "index 0000000..0000000",
        "--- /dev/null",
        f"+++ b/{rel_path}",
        f"@@ -0,0 +1,{len(lines)} @@",
    ]
    return "\n".join(header + [f"+{line}" for line in lines]) + "\n"


def _record_design_pack(
    blackboard: Blackboard,
    *,
    run_dir: Path,
    run_id: str,
    task: str,
    workflow: str,
    automation: dict[str, object],
) -> None:
    manifest = write_design_pack(run_dir=run_dir, run_id=run_id, task=task, workflow=workflow, automation=automation)
    blackboard.add_artifact(run_id, None, "design_contract.json", Path(str(manifest["contract"])), "design_contract")
    blackboard.add_artifact(run_id, None, "memory_proposals.json", Path(str(manifest["memory_proposals"])), "memory_proposals")
    for path in manifest.get("files", []):
        blackboard.add_artifact(run_id, None, Path(str(path)).name, Path(str(path)), "design_pack")


def _record_design_deliverables(
    blackboard: Blackboard,
    *,
    run_dir: Path,
    run_id: str,
    workspace: Path,
    task: str,
    workflow: str,
    automation: dict[str, object],
) -> Path:
    _record_design_pack(blackboard, run_dir=run_dir, run_id=run_id, task=task, workflow=workflow, automation=automation)
    return _record_user_design_document_from_stage_outputs(
        blackboard,
        run_id=run_id,
        workspace=workspace,
        task=task,
        workflow=workflow,
    )


def _record_evidence_run(
    blackboard: Blackboard,
    *,
    run_dir: Path,
    run_id: str,
    trace: TraceWriter,
) -> None:
    try:
        payload = write_evidence_run(run_dir, run_id, blackboard)
    except Exception as exc:
        blackboard.add_error(run_id, None, "evidence_v2_failed", str(exc))
        trace.write("evidence_v2_failed", error=redact(str(exc)))
        return
    artifacts = payload.get("artifacts", {}) if isinstance(payload.get("artifacts"), dict) else {}
    evaluation = payload.get("evaluation", {}) if isinstance(payload.get("evaluation"), dict) else {}
    manifest = payload.get("manifest", {}) if isinstance(payload.get("manifest"), dict) else {}
    _record_ledger(
        blackboard,
        run_dir,
        run_id,
        "evidence_v2_written",
        payload={
            "label": evaluation.get("label"),
            "confidence": evaluation.get("confidence"),
            "head_hash": manifest.get("head_hash"),
            "manifest_hash": artifacts.get("manifest_hash"),
            "evaluation_hash": artifacts.get("evaluation_hash"),
        },
    )
    trace.write("evidence_v2_written", label=evaluation.get("label"), confidence=evaluation.get("confidence"), head_hash=manifest.get("head_hash"))


def _iter_untracked_files(worktree: Path) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        cwd=worktree,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **hidden_subprocess_kwargs(),
    )
    if result.returncode == 0:
        return [line for line in (result.stdout or "").splitlines() if line and not _is_runtime_archive_path(line)]
    files: list[str] = []
    for path in worktree.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        rel_path = path.relative_to(worktree).as_posix()
        if _is_runtime_archive_path(rel_path):
            continue
        files.append(rel_path)
    return files


def _is_runtime_archive_path(rel_path: str) -> bool:
    return rel_path.startswith(".muxdev/provider_sessions/")


def _parse_review_result(content: str) -> ReviewResult:
    parsed = extract_json_object(content)
    if not parsed:
        return ReviewResult(has_blockers=False, blockers=[])
    blockers: list[ReviewBlocker] = []
    for item in parsed.get("blockers", []) if isinstance(parsed.get("blockers"), list) else []:
        if isinstance(item, dict):
            blockers.append(ReviewBlocker.model_validate(item))
    return ReviewResult(has_blockers=bool(parsed.get("has_blockers", blockers)), blockers=blockers)


def _has_external_confirmation_prompt(content: str) -> bool:
    provider_text = content.split("\n\n# Stream Events\n", 1)[0]
    lowered = provider_text.lower()
    if "waiting_external_confirmation" in lowered or "approval_prompt_detected" in lowered:
        return True
    events = StreamAdapter().parse_chunk(provider_text)
    prompt_types = {StreamEventType.APPROVAL_PROMPT_DETECTED, StreamEventType.WAITING_EXTERNAL_CONFIRMATION}
    return any(event.type in prompt_types for event in events)


def _stage_provider_for(
    blackboard: Blackboard,
    trace: TraceWriter,
    *,
    stage_id: str,
    role: str | None,
    fallback: str,
    role_providers: dict[str, str],
) -> str:
    planner = ProviderPlanner(
        role_providers=role_providers,
        recommender=lambda selected_role, selected_fallback: recommend_provider(
            blackboard,
            role=selected_role,
            fallback=selected_fallback,
        ),
    )
    route = planner.select(role=role, fallback=fallback)
    payload = route.trace_payload()
    payload.pop("role", None)
    trace.write("provider_route_decision", stage=stage_id, role=role, **payload)
    return route.provider


def _record_session_capsule(
    blackboard: Blackboard,
    *,
    run_dir: Path,
    run_id: str,
    stage_id: str,
    role: str | None,
    provider: str,
    worktree: Path,
    kind: str,
    summary: str,
    snapshot_ref: str | None,
    artifact_path: Path | None,
    automation: dict[str, object],
    provider_action_ids: list[str] | None = None,
) -> Path:
    patch_text = _worktree_diff_text(worktree)
    patch_hash = sha256_text(patch_text)
    evidence_refs = [str(row.get("event_id") or row.get("id")) for row in blackboard.table_rows("evidence_events", run_id=run_id) if row.get("event_id") or row.get("id")]
    open_findings = blackboard.table_rows("error_details", run_id=run_id) + _current_review_blockers(blackboard, run_id)
    path, digest, payload = write_session_capsule(
        run_dir,
        run_id=run_id,
        stage_id=stage_id,
        role=role,
        provider=provider,
        worktree=worktree,
        kind=kind,
        summary=summary,
        patch_text=patch_text,
        patch_hash=patch_hash,
        snapshot_ref=snapshot_ref,
        artifact_path=artifact_path,
        memory_refs=_memory_refs(automation),
        evidence_refs=evidence_refs,
        open_findings=open_findings,
        provider_actions=provider_action_ids or [],
    )
    blackboard.add_session_capsule(
        run_id,
        stage_id,
        role=role,
        provider=provider,
        kind=kind,
        status=str(payload.get("status") or "handoff_ready"),
        summary=summary,
        path=path,
        capsule_hash=digest,
    )
    blackboard.add_artifact(run_id, stage_id, path.name, path, "session_capsule")
    _record_ledger(blackboard, run_dir, run_id, "session_capsule_written", stage_id=stage_id, payload={"capsule_hash": digest, "kind": kind})
    return path


def _record_provider_actions(
    blackboard: Blackboard,
    *,
    run_id: str,
    stage_id: str,
    provider: str,
    role: str | None,
    output: ProviderStageOutput,
) -> list[str]:
    action_ids: list[str] = []
    for action in _provider_actions_from_output(output):
        prompt_text = str(action.get("prompt_text") or output.summary or "provider is waiting for external action")
        kind = str(action.get("kind") or ProviderActionKind.PROVIDER_BLOCKED)
        options = action.get("options") if isinstance(action.get("options"), list) else []
        choices = action.get("choices") if isinstance(action.get("choices"), list) else options
        attach_agent = role or stage_id
        source_hash = sha256_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "stage_id": stage_id,
                    "kind": kind,
                    "prompt_text": prompt_text,
                    "transcript_path": action.get("transcript_path"),
                    "chunks_path": action.get("chunks_path"),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        action_ids.append(
            blackboard.create_provider_action(
                run_id=run_id,
                stage_id=stage_id,
                provider=provider,
                role=role,
                kind=kind,
                prompt_text=prompt_text,
                options=[option for option in options if isinstance(option, dict)],
                input_kind=str(action.get("input_kind") or _provider_action_input_kind(kind, choices)),
                choices=[choice for choice in choices if isinstance(choice, dict)],
                default_choice=str(action.get("default_choice") or "") or None,
                timeout_seconds=int(action["timeout_seconds"]) if str(action.get("timeout_seconds") or "").isdigit() else None,
                auto_policy=str(action.get("auto_policy") or "manual"),
                transcript_path=str(action.get("transcript_path") or "") or None,
                chunks_path=str(action.get("chunks_path") or "") or None,
                attach_command=f"muxdev attach {run_id} --agent {attach_agent}",
                source_event_hash=source_hash,
            )
        )
    return action_ids


def _initial_workflow_context(blackboard: Blackboard, run_id: str, workflow) -> dict[str, object]:
    context: dict[str, object] = {"loop": 0, "review": {"has_blockers": False}}
    blockers_by_stage: dict[str, list[dict[str, object]]] = {}
    for row in blackboard.table_rows("review_blockers", run_id=run_id):
        blockers_by_stage.setdefault(str(row.get("stage_id") or "review"), []).append(row)
    for stage in workflow.stages:
        if not _is_review_stage(stage):
            continue
        decision = _latest_role_decision(blackboard, run_id, stage.id)
        blockers = [] if decision == "accept" else blockers_by_stage.get(stage.id, [])
        review = {"has_blockers": bool(decision == "reject" or blockers), "blockers": blockers}
        context[stage.id] = review
        if stage.id == "review":
            context["review"] = review
    return context


def _is_review_stage(stage) -> bool:
    return stage.output_schema == "ReviewResult" or stage.id in {"review", "design_review", "final_design_review"}


def _review_has_blockers(context: dict[str, object], review_stage: str = "review") -> bool:
    review = context.get(review_stage, {})
    return bool(review.get("has_blockers")) if isinstance(review, dict) else False


def _loop_review_stage(stage) -> str:
    if stage.loop_review_stage:
        return stage.loop_review_stage
    if stage.id == "fix":
        return "review"
    if stage.id.endswith("_revise"):
        return stage.id[: -len("_revise")] + "_review"
    return "review"


def _loop_reset_stages(stage) -> tuple[str, ...]:
    if stage.loop_reset_stages:
        return tuple(stage.loop_reset_stages)
    if stage.id == "fix":
        return ("test", "review", "fix")
    review_stage = _loop_review_stage(stage)
    return (review_stage, stage.id)


def _stage_triggers_review_loop(stage, context: dict[str, object]) -> bool:
    if not (stage.loop_review_stage or stage.id == "fix" or stage.id.endswith("_revise")):
        return False
    return _review_has_blockers(context, _loop_review_stage(stage))


def _loop_stage_exhausted(stage, context: dict[str, object]) -> bool:
    if not (stage.loop_review_stage or stage.id == "fix" or stage.id.endswith("_revise")):
        return False
    return _review_has_blockers(context, _loop_review_stage(stage))


def _loop_state(stage, context: dict[str, object], automation: dict[str, object]) -> dict[str, object]:
    policy = stage.loop_policy
    return {
        "iteration": int(context.get("loop", 0) or 0),
        "max_iterations": policy.max_iterations if policy and policy.max_iterations is not None else _effective_max_loops(stage, automation),
        "evaluator": (policy.evaluator if policy else None) or _loop_review_stage(stage),
        "improver": (policy.improver if policy else None) or stage.id,
        "stop_conditions": list(policy.stop_conditions if policy else []),
        "budget_guard": policy.budget_guard if policy else None,
        "review_has_blockers": _review_has_blockers(context, _loop_review_stage(stage)),
    }


def _effective_max_loops(stage, automation: dict[str, object]) -> int:
    if stage.loop_policy and stage.loop_policy.max_iterations is not None:
        return max(0, int(stage.loop_policy.max_iterations))
    if stage.loop_review_stage or stage.id.endswith("_revise"):
        configured = automation.get("max_review_fixes") if isinstance(automation, dict) else None
        try:
            if configured is not None:
                return max(0, int(configured))
        except (TypeError, ValueError):
            pass
    return int(stage.max_loops or 1)


def _human_gate_subject_extra(
    blackboard: Blackboard,
    *,
    run_dir: Path,
    run_id: str,
    approval_type: str,
) -> dict[str, object]:
    if approval_type != "design":
        return {"plan_hash": _latest_planning_hash(blackboard, run_id)}
    contract_path = run_dir / "design" / "design_contract.json"
    return {
        "design_contract_hash": sha256_file(contract_path) if contract_path.exists() else None,
        "review_result_hash": _latest_stage_output_hash(blackboard, run_id, "design_review"),
        "revision_count": _stage_attempt_count(blackboard, run_id, "design_revise"),
    }


def _latest_stage_output_hash(blackboard: Blackboard, run_id: str, stage_id: str) -> str | None:
    artifacts = [
        row
        for row in blackboard.table_rows("artifacts", run_id=run_id)
        if row.get("stage_id") == stage_id and row.get("kind") == "stage_output"
    ]
    if not artifacts:
        return None
    path = Path(str(artifacts[-1].get("path") or ""))
    return sha256_file(path) if path.exists() else None


def _stage_attempt_count(blackboard: Blackboard, run_id: str, stage_id: str) -> int:
    return sum(1 for row in blackboard.table_rows("provider_attempts", run_id=run_id) if row.get("stage_id") == stage_id and row.get("status") not in {"retried", "provider_action"})


def _provider_action_input_kind(kind: str, choices: list[object] | None = None) -> str:
    if kind == str(ProviderActionKind.CLI_CONFIRMATION):
        return "confirmation"
    if choices:
        return "choice"
    if kind in {str(ProviderActionKind.AUTH_REQUIRED), str(ProviderActionKind.RATE_LIMIT), str(ProviderActionKind.IDLE_TIMEOUT)}:
        return "external"
    return "text"


def _can_use_parallel_runtime(workflow) -> bool:
    for stage in workflow.stages:
        if stage.type != "agent":
            return False
        if stage.when or stage.allow_shell or stage.allow_write:
            return False
    return True


def _configured_workflow_engine(workspace: Path, automation: dict[str, object]) -> str:
    value = automation.get("workflow_engine") if isinstance(automation, dict) else None
    if not value:
        try:
            runtime = load_runtime_config(workspace).get("runtime", {})
            if isinstance(runtime, dict):
                value = runtime.get("workflow_engine")
        except Exception:
            value = None
    normalized = str(value or "langgraph").strip().lower()
    return "native" if normalized == "native" else "langgraph"


def _approval_wait_status(ci_block_on_approval: bool) -> RunStatus:
    return RunStatus.BLOCKED if ci_block_on_approval else RunStatus.AWAITING_APPROVAL


def _skills_for_stage(skills: list[dict[str, object]], *, role: str | None, stage_id: str) -> list[dict[str, object]]:
    selected: list[dict[str, object]] = []
    normalized_role = normalize_role(role or "") if role else None
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        skill_role = skill.get("role")
        normalized_skill_role = normalize_role(str(skill_role)) if skill_role else None
        if skill_role in {None, "", role, stage_id} or normalized_skill_role in {normalized_role, stage_id}:
            selected.append(skill)
    return selected


def _render_stage_task(task: str, *, workflow_name: str, stage, trace: TraceWriter) -> str:
    prompt = render_stage_prompt(task, workflow=workflow_name, stage=stage)
    trace.write("stage_prompt_rendered", stage=stage.id, role=stage.role, role_key=prompt.role_key, sections=list(prompt.sections))
    return prompt.text


def _resolve_workflow_role_skills(
    workspace: Path,
    *,
    task: str,
    workflow,
    provider: str,
) -> list[dict[str, object]]:
    roles = sorted({role for stage in workflow.stages for role in _stage_skill_roles(stage)})
    if not roles:
        return []
    try:
        return resolve_active_skills(workspace, task=task, roles=roles, provider=provider, include_content=True)
    except Exception:
        return []


def _stage_skill_roles(stage) -> set[str]:
    roles: set[str] = set()
    if stage.role:
        roles.add(str(stage.role))
        roles.add(normalize_role(str(stage.role)))
    for skill_spec in getattr(stage, "default_skills", []) or []:
        if isinstance(skill_spec, str) and "=" in skill_spec:
            roles.add(normalize_role(skill_spec.split("=", 1)[0]))
    return {role for role in roles if role}


def _merge_skill_payloads(*groups: list[dict[str, object]]) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for group in groups:
        for skill in group:
            if not isinstance(skill, dict):
                continue
            key = (
                str(skill.get("name") or ""),
                str(skill.get("role")) if skill.get("role") else None,
                str(skill.get("stage")) if skill.get("stage") else None,
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(skill)
    return merged


def _read_task_context(run_dir: Path) -> dict[str, object]:
    path = run_dir / "task_context.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
