"""Workflow supervisor runtime.

The supervisor is the central execution loop for M1-M7: it creates isolated run
state, prepares a worktree, loads a workflow, dispatches stages to provider
adapters, applies safety policy gates, writes the blackboard/trace, and emits a
final report. CLI, TUI, and tests call this layer instead of reimplementing
workflow semantics.
"""

from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from time import time

from ..models import ApprovalStatus, PolicyDecision, ReviewBlocker, ReviewResult, RunStatus, StageStatus, TestResult
from ..core.platforms import hidden_subprocess_kwargs
from ..core.redaction import redact
from ..providers.adapters import ProviderAdapter, extract_json_object, get_runtime_provider
from ..services.dashboard import write_run_dashboard
from ..services.reports import generate_final_report
from ..core.safety import SafetyPolicy, SafetyPolicyEngine
from ..storage import Blackboard, RunStore, TraceWriter
from ..workflows import execution_batches, load_workflow, ordered_stage_ids, should_run_when
from .worktree import WorktreeManager


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: RunStatus
    run_dir: Path
    report_path: Path | None


def new_run_id() -> str:
    """Create a sortable run id based on wall-clock milliseconds."""
    return "run_" + str(int(time() * 1000))


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
    ) -> RunResult:
        """Create a fresh run and execute its workflow until completion or pause."""
        run_id = run_id or new_run_id()
        run_dir = self.store.create_run_dir(run_id)
        worktree = WorktreeManager(self.workspace, self.worktrees_root).prepare(run_id, run_dir)
        blackboard = self._blackboard(run_dir)
        trace = TraceWriter(run_dir, run_id)
        policy = SafetyPolicyEngine(SafetyPolicy(max_cost_usd=max_cost_usd, approval_types=require_approval or set()))
        workflow = load_workflow(workflow_name)
        # Role-specific providers are optional overrides. The default provider
        # remains the fallback for any workflow role that has no explicit choice.
        role_providers = {key: value for key, value in (role_providers or {}).items() if value}
        skills = skills or []
        provider_impls = {provider: get_runtime_provider(provider)}
        for role_provider in role_providers.values():
            provider_impls.setdefault(role_provider, get_runtime_provider(role_provider))

        task_context = {
            "profile": profile,
            "gate": gate,
            "skills": skills,
            "role_providers": role_providers,
            "ci_block_on_approval": ci_block_on_approval,
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
        trace.write(
            "run_started",
            provider=provider,
            worktree=str(worktree.path),
            strategy=worktree.strategy,
            profile=profile,
            gate=gate,
            skills=[skill.get("name") for skill in skills],
        )
        blackboard.set_run_status(run_id, RunStatus.RUNNING)
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
            policy = SafetyPolicyEngine(SafetyPolicy(max_cost_usd=max_cost_usd, approval_types=set()))
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
                workflow_name=str(run["workflow"]),
                provider_impls=provider_impls,
                policy=policy,
                worktree=worktree,
                role_providers=role_providers,
                skills=skills,
                ci_block_on_approval=bool(task_context.get("ci_block_on_approval", False)),
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
        close_blackboard: bool,
    ) -> RunResult:
        """Execute workflow stages in dependency order.

        The method keeps the serial path explicit because it is the safest and
        easiest-to-audit behavior. Only workflows proven safe by
        _can_use_parallel_runtime are delegated to the parallel executor.
        """
        workflow = load_workflow(workflow_name)

        context: dict[str, object] = {"loop": 0, "review": {"has_blockers": False}}
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
                if not should_run_when(stage.when, context):
                    blackboard.upsert_stage(run_id, stage.id, role=stage.role, status=StageStatus.SKIPPED, summary="when condition false")
                    trace.write("stage_skipped", stage=stage.id)
                    index += 1
                    continue
                blackboard.add_checkpoint(run_id, stage.id, "stage_started")
                blackboard.upsert_stage(run_id, stage.id, role=stage.role, status=StageStatus.RUNNING)
                trace.write("stage_started", stage=stage.id, role=stage.role, type=stage.type)

                if stage.type == "human_gate":
                    if self._approval_gate(
                        blackboard,
                        trace,
                        run_id,
                        stage.id,
                        "plan",
                        "approve generated plan",
                        policy,
                    ):
                        blackboard.set_run_status(run_id, _approval_wait_status(ci_block_on_approval))
                        blackboard.upsert_stage(run_id, stage.id, role=stage.role, status=StageStatus.COMPLETED, summary="waiting for approval")
                        return RunResult(run_id, _approval_wait_status(ci_block_on_approval), run_dir, None)
                    blackboard.upsert_stage(run_id, stage.id, role=stage.role, status=StageStatus.COMPLETED, summary="auto-approved")
                    blackboard.add_checkpoint(run_id, stage.id, "stage_completed")
                    trace.write("stage_completed", stage=stage.id)
                    index += 1
                    continue

                if stage.allow_write:
                    if self._approval_gate(
                        blackboard,
                        trace,
                        run_id,
                        stage.id,
                        "write",
                        f"allow write operations for stage {stage.id}",
                        policy,
                    ):
                        blackboard.set_run_status(run_id, _approval_wait_status(ci_block_on_approval))
                        return RunResult(run_id, _approval_wait_status(ci_block_on_approval), run_dir, None)

                if stage.allow_shell:
                    decision = policy.evaluate_shell("pytest")
                    trace.write("policy_decision", stage=stage.id, command="pytest", decision=str(decision.decision), reason=decision.reason)
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
                    ):
                        blackboard.set_run_status(run_id, _approval_wait_status(ci_block_on_approval))
                        return RunResult(run_id, _approval_wait_status(ci_block_on_approval), run_dir, None)

                budget = policy.evaluate_budget(blackboard.usage_total_cost(run_id), 0.01)
                trace.write("budget_check", stage=stage.id, decision=str(budget.decision), reason=budget.reason)
                if budget.decision == PolicyDecision.DENY:
                    blackboard.set_run_status(run_id, RunStatus.PAUSED_BUDGET)
                    return RunResult(run_id, RunStatus.PAUSED_BUDGET, run_dir, None)

                stage_provider = role_providers.get(stage.role or "", provider)
                provider_impl = provider_impls.setdefault(stage_provider, get_runtime_provider(stage_provider))
                stage_skills = _skills_for_stage(skills, role=stage.role, stage_id=stage.id)
                if stage_skills:
                    trace.write("skills_activated", stage=stage.id, role=stage.role, skills=[skill.get("name") for skill in stage_skills])
                output = _run_provider_stage(provider_impl, stage_id=stage.id, task=task, worktree=worktree, skills=stage_skills)
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
                if "waiting_external_confirmation" in output.content or "approval_prompt_detected" in output.content:
                    approval_id = blackboard.create_approval(
                        run_id,
                        stage.id,
                        "external",
                        f"external CLI confirmation required for stage {stage.id}",
                    )
                    blackboard.set_run_status(run_id, _approval_wait_status(ci_block_on_approval))
                    trace.write("external_confirmation_requested", stage=stage.id, approval_id=approval_id)
                    return RunResult(run_id, _approval_wait_status(ci_block_on_approval), run_dir, None)
                if output.returncode != 0:
                    blackboard.upsert_stage(
                        run_id,
                        stage.id,
                        role=stage.role,
                        status=StageStatus.FAILED,
                        output_path=str(artifact_path),
                        summary=output.summary,
                    )
                    blackboard.add_error(run_id, stage.id, "provider_exit", output.summary)
                    blackboard.set_run_status(run_id, RunStatus.BLOCKED)
                    trace.write("stage_failed", stage=stage.id, returncode=output.returncode)
                    return RunResult(run_id, RunStatus.BLOCKED, run_dir, None)
                if stage.id == "test":
                    parsed = extract_json_object(output.content) or {}
                    test_result = TestResult(
                        passed=bool(parsed.get("passed", True)),
                        command=str(parsed.get("command", "pytest")),
                        summary=str(parsed.get("summary", output.summary)),
                    )
                    blackboard.add_test_result(run_id, stage.id, test_result.passed, test_result.command, test_result.summary)
                if stage.id == "review":
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
                    context["review"] = review.model_dump()
                blackboard.upsert_stage(run_id, stage.id, role=stage.role, status=StageStatus.COMPLETED, output_path=str(artifact_path), summary=output.summary)
                blackboard.add_checkpoint(run_id, stage.id, "stage_completed")
                trace.write("stage_completed", stage=stage.id, output=str(artifact_path))
                completed.add(stage.id)
                if stage.id == "fix" and _review_has_blockers(context):
                    context["loop"] = int(context.get("loop", 0)) + 1
                    max_loops = stage.max_loops or 1
                    trace.write("fix_loop_iteration", stage=stage.id, loop=context["loop"], max_loops=max_loops)
                    if int(context["loop"]) >= max_loops:
                        blackboard.add_error(run_id, stage.id, "review_blockers", f"review blockers remain after {max_loops} fix loops")
                        blackboard.set_run_status(run_id, RunStatus.BLOCKED)
                        trace.write("run_blocked", stage=stage.id, reason="review blockers remain")
                        return RunResult(run_id, RunStatus.BLOCKED, run_dir, None)
                    for reset_stage in ("test", "review", "fix"):
                        if reset_stage in by_id:
                            blackboard.reset_stage(run_id, reset_stage)
                            completed.discard(reset_stage)
                    restart = "test" if "test" in by_id else "review"
                    index = ordered.index(restart)
                    continue
                index += 1

            if self._approval_gate(
                blackboard,
                trace,
                run_id,
                None,
                "merge",
                "approve final merge gate",
                policy,
            ):
                blackboard.set_run_status(run_id, _approval_wait_status(ci_block_on_approval))
                return RunResult(run_id, _approval_wait_status(ci_block_on_approval), run_dir, None)
            blackboard.set_run_status(run_id, RunStatus.COMPLETED)
            diff_path = self.write_diff(run_dir, worktree)
            blackboard.add_artifact(run_id, None, "diff.patch", diff_path, "diff")
            report_path = generate_final_report(run_dir, run_id, blackboard)
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
        completed: set[str],
    ) -> RunResult:
        by_id = {stage.id: stage for stage in workflow.stages}
        for batch in execution_batches(workflow):
            runnable = [by_id[stage_id] for stage_id in batch if stage_id not in completed]
            if not runnable:
                continue
            trace.write("parallel_batch_started", stages=[stage.id for stage in runnable], max_parallel=workflow.max_parallel)
            for stage in runnable:
                blackboard.add_checkpoint(run_id, stage.id, "stage_started")
                blackboard.upsert_stage(run_id, stage.id, role=stage.role, status=StageStatus.RUNNING)
                trace.write("stage_started", stage=stage.id, role=stage.role, type=stage.type)
                budget = policy.evaluate_budget(blackboard.usage_total_cost(run_id), 0.01)
                trace.write("budget_check", stage=stage.id, decision=str(budget.decision), reason=budget.reason)
                if budget.decision == PolicyDecision.DENY:
                    blackboard.set_run_status(run_id, RunStatus.PAUSED_BUDGET)
                    return RunResult(run_id, RunStatus.PAUSED_BUDGET, run_dir, None)

            futures = {}
            with ThreadPoolExecutor(max_workers=min(workflow.max_parallel, len(runnable))) as executor:
                for stage in runnable:
                    stage_provider = role_providers.get(stage.role or "", provider)
                    provider_impl = provider_impls.setdefault(stage_provider, get_runtime_provider(stage_provider))
                    stage_skills = _skills_for_stage(skills, role=stage.role, stage_id=stage.id)
                    if stage_skills:
                        trace.write("skills_activated", stage=stage.id, role=stage.role, skills=[skill.get("name") for skill in stage_skills])
                    future = executor.submit(_run_provider_stage, provider_impl, stage_id=stage.id, task=task, worktree=worktree, skills=stage_skills)
                    futures[future] = (stage, stage_provider)
                for future in as_completed(futures):
                    stage, stage_provider = futures[future]
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
                    if output.returncode != 0:
                        blackboard.upsert_stage(
                            run_id,
                            stage.id,
                            role=stage.role,
                            status=StageStatus.FAILED,
                            output_path=str(artifact_path),
                            summary=output.summary,
                        )
                        blackboard.add_error(run_id, stage.id, "provider_exit", output.summary)
                        blackboard.set_run_status(run_id, RunStatus.BLOCKED)
                        trace.write("stage_failed", stage=stage.id, returncode=output.returncode)
                        return RunResult(run_id, RunStatus.BLOCKED, run_dir, None)
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

        if self._approval_gate(blackboard, trace, run_id, None, "merge", "approve final merge gate", policy):
            blackboard.set_run_status(run_id, _approval_wait_status(ci_block_on_approval))
            return RunResult(run_id, _approval_wait_status(ci_block_on_approval), run_dir, None)
        blackboard.set_run_status(run_id, RunStatus.COMPLETED)
        diff_path = self.write_diff(run_dir, worktree)
        blackboard.add_artifact(run_id, None, "diff.patch", diff_path, "diff")
        report_path = generate_final_report(run_dir, run_id, blackboard)
        trace.write("run_completed", report=str(report_path))
        return RunResult(run_id, RunStatus.COMPLETED, run_dir, report_path)

    @staticmethod
    def write_diff(run_dir: Path, worktree: Path) -> Path:
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
    ) -> bool:
        existing = blackboard.find_approval(run_id, stage_id, approval_type)
        if existing and existing["status"] == ApprovalStatus.APPROVED:
            trace.write("approval_reused", stage=stage_id, approval_id=existing["approval_id"], approval_type=approval_type)
            return False
        if existing and existing["status"] == ApprovalStatus.DENIED:
            raise PermissionError(f"approval denied: {existing['approval_id']}")
        if existing and existing["status"] == ApprovalStatus.PENDING:
            trace.write("approval_still_pending", stage=stage_id, approval_id=existing["approval_id"], approval_type=approval_type)
            return True
        approval_id = blackboard.create_approval(run_id, stage_id, approval_type, reason)
        trace.write("approval_requested", stage=stage_id, approval_id=approval_id, approval_type=approval_type)
        if policy.requires_approval(approval_type):
            return True
        blackboard.decide_approval(approval_id, ApprovalStatus.APPROVED)
        trace.write("approval_decided", stage=stage_id, approval_id=approval_id, status="approved")
        return False


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
        return [line for line in (result.stdout or "").splitlines() if line]
    files: list[str] = []
    for path in worktree.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        files.append(path.relative_to(worktree).as_posix())
    return files


