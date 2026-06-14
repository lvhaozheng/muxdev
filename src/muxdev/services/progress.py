"""Progress read helpers for human-facing workflow status."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def enrich_stages(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_with_elapsed(row, start_key="started_at", end_key="completed_at") for row in rows]


def enrich_provider_attempts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_with_elapsed(row, start_key="started_at", end_key="completed_at") for row in rows]


def progress_summary(
    *,
    run: dict[str, Any],
    stages: list[dict[str, Any]],
    provider_attempts: list[dict[str, Any]],
    approvals: list[dict[str, Any]],
    provider_actions: list[dict[str, Any]],
) -> dict[str, Any]:
    current = next((row for row in stages if row.get("status") == "running"), None)
    latest_attempt = _latest(provider_attempts, "started_at")
    pending_action = next((row for row in provider_actions if str(row.get("status")) == "pending"), None)
    pending_approval = next((row for row in approvals if str(row.get("status")) == "pending"), None)
    current_stage = str(current.get("stage_id") or "") if current else ""
    return {
        "elapsed_seconds": elapsed_seconds(run.get("created_at"), run.get("updated_at") if _terminal(run) else None),
        "current_stage": current_stage,
        "current_stage_elapsed_seconds": current.get("elapsed_seconds") if current else None,
        "current_activity": _current_activity(
            current=current,
            latest_attempt=latest_attempt,
            pending_action=pending_action,
            pending_approval=pending_approval,
        ),
        "latest_provider_attempt": _attempt_excerpt(latest_attempt),
        "stage_timeline": [_stage_excerpt(row) for row in stages],
    }


def elapsed_seconds(start: object, end: object | None = None) -> int | None:
    started = _parse_time(start)
    if started is None:
        return None
    ended = _parse_time(end) if end else datetime.now(timezone.utc)
    if ended is None:
        return None
    return max(0, int((ended - started).total_seconds()))


def _with_elapsed(row: dict[str, Any], *, start_key: str, end_key: str) -> dict[str, Any]:
    result = dict(row)
    result["elapsed_seconds"] = elapsed_seconds(row.get(start_key), row.get(end_key))
    return result


def _current_activity(
    *,
    current: dict[str, Any] | None,
    latest_attempt: dict[str, Any] | None,
    pending_action: dict[str, Any] | None,
    pending_approval: dict[str, Any] | None,
) -> str:
    if pending_action:
        return f"waiting for provider action: {pending_action.get('stage_id') or '-'} {pending_action.get('kind') or ''}".strip()
    if pending_approval:
        return f"waiting for approval: {pending_approval.get('type') or '-'} {pending_approval.get('reason') or ''}".strip()
    if latest_attempt and str(latest_attempt.get("status")) in {"running", "started"}:
        return f"provider {latest_attempt.get('provider') or '-'} attempt {latest_attempt.get('attempt') or '-'} on {latest_attempt.get('stage_id') or '-'}"
    if current:
        return f"running stage {current.get('stage_id') or '-'}"
    return ""


def _stage_excerpt(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage_id": row.get("stage_id"),
        "role": row.get("role"),
        "status": row.get("status"),
        "elapsed_seconds": row.get("elapsed_seconds"),
        "summary": row.get("summary"),
    }


def _attempt_excerpt(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "stage_id": row.get("stage_id"),
        "provider": row.get("provider"),
        "attempt": row.get("attempt"),
        "status": row.get("status"),
        "failure_kind": row.get("failure_kind"),
        "returncode": row.get("returncode"),
        "elapsed_seconds": row.get("elapsed_seconds"),
        "summary": row.get("summary"),
    }


def _latest(rows: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    if not rows:
        return None
    return max(rows, key=lambda row: str(row.get(key) or ""))


def _terminal(run: dict[str, Any]) -> bool:
    return str(run.get("status") or "") in {"completed", "blocked", "aborted"}


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
