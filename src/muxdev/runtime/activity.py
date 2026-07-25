"""Single validated entry point for Conversation activity publication."""

from __future__ import annotations

from typing import Any, Mapping

from ..models import ActivityEventV2, CaptureGrade
from ..storage import ControlStore


class ActivityPublisher:
    def __init__(self, store: ControlStore) -> None:
        self.store = store

    def publish(
        self,
        conversation_id: str,
        event_type: str,
        payload: Mapping[str, object],
        *,
        actor_id: str,
        actor_kind: str | None = None,
        run_id: str | None = None,
        assignment_id: str | None = None,
        session_id: str | None = None,
        generation: int | None = None,
        correlation_id: str | None = None,
        capture_grade: CaptureGrade | str = CaptureGrade.RECORDED,
        event_id: str | None = None,
    ) -> ActivityEventV2:
        event_key = self.store.append_conversation_event(
            conversation_id,
            event_type,
            payload,
            actor=actor_id,
            actor_kind=actor_kind,
            run_id=run_id,
            assignment_id=assignment_id,
            session_id=session_id,
            generation=generation,
            correlation_id=correlation_id,
            capture_grade=str(capture_grade),
            event_id=event_id,
        )
        row = self.store.connection.execute(
            "SELECT sequence FROM conversation_events WHERE event_id = ?",
            (event_key,),
        ).fetchone()
        if not row:
            raise RuntimeError("activity event was not persisted")
        event = self.store.conversation_activity(
            conversation_id, after=max(0, int(row[0]) - 1), limit=1
        )[0]
        return ActivityEventV2.model_validate(event)


def activity_summary(event: Mapping[str, Any]) -> dict[str, Any]:
    """Return a terminal-safe summary suitable for Timeline projection."""
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    allowed = {
        key: payload[key]
        for key in (
            "summary",
            "status",
            "path",
            "kind",
            "additions",
            "deletions",
            "assignment_id",
            "session_id",
            "run_id",
        )
        if key in payload
    }
    return {"type": event.get("type"), "payload": allowed}


__all__ = ["ActivityPublisher", "activity_summary"]
