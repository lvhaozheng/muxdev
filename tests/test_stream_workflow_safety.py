from __future__ import annotations

import sys
from pathlib import Path

import pytest

from muxdev.core.safety import PolicyDecision, SafetyPolicyEngine, redact
from muxdev.clients.sessions import HeadlessSubprocessBackend, TmuxBackend
from muxdev.clients.stream import StreamAdapter, StreamEventType
from muxdev.workflows import load_workflow, ordered_stage_ids, validate_dag
from muxdev.models import WorkflowDefinition


def test_stream_adapter_detects_plain_and_ansi_approval_prompts() -> None:
    adapter = StreamAdapter()

    plain = adapter.parse_chunk("Apply this change? [y/N]")
    ansi = adapter.parse_chunk("\x1b[31mApprove?\x1b[0m")
    explicit = adapter.parse_chunk("Do you want to continue?")

    assert any(event.type == StreamEventType.APPROVAL_PROMPT_DETECTED for event in plain)
    assert any(event.type == StreamEventType.APPROVAL_PROMPT_DETECTED for event in ansi)
    assert any(event.type == StreamEventType.APPROVAL_PROMPT_DETECTED for event in explicit)


def test_stream_adapter_extracts_provider_action_options() -> None:
    adapter = StreamAdapter()

    actions = adapter.provider_actions(adapter.parse_chunk("Apply this change? [y/N]"))
    auth_actions = adapter.provider_actions(adapter.parse_chunk("Please sign in to continue."))

    assert actions[0].kind == "cli_confirmation"
    assert actions[0].options == [{"label": "Yes", "value": "y"}, {"label": "No", "value": "n", "default": True}]
    assert "Apply this change" in actions[0].prompt_text
    assert auth_actions[0].kind == "auth_required"


def test_stream_adapter_does_not_treat_status_text_as_approval() -> None:
    adapter = StreamAdapter()

    events = adapter.parse_chunk("I confirmed there are no browser instances exposed here. Continue reading the docs.")

    assert not any(event.type == StreamEventType.APPROVAL_PROMPT_DETECTED for event in events)
    assert not any(event.type == StreamEventType.WAITING_EXTERNAL_CONFIRMATION for event in events)


def test_stream_adapter_idle_and_cli_exit_events() -> None:
    adapter = StreamAdapter()

    assert adapter.idle_timeout(12).type == StreamEventType.IDLE_TIMEOUT
    assert adapter.cli_exited(2).type == StreamEventType.CLI_EXITED


def test_headless_backend_reports_cli_crash() -> None:
    result = HeadlessSubprocessBackend().run(
        [sys.executable, "-c", "import sys; sys.exit(3)"],
        cwd=Path.cwd(),
    )

    assert result.returncode == 3
    assert result.events[-1].type == StreamEventType.CLI_EXITED


def test_headless_backend_parses_output_in_chunks() -> None:
    temp_dir = Path(".test_workspaces") / "stream_chunks"
    temp_dir.mkdir(parents=True, exist_ok=True)
    transcript = temp_dir / "tmp_transcript.log"
    chunks = temp_dir / "tmp_chunks.jsonl"
    result = HeadlessSubprocessBackend().run(
        [sys.executable, "-c", "print('first line'); print('Approve?')"],
        cwd=Path.cwd(),
        transcript_path=transcript,
        chunks_path=chunks,
    )

    assert result.returncode == 0
    assert sum(1 for event in result.events if event.type == StreamEventType.OUTPUT) >= 2
    assert any(event.type == StreamEventType.APPROVAL_PROMPT_DETECTED for event in result.events)
    assert "first line" in transcript.read_text(encoding="utf-8")
    assert len(chunks.read_text(encoding="utf-8").splitlines()) >= 2


def test_headless_backend_enforces_idle_timeout_without_output() -> None:
    result = HeadlessSubprocessBackend().run(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        cwd=Path.cwd(),
        timeout=0.2,
    )

    assert result.returncode == 124
    assert any(event.type == StreamEventType.IDLE_TIMEOUT for event in result.events)


def test_tmux_backend_reports_missing_binary() -> None:
    result = TmuxBackend(tmux="").start("muxdev-test", ["echo", "hi"], cwd=Path.cwd())

    assert result.returncode == 127
    assert "tmux command not found" in result.stderr


def test_workflow_loads_dag_ready_order() -> None:
    workflow = load_workflow("software-dev")

    assert ordered_stage_ids(workflow) == ["design", "approve_plan", "implement", "test", "review", "fix"]


def test_workflow_cycle_is_rejected() -> None:
    workflow = WorkflowDefinition.model_validate(
        {
            "name": "bad",
            "stages": [
                {"id": "a", "deps": ["b"]},
                {"id": "b", "deps": ["a"]},
            ],
        }
    )

    with pytest.raises(ValueError, match="cycle"):
        validate_dag(workflow)


def test_policy_and_redaction_acceptance_cases() -> None:
    engine = SafetyPolicyEngine()

    assert engine.evaluate_shell("rm -rf /").decision == PolicyDecision.DENY
    assert engine.evaluate_shell("pytest").decision == PolicyDecision.ALLOW
    assert redact("sk-secret Bearer token.value") == "[REDACTED] [REDACTED]"
