"""Daemon-owned task lifecycle manager."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..application import TaskRuntimeService
from ..clients.sessions import TmuxBackend
from ..config.loader import path_config
from ..core.platforms import follow_file_command, hidden_subprocess_kwargs
from ..domain import RunSpec
from ..models import ApprovalStatus, ProviderActionStatus, RunStatus
from ..runtime import SupervisorRuntime, new_run_id
from ..services.dashboard_run import build_run_dashboard_payload, startup_dashboard_payload
from ..services.feedback import route_feedback
from ..services.progress import enrich_provider_attempts, enrich_stages, progress_summary
from ..services.provider_learning import refresh_provider_learning
from ..services.provider_scores import build_provider_scores
from ..storage import Blackboard
from ..storage.repositories import ProviderActionsRepository, RunsRepository
from ..storage.read_models import DashboardReadModel
from .event_bus import EventBus
from .paths import DaemonPaths, default_daemon_paths
from .queue import TaskQueue


TERMINAL_STATUSES = {str(RunStatus.COMPLETED), str(RunStatus.BLOCKED), str(RunStatus.ABORTED)}


@dataclass
class TaskManager:
    """Own all daemon-side writes to task state and artifacts."""

    paths: DaemonPaths = field(default_factory=default_daemon_paths)
    lock: threading.RLock = field(default_factory=threading.RLock)
    workers: dict[str, threading.Thread] = field(default_factory=dict)
    subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)
    queue: TaskQueue = field(init=False)
    events: EventBus = field(init=False)

    def __post_init__(self) -> None:
        self.queue = TaskQueue(lock=self.lock, workers=self.workers)
        self.events = EventBus(subscribers=self.subscribers)
        self.paths.ensure()
        with self.board() as board:
            board.list_runs()

    def board(self) -> Blackboard:
        return Blackboard(self.paths.data_dir, db_path=self.paths.db_path)

    def submit_task(
        self,
        *,
        task: str,
        workspace: Path,
        provider: str = "mock",
        workflow: str = "software-dev",
        profile: str | None = None,
        gate: str | None = None,
        require_approval: set[str] | None = None,
        max_cost_usd: float = 0.5,
        role_providers: dict[str, str] | None = None,
        skills: list[dict[str, object]] | None = None,
        ci_block_on_approval: bool = False,
        depth: str | None = None,
        topology: str | None = None,
        automation: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        spec = RunSpec.from_submit_payload(
            task=task,
            workspace=workspace,
            provider=provider,
            workflow=workflow,
            run_id=new_run_id(),
            profile=profile,
            gate=gate,
            require_approval=require_approval,
            max_cost_usd=max_cost_usd,
            role_providers=role_providers,
            skills=skills,
            ci_block_on_approval=ci_block_on_approval,
            depth=depth,
            topology=topology,
            automation=automation,
        )
        task_id = spec.run_id
        run_dir = self._project_run_dir(spec.workspace, task_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "task_context.json").write_text(
            json.dumps(spec.task_context(), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        with self.board() as board:
            RunsRepository(board).create(spec, worktree=self._project_worktree_dir(spec.workspace, task_id))
        thread = threading.Thread(
            target=self._run_task,
            args=(spec,),
            name=f"muxdev-task-{task_id}",
            daemon=True,
        )
        self.queue.start(task_id, thread)
        self.broadcast({"type": "task_submitted", "task_id": task_id})
        return {
            "task_id": task_id,
            "run_id": task_id,
            "status": str(RunStatus.CREATED),
            "dashboard_url": f"/tasks/{task_id}",
            "profile": spec.profile,
            "gate": spec.gate,
            "depth": spec.depth,
            "topology": spec.topology,
            "skills": [skill.name for skill in spec.skills],
        }

    def continue_task(self, task_id: str | None = None, *, max_cost_usd: float = 0.5) -> dict[str, Any]:
        resolved = self.resolve_task_id(task_id or "latest")
        run = self.get_run(resolved)
        workspace = Path(run["workspace"])
        with self.board() as board:
            pending_actions = ProviderActionsRepository(board).list_pending(resolved)
            if pending_actions:
                board.set_run_status(resolved, RunStatus.AWAITING_PROVIDER_ACTION)
                return {
                    "task_id": resolved,
                    "run_id": resolved,
                    "status": str(RunStatus.AWAITING_PROVIDER_ACTION),
                    "provider_actions": pending_actions,
                }
        thread = threading.Thread(
            target=self._resume_task,
            args=(resolved, workspace),
            kwargs={"max_cost_usd": max_cost_usd},
            name=f"muxdev-continue-{resolved}",
            daemon=True,
        )
        if not self.queue.start_if_idle(resolved, thread):
            return {"task_id": resolved, "run_id": resolved, "status": "already_running"}
        self.broadcast({"type": "task_continue_requested", "task_id": resolved})
        return {"task_id": resolved, "run_id": resolved, "status": "continue_requested"}

    def stop_task(self, task_id: str) -> dict[str, Any]:
        resolved = self.resolve_task_id(task_id)
        with self.board() as board:
            board.set_run_status(resolved, RunStatus.ABORTED)
        self.broadcast({"type": "task_stopped", "task_id": resolved})
        return {"task_id": resolved, "run_id": resolved, "status": str(RunStatus.ABORTED)}

    def list_tasks(self) -> list[dict[str, Any]]:
        with self.board() as board:
            rows_by_table = {
                "stages": _rows_by_run(board.table_rows("stages")),
                "approvals": _rows_by_run(board.table_rows("approvals")),
                "provider_actions": _rows_by_run(board.table_rows("provider_actions")),
                "provider_attempts": _rows_by_run(board.table_rows("provider_attempts")),
                "usage_records": _rows_by_run(board.table_rows("usage_records")),
                "error_details": _rows_by_run(board.table_rows("error_details")),
            }
            return [self._task_summary(board, row, rows_by_table=rows_by_table) for row in board.list_runs()]

    def task_detail(self, task_id: str) -> dict[str, Any]:
        resolved = self.resolve_task_id(task_id)
        with self.board() as board:
            run = board.get_run(resolved)
            workspace = Path(run["workspace"])
            run_dir = self._run_dir(resolved, run=run)
            return DashboardReadModel(
                workspace,
                run_dir,
                resolved,
                board,
                build_run_dashboard_payload,
                context=self._task_context(resolved),
            ).load()

    def approvals(self, *, status: str | None = None) -> list[dict[str, Any]]:
        with self.board() as board:
            return board.list_approvals(status=status)

    def provider_actions(self, *, status: str | None = None, task_id: str | None = None) -> list[dict[str, Any]]:
        run_id = self.resolve_task_id(task_id) if task_id else None
        with self.board() as board:
            return ProviderActionsRepository(board).list(status=status, run_id=run_id)

    def provider_scores(self, *, role: str | None = None) -> list[dict[str, Any]]:
        with self.board() as board:
            return build_provider_scores(board, role=role)

    def provider_learning(self, *, role: str | None = None) -> list[dict[str, Any]]:
        with self.board() as board:
            refresh_provider_learning(board, role=role)
            return board.list_provider_learning(role=role)

    def parallel_conflicts(self, *, status: str | None = None, task_id: str | None = None) -> list[dict[str, Any]]:
        run_id = self.resolve_task_id(task_id) if task_id else None
        with self.board() as board:
            return board.list_parallel_conflicts(status=status, run_id=run_id)

    def semantic_merge_reviews(self, *, task_id: str | None = None) -> list[dict[str, Any]]:
        run_id = self.resolve_task_id(task_id) if task_id else None
        with self.board() as board:
            return board.list_semantic_merge_reviews(run_id=run_id)

    def multi_repo_orchestrations(self, *, status: str | None = None) -> list[dict[str, Any]]:
        with self.board() as board:
            return board.list_multi_repo_orchestrations(status=status)

    def ingest_feedback(
        self,
        *,
        kind: str,
        source: str,
        content: str,
        workspace: Path,
        run_id: str | None = None,
        severity: str = "medium",
        provider: str = "mock",
        payload: dict[str, Any] | None = None,
        auto_submit: bool = True,
    ) -> dict[str, Any]:
        with self.board() as board:
            routed = route_feedback(
                workspace,
                board,
                kind=kind,
                source=source,
                content=content,
                run_id=run_id,
                severity=severity,
                payload=payload or {},
            )
        result = routed.to_dict()
        if routed.auto and auto_submit:
            submitted = self.submit_task(
                task=routed.task,
                workspace=workspace,
                provider=provider,
                workflow=routed.workflow,
                profile="ci" if kind in {"ci_failed", "local_test_failure"} else None,
                gate="ci" if kind in {"ci_failed", "local_test_failure"} else None,
                require_approval=set(),
                max_cost_usd=0.5,
                role_providers={routed.route_to: provider},
                skills=[],
                ci_block_on_approval=kind in {"ci_failed", "local_test_failure"},
                depth="ci" if kind in {"ci_failed", "local_test_failure"} else None,
                topology="ci" if kind in {"ci_failed", "local_test_failure"} else None,
                automation={"intent": "ci" if kind in {"ci_failed", "local_test_failure"} else "feedback", "feedback_id": routed.feedback_id, "route_to": routed.route_to},
            )
            result["submitted"] = submitted
            if routed.rescue_id:
                with self.board() as board:
                    board.update_ci_rescue(routed.rescue_id, rescue_run_id=str(submitted["run_id"]), status="submitted")
        self.broadcast({"type": "feedback_routed", "feedback_id": routed.feedback_id, "route_to": routed.route_to, "auto": routed.auto})
        return result

    def ecosystem_state(self) -> dict[str, Any]:
        with self.board() as board:
            return {
                "feedback_events": board.table_rows("feedback_events"),
                "ci_rescues": board.table_rows("ci_rescues"),
                "cache_entries": board.table_rows("cache_entries"),
                "skill_locks": board.table_rows("skill_locks"),
                "guardrail_events": board.table_rows("guardrail_events"),
                "parallel_conflicts": board.list_parallel_conflicts(),
                "semantic_merge_reviews": board.list_semantic_merge_reviews(),
                "provider_learning": board.list_provider_learning(),
                "multi_repo_orchestrations": board.list_multi_repo_orchestrations(),
            }

    def decide_approval(self, approval_id: str, status: ApprovalStatus) -> dict[str, Any]:
        with self.board() as board:
            match: dict[str, Any] | None = None
            for row in board.table_rows("approvals"):
                if row["approval_id"] == approval_id:
                    match = row
                    break
            if match is None:
                raise KeyError(f"approval not found: {approval_id}")
            board.decide_approval(approval_id, status)
            match["status"] = str(status)
            match["decided"] = True
        self.broadcast({"type": "approval_decided", "approval_id": approval_id, "status": str(status)})
        return match

    def update_provider_action(self, action_id: str, status: ProviderActionStatus) -> dict[str, Any]:
        with self.board() as board:
            match = ProviderActionsRepository(board).mark(action_id, status)
        self.broadcast({"type": "provider_action_updated", "action_id": action_id, "status": str(status)})
        return match

    def respond_provider_action(
        self,
        action_id: str,
        response: Any,
        *,
        status: ProviderActionStatus = ProviderActionStatus.HANDLED,
    ) -> dict[str, Any]:
        with self.board() as board:
            match = ProviderActionsRepository(board).respond(action_id, response, status=status)
        self.broadcast({"type": "provider_action_updated", "action_id": action_id, "status": str(status), "response": response})
        return match

    def diff(self, task_id: str) -> dict[str, Any]:
        resolved = self.resolve_task_id(task_id)
        path = self._run_dir(resolved) / "diff.patch"
        return {"task_id": resolved, "run_id": resolved, "path": str(path), "diff": path.read_text(encoding="utf-8") if path.exists() else ""}

    def report(self, task_id: str) -> dict[str, Any]:
        resolved = self.resolve_task_id(task_id)
        path = self._run_dir(resolved) / "final_report.md"
        return {"task_id": resolved, "run_id": resolved, "path": str(path), "content": path.read_text(encoding="utf-8") if path.exists() else ""}

    def rollback(self, task_id: str, *, to_stage: str | None = None) -> dict[str, Any]:
        resolved = self.resolve_task_id(task_id)
        run = self.get_run(resolved)
        worktree = Path(run["worktree"])
        if not worktree.exists():
            return {"task_id": resolved, "run_id": resolved, "status": "failed", "error": f"worktree not found: {worktree}"}
        if to_stage:
            return self._rollback_to_stage_snapshot(resolved, worktree, to_stage)
        checkout = subprocess.run(
            ["git", "checkout", "--", "."],
            cwd=worktree,
            capture_output=True,
            text=True,
            check=False,
            **hidden_subprocess_kwargs(),
        )
        clean = subprocess.run(
            ["git", "clean", "-fd"],
            cwd=worktree,
            capture_output=True,
            text=True,
            check=False,
            **hidden_subprocess_kwargs(),
        )
        fallback = ""
        if checkout.returncode != 0 or clean.returncode != 0:
            fallback = "fallback cleaned run worktree: " + ", ".join(_clean_worktree_without_git(worktree))
        return {
            "task_id": resolved,
            "run_id": resolved,
            "worktree": str(worktree),
            "status": "rolled_back" if fallback or (checkout.returncode == 0 and clean.returncode == 0) else "failed",
            "stdout": (checkout.stdout or "") + (clean.stdout or ""),
            "stderr": (checkout.stderr or "") + (clean.stderr or ""),
            "fallback": fallback,
        }

    def _rollback_to_stage_snapshot(self, task_id: str, worktree: Path, stage_id: str) -> dict[str, Any]:
        with self.board() as board:
            rows = [row for row in board.table_rows("snapshots", run_id=task_id) if row.get("stage_id") == stage_id]
        if not rows:
            return {"task_id": task_id, "run_id": task_id, "status": "failed", "error": f"snapshot not found for stage: {stage_id}"}
        patch_path = Path(str(rows[-1]["path"]))
        if not patch_path.exists():
            return {"task_id": task_id, "run_id": task_id, "status": "failed", "error": f"snapshot patch missing: {patch_path}"}
        checkout = subprocess.run(["git", "checkout", "--", "."], cwd=worktree, capture_output=True, text=True, check=False, **hidden_subprocess_kwargs())
        clean = subprocess.run(["git", "clean", "-fd"], cwd=worktree, capture_output=True, text=True, check=False, **hidden_subprocess_kwargs())
        fallback = ""
        git_failed = checkout.returncode != 0 or clean.returncode != 0
        if git_failed:
            fallback = "fallback cleaned run worktree: " + ", ".join(_clean_worktree_without_git(worktree))
        apply_result = None
        patch_text = patch_path.read_text(encoding="utf-8", errors="replace")
        if patch_text.strip():
            if git_failed and fallback:
                _remove_patch_targets(worktree, patch_text)
            apply_result = subprocess.run(
                ["git", "apply", str(patch_path)],
                cwd=worktree,
                capture_output=True,
                text=True,
                check=False,
                **hidden_subprocess_kwargs(),
            )
        apply_failed = apply_result is not None and apply_result.returncode != 0
        if apply_failed and fallback and "already exists in working directory" in (apply_result.stderr or ""):
            apply_failed = False
        failed = apply_failed or (git_failed and not fallback)
        return {
            "task_id": task_id,
            "run_id": task_id,
            "worktree": str(worktree),
            "to_stage": stage_id,
            "snapshot": str(patch_path),
            "status": "failed" if failed else "rolled_back",
            "stdout": (checkout.stdout or "") + (clean.stdout or "") + ((apply_result.stdout or "") if apply_result else ""),
            "stderr": (checkout.stderr or "") + (clean.stderr or "") + ((apply_result.stderr or "") if apply_result else ""),
            "fallback": fallback,
        }

    def attach_command(self, task_id: str, *, agent: str = "implementer") -> dict[str, Any]:
        resolved = self.resolve_task_id(task_id)
        tmux = TmuxBackend()
        session_name = f"muxdev-{resolved}-{agent}".replace(":", "-").replace("_", "-")
        if tmux.available:
            handoff = {"mode": "tmux", "command": tmux.attach_command(session_name), "session": session_name}
        else:
            run_dir = self._run_dir(resolved)
            candidates = sorted((run_dir / "session").glob(f"*{agent}*.log")) if (run_dir / "session").exists() else []
            transcript = candidates[-1] if candidates else run_dir / "trace.jsonl"
            handoff = {"mode": "transcript", "command": follow_file_command(transcript), "path": str(transcript)}
        with self.board() as board:
            run = board.get_run(resolved)
            board.upsert_agent(resolved, agent, str(run["provider"]), session_id=f"{resolved}:{agent}", status="attached")
        return {"task_id": resolved, "run_id": resolved, "agent": agent, "session_id": f"{resolved}:{agent}", "status": "attached", "handoff": handoff}

    def daemon_status(self) -> dict[str, Any]:
        tasks = self.list_tasks()
        return {
            "status": "running",
            "tasks": len(tasks),
            "running_tasks": sum(1 for task in tasks if task.get("status") == str(RunStatus.RUNNING)),
            "queue_length": sum(1 for task in tasks if task.get("status") == str(RunStatus.CREATED)),
            "data": str(self.paths.data_dir),
            "database": str(self.paths.db_path),
        }

    def startup_payload(self) -> dict[str, Any]:
        return startup_dashboard_payload(Path.cwd())

    def resolve_task_id(self, task_id: str) -> str:
        if task_id != "latest":
            return task_id
        tasks = self.list_tasks()
        if not tasks:
            raise KeyError("no muxdev tasks found")
        unfinished = [task for task in tasks if task.get("status") not in TERMINAL_STATUSES]
        return str((unfinished or tasks)[0]["task_id"])

    def get_run(self, task_id: str) -> dict[str, Any]:
        with self.board() as board:
            return board.get_run(self.resolve_task_id(task_id))

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        return await self.events.subscribe()

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self.events.unsubscribe(queue)

    def broadcast(self, event: dict[str, Any]) -> None:
        self.events.publish(event)

    def _run_task(self, spec: RunSpec) -> None:
        self._runtime_service().run(spec)

    def _resume_task(self, task_id: str, workspace: Path, *, max_cost_usd: float) -> None:
        self._runtime_service().resume(task_id, workspace, max_cost_usd=max_cost_usd)

    def _runtime_service(self) -> TaskRuntimeService:
        return TaskRuntimeService(runtime_factory=self._runtime_for_task, board_factory=self.board, publish=self.broadcast)

    def _runtime_for_task(self, workspace: Path, run_id: str | None = None) -> SupervisorRuntime:
        if run_id:
            run_dir = self._run_dir(run_id)
            if self.paths.runs_dir == run_dir.parent:
                return self._runtime(workspace, runs_dir=self.paths.runs_dir, worktrees_root=self.paths.worktrees_dir)
        return self._runtime(workspace)

    def _runtime(self, workspace: Path, *, runs_dir: Path | None = None, worktrees_root: Path | None = None) -> SupervisorRuntime:
        return SupervisorRuntime(
            workspace,
            runs_dir=runs_dir,
            state_db=self.paths.db_path,
            worktrees_root=worktrees_root,
            write_dashboards=False,
        )

    def _task_summary(
        self,
        board: Blackboard,
        run: dict[str, Any],
        *,
        rows_by_table: dict[str, dict[str, list[dict[str, Any]]]] | None = None,
    ) -> dict[str, Any]:
        run_id = str(run["run_id"])
        context = self._task_context(run_id, run=run)
        if rows_by_table is None:
            stages = board.table_rows("stages", run_id=run_id)
            approvals = board.table_rows("approvals", run_id=run_id)
            provider_actions = board.table_rows("provider_actions", run_id=run_id)
            provider_attempts = board.table_rows("provider_attempts", run_id=run_id)
            usage = board.table_rows("usage_records", run_id=run_id)
            errors = board.table_rows("error_details", run_id=run_id)
        else:
            stages = rows_by_table["stages"].get(run_id, [])
            approvals = rows_by_table["approvals"].get(run_id, [])
            provider_actions = rows_by_table["provider_actions"].get(run_id, [])
            provider_attempts = rows_by_table["provider_attempts"].get(run_id, [])
            usage = rows_by_table["usage_records"].get(run_id, [])
            errors = rows_by_table["error_details"].get(run_id, [])
        enriched_stages = enrich_stages(stages)
        enriched_attempts = enrich_provider_attempts(provider_attempts)
        progress = progress_summary(
            run=run,
            stages=enriched_stages,
            provider_attempts=enriched_attempts,
            approvals=approvals,
            provider_actions=provider_actions,
        )
        latest_error = _latest_error(errors)
        return {
            **run,
            "task_id": run_id,
            "run_id": run_id,
            "current_stage": progress.get("current_stage") or "",
            "current_activity": progress.get("current_activity") or "",
            "elapsed_seconds": progress.get("elapsed_seconds"),
            "current_stage_elapsed_seconds": progress.get("current_stage_elapsed_seconds"),
            "latest_provider_attempt": progress.get("latest_provider_attempt"),
            "stage_timeline": progress.get("stage_timeline", []),
            "pending_approvals": sum(1 for row in approvals if row.get("status") == str(ApprovalStatus.PENDING)),
            "pending_provider_actions": sum(1 for row in provider_actions if row.get("status") == str(ProviderActionStatus.PENDING)),
            "tokens": sum(int(row.get("tokens") or 0) for row in usage),
            "cost_usd": round(sum(float(row.get("cost_usd") or 0) for row in usage), 6),
            "errors": len(errors),
            "error_summary": latest_error,
            "profile": context.get("profile"),
            "gate": context.get("gate"),
            "depth": context.get("depth"),
            "topology": context.get("topology"),
            "role_providers": context.get("role_providers", {}) if isinstance(context.get("role_providers"), dict) else {},
            "skills": [skill.get("name") for skill in context.get("skills", []) if isinstance(skill, dict)],
            "recover_endpoint": f"/api/tasks/{run_id}/continue",
            "rollback_endpoint": f"/api/tasks/{run_id}/rollback",
            "report_endpoint": f"/api/tasks/{run_id}/report",
        }

    def _task_context(self, task_id: str, *, run: dict[str, Any] | None = None) -> dict[str, Any]:
        path = self._run_dir(task_id, run=run) / "task_context.json"
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _run_dir(self, task_id: str, *, run: dict[str, Any] | None = None) -> Path:
        if run is None:
            with self.board() as board:
                run = board.get_run(task_id)
        project_run = self._project_run_dir(Path(str(run["workspace"])), task_id)
        if project_run.exists():
            return project_run
        legacy_run = self.paths.runs_dir / task_id
        if legacy_run.exists():
            return legacy_run
        return project_run

    @staticmethod
    def _project_run_dir(workspace: Path, task_id: str) -> Path:
        return path_config(workspace, "runs") / task_id

    @staticmethod
    def _project_worktree_dir(workspace: Path, task_id: str) -> Path:
        return path_config(workspace, "worktrees") / task_id


def _rows_by_run(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("run_id") or "")].append(row)
    return grouped


def _latest_error(errors: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not errors:
        return None
    row = max(errors, key=lambda item: str(item.get("created_at") or ""))
    return {
        "stage_id": row.get("stage_id"),
        "type": row.get("type"),
        "message": row.get("message"),
        "created_at": row.get("created_at"),
    }


def _clean_worktree_without_git(worktree: Path) -> list[str]:
    resolved = worktree.resolve()
    cleaned: list[str] = []
    for child in worktree.iterdir():
        if child.name == ".git":
            continue
        child_resolved = child.resolve()
        if resolved not in child_resolved.parents and child_resolved != resolved:
            raise RuntimeError(f"refusing to clean path outside worktree: {child}")
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
            cleaned.append(str(child.relative_to(worktree)))
        else:
            try:
                child.unlink(missing_ok=True)
                cleaned.append(str(child.relative_to(worktree)))
            except PermissionError:
                child.write_text("", encoding="utf-8")
                cleaned.append(f"{child.relative_to(worktree)} (truncated)")
    return cleaned


def _remove_patch_targets(worktree: Path, patch_text: str) -> None:
    root = worktree.resolve()
    for rel_path in _patch_target_paths(patch_text):
        target = (worktree / rel_path).resolve()
        if root not in target.parents and target != root:
            raise RuntimeError(f"refusing to remove patch target outside worktree: {target}")
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            try:
                target.unlink()
            except OSError:
                target.write_text("", encoding="utf-8")


def _patch_target_paths(patch_text: str) -> list[str]:
    paths: list[str] = []
    for line in patch_text.splitlines():
        if not line.startswith("+++ b/"):
            continue
        rel = line.removeprefix("+++ b/").strip()
        if rel and rel != "/dev/null" and rel not in paths:
            paths.append(rel)
    return paths
