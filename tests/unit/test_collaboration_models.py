from __future__ import annotations

import json
import os
import re

import pytest
from pydantic import ValidationError

from muxdev.config.loader import load_config, validate_config
from muxdev.api.collaboration import _TerminalRateLimiter
from muxdev.models import CliAdapterDefinition, OrchestrationPlanV1
from muxdev.services.agents import AgentRegistry


def test_builtin_agent_config_is_valid(workspace):
    config = load_config(workspace)
    result = validate_config(config)
    assert result["valid"] is True
    registry = AgentRegistry(workspace, config=config)
    assert registry.get("codex").can_orchestrate is True
    assert registry.adapter_for("codex").supports_resume is True
    assert registry.adapter_for("codex").requires_pty is True
    assert registry.adapter_for("claude-code").requires_pty is True
    assert registry.adapter_for("deepcode").requires_pty is True
    assert registry.adapter_for("deepcode").supports_resume is True
    assert registry.adapter_for("deepcode").startup_ready_pattern == (
        r"Type your message\.\.\."
    )
    assert registry.adapter_for("deepcode").prompt_transport == "bracketed_paste"
    assert registry.adapter_for("codex").startup_ready_pattern == "OpenAI Codex"
    assert "Do you trust" in (
        registry.adapter_for("codex").startup_blocking_pattern or ""
    )
    assert "usage limit" in (
        registry.adapter_for("codex").runtime_waiting_pattern or ""
    )
    assert "Would you like" in (
        registry.adapter_for("codex").runtime_approval_pattern or ""
    )
    assert registry.adapter_for("deepcode").turn_completed_pattern
    assert registry.adapter_for("codex").prompt_transport == "bracketed_paste"
    assert registry.adapter_for("codex").bootstrap_transport == "argv"
    assert registry.adapter_for("qwen").requires_pty is True
    assert registry.adapter_for("kimi").requires_pty is True
    assert registry.get("qwen").can_orchestrate is True
    assert registry.get("kimi").can_orchestrate is True
    assert registry.adapter_for("mock").requires_pty is False
    assert registry.get("mock").selectable is False
    assert registry.get("mock-review").selectable is False
    assert registry.get("codex").selectable is True
    assert registry.get("deepcode").can_orchestrate is True


def test_terminal_rate_limiter_separates_input_resize_and_control() -> None:
    limiter = _TerminalRateLimiter()

    for index in range(200):
        allowed, _ = limiter.allow("input_bytes", 1, 1024 * 1024, now=index / 1000)
        assert allowed is True
    for index in range(20):
        allowed, _ = limiter.allow("resize", 1, 20, now=index / 1000)
        assert allowed is True

    allowed, retry_after = limiter.allow("resize", 1, 20, now=0.5)
    assert allowed is False
    assert retry_after > 0
    allowed, _ = limiter.allow("control", 1, 30, now=0.5)
    assert allowed is True


def test_terminal_rate_limiter_accepts_64kib_paste_until_byte_budget() -> None:
    limiter = _TerminalRateLimiter()

    for index in range(16):
        allowed, _ = limiter.allow(
            "input_bytes",
            64 * 1024,
            1024 * 1024,
            now=index / 1000,
        )
        assert allowed is True
    allowed, retry_after = limiter.allow(
        "input_bytes",
        1,
        1024 * 1024,
        now=0.5,
    )
    assert allowed is False
    assert retry_after > 0


def test_terminal_rate_limiter_closes_only_after_five_recent_violations() -> None:
    limiter = _TerminalRateLimiter()

    assert [limiter.note_violation(now=float(index)) for index in range(5)] == [
        False,
        False,
        False,
        False,
        True,
    ]


def test_cli_adapter_rejects_shell_text_and_unknown_placeholders():
    with pytest.raises(ValidationError):
        CliAdapterDefinition(cli_id="bad", command="tool --flag")  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="unsupported CLI argv placeholder"):
        CliAdapterDefinition(cli_id="bad", command=["tool", "{shell}"])


def test_orchestration_plan_rejects_cycles():
    with pytest.raises(ValidationError, match="acyclic"):
        OrchestrationPlanV1.model_validate(
            {
                "summary": "cycle",
                "nodes": [
                    {
                        "id": "a",
                        "title": "A",
                        "brief": "A task",
                        "agent_id": "mock",
                        "role": "implementer",
                        "dependencies": ["b"],
                        "work_mode": "write",
                        "deliverables": ["a"],
                        "completion": ["done"],
                        "proof": ["artifact"],
                    },
                    {
                        "id": "b",
                        "title": "B",
                        "brief": "B task",
                        "agent_id": "mock-review",
                        "role": "reviewer",
                        "dependencies": ["a"],
                        "work_mode": "consult",
                        "deliverables": ["b"],
                        "completion": ["done"],
                        "proof": ["review"],
                    },
                ],
            }
        )


