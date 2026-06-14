from __future__ import annotations

import shutil
from pathlib import Path

from muxdev.providers import HeadlessCliProviderAdapter, get_runtime_provider
from muxdev.clients.sessions import SessionResult


def test_codex_adapter_places_global_approval_before_exec(monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "codex" if name == "codex" else None)

    adapter = get_runtime_provider("codex")

    assert isinstance(adapter, HeadlessCliProviderAdapter)
    assert adapter.command[:4] == ["codex", "--ask-for-approval", "never", "exec"]
    assert "--json" in adapter.command
    assert adapter.prompt_transport == "stdin"


def test_qwen_adapter_uses_headless_prompt_mode(monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "qwen" if name == "qwen" else None)

    adapter = get_runtime_provider("qwen")

    assert isinstance(adapter, HeadlessCliProviderAdapter)
    assert adapter.command[:2] == ["qwen", "--bare"]
    assert ["--output-format", "stream-json"] == adapter.command[4:6]


def test_codex_adapter_sends_prompt_via_stdin(monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "codex" if name == "codex" else None)
    adapter = get_runtime_provider("codex")
    captured: dict[str, object] = {}

    def fake_run(command, *, cwd, timeout=30, transcript_path=None, chunks_path=None, input_text=None):
        captured["command"] = command
        captured["input_text"] = input_text
        return SessionResult(0, "ok", "", [])

    adapter.backend.run = fake_run

    worktree = _test_worktree("provider_adapter_stdin")
    output = adapter.run_stage(stage_id="problem_statement", task="ship it", worktree=worktree)

    assert output.returncode == 0
    assert captured["command"] == adapter.command
    assert "Task: ship it" in str(captured["input_text"])


def test_headless_cli_failure_summary_includes_output_excerpt() -> None:
    adapter = HeadlessCliProviderAdapter("fake", ["fake"], timeout=1, prompt_template="{task}")

    def fake_run(command, *, cwd, timeout=30, transcript_path=None, chunks_path=None, input_text=None):
        return SessionResult(1, "first line\nfatal: command line too long\n", "", [])

    adapter.backend.run = fake_run

    worktree = _test_worktree("provider_adapter_summary")
    output = adapter.run_stage(stage_id="problem_statement", task="ship it", worktree=worktree)

    assert output.summary == "fake problem_statement exited with 1: first line\nfatal: command line too long"


def _test_worktree(name: str) -> Path:
    root = Path(".test_workspaces") / name
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    return root