def _parse_review_result(content: str) -> ReviewResult:
    parsed = extract_json_object(content)
    if not parsed:
        return ReviewResult(has_blockers=False, blockers=[])
    blockers: list[ReviewBlocker] = []
    for item in parsed.get("blockers", []) if isinstance(parsed.get("blockers"), list) else []:
        if isinstance(item, dict):
            blockers.append(ReviewBlocker.model_validate(item))
    return ReviewResult(has_blockers=bool(parsed.get("has_blockers", blockers)), blockers=blockers)


def _review_has_blockers(context: dict[str, object]) -> bool:
    review = context.get("review", {})
    return bool(review.get("has_blockers")) if isinstance(review, dict) else False


def _can_use_parallel_runtime(workflow) -> bool:
    for stage in workflow.stages:
        if stage.type != "agent":
            return False
        if stage.when or stage.allow_shell or stage.allow_write:
            return False
    return True


def _approval_wait_status(ci_block_on_approval: bool) -> RunStatus:
    return RunStatus.BLOCKED if ci_block_on_approval else RunStatus.AWAITING_APPROVAL


def _skills_for_stage(skills: list[dict[str, object]], *, role: str | None, stage_id: str) -> list[dict[str, object]]:
    selected: list[dict[str, object]] = []
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        skill_role = skill.get("role")
        if skill_role in {None, "", role, stage_id}:
            selected.append(skill)
    return selected


def _read_task_context(run_dir: Path) -> dict[str, object]:
    path = run_dir / "task_context.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _run_provider_stage(
    provider_impl: ProviderAdapter,
    *,
    stage_id: str,
    task: str,
    worktree: Path,
    skills: list[dict[str, object]],
):
    try:
        return provider_impl.run_stage(stage_id=stage_id, task=task, worktree=worktree, skills=skills)
    except TypeError as exc:
        if "skills" not in str(exc):
            raise
        return provider_impl.run_stage(stage_id=stage_id, task=task, worktree=worktree)
