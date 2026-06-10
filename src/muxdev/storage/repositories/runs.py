"""Run repository facade over Blackboard."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...domain import RunSpec
from ...models import RunStatus


@dataclass
class RunsRepository:
    blackboard: Any

    def create(self, spec: RunSpec, *, worktree: Path) -> None:
        self.blackboard.create_run(
            run_id=spec.run_id,
            task=spec.task,
            workflow=spec.workflow,
            provider=spec.default_provider,
            workspace=spec.workspace,
            worktree=worktree,
        )

    def set_status(self, run_id: str, status: RunStatus | str) -> None:
        self.blackboard.set_run_status(run_id, status)

    def get(self, run_id: str) -> dict[str, Any]:
        return self.blackboard.get_run(run_id)

    def list(self, *, status: str | None = None) -> list[dict[str, Any]]:
        return self.blackboard.list_runs(status=status)
