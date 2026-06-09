"""User-facing UX summaries for dashboard, TUI, and API clients.

The daemon stores a rich internal payload. This module compresses that payload
into the few facts a human needs first: what is happening, why it stopped, and
what can be done next.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


TERMINAL_STATUSES = {"completed", "blocked", "aborted"}
WAITING_STATUSES = {"awaiting_approval", "awaiting_provider_action", "paused_budget"}
FAILED_STATUSES = {"blocked", "aborted", "failed"}
DONE_STATUSES = {"completed"}
BOARD_COLUMNS = {
    "todo": "Todo",
    "running": "Running",
    "waiting": "Waiting",
    "needs_review": "Needs Review",
    "done": "Done",
    "failed": "Failed",
}


@dataclass(frozen=True)
class NextAction:
    kind: str
    label: str
    description: str
    command: str | None = None
    endpoint: str | None = None
    method: str = "POST"
    danger: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TaskUxSummary:
    run_id: str
    title: str
    status: str
    user_state: str
    headline: str
    why: str
    current_stage: str | None = None
    progress: list[dict[str, Any]] = field(default_factory=list)
    next_actions: list[NextAction] = field(default_factory=list)
    deliverables: list[dict[str, Any]] = field(default_factory=list)
    risk: str = "low"
    advanced: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["next_actions"] = [action.to_dict() for action in self.next_actions]
        return data


def build_task_ux_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a human-oriented summary for one task detail payload."""
    run = payload.get("run") if isinstance(payload.get("run"), dict) else {}
    if not run:
        return TaskUxSummary(
            run_id="",
            title="No active task",
            status="idle",
            user_state="idle",
            headline="No active task yet",
            why="Start a task from the dashboard, TUI, or `muxdev \"task\"`.",
            next_actions=[
                NextAction(
                    kind="start_task",
                    label="Start a task",
                    description="Describe the change you want muxdev to make.",
                    command='muxdev "fix the failing login test"',
                    method="GET",
                )
            ],
            risk="low",
        ).to_dict()

    run_id = str(run.get("run_id") or payload.get("run_id") or payload.get("task_id") or "")
    title = str(run.get("task") or run_id or "muxdev task")
    status = str(run.get("status") or "unknown")
    stages = [row for row in payload.get("stages", []) if isinstance(row, dict)]
    approvals = [row for row in payload.get("approvals", []) if isinstance(row, dict)]
    provider_actions = [row for row in payload.get("provider_actions", []) if isinstance(row, dict)]
    errors = [row for row in payload.get("errors", []) if isinstance(row, dict)]
    blockers = [row for row in payload.get("review_blockers", []) if isinstance(row, dict)]
    pending_approvals = [row for row in approvals if str(row.get("status")) == "pending"]
    pending_actions = [row for row in provider_actions if str(row.get("status")) == "pending"]
    current_stage = _current_stage(stages)
    progress = [_stage_progress(row) for row in stages]
    deliverables = _deliverables(payload, run_id)

    if pending_actions:
        first = pending_actions[0]
        provider = str(first.get("provider") or "provider")
        action_id = str(first.get("action_id") or "")
        headline = f"{provider} is waiting for your action"
        why = "The external provider CLI/session asked for confirmation, auth, rate-limit handling, or another manual step. muxdev will not answer it for you."
        next_actions = []
        attach = first.get("attach_command") or first.get("transcript_path")
        if attach:
            next_actions.append(
                NextAction(
                    kind="copy_command",
                    label="Open provider session",
                    description="Attach to the provider session and complete the prompt there.",
                    command=str(attach),
                    method="GET",
                )
            )
        next_actions.extend(
            [
                NextAction(
                    kind="mark_handled_continue",
                    label="I handled it, continue",
                    description="Mark this provider action handled and resume the task.",
                    endpoint=f"/api/tasks/{run_id}/actions/{action_id}/handled-and-continue",
                ),
                NextAction(
                    kind="dismiss_provider_action",
                    label="Dismiss action",
                    description="Dismiss this provider action without continuing the task.",
                    endpoint=f"/api/provider-actions/{action_id}/dismiss",
                ),
                NextAction(
                    kind="stop_task",
                    label="Stop task",
                    description="Abort this run if the provider is stuck or the prompt is unsafe.",
                    endpoint=f"/api/tasks/{run_id}/stop",
                    danger=True,
                ),
            ]
        )
        user_state = "needs_action"
        risk = "medium"
    elif pending_approvals:
        first = pending_approvals[0]
        approval_id = str(first.get("approval_id") or "")
        headline = "muxdev is waiting for approval"
        why = str(first.get("reason") or "A policy gate requires a human decision before the task can continue.")
        next_actions = [
            NextAction(
                kind="approve",
                label="Approve",
                description="Allow this gated step to proceed.",
                endpoint=f"/api/approvals/{approval_id}/approve",
            ),
            NextAction(
                kind="deny",
                label="Deny",
                description="Block this gated step and leave the run paused.",
                endpoint=f"/api/approvals/{approval_id}/deny",
                danger=True,
            ),
        ]
        user_state = "needs_approval"
        risk = "medium"
    elif status in {"blocked", "aborted"} or errors:
        headline = "Task needs recovery"
        why = _first_error(errors) or "The task stopped before normal delivery."
        next_actions = [
            NextAction(
                kind="retry_continue",
                label="Try continue",
                description="Ask muxdev to resume from the current run state.",
                endpoint=f"/api/tasks/{run_id}/continue",
            ),
            NextAction(
                kind="view_report",
                label="View report",
                description="Inspect the final or partial report for recovery context.",
                endpoint=f"/api/tasks/{run_id}/report",
                method="GET",
            ),
            NextAction(
                kind="rollback",
                label="Rollback worktree",
                description="Restore the task worktree to the latest safe state.",
                endpoint=f"/api/tasks/{run_id}/rollback",
                danger=True,
            ),
        ]
        user_state = "failed"
        risk = "high"
    elif status == "completed":
        scorecard = payload.get("evidence_scorecard") if isinstance(payload.get("evidence_scorecard"), dict) else {}
        score = scorecard.get("score") if scorecard else None
        headline = "Delivery is ready for review"
        why = f"Run completed with Delivery Confidence {score}/100." if score is not None else "Run completed and delivery artifacts are available."
        next_actions = [
            NextAction(
                kind="view_report",
                label="Open report",
                description="Read the final report and delivery notes.",
                endpoint=f"/api/tasks/{run_id}/report",
                method="GET",
            ),
            NextAction(
                kind="view_diff",
                label="Review diff",
                description="Inspect the patch before shipping.",
                endpoint=f"/api/tasks/{run_id}/diff",
                method="GET",
            ),
        ]
        user_state = "deliverable"
        risk = _scorecard_risk(scorecard)
    else:
        headline = "Task is running"
        why = f"muxdev is working on stage `{current_stage}`." if current_stage else "muxdev is preparing or running this task."
        next_actions = [
            NextAction(
                kind="refresh",
                label="Refresh",
                description="Refresh task state.",
                endpoint=f"/api/tasks/{run_id}",
                method="GET",
            ),
            NextAction(
                kind="stop_task",
                label="Stop task",
                description="Abort this run.",
                endpoint=f"/api/tasks/{run_id}/stop",
                danger=True,
            ),
        ]
        user_state = "running"
        risk = "low" if not blockers else "medium"

    return TaskUxSummary(
        run_id=run_id,
        title=title,
        status=status,
        user_state=user_state,
        headline=headline,
        why=why,
        current_stage=current_stage,
        progress=progress,
        next_actions=next_actions,
        deliverables=deliverables,
        risk=risk,
        advanced={
            "approvals": len(approvals),
            "pending_approvals": len(pending_approvals),
            "provider_actions": len(provider_actions),
            "pending_provider_actions": len(pending_actions),
            "errors": len(errors),
            "review_blockers": len(blockers),
            "trace_events": len(payload.get("trace", []) if isinstance(payload.get("trace"), list) else []),
        },
    ).to_dict()


