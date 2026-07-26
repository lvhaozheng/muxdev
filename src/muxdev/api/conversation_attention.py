"""Actionable user-attention projections for the Conversation workbench."""

from __future__ import annotations

from typing import Any, Mapping


def attention_bucket(status: str) -> str:
    if status == "needs_user":
        return "needs_you"
    if status in {"candidate_ready", "awaiting_acceptance"}:
        return "ready"
    if status in {"working", "verifying", "recovering", "clarifying"}:
        return "active"
    return "history"


def _waiting_attention(
    assignment: Mapping[str, Any],
    sessions: list[dict[str, Any]],
) -> dict[str, Any]:
    metadata = (
        assignment.get("metadata")
        if isinstance(assignment.get("metadata"), Mapping)
        else {}
    )
    reason = (
        metadata.get("waiting_reason")
        if isinstance(metadata.get("waiting_reason"), Mapping)
        else {}
    )
    session = next(
        (
            item
            for item in sessions
            if str(
                item.get("current_assignment_id")
                or item.get("assignment_id")
                or ""
            )
            == str(assignment.get("assignment_id") or "")
        ),
        None,
    )
    return {
        "kind": "cli_input",
        "label": "需要处理 CLI",
        "title": "Agent CLI 已暂停，需在 Terminal 处理",
        "message": str(
            reason.get("message")
            or "Agent CLI 正在等待额度恢复或运行期操作。"
        ),
        "remediation": str(
            reason.get("remediation")
            or "打开 Terminal 查看具体提示，处理后继续当前 Session。"
        ),
        "action": "open_terminal",
        "assignment_id": str(assignment.get("assignment_id") or ""),
        "session_id": str((session or {}).get("session_id") or ""),
        "agent_id": str(assignment.get("agent_id") or ""),
        "code": str(reason.get("code") or "agent_cli_input_required"),
    }


def _failure_attention(
    conversation: Mapping[str, Any],
    sessions: list[dict[str, Any]],
) -> dict[str, Any] | None:
    metadata = (
        conversation.get("metadata")
        if isinstance(conversation.get("metadata"), Mapping)
        else {}
    )
    startup_error = (
        metadata.get("startup_error")
        if isinstance(metadata.get("startup_error"), Mapping)
        else None
    )
    failed_session = next(
        (item for item in sessions if str(item.get("status") or "") == "failed"),
        None,
    )
    if not startup_error and not failed_session:
        return None
    failure = startup_error or (
        failed_session.get("failure")
        if isinstance((failed_session or {}).get("failure"), Mapping)
        else {}
    )
    return {
        "kind": "session_failed",
        "label": "Session 需要修复",
        "title": "Agent Session 未能继续执行",
        "message": str((failure or {}).get("message") or "Agent Session 已失败。"),
        "remediation": str(
            (failure or {}).get("remediation")
            or "打开 Terminal 查看输出，修复后重新启动 Session。"
        ),
        "action": "open_terminal",
        "session_id": str((failed_session or {}).get("session_id") or ""),
        "agent_id": str((failed_session or {}).get("agent_id") or ""),
        "code": str((failure or {}).get("code") or "session_failed"),
    }


def attention_detail(
    conversation: Mapping[str, Any],
    *,
    interactions: list[dict[str, Any]],
    assignments: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
) -> dict[str, Any] | None:
    pending = next(
        (
            item
            for item in interactions
            if str(item.get("status") or "") == "pending"
        ),
        None,
    )
    if pending:
        return {
            "kind": "clarification",
            "label": "需要回答问题",
            "title": "Agent 正在等待你的回答",
            "message": str(pending.get("prompt") or "请补充完成任务所需的信息。"),
            "remediation": "在对话区的问题卡片中选择选项或输入自定义回答。",
            "action": "respond",
            "interaction_id": str(pending.get("interaction_id") or ""),
        }

    for assignment in assignments:
        if str(assignment.get("status") or "") != "waiting_user":
            continue
        return _waiting_attention(assignment, sessions)

    failure = _failure_attention(conversation, sessions)
    if failure:
        return failure
    if str(conversation.get("status") or "") == "needs_user":
        return {
            "kind": "action_required",
            "label": "需要处理异常",
            "title": "任务已暂停，但没有记录可回答的澄清问题",
            "message": "这不是一个可以在聊天框直接回答的问题。",
            "remediation": "请打开 Terminal 查看最近输出，或查看时间线中的失败事件。",
            "action": "open_terminal",
        }
    return None


def next_actions(
    conversation: Mapping[str, Any],
    run: Mapping[str, Any] | None,
    detail: Mapping[str, Any] | None = None,
) -> list[str]:
    status = str(conversation["status"])
    if status == "needs_user":
        if detail and detail.get("action") == "open_terminal":
            return ["open_terminal", "reassign", "close"]
        return ["respond", "continue", "close"]
    if status in {"candidate_ready", "awaiting_acceptance"}:
        return ["request_changes", "discard", "accept"]
    if status in {"working", "verifying", "recovering"}:
        return ["pause"]
    if status == "idle":
        return ["continue", "close"]
    if run and run.get("review_state") == "answered":
        return ["continue", "close"]
    return ["reopen"] if status == "closed" else ["continue"]


__all__ = ["attention_bucket", "attention_detail", "next_actions"]
