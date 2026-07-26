"""Durable Conversation message outbox and generation-fenced CLI delivery."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..models import AgentSessionStatus, AssignmentStatus
from ..storage import ControlStore
from .agent_session_errors import SessionLifecycleError
from .agent_session_startup import StartupProcessExited, StartupTimeout
from .change_tracking import ChangeTrackingContext, change_monitors


def deliver_runtime_message(
    manager: Any,
    session_id: str,
    data: str,
    *,
    delivery_id: str,
) -> dict[str, Any]:
    """Deliver to one stable generation while holding the lifecycle fence."""
    if len(data.encode("utf-8")) > 262144:
        raise ValueError("runtime terminal message exceeds 256 KiB")
    with manager._lifecycle_lock:
        with ControlStore(manager.workspace) as store:
            record = store.get_agent_session(session_id)
        if not record:
            raise FileNotFoundError(session_id)
        status = str(record.get("status") or "")
        _reject_final_session(status, session_id)
        with manager._lock:
            existing = manager._sessions.get(session_id)
        if status == AgentSessionStatus.STARTING.value:
            return {"status": "queued", "reason": "session_starting"}
        if existing and (
            existing.closing or time.monotonic() < existing.transitioning_until
        ):
            return {"status": "queued", "reason": "session_transitioning"}
        if status == AgentSessionStatus.WAITING_INPUT.value:
            readiness = _recover_runtime_readiness(manager, existing, session_id)
            if readiness:
                return readiness
            with ControlStore(manager.workspace) as store:
                record = store.update_agent_session(
                    session_id,
                    status=AgentSessionStatus.READY.value,
                    metadata={"runtime_waiting": None},
                )
        else:
            record = manager.ensure_live(session_id)
        with manager._lock:
            live = manager._sessions.get(session_id)
        if (
            not live
            or live.closing
            or time.monotonic() < live.transitioning_until
            or not live.backend.snapshot().running
        ):
            return {"status": "queued", "reason": "session_transitioning"}
        manager._send_prompt(
            live,
            data,
            metadata={"runtime": True, "delivery_id": delivery_id},
        )
        if not live.backend.snapshot().running:
            return {
                "status": "uncertain",
                "reason": "session_exited_after_dispatch",
            }
        live.pending_deliveries[delivery_id] = live.sequence
        with ControlStore(manager.workspace) as store:
            current = store.get_agent_session(session_id) or record
        return {
            "status": "dispatched",
            "session_id": session_id,
            "generation": int(current.get("generation") or 0),
            "transcript_sequence": live.sequence,
        }


def _reject_final_session(status: str, session_id: str) -> None:
    if status == AgentSessionStatus.FAILED.value:
        raise SessionLifecycleError(
            "Agent Session 已失败，需要明确重新启动后才能继续。",
            code="session_restart_required",
            remediation="查看失败输出，然后点击“重新启动”。",
            retryable=True,
            session_id=session_id,
        )
    if status == AgentSessionStatus.CLOSED.value:
        raise SessionLifecycleError(
            "Agent Session 已关闭，不能投递消息。",
            code="session_closed",
            remediation="重新打开 Conversation 或显式重新启动 Session。",
            retryable=True,
            session_id=session_id,
        )


def _recover_runtime_readiness(
    manager: Any,
    live: Any,
    session_id: str,
) -> dict[str, str] | None:
    if not live or not live.runtime_recovery_pending:
        return {"status": "queued", "reason": "session_waiting_input"}
    try:
        live.startup.wait(live.backend, 0.25)
    except StartupTimeout:
        return {
            "status": "queued",
            "reason": "session_waiting_for_ready_prompt",
        }
    except StartupProcessExited:
        return {"status": "queued", "reason": "session_transitioning"}
    live.runtime_recovery_pending = False
    return None


def mark_delivery_uncertain(
    manager: Any,
    delivery_id: str,
    *,
    session_id: str,
    reason: str,
) -> None:
    with ControlStore(manager.workspace) as store:
        delivery = store.get_message_delivery(delivery_id)
        if not delivery or str(delivery.get("status") or "") != "dispatched":
            return
        delivery = store.update_message_delivery(
            delivery_id,
            status="uncertain",
            last_error=reason,
            metadata={"uncertain_reason": reason},
        )
        assignment_id = str(delivery.get("assignment_id") or "")
        assignment = store.get_assignment(assignment_id) if assignment_id else None
        run_id = str(delivery.get("run_id") or "")
        waiting_reason = {
            "code": "message_delivery_uncertain",
            "message": "Agent CLI 在确认处理消息前退出，任务已暂停。",
            "remediation": "查看 Terminal 最近输出，确认后重新发送该消息。",
        }
        if assignment and str(assignment.get("status") or "") == "running":
            store.update_assignment(
                assignment_id,
                status="waiting_user",
                metadata={"waiting_reason": waiting_reason},
            )
            if run_id:
                store.update_run(
                    run_id,
                    status="waiting_user",
                    current_stage="assignment",
                )
            store.update_conversation(
                str(delivery["conversation_id"]),
                status="needs_user",
                active_run_id=run_id or None,
            )
        store.append_conversation_event(
            str(delivery["conversation_id"]),
            "message.delivery_uncertain",
            {
                "delivery_id": delivery_id,
                "message_event_id": str(delivery["message_event_id"]),
                "recipient_agent_id": str(delivery["recipient_agent_id"]),
                "reason": reason,
            },
            actor="runtime",
            run_id=run_id or None,
            assignment_id=assignment_id or None,
            session_id=session_id,
            generation=int(delivery.get("target_generation") or 0) or None,
            correlation_id=delivery_id,
        )


class AgentSessionMessageMixin:
    """Message-specific methods mixed into the core Session manager."""

    def send_runtime(self, session_id: str, data: str) -> None:
        if len(data.encode("utf-8")) > 262144:
            raise ValueError("runtime terminal message exceeds 256 KiB")
        live = self._live(session_id)
        self._send_prompt(live, data, metadata={"runtime": True})

    def deliver_runtime(
        self,
        session_id: str,
        data: str,
        *,
        delivery_id: str,
    ) -> dict[str, Any]:
        return deliver_runtime_message(
            self,
            session_id,
            data,
            delivery_id=delivery_id,
        )

    def _mark_delivery_uncertain(
        self,
        delivery_id: str,
        *,
        session_id: str,
        reason: str,
    ) -> None:
        mark_delivery_uncertain(
            self,
            delivery_id,
            session_id=session_id,
            reason=reason,
        )


class CollaborationMessageDeliveryMixin:
    """Application-level durable routing and execution-state projection."""

    def _dispatch_message_deliveries(
        self,
        deliveries: Sequence[Mapping[str, object]],
    ) -> list[dict[str, Any]]:
        return [
            self._dispatch_message_delivery(str(item["delivery_id"]))
            for item in deliveries
        ]

    def dispatch_pending_messages(
        self,
        *,
        limit: int = 100,
        force: bool = False,
    ) -> int:
        pending = self.store.list_message_deliveries(
            statuses=["pending"],
            limit=limit,
        )
        dispatched = 0
        now = datetime.now(UTC)
        for item in pending:
            if not force and not _retry_due(item, now):
                continue
            result = self._dispatch_message_delivery(str(item["delivery_id"]))
            if result["status"] == "dispatched":
                dispatched += 1
        return dispatched

    def _dispatch_message_delivery(self, delivery_id: str) -> dict[str, Any]:
        current = self.store.get_message_delivery(delivery_id)
        if not current:
            raise FileNotFoundError(delivery_id)
        if str(current.get("status") or "") not in {"pending", "dispatching"}:
            return self._message_delivery_response(current)
        claimed = (
            self.store.claim_message_delivery(delivery_id)
            if str(current.get("status") or "") == "pending"
            else current
        )
        if not claimed:
            latest = self.store.get_message_delivery(delivery_id)
            if not latest:
                raise FileNotFoundError(delivery_id)
            return self._message_delivery_response(latest)
        payload = (
            claimed.get("message_payload")
            if isinstance(claimed.get("message_payload"), Mapping)
            else {}
        )
        try:
            receipt = self.sessions.deliver_runtime(
                str(claimed.get("session_id") or ""),
                str(payload.get("content") or ""),
                delivery_id=delivery_id,
            )
        except SessionLifecycleError as exc:
            return self._fail_delivery(claimed, exc.code, str(exc), exc.detail())
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            return self._fail_delivery(
                claimed,
                type(exc).__name__,
                str(exc),
                {"message": str(exc)},
            )
        return self._settle_delivery_receipt(claimed, receipt)

    def _fail_delivery(
        self,
        claimed: Mapping[str, object],
        code: str,
        message: str,
        detail: Mapping[str, object],
    ) -> dict[str, Any]:
        failed = self.store.update_message_delivery(
            str(claimed["delivery_id"]),
            status="failed",
            last_error=code,
            metadata={"error": dict(detail)},
        )
        self._append_message_delivery_event(
            failed,
            "message.delivery_failed",
            {"code": code, "message": message},
        )
        return self._message_delivery_response(failed)

    def _settle_delivery_receipt(
        self,
        claimed: Mapping[str, object],
        receipt: Mapping[str, object],
    ) -> dict[str, Any]:
        delivery_id = str(claimed["delivery_id"])
        status = str(receipt.get("status") or "")
        if status == "queued":
            return self._queue_delivery(claimed, receipt)
        if status == "uncertain":
            uncertain = self.store.update_message_delivery(
                delivery_id,
                status="uncertain",
                last_error=str(receipt.get("reason") or "delivery_uncertain"),
                metadata={"receipt": dict(receipt)},
            )
            self._append_message_delivery_event(
                uncertain,
                "message.delivery_uncertain",
                {"reason": receipt.get("reason")},
            )
            return self._message_delivery_response(uncertain)
        dispatched = self.store.update_message_delivery(
            delivery_id,
            status="dispatched",
            session_id=str(claimed.get("session_id") or ""),
            target_generation=int(receipt.get("generation") or 0),
            metadata={"receipt": dict(receipt), "queue_reason": None},
        )
        resumed = self._resume_assignment_after_delivery(dispatched)
        if resumed:
            dispatched = self.store.update_message_delivery(
                delivery_id,
                status="dispatched",
                metadata={"resumed_assignment": True},
            )
        self._append_message_delivery_event(
            dispatched,
            "message.dispatched",
            {
                "generation": receipt.get("generation"),
                "transcript_sequence": receipt.get("transcript_sequence"),
                "resumed_assignment": resumed,
            },
            capture_grade="observed",
        )
        return self._message_delivery_response(dispatched)

    def _queue_delivery(
        self,
        claimed: Mapping[str, object],
        receipt: Mapping[str, object],
    ) -> dict[str, Any]:
        metadata = dict(claimed.get("metadata") or {})
        queued = self.store.update_message_delivery(
            str(claimed["delivery_id"]),
            status="pending",
            last_error=str(receipt.get("reason") or "session_unavailable"),
            metadata={
                "queue_reason": receipt.get("reason"),
                "queued_event_recorded": True,
            },
        )
        if not metadata.get("queued_event_recorded"):
            self._append_message_delivery_event(
                queued,
                "message.queued",
                {"reason": receipt.get("reason")},
            )
        return self._message_delivery_response(queued)

    def _resume_assignment_after_delivery(
        self,
        delivery: Mapping[str, object],
    ) -> bool:
        assignment_id = str(delivery.get("assignment_id") or "")
        assignment = self.store.get_assignment(assignment_id) if assignment_id else None
        if not assignment or str(assignment.get("status") or "") != "waiting_user":
            return False
        run_id = str(delivery.get("run_id") or assignment.get("run_id") or "")
        conversation_id = str(delivery["conversation_id"])
        session_id = str(delivery.get("session_id") or "")
        generation = int(delivery.get("target_generation") or 0)
        self.store.update_assignment(
            assignment_id,
            status=AssignmentStatus.RUNNING.value,
            metadata={"waiting_reason": None, "blocked_reason": None},
        )
        if run_id:
            self.store.update_run(run_id, status="running", current_stage="assignment")
        self.store.update_conversation(
            conversation_id,
            status="working",
            active_run_id=run_id or None,
        )
        if session_id:
            self.store.update_agent_session(
                session_id,
                status="ready",
                metadata={"runtime_waiting": None},
            )
        self._restart_change_monitor(
            assignment,
            conversation_id=conversation_id,
            run_id=run_id,
            session_id=session_id,
            generation=generation,
        )
        return True

    def _restart_change_monitor(
        self,
        assignment: Mapping[str, object],
        *,
        conversation_id: str,
        run_id: str,
        session_id: str,
        generation: int,
    ) -> None:
        if (
            not run_id
            or str(assignment.get("work_mode") or "") != "write"
            or not str(assignment.get("worktree") or "")
        ):
            return
        change_monitors.start(
            self.workspace,
            ChangeTrackingContext(
                conversation_id=conversation_id,
                run_id=run_id,
                assignment_id=str(assignment["assignment_id"]),
                session_id=session_id or None,
                generation=generation or None,
                worktree=Path(str(assignment["worktree"])),
                author=str(assignment.get("agent_id") or "runtime"),
            ),
        )

    def _append_message_delivery_event(
        self,
        delivery: Mapping[str, object],
        event_type: str,
        payload: Mapping[str, object],
        *,
        capture_grade: str = "recorded",
    ) -> None:
        self.store.append_conversation_event(
            str(delivery["conversation_id"]),
            event_type,
            {
                "delivery_id": str(delivery["delivery_id"]),
                "message_event_id": str(delivery["message_event_id"]),
                "recipient_agent_id": str(delivery["recipient_agent_id"]),
                **dict(payload),
            },
            actor="runtime",
            run_id=str(delivery.get("run_id") or "") or None,
            assignment_id=str(delivery.get("assignment_id") or "") or None,
            session_id=str(delivery.get("session_id") or "") or None,
            generation=int(delivery.get("target_generation") or 0) or None,
            correlation_id=str(delivery["delivery_id"]),
            capture_grade=capture_grade,
        )

    @staticmethod
    def _message_delivery_response(
        delivery: Mapping[str, object],
    ) -> dict[str, Any]:
        status = str(delivery.get("status") or "")
        metadata = (
            delivery.get("metadata")
            if isinstance(delivery.get("metadata"), Mapping)
            else {}
        )
        return {
            "delivery_id": str(delivery["delivery_id"]),
            "agent_id": str(delivery["recipient_agent_id"]),
            "status": "queued" if status in {"pending", "dispatching"} else status,
            "session_id": str(delivery.get("session_id") or "") or None,
            "generation": int(delivery.get("target_generation") or 0) or None,
            "assignment_id": str(delivery.get("assignment_id") or "") or None,
            "run_id": str(delivery.get("run_id") or "") or None,
            "resumed_assignment": bool(metadata.get("resumed_assignment")),
            "error": metadata.get("error"),
        }

    @staticmethod
    def _aggregate_delivery_status(routes: Sequence[Mapping[str, object]]) -> str:
        statuses = {str(item.get("status") or "") for item in routes}
        if statuses == {"dispatched"}:
            return "dispatched"
        if statuses == {"queued"}:
            return "queued"
        if statuses and statuses <= {"failed", "uncertain"}:
            return "failed"
        return "mixed"


def _retry_due(item: Mapping[str, object], now: datetime) -> bool:
    attempts = int(item.get("attempts") or 0)
    updated_at = str(item.get("updated_at") or "")
    if not attempts or not updated_at:
        return True
    try:
        updated = datetime.fromisoformat(updated_at)
    except ValueError:
        updated = now - timedelta(seconds=10)
    delay = min(5.0, 0.5 * (2 ** min(attempts, 4)))
    return (now - updated).total_seconds() >= delay


__all__ = [
    "AgentSessionMessageMixin",
    "CollaborationMessageDeliveryMixin",
    "deliver_runtime_message",
    "mark_delivery_uncertain",
]
