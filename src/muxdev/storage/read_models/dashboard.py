"""Dashboard/task-detail read model."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..trace import compact_trace, read_trace

DashboardPayloadLoader = Callable[[Path, Path, str, Any], dict[str, Any]]


@dataclass(frozen=True)
class DashboardReadModel:
    workspace: Path
    run_dir: Path
    run_id: str
    blackboard: Any
    payload_loader: DashboardPayloadLoader
    context: dict[str, Any] | None = None

    def load(self) -> dict[str, Any]:
        payload = self.payload_loader(self.workspace, self.run_dir, self.run_id, self.blackboard)
        payload["task_id"] = self.run_id
        payload["run_id"] = self.run_id
        payload["trace"] = compact_trace(read_trace(self.run_dir))[-50:] if self.run_dir.exists() else []
        payload["context"] = self.context or {}
        return payload
