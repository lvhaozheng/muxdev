"""Conversation lifecycle models for long-running trusted delivery work."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ConversationStatus(StrEnum):
    CLARIFYING = "clarifying"
    WORKING = "working"
    NEEDS_USER = "needs_user"
    CANDIDATE_READY = "candidate_ready"
    VERIFYING = "verifying"
    RECOVERING = "recovering"
    AWAITING_ACCEPTANCE = "awaiting_acceptance"
    DELIVERED = "delivered"
    CLOSED = "closed"


class ConversationIntent(StrEnum):
    DISCUSS = "discuss"
    ANSWER = "answer"
    CHANGE = "change"
    VERIFY = "verify"


class ConversationActor(StrEnum):
    DEVELOPER = "developer"
    IMPLEMENTER = "implementer"
    REVIEWER = "reviewer"
    SUPERVISOR = "supervisor"


class CandidateStatus(StrEnum):
    VERIFYING = "verifying"
    FAILED = "failed"
    VERIFIED = "verified"
    INVALIDATED = "invalidated"
    ACCEPTED = "accepted"


class DeliveryContract(BaseModel):
    contract_id: str
    conversation_id: str
    version: int = Field(ge=1)
    status: str
    goal: str
    acceptance_criteria: tuple[str, ...] = ()
    allowed_scope: tuple[str, ...] = ()
    workflow: str
    profile: str
    provider: str
    max_cost_usd: float = Field(gt=0)
    policy: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    superseded_at: str | None = None


class DeliveryCandidate(BaseModel):
    candidate_id: str
    conversation_id: str
    contract_id: str
    run_id: str
    version: int = Field(ge=1)
    status: CandidateStatus
    subject_digest: str
    changeset_digest: str
    message_sequence: int = Field(ge=0)
    policy_hash: str
    parent_candidate_id: str | None = None
    evidence_path: str | None = None
    created_at: str
    updated_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)

