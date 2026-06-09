"""Run-level task collaboration dashboard payload and writer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .. import __version__
from ..api.web import write_dashboard
from ..config.runtime import load_runtime_config, provider_cache_path
from ..models import ApprovalStatus, ProviderActionStatus
from ..storage import Blackboard, compact_trace, read_trace
from .ux import build_task_ux_summary


TERMINAL_RUN_STATES = {"completed", "blocked", "aborted"}


def dashboard_path(run_dir: Path) -> Path:
    """Return the canonical dashboard path for a run directory."""
    return run_dir / "dashboard.html"


def startup_dashboard_payload(workspace: Path) -> dict[str, Any]:
    """Build a dashboard payload for a workspace that has no run yet."""
    payload = {
        "app": _app_payload(workspace),
        "run": None,
        "stages": [],
        "agents": [],
        "approvals": [],
        "provider_actions": [],
        "provider_attempts": [],
        "session_capsules": [],
        "feedback_events": [],
        "ci_rescues": [],
        "cache_entries": [],
        "skill_locks": [],
        "plugin_manifests": [],
        "guardrail_events": [],
        "parallel_conflicts": [],
        "semantic_merge_reviews": [],
        "provider_learning": [],
        "multi_repo_orchestrations": [],
        "memory_context": [],
        "artifacts": [],
        "test_results": [],
        "review_blockers": [],
        "checkpoints": [],
        "errors": [],
        "usage": [],
        "stage_contracts": [],
        "evidence_bundles": [],
        "evidence_items": [],
        "evidence_scorecard": None,
        "evidence_scorecards": [],
        "ledger_events": [],
        "snapshots": [],
        "validator_panels": [],
        "trace": [],
        "summary": {
            "dashboard_status": "ready",
            "progress": 0,
            "stage_total": 0,
            "stage_done": 0,
            "pending_approvals": 0,
            "pending_provider_actions": 0,
            "errors": 0,
            "blockers": 0,
            "tokens": 0,
            "cost_usd": 0.0,
            "terminal": False,
        },
    }
    payload["ux"] = build_task_ux_summary(payload)
    return payload


def build_run_dashboard_payload(workspace: Path, run_dir: Path, run_id: str, blackboard: Blackboard) -> dict[str, Any]:
    """Collect all blackboard, trace, and provider data for one task dashboard."""
    run = blackboard.get_run(run_id)
    stages = blackboard.table_rows("stages", run_id=run_id)
    approvals = blackboard.table_rows("approvals", run_id=run_id)
    provider_actions = blackboard.list_provider_actions(run_id=run_id)
    provider_attempts = blackboard.table_rows("provider_attempts", run_id=run_id)
    usage = blackboard.table_rows("usage_records", run_id=run_id)
    blockers = blackboard.table_rows("review_blockers", run_id=run_id)
    errors = blackboard.table_rows("error_details", run_id=run_id)
    scorecards = blackboard.table_rows("evidence_scorecards", run_id=run_id)
    payload = {
        "app": _app_payload(workspace),
        "run": run,
        "stages": stages,
        "agents": blackboard.table_rows("agents", run_id=run_id),
        "approvals": approvals,
        "provider_actions": provider_actions,
        "provider_attempts": provider_attempts,
        "session_capsules": blackboard.table_rows("session_capsules", run_id=run_id),
        "feedback_events": blackboard.table_rows("feedback_events", run_id=run_id),
        "ci_rescues": blackboard.table_rows("ci_rescues", run_id=run_id),
        "cache_entries": blackboard.table_rows("cache_entries", run_id=run_id),
        "skill_locks": blackboard.table_rows("skill_locks", run_id=run_id),
        "plugin_manifests": blackboard.table_rows("plugin_manifests", run_id=run_id),
        "guardrail_events": blackboard.table_rows("guardrail_events", run_id=run_id),
        "parallel_conflicts": blackboard.list_parallel_conflicts(run_id=run_id),
        "semantic_merge_reviews": blackboard.list_semantic_merge_reviews(run_id=run_id),
        "provider_learning": blackboard.list_provider_learning(),
        "multi_repo_orchestrations": blackboard.list_multi_repo_orchestrations(),
        "memory_context": _memory_context(run_dir),
        "artifacts": blackboard.table_rows("artifacts", run_id=run_id),
        "test_results": blackboard.table_rows("test_results", run_id=run_id),
        "review_blockers": blockers,
        "checkpoints": blackboard.table_rows("checkpoints", run_id=run_id),
        "errors": errors,
        "usage": usage,
        "stage_contracts": blackboard.table_rows("stage_contracts", run_id=run_id),
        "evidence_bundles": blackboard.table_rows("evidence_bundles", run_id=run_id),
        "evidence_items": blackboard.table_rows("evidence_items", run_id=run_id),
        "evidence_scorecard": scorecards[-1] if scorecards else None,
        "evidence_scorecards": scorecards,
        "ledger_events": blackboard.table_rows("ledger_events", run_id=run_id),
        "snapshots": blackboard.table_rows("snapshots", run_id=run_id),
        "validator_panels": blackboard.table_rows("validator_panels", run_id=run_id),
        "trace": compact_trace(read_trace(run_dir))[-50:],
        "summary": _summary(stages, approvals, provider_actions, usage, blockers, errors, run),
    }
    payload["ux"] = build_task_ux_summary(payload)
    return payload


def write_run_dashboard(
    workspace: Path,
    run_dir: Path,
    run_id: str,
    *,
    blackboard: Blackboard | None = None,
    output: Path | None = None,
    record_artifact: bool = True,
) -> Path:
    """Write the task dashboard for a run and optionally record it as an artifact."""
    owns_blackboard = blackboard is None
    board = blackboard or Blackboard(run_dir)
    try:
        path = write_dashboard(output or dashboard_path(run_dir), build_run_dashboard_payload(workspace, run_dir, run_id, board))
        if record_artifact and not _has_dashboard_artifact(board, path):
            board.add_artifact(run_id, None, path.name, path, "dashboard")
        return path
    finally:
        if owns_blackboard:
            board.close()


def _app_payload(workspace: Path) -> dict[str, Any]:
    return {
        "name": "muxdev",
        "version": __version__,
        "workspace": str(workspace),
        "providers": _provider_summary(workspace),
    }


def _provider_summary(workspace: Path) -> dict[str, Any]:
    """Return provider health without launching provider CLI probes.

    The live dashboard refreshes frequently, so this path must stay read-only and
    side-effect free. Provider probing is performed by setup/provider commands
    and cached under the muxdev home; dashboard rendering only consumes that
    cache or falls back to configured provider names.
    """
    cached = _provider_summary_from_cache(provider_cache_path())
    if cached is not None:
        return cached
    return _provider_summary_from_config(workspace)


def _provider_summary_from_cache(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, list):
        return None

    rows = [row for row in data if isinstance(row, dict)]
    ready = _providers_with_status(rows, "ready")
    partial = _providers_with_status(rows, "partial")
    return {
        "ready": ready,
        "partial": partial,
        "total": len(rows),
        "source": str(path),
        "cached": True,
    }


def _provider_summary_from_config(workspace: Path) -> dict[str, Any]:
    try:
        config = load_runtime_config(workspace)
    except Exception:
        return {"ready": [], "partial": [], "total": 0, "source": "config", "cached": False}
    cli = config.get("cli", {})
    providers = [name for name, data in cli.items() if name != "fallback" and isinstance(data, dict)] if isinstance(cli, dict) else []
    return {
        "ready": ["mock"] if "mock" in providers else [],
        "partial": [],
        "total": len(providers),
        "source": "config",
        "cached": False,
    }


def _providers_with_status(rows: list[dict[str, Any]], status: str) -> list[str]:
    return [str(row["provider"]) for row in rows if row.get("provider") and str(row.get("status")) == status]


def _memory_context(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "task_context.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    automation = data.get("automation", {}) if isinstance(data, dict) else {}
    memory = automation.get("memory_context", []) if isinstance(automation, dict) else []
    return [row for row in memory if isinstance(row, dict)]


def _summary(
    stages: list[dict[str, Any]],
    approvals: list[dict[str, Any]],
    provider_actions: list[dict[str, Any]],
    usage: list[dict[str, Any]],
    blockers: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    run: dict[str, Any],
) -> dict[str, Any]:
    total = len(stages)
    done = sum(1 for row in stages if row.get("status") in {"completed", "skipped"})
    pending = sum(1 for row in approvals if row.get("status") == str(ApprovalStatus.PENDING))
    pending_actions = sum(1 for row in provider_actions if row.get("status") == str(ProviderActionStatus.PENDING))
    status = str(run.get("status", "ready"))
    return {
        "dashboard_status": "awaiting_provider_action" if pending_actions else ("needs_approval" if pending else status),
        "progress": int((done / total) * 100) if total else 0,
        "stage_total": total,
        "stage_done": done,
        "pending_approvals": pending,
        "pending_provider_actions": pending_actions,
        "errors": len(errors),
        "blockers": len(blockers),
        "tokens": sum(int(row.get("tokens") or 0) for row in usage),
        "cost_usd": round(sum(float(row.get("cost_usd") or 0) for row in usage), 6),
        "terminal": status in TERMINAL_RUN_STATES,
    }


def _has_dashboard_artifact(blackboard: Blackboard, path: Path) -> bool:
    target = str(path)
    return any(row.get("kind") == "dashboard" and row.get("path") == target for row in blackboard.table_rows("artifacts"))
