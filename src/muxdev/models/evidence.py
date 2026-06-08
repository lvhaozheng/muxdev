"""Evidence scorecard domain models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from . import utc_now


EvidenceKind = Literal[
    "requirement_evidence",
    "change_evidence",
    "test_evidence",
    "review_evidence",
    "security_evidence",
    "runtime_evidence",
    "human_evidence",
]
EvidenceStrength = Literal["A", "B", "C", "D", "E", "X"]
ScorecardLabel = Literal["ready", "reviewable", "risky", "blocked"]


class ArtifactRef(BaseModel):
    path: str
    sha256: str | None = None


class EvidenceItem(BaseModel):
    """One normalized claim-to-artifact evidence item."""

    id: str
    kind: EvidenceKind
    strength: EvidenceStrength
    claim: str
    supports: list[str] = Field(default_factory=list)
    command: str | None = None
    exit_code: int | None = None
    summary: str = ""
    relevance: float | None = None
    confidence: float | None = None
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    human_summary: str = ""
    created_at: str = Field(default_factory=utc_now)


class CoverageRow(BaseModel):
    acceptance_id: str
    criterion: str
    implementation: str
    tests: str
    review: str
    evidence_refs: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class EvidenceScorecard(BaseModel):
    """Default human-facing delivery confidence summary for a run."""

    contract_version: str = "muxdev.evidence_scorecard.v1"
    run_id: str
    score: int
    label: ScorecardLabel
    recommendation: str
    components: dict[str, int]
    risk_penalty: int
    top_reasons: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    evidence_counts: dict[str, int] = Field(default_factory=dict)
    coverage_summary: dict[str, int] = Field(default_factory=dict)
    next_actions: list[dict[str, str]] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)
