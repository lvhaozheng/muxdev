"""Evidence v3 contracts: objective records, deterministic gates, clear scores."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

from . import utc_now


EvidenceKind = Literal["artifact", "check", "review", "interaction", "runtime"]
RequirementStatus = Literal["satisfied", "missing", "failed", "not_applicable"]
GateStatus = Literal["PASS", "BLOCKED", "WAITING_HUMAN"]
RecoveryStatus = Literal["not_needed", "recovering", "recovered", "needs_action", "exhausted"]


def canonical_hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class EvidenceRequirement(BaseModel):
    id: str
    description: str
    required: bool = True
    accepted_kinds: list[EvidenceKind]
    subject_selector: Literal["run", "any"] = "run"
    require_reproducible: bool = False
    require_integrity: bool = True
    require_independent: bool = False


class EvidencePolicy(BaseModel):
    policy_id: str
    version: int = 1
    requirements: list[EvidenceRequirement]
    policy_hash: str = ""

    @model_validator(mode="after")
    def freeze_hash(self) -> "EvidencePolicy":
        identifiers = [item.id for item in self.requirements]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Evidence requirement ids must be unique")
        expected = canonical_hash(
            {
                "policy_id": self.policy_id,
                "version": self.version,
                "requirements": [item.model_dump(mode="json") for item in self.requirements],
            }
        )
        if self.policy_hash and self.policy_hash != expected:
            raise ValueError("Evidence policy hash does not match its content")
        self.policy_hash = expected
        return self


class EvidenceRecord(BaseModel):
    record_id: str
    run_id: str
    stage_id: str | None = None
    requirement_id: str
    kind: EvidenceKind
    subject_digest: str
    producer: str
    created_at: str = Field(default_factory=utc_now)
    schema_version: int = 3
    integrity_valid: bool = True


class ArtifactEvidence(EvidenceRecord):
    kind: Literal["artifact"] = "artifact"
    path: str
    digest: str
    size: int
    media_type: str = "application/octet-stream"


class CheckEvidence(EvidenceRecord):
    kind: Literal["check"] = "check"
    argv: list[str]
    cwd: str = "."
    cwd_digest: str
    exit_code: int
    declared_exit_code: int | None = None
    declared_status: Literal["passed", "failed", "skipped", "unavailable"] | None = None
    duration_ms: int = 0
    stdout_digest: str | None = None
    stderr_digest: str | None = None
    stdout_summary: str = ""
    stderr_summary: str = ""
    summary: str = ""
    reproducible: bool = True

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


class ReviewFinding(BaseModel):
    finding_id: str
    category: str = "correctness"
    severity: Literal["low", "medium", "high"] = "medium"
    message: str
    file: str | None = None
    line: int | None = None
    remediation: str
    resolved: bool = False


class ReviewEvidence(EvidenceRecord):
    kind: Literal["review"] = "review"
    target_digest: str
    reviewer: str
    executor: str | None = None
    independent: bool = False
    findings: list[ReviewFinding] = Field(default_factory=list)
    residual_risk: str = ""


class InteractionEvidence(EvidenceRecord):
    kind: Literal["interaction"] = "interaction"
    interaction_type: Literal["approval", "rejection", "feedback"]
    decision: Literal["approved", "rejected", "provided", "pending"]
    actor: str | None = None


class RuntimeEvidence(EvidenceRecord):
    kind: Literal["runtime"] = "runtime"
    event_type: str
    status: Literal["observed", "passed", "failed", "pending"] = "observed"
    details: dict[str, Any] = Field(default_factory=dict)


AnyEvidenceRecord = Annotated[
    ArtifactEvidence | CheckEvidence | ReviewEvidence | InteractionEvidence | RuntimeEvidence,
    Field(discriminator="kind"),
]


class RequirementEvaluation(BaseModel):
    requirement_id: str
    status: RequirementStatus
    record_ids: list[str] = Field(default_factory=list)
    reason: str
    remediation: str | None = None


class ScoreDimension(BaseModel):
    numerator: int
    denominator: int
    percent: float | None
    failed_requirements: list[str] = Field(default_factory=list)


class EvidenceScorecard(BaseModel):
    completeness: ScoreDimension
    reproducibility: ScoreDimension
    integrity: ScoreDimension
    independence: ScoreDimension
    overall: float | None


class GateBlocker(BaseModel):
    code: str
    requirement_id: str
    reason: str
    record_ids: list[str] = Field(default_factory=list)
    remediation: str


class GateDecision(BaseModel):
    status: GateStatus
    policy_hash: str
    requirements: list[RequirementEvaluation]
    blockers: list[GateBlocker] = Field(default_factory=list)
    scorecard: EvidenceScorecard
    evaluated_at: str = Field(default_factory=utc_now)


class FailureDiagnosis(BaseModel):
    code: str
    kind: Literal[
        "output_contract",
        "test_failure",
        "review_blocker",
        "provider_timeout",
        "provider_failure",
        "environment",
        "independent_reviewer",
        "policy",
        "unknown",
    ] = "unknown"
    stage_id: str | None = None
    summary: str
    details: list[str] = Field(default_factory=list)
    consequences: list[str] = Field(default_factory=list)


class RecoveryAttempt(BaseModel):
    action: Literal["fix-output", "retry", "switch-provider", "repair"]
    stage_id: str | None = None
    provider: str | None = None
    status: Literal["started", "succeeded", "failed", "skipped"]
    failure_kind: str | None = None
    feedback_summary: list[str] = Field(default_factory=list)


class RecoveryAction(BaseModel):
    action: Literal[
        "auto", "fix-output", "retry", "switch-provider", "configure-reviewer", "rerun-lite", "inspect"
    ]
    label: str
    description: str
    command: str
    requires_confirmation: bool = False


class RecoverySummary(BaseModel):
    status: RecoveryStatus = "not_needed"
    max_actions: int = 2
    actions_used: int = 0
    primary_failure: FailureDiagnosis | None = None
    attempts: list[RecoveryAttempt] = Field(default_factory=list)
    next_actions: list[RecoveryAction] = Field(default_factory=list)
    workspace_safe: bool = True
    workspace_message: str = "All recovery work stayed inside the isolated run worktree."


class EvidenceReport(BaseModel):
    contract_version: Literal["muxdev.evidence.v3"] = "muxdev.evidence.v3"
    run_id: str
    subject: dict[str, Any]
    policy: EvidencePolicy
    records: list[AnyEvidenceRecord]
    decision: GateDecision
    integrity: dict[str, Any]
    routing: dict[str, Any] = Field(default_factory=dict)
    reviewer: dict[str, Any] = Field(default_factory=dict)
    harness: dict[str, Any] = Field(default_factory=dict)
    recovery: RecoverySummary | None = None
    generated_at: str = Field(default_factory=utc_now)
