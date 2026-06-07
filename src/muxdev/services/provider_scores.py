"""Provider attempt scoring and conservative role routing."""

from __future__ import annotations

from typing import Any

from ..storage import Blackboard


def build_provider_scores(blackboard: Blackboard, *, role: str | None = None) -> list[dict[str, Any]]:
    """Aggregate provider reliability by role from persisted attempts/actions."""
    attempts = blackboard.table_rows("provider_attempts")
    actions = blackboard.table_rows("provider_actions")
    buckets: dict[tuple[str, str | None], dict[str, Any]] = {}
    for row in attempts:
        row_role = row.get("role")
        if role and row_role != role:
            continue
        key = (str(row.get("provider") or "unknown"), str(row_role) if row_role else None)
        bucket = buckets.setdefault(
            key,
            {
                "provider": key[0],
                "role": key[1],
                "attempts": 0,
                "successes": 0,
                "failures": 0,
                "retries": 0,
                "human_actions": 0,
                "last_failure_kind": None,
            },
        )
        bucket["attempts"] += 1
        if int(row.get("attempt") or 1) > 1:
            bucket["retries"] += 1
        status = str(row.get("status") or "")
        if status == "succeeded":
            bucket["successes"] += 1
        elif status in {"failed", "provider_action", "read_only_violation"}:
            bucket["failures"] += 1
            if row.get("failure_kind"):
                bucket["last_failure_kind"] = row.get("failure_kind")
    for row in actions:
        row_role = row.get("role")
        if role and row_role != role:
            continue
        key = (str(row.get("provider") or "unknown"), str(row_role) if row_role else None)
        bucket = buckets.setdefault(
            key,
            {
                "provider": key[0],
                "role": key[1],
                "attempts": 0,
                "successes": 0,
                "failures": 0,
                "retries": 0,
                "human_actions": 0,
                "last_failure_kind": None,
            },
        )
        bucket["human_actions"] += 1
    for bucket in buckets.values():
        attempts_count = max(int(bucket["attempts"]), 1)
        success_rate = float(bucket["successes"]) / attempts_count
        retry_rate = float(bucket["retries"]) / attempts_count
        human_rate = float(bucket["human_actions"]) / attempts_count
        failure_rate = float(bucket["failures"]) / attempts_count
        score = success_rate - (failure_rate * 0.25) - (human_rate * 0.2) - (retry_rate * 0.1)
        bucket["success_rate"] = round(success_rate, 4)
        bucket["retry_rate"] = round(retry_rate, 4)
        bucket["human_intervention_rate"] = round(human_rate, 4)
        bucket["score"] = round(max(0.0, min(1.0, score)), 4)
    return sorted(buckets.values(), key=lambda item: (float(item["score"]), int(item["attempts"])), reverse=True)


def recommend_provider(blackboard: Blackboard, *, role: str | None, fallback: str) -> tuple[str, dict[str, Any]]:
    """Choose a provider only when historical scores are clearly better."""
    scores = build_provider_scores(blackboard, role=role)
    fallback_score = next((float(row["score"]) for row in scores if row["provider"] == fallback), 0.0)
    for row in scores:
        if row["provider"] == fallback:
            continue
        if int(row["attempts"]) < 2:
            continue
        if float(row["score"]) >= fallback_score + 0.25:
            return str(row["provider"]), {"reason": "historical provider score", "selected": row, "fallback_score": fallback_score}
    return fallback, {"reason": "fallback provider", "fallback_score": fallback_score, "scores": scores[:3]}
