"""Provider text normalization for display and action detection."""

from __future__ import annotations

import json
import re
from typing import Any


REQUESTS_WARNING_RE = re.compile(
    r"^.*requests[\\/]+__init__\.py:\d+:\s*RequestsDependencyWarning:.*(?:\n\s*warnings\.warn\(.*)?$",
    re.IGNORECASE | re.MULTILINE,
)
JSON_STATUS_RE = re.compile(r'\{[^\n{}]*"exit_code"\s*:\s*0[^\n{}]*"status"\s*:\s*"completed"[^\n{}]*\}', re.IGNORECASE)
EXTERNAL_CONFIRMATION_RE = re.compile(r"(^|\n)\s*waiting_external_confirmation\s*[:：]\s*", re.IGNORECASE)
MOJIBAKE_MARKERS_RE = re.compile(r"[ÃÂ]|(?:[äåæçèé][\x80-\xff]*)|�|[\ue000-\uf8ff]")
CONTEXT_DUMP_MARKERS = (
    '"provider_action_responses"',
    "provider_action_responses",
    '"context_packet_hash"',
    "context_packet_hash",
    '"workflow"',
    '"worktree"',
    '"task_id"',
    "# muxdev context packet",
)
RUNTIME_NOISE_MARKERS = (
    "requestdependencywarning",
    "charset_normalizer",
    "warnings.warn",
    '"exit_code"',
    '"status":"completed"',
    '"status": "completed"',
)


def clean_provider_text(value: object, *, fallback: str = "") -> str:
    """Return a short, user-facing provider text string."""
    raw = "" if value is None else str(value)
    text = _provider_visible_text(raw, allow_raw=True)
    text = _normalize_text(text)
    if _looks_like_runtime_noise(text):
        return fallback
    return text or fallback


def provider_action_text(value: object) -> str:
    """Return text that is safe to scan for provider actions."""
    raw = "" if value is None else str(value)
    text = _provider_visible_text(raw, allow_raw=False)
    text = _normalize_text(text)
    if _looks_like_runtime_noise(text):
        return ""
    return text


def has_external_confirmation(text: object) -> bool:
    return bool(EXTERNAL_CONFIRMATION_RE.search(provider_action_text(text)))


def is_false_positive_provider_action(row: dict[str, Any]) -> bool:
    """Detect historical provider actions created from context dumps or warnings."""
    prompt = str(row.get("prompt_text") or "")
    cleaned = clean_provider_text(prompt)
    if not cleaned:
        return True
    raw_lc = prompt.lower()
    cleaned_lc = cleaned.lower()
    if any(marker in raw_lc for marker in RUNTIME_NOISE_MARKERS):
        return True
    if any(marker in raw_lc for marker in CONTEXT_DUMP_MARKERS) and not EXTERNAL_CONFIRMATION_RE.search(cleaned):
        return True
    if len(cleaned) > 600 and any(marker in cleaned_lc for marker in CONTEXT_DUMP_MARKERS):
        return True
    return False


def _provider_visible_text(text: str, *, allow_raw: bool) -> str:
    agent_messages = _codex_agent_messages(text)
    if agent_messages:
        return "\n".join(agent_messages)
    if _looks_like_json_stream(text):
        return "" if not allow_raw else text
    return text


def _codex_agent_messages(text: str) -> list[str]:
    messages: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        item = _json_loads(line)
        if isinstance(item, dict):
            message = _agent_message_text(item)
            if message:
                messages.append(message)
    if messages:
        return messages
    item = _json_loads(text.strip())
    if isinstance(item, dict):
        message = _agent_message_text(item)
        return [message] if message else []
    return []


def _agent_message_text(item: dict[str, Any]) -> str:
    nested = item.get("item")
    if isinstance(nested, dict):
        item_type = str(nested.get("type") or "")
        if item_type in {"agent_message", "assistant_message", "message"} and isinstance(nested.get("text"), str):
            return str(nested["text"])
    item_type = str(item.get("type") or "")
    if item_type in {"agent_message", "assistant_message", "message"} and isinstance(item.get("text"), str):
        return str(item["text"])
    return ""


def _json_loads(text: str) -> object | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _looks_like_json_stream(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    json_like = 0
    for line in lines[:20]:
        if (line.startswith("{") and line.endswith("}")) or (line.startswith("[") and line.endswith("]")):
            json_like += 1
    return json_like > 0 and json_like >= max(1, len(lines[:20]) // 2)


def _normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _decode_escaped_newlines(text)
    text = REQUESTS_WARNING_RE.sub("", text)
    text = JSON_STATUS_RE.sub("", text)
    text = _repair_mojibake(text)
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()


def _decode_escaped_newlines(text: str) -> str:
    return (
        text.replace("\\\\r\\\\n", "\n")
        .replace("\\r\\n", "\n")
        .replace("\\\\n", "\n")
        .replace("\\n", "\n")
        .replace('\\"', '"')
    )


def _looks_like_runtime_noise(text: str) -> bool:
    if not text.strip():
        return True
    lowered = text.lower()
    if any(marker in lowered for marker in RUNTIME_NOISE_MARKERS):
        return True
    if any(marker in lowered for marker in CONTEXT_DUMP_MARKERS):
        return True
    return False


def _repair_mojibake(text: str) -> str:
    best = text
    best_score = _readability_score(text)
    for _ in range(2):
        improved = False
        for candidate in _mojibake_candidates(best):
            score = _readability_score(candidate)
            if score > best_score + 2:
                best = candidate
                best_score = score
                improved = True
        if not improved:
            break
    return best


def _mojibake_candidates(text: str) -> list[str]:
    if not MOJIBAKE_MARKERS_RE.search(text):
        return []
    candidates: list[str] = []
    for encoding in ("latin1", "cp1252", "gbk", "cp936"):
        try:
            candidates.append(text.encode(encoding).decode("utf-8"))
        except (UnicodeEncodeError, UnicodeDecodeError, LookupError):
            continue
    return candidates


def _readability_score(text: str) -> int:
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    ascii_letters = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    suspicious = len(re.findall(r"[ÃÂ]|[äåæçèé]|�|[\ue000-\uf8ff]", text))
    controls = sum(1 for ch in text if ord(ch) < 32 and ch not in "\n\t")
    return cjk * 3 + ascii_letters // 4 - suspicious * 4 - controls * 10
