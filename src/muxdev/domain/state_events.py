"""Versioned operational state events and the pure lifecycle reducer."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4


RUN_CREATED = "run.created"
RUN_TRANSITIONED = "run.transitioned"
STAGE_TRANSITIONED = "stage.transitioned"
APPROVAL_REQUESTED = "approval.requested"
APPROVAL_DECIDED = "approval.decided"
PROVIDER_ACTION_REQUESTED = "provider_action.requested"
PROVIDER_ACTION_RESPONDED = "provider_action.responded"
PROVIDER_ACTION_TRANSITIONED = "provider_action.transitioned"
WORKER_FAILED = "worker.failed"
LEGACY_RUN_IMPORTED = "legacy_run.imported"

SUPPORTED_EVENT_VERSIONS = {
    RUN_CREATED: 1,
    RUN_TRANSITIONED: 1,
    STAGE_TRANSITIONED: 1,
    APPROVAL_REQUESTED: 1,
    APPROVAL_DECIDED: 1,
    PROVIDER_ACTION_REQUESTED: 1,
    PROVIDER_ACTION_RESPONDED: 1,
    PROVIDER_ACTION_TRANSITIONED: 1,
    WORKER_FAILED: 1,
    LEGACY_RUN_IMPORTED: 1,
}


class InvalidStateTransition(ValueError):
    """The event is known but illegal for the current aggregate state."""


class UnknownStateEvent(ValueError):
    """The event type or version is not supported."""


@dataclass(frozen=True)
class StateEventEnvelope:
    event_id: str
    run_id: str
    sequence: int
    event_type: str
    event_version: int
    idempotency_key: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    causation_id: str | None = None
    correlation_id: str | None = None
    prev_hash: str | None = None
    event_hash: str = ""

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        sequence: int,
        event_type: str,
        idempotency_key: str,
        payload: Mapping[str, Any] | None = None,
        event_version: int = 1,
        causation_id: str | None = None,
        correlation_id: str | None = None,
        prev_hash: str | None = None,
        event_id: str | None = None,
        created_at: str | None = None,
    ) -> "StateEventEnvelope":
        values = {
            "event_id": event_id or f"sev_{uuid4().hex}",
            "run_id": run_id,
            "sequence": int(sequence),
            "event_type": event_type,
            "event_version": int(event_version),
            "idempotency_key": idempotency_key,
            "causation_id": causation_id,
            "correlation_id": correlation_id,
            "payload": dict(payload or {}),
            "created_at": created_at or datetime.now(timezone.utc).isoformat(),
            "prev_hash": prev_hash,
        }
        return cls(**values, event_hash=_event_hash(values))

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "StateEventEnvelope":
        payload = json.loads(str(row.get("payload_json") or "{}"))
        return cls(
            event_id=str(row["event_id"]), run_id=str(row["run_id"]), sequence=int(row["sequence"]),
            event_type=str(row["event_type"]), event_version=int(row["event_version"]),
            idempotency_key=str(row["idempotency_key"]),
            causation_id=str(row["causation_id"]) if row.get("causation_id") else None,
            correlation_id=str(row["correlation_id"]) if row.get("correlation_id") else None,
            payload=payload if isinstance(payload, dict) else {}, created_at=str(row["created_at"]),
            prev_hash=str(row["prev_hash"]) if row.get("prev_hash") else None,
            event_hash=str(row["event_hash"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id, "run_id": self.run_id, "sequence": self.sequence,
            "event_type": self.event_type, "event_version": self.event_version,
            "idempotency_key": self.idempotency_key, "causation_id": self.causation_id,
            "correlation_id": self.correlation_id, "payload": dict(self.payload),
            "created_at": self.created_at, "prev_hash": self.prev_hash, "event_hash": self.event_hash,
        }


def initial_run_state(run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id, "exists": False, "status": None, "history_complete": True,
        "stages": {}, "approvals": {}, "provider_actions": {}, "last_sequence": 0,
    }


def reduce_run_state(state: Mapping[str, Any] | None, event: StateEventEnvelope) -> dict[str, Any]:
    """Reduce one event without accessing I/O or mutable process state."""
    supported = SUPPORTED_EVENT_VERSIONS.get(event.event_type)
    if supported is None or event.event_version != supported:
        raise UnknownStateEvent(f"unsupported state event: {event.event_type}@{event.event_version}")
    current = _copy_state(state or initial_run_state(event.run_id))
    if current["run_id"] != event.run_id:
        raise InvalidStateTransition("event run_id does not match aggregate")
    if event.sequence != int(current.get("last_sequence") or 0) + 1:
        raise InvalidStateTransition("event sequence is not contiguous")
    payload = dict(event.payload)
    if event.event_type == RUN_CREATED:
        if current["exists"]:
            raise InvalidStateTransition("run already exists")
        current.update(exists=True, status="created", history_complete=True)
        current["run"] = payload
    elif event.event_type == LEGACY_RUN_IMPORTED:
        if current["exists"]:
            raise InvalidStateTransition("legacy run already imported")
        snapshot = dict(payload.get("snapshot") or {})
        current.update(
            exists=True, status=str(snapshot.get("status") or "created"), history_complete=False,
            stages={str(x["stage_id"]): dict(x) for x in snapshot.get("stages", []) if x.get("stage_id")},
            approvals={str(x["approval_id"]): dict(x) for x in snapshot.get("approvals", []) if x.get("approval_id")},
            provider_actions={str(x["action_id"]): dict(x) for x in snapshot.get("provider_actions", []) if x.get("action_id")},
        )
        current["run"] = dict(snapshot.get("run") or {})
    elif not current["exists"]:
        raise InvalidStateTransition("run must exist before lifecycle events")
    elif event.event_type == RUN_TRANSITIONED:
        target = str(payload.get("to_status") or "")
        _validate_run_transition(str(current.get("status") or ""), target, payload.get("recovery_reason"))
        current["status"] = target
    elif event.event_type == STAGE_TRANSITIONED:
        _reduce_stage(current, payload)
    elif event.event_type == APPROVAL_REQUESTED:
        approval_id = _required(payload, "approval_id")
        if approval_id in current["approvals"]:
            raise InvalidStateTransition(f"approval already exists: {approval_id}")
        current["approvals"][approval_id] = {**payload, "status": "pending"}
    elif event.event_type == APPROVAL_DECIDED:
        approval_id = _required(payload, "approval_id")
        approval = current["approvals"].get(approval_id)
        if approval is None or str(approval.get("status")) != "pending":
            raise InvalidStateTransition(f"approval cannot be decided: {approval_id}")
        approval["status"] = _required(payload, "status")
    elif event.event_type == PROVIDER_ACTION_REQUESTED:
        action_id = _required(payload, "action_id")
        if action_id in current["provider_actions"]:
            raise InvalidStateTransition(f"provider action already exists: {action_id}")
        current["provider_actions"][action_id] = {**payload, "status": "pending"}
    elif event.event_type in {PROVIDER_ACTION_RESPONDED, PROVIDER_ACTION_TRANSITIONED}:
        action_id = _required(payload, "action_id")
        action = current["provider_actions"].get(action_id)
        if action is None:
            raise InvalidStateTransition(f"provider action does not exist: {action_id}")
        action["status"] = _required(payload, "status")
        if event.event_type == PROVIDER_ACTION_RESPONDED:
            action["response"] = payload.get("response")
    elif event.event_type == WORKER_FAILED:
        if str(current.get("status")) == "aborted":
            raise InvalidStateTransition("aborted run cannot be changed by worker failure")
        current["status"] = "blocked"
    current["last_sequence"] = event.sequence
    return current


def verify_event_hash(event: StateEventEnvelope) -> bool:
    values = event.to_dict()
    expected = values.pop("event_hash")
    return expected == _event_hash(values)


def _reduce_stage(state: dict[str, Any], payload: dict[str, Any]) -> None:
    stage_id = _required(payload, "stage_id")
    target = _required(payload, "status")
    stages: dict[str, dict[str, Any]] = state["stages"]
    previous = stages.get(stage_id)
    previous_status = str(previous.get("status")) if previous else None
    attempt = int(payload.get("attempt") or (previous.get("attempt", 0) if previous else 0))
    allowed = {
        None: {"pending", "running", "skipped"}, "pending": {"pending", "running", "skipped", "failed"},
        "running": {"pending", "running", "completed", "failed", "skipped"},
        "failed": {"failed", "pending", "running", "skipped"},
        "completed": {"completed", "pending", "running", "skipped"}, "skipped": {"skipped", "pending", "running"},
    }
    if target not in allowed.get(previous_status, set()):
        raise InvalidStateTransition(f"illegal stage transition: {previous_status} -> {target}")
    retrying = (
        previous_status in {"failed", "completed", "skipped"} and target in {"pending", "running"}
    ) or (previous_status == "running" and target == "pending")
    if retrying:
        if not payload.get("retry_reason"):
            raise InvalidStateTransition("reopening a terminal stage requires retry_reason")
        expected_attempt = int(previous.get("attempt") or 0) + 1 if previous else 1
        if attempt != expected_attempt:
            raise InvalidStateTransition("stage retry must increment attempt")
    stages[stage_id] = {**(previous or {}), **payload, "stage_id": stage_id, "status": target, "attempt": attempt}


def _validate_run_transition(current: str, target: str, recovery_reason: object) -> None:
    known = {"created", "routing", "running", "reviewing", "attesting", "awaiting_approval", "awaiting_provider_action", "awaiting_feedback", "paused_budget", "blocked", "completed", "aborted"}
    if target not in known:
        raise InvalidStateTransition(f"unknown run status: {target}")
    if current == "aborted" and target != "aborted":
        raise InvalidStateTransition("aborted run cannot be reopened")
    if current in {"blocked", "completed"} and target not in {current, "aborted"} and not recovery_reason:
        raise InvalidStateTransition(f"reopening {current} run requires recovery_reason")
    if current == "created" and target not in {"created", "routing", "running", "blocked", "aborted"}:
        raise InvalidStateTransition(f"illegal run transition: {current} -> {target}")


def _copy_state(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **dict(state),
        "stages": {str(k): dict(v) for k, v in dict(state.get("stages") or {}).items()},
        "approvals": {str(k): dict(v) for k, v in dict(state.get("approvals") or {}).items()},
        "provider_actions": {str(k): dict(v) for k, v in dict(state.get("provider_actions") or {}).items()},
    }


def _required(payload: Mapping[str, Any], key: str) -> str:
    value = str(payload.get(key) or "")
    if not value:
        raise InvalidStateTransition(f"event payload requires {key}")
    return value


def _event_hash(values: Mapping[str, Any]) -> str:
    encoded = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()
