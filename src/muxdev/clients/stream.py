"""Stream event parser for provider subprocess output."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


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


class StreamAdapter:
    APPROVAL_PATTERNS = (
        re.compile(r"\[y/N\]", re.IGNORECASE),
        re.compile(r"approve\?", re.IGNORECASE),
        re.compile(r"confirm", re.IGNORECASE),
    )

    def parse_chunk(self, text: str) -> list[StreamEvent]:
        clean = ANSI_RE.sub("", text)
        events = [StreamEvent(StreamEventType.OUTPUT, clean)]
        lowered = clean.lower()
        if "auth" in lowered and ("error" in lowered or "login" in lowered):
            events.append(StreamEvent(StreamEventType.AUTH_ERROR, clean))
        if "rate limit" in lowered:
            events.append(StreamEvent(StreamEventType.RATE_LIMIT, clean))
        if any(pattern.search(clean) for pattern in self.APPROVAL_PATTERNS):
            events.append(StreamEvent(StreamEventType.APPROVAL_PROMPT_DETECTED, clean))
        elif "?" in clean and any(word in lowered for word in ("yes", "no", "continue", "proceed")):
            events.append(StreamEvent(StreamEventType.WAITING_EXTERNAL_CONFIRMATION, clean))
        return events

    def idle_timeout(self, seconds: float) -> StreamEvent:
        return StreamEvent(StreamEventType.IDLE_TIMEOUT, f"no output for {seconds:.1f}s")

    def cli_exited(self, returncode: int) -> StreamEvent:
        return StreamEvent(StreamEventType.CLI_EXITED, f"process exited with {returncode}")
