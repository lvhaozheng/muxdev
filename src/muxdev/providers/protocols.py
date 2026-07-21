"""Provider-specific CLI invocation and output normalization.

The runtime speaks one :class:`StageExecutionInput` contract.  This module is
the anti-corruption layer that translates that contract to the deliberately
different headless protocols exposed by coding-agent CLIs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable


CLARIFICATION_PROTOCOL_PROMPT = (
    "# Clarification protocol\n"
    "If a material requirement is ambiguous, ask before acting by returning a JSON object "
    "with interaction_request: {question, options (2-4), recommended_option_id, "
    "allow_custom_input, blocking, risk, reason}. Use blocking=true for permissions, "
    "deletion, credentials, gate changes, or delivery acceptance. Otherwise the runtime "
    "waits 60 seconds and safely selects the recommended option. Do not modify files before asking."
)


@dataclass(frozen=True)
class CliInvocation:
    argv: tuple[str, ...]
    stdin: str | None
    transport: str


@dataclass(frozen=True)
class ParsedCliOutput:
    content: str
    protocol: str
    event_count: int
    session_id: str | None = None
    interaction_requests: tuple[dict[str, Any], ...] = ()


def build_cli_invocation(template: Iterable[str], prompt: str, *, transport: str) -> CliInvocation:
    """Bind exactly one prompt to an argv or stdin transport.

    Making transport explicit prevents a subtle adapter bug where a CLI flag
    such as ``-p`` was emitted but its prompt was accidentally sent to stdin.
    """
    argv_template = tuple(str(item) for item in template)
    placeholders = sum(item.count("{prompt}") for item in argv_template)
    if transport == "argument":
        if placeholders != 1:
            raise ValueError("argument prompt transport requires exactly one {prompt} placeholder")
        return CliInvocation(
            argv=tuple(item.replace("{prompt}", prompt) for item in argv_template),
            stdin=None,
            transport=transport,
        )
    if transport == "stdin":
        if placeholders:
            raise ValueError("stdin prompt transport cannot also contain a {prompt} placeholder")
        return CliInvocation(argv=argv_template, stdin=prompt, transport=transport)
    raise ValueError(f"unsupported prompt transport: {transport}")


def parse_cli_output(provider: str, stdout: str, stderr: str) -> ParsedCliOutput:
    """Normalize supported JSONL event streams without treating them as facts."""
    events = list(_json_lines(stdout))
    messages: list[str] = []
    session_id: str | None = None
    for event in events:
        session_id = session_id or _session_id(event)
        if provider == "codex":
            messages.extend(_codex_messages(event))
        elif provider == "claude-code":
            messages.extend(_claude_messages(event))
        else:
            messages.extend(_generic_messages(event))
    body = messages[-1] if messages else stdout
    interactions = _interaction_requests(events, body)
    if stderr:
        body += f"\n\n# stderr\n{stderr}"
    protocol = "jsonl" if events else "text"
    return ParsedCliOutput(
        content=body,
        protocol=f"{provider}.{protocol}",
        event_count=len(events),
        session_id=session_id,
        interaction_requests=interactions,
    )


def _interaction_requests(
    events: list[dict[str, Any]], body: str
) -> tuple[dict[str, Any], ...]:
    requests: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") not in {"muxdev.interaction.request", "interaction.requested"}:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
        requests.append(dict(payload))
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        single = payload.get("interaction_request")
        many = payload.get("interaction_requests")
        if isinstance(single, dict):
            requests.append(dict(single))
        if isinstance(many, list):
            requests.extend(dict(item) for item in many if isinstance(item, dict))
    return tuple(requests[:3])


def _json_lines(value: str) -> Iterable[dict[str, Any]]:
    for line in value.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            yield payload


def _codex_messages(event: dict[str, Any]) -> list[str]:
    item = event.get("item") if isinstance(event.get("item"), dict) else {}
    if item.get("type") in {"agent_message", "agentMessage"} and isinstance(item.get("text"), str):
        return [str(item["text"])]
    if event.get("type") in {"agent_message", "agentMessage"} and isinstance(event.get("text"), str):
        return [str(event["text"])]
    return []


def _claude_messages(event: dict[str, Any]) -> list[str]:
    if event.get("type") == "result" and isinstance(event.get("result"), str):
        return [str(event["result"])]
    if event.get("type") != "assistant":
        return []
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    content = message.get("content")
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    return [
        str(block["text"])
        for block in content
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
    ]


def _generic_messages(event: dict[str, Any]) -> list[str]:
    if event.get("type") == "result" and isinstance(event.get("result"), str):
        return [str(event["result"])]
    if event.get("role") == "assistant" and isinstance(event.get("content"), str):
        return [str(event["content"])]
    return _claude_messages(event)


def _session_id(event: dict[str, Any]) -> str | None:
    for key in ("session_id", "sessionId", "thread_id", "threadId"):
        value = event.get(key)
        if isinstance(value, str) and value:
            return value
    return None
