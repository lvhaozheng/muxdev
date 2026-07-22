"""Public runtime and workflow contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class RunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    ABORTED = "aborted"


class PlanDecision(BaseModel):
    id: str
    decision: str
    rationale: str
    alternatives: list[str] = Field(default_factory=list)


class AcceptanceCriterion(BaseModel):
    id: str
    text: str
    verification: str


class PlanResult(BaseModel):
    summary: str
    decisions: list[PlanDecision] = Field(default_factory=list)
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class ChangeResult(BaseModel):
    summary: str
    affected_paths: list[str] = Field(default_factory=list)
    suggested_verification: list[str] = Field(default_factory=list)
    residual_risks: list[str] = Field(default_factory=list)


class ReviewFinding(BaseModel):
    type: str
    category: str = "correctness"
    file: str | None = None
    line: int | None = None
    severity: Literal["low", "medium", "high"] = "medium"
    message: str
    remediation: str


class StandardAssessment(BaseModel):
    standard_id: str = Field(min_length=1, max_length=80)
    status: Literal["satisfied", "failed"]
    note: str = Field(default="", max_length=1000)


class ReviewResult(BaseModel):
    target_subject: str
    findings: list[ReviewFinding] = Field(default_factory=list)
    residual_risk: str = ""
    standard_assessments: list[StandardAssessment] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_standard_assessments(self) -> "ReviewResult":
        identifiers = [item.standard_id for item in self.standard_assessments]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("standard assessment ids must be unique")
        return self


class TestCheck(BaseModel):
    id: str
    criteria_ids: list[str] = Field(default_factory=list)
    argv: list[str] = Field(default_factory=list)
    status: Literal["passed", "failed", "skipped", "unavailable"]
    exit_code: int | None = None
    summary: str
    skipped_reason: str | None = None

    @model_validator(mode="after")
    def validate_execution(self) -> "TestCheck":
        if self.status in {"passed", "failed"} and self.exit_code is None:
            raise ValueError("executed checks require an exit_code")
        if self.status == "passed" and self.exit_code != 0:
            raise ValueError("passed checks require exit_code 0")
        if self.status == "failed" and self.exit_code == 0:
            raise ValueError("failed checks require a non-zero exit_code")
        if self.status in {"skipped", "unavailable"} and not self.skipped_reason:
            raise ValueError("skipped checks require a reason")
        return self


class VerificationSuggestion(BaseModel):
    """Untrusted provider advice; this object is never executed by the runtime."""

    id: str
    criteria_ids: list[str] = Field(default_factory=list)
    argv: list[str] = Field(default_factory=list)
    summary: str
    rationale: str | None = None


class TestResult(BaseModel):
    checks: list[VerificationSuggestion]
    criteria_covered: list[str] = Field(default_factory=list)
    residual_risks: list[str] = Field(default_factory=list)

    @property
    def summary(self) -> str:
        return "; ".join(item.summary for item in self.checks) or "No checks reported"


class VerificationCommand(BaseModel):
    """A runtime-owned, argv-only command frozen into the run policy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    argv: list[str] = Field(min_length=1)
    cwd: str = "."
    timeout_seconds: int = Field(default=300, ge=1, le=3600)
    criteria_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_cwd(self) -> "VerificationCommand":
        from pathlib import PurePath

        path = PurePath(self.cwd)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("verification command cwd must stay inside the worktree")
        return self


class ExecutedCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    argv: list[str]
    cwd: str
    cwd_digest: str
    exit_code: int
    duration_ms: int
    stdout_digest: str
    stderr_digest: str
    stdout_summary: str = ""
    stderr_summary: str = ""
    reproducible: bool = True


class RunPolicySnapshot(BaseModel):
    """Immutable policy inputs captured before provider execution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: Literal["muxdev.run-policy.v1"] = "muxdev.run-policy.v1"
    run_id: str
    workflow: str
    profile: str
    evidence_policy_hash: str
    config_hash: str
    provider_request: str
    provider_route: dict[str, Any] = Field(default_factory=dict)
    workflow_definition: dict[str, Any]
    delivery_standard: dict[str, Any] = Field(default_factory=dict)
    workspace_manifest: dict[str, Any]
    stage_capabilities: dict[str, dict[str, Any]] = Field(default_factory=dict)
    stage_skills: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    provider_capabilities: dict[str, dict[str, Any]] = Field(default_factory=dict)
    provider_definitions: dict[str, dict[str, Any]] = Field(default_factory=dict)
    skill_lock_digest: str = ""
    secret_names: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


class WorkflowStage(BaseModel):
    id: str
    role: str | None = None
    type: Literal["agent", "human_gate"] = "agent"
    deps: list[str] = Field(default_factory=list)
    read_only: bool = False
    allow_write: bool = False
    allow_shell: bool = False
    allow_network: bool = False
    allowed_secrets: list[str] = Field(default_factory=list)
    mcp_tools: list[str] = Field(default_factory=list)
    verification_commands: list[VerificationCommand] = Field(default_factory=list)
    output_schema: str | None = None
    when: str | None = None
    approval_type: str | None = None
    approval_reason: str | None = None
    default_skills: list[str] = Field(default_factory=list)


class WorkflowDefinition(BaseModel):
    name: Literal["change", "design", "review", "test"]
    stages: list[WorkflowStage]


from .evidence import (  # noqa: E402
    ArtifactEvidence,
    CheckEvidence,
    EvidencePolicy,
    EvidenceReport,
    EvidenceRequirement,
    EvidenceScorecard,
    FailureDiagnosis,
    GateDecision,
    InteractionEvidence,
    RecoveryAction,
    RecoveryAttempt,
    RecoverySummary,
    ReviewEvidence,
    RuntimeEvidence,
)
from .conversation import (  # noqa: E402
    CandidateStatus,
    ConversationActor,
    ConversationIntent,
    ConversationStatus,
    DeliveryCandidate,
    DeliveryContract,
)
from .collaboration import (  # noqa: E402
    AgentDefinition,
    AgentSessionStatus,
    AssignmentStatus,
    CliAdapterDefinition,
    ConversationMode,
    DeliveryTriple,
    OrchestrationNodeV1,
    OrchestrationPlanStatus,
    OrchestrationPlanV1,
)


__all__ = [
    "AcceptanceCriterion",
    "AgentDefinition",
    "AgentSessionStatus",
    "ArtifactEvidence",
    "AssignmentStatus",
    "CandidateStatus",
    "ChangeResult",
    "CliAdapterDefinition",
    "CheckEvidence",
    "ConversationActor",
    "ConversationIntent",
    "ConversationMode",
    "ConversationStatus",
    "DeliveryCandidate",
    "DeliveryContract",
    "DeliveryTriple",
    "EvidencePolicy",
    "EvidenceReport",
    "EvidenceRequirement",
    "EvidenceScorecard",
    "FailureDiagnosis",
    "GateDecision",
    "InteractionEvidence",
    "RecoveryAction",
    "RecoveryAttempt",
    "RecoverySummary",
    "PlanDecision",
    "PlanResult",
    "OrchestrationNodeV1",
    "OrchestrationPlanStatus",
    "OrchestrationPlanV1",
    "ReviewEvidence",
    "ReviewFinding",
    "ReviewResult",
    "RunPolicySnapshot",
    "RunStatus",
    "RuntimeEvidence",
    "StandardAssessment",
    "TestCheck",
    "TestResult",
    "ExecutedCheck",
    "VerificationCommand",
    "VerificationSuggestion",
    "WorkflowDefinition",
    "WorkflowStage",
    "utc_now",
]
