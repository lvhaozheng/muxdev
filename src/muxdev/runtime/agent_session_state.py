"""Durable projections for noteworthy coding CLI runtime states."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Mapping

from ..models import AgentSessionStatus
from ..storage import ControlStore


_RUNTIME_WAITING_REASONS = {
    "usage_limit": {
        "code": "agent_cli_usage_limit",
        "message": "Agent CLI 已明确报告当前使用额度耗尽，任务已暂停。",
        "remediation": (
            "打开 Terminal 查看额度重置时间；额度恢复后在原 Session 继续，"
            "或将任务重新分配给其他可用 Agent。"
        ),
    },
    "approval": {
        "code": "agent_cli_approval_required",
        "message": "Agent CLI 正在等待命令授权，任务尚未继续执行。",
        "remediation": (
            "打开 Terminal 查看待执行命令及理由并确认。处理后 MuxDev 会自动恢复"
            "原 Assignment；新启动的 Codex Session 默认使用 Approve for me。"
        ),
    },
}
_RECOVERABLE_RUNTIME_CODES = {
    str(item["code"]) for item in _RUNTIME_WAITING_REASONS.values()
}
_REVIEW_FAILURE = re.compile(
    r"(?i)(?:review|audit|verdict|审核|审计|结论).{0,40}"
    r"(?:failed|blocked|reject|不通过|阻塞|失败)"
)
_REVIEW_SUCCESS = re.compile(
    r"(?i)(?:review|audit|verdict|审核|审计|结论).{0,40}"
    r"(?:passed|satisfied|approved|通过|无阻塞)"
    r"|no (?:blocking )?(?:issues|findings)"
    r"|未发现.{0,20}(?:问题|缺陷|风险)"
)


def project_runtime_waiting(
    workspace: Path,
    session_id: str,
    *,
    reason_kind: str = "usage_limit",
) -> bool:
    """Move a running Assignment to an actionable user-waiting state."""
    reason = dict(
        _RUNTIME_WAITING_REASONS.get(
            reason_kind,
            {
                "code": "agent_cli_input_required",
                "message": "Agent CLI 正在等待运行期操作。",
                "remediation": "打开 Terminal 查看具体提示，处理后继续当前 Session。",
            },
        )
    )
    with ControlStore(workspace) as store:
        session = store.get_agent_session(session_id)
        if not session:
            return False
        assignment_id = str(
            session.get("current_assignment_id")
            or session.get("assignment_id")
            or ""
        )
        assignment = store.get_assignment(assignment_id) if assignment_id else None
        if not assignment or str(assignment.get("status")) != "running":
            return False
        store.update_agent_session(
            session_id,
            status=AgentSessionStatus.WAITING_INPUT.value,
            metadata={"runtime_waiting": reason},
        )
        store.update_assignment(
            assignment_id,
            status="waiting_user",
            metadata={"waiting_reason": reason},
        )
        run_id = str(assignment.get("run_id") or "")
        if run_id:
            store.update_run(
                run_id,
                status="waiting_user",
                current_stage="assignment",
            )
        conversation_id = str(assignment["conversation_id"])
        store.update_conversation(conversation_id, status="needs_user")
        store.append_conversation_event(
            conversation_id,
            "assignment.waiting_user",
            {
                "assignment_id": assignment_id,
                "session_id": session_id,
                "reason": reason,
            },
            actor="runtime",
            run_id=run_id or None,
            assignment_id=assignment_id,
            session_id=session_id,
            generation=int(session.get("generation") or 0) or None,
        )
        return True


def project_runtime_resumed(
    workspace: Path,
    session_id: str,
    *,
    trigger: str = "agent_activity",
) -> bool:
    """Clear a stale CLI blocker once the same Session produces fresh activity."""
    tracking: dict[str, Any] | None = None
    with ControlStore(workspace) as store:
        session = store.get_agent_session(session_id)
        if not session:
            return False
        assignment_id = str(
            session.get("current_assignment_id")
            or session.get("assignment_id")
            or ""
        )
        assignment = store.get_assignment(assignment_id) if assignment_id else None
        if not assignment or str(assignment.get("status") or "") != "waiting_user":
            return False
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
        if str(reason.get("code") or "") not in _RECOVERABLE_RUNTIME_CODES:
            return False
        run_id = str(assignment.get("run_id") or "")
        conversation_id = str(assignment["conversation_id"])
        store.update_agent_session(
            session_id,
            status=AgentSessionStatus.BUSY.value,
            metadata={"runtime_waiting": None},
        )
        store.update_assignment(
            assignment_id,
            status="running",
            metadata={"waiting_reason": None, "blocked_reason": None},
        )
        if run_id:
            run = store.get_run(run_id)
            if run and str(run.get("status") or "") == "waiting_user":
                store.update_run(run_id, status="running", current_stage="assignment")
        assignments = store.list_assignments(conversation_id)
        has_other_waiting = any(
            str(item.get("assignment_id") or "") != assignment_id
            and str(item.get("status") or "") == "waiting_user"
            for item in assignments
        )
        has_pending_interaction = any(
            str(item.get("status") or "") == "pending"
            for item in store.list_conversation_interactions(conversation_id)
        )
        if not has_other_waiting and not has_pending_interaction:
            store.update_conversation(
                conversation_id,
                status="working",
                active_run_id=run_id or None,
            )
        store.append_conversation_event(
            conversation_id,
            "assignment.resumed",
            {
                "assignment_id": assignment_id,
                "session_id": session_id,
                "trigger": trigger,
                "cleared_reason": reason,
            },
            actor="runtime",
            run_id=run_id or None,
            assignment_id=assignment_id,
            session_id=session_id,
            generation=int(session.get("generation") or 0) or None,
        )
        if run_id and str(assignment.get("work_mode") or "") == "write":
            tracking = {
                "conversation_id": conversation_id,
                "run_id": run_id,
                "assignment_id": assignment_id,
                "session_id": session_id,
                "generation": int(session.get("generation") or 0) or None,
                "worktree": Path(str(assignment["worktree"])),
                "author": str(assignment.get("agent_id") or "runtime"),
            }
    if tracking:
        from .change_tracking import ChangeTrackingContext, change_monitors

        change_monitors.start(
            Path(workspace),
            ChangeTrackingContext(**tracking),
        )
    return True


def project_agent_turn_completed(
    workspace: Path,
    session_id: str,
    transcript_text: str,
) -> bool:
    """Translate an observed interactive-CLI completion into an Agent report.

    The report remains an Agent claim. Change tracking and Evidence gates still
    establish whether the claimed deliverables are trustworthy.
    """
    workspace = Path(workspace).resolve()
    with ControlStore(workspace) as store:
        session = store.get_agent_session(session_id)
        if not session:
            return False
        generation = int(session.get("generation") or 0)
        session_metadata = (
            session.get("metadata")
            if isinstance(session.get("metadata"), Mapping)
            else {}
        )
        if int(session_metadata.get("auto_reported_generation") or 0) == generation:
            return False
        assignment_id = str(
            session.get("current_assignment_id")
            or session.get("assignment_id")
            or ""
        )
        assignment = store.get_assignment(assignment_id) if assignment_id else None
        if not assignment or str(assignment.get("status") or "") not in {
            "running",
            "waiting_user",
        }:
            return False
        assignment_snapshot = dict(assignment)

    if str(assignment_snapshot.get("status") or "") == "waiting_user":
        project_runtime_resumed(
            workspace,
            session_id,
            trigger="agent_turn_completed",
        )

    manifest: dict[str, object] = {
        "summary": _completion_summary(transcript_text),
        "deliverables": list(assignment_snapshot.get("deliverables") or [])
        or ["Agent 已完成当前 Assignment 回合"],
        "proof": [
            "DeepCode TUI reported status: completed",
            "terminal transcript sha256:"
            + hashlib.sha256(transcript_text.encode("utf-8")).hexdigest(),
            "MuxDev change tracking and Evidence gates remain authoritative",
        ],
        "capture_grade": "observed",
    }
    if str(assignment_snapshot.get("dispatch_kind") or "") in {
        "review",
        "security-review",
    }:
        manifest["status"] = _review_status(transcript_text)

    from .collaboration_service import CollaborationService
    from .conversation_service import ConversationService
    from .engine import RunEngine

    engine = RunEngine(workspace)
    try:
        service = CollaborationService(
            ConversationService(engine, engine.store),
            engine.store,
        )
        current = service.store.get_assignment(
            str(assignment_snapshot["assignment_id"])
        )
        if not current or str(current.get("status") or "") not in {
            "running",
            "waiting_user",
        }:
            return False
        service.report(str(current["assignment_id"]), manifest)
        service.store.update_agent_session(
            session_id,
            metadata={
                "auto_reported_generation": generation,
                "auto_report_capture_grade": "observed",
            },
        )
        service.store.append_conversation_event(
            str(current["conversation_id"]),
            "assignment.auto_reported",
            {
                "assignment_id": str(current["assignment_id"]),
                "session_id": session_id,
                "generation": generation,
                "capture_grade": "observed",
            },
            actor="runtime",
            run_id=str(current.get("run_id") or "") or None,
            assignment_id=str(current["assignment_id"]),
            session_id=session_id,
            generation=generation or None,
        )
        return True
    finally:
        engine.store.close()


def _completion_summary(transcript_text: str) -> str:
    lines = [
        line.strip()
        for line in transcript_text.splitlines()
        if line.strip()
        and not re.search(
            r"(?i)^status:\s*completed|^tokens:\s*\d+|Type your message",
            line.strip(),
        )
    ]
    latest = lines[-1] if lines else ""
    if 12 <= len(latest) <= 500:
        return f"DeepCode 已完成当前回合：{latest}"
    return "DeepCode 已完成当前 Assignment 回合；结果已交由 MuxDev 变更跟踪与交付门禁验证。"


def _review_status(transcript_text: str) -> str:
    if _REVIEW_FAILURE.search(transcript_text):
        return "failed"
    if _REVIEW_SUCCESS.search(transcript_text):
        return "passed"
    return "satisfied"


__all__ = [
    "project_agent_turn_completed",
    "project_runtime_resumed",
    "project_runtime_waiting",
]