def test_agent_registry_resolves_windows_script_invocation(workspace, monkeypatch):
    config = load_config(workspace)
    registry = AgentRegistry(workspace, config=config)
    monkeypatch.setattr(
        registry,
        "_resolve_executable",
        lambda command: (
            r"C:\Tools\codex.CMD" if command == "codex" else r"C:\Python\python.exe"
        ),
    )
    monkeypatch.setattr(
        "muxdev.services.agents.script_invocation",
        lambda command, args: ["launcher", command, *args],
    )

    argv = registry.build_argv("codex", worktree=workspace)

    assert argv[:7] == [
        "launcher",
        r"C:\Tools\codex.CMD",
        "-c",
        "check_for_update_on_startup=false",
        "-c",
        (
            "projects={ "
            + json.dumps(os.path.normcase(str(workspace.resolve())))
            + '={trust_level="trusted"} }'
        ),
        "--no-alt-screen",
    ]
    assert argv[-2:] == ["-C", str(workspace.resolve())]
    assert argv[argv.index("--ask-for-approval") + 1] == "never"
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    assert argv[argv.index("--add-dir") + 1] == str(
        (workspace / ".muxdev").resolve()
    )

    with_prompt = registry.build_argv(
        "codex",
        worktree=workspace,
        initial_prompt="Read the MuxDev brief and begin.",
    )
    assert with_prompt[-1] == "Read the MuxDev brief and begin."


def test_codex_runtime_patterns_distinguish_tip_quota_and_approval(workspace):
    adapter = AgentRegistry(workspace).adapter_for("codex")
    quota = re.compile(adapter.runtime_waiting_pattern or "")
    approval = re.compile(adapter.runtime_approval_pattern or "")

    assert not quota.search(
        "You have 2 usage limit resets available. Run /usage to use one."
    )
    assert quota.search("You've hit your usage limit")
    assert approval.search("Would you like to run the following command?")


def test_deepcode_adapter_uses_native_resume_and_scoped_environment(
    workspace, monkeypatch
):
    registry = AgentRegistry(workspace)
    monkeypatch.setattr(
        registry,
        "_resolve_executable",
        lambda command: r"C:\Tools\deepcode.CMD" if command == "deepcode" else None,
    )
    monkeypatch.setattr(
        "muxdev.services.agents.script_invocation",
        lambda command, args: ["launcher", command, *args],
    )
    session_id = "123e4567-e89b-42d3-a456-426614174000"

    argv = registry.build_argv(
        "deepcode",
        worktree=workspace,
        native_session_id=session_id,
    )
    env = registry.build_environment(
        "deepcode",
        source={
            "PATH": "bin",
            "TERM": "dumb",
            "DEEPCODE_API_KEY": "secret",
            "DEEPCODE_MODEL": "deepseek-v4-pro",
            "UNRELATED_SECRET": "must-not-leak",
        },
    )

    assert argv == [
        "launcher",
        r"C:\Tools\deepcode.CMD",
        "--resume",
        session_id,
    ]
    assert env["DEEPCODE_API_KEY"] == "secret"
    assert env["DEEPCODE_MODEL"] == "deepseek-v4-pro"
    assert env["TERM"] == "xterm-256color"
    assert "UNRELATED_SECRET" not in env


def test_unavailable_agent_is_rejected_before_conversation_creation(
    workspace, monkeypatch
):
    registry = AgentRegistry(workspace)
    monkeypatch.setattr(registry, "_resolve_executable", lambda _command: None)

    with pytest.raises(ValueError, match="当前不可用"):
        registry.require_available("claude-code")


def test_agent_registry_auto_detects_supported_provider_commands(
    workspace, monkeypatch
):
    registry = AgentRegistry(workspace)
    resolved = {
        "codex": r"C:\Tools\codex.CMD",
        "claude-code": r"C:\Tools\claude-code.CMD",
        "deepcode": r"C:\Tools\deepcode.CMD",
        "qwen": r"C:\Tools\qwen.PS1",
    }
    monkeypatch.setattr(
        registry,
        "_resolve_executable",
        lambda command: resolved.get(command),
    )
    monkeypatch.setattr(
        "muxdev.services.agents._terminal_capabilities",
        lambda **_kwargs: {
            "backend": "conpty",
            "pty": True,
            "resize": True,
            "process_resume": False,
            "degraded": False,
            "degradation_reason": None,
        },
    )

    agents = {item["agent_id"]: item for item in registry.list()}

    assert agents["qwen"]["available"] is True
    assert agents["qwen"]["installed"] is True
    assert agents["qwen"]["detected_command"] == "qwen"
    assert agents["qwen"]["provider_id"] == "qwen"
    assert agents["kimi"]["available"] is False
    assert agents["kimi"]["command_candidates"] == ["kimi"]
    assert agents["claude-code"]["available"] is True
    assert agents["claude-code"]["detected_command"] == "claude-code"
    assert agents["deepcode"]["available"] is True
    assert agents["deepcode"]["detected_command"] == "deepcode"


def test_agent_registry_launches_the_auto_detected_provider_command(
    workspace, monkeypatch
):
    registry = AgentRegistry(workspace)
    monkeypatch.setattr(
        registry,
        "_resolve_executable",
        lambda command: r"C:\Tools\qwen.PS1" if command == "qwen" else None,
    )
    monkeypatch.setattr(
        "muxdev.services.agents.script_invocation",
        lambda command, args: ["launcher", command, *args],
    )

    argv = registry.build_argv("qwen", worktree=workspace)

    assert argv == ["launcher", r"C:\Tools\qwen.PS1"]
