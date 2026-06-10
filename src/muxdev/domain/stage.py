"""Stage execution contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .evidence import ArtifactDescriptor, EvidenceBundle
from .provider_actions import ProviderActionRequest


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


@dataclass(frozen=True)
class StageExecutionResult:
    stage_id: str
    provider: str
    status: str
    content: str
    summary: str
    evidence: EvidenceBundle | None = None
    provider_actions: tuple[ProviderActionRequest, ...] = ()
    usage: UsageRecord = field(default_factory=lambda: UsageRecord(provider="unknown"))
    artifacts: tuple[ArtifactDescriptor, ...] = ()
