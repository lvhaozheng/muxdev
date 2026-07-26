"""Versioned activity, review, and Conversation contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CaptureGrade(StrEnum):
    RECORDED = "recorded"
    OBSERVED = "observed"
    VERIFIED = "verified"


class ReviewState(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    ROLLED_BACK = "rolled_back"
    ANSWERED = "answered"
    SUPERSEDED = "superseded"


class ActivityActor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["developer", "agent", "runtime", "system"]
    id: str


class ActivitySource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str | None = None
    assignment_id: str | None = None
    session_id: str | None = None
    generation: int | None = Field(default=None, ge=1)


class ActivityEventV2(BaseModel):
    """Canonical public form for a hash-chained Conversation activity."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    conversation_id: str
    sequence: int = Field(ge=1)
    schema_version: Literal[2] = 2
    type: str
    actor: ActivityActor
    source: ActivitySource = Field(default_factory=ActivitySource)
    occurred_at: str
    capture_grade: CaptureGrade = CaptureGrade.RECORDED
    correlation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    previous_hash: str
    event_hash: str


class FileChangeView(BaseModel):
    change_id: str
    path: str
    kind: Literal["add", "modify", "delete", "rename"]
    before_hash: str | None = None
    after_hash: str | None = None
    patch: str = ""
    additions: int = Field(default=0, ge=0)
    deletions: int = Field(default=0, ge=0)
    capture_grade: CaptureGrade
    created_at: str


class ChangeSetViewV1(BaseModel):
    schema_version: Literal["muxdev.changeset-view.v1"] = "muxdev.changeset-view.v1"
    conversation_id: str
    run_id: str
    files: list[FileChangeView] = Field(default_factory=list)
    additions: int = Field(default=0, ge=0)
    deletions: int = Field(default=0, ge=0)
    verified: bool = False


class VerificationAttemptView(BaseModel):
    attempt_id: str
    run_id: str
    command: list[str]
    status: Literal["passed", "failed", "skipped", "unavailable"]
    freshness: Literal["current", "stale", "superseded"]
    exit_code: int | None = None
    duration_ms: int | None = None
    summary: str = ""
    evidence_ref: str | None = None
    created_at: str


class ReviewReference(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)


class ReviewRecordV1(BaseModel):
    schema_version: Literal["muxdev.review-record.v1"] = "muxdev.review-record.v1"
    review_id: str
    conversation_id: str
    run_id: str | None = None
    assignment_id: str | None = None
    session_id: str | None = None
    kind: Literal["interaction", "decision", "change_request"]
    status: str
    prompt: str
    options: list[dict[str, Any]] = Field(default_factory=list)
    response: str | None = None
    references: list[ReviewReference] = Field(default_factory=list)
    actor: ActivityActor
    created_at: str
    resolved_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversationMemoryCheckpointV1(BaseModel):
    schema_version: Literal["muxdev.conversation-memory.v1"] = (
        "muxdev.conversation-memory.v1"
    )
    checkpoint_id: str
    conversation_id: str
    version: int = Field(ge=1)
    kind: Literal["automatic", "correction", "consolidated"]
    through_sequence: int = Field(ge=0)
    source_hash: str
    parent_checkpoint_id: str | None = None
    content: dict[str, Any]
    created_by: str
    created_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReviewViewV1(BaseModel):
    schema_version: Literal["muxdev.review-view.v1"] = "muxdev.review-view.v1"
    conversation_id: str
    run_id: str
    outcome: str
    review_state: ReviewState
    changes: ChangeSetViewV1
    verification_attempts: list[VerificationAttemptView] = Field(default_factory=list)
    candidate_id: str | None = None
    available_actions: list[str] = Field(default_factory=list)
    records: list[ReviewRecordV1] = Field(default_factory=list)


class ConversationSnapshotV1(BaseModel):
    schema_version: Literal["muxdev.conversation-snapshot.v1"] = (
        "muxdev.conversation-snapshot.v1"
    )
    conversation: dict[str, Any]
    attention: str
    attention_detail: dict[str, Any] | None = None
    active_turn: dict[str, Any] | None = None
    participants: list[dict[str, Any]] = Field(default_factory=list)
    sessions: list[dict[str, Any]] = Field(default_factory=list)
    assignments: list[dict[str, Any]] = Field(default_factory=list)
    orchestration_plans: list[dict[str, Any]] = Field(default_factory=list)
    interactions: list[dict[str, Any]] = Field(default_factory=list)
    timeline: list[ActivityEventV2] = Field(default_factory=list)
    tool_summaries: dict[str, Any] = Field(default_factory=dict)
    next_actions: list[str] = Field(default_factory=list)
    last_sequence: int = Field(default=0, ge=0)


__all__ = [
    "ActivityActor",
    "ActivityEventV2",
    "ActivitySource",
    "CaptureGrade",
    "ChangeSetViewV1",
    "ConversationMemoryCheckpointV1",
    "FileChangeView",
    "ReviewReference",
    "ReviewRecordV1",
    "ReviewState",
    "ReviewViewV1",
    "ConversationSnapshotV1",
    "VerificationAttemptView",
]
