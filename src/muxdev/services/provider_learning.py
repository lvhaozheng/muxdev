"""Cross-run provider learning snapshots."""

from __future__ import annotations

from typing import Any

from ..storage import Blackboard
from .provider_scores import build_provider_scores


def refresh_provider_learning(blackboard: Blackboard, *, run_id: str | None = None, role: str | None = None) -> list[dict[str, Any]]:
    """Persist score aggregates so routing can learn across runs."""
    scores = build_provider_scores(blackboard, role=role)
    for row in scores:
        blackboard.upsert_provider_learning(
            provider=str(row.get("provider") or "unknown"),
            role=str(row.get("role") or "any"),
            run_id=run_id,
            attempts=int(row.get("attempts") or 0),
            successes=int(row.get("successes") or 0),
            failures=int(row.get("failures") or 0),
            human_actions=int(row.get("human_actions") or 0),
            score=float(row.get("score") or 0.0),
            metadata={
                "success_rate": row.get("success_rate"),
                "retry_rate": row.get("retry_rate"),
                "human_intervention_rate": row.get("human_intervention_rate"),
                "last_failure_kind": row.get("last_failure_kind"),
            },
        )
    return blackboard.list_provider_learning(role=role)
