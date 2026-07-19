"""Task use-case boundaries shared by HTTP, CLI gateways, and the daemon.

The backend implements persistence and scheduling.  These small services make
the read/write split explicit so entry surfaces do not need to know which
runtime or SQLite operation fulfils a use case.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..models import ApprovalStatus, ProviderActionStatus


class TaskBackend(Protocol):
    """Structural port implemented by the daemon task backend."""


@dataclass(frozen=True)
class TaskCommandService:
    backend: TaskBackend

    def submit(self, **request: Any) -> dict[str, Any]:
        return self.backend.submit_task(**request)  # type: ignore[attr-defined]

    def continue_task(self, task_id: str | None, *, max_cost_usd: float = 0.5) -> dict[str, Any]:
        return self.backend.continue_task(task_id, max_cost_usd=max_cost_usd)  # type: ignore[attr-defined]

    def stop(self, task_id: str) -> dict[str, Any]:
        return self.backend.stop_task(task_id)  # type: ignore[attr-defined]

    def approve(self, approval_id: str, status: ApprovalStatus) -> dict[str, Any]:
        return self.backend.decide_approval(approval_id, status)  # type: ignore[attr-defined]

    def planning_feedback(self, approval_id: str, feedback: str) -> dict[str, Any]:
        return self.backend.plan_feedback(approval_id, feedback)  # type: ignore[attr-defined]

    def provider_action(self, action_id: str, status: ProviderActionStatus) -> dict[str, Any]:
        return self.backend.update_provider_action(action_id, status)  # type: ignore[attr-defined]

    def provider_response(self, action_id: str, response: dict[str, object]) -> dict[str, Any]:
        return self.backend.respond_provider_action(action_id, response)  # type: ignore[attr-defined]

    def rollback(self, task_id: str, *, to_stage: str | None = None) -> dict[str, Any]:
        return self.backend.rollback(task_id, to_stage=to_stage)  # type: ignore[attr-defined]

    def export_attestation(self, run_id: str, output: Path) -> dict[str, Any]:
        return self.backend.export_task_attestation(run_id, output)  # type: ignore[attr-defined]

    def plan_multi_repo(self, *, workspace: Path, repos: list[Path], task: str, mode: str) -> dict[str, Any]:
        return self.backend.plan_multi_repo(workspace=workspace, repos=repos, task=task, mode=mode)  # type: ignore[attr-defined]


@dataclass(frozen=True)
class TaskQueryService:
    backend: TaskBackend

    def list_tasks(self) -> list[dict[str, Any]]:
        return self.backend.list_tasks()  # type: ignore[attr-defined]

    def detail(self, task_id: str) -> dict[str, Any]:
        return self.backend.task_detail(task_id)  # type: ignore[attr-defined]

    def dashboard(self, *, cursor: str | None = None, limit: int = 50) -> dict[str, Any]:
        return self.backend.dashboard_tasks(cursor=cursor, limit=limit)  # type: ignore[attr-defined]

    def story(self, task_id: str, *, cursor: str | None = None, limit: int = 200) -> dict[str, Any]:
        return self.backend.task_story(task_id, cursor=cursor, limit=limit)  # type: ignore[attr-defined]

    def diff(self, task_id: str) -> dict[str, Any]:
        return self.backend.diff(task_id)  # type: ignore[attr-defined]

    def report(self, task_id: str) -> dict[str, Any]:
        return self.backend.report(task_id)  # type: ignore[attr-defined]

    def approvals(self, *, status: str | None = None) -> list[dict[str, Any]]:
        return self.backend.approvals(status=status)  # type: ignore[attr-defined]

    def provider_actions(self, *, status: str | None = None, task_id: str | None = None) -> list[dict[str, Any]]:
        return self.backend.provider_actions(status=status, task_id=task_id)  # type: ignore[attr-defined]

    def attestation(self, run_id: str) -> dict[str, Any] | None:
        return self.backend.task_attestation(run_id)  # type: ignore[attr-defined]

    def attestation_export(self, export_id: str) -> dict[str, Any] | None:
        return self.backend.attestation_export_record(export_id)  # type: ignore[attr-defined]

    def replay(self, run_id: str) -> dict[str, Any]:
        return self.backend.replay_task_state(run_id)  # type: ignore[attr-defined]


@dataclass(frozen=True)
class TaskQueueCoordinator:
    backend: TaskBackend

    def cancel(self, task_id: str, *, reason: str = "", wait: bool = False, timeout: float = 30.0) -> dict[str, Any]:
        return self.backend.cancel_task(task_id, reason=reason, wait=wait, timeout=timeout)  # type: ignore[attr-defined]

    def executions(self, task_id: str) -> dict[str, Any]:
        return self.backend.task_executions(task_id)  # type: ignore[attr-defined]

    def reconcile(self, task_id: str, **request: Any) -> dict[str, Any]:
        return self.backend.reconcile_task(task_id, **request)  # type: ignore[attr-defined]

    def wait(self, task_id: str, *, timeout: float = 30.0) -> bool:
        return self.backend.wait(task_id, timeout=timeout)  # type: ignore[attr-defined]

    def export_benchmark(self, execution_id: str, output: Path) -> dict[str, Any]:
        return self.backend.export_benchmark(execution_id, output)  # type: ignore[attr-defined]
