from __future__ import annotations

import json

import pytest

from muxdev.config.loader import load_config
from muxdev.providers.adapters import _mcp_call_events
from muxdev.providers.protocols import build_cli_invocation, parse_cli_output


def test_prompt_transport_is_explicit_and_single_source() -> None:
    codex = build_cli_invocation(["codex", "exec", "--json"], "task", transport="stdin")
    claude = build_cli_invocation(["claude", "-p", "{prompt}"], "task", transport="argument")
    assert codex.argv == ("codex", "exec", "--json") and codex.stdin == "task"
    assert claude.argv == ("claude", "-p", "task") and claude.stdin is None
    with pytest.raises(ValueError, match="exactly one"):
        build_cli_invocation(["qwen", "-p"], "task", transport="argument")
    with pytest.raises(ValueError, match="cannot also"):
        build_cli_invocation(["codex", "{prompt}"], "task", transport="stdin")


def test_codex_and_claude_jsonl_are_normalized_by_protocol() -> None:
    codex = "\n".join([
        json.dumps({"type": "thread.started", "thread_id": "t-1"}),
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "codex final"}}),
    ])
    claude = "\n".join([
        json.dumps({"type": "assistant", "session_id": "s-1", "message": {"content": [{"type": "text", "text": "draft"}]}}),
        json.dumps({"type": "result", "result": "claude final"}),
    ])
    codex_result = parse_cli_output("codex", codex, "")
    claude_result = parse_cli_output("claude-code", claude, "warning")
    assert codex_result.content == "codex final"
    assert codex_result.session_id == "t-1" and codex_result.event_count == 2
    assert claude_result.content.startswith("claude final") and "warning" in claude_result.content
    assert claude_result.session_id == "s-1"


def test_every_configured_headless_cli_binds_one_prompt() -> None:
    providers = load_config()["providers"]
    for provider, config in providers.items():
        runtime = config.get("runtime", {})
        if runtime.get("kind") != "headless_cli":
            continue
        invocation = build_cli_invocation(
            runtime["command"], "task", transport=runtime["prompt_transport"]
        )
        assert (invocation.stdin == "task") != ("task" in invocation.argv), provider


def test_headless_mcp_observation_requires_a_real_tool_event() -> None:
    output = "\n".join([
        json.dumps({"type": "agent_message", "text": "docs/search"}),
        json.dumps({"type": "mcp_tool_call", "server": "docs", "tool": "search"}),
    ])
    events = _mcp_call_events(output, ("docs/search", "issues/get"))
    assert [event.details["tool_ref"] for event in events] == ["docs/search"]


def test_provider_output_can_raise_a_structured_clarification() -> None:
    body = json.dumps({
        "interaction_request": {
            "question": "选择默认布局",
            "options": [
                {"id": "compact", "label": "紧凑", "recommended": True},
                {"id": "comfortable", "label": "舒适"},
            ],
        }
    }, ensure_ascii=False)
    output = json.dumps({
        "type": "item.completed",
        "item": {"type": "agent_message", "text": body},
    }, ensure_ascii=False)

    parsed = parse_cli_output("codex", output, "")

    assert parsed.interaction_requests[0]["question"] == "选择默认布局"
    assert parsed.interaction_requests[0]["options"][0]["id"] == "compact"
