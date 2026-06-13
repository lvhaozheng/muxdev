"""Shared domain models for muxdev runtime, storage, and UI layers.

These models are intentionally small and serializable. They form the stable
protocol between CLI commands, workflow execution, SQLite blackboard rows,
trace events, reports, and JSON output consumed by automation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp used across persisted records."""
    return datetime.now(timezone.utc).isoformat()


class RunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_PROVIDER_ACTION = "awaiting_provider_action"
    PAUSED_BUDGET = "paused_budget"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    ABORTED = "aborted"


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SKIPPED = "skipped"
    COMPLETED = "completed"
    FAILED = "failed"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class ProviderActionStatus(StrEnum):
    PENDING = "pending"
    HANDLED = "handled"
    DISMISSED = "dismissed"
    EXPIRED = "expired"


class ProviderActionKind(StrEnum):
    CLI_CONFIRMATION = "cli_confirmation"
    AUTH_REQUIRED = "auth_required"
    RATE_LIMIT = "rate_limit"
    PROVIDER_BLOCKED = "provider_blocked"
    IDLE_TIMEOUT = "idle_timeout"


class PolicyDecision(StrEnum):
    ALLOW = "allow"
    APPROVE = "approve"
    DENY = "deny"


class PlanArtifact(BaseModel):
    summary: str
    steps: list[str] = Field(default_factory=list)


class ReviewBlocker(BaseModel):
    type: str
    file: str | None = None
    line: int | None = None
    severity: Literal["low", "medium", "high"] = "medium"
    suggestion: str


class ReviewResult(BaseModel):
    has_blockers: bool = False
    blockers: list[ReviewBlocker] = Field(default_factory=list)


class TestResult(BaseModel):
    passed: bool
    command: str
    summary: str


class WorkflowStage(BaseModel):
    """One node in a DAG-ready workflow definition."""

    id: str
    role: str | None = None
    type: str = "agent"
    deps: list[str] = Field(default_factory=list)
    read_only: bool = False
    allow_write: bool = False
    allow_shell: bool = False
    checkpoint: bool = False
    output_schema: str | None = None
    when: str | None = None
    max_loops: int | None = None
    prompt: str | None = None
    prompt_template: str | None = None
    default_skills: list[str] = Field(default_factory=list)


class WorkflowDefinition(BaseModel):
    """A named workflow containing ordered or dependency-linked stages."""

    name: str
    max_parallel: int = 1
    stages: list[WorkflowStage]


class TraceEvent(BaseModel):
    """Append-only event shape written to trace.jsonl for replay/debugging."""

    type: str
    time: str = Field(default_factory=utc_now)
    run_id: str | None = None
    stage: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
