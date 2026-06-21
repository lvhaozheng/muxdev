from __future__ import annotations

import shutil
import uuid
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

    def fake_run(command, *, cwd, timeout=30, transcript_path=None, chunks_path=None, input_text=None, env=None):
        captured["command"] = command
        captured["input_text"] = input_text
        captured["env"] = env
        return SessionResult(0, "ok", "", [])

    adapter.backend.run = fake_run

    worktree = _test_worktree("provider_adapter_stdin")
    monkeypatch.setenv("MUXDEV_HOME", str(worktree / "muxdev-home"))
    output = adapter.run_stage(stage_id="problem_statement", task="ship it", worktree=worktree)

    assert output.returncode == 0
    assert captured["command"] == adapter.command
    assert "Task: ship it" in str(captured["input_text"])
    assert "CODEX_HOME" in captured["env"]


def test_codex_adapter_uses_muxdev_writable_codex_home(monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "codex" if name == "codex" else None)
    worktree = _test_worktree("provider_adapter_codex_home")
    source_home = worktree / "source-codex"
    muxdev_home = worktree / "muxdev-home"
    source_home.mkdir(parents=True)
    (source_home / "auth.json").write_text('{"token":"redacted"}', encoding="utf-8")
    (source_home / "config.toml").write_text('model = "test"\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(source_home))
    monkeypatch.setenv("MUXDEV_HOME", str(muxdev_home))
    adapter = get_runtime_provider("codex")
    captured: dict[str, object] = {}

    def fake_run(command, *, cwd, timeout=30, transcript_path=None, chunks_path=None, input_text=None, env=None):
        captured["env"] = env or {}
        return SessionResult(0, "ok", "", [])

    adapter.backend.run = fake_run
    adapter.run_stage(stage_id="plan", task="ship it", worktree=worktree)

    codex_home = Path(str(captured["env"]["CODEX_HOME"]))
    assert codex_home == muxdev_home / "data" / "provider_state" / "codex"
    assert (codex_home / "auth.json").read_text(encoding="utf-8") == '{"token":"redacted"}'
    assert (codex_home / "config.toml").read_text(encoding="utf-8") == 'model = "test"\n'


def test_headless_cli_failure_summary_includes_output_excerpt() -> None:
    adapter = HeadlessCliProviderAdapter("fake", ["fake"], timeout=1, prompt_template="{task}")

    def fake_run(command, *, cwd, timeout=30, transcript_path=None, chunks_path=None, input_text=None, env=None):
        return SessionResult(1, "first line\nfatal: command line too long\n", "", [])

    adapter.backend.run = fake_run

    worktree = _test_worktree("provider_adapter_summary")
    output = adapter.run_stage(stage_id="problem_statement", task="ship it", worktree=worktree)

    assert output.summary == "fake problem_statement exited with 1: first line\nfatal: command line too long"


def _test_worktree(name: str) -> Path:
    root = Path(".test_workspaces") / f"{name}_{uuid.uuid4().hex}"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    return root
