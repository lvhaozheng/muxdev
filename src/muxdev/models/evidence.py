"""Evidence v2 domain models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from . import utc_now


EvidenceLayer = Literal["core", "approval", "evaluation", "learning"]
EvidenceKind = Literal[
    "task",
    "stage",
    "change",
    "test",
    "review",
    "security",
    "runtime",
    "approval",
    "artifact",
    "learning",
    "policy",
]
EvidenceStatus = Literal["observed", "passed", "failed", "missing", "approved", "rejected", "blocked"]
EvidenceStrength = Literal["A", "B", "C", "D", "E", "X"]
EvaluationLabel = Literal["ready", "reviewable", "risky", "blocked"]
StandardSeverity = Literal["P0", "P1", "P2", "P3"]
RiskLevel = Literal["R0", "R1", "R2", "R3"]
EvidenceLevel = Literal["E0", "E1", "E2", "E3"]


class ArtifactRef(BaseModel):
    """Content-addressed artifact reference used by evidence events."""

    path: str
    sha256: str | None = None
    media_type: str | None = None
    producer: str | None = None


class EvidenceEvent(BaseModel):
    """One append-only evidence event.

    Evidence events are the fact source. Manifests and evaluations are derived
    views over this stream and can be regenerated from it.
    """

    id: str
    run_id: str
    stage_id: str | None = None
    layer: EvidenceLayer = "core"
    kind: EvidenceKind
    claim: str
    status: EvidenceStatus = "observed"
    strength: EvidenceStrength = "C"
    standard_id: str | None = None
    severity: StandardSeverity | None = None
    risk_level: RiskLevel | None = None
    evidence_level: EvidenceLevel | None = None
    subject_hash: str | None = None
    prev_hash: str | None = None
    event_hash: str | None = None
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    metrics: dict[str, object] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    source: str = "muxdev"
    created_at: str = Field(default_factory=utc_now)


class EvidenceManifest(BaseModel):
    """Lightweight run evidence manifest."""

    contract_version: str = "muxdev.evidence.v2"
    run_id: str
    event_count: int
    artifact_count: int
    layers: dict[str, int] = Field(default_factory=dict)
    kinds: dict[str, int] = Field(default_factory=dict)
    required_matrix: dict[str, bool] = Field(default_factory=dict)
    missing_required: list[str] = Field(default_factory=list)
    head_hash: str | None = None
    events_path: str
    manifest_path: str
    created_at: str = Field(default_factory=utc_now)


class EvidenceEvaluation(BaseModel):
    """Gate-first delivery evaluation derived from Evidence v2 events."""

    contract_version: str = "muxdev.evidence_evaluation.v2"
    run_id: str
    label: EvaluationLabel
    confidence: float
    gates: dict[str, str] = Field(default_factory=dict)
    components: dict[str, float] = Field(default_factory=dict)
    standard_scores: dict[str, object] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    next_actions: list[dict[str, str]] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)
