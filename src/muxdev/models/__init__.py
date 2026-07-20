"""Public runtime and workflow contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


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


class ReviewResult(BaseModel):
    target_subject: str
    findings: list[ReviewFinding] = Field(default_factory=list)
    residual_risk: str = ""


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


class TestResult(BaseModel):
    checks: list[TestCheck]
    criteria_covered: list[str] = Field(default_factory=list)
    residual_risks: list[str] = Field(default_factory=list)

    @property
    def summary(self) -> str:
        return "; ".join(item.summary for item in self.checks) or "No checks reported"


class WorkflowStage(BaseModel):
    id: str
    role: str | None = None
    type: Literal["agent", "human_gate"] = "agent"
    deps: list[str] = Field(default_factory=list)
    read_only: bool = False
    allow_write: bool = False
    allow_shell: bool = False
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
    GateDecision,
    InteractionEvidence,
    ReviewEvidence,
    RuntimeEvidence,
)


__all__ = [
    "AcceptanceCriterion",
    "ArtifactEvidence",
    "ChangeResult",
    "CheckEvidence",
    "EvidencePolicy",
    "EvidenceReport",
    "EvidenceRequirement",
    "EvidenceScorecard",
    "GateDecision",
    "InteractionEvidence",
    "PlanDecision",
    "PlanResult",
    "ReviewEvidence",
    "ReviewFinding",
    "ReviewResult",
    "RunStatus",
    "RuntimeEvidence",
    "TestCheck",
    "TestResult",
    "WorkflowDefinition",
    "WorkflowStage",
    "utc_now",
]
