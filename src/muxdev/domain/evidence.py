"""Evidence and artifact contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class ArtifactDescriptor:
    name: str
    path: Path
    kind: str
    stage_id: str | None = None
    digest: str | None = None


@dataclass(frozen=True)
class EvidenceBundle:
    claims: tuple[Mapping[str, object], ...] = ()
    evidence: tuple[Mapping[str, object], ...] = ()
    tests: tuple[Mapping[str, object], ...] = ()
    missing_evidence: tuple[str, ...] = ()
    risks: tuple[Mapping[str, object], ...] = ()
