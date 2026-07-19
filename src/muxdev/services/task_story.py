"""Privacy-bounded task-first read models for the v0.2 dashboard."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


TASK_STORY_CONTRACT = "muxdev.task-story.v1"
TASK_LIST_CONTRACT = "muxdev.dashboard-task-list.v1"
PHASES = ("submitted", "routed", "executing", "reviewing", "verifying", "attested")
MAX_TIMELINE_LIMIT = 200
MAX_TASK_LIMIT = 100


def build_task_story(
    board: Any,
    run_id: str,
    *,
    cursor_secret: bytes,
    cursor: str | None = None,
    limit: int = MAX_TIMELINE_LIMIT,
) -> dict[str, Any]:
    run = board.get_run(run_id)
    approvals = board.list_approvals(run_id=run_id)
    reviews = board.list_review_assignments(run_id)
    route = board.latest_route_decision(run_id, kind="main")
    attempts = _recent_rows(board, "provider_attempts", run_id, "attempt DESC", limit=1)
    jobs = _recent_rows(board, "execution_jobs", run_id, "created_at_ms DESC", limit=1)
    tests = _aggregate_tests(board, run_id)
    artifact_count = _count_rows(board, "artifacts", run_id)
    evaluations = _recent_rows(board, "evidence_evaluations", run_id, "created_at DESC", limit=1)
    attestation = board.latest_delivery_attestation(run_id)
    offset = _decode_cursor(cursor, cursor_secret, run_id)
    page_limit = max(1, min(int(limit), MAX_TIMELINE_LIMIT))
    timeline, timeline_total = _bounded_timeline(board, run_id, offset + page_limit)
    page = timeline[offset : offset + page_limit]
    next_offset = offset + len(page)
    phase = _phase(run, route, reviews, evaluations, attestation)
    next_action = _next_action(run, jobs, approvals, reviews, attestation)
    current_review = reviews[-1] if reviews else None
    current_attempt = attempts[-1] if attempts else None
    current_job = jobs[-1] if jobs else None
    warnings = _warnings(run, current_job, current_review, attestation)
    job_state = current_job.get("state") if current_job else None
    return {
        "contract_version": TASK_STORY_CONTRACT,
        "run": {
            "run_id": run_id,
            "title": str(run.get("task") or run_id)[:500],
            "status": str(run.get("status") or "unknown"),
            "provider": run.get("provider"),
            "workflow": run.get("workflow"),
            "risk_level": _risk_level(board, run_id),
            "delivery_mode": _delivery_mode(board, run_id),
            "created_at": run.get("created_at"),
            "updated_at": run.get("updated_at"),
        },
        "progress": {
            "phases": list(PHASES),
            "current_phase": phase,
            "percent": int((PHASES.index(phase) + (1 if phase == "attested" else 0)) / len(PHASES) * 100),
            "current_stage": run.get("current_stage"),
            "next_action": next_action,
        },
        "route": _route_projection(route),
        "execution": {
            "status": job_state,
            "attempt": current_job.get("attempt") if current_job else None,
            "cancel_requested": job_state == "cancel_requested",
            "recovery_status": "manual_reconciliation" if job_state == "reconciliation_required" else "automatic",
        },
        "review": _review_projection(current_review),
        "timeline": {
            "items": page,
            "next_cursor": _encode_cursor(cursor_secret, run_id, next_offset) if next_offset < timeline_total else None,
            "total": timeline_total,
        },
        "trust": {
            "provider_attempt": _attempt_projection(current_attempt),
            "review": _review_projection(current_review),
            "evidence": _evidence_projection(evaluations[-1] if evaluations else None),
            "attestation": _attestation_projection(attestation),
        },
        "deliverables": {
            "tests": tests,
            "artifact_count": artifact_count,
            "links": {
                "report": f"/api/tasks/{run_id}/report",
                "diff": f"/api/tasks/{run_id}/diff",
                "attestation": f"/api/tasks/{run_id}/attestation",
                "review": f"/review/{run_id}",
            },
        },
        "warnings": warnings,
        "links": {"self": f"/api/tasks/{run_id}/story", "task": f"/api/tasks/{run_id}"},
    }


def query_dashboard_task_summaries(
    board: Any,
    *,
    cursor_secret: bytes,
    cursor: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Page task cards in SQLite without building every legacy task detail."""
    offset = _decode_cursor(cursor, cursor_secret, "dashboard-tasks")
    page_limit = max(1, min(int(limit), MAX_TASK_LIMIT))
    total = int(board.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0])
    rows = [
        dict(row)
        for row in board.conn.execute(
            """
            SELECT r.*,
              (SELECT s.stage_id FROM stages s WHERE s.run_id=r.run_id
               ORDER BY COALESCE(s.started_at, s.completed_at, '') DESC, s.stage_id DESC LIMIT 1) AS current_stage,
              (SELECT COUNT(*) FROM approvals a WHERE a.run_id=r.run_id AND a.status='pending') AS pending_approvals,
              (SELECT COUNT(*) FROM provider_actions p WHERE p.run_id=r.run_id AND p.status='pending') AS pending_provider_actions,
              (SELECT e.label FROM evidence_evaluations e WHERE e.run_id=r.run_id ORDER BY e.created_at DESC LIMIT 1) AS evidence_label,
              (SELECT e.confidence FROM evidence_evaluations e WHERE e.run_id=r.run_id ORDER BY e.created_at DESC LIMIT 1) AS evidence_confidence
            FROM runs r
            ORDER BY r.updated_at DESC, r.run_id DESC
            LIMIT ? OFFSET ?
            """,
            (page_limit, offset),
        )
    ]
    items = [_task_summary(row) for row in rows]
    next_offset = offset + len(items)
    return {
        "contract_version": TASK_LIST_CONTRACT,
        "items": items,
        "next_cursor": _encode_cursor(cursor_secret, "dashboard-tasks", next_offset) if next_offset < total else None,
        "total": total,
    }


