from __future__ import annotations

import json
import importlib
import shutil
import time
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from muxdev.api.web import create_app
from muxdev.cli import app
from muxdev.config import runtime as runtime_config
from muxdev.config.runtime import load_runtime_config, resolve_task_request, setup_muxdev
from muxdev.daemon.paths import default_daemon_paths
from muxdev.daemon.tasks import TaskManager
from muxdev.providers.registry import (
    CapabilityState,
    ProviderProbe,
    ProviderStatus,
)
from muxdev.services.skills import bind_skill, resolve_active_skills, scan_skills


runner = CliRunner()


def test_setup_check_does_not_write_and_yes_writes_toml(monkeypatch) -> None:
    workspace = _workspace_temp("setup")
    home = workspace / "home"
    monkeypatch.setattr(runtime_config, "detect_providers", lambda: [_probe("mock", ProviderStatus.READY)])
    try:
        checked = setup_muxdev(workspace, check=True, env={"MUXDEV_HOME": str(home)})
        assert checked["status"] == "ok"
        assert not (home / "config.toml").exists()
        assert not (home / "cache" / "providers.json").exists()

        written = setup_muxdev(workspace, yes=True, env={"MUXDEV_HOME": str(home)})
        effective = load_runtime_config(workspace, env={"MUXDEV_HOME": str(home)})
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert written["written"] is True
    assert effective["profile"] == "squad"
    assert effective["gate"] == "safe"
    assert (home / "config.toml").exists()
    assert (home / "cache" / "providers.json").exists()


def test_resolve_task_request_maps_new_and_legacy_roles(monkeypatch) -> None:
    workspace = _workspace_temp("resolve")
    monkeypatch.setattr(runtime_config, "detect_providers", lambda: [_probe("mock", ProviderStatus.READY)])
    try:
        request = resolve_task_request(
            workspace=workspace,
            task="ship feature",
            command_workflow="dev",
            provider="mock",
            profile="squad",
            gate="strict",
            role_overrides=["code=mock"],
            skill_specs=["review=security-review"],
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert request["workflow"] == "dev"
    assert request["profile"] == "squad"
    assert request["gate"] == "strict"
    assert request["role_providers"]["code"] == "mock"
    assert request["role_providers"]["implementer"] == "mock"
    assert "write" in request["require_approval"]
    assert request["skill_specs"] == ["review=security-review"]


def test_skill_scan_priority_and_role_binding() -> None:
    workspace = _workspace_temp("skills")
    try:
        _write_skill(workspace / ".muxdev" / "skills" / "demo", "demo", "project priority")
        _write_skill(workspace / ".agents" / "skills" / "demo", "demo", "lower priority")
        bind_skill(workspace, "review", "demo")

        skills = scan_skills(workspace)
        active = resolve_active_skills(workspace, task="please review this", roles=["review"], provider="mock")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    demo = next(skill for skill in skills if skill.name == "demo")
    assert ".muxdev" in demo.path
    assert active[0]["name"] == "demo"
    assert active[0]["role"] == "review"
    assert active[0]["reason"] == "role_binding"
    assert "content" not in active[0]


def test_daemon_api_persists_profile_gate_and_skills() -> None:
    workspace = _workspace_temp("daemon-context")
    try:
        manager = TaskManager(paths=default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure())
        client = TestClient(create_app(task_manager=manager))
        submitted = client.post(
            "/api/tasks",
            json={
                "task": "context smoke",
                "workspace": str(workspace),
                "provider": "mock",
                "workflow": "review",
                "profile": "squad",
                "gate": "auto",
                "skills": [{"name": "demo", "role": "review", "content": "# demo"}],
            },
        ).json()
        task_id = submitted["task_id"]
        _wait_for_status(client, task_id, "completed")
        detail = client.get(f"/api/tasks/{task_id}").json()
        tasks = client.get("/api/tasks").json()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert detail["context"]["profile"] == "squad"
    assert detail["context"]["gate"] == "auto"
    assert detail["context"]["skills"][0]["name"] == "demo"
    assert tasks[0]["profile"] == "squad"
    assert tasks[0]["skills"][0] == "demo"
    assert "default-review" in tasks[0]["skills"]


def test_cli_dev_submits_resolved_daemon_payload(monkeypatch) -> None:
    workspace = _workspace_temp("cli-dev")
    monkeypatch.setattr(runtime_config, "detect_providers", lambda: [_probe("mock", ProviderStatus.READY)])

    class FakeClient:
        def __init__(self, *, host: str, port: int) -> None:
            self.host = host
            self.port = port

        def submit_task(self, payload: dict[str, object]) -> dict[str, object]:
            assert payload["task"] == "cli main path"
            assert payload["workflow"] == "dev"
            assert payload["profile"] == "pair"
            assert payload["gate"] == "auto"
            assert payload["role_providers"]["code"] == "mock"
            return {"task_id": "task-1", "run_id": "task-1", "status": "created"}

    cli_main_module = importlib.import_module("muxdev.cli.main")
    monkeypatch.setattr(cli_main_module, "_daemon_client", lambda host="127.0.0.1", port=8788: FakeClient(host=host, port=port))
    try:
        result = runner.invoke(
            app,
            ["dev", "cli main path", "-p", "pair", "-g", "auto", "--role", "code=mock", "--provider", "mock", "--json"],
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert result.exit_code == 0
    assert json.loads(result.stdout)["task_id"] == "task-1"


def _probe(name: str, status: ProviderStatus) -> ProviderProbe:
    return ProviderProbe(
        provider=name,
        mode="builtin" if name == "mock" else "local CLI",
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
        notes="test",
    )


def _write_skill(path: Path, name: str, description: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nkeywords: [review]\n---\n# {name}\n",
        encoding="utf-8",
    )


def _wait_for_status(client: TestClient, task_id: str, expected: str) -> None:
    for _ in range(80):
        status = client.get(f"/api/tasks/{task_id}").json()["run"]["status"]
        if status == expected:
            return
        time.sleep(0.1)
    raise AssertionError(f"task did not reach {expected}")


def _workspace_temp(prefix: str) -> Path:
    path = Path(".test_workspaces") / f"{prefix}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()
