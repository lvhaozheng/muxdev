"""Daemon-owned task lifecycle manager."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..clients.sessions import TmuxBackend
from ..core.platforms import follow_file_command, hidden_subprocess_kwargs
from ..models import ApprovalStatus, ProviderActionStatus, RunStatus
from ..runtime import SupervisorRuntime, new_run_id
from ..services.dashboard import build_run_dashboard_payload, startup_dashboard_payload
from ..services.feedback import route_feedback
from ..services.provider_learning import refresh_provider_learning
from ..services.provider_scores import build_provider_scores
from ..storage import Blackboard, compact_trace, read_trace
from .paths import DaemonPaths, default_daemon_paths


TERMINAL_STATUSES = {str(RunStatus.COMPLETED), str(RunStatus.BLOCKED), str(RunStatus.ABORTED)}


@dataclass
class TaskManager:
    """Own all daemon-side writes to task state and artifacts."""

    paths: DaemonPaths = field(default_factory=default_daemon_paths)
    lock: threading.RLock = field(default_factory=threading.RLock)
    workers: dict[str, threading.Thread] = field(default_factory=dict)
    subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)

    def __post_init__(self) -> None:
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
        task_id = new_run_id()
        run_dir = self.paths.runs_dir / task_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "task_context.json").write_text(
            json.dumps(
                {
                    "profile": profile,
                    "gate": gate,
                    "skills": skills or [],
                    "role_providers": role_providers or {},
                    "ci_block_on_approval": ci_block_on_approval,
                    "depth": depth,
                    "topology": topology,
                    "automation": automation or {},
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        with self.board() as board:
            board.create_run(
                run_id=task_id,
                task=task,
                workflow=workflow,
                provider=provider,
                workspace=workspace,
                worktree=run_dir / "worktree",
            )
        thread = threading.Thread(
            target=self._run_task,
            args=(task_id, workspace, task),
            kwargs={
                "provider": provider,
                "workflow": workflow,
                "profile": profile,
                "gate": gate,
                "require_approval": require_approval or set(),
                "max_cost_usd": max_cost_usd,
                "role_providers": role_providers or {},
                "skills": skills or [],
                "ci_block_on_approval": ci_block_on_approval,
                "depth": depth,
                "topology": topology,
                "automation": automation or {},
            },
            name=f"muxdev-task-{task_id}",
            daemon=True,
        )
        with self.lock:
            self.workers[task_id] = thread
        thread.start()
        self.broadcast({"type": "task_submitted", "task_id": task_id})
        return {
            "task_id": task_id,
            "run_id": task_id,
            "status": str(RunStatus.CREATED),
            "dashboard_url": f"/tasks/{task_id}",
            "profile": profile,
            "gate": gate,
            "depth": depth,
            "topology": topology,
            "skills": [skill.get("name") for skill in skills or [] if isinstance(skill, dict)],
        }

    def continue_task(self, task_id: str | None = None, *, max_cost_usd: float = 0.5) -> dict[str, Any]:
        resolved = self.resolve_task_id(task_id or "latest")
        run = self.get_run(resolved)
        workspace = Path(run["workspace"])
        with self.board() as board:
            pending_actions = board.list_provider_actions(status=str(ProviderActionStatus.PENDING), run_id=resolved)
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
        with self.lock:
            existing = self.workers.get(resolved)
            if existing and existing.is_alive():
                return {"task_id": resolved, "run_id": resolved, "status": "already_running"}
            self.workers[resolved] = thread
        thread.start()
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
            return [self._task_summary(board, row) for row in board.list_runs()]

    def task_detail(self, task_id: str) -> dict[str, Any]:
        resolved = self.resolve_task_id(task_id)
        run_dir = self.paths.runs_dir / resolved
        with self.board() as board:
            payload = build_run_dashboard_payload(Path(board.get_run(resolved)["workspace"]), run_dir, resolved, board)
        payload["task_id"] = resolved
        payload["run_id"] = resolved
        payload["trace"] = compact_trace(read_trace(run_dir))[-50:] if run_dir.exists() else []
        payload["context"] = self._task_context(resolved)
        return payload

    def approvals(self, *, status: str | None = None) -> list[dict[str, Any]]:
        with self.board() as board:
            return board.list_approvals(status=status)

    def provider_actions(self, *, status: str | None = None, task_id: str | None = None) -> list[dict[str, Any]]:
        run_id = self.resolve_task_id(task_id) if task_id else None
        with self.board() as board:
            return board.list_provider_actions(status=status, run_id=run_id)

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
                "plugin_manifests": board.table_rows("plugin_manifests"),
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
            match: dict[str, Any] | None = None
            for row in board.table_rows("provider_actions"):
                if row["action_id"] == action_id:
                    match = row
                    break
            if match is None:
                raise KeyError(f"provider action not found: {action_id}")
            board.update_provider_action_status(action_id, status)
            match["status"] = str(status)
            match["updated"] = True
        self.broadcast({"type": "provider_action_updated", "action_id": action_id, "status": str(status)})
        return match

    def diff(self, task_id: str) -> dict[str, Any]:
        resolved = self.resolve_task_id(task_id)
        path = self.paths.runs_dir / resolved / "diff.patch"
        return {"task_id": resolved, "run_id": resolved, "path": str(path), "diff": path.read_text(encoding="utf-8") if path.exists() else ""}

    def report(self, task_id: str) -> dict[str, Any]:
        resolved = self.resolve_task_id(task_id)
        path = self.paths.runs_dir / resolved / "final_report.md"
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
            apply_result = subprocess.run(
                ["git", "apply", str(patch_path)],
                cwd=worktree,
                capture_output=True,
                text=True,
                check=False,
                **hidden_subprocess_kwargs(),
            )
        apply_failed = apply_result is not None and apply_result.returncode != 0
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
            run_dir = self.paths.runs_dir / resolved
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
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        queue.put_nowait({"type": "hello", "message": "muxdev events connected"})
        self.subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self.subscribers.discard(queue)

    def broadcast(self, event: dict[str, Any]) -> None:
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(event)
            except Exception:
                self.subscribers.discard(queue)

    def _run_task(
        self,
        task_id: str,
        workspace: Path,
        task: str,
        *,
        provider: str,
        workflow: str,
        profile: str | None,
        gate: str | None,
        require_approval: set[str],
        max_cost_usd: float,
        role_providers: dict[str, str],
        skills: list[dict[str, object]],
        ci_block_on_approval: bool,
        depth: str | None,
        topology: str | None,
        automation: dict[str, object],
    ) -> None:
        try:
            runtime = self._runtime(workspace)
            result = runtime.run(
                task,
                provider=provider,
                workflow_name=workflow,
                require_approval=require_approval,
                max_cost_usd=max_cost_usd,
                role_providers=role_providers,
                run_id=task_id,
                profile=profile,
                gate=gate,
                skills=skills,
                ci_block_on_approval=ci_block_on_approval,
                depth=depth,
                topology=topology,
                automation=automation,
            )
            self.broadcast({"type": "task_updated", "task_id": result.run_id, "status": str(result.status)})
        except Exception as exc:
            with self.board() as board:
                board.set_run_status(task_id, RunStatus.BLOCKED)
                board.add_error(task_id, None, "worker_exception", str(exc))
            self.broadcast({"type": "task_updated", "task_id": task_id, "status": str(RunStatus.BLOCKED)})

    def _resume_task(self, task_id: str, workspace: Path, *, max_cost_usd: float) -> None:
        try:
            runtime = self._runtime(workspace)
            result = runtime.resume(task_id, max_cost_usd=max_cost_usd)
            self.broadcast({"type": "task_updated", "task_id": result.run_id, "status": str(result.status)})
        except Exception as exc:
            with self.board() as board:
                board.set_run_status(task_id, RunStatus.BLOCKED)
                board.add_error(task_id, None, "worker_exception", str(exc))
            self.broadcast({"type": "task_updated", "task_id": task_id, "status": str(RunStatus.BLOCKED)})

    def _runtime(self, workspace: Path) -> SupervisorRuntime:
        return SupervisorRuntime(
            workspace,
            runs_dir=self.paths.runs_dir,
            state_db=self.paths.db_path,
            worktrees_root=self.paths.worktrees_dir,
            write_dashboards=False,
        )

    def _task_summary(self, board: Blackboard, run: dict[str, Any]) -> dict[str, Any]:
        run_id = str(run["run_id"])
        context = self._task_context(run_id)
        stages = board.table_rows("stages", run_id=run_id)
        approvals = board.table_rows("approvals", run_id=run_id)
        provider_actions = board.list_provider_actions(run_id=run_id)
        usage = board.table_rows("usage_records", run_id=run_id)
        errors = board.table_rows("error_details", run_id=run_id)
        current = next((row["stage_id"] for row in stages if row.get("status") == "running"), "")
        return {
            **run,
            "task_id": run_id,
            "run_id": run_id,
            "current_stage": current,
            "pending_approvals": sum(1 for row in approvals if row.get("status") == str(ApprovalStatus.PENDING)),
            "pending_provider_actions": sum(1 for row in provider_actions if row.get("status") == str(ProviderActionStatus.PENDING)),
            "tokens": sum(int(row.get("tokens") or 0) for row in usage),
            "cost_usd": round(sum(float(row.get("cost_usd") or 0) for row in usage), 6),
            "errors": len(errors),
            "profile": context.get("profile"),
            "gate": context.get("gate"),
            "depth": context.get("depth"),
            "topology": context.get("topology"),
            "skills": [skill.get("name") for skill in context.get("skills", []) if isinstance(skill, dict)],
        }

    def _task_context(self, task_id: str) -> dict[str, Any]:
        path = self.paths.runs_dir / task_id / "task_context.json"
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}


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
