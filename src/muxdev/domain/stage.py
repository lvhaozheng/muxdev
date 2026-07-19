"""Stage execution contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .evidence import ArtifactDescriptor, EvidenceBundle


@dataclass(frozen=True)
class UsageRecord:
    provider: str
    tokens: int = 0
    cost_usd: float = 0.0


@dataclass(frozen=True)
class StageExecutionInput:
    run_id: str
    stage_id: str
    role: str | None
    task: str
    worktree: Path
    context: Mapping[str, object]
    capabilities: Mapping[str, object]
    provider: str
    policy: Mapping[str, object]
    skills: tuple[Mapping[str, object], ...] = ()
    session_dir: Path | None = None
    attempt: int = 1

    @classmethod
    def for_provider(
        cls,
        *,
        stage_id: str,
        task: str,
        worktree: Path,
        provider: str,
        run_id: str = "unbound",
    ) -> "StageExecutionInput":
        """Build the minimal input used by direct adapter checks."""
        return cls(
            run_id=run_id,
            stage_id=stage_id,
            role=None,
            task=task,
            worktree=worktree,
            context={},
            capabilities={},
            provider=provider,
            policy={},
        )


@dataclass(frozen=True)
class StageExecutionResult:
    artifact_name: str
    content: str
    summary: str
    stage_id: str = ""
    provider: str = ""
    status: str = "completed"
    tokens: int = 0
    cost_usd: float = 0.0
    returncode: int = 0
    evidence: EvidenceBundle | None = None
    provider_actions: list[dict[str, Any]] = field(default_factory=list)
    harness_events: tuple[Any, ...] = ()
    transcript_path: str | None = None
    chunks_path: str | None = None
    stdout_hash: str | None = None
    stderr_hash: str | None = None
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    artifacts: tuple[ArtifactDescriptor, ...] = ()

    @property
    def usage(self) -> UsageRecord:
        return UsageRecord(provider=self.provider or "unknown", tokens=self.tokens, cost_usd=self.cost_usd)
