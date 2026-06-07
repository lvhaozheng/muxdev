"""Stream event parser for provider subprocess output."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from ..models import ProviderActionKind


ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class StreamEventType(StrEnum):
    OUTPUT = "output"
    APPROVAL_PROMPT_DETECTED = "approval_prompt_detected"
    AUTH_ERROR = "auth_error"
    RATE_LIMIT = "rate_limit"
    IDLE_TIMEOUT = "idle_timeout"
    CLI_EXITED = "cli_exited"
    WAITING_EXTERNAL_CONFIRMATION = "waiting_external_confirmation"


@dataclass(frozen=True)
class StreamEvent:
    type: StreamEventType
    text: str


@dataclass(frozen=True)
class ProviderActionRequest:
    kind: str
    prompt_text: str
    options: list[dict[str, object]]


class StreamAdapter:
    APPROVAL_PATTERNS = (
        re.compile(r"\[y/N\]", re.IGNORECASE),
        re.compile(r"\bapprove\?\s*$", re.IGNORECASE),
        re.compile(r"\b(?:apply|confirm|continue|proceed)\?\s*$", re.IGNORECASE),
        re.compile(r"\b(?:do you want|would you like).*\b(?:apply|approve|confirm|continue|proceed)\b", re.IGNORECASE),
    )

    def parse_chunk(self, text: str) -> list[StreamEvent]:
        clean = ANSI_RE.sub("", text)
        events = [StreamEvent(StreamEventType.OUTPUT, clean)]
        lowered = clean.lower()
        if "waiting_external_confirmation" in lowered:
            events.append(StreamEvent(StreamEventType.WAITING_EXTERNAL_CONFIRMATION, clean))
        if ("auth" in lowered and ("error" in lowered or "login" in lowered)) or "please sign in" in lowered:
            events.append(StreamEvent(StreamEventType.AUTH_ERROR, clean))
        if re.search(r"\brate[- ]limit(?:ed|s)?\b", lowered) or "too many requests" in lowered or "quota exceeded" in lowered:
            events.append(StreamEvent(StreamEventType.RATE_LIMIT, clean))
        if any(pattern.search(clean) for pattern in self.APPROVAL_PATTERNS):
            events.append(StreamEvent(StreamEventType.APPROVAL_PROMPT_DETECTED, clean))
        return events

    def provider_actions(self, events: list[StreamEvent]) -> list[ProviderActionRequest]:
        """Convert low-level stream events into user-visible provider actions."""
        actions: list[ProviderActionRequest] = []
        seen: set[tuple[str, str]] = set()
        for event in events:
            kind = _action_kind(event.type)
            if not kind:
                continue
            prompt = _prompt_excerpt(event.text)
            key = (kind, prompt)
            if key in seen:
                continue
            seen.add(key)
            actions.append(ProviderActionRequest(kind=kind, prompt_text=prompt, options=_options_for(kind, event.text)))
        return actions

    def idle_timeout(self, seconds: float) -> StreamEvent:
        return StreamEvent(StreamEventType.IDLE_TIMEOUT, f"no output for {seconds:.1f}s")

    def cli_exited(self, returncode: int) -> StreamEvent:
        return StreamEvent(StreamEventType.CLI_EXITED, f"process exited with {returncode}")


def _action_kind(event_type: StreamEventType) -> str | None:
    if event_type in {StreamEventType.APPROVAL_PROMPT_DETECTED, StreamEventType.WAITING_EXTERNAL_CONFIRMATION}:
        return str(ProviderActionKind.CLI_CONFIRMATION)
    if event_type == StreamEventType.AUTH_ERROR:
        return str(ProviderActionKind.AUTH_REQUIRED)
    if event_type == StreamEventType.RATE_LIMIT:
        return str(ProviderActionKind.RATE_LIMIT)
    if event_type == StreamEventType.IDLE_TIMEOUT:
        return str(ProviderActionKind.IDLE_TIMEOUT)
    return None


def _prompt_excerpt(text: str, *, max_chars: int = 1200) -> str:
    lines = [line.rstrip() for line in ANSI_RE.sub("", text).splitlines() if line.strip()]
    excerpt = "\n".join(lines[-8:]) if lines else ANSI_RE.sub("", text).strip()
    if len(excerpt) <= max_chars:
        return excerpt
    return excerpt[-max_chars:].lstrip()


def _options_for(kind: str, text: str) -> list[dict[str, object]]:
    if kind != str(ProviderActionKind.CLI_CONFIRMATION):
        return []
    if re.search(r"\[\s*y\s*/\s*n\s*\]", text, re.IGNORECASE):
        return [
            {"label": "Yes", "value": "y"},
            {"label": "No", "value": "n", "default": True},
        ]
    if re.search(r"\b(approve|apply|confirm|continue|proceed)\?\s*$", text, re.IGNORECASE) or re.search(
        r"\b(?:do you want|would you like).*\b(?:apply|approve|confirm|continue|proceed)\b",
        text,
        re.IGNORECASE,
    ):
        return [
            {"label": "Yes", "value": "yes"},
            {"label": "No", "value": "no"},
        ]
    return []
