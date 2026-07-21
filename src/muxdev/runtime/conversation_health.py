"""Repair Conversation execution states left behind by an interrupted process."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Mapping

from ..models import ConversationStatus


_INITIALIZATION_STALE_AFTER = timedelta(minutes=10)


class ConversationHealthMixin:
    def _reconcile_execution_state(
        self, conversation: Mapping[str, Any]
    ) -> dict[str, Any]:
        if conversation.get("status") not in {
            ConversationStatus.VERIFYING,
            ConversationStatus.RECOVERING,
        }:
            return dict(conversation)
        run_id = str(conversation.get("active_run_id") or "")
        run = self.store.get_run(run_id) if run_id else None
        reason = ""
        if not run:
            reason = "会话已进入执行态，但后台未能持久化对应 Run。"
        elif run.get("status") == "created" and _older_than(
            str(run.get("updated_at") or ""), _INITIALIZATION_STALE_AFTER
        ):
            reason = "Run 初始化超过 10 分钟且尚未进入任何执行阶段。"
        if not reason:
            return dict(conversation)
        failure = {
            "code": "orphaned_execution",
            "kind": "environment",
            "stage_id": "startup",
            "summary": "后台执行被中断，已从无任务的假运行状态恢复。",
            "details": [reason],
        }
        next_actions = [{
            "action": "start_work",
            "label": "重新建立执行",
            "description": "保留当前隔离工作树和契约，创建新的 Run 并重新开始。",
            "requires_confirmation": False,
        }]
        updated = self.store.update_conversation(
            str(conversation["conversation_id"]),
            status=ConversationStatus.NEEDS_USER,
        )
        self.store.append_conversation_event(
            str(conversation["conversation_id"]),
            "run.orphaned",
            {
                "message": failure["summary"],
                "run_id": run_id or None,
                "failure": failure,
                "recovery": {
                    "status": "needs_action",
                    "actions_used": 0,
                    "max_actions": 2,
                    "attempts": [],
                    "workspace_safe": True,
                    "workspace_message": "隔离工作树和已生成文件均已保留，未写回原项目。",
                },
                "next_actions": next_actions,
            },
            actor="supervisor",
            run_id=run_id or None,
        )
        return updated

    def _recovery_projection(
        self,
        conversation_id: str,
        candidates: list[Mapping[str, Any]],
    ) -> dict[str, Any]:
        recovery: dict[str, Any] = {}
        for event in reversed(self.store.conversation_events(conversation_id)):
            if event["type"] in {"delivery.verified", "delivery.needs_user"}:
                break
            if event["type"] not in {
                "run.failed_to_start", "recovery.failed", "run.orphaned",
            }:
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            recovery = dict(payload.get("recovery") or {})
            recovery["primary_failure"] = payload.get("failure")
            recovery["next_actions"] = payload.get("next_actions", [])
            break
        candidate = candidates[-1] if candidates else None
        metadata = (
            candidate.get("metadata")
            if candidate and isinstance(candidate.get("metadata"), dict) else {}
        )
        recovery = recovery or dict(metadata.get("recovery") or {})
        recovery.setdefault("status", "not_needed")
        recovery.setdefault("max_actions", 2)
        recovery.setdefault("actions_used", 0)
        recovery.setdefault("attempts", [])
        recovery.setdefault("next_actions", [])
        recovery.setdefault("workspace_safe", True)
        recovery["remaining_actions"] = max(
            0,
            int(recovery.get("max_actions") or 0)
            - int(recovery.get("actions_used") or 0),
        )
        return recovery


def _older_than(value: str, duration: timedelta) -> bool:
    if not value:
        return True
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        return True
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return datetime.now(UTC) - timestamp > duration


__all__ = ["ConversationHealthMixin"]