def paginate_task_summaries(
    rows: Iterable[Mapping[str, Any]],
    *,
    cursor_secret: bytes,
    cursor: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    safe_rows = sorted((dict(row) for row in rows), key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""), reverse=True)
    offset = _decode_cursor(cursor, cursor_secret, "dashboard-tasks")
    page_limit = max(1, min(int(limit), MAX_TASK_LIMIT))
    items = [_task_summary(row) for row in safe_rows[offset : offset + page_limit]]
    next_offset = offset + len(items)
    return {
        "contract_version": TASK_LIST_CONTRACT,
        "items": items,
        "next_cursor": _encode_cursor(cursor_secret, "dashboard-tasks", next_offset) if next_offset < len(safe_rows) else None,
        "total": len(safe_rows),
    }


def _timeline(*groups: object) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for group in groups:
        rows = group if isinstance(group, list) else ([group] if isinstance(group, dict) else [])
        for row in rows:
            if not isinstance(row, dict):
                continue
            event_type = str(row.get("event_type") or row.get("type") or row.get("status") or "event")
            source = _event_source(row)
            items.append(
                {
                    "id": str(row.get("event_id") or row.get("approval_id") or row.get("review_id") or row.get("attestation_id") or hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()[:16]),
                    "category": source,
                    "type": event_type,
                    "status": row.get("status"),
                    "summary": _summary(event_type, row),
                    "created_at": row.get("created_at") or row.get("updated_at") or row.get("decided_at"),
                }
            )
    items.sort(key=lambda item: (_timestamp(item.get("created_at")), str(item["id"])), reverse=True)
    return items


def _bounded_timeline(board: Any, run_id: str, fetch_limit: int) -> tuple[list[dict[str, Any]], int]:
    """Read only enough rows from each fact stream to fill the requested page."""
    fetch_limit = max(1, int(fetch_limit))
    specifications = (
        ("state_events", "created_at", "event_id,event_type,created_at", "state"),
        ("execution_events", "created_at_ms", "event_id,event_type,created_at_ms", "execution"),
        ("harness_events", "created_at", "event_id,event_type,created_at,stage_id,provider", "harness"),
        ("approvals", "COALESCE(decided_at,created_at)", "approval_id,status,reason,created_at,decided_at", "approval"),
        ("review_assignments", "updated_at", "review_id,status,verdict,created_at,updated_at", "review"),
        ("delivery_attestations", "created_at", "attestation_id,status,created_at", "delivery"),
    )
    groups: list[list[dict[str, Any]]] = []
    total = 0
    for table, order_column, columns, category in specifications:
        total += int(board.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE run_id=?", (run_id,)).fetchone()[0])
        rows = [
            dict(row)
            for row in board.conn.execute(
                f"SELECT {columns} FROM {table} WHERE run_id=? ORDER BY {order_column} DESC LIMIT ?",
                (run_id, fetch_limit),
            )
        ]
        for row in rows:
            row["_category"] = category
            if "created_at_ms" in row:
                milliseconds = int(row.pop("created_at_ms") or 0)
                row["created_at"] = datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).isoformat()
        groups.append(rows)
    return _timeline(*groups), total