def build_ux_overview(
    *,
    daemon: dict[str, Any],
    tasks: list[dict[str, Any]],
    approvals: list[dict[str, Any]],
    provider_actions: list[dict[str, Any]],
    selected_task: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an action-first dashboard overview."""
    action_items: list[dict[str, Any]] = []
    for row in provider_actions:
        if not isinstance(row, dict):
            continue
        run_id = str(row.get("run_id") or row.get("task_id") or "")
        action_id = str(row.get("action_id") or "")
        action_items.append(
            {
                "kind": "provider_action",
                "run_id": run_id,
                "action_id": action_id,
                "headline": f"{row.get('provider') or 'provider'} is waiting for your action",
                "why": "Handle the provider CLI prompt, then continue the task.",
                "endpoint": f"/api/tasks/{run_id}/actions/{action_id}/handled-and-continue",
                "command": row.get("attach_command") or row.get("transcript_path"),
            }
        )
    for row in approvals:
        if not isinstance(row, dict):
            continue
        approval_id = str(row.get("approval_id") or "")
        action_items.append(
            {
                "kind": "approval",
                "run_id": row.get("run_id") or row.get("task_id"),
                "approval_id": approval_id,
                "headline": "Approval required",
                "why": row.get("reason") or "A policy gate needs a decision.",
                "endpoint": f"/api/approvals/{approval_id}/approve",
            }
        )

    needs_attention = [task for task in tasks if int(task.get("pending_approvals") or 0) or int(task.get("pending_provider_actions") or 0) or int(task.get("errors") or 0)]
    active = [task for task in tasks if str(task.get("status")) not in TERMINAL_STATUSES]
    latest = selected_task or (tasks[0] if tasks else None)
    task_board = _task_board(tasks)
    return {
        "headline": _overview_headline(action_items, active),
        "daemon": daemon,
        "current_status": _current_status(tasks, approvals, provider_actions, daemon),
        "counts": {
            "tasks": len(tasks),
            "active": len(active),
            "needs_attention": len(needs_attention),
            "approvals": len(approvals),
            "provider_actions": len(provider_actions),
        },
        "action_center": action_items,
        "task_board": task_board,
        "filters": _task_filters(tasks),
        "artifact_center": _overview_artifact_center(tasks),
        "tasks": tasks,
        "selected_task": latest,
    }


def build_provider_health(probes: list[dict[str, Any]]) -> dict[str, Any]:
    ready = [row for row in probes if str(row.get("status")) == "ready"]
    partial = [row for row in probes if str(row.get("status")) == "partial"]
    unavailable = [row for row in probes if str(row.get("status")) == "unavailable"]
    recommendations = []
    if not ready:
        recommendations.append("Use the built-in mock provider for a first demo, then install a real provider CLI.")
    if unavailable:
        recommendations.append("Run `muxdev provider install <name>` or `muxdev provider account <name>` for unavailable providers.")
    if partial:
        recommendations.append("Run `muxdev provider doctor <name>` for providers with partial capability support.")
    return {
        "ready": [row.get("provider") for row in ready],
        "partial": [row.get("provider") for row in partial],
        "unavailable": [row.get("provider") for row in unavailable],
        "total": len(probes),
        "providers": probes,
        "recommendations": recommendations,
    }


def build_setup_status(*, workspace: str, daemon: dict[str, Any], provider_health: dict[str, Any]) -> dict[str, Any]:
    steps = [
        {"id": "daemon", "label": "Local daemon", "status": "ok" if daemon.get("status") in {"ok", "running"} else "needs_attention"},
        {"id": "provider", "label": "Provider ready", "status": "ok" if provider_health.get("ready") else "needs_attention"},
        {"id": "demo", "label": "Run a mock demo", "status": "recommended"},
        {"id": "dashboard", "label": "Open dashboard", "status": "available"},
    ]
    return {
        "workspace": workspace,
        "daemon": daemon,
        "provider_health": provider_health,
        "steps": steps,
        "next_actions": [
            {"label": "Run a mock demo", "command": 'muxdev "make a tiny README change" --provider mock'},
            {"label": "Check providers", "command": "muxdev provider detect"},
            {"label": "Open dashboard", "command": "muxdev dashboard"},
        ],
    }


def _current_stage(stages: list[dict[str, Any]]) -> str | None:
    for row in stages:
        if str(row.get("status")) == "running":
            return str(row.get("stage_id") or "")
    for row in reversed(stages):
        if row.get("stage_id"):
            return str(row.get("stage_id"))
    return None


def _stage_progress(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage_id": row.get("stage_id"),
        "role": row.get("role"),
        "status": row.get("status"),
        "summary": row.get("summary"),
    }


def _deliverables(payload: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    rows = [row for row in payload.get("artifacts", []) if isinstance(row, dict)]
    deliverables: list[dict[str, Any]] = []
    for kind, label in (("plan", "Plan"), ("diff", "Diff"), ("test", "Test report"), ("report", "Final report"), ("dashboard", "Dashboard")):
        match = next((row for row in rows if str(row.get("kind") or "").lower() == kind), None)
        if match:
            deliverables.append({"kind": kind, "label": label, "path": match.get("path"), "ready": True})
    if not any(row["kind"] == "diff" for row in deliverables):
        deliverables.append({"kind": "diff", "label": "Diff", "endpoint": f"/api/tasks/{run_id}/diff", "ready": True})
    if not any(row["kind"] == "report" for row in deliverables):
        deliverables.append({"kind": "report", "label": "Report", "endpoint": f"/api/tasks/{run_id}/report", "ready": True})
    if payload.get("evidence_scorecard"):
        deliverables.append({"kind": "evidence", "label": "Evidence Scorecard", "ready": True})
    return deliverables


def _first_error(errors: list[dict[str, Any]]) -> str:
    if not errors:
        return ""
    first = errors[0]
    return str(first.get("message") or first.get("error") or first.get("type") or "")


def _scorecard_risk(scorecard: dict[str, Any]) -> str:
    if not scorecard:
        return "medium"
    label = str(scorecard.get("label") or "")
    score = int(scorecard.get("score") or 0)
    if label in {"blocked", "risky"} or score < 60:
        return "high"
    if label == "reviewable" or score < 85:
        return "medium"
    return "low"


def _overview_headline(action_items: list[dict[str, Any]], active: list[dict[str, Any]]) -> str:
    if action_items:
        return f"{len(action_items)} item(s) need your attention"
    if active:
        return f"{len(active)} task(s) are running"
    return "Ready for a new task"


def _current_status(
    tasks: list[dict[str, Any]],
    approvals: list[dict[str, Any]],
    provider_actions: list[dict[str, Any]],
    daemon: dict[str, Any],
) -> dict[str, Any]:
    running = [task for task in tasks if str(task.get("status")) == "running"]
    waiting_provider = [task for task in tasks if int(task.get("pending_provider_actions") or 0) > 0 or str(task.get("status")) == "awaiting_provider_action"]
    waiting_approval = [task for task in tasks if int(task.get("pending_approvals") or 0) > 0 or str(task.get("status")) == "awaiting_approval"]
    stuck = [task for task in tasks if str(task.get("status")) in FAILED_STATUSES or int(task.get("errors") or 0) > 0]
    completed = [task for task in tasks if str(task.get("status")) in DONE_STATUSES]
    latest_completed = completed[:5]
    return {
        "daemon": daemon,
        "running": len(running),
        "stuck": len(stuck),
        "waiting_provider_action": len(waiting_provider),
        "waiting_muxdev_approval": len(waiting_approval),
        "pending_provider_actions": len(provider_actions),
        "pending_approvals": len(approvals),
        "recent_completed": [
            {
                "task_id": task.get("task_id") or task.get("run_id"),
                "task": task.get("task"),
                "provider": task.get("provider"),
                "workflow": task.get("workflow"),
                "cost_usd": task.get("cost_usd", 0),
                "tokens": task.get("tokens", 0),
            }
            for task in latest_completed
        ],
    }


def _task_board(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets = {key: [] for key in BOARD_COLUMNS}
    for task in tasks:
        buckets[_board_column(task)].append(_board_task(task))
    return [{"id": key, "label": label, "tasks": buckets[key]} for key, label in BOARD_COLUMNS.items()]


def _board_column(task: dict[str, Any]) -> str:
    status = str(task.get("status") or "")
    if status in DONE_STATUSES:
        return "done"
    if status in FAILED_STATUSES or int(task.get("errors") or 0) > 0:
        return "failed"
    if status in WAITING_STATUSES or int(task.get("pending_approvals") or 0) or int(task.get("pending_provider_actions") or 0):
        return "waiting"
    if status in {"needs_review", "review"} or str(task.get("current_stage") or "") == "review":
        return "needs_review"
    if status in {"created", "queued", "pending"}:
        return "todo"
    return "running" if status else "todo"


def _board_task(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": task.get("task_id") or task.get("run_id"),
        "task": task.get("task"),
        "status": task.get("status"),
        "provider": task.get("provider"),
        "workflow": task.get("workflow"),
        "branch": task.get("branch") or task.get("worktree"),
        "risk": task.get("risk") or _task_risk(task),
        "cost_usd": task.get("cost_usd", 0),
        "tokens": task.get("tokens", 0),
        "pending_approvals": task.get("pending_approvals", 0),
        "pending_provider_actions": task.get("pending_provider_actions", 0),
        "current_stage": task.get("current_stage"),
    }


def _task_filters(tasks: list[dict[str, Any]]) -> dict[str, list[str]]:
    return {
        "provider": _unique(task.get("provider") for task in tasks),
        "workflow": _unique(task.get("workflow") for task in tasks),
        "status": _unique(task.get("status") for task in tasks),
        "branch": _unique(task.get("branch") or task.get("worktree") for task in tasks),
        "risk": _unique(_task_risk(task) for task in tasks),
        "cost": ["0", "0-0.50", "0.50+"],
    }


def _overview_artifact_center(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [task for task in tasks if str(task.get("status")) in DONE_STATUSES]
    return {
        "recent_completed": [
            {
                "task_id": task.get("task_id") or task.get("run_id"),
                "task": task.get("task"),
                "report_endpoint": f"/api/tasks/{task.get('task_id') or task.get('run_id')}/report",
                "diff_endpoint": f"/api/tasks/{task.get('task_id') or task.get('run_id')}/diff",
                "tokens": task.get("tokens", 0),
                "cost_usd": task.get("cost_usd", 0),
            }
            for task in completed[:8]
        ],
        "kinds": ["final_report", "diff", "test_result", "provider_transcript", "stage_contract", "snapshot", "rollback_point", "semantic_merge_result"],
    }


def _task_risk(task: dict[str, Any]) -> str:
    status = str(task.get("status") or "")
    if status in FAILED_STATUSES or int(task.get("errors") or 0) > 0:
        return "high"
    if int(task.get("pending_approvals") or 0) or int(task.get("pending_provider_actions") or 0):
        return "medium"
    if float(task.get("cost_usd") or 0) > 0.5:
        return "medium"
    return "low"


def _unique(values: object) -> list[str]:
    result = sorted({str(value) for value in values if value not in {None, ""}})
    return result
