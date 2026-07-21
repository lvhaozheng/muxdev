"""Application-facing task lifecycle without concrete adapter dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class RunEnginePort(Protocol):
    def run(self, task: str, **options: object) -> Any: ...

    def resume(self, run_id: str, *, action: str = "auto") -> Any: ...

    def cancel(self, run_id: str) -> None: ...


class TaskStorePort(Protocol):
    def get_run(self, run_id: str) -> dict[str, Any] | None: ...

    def list_runs(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]: ...

    def stages(self, run_id: str) -> list[dict[str, Any]]: ...

    def interactions(self, run_id: str, *, pending_only: bool = False) -> list[dict[str, Any]]: ...

    def respond(self, interaction_id: str, *, status: str, response: str | None = None) -> dict[str, Any]: ...


@dataclass(frozen=True)
class TaskService:
    engine: RunEnginePort
    store: TaskStorePort

    def create(self, task: str, **options: object) -> Any:
        return self.engine.run(task, **options)

    def get(self, run_id: str) -> dict[str, Any]:
        run = self.store.get_run(run_id)
        if not run:
            raise FileNotFoundError(run_id)
        return {"run": run, "stages": self.store.stages(run_id), "interactions": self.store.interactions(run_id)}

    def list(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return self.store.list_runs(status=status, limit=limit)

    def resume(self, run_id: str, *, action: str = "auto") -> Any:
        return self.engine.resume(run_id, action=action)

    def cancel(self, run_id: str) -> None:
        self.engine.cancel(run_id)

    def respond(self, interaction_id: str, *, status: str, response: str | None = None) -> dict[str, Any]:
        return self.store.respond(interaction_id, status=status, response=response)
