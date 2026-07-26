"""Readiness primitives for interactive coding CLI startup."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .terminal import TerminalBackend


_ANSI_LAYOUT_ESCAPE = re.compile(
    r"(?:\x1B\[|\x9B)[0-?]*[ -/]*[ABCDEFGHJKSTf]"
)
_ANSI_ESCAPE = re.compile(
    r"(?:"
    r"\x1B(?:"
    r"\][^\x07\x1B]*(?:\x07|\x1B\\)"
    r"|[PX^_][^\x1B]*(?:\x1B\\)"
    r"|\[[0-?]*[ -/]*[@-~]"
    r"|[ -/]*[@-~]"
    r")"
    r"|\x9B[0-?]*[ -/]*[@-~]"
    r"|\x9D[^\x07\x9C]*(?:\x07|\x9C)"
    r"|\x90[^\x9C]*(?:\x9C)"
    r")"
)
_INCOMPLETE_ANSI_ESCAPE = re.compile(
    r"(?:\x1B(?:\[[0-?]*[ -/]*|\][^\x07]*|[PX^_][^\x1B]*)|\x9B[0-?]*[ -/]*)\Z"
)
# A CLI can echo a transcript after first removing ESC. These are no longer
# control sequences and xterm correctly renders them as text, so clean the
# conservative set of terminal fragments that MuxDev itself may have persisted.
_ORPHAN_ANSI_LAYOUT = re.compile(
    r"(?<!\x1B)\[(?:\d{0,5}(?:;\d{0,5})*)?[ABCDEFGHJKSTf]"
)
_ORPHAN_ANSI_ESCAPE = re.compile(
    r"(?<!\x1B)\[(?:"
    r"\?(?:\d{1,5})(?:;\d{1,5})*[hl]"
    r"|(?:\d{0,3}(?:[;:]\d{0,3})*)?m"
    r"|(?:\d{0,5}(?:;\d{0,5})*)?[@LMPRXZa-dg-ln-qs-u]"
    r")"
)
_PLAIN_TEXT_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_TRANSIENT_TUI_LINE = re.compile(
    r"(?i)(?:"
    r"usage limit resets? available|Run /usage to use one"
    r"|Would you like to run the following command\?"
    r"|Press enter to confirm or esc to cancel"
    r"|Yes, and don'?t ask again for commands"
    r"|^\s*status:\s*(?:completed|processing|waiting_for_user)"
    r"(?:\s*[·•]\s*tokens:\s*\d+)?\s*$"
    r")"
)
_BUFFER_LIMIT = 65536
_SUMMARY_LINE_LIMIT = 240
_GENERIC_QUIET_SECONDS = 0.35
_READY_GRACE_SECONDS = 0.75
_BRACKETED_PASTE_START = "\x1b[200~"
_BRACKETED_PASTE_END = "\x1b[201~"
_BRACKETED_PASTE_SETTLE_SECONDS = 0.3


class StartupTimeout(RuntimeError):
    pass


class StartupProcessExited(RuntimeError):
    pass


@dataclass
class StartupReadiness:
    ready_pattern: re.Pattern[str] | None = None
    blocking_pattern: re.Pattern[str] | None = None
    condition: threading.Condition = field(default_factory=threading.Condition)
    buffer: str = ""
    last_output_at: float = 0.0
    ready_seen_at: float = 0.0

    def reset(self) -> None:
        """Start a fresh readiness window after runtime input or interruption."""
        with self.condition:
            self.buffer = ""
            self.last_output_at = 0.0
            self.ready_seen_at = 0.0
            self.condition.notify_all()

    def feed(self, normalized_output: str) -> None:
        with self.condition:
            self.buffer = (self.buffer + normalized_output)[-_BUFFER_LIMIT:]
            self.last_output_at = time.monotonic()
            self.condition.notify_all()

    def wait(self, backend: TerminalBackend, timeout_seconds: float) -> None:
        if backend.backend_name == "pipe":
            return
        deadline = time.monotonic() + timeout_seconds
        while True:
            now = time.monotonic()
            if not backend.snapshot().running:
                raise StartupProcessExited
            with self.condition:
                blocked = bool(
                    self.blocking_pattern
                    and self.blocking_pattern.search(self.buffer)
                )
                ready = bool(
                    self.ready_pattern
                    and self.ready_pattern.search(self.buffer)
                )
                if ready and not self.ready_seen_at:
                    self.ready_seen_at = now
                if (
                    ready
                    and not blocked
                    and now - self.ready_seen_at >= _READY_GRACE_SECONDS
                ):
                    return
                if (
                    self.ready_pattern is None
                    and self.buffer
                    and not blocked
                    and now - self.last_output_at >= _GENERIC_QUIET_SECONDS
                ):
                    return
                remaining = deadline - now
                if remaining <= 0:
                    raise StartupTimeout
                self.condition.wait(timeout=min(0.1, remaining))


def normalize_terminal_output(data: str) -> str:
    normalized = _ANSI_ESCAPE.sub("", data)
    return _INCOMPLETE_ANSI_ESCAPE.sub("", normalized)


def terminal_output_to_text(data: str, *, max_chars: int = 12000) -> str:
    """Convert terminal repaint traffic into bounded text safe for a CLI prompt.

    This intentionally differs from browser replay: live output must retain ANSI
    so xterm can render it. Only reconstructed context uses this plain-text copy.
    """
    rendered = _ANSI_LAYOUT_ESCAPE.sub("\n", data)
    rendered = normalize_terminal_output(rendered)
    rendered = _ORPHAN_ANSI_LAYOUT.sub("\n", rendered)
    rendered = _ORPHAN_ANSI_ESCAPE.sub("", rendered)
    rendered = rendered.replace("\r\n", "\n").replace("\r", "\n")

    characters: list[str] = []
    for character in rendered:
        if character == "\b":
            if characters and characters[-1] != "\n":
                characters.pop()
            continue
        if character in {"\n", "\t"} or (
            ord(character) >= 32 and character != "\x7f"
        ):
            characters.append(character)
    rendered = _PLAIN_TEXT_CONTROL.sub("", "".join(characters))

    # TUI applications repaint identical rows frequently. Keep the last copy of
    # consecutive rows so a daemon restart does not amplify an entire screen.
    lines: list[str] = []
    for raw_line in rendered.splitlines():
        line = raw_line.rstrip()
        if _TRANSIENT_TUI_LINE.search(line):
            continue
        if not line:
            if lines and lines[-1]:
                lines.append("")
            continue
        if lines and line == lines[-1]:
            continue
        lines.append(line)
    while lines and not lines[-1]:
        lines.pop()
    compact = "\n".join(lines[-_SUMMARY_LINE_LIMIT:])
    return compact[-max(0, max_chars):]


def submit_terminal_prompt(
    backend: TerminalBackend,
    data: str,
    *,
    transport: str,
    record: Callable[[str], None],
) -> None:
    """Record and submit one logical prompt without multiline Enter events."""
    logical = data.rstrip("\r\n")
    transcript_payload = logical + "\n"
    record(transcript_payload)
    if transport == "bracketed_paste":
        backend.write(_BRACKETED_PASTE_START + logical + _BRACKETED_PASTE_END)
        # Codex converts large pastes into an asynchronous pending-paste
        # element. On Windows ConPTY, Enter can arrive before that element is
        # committed and is then ignored, leaving "[Pasted Content N chars]" in
        # the composer while MuxDev incorrectly appears to be running.
        time.sleep(_BRACKETED_PASTE_SETTLE_SECONDS)
        backend.write("\r")
    else:
        backend.write(transcript_payload)


__all__ = [
    "StartupProcessExited",
    "StartupReadiness",
    "StartupTimeout",
    "normalize_terminal_output",
    "submit_terminal_prompt",
    "terminal_output_to_text",
]
