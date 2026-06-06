from __future__ import annotations

import json
import os
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

from typer.testing import CliRunner

from muxdev.cli import app
from muxdev.config.loader import deep_merge, load_config, validate_config
from muxdev.providers.adapters import HeadlessCliProviderAdapter, get_runtime_provider


runner = CliRunner()


def test_default_config_is_valid(monkeypatch) -> None:
    monkeypatch.delenv("MUXDEV_CONFIG", raising=False)
    config = load_config(env={"APPDATA": str(Path(".test_workspaces") / "missing-appdata")})

    assert validate_config(config)["valid"] is True
    assert list(config["providers"])[:7] == ["mock", "codex", "claude-code", "qwen", "kimi", "trae", "antigravity"]
    assert config["workflows"]["software-dev"]["stages"][0]["id"] == "design"


def test_config_precedence_user_project_env(monkeypatch) -> None:
    temp_dir = _workspace_temp()
    try:
        user_root = temp_dir / "user"
        project = temp_dir / "project"
        project.mkdir()
        env_file = temp_dir / "env.yaml"
        user_config = user_root / "muxdev" / "config.yaml"
        project_config = project / ".muxdev" / "config.yaml"
        user_config.parent.mkdir(parents=True)
        project_config.parent.mkdir(parents=True)
        user_config.write_text("providers:\n  codex:\n    mode: user-mode\n", encoding="utf-8")
        project_config.write_text("providers:\n  codex:\n    mode: project-mode\n", encoding="utf-8")
        env_file.write_text("providers:\n  codex:\n    mode: env-mode\n", encoding="utf-8")
        monkeypatch.setenv("MUXDEV_CONFIG", str(env_file))

        config = load_config(project, env={"APPDATA": str(user_root), "MUXDEV_CONFIG": str(env_file)})
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    assert config["providers"]["codex"]["mode"] == "env-mode"


def test_deep_merge_preserves_keyed_collections() -> None:
    merged = deep_merge(
        {"providers": {"codex": {"mode": "local", "commands": ["codex"]}}},
        {"providers": {"codex": {"mode": "custom"}, "fake": {"mode": "test"}}},
    )

    assert merged["providers"]["codex"]["commands"] == ["codex"]
    assert merged["providers"]["codex"]["mode"] == "custom"
    assert merged["providers"]["fake"]["mode"] == "test"


def test_config_cli_commands(monkeypatch) -> None:
    temp_dir = _workspace_temp()
    try:
        monkeypatch.setenv("APPDATA", str(temp_dir / "user"))
        with _chdir(temp_dir):
            show = runner.invoke(app, ["config", "show", "--json"])
            paths = runner.invoke(app, ["config", "paths", "--json"])
            valid = runner.invoke(app, ["config", "validate", "--json"])
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    assert show.exit_code == 0
    assert "providers" in json.loads(show.stdout)
    assert paths.exit_code == 0
    assert {row["kind"] for row in json.loads(paths.stdout)} >= {"builtin", "user", "project"}
    assert valid.exit_code == 0
    assert json.loads(valid.stdout)["valid"] is True


def test_project_config_adds_dynamic_provider(monkeypatch) -> None:
    temp_dir = _workspace_temp()
    try:
        monkeypatch.setenv("APPDATA", str(temp_dir / "user"))
        config_path = temp_dir / ".muxdev" / "config.yaml"
        config_path.parent.mkdir()
        config_path.write_text(
            """
providers:
  fake:
    mode: local CLI
    commands: [fake-muxdev]
    status_hint: test
    probe: generic
accounts:
  fake:
    required: false
    signup_url: ""
    docs_url: ""
    login_command: ""
    notes: Fake provider for tests.
installers:
  fake:
    supported: false
    manager: manual
    command: []
    verify_command: []
    docs_url: ""
    notes: Fake provider has no installer.
""",
            encoding="utf-8",
        )
        with _chdir(temp_dir):
            result = runner.invoke(app, ["provider", "detect", "--json"])
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    assert result.exit_code == 0
    by_name = {row["provider"]: row for row in json.loads(result.stdout)}
    assert by_name["fake"]["status"] == "unavailable"
    assert by_name["fake"]["headless"] == "not_installed"


def test_project_config_adds_dynamic_workflow_and_plugin(monkeypatch) -> None:
    temp_dir = _workspace_temp()
    try:
        monkeypatch.setenv("APPDATA", str(temp_dir / "user"))
        config_path = temp_dir / ".muxdev" / "config.yaml"
        config_path.parent.mkdir()
        config_path.write_text(
            """
workflows:
  tiny:
    name: tiny
    max_parallel: 1
    stages:
      - id: only
        role: tester
        deps: []
workflow_plugins:
  custom:
    description: Custom project plugin.
    phases: [planning]
    supported_providers: [codex]
    commands:
      planning: "/custom:plan {task}"
    artifacts:
      planning: custom-plan.md
command_dialects:
  codex:
    prefix: "$"
    colon: "-"
""",
            encoding="utf-8",
        )
        with _chdir(temp_dir):
            graph = runner.invoke(app, ["graph", "export", "--workflow", "tiny", "--json"])
            plugins = runner.invoke(app, ["workflow", "plugins", "--json"])
            rendered = runner.invoke(
                app,
                ["workflow", "render", "custom", "--phase", "planning", "--provider", "codex", "--task", "x", "--json"],
            )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    assert graph.exit_code == 0
    assert json.loads(graph.stdout)["name"] == "tiny"
    assert plugins.exit_code == 0
    assert "custom" in {row["name"] for row in json.loads(plugins.stdout)}
    assert rendered.exit_code == 0
    assert json.loads(rendered.stdout)["command"] == "$custom-plan x"


def test_project_config_controls_runtime_adapter(monkeypatch) -> None:
    temp_dir = _workspace_temp()
    try:
        monkeypatch.setenv("APPDATA", str(temp_dir / "user"))
        monkeypatch.setattr("muxdev.providers.adapters.shutil.which", lambda command: "C:/bin/fake.exe" if command == "fake-agent" else None)
        config_path = temp_dir / ".muxdev" / "config.yaml"
        config_path.parent.mkdir()
        config_path.write_text(
            """
providers:
  fake-runtime:
    mode: local CLI
    commands: [fake-agent]
    status_hint: test
    runtime:
      kind: headless_cli
      command: [fake-agent, --machine]
      prompt_template: "Do {stage_id}: {task}"
""",
            encoding="utf-8",
        )
        with _chdir(temp_dir):
            adapter = get_runtime_provider("fake-runtime")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    assert isinstance(adapter, HeadlessCliProviderAdapter)
    assert adapter.command == ["C:/bin/fake.exe", "--machine"]
    assert adapter._prompt("plan", "ship") == "Do plan: ship"


def _workspace_temp() -> Path:
    path = Path(".test_workspaces") / f"config_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


@contextmanager
def _chdir(path: Path):
    cwd = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(cwd)
