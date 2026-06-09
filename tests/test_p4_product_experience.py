from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from muxdev.api.web import create_app, render_live_dashboard_html
from muxdev.cli import app
from muxdev.config import runtime as runtime_config
from muxdev.config.runtime import setup_muxdev
from muxdev.daemon.paths import default_daemon_paths
from muxdev.daemon.tasks import TaskManager
from muxdev.providers.registry import CapabilityState, ProviderProbe, ProviderStatus
from muxdev.services.product_experience import build_product_experience


runner = CliRunner()


def test_project_setup_writes_muxdev_context_and_provider_setup(monkeypatch) -> None:
    workspace = _workspace_temp("p4-setup")
    home = workspace / "home"
    monkeypatch.setattr(runtime_config, "detect_providers", lambda: [_probe("mock", ProviderStatus.READY)])
    try:
        payload = setup_muxdev(workspace, project=True, yes=True, env={"MUXDEV_HOME": str(home)})
        context_path = workspace / "MUXDEV.md"
        content = context_path.read_text(encoding="utf-8")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert payload["project_context"]["written"] is True
    assert payload["provider_setup"]["steps"][0]["provider"] == "mock"
    assert "Provider actions: muxdev never types yes/no" in content
    assert "muxdev provider setup" in content


def test_product_experience_payload_covers_p4_surface() -> None:
    workspace = _workspace_temp("p4-surface")
    try:
        payload = build_product_experience(
            workspace,
            tasks=[
                {"task_id": "run_1", "status": "completed", "provider": "mock", "workflow": "dev", "cost_usd": 0.12, "tokens": 42},
                {"task_id": "run_2", "status": "awaiting_provider_action", "provider": "codex", "workflow": "dev", "cost_usd": 0.7, "tokens": 100},
            ],
            provider_health={"ready": ["mock"], "partial": [], "unavailable": ["codex"], "total": 2, "providers": [], "recommendations": []},
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert payload["quickstart"]["one_line_install"] == "pipx install muxdev"
    assert payload["task_management"]["kanban_columns"]["waiting"] == 1
    assert payload["budget"]["high_cost_tasks"] == 1
    assert "provider" in payload["task_management"]["filters"]
    assert "muxdev rollback latest" in payload["git_safety"]["commands"]
    assert "git revert <reviewed_commit>" in payload["git_safety"]["commands"]
    assert payload["web_ui"]["ide_plugin"].startswith("optional")


def test_provider_setup_and_context_cli_json() -> None:
    workspace = _workspace_temp("p4-cli")
    previous = Path.cwd()
    try:
        os.chdir(workspace)
        provider = runner.invoke(app, ["provider", "setup", "--json"])
        context = runner.invoke(app, ["context", "--write", "--json"])
        experience = runner.invoke(app, ["experience", "--json"])
    finally:
        os.chdir(previous)
        shutil.rmtree(workspace, ignore_errors=True)

    assert provider.exit_code == 0
    assert "steps" in json.loads(provider.stdout)
    assert context.exit_code == 0
    assert json.loads(context.stdout)["path"].endswith("MUXDEV.md")
    assert experience.exit_code == 0
    assert json.loads(experience.stdout)["quickstart"]["first_run"][0] == "muxdev setup --project"


def test_product_experience_api_and_dashboard_section() -> None:
    workspace = _workspace_temp("p4-api")
    try:
        manager = TaskManager(paths=default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure())
        client = TestClient(create_app(task_manager=manager))
        payload = client.get("/api/product/experience").json()
        setup = client.get("/api/setup/status").json()
        html = render_live_dashboard_html()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert payload["quickstart"]["one_line_install"] == "pipx install muxdev"
    assert "git_safety" in payload
    assert "product_experience" in setup
    assert "Product Experience" in html
    assert "/product/experience" in html


def _probe(name: str, status: ProviderStatus) -> ProviderProbe:
    return ProviderProbe(
        provider=name,
        mode="builtin",
        command=name,
        installed=True,
        version="test",
        headless=CapabilityState.SUPPORTED,
        pty=CapabilityState.SUPPORTED,
        json=CapabilityState.SUPPORTED,
        approval=CapabilityState.SUPPORTED,
        skill=CapabilityState.SUPPORTED,
        attach=CapabilityState.SUPPORTED,
        status=status,
        notes="",
    )


def _workspace_temp(prefix: str) -> Path:
    path = Path(".test_workspaces") / f"{prefix}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()
