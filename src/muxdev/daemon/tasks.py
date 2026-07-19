"""Daemon-owned task lifecycle manager."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import shutil
import subprocess
import threading
import weakref
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar
from uuid import uuid4

from ..application import LifecycleService, TaskCommandService, TaskQueryService, TaskQueueCoordinator, TaskRuntimeService
from ..clients.sessions import TmuxBackend
from ..config.loader import path_config
from ..core.projects import resolve_project_root
from ..core.platforms import follow_file_command, hidden_subprocess_kwargs
from ..core.text_cleaning import is_false_positive_provider_action
from ..domain import CancellationToken, ExecutionGuard, ExecutionLease, RunSpec
from ..models import ApprovalStatus, ProviderActionStatus, RunStatus
from ..providers import get_runtime_provider
from ..providers.certification import certification_is_current
from ..providers.policy import isolation_summary
from ..runtime import SupervisorRuntime, new_run_id
from ..runtime.recovery import finalize_stage_recovery
from ..services.dashboard_run import build_run_dashboard_payload, startup_dashboard_payload
from ..services.deliverables import workflow_deliverable_status
from ..services.feedback import route_feedback
from ..services.attestation_bundle import export_attestation_bundle
from ..services.multirepo import plan_multi_repo_orchestration
from ..services.progress import enrich_provider_attempts, enrich_stages, progress_summary
from ..services.provider_learning import refresh_provider_learning
from ..services.provider_scores import build_provider_scores
from ..services.routing import replay_route
from ..services.routing_benchmark import (
    build_benchmark_plan,
    export_benchmark_results,
    load_registered_routing_suite,
    run_replay_benchmark,
)
from ..services.demo import list_demo_scenarios, load_demo_scenario, start_managed_demo
from ..services.local_auth import LocalApiAuth
from ..services.task_story import build_task_story, query_dashboard_task_summaries
from ..storage import ActiveExecutionError, Blackboard, DurableExecutionQueue
from ..storage.repositories import ProviderActionsRepository
from ..storage.read_models import DashboardReadModel
from ..workflows import load_workflow
from .event_bus import EventBus
from .paths import DaemonPaths, daemon_runtime_settings, default_daemon_paths
from .queue import DurableWorkerPool


TERMINAL_STATUSES = {str(RunStatus.COMPLETED), str(RunStatus.BLOCKED), str(RunStatus.ABORTED)}


def _dismiss_false_positive_provider_actions(board: Blackboard, run_id: str) -> list[str]:
    dismissed: list[str] = []
    for row in board.list_provider_actions(status=str(ProviderActionStatus.PENDING), run_id=run_id):
        if not is_false_positive_provider_action(row):
            continue
        action_id = str(row.get("action_id") or "")
        if not action_id:
            continue
        board.update_provider_action_status(action_id, ProviderActionStatus.DISMISSED)
        dismissed.append(action_id)
    return dismissed


def _terminal_handoff_for_run(board: Blackboard, run_dir: Path, run_id: str, agent: str) -> dict[str, Any]:
    native = _native_cli_handoff(board, run_id, agent)
    if native:
        return native
    transcript = _transcript_path_for_agent(board, run_dir, run_id, agent)
    return {
        "mode": "transcript",
        "command": follow_file_command(transcript),
        "path": str(transcript),
        "fallback_reason": "no native provider CLI session is recorded for this run",
    }


def _native_cli_handoff(board: Blackboard, run_id: str, agent: str) -> dict[str, Any] | None:
    for action in board.list_provider_actions(run_id=run_id):
        if not _agent_matches(agent, action):
            continue
        command = str(action.get("attach_command") or "").strip()
        if not _is_real_attach_command(command):
            continue
        return {
            "mode": "native_cli",
            "command": command,
            "provider": action.get("provider"),
            "role": action.get("role"),
            "stage_id": action.get("stage_id"),
            "source": "provider_action",
        }
    tmux = TmuxBackend()
    for row in board.table_rows("agents", run_id=run_id):
        if str(row.get("role") or "") != agent:
            continue
        session = str(row.get("session_id") or "")
        if session.startswith("tmux:") and tmux.available:
            session_name = session.split(":", 1)[1]
            return {
                "mode": "tmux",
                "command": tmux.attach_command(session_name),
                "session": session_name,
                "raw_session": session,
                "source": "agent_session",
            }
    return None


def _transcript_path_for_agent(board: Blackboard, run_dir: Path, run_id: str, agent: str) -> Path:
    for action in board.list_provider_actions(run_id=run_id):
        if not _agent_matches(agent, action):
            continue
        transcript = _existing_run_path(run_dir, str(action.get("transcript_path") or ""))
        if transcript:
            return transcript
    candidates: list[Path] = []
    for folder, pattern in ((run_dir / "provider_sessions", "*.transcript.log"), (run_dir / "session", f"*{agent}*.log")):
        if folder.exists():
            candidates.extend(path for path in folder.glob(pattern) if path.is_file())
    if candidates:
        return max(candidates, key=lambda path: path.stat().st_mtime)
    return run_dir / "trace.jsonl"


def _existing_run_path(run_dir: Path, value: str) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = run_dir / path
    return path if path.exists() else None


def _agent_matches(agent: str, row: dict[str, Any]) -> bool:
    return agent in {str(row.get("role") or ""), str(row.get("stage_id") or ""), str(row.get("provider") or "")}


def _is_real_attach_command(command: str) -> bool:
    if not command:
        return False
    return not command.startswith("muxdev attach ")


@dataclass
class TaskManager:
    """Own all daemon-side writes to task state and artifacts."""

    _instance_refs: ClassVar[list[weakref.ReferenceType["TaskManager"]]] = []

    paths: DaemonPaths = field(default_factory=default_daemon_paths)
    lock: threading.RLock = field(default_factory=threading.RLock)
    workers: dict[str, threading.Thread] = field(default_factory=dict)
    subscribers: set[asyncio.Queue[dict[str, Any]]] = field(default_factory=set)
    queue: DurableWorkerPool = field(init=False)
    events: EventBus = field(init=False)
    storage_boot_error: str | None = field(init=False, default=None)
    worker_count: int | None = None
    lease_ms: int | None = None
    heartbeat_ms: int | None = None
    cancel_grace_ms: int | None = None
    poll_ms: int = 250
    execution_guards: dict[str, ExecutionGuard] = field(init=False, default_factory=dict)
    auth: LocalApiAuth = field(init=False)
    commands: TaskCommandService = field(init=False)
    queries: TaskQueryService = field(init=False)
    coordinator: TaskQueueCoordinator = field(init=False)

    def __post_init__(self) -> None:
        type(self)._instance_refs = [ref for ref in type(self)._instance_refs if ref() is not None]
        type(self)._instance_refs.append(weakref.ref(self))
        self.events = EventBus(subscribers=self.subscribers)
        self.commands = TaskCommandService(self)
        self.queries = TaskQueryService(self)
        self.coordinator = TaskQueueCoordinator(self)
        self.paths.ensure()
        self.auth = LocalApiAuth(self.paths.data_dir)
        daemon_settings = daemon_runtime_settings(self.paths)
        self.worker_count = int(self.worker_count or daemon_settings["workers"])
        self.lease_ms = int(self.lease_ms or daemon_settings["lease_ms"])
        self.heartbeat_ms = int(self.heartbeat_ms or daemon_settings["heartbeat_ms"])
        self.cancel_grace_ms = int(self.cancel_grace_ms or daemon_settings["cancel_grace_ms"])
        try:
            with self.board() as board:
                board.list_runs()
        except sqlite3.OperationalError as exc:
            if "readonly" not in str(exc).lower() and "read-only" not in str(exc).lower():
                raise
            # A read-only legacy database must not prevent diagnostics-only API
            # surfaces from starting. Any stateful operation still fails closed.
            with Blackboard(self.paths.data_dir, db_path=self.paths.db_path, readonly=True) as board:
                board.list_runs()
            self.storage_boot_error = str(exc)
        self.queue = DurableWorkerPool(
            board_factory=self.board,
            execute=self._execute_execution,
            publish=self.broadcast,
            worker_count=self.worker_count,
            lease_ms=self.lease_ms,
            heartbeat_ms=self.heartbeat_ms,
            poll_ms=self.poll_ms,
        )
        if not self.storage_boot_error:
            self._reconcile_legacy_runs()
            with self.board() as board:
                states = DurableExecutionQueue(board).status().get("queue", {})
            if isinstance(states, dict) and any(int(states.get(state, 0) or 0) for state in ("queued", "retry_wait", "leased", "running", "cancel_requested")):
                self.queue.start()

    def _reconcile_legacy_runs(self) -> None:
        """Baseline pre-v3 active Runs without guessing opaque outcomes."""
        with self.board() as board:
            queue = DurableExecutionQueue(board)
            for run in board.list_runs():
                run_id = str(run["run_id"])
                status = str(run.get("status") or "")
                if status not in {str(RunStatus.CREATED), str(RunStatus.RUNNING)}:
                    continue
                if queue.latest_for_run(run_id, internal=True) is not None:
                    continue
                with board.unit_of_work():
                    self._ensure_durable_run_spec(board, run_id, run=run)
                    provider_running = board.conn.execute(
                        "SELECT 1 FROM provider_attempts WHERE run_id=? AND status='running' LIMIT 1", (run_id,)
                    ).fetchone()
                    worktree_missing = status == str(RunStatus.RUNNING) and not Path(str(run["worktree"])).exists()
                    reason = None
                    if provider_running is not None:
                        reason = "legacy opaque Provider outcome is unknown"
                    elif worktree_missing:
                        reason = "legacy running task has no recoverable worktree"
                    queue.import_legacy_run(
                        run_id,
                        command="start" if status == str(RunStatus.CREATED) else "resume",
                        ambiguous_reason=reason,
                    )

    def board(self) -> Blackboard:
        return Blackboard(self.paths.data_dir, db_path=self.paths.db_path, event_sink=self.broadcast)

    def submit_task(
        self,
        *,
        task: str,
        workspace: Path,
        provider: str = "mock",
        workflow: str = "software-dev",
        gate: str | None = None,
        require_approval: set[str] | None = None,
        max_cost_usd: float = 0.5,
        role_providers: dict[str, str] | None = None,
        skills: list[dict[str, object]] | None = None,
        ci_block_on_approval: bool = False,
        depth: str | None = None,
        automation: dict[str, object] | None = None,
        routing_policy: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        workspace = resolve_project_root(workspace)
        spec = RunSpec.from_submit_payload(
            task=task,
            workspace=workspace,
            provider=provider,
            workflow=workflow,
            run_id=new_run_id(),
            gate=gate,
            require_approval=require_approval,
            max_cost_usd=max_cost_usd,
            role_providers=role_providers,
            skills=skills,
            ci_block_on_approval=ci_block_on_approval,
            depth=depth,
            automation=automation,
            routing_policy=routing_policy,
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
            job = DurableExecutionQueue(board).create_initial(
                spec,
                worktree=self._project_worktree_dir(spec.workspace, task_id),
            )
        self.queue.notify()
        self.broadcast({"type": "task_submitted", "task_id": task_id})
        return {
            "task_id": task_id,
            "run_id": task_id,
            "status": str(RunStatus.CREATED),
            "dashboard_url": f"/tasks/{task_id}",
            "gate": spec.gate,
            "depth": spec.depth,
            "skills": [skill.name for skill in spec.skills],
            "job_id": job["job_id"],
        }

    def continue_task(self, task_id: str | None = None, *, max_cost_usd: float = 0.5) -> dict[str, Any]:
        resolved = self.resolve_task_id(task_id or "latest")
        run = self.get_run(resolved)
        with self.board() as board:
            dismissed_actions = _dismiss_false_positive_provider_actions(board, resolved)
            pending_actions = ProviderActionsRepository(board).list_pending(resolved)
            if pending_actions:
                LifecycleService(board).transition_run(resolved, RunStatus.AWAITING_PROVIDER_ACTION)
                return {
                    "task_id": resolved,
                    "run_id": resolved,
                    "status": str(RunStatus.AWAITING_PROVIDER_ACTION),
                    "provider_actions": pending_actions,
                    "dismissed_provider_actions": dismissed_actions,
                }
            pending_feedback = _pending_design_feedback_requests(board, resolved)
            if pending_feedback:
                LifecycleService(board).transition_run(resolved, RunStatus.AWAITING_FEEDBACK)
                return {
                    "task_id": resolved,
                    "run_id": resolved,
                    "status": str(RunStatus.AWAITING_FEEDBACK),
                    "feedback_requests": pending_feedback,
                    "dismissed_provider_actions": dismissed_actions,
                }
        try:
            with self.board() as board:
                queue = DurableExecutionQueue(board)
                with board.unit_of_work():
                    self._ensure_durable_run_spec(board, resolved, run=run)
                    if str(run.get("status") or "") != str(RunStatus.COMPLETED):
                        LifecycleService(board).transition_run(
                            resolved,
                            RunStatus.RUNNING,
                            recovery_reason="user requested continue",
                            idempotency_key=f"execution:continue:{uuid4().hex}",
                        )
                    job = queue.enqueue(
                        resolved,
                        command="resume",
                        idempotency_key=f"continue:{uuid4().hex}",
                        request={"max_cost_usd": max_cost_usd},
                    )
        except ActiveExecutionError:
            return {"task_id": resolved, "run_id": resolved, "status": "already_running"}
        self.queue.notify()
        self.broadcast({"type": "task_continue_requested", "task_id": resolved})
        return {"task_id": resolved, "run_id": resolved, "job_id": job["job_id"], "status": "continue_requested", "dismissed_provider_actions": dismissed_actions}

    def cancel_task(self, task_id: str, *, reason: str = "", wait: bool = False, timeout: float = 30.0) -> dict[str, Any]:
        resolved = self.resolve_task_id(task_id)
        with self.board() as board:
            result = DurableExecutionQueue(board).request_cancel(resolved, reason=reason)
        self.queue.signal_cancel(resolved, reason)
        if wait and result.get("status") == "cancel_requested":
            self.queue.wait(resolved, timeout=timeout)
            with self.board() as board:
                latest = DurableExecutionQueue(board).latest_for_run(resolved)
                result = {
                    **result,
                    "status": latest.get("state") if latest else result["status"],
                    "run_status": str(board.get_run(resolved)["status"]),
                }
        self.broadcast({"type": "task_cancel_requested", "task_id": resolved, "status": result["status"]})
        return {"task_id": resolved, **result}

    def stop_task(self, task_id: str) -> dict[str, Any]:
        """Compatibility alias for the durable cancellation contract."""
        return self.cancel_task(task_id, reason="legacy task stop")

    def task_executions(self, task_id: str) -> dict[str, Any]:
        resolved = self.resolve_task_id(task_id)
        with self.board() as board:
            queue = DurableExecutionQueue(board)
            return {"run_id": resolved, "jobs": queue.list_for_run(resolved), "events": queue.events_for_run(resolved)}

    def reconcile_task(
        self,
        task_id: str,
        *,
        decision: str,
        reason: str,
        acknowledge_duplicate_risk: bool = False,
    ) -> dict[str, Any]:
        resolved = self.resolve_task_id(task_id)
        with self.board() as board:
            result = DurableExecutionQueue(board).reconcile(
                resolved,
                decision=decision,
                reason=reason,
                acknowledge_duplicate_risk=acknowledge_duplicate_risk,
            )
        if decision == "retry":
            self.queue.notify()
        self.broadcast({"type": "task_reconciled", "task_id": resolved, "decision": decision})
        return {"task_id": resolved, **result}

    def list_tasks(self) -> list[dict[str, Any]]:
        with self.board() as board:
            rows_by_table = {
                "stages": _rows_by_run(board.table_rows("stages")),
                "approvals": _rows_by_run(board.table_rows("approvals")),
                "provider_actions": _rows_by_run(board.table_rows("provider_actions")),
                "provider_attempts": _rows_by_run(board.table_rows("provider_attempts")),
                "usage_records": _rows_by_run(board.table_rows("usage_records")),
                "error_details": _rows_by_run(board.table_rows("error_details")),
                "test_results": _rows_by_run(board.table_rows("test_results")),
                "review_blockers": _rows_by_run(board.table_rows("review_blockers")),
                "evidence_evaluations": _rows_by_run(board.table_rows("evidence_evaluations")),
                "snapshots": _rows_by_run(board.table_rows("snapshots")),
                "artifacts": _rows_by_run(board.table_rows("artifacts")),
                "feedback_events": _rows_by_run(board.table_rows("feedback_events")),
            }
            return [self._task_summary(board, row, rows_by_table=rows_by_table) for row in board.list_runs()]

    def task_detail(self, task_id: str) -> dict[str, Any]:
        resolved = self.resolve_task_id(task_id)
        with self.board() as board:
            run = board.get_run(resolved)
            workspace = Path(run["workspace"])
            run_dir = self._run_dir(resolved, run=run)
            payload = DashboardReadModel(
                workspace,
                run_dir,
                resolved,
                board,
                build_run_dashboard_payload,
                context=self._task_context(resolved),
            ).load()
            events = board.list_harness_events(resolved)
            payload["harness"] = {
                "event_count": len(events),
                "latest_event": events[-1].to_dict() if events else None,
                "attempts": isolation_summary(board, resolved)["attempts"],
            }
            payload["isolation"] = isolation_summary(board, resolved)
            assignments = board.list_review_assignments(resolved)
            payload["routing"] = {
                "feature_set": board.latest_task_feature_set(resolved),
                "current": board.latest_route_decision(resolved, kind="main"),
            }
            payload["heterogeneous_review"] = {
                "assignments": assignments,
                "current": assignments[-1] if assignments else None,
            }
            attestation = board.latest_delivery_attestation(resolved)
            payload["attestation"] = (
                {
                    "attestation_id": attestation.get("attestation_id"),
                    "generation": attestation.get("generation"),
                    "status": attestation.get("status"),
                    "payload_hash": attestation.get("payload_hash"),
                    "key_id": attestation.get("key_id"),
                    "public_key_fingerprint": attestation.get("public_key_fingerprint"),
                    "evidence_valid": attestation.get("evidence_valid"),
                    "identity_status": attestation.get("identity_status"),
                    "warning": "unsigned delivery is not trusted production evidence" if attestation.get("status") != "signed" else None,
                }
                if attestation
                else {"status": "legacy_unsigned", "history_complete": False}
            )
            return payload

    def dashboard_tasks(self, *, cursor: str | None = None, limit: int = 50) -> dict[str, Any]:
        with self.board() as board:
            return query_dashboard_task_summaries(
                board, cursor_secret=self.auth.cursor_secret, cursor=cursor, limit=limit
            )

    def task_story(self, task_id: str, *, cursor: str | None = None, limit: int = 200) -> dict[str, Any]:
        resolved = self.resolve_task_id(task_id)
        with self.board() as board:
            return build_task_story(
                board, resolved, cursor_secret=self.auth.cursor_secret, cursor=cursor, limit=limit
            )

    def demo_scenarios(self) -> list[dict[str, Any]]:
        return list_demo_scenarios()

    def demo_scenario(self, scenario_id: str) -> dict[str, Any]:
        return load_demo_scenario(scenario_id)

    def start_demo(self, scenario_id: str, *, workspace: Path) -> dict[str, Any]:
        return start_managed_demo(self, scenario_id=scenario_id, workspace=workspace)

    def provider_certifications(self, *, provider: str | None = None) -> list[dict[str, Any]]:
        with self.board() as board:
            rows = board.list_adapter_certifications(provider=provider)
        adapters: dict[str, object] = {}
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            name = str(item.get("provider") or "")
            try:
                adapter = adapters.setdefault(name, get_runtime_provider(name))
                if not certification_is_current(item, adapter.probe()):
                    item["recorded_status"] = item.get("status")
                    item["status"] = "stale"
            except (OSError, ValueError):
                item["recorded_status"] = item.get("status")
                item["status"] = "stale"
            result.append(item)
        return result

    def harness_events(self, task_id: str, *, stage_id: str | None = None) -> dict[str, Any]:
        resolved = self.resolve_task_id(task_id)
        with self.board() as board:
            events = board.list_harness_events(resolved, stage_id=stage_id)
        return {"run_id": resolved, "events": [event.to_dict() for event in events]}

    def task_isolation(self, task_id: str) -> dict[str, object]:
        resolved = self.resolve_task_id(task_id)
        with self.board() as board:
            return isolation_summary(board, resolved)

    def task_route(self, task_id: str) -> dict[str, object]:
        resolved = self.resolve_task_id(task_id)
        with self.board() as board:
            return {
                "run_id": resolved,
                "feature_set": board.latest_task_feature_set(resolved),
                "decisions": board.list_route_decisions(resolved),
                "current": board.latest_route_decision(resolved, kind="main"),
            }

    def task_review(self, task_id: str) -> dict[str, object]:
        resolved = self.resolve_task_id(task_id)
        with self.board() as board:
            assignments = board.list_review_assignments(resolved)
            return {
                "run_id": resolved,
                "assignments": assignments,
                "current": assignments[-1] if assignments else None,
            }

    def routing_replay(self, task_id: str, *, benchmark_snapshot_id: str | None = None) -> dict[str, object]:
        resolved = self.resolve_task_id(task_id)
        with self.board() as board:
            return replay_route(board, run_id=resolved, benchmark_snapshot_id=benchmark_snapshot_id)

    def routing_snapshots(self) -> list[dict[str, Any]]:
        with self.board() as board:
            return board.list_benchmark_snapshots()

    def routing_benchmark(
        self,
        suite_id: str,
        *,
        live: bool = False,
        acknowledged: bool = False,
        max_cost_usd: float | None = None,
    ) -> dict[str, object]:
        suite = load_registered_routing_suite(suite_id)
        plan = build_benchmark_plan(
            suite,
            live=live,
            acknowledged=acknowledged,
            max_cost_usd=max_cost_usd,
        )
        snapshot_id = f"bench_{uuid4().hex}"
        with self.board() as board:
            snapshot = board.register_benchmark_snapshot(
                snapshot_id=snapshot_id,
                suite_name=str(suite.get("name") or suite_id),
                payload=plan,
            )
        return {
            "status": "live_plan_registered" if live else "offline_snapshot_registered",
            "execution_started": False,
            "snapshot": snapshot,
            "plan": plan,
        }

    def task_attestation(self, run_id: str) -> dict[str, Any] | None:
        with self.board() as board:
            return board.latest_delivery_attestation(run_id)

    def attestation_export_record(self, export_id: str) -> dict[str, Any] | None:
        with self.board() as board:
            return board.get_attestation_export(export_id)

    def export_task_attestation(self, run_id: str, output: Path) -> dict[str, Any]:
        with self.board() as board:
            run = board.get_run(run_id)
            run_dir = self._run_dir(run_id, run=run)
            return export_attestation_bundle(board, run_id=run_id, run_dir=run_dir, output=output)

    def replay_task_state(self, run_id: str) -> dict[str, Any]:
        with self.board() as board:
            return board.replay_run(run_id)

    def plan_multi_repo(self, *, workspace: Path, repos: list[Path], task: str, mode: str) -> dict[str, Any]:
        with self.board() as board:
            return plan_multi_repo_orchestration(workspace, repos=repos, task=task, mode=mode, blackboard=board)

    def benchmark_replay(self, suite_id: str = "trusted-routing-v1") -> dict[str, Any]:
        with self.board() as board:
            return run_replay_benchmark(board, suite_id=suite_id)

    def benchmark_executions(self) -> list[dict[str, Any]]:
        with self.board() as board:
            return board.list_benchmark_executions()

    def benchmark_execution(self, execution_id: str) -> dict[str, Any]:
        with self.board() as board:
            payload = board.get_benchmark_execution(execution_id)
        if payload is None:
            raise KeyError(f"benchmark execution not found: {execution_id}")
        return payload

    def benchmark_report(self, execution_id: str) -> dict[str, Any]:
        payload = self.benchmark_execution(execution_id)
        report = payload.get("report")
        if not isinstance(report, dict):
            raise KeyError(f"benchmark report not found: {execution_id}")
        return report

    def cancel_benchmark(self, execution_id: str) -> dict[str, Any]:
        with self.board() as board:
            current = board.get_benchmark_execution(execution_id)
            if current is None:
                raise KeyError(f"benchmark execution not found: {execution_id}")
            if str(current.get("status")) in {"completed", "failed", "cancelled", "preflight_failed"}:
                return current
            updated = board.update_benchmark_execution(execution_id, status="cancelled")
            board.append_benchmark_event(execution_id, "benchmark.cancelled", {"operator_source": "local_api"})
            return updated

    def export_benchmark(self, execution_id: str, output: Path) -> dict[str, Any]:
        with self.board() as board:
            return export_benchmark_results(board, execution_id, output)

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
        if kind == "plan_feedback" and run_id:
            return self.run_plan_feedback(
                run_id,
                content,
                source=source,
                payload=payload or {},
                auto_submit=auto_submit,
                max_cost_usd=0.5,
            )
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
                gate="ci" if kind in {"ci_failed", "local_test_failure"} else None,
                require_approval=set(),
                max_cost_usd=0.5,
                role_providers={routed.route_to: provider},
                skills=[],
                ci_block_on_approval=kind in {"ci_failed", "local_test_failure"},
                depth="ci" if kind in {"ci_failed", "local_test_failure"} else None,
                automation={"intent": "ci" if kind in {"ci_failed", "local_test_failure"} else "feedback", "feedback_id": routed.feedback_id, "route_to": routed.route_to},
            )
            result["submitted"] = submitted
            if routed.rescue_id:
                with self.board() as board:
                    board.update_ci_rescue(routed.rescue_id, rescue_run_id=str(submitted["run_id"]), status="submitted")
        self.broadcast({"type": "feedback_routed", "feedback_id": routed.feedback_id, "route_to": routed.route_to, "auto": routed.auto})
        return result

    def run_plan_feedback(
        self,
        run_id: str,
        feedback: str,
        *,
        source: str = "user",
        payload: dict[str, Any] | None = None,
        auto_submit: bool = True,
        max_cost_usd: float = 0.5,
    ) -> dict[str, Any]:
        content = feedback.strip()
        if not content:
            raise ValueError("feedback is required")
        resolved = self.resolve_task_id(run_id)
        with self.board() as board:
            pending_requests = _pending_design_feedback_requests(board, resolved)
            feedback_id = board.add_feedback_event(
                run_id=resolved,
                source=source,
                kind="plan_feedback",
                severity="medium",
                status="pending",
                route_to="plan",
                content=content,
                payload={
                    **(payload or {}),
                    "source_feedback_requests": [row.get("feedback_id") for row in pending_requests],
                },
            )
            for row in pending_requests:
                board.update_feedback_event_status(str(row.get("feedback_id")), "handled")
            reset_stages = _plan_feedback_reset_stages(board, resolved)
            for stage_id in reset_stages:
                board.reset_stage(resolved, stage_id)
            LifecycleService(board).transition_run(resolved, RunStatus.RUNNING)
        result: dict[str, Any] = {
            "task_id": resolved,
            "run_id": resolved,
            "feedback_id": feedback_id,
            "status": "pending",
            "kind": "plan_feedback",
            "handled_feedback_requests": [row.get("feedback_id") for row in pending_requests],
            "reset_stages": reset_stages,
        }
        self.broadcast({"type": "plan_feedback_submitted", "run_id": resolved, "feedback_id": feedback_id})
        if auto_submit:
            result["continue"] = self.continue_task(resolved, max_cost_usd=max_cost_usd)
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
            match = _resolve_approval(board, approval_id)
            resolved_id = str(match["approval_id"])
            board.decide_approval(resolved_id, status)
            match = next(
                (row for row in board.list_approvals(run_id=str(match["run_id"])) if row.get("approval_id") == resolved_id),
                match,
            )
            match["decided"] = True
        self.broadcast({"type": "approval_decided", "approval_id": match["approval_id"], "status": str(status)})
        return match

    def plan_feedback(self, approval_id: str, feedback: str) -> dict[str, Any]:
        content = feedback.strip()
        if not content:
            raise ValueError("feedback is required")
        with self.board() as board:
            match = _resolve_approval(board, approval_id)
            resolved_id = str(match["approval_id"])
            run_id = str(match["run_id"])
            approval_type = str(match.get("type") or "")
            if approval_type not in {"plan", "design"}:
                raise ValueError(f"feedback is only supported for plan approvals, got: {approval_type}")
            feedback_id = board.add_feedback_event(
                run_id=run_id,
                source="user",
                kind="plan_feedback",
                severity="medium",
                status="pending",
                route_to="plan",
                content=content,
                payload={
                    "approval_id": resolved_id,
                    "stage_id": match.get("stage_id"),
                    "approval_type": approval_type,
                },
            )
            board.decide_approval(resolved_id, ApprovalStatus.FEEDBACK)
            reset_stages = _plan_feedback_reset_stages(board, run_id)
            for stage_id in reset_stages:
                board.reset_stage(run_id, stage_id)
            LifecycleService(board).transition_run(run_id, RunStatus.RUNNING)
            updated = next(
                (row for row in board.list_approvals(run_id=run_id) if row.get("approval_id") == resolved_id),
                match,
            )
            updated["feedback_id"] = feedback_id
            updated["reset_stages"] = reset_stages
        self.broadcast({"type": "approval_feedback", "approval_id": resolved_id, "run_id": run_id, "feedback_id": feedback_id})
        return updated

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
            encoding="utf-8",
            errors="replace",
            check=False,
            **hidden_subprocess_kwargs(),
        )
        clean = subprocess.run(
            ["git", "clean", "-fd"],
            cwd=worktree,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
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
        checkout = subprocess.run(
            ["git", "checkout", "--", "."],
            cwd=worktree,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            **hidden_subprocess_kwargs(),
        )
        clean = subprocess.run(
            ["git", "clean", "-fd"],
            cwd=worktree,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            **hidden_subprocess_kwargs(),
        )
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
                encoding="utf-8",
                errors="replace",
                check=False,
                **hidden_subprocess_kwargs(),
            )
        apply_failed = apply_result is not None and apply_result.returncode != 0
        if apply_failed and fallback and "already exists in working directory" in (apply_result.stderr or ""):
            apply_failed = False
        failed = apply_failed or (git_failed and not fallback)
        payload: dict[str, Any] = {
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
        if not failed:
            run_dir = self._run_dir(task_id)
            with self.board() as board:
                run = board.get_run(task_id)
                archived = run_dir / "workflow.yaml"
                workflow = load_workflow(str(archived)) if archived.is_file() else load_workflow(str(run["workflow"]))
                payload.update(
                    finalize_stage_recovery(
                        board,
                        run_dir=run_dir,
                        run_id=task_id,
                        workflow=workflow,
                        from_stage=stage_id,
                        snapshot_path=patch_path,
                        snapshot_hash=str(rows[-1].get("patch_hash") or ""),
                    )
                )
            self.broadcast(
                {
                    "type": "recovery_forked",
                    "run_id": task_id,
                    "recovery_id": payload.get("recovery_id"),
                    "from_stage": stage_id,
                    "invalidated_stages": payload.get("invalidated_stages"),
                }
            )
        return payload

    def attach_command(self, task_id: str, *, agent: str = "implementer") -> dict[str, Any]:
        resolved = self.resolve_task_id(task_id)
        run_dir = self._run_dir(resolved)
        with self.board() as board:
            run = board.get_run(resolved)
            handoff = _terminal_handoff_for_run(board, run_dir, resolved, agent)
            status = "attached" if handoff.get("mode") in {"native_cli", "tmux"} else "transcript"
            board.upsert_agent(
                resolved,
                agent,
                str(run["provider"]),
                session_id=str(handoff.get("raw_session") or handoff.get("session") or f"{resolved}:{agent}"),
                status=status,
            )
        return {"task_id": resolved, "run_id": resolved, "agent": agent, "session_id": f"{resolved}:{agent}", "status": str(handoff.get("mode") or "transcript"), "handoff": handoff}

    def daemon_status(self) -> dict[str, Any]:
        if self.storage_boot_error:
            return {
                "status": "degraded",
                "tasks": 0,
                "running_tasks": 0,
                "queue_length": 0,
                "data": str(self.paths.data_dir),
                "database": str(self.paths.db_path),
                "storage_error": self.storage_boot_error,
            }
        tasks = self.list_tasks()
        runtime = self.runtime_status()
        return {
            "status": "running",
            "tasks": len(tasks),
            "running_tasks": sum(
                1 for task in tasks
                if task.get("status") in {str(RunStatus.ROUTING), str(RunStatus.RUNNING), str(RunStatus.REVIEWING)}
            ),
            "queue_length": int(runtime.get("queue", {}).get("queued", 0)) if isinstance(runtime.get("queue"), dict) else 0,
            "runtime": runtime,
            "data": str(self.paths.data_dir),
            "database": str(self.paths.db_path),
        }

    def runtime_status(self) -> dict[str, Any]:
        if self.storage_boot_error:
            return {"accepting": False, "status": "degraded", "storage_error": self.storage_boot_error}
        return self.queue.status()

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

    def wait(self, task_id: str, *, timeout: float = 30.0) -> bool:
        return self.queue.wait(self.resolve_task_id(task_id), timeout)

    def close(self, *, timeout: float = 30.0) -> list[str]:
        """Wait for daemon-owned worker threads during an orderly shutdown."""
        remaining = self.queue.shutdown(timeout)
        type(self)._instance_refs = [
            ref for ref in type(self)._instance_refs
            if (instance := ref()) is not None and instance is not self
        ]
        return remaining

    @classmethod
    def live_instances(cls) -> list["TaskManager"]:
        """Return live managers for diagnostics and deterministic test cleanup."""
        instances = [instance for ref in cls._instance_refs if (instance := ref()) is not None]
        cls._instance_refs = [weakref.ref(instance) for instance in instances]
        return instances

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        return await self.events.subscribe()

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self.events.unsubscribe(queue)

    def broadcast(self, event: dict[str, Any]) -> None:
        self.events.publish(event)
        run_id = str(event.get("run_id") or event.get("task_id") or "")
        if run_id and event.get("type") != "task_story_invalidated":
            self.events.publish({"type": "task_story_invalidated", "version": 1, "run_id": run_id})

    def _execute_execution(self, lease: ExecutionLease, token: CancellationToken) -> str:
        guard = ExecutionGuard(lease, token)
        self.execution_guards[lease.run_id] = guard
        try:
            with self.board() as board:
                queue = DurableExecutionQueue(board)
                spec = queue.load_run_spec(lease.run_id)
                job = queue.get_job(lease.job_id, internal=True)
                run_status = str(board.get_run(lease.run_id)["status"])
                request = json.loads(str(job.get("request_json") or "{}"))
            if lease.command == "start" and run_status == str(RunStatus.CREATED):
                outcome = self._run_task(spec)
            else:
                outcome = self._resume_task(
                    lease.run_id,
                    spec.workspace,
                    max_cost_usd=float(request.get("max_cost_usd") or spec.policy.max_cost_usd),
                )
            if outcome:
                return str(outcome)
            return str(self.get_run(lease.run_id)["status"])
        finally:
            self.execution_guards.pop(lease.run_id, None)

    def _run_task(self, spec: RunSpec) -> str:
        runtime = self._runtime_for_task(spec.workspace, spec.run_id)
        result = runtime.run(spec.task, **spec.runtime_kwargs())
        return str(result.status)

    def _resume_task(self, task_id: str, workspace: Path, *, max_cost_usd: float) -> str:
        runtime = self._runtime_for_task(workspace, task_id)
        result = runtime.resume(task_id, max_cost_usd=max_cost_usd)
        return str(result.status)

    def _runtime_service(self) -> TaskRuntimeService:
        return TaskRuntimeService(runtime_factory=self._runtime_for_task, board_factory=self.board, publish=self.broadcast)

    def _runtime_for_task(self, workspace: Path, run_id: str | None = None) -> SupervisorRuntime:
        if run_id:
            run_dir = self._run_dir(run_id)
            if self.paths.runs_dir == run_dir.parent:
                return self._runtime(
                    workspace,
                    runs_dir=self.paths.runs_dir,
                    worktrees_root=self.paths.worktrees_dir,
                    execution_guard=self.execution_guards.get(run_id),
                )
        return self._runtime(workspace, execution_guard=self.execution_guards.get(run_id or ""))

    def _runtime(
        self,
        workspace: Path,
        *,
        runs_dir: Path | None = None,
        worktrees_root: Path | None = None,
        execution_guard: ExecutionGuard | None = None,
    ) -> SupervisorRuntime:
        return SupervisorRuntime(
            workspace,
            runs_dir=runs_dir,
            state_db=self.paths.db_path,
            worktrees_root=worktrees_root,
            write_dashboards=False,
            state_event_sink=self.broadcast,
            execution_guard=execution_guard,
            cancel_grace_seconds=float(self.cancel_grace_ms or 10_000) / 1_000,
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
            test_results = board.table_rows("test_results", run_id=run_id)
            review_blockers = board.table_rows("review_blockers", run_id=run_id)
            evaluations = board.table_rows("evidence_evaluations", run_id=run_id)
            snapshots = board.table_rows("snapshots", run_id=run_id)
            artifacts = board.table_rows("artifacts", run_id=run_id)
            feedback_events = board.table_rows("feedback_events", run_id=run_id)
        else:
            stages = rows_by_table["stages"].get(run_id, [])
            approvals = rows_by_table["approvals"].get(run_id, [])
            provider_actions = rows_by_table["provider_actions"].get(run_id, [])
            provider_attempts = rows_by_table["provider_attempts"].get(run_id, [])
            usage = rows_by_table["usage_records"].get(run_id, [])
            errors = rows_by_table["error_details"].get(run_id, [])
            test_results = rows_by_table["test_results"].get(run_id, [])
            review_blockers = rows_by_table["review_blockers"].get(run_id, [])
            evaluations = rows_by_table["evidence_evaluations"].get(run_id, [])
            snapshots = rows_by_table["snapshots"].get(run_id, [])
            artifacts = rows_by_table["artifacts"].get(run_id, [])
            feedback_events = rows_by_table.get("feedback_events", {}).get(run_id, [])
        enriched_stages = enrich_stages(stages)
        enriched_attempts = enrich_provider_attempts(provider_attempts)
        pending_feedback_requests = [
            row
            for row in feedback_events
            if str(row.get("kind") or "") == "design_feedback_request" and str(row.get("status") or "") == "pending"
        ]
        progress = progress_summary(
            run=run,
            stages=enriched_stages,
            provider_attempts=enriched_attempts,
            approvals=approvals,
            provider_actions=provider_actions,
        )
        deliverable_status = workflow_deliverable_status(
            board,
            run_dir=self._run_dir(run_id, run=run),
            run_id=run_id,
            workflow=str(run.get("workflow") or ""),
            require_report=str(run.get("status") or "") == str(RunStatus.COMPLETED),
        )
        completed_deliverables_ok = bool(
            str(run.get("status") or "") == str(RunStatus.COMPLETED) and deliverable_status.get("complete") is True
        )
        current_errors = [] if completed_deliverables_ok else errors
        current_review_blockers = [] if completed_deliverables_ok else review_blockers
        latest_error = _latest_error(current_errors)
        delivery_confidence = _delivery_confidence(
            run,
            test_results=test_results,
            review_blockers=current_review_blockers,
            evaluations=evaluations,
            snapshots=snapshots,
            artifacts=artifacts,
            usage=usage,
            errors=current_errors,
            completed_deliverables_ok=completed_deliverables_ok,
        )
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
            "pending_feedback_requests": len(pending_feedback_requests),
            "feedback_request": pending_feedback_requests[-1] if pending_feedback_requests else None,
            "feedback_events": feedback_events,
            "tokens": sum(int(row.get("tokens") or 0) for row in usage),
            "cost_usd": round(sum(float(row.get("cost_usd") or 0) for row in usage), 6),
            "errors": len(current_errors),
            "historical_errors": len(errors) if completed_deliverables_ok else 0,
            "historical_error_summary": _latest_error(errors) if completed_deliverables_ok else None,
            "error_summary": latest_error,
            "delivery_confidence": delivery_confidence,
            "deliverable_status": deliverable_status,
            "evidence_summary": delivery_confidence["evidence_summary"],
            "gate": context.get("gate"),
            "depth": context.get("depth"),
            "role_providers": context.get("role_providers", {}) if isinstance(context.get("role_providers"), dict) else {},
            "skills": [skill.get("name") for skill in context.get("skills", []) if isinstance(skill, dict)],
            "recover_endpoint": f"/api/tasks/{run_id}/continue",
            "rollback_endpoint": f"/api/tasks/{run_id}/rollback",
            "report_endpoint": f"/api/tasks/{run_id}/report",
            "execution": DurableExecutionQueue(board).latest_for_run(run_id),
            "harness": {
                "adapter_version": enriched_attempts[-1].get("adapter_version") if enriched_attempts else None,
                "certification_id": enriched_attempts[-1].get("certification_id") if enriched_attempts else None,
                "trust_tier": enriched_attempts[-1].get("trust_tier") if enriched_attempts else None,
                "isolation_mode": enriched_attempts[-1].get("isolation_mode") if enriched_attempts else None,
                "waiver_approval_id": enriched_attempts[-1].get("waiver_approval_id") if enriched_attempts else None,
            },
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

    def _ensure_durable_run_spec(self, board: Blackboard, run_id: str, *, run: dict[str, Any] | None = None) -> RunSpec:
        queue = DurableExecutionQueue(board)
        try:
            return queue.load_run_spec(run_id)
        except FileNotFoundError:
            pass
        run = run or board.get_run(run_id)
        context = self._task_context(run_id, run=run)
        policy = context.get("safety_policy", {}) if isinstance(context.get("safety_policy"), dict) else {}
        approvals = policy.get("approval_types", []) if isinstance(policy.get("approval_types"), list) else []
        roles = context.get("role_providers", {}) if isinstance(context.get("role_providers"), dict) else {}
        skills = context.get("skills", []) if isinstance(context.get("skills"), list) else []
        automation = context.get("automation", {}) if isinstance(context.get("automation"), dict) else {}
        spec = RunSpec.from_submit_payload(
            run_id=run_id,
            task=str(run.get("task") or ""),
            workspace=Path(str(run["workspace"])).expanduser().resolve(),
            provider=str(run.get("provider") or "mock"),
            workflow=str(run.get("workflow") or "software-dev"),
            gate=str(context["gate"]) if context.get("gate") else None,
            require_approval={str(item) for item in approvals},
            max_cost_usd=float(policy.get("max_cost_usd") or 0.5),
            role_providers={str(key): str(value) for key, value in roles.items() if value},
            skills=[item for item in skills if isinstance(item, dict)],
            ci_block_on_approval=bool(context.get("ci_block_on_approval")),
            depth=str(context["depth"]) if context.get("depth") else None,
            automation=automation,
        )
        queue.persist_run_spec(spec, legacy=True)
        return spec

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
        if (workspace / ".git").exists():
            return path_config(workspace, "worktrees") / task_id
        return path_config(workspace, "runs") / task_id / "worktree"


def _rows_by_run(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("run_id") or "")].append(row)
    return grouped


def _resolve_approval(board: Blackboard, approval_or_run_id: str) -> dict[str, Any]:
    approvals = board.table_rows("approvals")
    for row in approvals:
        if str(row.get("approval_id") or "") == approval_or_run_id:
            return row

    pending_for_run = [
        row
        for row in approvals
        if str(row.get("run_id") or row.get("task_id") or "") == approval_or_run_id
        and str(row.get("status") or "") == str(ApprovalStatus.PENDING)
    ]
    if len(pending_for_run) == 1:
        return pending_for_run[0]
    if len(pending_for_run) > 1:
        raise KeyError(f"multiple pending approvals for run: {approval_or_run_id}; use an approval id")

    if any(str(row.get("run_id") or "") == approval_or_run_id for row in board.table_rows("runs")):
        raise KeyError(f"no pending approval for run: {approval_or_run_id}; use continue instead")
    raise KeyError(f"approval not found: {approval_or_run_id}")


def _plan_feedback_reset_stages(board: Blackboard, run_id: str) -> list[str]:
    stage_ids = {str(row.get("stage_id") or "") for row in board.table_rows("stages", run_id=run_id)}
    reset: list[str] = []
    if "plan_revise" in stage_ids:
        reset.extend(["plan_revise", "plan_review", "plan_verify", "approve_plan"])
    elif "design_revise" in stage_ids:
        reset.extend(["design_revise", "design_review", "design_verify", "approve_plan", "human_design_approval", "design_pack"])
    else:
        for stage_id in ("quick_plan", "scaffold_plan", "design_brief", "design_plan", "plan", "design"):
            if stage_id in stage_ids:
                reset.append(stage_id)
        for stage_id in ("plan_review", "plan_verify", "design_review", "design_verify", "approve_plan", "human_design_approval", "design_pack"):
            if stage_id in stage_ids:
                reset.append(stage_id)
    return [stage_id for index, stage_id in enumerate(reset) if stage_id in stage_ids and stage_id not in reset[:index]]


def _pending_design_feedback_requests(board: Blackboard, run_id: str) -> list[dict[str, Any]]:
    return [
        row
        for row in board.table_rows("feedback_events", run_id=run_id)
        if str(row.get("kind") or "") == "design_feedback_request" and str(row.get("status") or "") == "pending"
    ]


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


def _delivery_confidence(
    run: dict[str, Any],
    *,
    test_results: list[dict[str, Any]],
    review_blockers: list[dict[str, Any]],
    evaluations: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    usage: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    completed_deliverables_ok: bool = False,
) -> dict[str, Any]:
    evaluation = None if completed_deliverables_ok else _latest_evaluation(evaluations)
    status = str(run.get("status") or "")
    test_total = len(test_results)
    test_failed = sum(1 for row in test_results if not bool(row.get("passed")))
    test_passed = test_total - test_failed
    blocker_total = len(review_blockers)
    high_blockers = sum(1 for row in review_blockers if str(row.get("severity") or "").lower() in {"high", "critical"})
    rollback_available = bool(snapshots)
    diff_available = _has_artifact_kind(artifacts, "diff", "patch") or any(str(row.get("patch_hash") or "") for row in snapshots)

    if evaluation:
        label = str(evaluation.get("label") or "reviewable")
        confidence = float(evaluation.get("confidence") or 0)
        reasons = [str(reason) for reason in evaluation.get("reasons", []) if reason]
        missing = [str(item) for item in evaluation.get("missing_evidence", []) if item]
    else:
        label = "blocked" if errors else ("reviewable" if status == "completed" else "collecting")
        confidence = _fallback_confidence(status, test_total=test_total, test_failed=test_failed, blockers=blocker_total, errors=len(errors), rollback_available=rollback_available)
        reasons = _fallback_reasons(status, test_total=test_total, test_failed=test_failed, blockers=blocker_total, rollback_available=rollback_available)
        missing = []
        if test_total == 0:
            missing.append("tests")
        if not rollback_available:
            missing.append("rollback snapshot")
        if not diff_available:
            missing.append("diff")

    tokens = sum(int(row.get("tokens") or 0) for row in usage)
    cost_usd = round(sum(float(row.get("cost_usd") or 0) for row in usage), 6)
    test_status = "missing" if test_total == 0 else ("failed" if test_failed else "passed")
    review_status = "blocked" if high_blockers else ("needs_review" if blocker_total else "clear")
    return {
        "label": label,
        "confidence": round(confidence, 3),
        "score": round(confidence * 100),
        "tests": {"total": test_total, "passed": test_passed, "failed": test_failed, "status": test_status},
        "review": {"blockers": blocker_total, "high_blockers": high_blockers, "status": review_status},
        "rollback": {"available": rollback_available, "snapshots": len(snapshots)},
        "diff": {"available": diff_available},
        "usage": {"tokens": tokens, "cost_usd": cost_usd},
        "reasons": reasons[:4],
        "missing_evidence": missing[:4],
        "evidence_summary": {
            "label": label,
            "confidence": round(confidence, 3),
            "tests": test_status,
            "review": review_status,
            "rollback": "available" if rollback_available else "missing",
            "diff": "available" if diff_available else "missing",
        },
    }


def _latest_evaluation(evaluations: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not evaluations:
        return None
    return max(evaluations, key=lambda item: str(item.get("created_at") or ""))


def _fallback_confidence(
    status: str,
    *,
    test_total: int,
    test_failed: int,
    blockers: int,
    errors: int,
    rollback_available: bool,
) -> float:
    if errors or status in {"blocked", "aborted", "failed"}:
        return 0.2
    score = 0.45 if status == "completed" else 0.25
    if test_total and not test_failed:
        score += 0.25
    if rollback_available:
        score += 0.15
    if blockers:
        score -= 0.2
    if test_failed:
        score -= 0.25
    return max(0.0, min(0.95, score))


def _fallback_reasons(status: str, *, test_total: int, test_failed: int, blockers: int, rollback_available: bool) -> list[str]:
    reasons: list[str] = []
    if status == "completed":
        reasons.append("run completed")
    if test_total:
        reasons.append("tests passed" if not test_failed else "tests failed")
    if blockers:
        reasons.append(f"{blockers} review blocker(s)")
    if rollback_available:
        reasons.append("rollback snapshot available")
    return reasons


def _has_artifact_kind(artifacts: list[dict[str, Any]], *needles: str) -> bool:
    for row in artifacts:
        text = " ".join(str(row.get(key) or "").lower() for key in ("kind", "name", "path"))
        if any(needle in text for needle in needles):
            return True
    return False


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
