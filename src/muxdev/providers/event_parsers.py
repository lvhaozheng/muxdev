"""Provider-specific JSONL normalization without claiming generic conformance."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NormalizedProviderEvent:
    type: str
    payload: dict[str, object]


class ProviderEventParser:
    provider = "generic"

    def parse(self, output: str, *, returncode: int) -> list[NormalizedProviderEvent]:
        events: list[NormalizedProviderEvent] = []
        for line_number, line in enumerate(output.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                events.append(NormalizedProviderEvent("provider.output", {"line": line_number, "text": line[:1000]}))
                continue
            if not isinstance(payload, dict):
                events.append(NormalizedProviderEvent("provider.output", {"line": line_number, "value": payload}))
                continue
            events.append(self.normalize(payload, line_number=line_number))
        if not events:
            events.append(NormalizedProviderEvent("provider.terminal", {"returncode": returncode}))
        if returncode:
            events.append(NormalizedProviderEvent("provider.error_classified", classify_provider_error(output, returncode=returncode)))
        return events

    def normalize(self, payload: dict[str, Any], *, line_number: int) -> NormalizedProviderEvent:
        return NormalizedProviderEvent("provider.event", {"line": line_number, "event": payload})

    def session_id(self, output: str) -> str | None:
        for line in output.splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                value = payload.get("session_id") or payload.get("thread_id")
                if value:
                    return str(value)
        return None


class CodexEventParser(ProviderEventParser):
    provider = "codex"

    def normalize(self, payload: dict[str, Any], *, line_number: int) -> NormalizedProviderEvent:
        raw_type = str(payload.get("type") or "")
        mapping = {
            "thread.started": "provider.session_started",
            "turn.started": "provider.turn_started",
            "turn.completed": "provider.turn_completed",
            "turn.failed": "provider.error",
            "item.started": "provider.item_started",
            "item.completed": "provider.item_completed",
        }
        return NormalizedProviderEvent(mapping.get(raw_type, "provider.event"), {"line": line_number, "raw_type": raw_type, "event": payload})


class ClaudeEventParser(ProviderEventParser):
    provider = "claude-code"

    def normalize(self, payload: dict[str, Any], *, line_number: int) -> NormalizedProviderEvent:
        raw_type = str(payload.get("type") or "")
        mapping = {
            "system": "provider.session_started",
            "assistant": "provider.message",
            "user": "provider.tool_result",
            "result": "provider.turn_completed",
            "error": "provider.error",
        }
        return NormalizedProviderEvent(mapping.get(raw_type, "provider.event"), {"line": line_number, "raw_type": raw_type, "event": payload})


class QwenEventParser(ProviderEventParser):
    provider = "qwen"

    def normalize(self, payload: dict[str, Any], *, line_number: int) -> NormalizedProviderEvent:
        raw_type = str(payload.get("type") or payload.get("event") or "")
        lowered = raw_type.lower()
        if "tool" in lowered:
            normalized = "provider.tool"
        elif lowered in {"assistant", "message", "content"}:
            normalized = "provider.message"
        elif lowered in {"result", "complete", "completed"}:
            normalized = "provider.turn_completed"
        elif "error" in lowered:
            normalized = "provider.error"
        else:
            normalized = "provider.event"
        return NormalizedProviderEvent(normalized, {"line": line_number, "raw_type": raw_type, "event": payload})


def parser_for(provider: str) -> ProviderEventParser:
    normalized = provider.lower()
    if normalized == "codex":
        return CodexEventParser()
    if normalized in {"claude", "claude-code"}:
        return ClaudeEventParser()
    if normalized == "qwen":
        return QwenEventParser()
    return ProviderEventParser()


def classify_provider_error(output: str, *, returncode: int) -> dict[str, object]:
    lowered = output.lower()
    patterns = (
        ("auth_required", r"unauthorized|not logged in|login required|authentication"),
        ("rate_limit", r"rate.?limit|too many requests|\b429\b|quota"),
        ("context_limit", r"context (window|length)|too many tokens|max(?:imum)? tokens"),
        ("timeout", r"timed? out|deadline exceeded|idle timeout"),
        ("transient", r"temporar|connection reset|service unavailable|\b50[234]\b"),
    )
    for kind, pattern in patterns:
        if re.search(pattern, lowered):
            return {"kind": kind, "returncode": returncode, "retryable": kind in {"rate_limit", "timeout", "transient"}}
    return {"kind": "provider_exit", "returncode": returncode, "retryable": False}
