from __future__ import annotations

import shutil

from muxdev.providers import HeadlessCliProviderAdapter, get_runtime_provider


def test_codex_adapter_places_global_approval_before_exec(monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "codex" if name == "codex" else None)

    adapter = get_runtime_provider("codex")

    assert isinstance(adapter, HeadlessCliProviderAdapter)
    assert adapter.command[:4] == ["codex", "--ask-for-approval", "never", "exec"]
    assert "--json" in adapter.command


def test_qwen_adapter_uses_headless_prompt_mode(monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: "qwen" if name == "qwen" else None)

    adapter = get_runtime_provider("qwen")

    assert isinstance(adapter, HeadlessCliProviderAdapter)
    assert adapter.command[:2] == ["qwen", "--bare"]
    assert ["--output-format", "stream-json"] == adapter.command[4:6]
