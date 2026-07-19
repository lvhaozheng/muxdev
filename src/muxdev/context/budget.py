"""Deterministic, provider-aware context selection with an auditable omission manifest."""

from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass
from ..storage.contracts import canonical_hash


@dataclass(frozen=True)
class ContextBudget:
    max_input_tokens: int
    reserved_output_tokens: int

    @property
    def available_tokens(self) -> int:
        return max(256, self.max_input_tokens - self.reserved_output_tokens)


_PROVIDER_DEFAULTS = {
    "codex": ContextBudget(32_000, 8_000),
    "claude": ContextBudget(32_000, 8_000),
    "claude-code": ContextBudget(32_000, 8_000),
    "qwen": ContextBudget(24_000, 6_000),
}

_LIST_PATHS: tuple[tuple[str, ...], ...] = (
    ("session", "temporary_context"),
    ("task", "previous_attempts"),
    ("task", "provider_action_responses"),
    ("run", "task_memory"),
    ("run", "review_blockers"),
    ("run", "feedback_events"),
    ("run", "artifacts"),
    ("run", "upstream_artifacts"),
    ("rag_context",),
    ("branch", "feature_memory"),
    ("project", "long_term_memory"),
    ("project", "workspace_memory"),
    ("project", "review_required"),
    ("user_preferences", "memory"),
)


def apply_context_budget(packet: dict[str, object], *, provider: str, automation: dict[str, object]) -> dict[str, object]:
    budget = _budget_for(provider, automation)
    original_hash = canonical_hash(packet)
    result = copy.deepcopy(packet)
    candidates: list[tuple[int, int, tuple[str, ...], object, bool]] = []
    order = 0
    total_items = 0
    for path in _LIST_PATHS:
        values = _get_path(result, path)
        if not isinstance(values, list):
            continue
        _set_path(result, path, [])
        for value in values:
            mandatory = _mandatory(path, value)
            candidates.append((_priority(path, value), order, path, value, mandatory))
            order += 1
            total_items += 1

    used = estimate_tokens(result)
    included = 0
    omitted: list[dict[str, object]] = []
    mandatory_overflow = False
    for priority, _, path, value, mandatory in sorted(candidates, key=lambda item: (not item[4], item[0], item[1])):
        cost = estimate_tokens(value)
        if mandatory or used + cost <= budget.available_tokens:
            target = _get_path(result, path)
            if isinstance(target, list):
                target.append(value)
            used += cost
            included += 1
            if mandatory and used > budget.available_tokens:
                mandatory_overflow = True
            continue
        omitted.append(
            {
                "path": ".".join(path),
                "item_ref": _item_ref(value),
                "item_hash": canonical_hash({"value": value}),
                "estimated_tokens": cost,
                "priority": priority,
                "reason": "provider_input_budget_exhausted",
            }
        )

    rag_total = sum(1 for _, _, path, _, _ in candidates if path == ("rag_context",))
    rag_included = len(_get_path(result, ("rag_context",)) or [])
    result["context_budget"] = {
        "contract_version": "muxdev.context_budget.v1",
        "provider": provider,
        "max_input_tokens": budget.max_input_tokens,
        "reserved_output_tokens": budget.reserved_output_tokens,
        "available_tokens": budget.available_tokens,
        "estimated_tokens": used,
        "estimator": "cjk-1_ascii-4-v1",
        "original_packet_hash": original_hash,
        "included_items": included,
        "total_items": total_items,
        "compression_ratio": round(included / total_items, 4) if total_items else 1.0,
        "mandatory_overflow": mandatory_overflow,
        "omitted": omitted,
        "citation_coverage": {
            "rag_included": rag_included,
            "rag_total": rag_total,
            "ratio": round(rag_included / rag_total, 4) if rag_total else 1.0,
        },
        "rebuild": "all selected and omitted items retain source references or content hashes; the packet is a derived projection",
    }
    result["context_budget"]["estimated_tokens_with_audit"] = estimate_tokens(result)  # type: ignore[index]
    return result


def estimate_tokens(value: object) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    cjk = len(re.findall(r"[\u3400-\u9fff\uf900-\ufaff]", text))
    return max(1, cjk + math.ceil((len(text) - cjk) / 4))


def _budget_for(provider: str, automation: dict[str, object]) -> ContextBudget:
    default = _PROVIDER_DEFAULTS.get(provider.lower(), ContextBudget(24_000, 6_000))
    configured = automation.get("context_budget") if isinstance(automation, dict) else None
    if not isinstance(configured, dict):
        return default
    provider_config = configured.get(provider)
    values = provider_config if isinstance(provider_config, dict) else configured
    maximum = int(values.get("max_input_tokens") or default.max_input_tokens)
    reserved = int(values.get("reserved_output_tokens") or default.reserved_output_tokens)
    if maximum <= 0 or reserved < 0 or reserved >= maximum:
        return default
    return ContextBudget(maximum, reserved)


def _mandatory(path: tuple[str, ...], value: object) -> bool:
    if path in {("task", "provider_action_responses"), ("run", "review_blockers"), ("project", "review_required")}:
        return True
    if path == ("run", "feedback_events") and isinstance(value, dict):
        return str(value.get("severity") or "").lower() == "high" or str(value.get("status") or "").lower() == "pending"
    return False


def _priority(path: tuple[str, ...], value: object) -> int:
    base = {
        ("session", "temporary_context"): 10,
        ("run", "task_memory"): 20,
        ("run", "feedback_events"): 25,
        ("task", "previous_attempts"): 30,
        ("rag_context",): 40,
        ("run", "artifacts"): 50,
        ("run", "upstream_artifacts"): 55,
        ("branch", "feature_memory"): 60,
        ("project", "long_term_memory"): 70,
        ("project", "workspace_memory"): 75,
        ("user_preferences", "memory"): 80,
    }.get(path, 0)
    if isinstance(value, dict) and str(value.get("severity") or "").lower() == "high":
        base -= 15
    return base


def _item_ref(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in ("id", "memory_id", "action_id", "feedback_id", "stage_id", "path", "name"):
        if value.get(key):
            return f"{key}:{value[key]}"
    return None


def _get_path(packet: dict[str, object], path: tuple[str, ...]) -> object:
    current: object = packet
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _set_path(packet: dict[str, object], path: tuple[str, ...], value: object) -> None:
    current: dict[str, object] = packet
    for part in path[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    current[path[-1]] = value