def _recent_rows(board: Any, table: str, run_id: str, order_by: str, *, limit: int) -> list[dict[str, Any]]:
    allowed = {
        "provider_attempts": "attempt DESC",
        "execution_jobs": "created_at_ms DESC",
        "evidence_evaluations": "created_at DESC",
    }
    if allowed.get(table) != order_by:
        raise ValueError("unsupported TaskStory projection query")
    return [
        dict(row)
        for row in board.conn.execute(
            f"SELECT * FROM {table} WHERE run_id=? ORDER BY {order_by} LIMIT ?", (run_id, int(limit))
        )
    ]


def _aggregate_tests(board: Any, run_id: str) -> dict[str, int]:
    row = board.conn.execute(
        "SELECT COUNT(*) AS total, COALESCE(SUM(CASE WHEN passed THEN 1 ELSE 0 END),0) AS passed FROM test_results WHERE run_id=?",
        (run_id,),
    ).fetchone()
    return {"total": int(row["total"]), "passed": int(row["passed"])}


def _count_rows(board: Any, table: str, run_id: str) -> int:
    if table != "artifacts":
        raise ValueError("unsupported TaskStory count query")
    return int(board.conn.execute("SELECT COUNT(*) FROM artifacts WHERE run_id=?", (run_id,)).fetchone()[0])


def _event_source(row: Mapping[str, Any]) -> str:
    if row.get("_category"):
        return str(row["_category"])
    if "attestation_id" in row:
        return "delivery"
    if "review_id" in row:
        return "review"
    if "approval_id" in row:
        return "approval"
    event = str(row.get("event_type") or "")
    if event.startswith("execution."):
        return "execution"
    if row.get("stage_id") and row.get("provider"):
        return "harness"
    return "state"


def _summary(event_type: str, row: Mapping[str, Any]) -> str:
    for key in ("reason", "summary", "verdict", "error"):
        value = row.get(key)
        if value:
            return str(value)[:300]
    return event_type.replace("_", " ").replace(".", " · ")[:300]


def _phase(run: Mapping[str, Any], route: Mapping[str, Any] | None, reviews: list[dict[str, Any]], evaluations: list[dict[str, Any]], attestation: Mapping[str, Any] | None) -> str:
    if attestation:
        return "attested"
    status = str(run.get("status") or "")
    if status in {"attesting", "completed"} or evaluations:
        return "verifying"
    if status == "reviewing" or reviews:
        return "reviewing"
    if status in {"running", "waiting_approval", "awaiting_approval", "awaiting_provider_action", "paused_budget", "blocked", "aborted"}:
        return "executing"
    return "routed" if route else "submitted"


def _next_action(run: Mapping[str, Any], jobs: list[dict[str, Any]], approvals: list[dict[str, Any]], reviews: list[dict[str, Any]], attestation: Mapping[str, Any] | None) -> dict[str, Any]:
    pending = [row for row in approvals if str(row.get("status")) == "pending"]
    if pending:
        return {"kind": "approval", "label": "Review pending approval", "target": f"/api/approvals/{pending[-1].get('approval_id')}"}
    job = jobs[-1] if jobs else {}
    if (job.get("state") or job.get("status")) == "reconciliation_required":
        return {"kind": "reconcile", "label": "Choose retry or abort after reviewing external side effects", "target": f"/api/tasks/{run.get('run_id')}/executions"}
    review = reviews[-1] if reviews else {}
    if review.get("status") in {"assigned", "running", "blocked"}:
        return {"kind": "review", "label": "Resolve independent review", "target": f"/api/tasks/{run.get('run_id')}/review"}
    status = str(run.get("status") or "")
    if status == "completed":
        return {"kind": "verify", "label": "Verify signed delivery", "target": f"/api/tasks/{run.get('run_id')}/attestation"}
    if status in {"blocked", "aborted"}:
        return {"kind": "inspect", "label": "Inspect failure evidence before resume or repair", "target": f"/api/tasks/{run.get('run_id')}"}
    if attestation:
        return {"kind": "export", "label": "Export offline attestation", "target": f"/api/tasks/{run.get('run_id')}/attestation-exports"}
    return {"kind": "observe", "label": "Follow durable execution", "target": f"/api/tasks/{run.get('run_id')}/executions"}


