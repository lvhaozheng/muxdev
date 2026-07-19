"""Single typed Provider execution boundary used by every scheduler."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..domain import StageExecutionInput, StageExecutionResult
from ..providers.adapters import ProviderAdapter


@dataclass(frozen=True)
class StageExecutor:
    """Build the canonical stage input and invoke one Provider adapter."""

    adapter: ProviderAdapter

    def execute(
        self,
        *,
        run_id: str,
        stage_id: str,
        role: str | None,
        provider: str,
        task: str,
        worktree: Path,
        skills: list[dict[str, object]],
        session_dir: Path | None,
        attempt: int,
    ) -> StageExecutionResult:
        execution_input = StageExecutionInput(
            run_id=run_id,
            stage_id=stage_id,
            role=role,
            task=task,
            worktree=worktree,
            context={},
            capabilities={},
            provider=provider,
            policy={},
            skills=tuple(skills),
            session_dir=session_dir,
            attempt=attempt,
        )
        return self.adapter.execute(execution_input)
