"""Feedback router and CI rescue planning."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..storage import Blackboard
from .cas_cache import CasCache


ROUTES = {
    "local_test_failure": {"route_to": "test", "auto": True, "workflow": "fix"},
    "ci_failed": {"route_to": "test", "auto": True, "workflow": "fix"},
    "github_pr_comment": {"route_to": "code", "auto": True, "workflow": "dev"},
    "review_comment": {"route_to": "code", "auto": True, "workflow": "dev"},
    "issue_comment": {"route_to": "plan", "auto": False, "workflow": "dev"},
    "manual_feedback": {"route_to": "plan", "auto": False, "workflow": "dev"},
    "security_blocker": {"route_to": "secure", "auto": False, "workflow": "dev"},
}


@dataclass(frozen=True)
class RoutedFeedback:
    feedback_id: str
    kind: str
    source: str
    route_to: str
    auto: bool
    status: str
    task: str
    workflow: str
    cache: dict[str, Any]
    rescue_id: str | None = None
    rescue_run_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "feedback_id": self.feedback_id,
            "kind": self.kind,
            "source": self.source,
            "route_to": self.route_to,
            "auto": self.auto,
            "status": self.status,
            "task": self.task,
            "workflow": self.workflow,
            "cache": self.cache,
            "rescue_id": self.rescue_id,
            "rescue_run_id": self.rescue_run_id,
        }


def route_feedback(
    workspace: Path,
    blackboard: Blackboard,
    *,
    kind: str,
    source: str,
    content: str,
    run_id: str | None = None,
    severity: str = "medium",
    payload: dict[str, Any] | None = None,
) -> RoutedFeedback:
    """Persist feedback, choose a role route, and cache the normalized event."""
    rule = ROUTES.get(kind, {"route_to": "plan", "auto": False, "workflow": "dev"})
    route_to = str(rule["route_to"])
    auto = bool(rule["auto"])
    status = "routed" if auto else "needs_review"
    feedback_id = blackboard.add_feedback_event(
        run_id=run_id,
        source=source,
        kind=kind,
        severity=severity,
        status=status,
        route_to=route_to,
        content=content,
        payload=payload or {},
    )
    task = _task_text(kind=kind, source=source, content=content, route_to=route_to, run_id=run_id)
    cache = CasCache(workspace).put_json(
        kind="feedback_event",
        payload={"feedback_id": feedback_id, "kind": kind, "source": source, "content": content, "run_id": run_id, "route_to": route_to},
        metadata={"severity": severity},
    )
    blackboard.add_cache_entry(
        cache_key=str(cache["cache_key"]),
        run_id=run_id,
        kind="feedback_event",
        path=Path(str(cache["path"])),
        value_hash=str(cache["value_hash"]),
        metadata=cache.get("metadata", {}),
    )
    rescue_id = None
    if kind in {"ci_failed", "local_test_failure"}:
        rescue_id = blackboard.add_ci_rescue(
            feedback_id=feedback_id,
            run_id=run_id,
            rescue_run_id=None,
            route_to=route_to,
            status="planned" if auto else "needs_review",
            summary=task,
        )
    return RoutedFeedback(
        feedback_id=feedback_id,
        kind=kind,
        source=source,
        route_to=route_to,
        auto=auto,
        status=status,
        task=task,
        workflow=str(rule["workflow"]),
        cache=cache,
        rescue_id=rescue_id,
    )


def _task_text(*, kind: str, source: str, content: str, route_to: str, run_id: str | None) -> str:
    prefix = "CI rescue" if kind in {"ci_failed", "local_test_failure"} else "Feedback routed"
    target = f" for run {run_id}" if run_id else ""
    return f"{prefix}{target}: route {kind} from {source} to {route_to}.\n\nFeedback:\n{content}"
