"""Application service for daemon-owned runtime workers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..domain import RunSpec
from ..models import RunStatus


RuntimeFactory = Callable[[Path], Any]
BoardFactory = Callable[[], Any]
Publisher = Callable[[dict[str, Any]], None]


@dataclass
class TaskRuntimeService:
    runtime_factory: RuntimeFactory
    board_factory: BoardFactory
    publish: Publisher

    def run(self, spec: RunSpec) -> None:
        try:
            runtime = self.runtime_factory(spec.workspace)
            result = runtime.run(spec.task, **spec.runtime_kwargs())
            self.publish({"type": "task_updated", "task_id": result.run_id, "status": str(result.status)})
        except Exception as exc:
            self._record_worker_exception(spec.run_id, exc)

    def resume(self, task_id: str, workspace: Path, *, max_cost_usd: float) -> None:
        try:
            runtime = self.runtime_factory(workspace)
            result = runtime.resume(task_id, max_cost_usd=max_cost_usd)
            self.publish({"type": "task_updated", "task_id": result.run_id, "status": str(result.status)})
        except Exception as exc:
            self._record_worker_exception(task_id, exc)

    def _record_worker_exception(self, task_id: str, exc: Exception) -> None:
        with self.board_factory() as board:
            board.set_run_status(task_id, RunStatus.BLOCKED)
            board.add_error(task_id, None, "worker_exception", str(exc))
        self.publish({"type": "task_updated", "task_id": task_id, "status": str(RunStatus.BLOCKED)})