def _route_projection(route: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not route:
        return None
    candidates = route.get("candidates") if isinstance(route.get("candidates"), list) else []
    return {
        "decision_id": route.get("decision_id"),
        "selected_main_provider": route.get("selected_main_provider"),
        "selected_reviewer_provider": route.get("selected_reviewer_provider"),
        "reason_codes": route.get("reason_codes") or [],
        "candidate_exclusions": [
            {"provider": row.get("provider"), "exclusion_codes": row.get("exclusion_codes") or []}
            for row in candidates if isinstance(row, dict) and row.get("exclusion_codes")
        ],
        "policy_version": route.get("policy_version"),
        "benchmark_snapshot_id": route.get("benchmark_snapshot_id"),
        "decision_hash": route.get("decision_hash"),
    }


def _review_projection(review: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not review:
        return None
    return {key: review.get(key) for key in ("review_id", "reviewer_provider", "status", "attempt", "verdict", "snapshot_hash", "waiver_approval_id")}


def _attempt_projection(attempt: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not attempt:
        return None
    return {key: attempt.get(key) for key in ("provider", "status", "adapter_version", "certification_id", "trust_tier", "isolation_mode", "waiver_approval_id", "cancellation_mode")}


def _attestation_projection(attestation: Mapping[str, Any] | None) -> dict[str, Any]:
    if not attestation:
        return {"status": "legacy_unsigned", "identity_status": "unsigned", "evidence_valid": False}
    return {key: attestation.get(key) for key in ("attestation_id", "generation", "status", "payload_hash", "key_id", "public_key_fingerprint", "identity_status", "evidence_valid")}


def _evidence_projection(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {key: row.get(key) for key in ("label", "confidence", "head_hash", "created_at")}


def _warnings(run: Mapping[str, Any], job: Mapping[str, Any] | None, review: Mapping[str, Any] | None, attestation: Mapping[str, Any] | None) -> list[str]:
    warnings: list[str] = []
    if job and (job.get("state") or job.get("status")) == "reconciliation_required":
        warnings.append("opaque Provider outcome requires explicit reconciliation")
    if review and review.get("waiver_approval_id"):
        warnings.append("heterogeneous review requirement was waived")
    if str(run.get("status")) == "completed" and (not attestation or attestation.get("status") != "signed"):
        warnings.append("completed delivery is unsigned and is not trusted production evidence")
    return warnings


def _risk_level(board: Any, run_id: str) -> str:
    payload = _run_spec_payload(board, run_id)
    policy = payload.get("harness_policy") if isinstance(payload.get("harness_policy"), dict) else {}
    return str(policy.get("risk_level") or "unknown")


def _delivery_mode(board: Any, run_id: str) -> str:
    payload = _run_spec_payload(board, run_id)
    policy = payload.get("routing_policy") if isinstance(payload.get("routing_policy"), dict) else {}
    return str(policy.get("delivery_mode") or "unknown")


def _run_spec_payload(board: Any, run_id: str) -> dict[str, Any]:
    row = board.conn.execute("SELECT payload_json FROM run_specs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        return {}
    try:
        payload = json.loads(str(row["payload_json"]))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _task_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    evidence = row.get("evidence_summary") or {}
    if not evidence and row.get("evidence_label"):
        evidence = {"label": row.get("evidence_label"), "confidence": row.get("evidence_confidence")}
    return {
        "run_id": row.get("run_id") or row.get("task_id"),
        "title": str(row.get("task") or row.get("title") or row.get("run_id") or "")[:300],
        "status": row.get("status"),
        "provider": row.get("provider"),
        "workflow": row.get("workflow"),
        "current_stage": row.get("current_stage"),
        "pending_approvals": row.get("pending_approvals", 0),
        "pending_provider_actions": row.get("pending_provider_actions", 0),
        "evidence": evidence,
        "updated_at": row.get("updated_at") or row.get("created_at"),
    }


def _encode_cursor(secret: bytes, scope: str, offset: int) -> str:
    raw = json.dumps({"scope": scope, "offset": int(offset)}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    signature = hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def _decode_cursor(cursor: str | None, secret: bytes, scope: str) -> int:
    if not cursor:
        return 0
    try:
        encoded, signature = cursor.rsplit(".", 1)
        expected = hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid cursor signature")
        raw = base64.urlsafe_b64decode(encoded + "=" * ((4 - len(encoded) % 4) % 4))
        payload = json.loads(raw.decode("utf-8"))
        if payload.get("scope") != scope:
            raise ValueError("cursor scope mismatch")
        return max(0, int(payload["offset"]))
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid task cursor") from exc


def _timestamp(value: object) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (OSError, ValueError):
        # ``datetime.min.timestamp()`` is not representable on Windows.
        return datetime(1970, 1, 1, tzinfo=timezone.utc).timestamp()
