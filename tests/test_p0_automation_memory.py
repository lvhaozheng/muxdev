from __future__ import annotations

import json
import os
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

from typer.testing import CliRunner

from muxdev.cli import app
from muxdev.config import runtime as runtime_config
from muxdev.config.runtime import resolve_task_request
from muxdev.models import RunStatus
from muxdev.providers.registry import CapabilityState, ProviderProbe, ProviderStatus
from muxdev.runtime import SupervisorRuntime
from muxdev.storage import MemoryStore


runner = CliRunner()


def test_auto_request_compiles_sensitive_dev_to_deep_squad(monkeypatch) -> None:
    workspace = _workspace_temp("auto")
    monkeypatch.setattr(runtime_config, "detect_providers", lambda: [_probe("mock", ProviderStatus.READY)])
    try:
        request = resolve_task_request(
            workspace=workspace,
            task="harden auth login boundary",
            command_workflow="dev",
            provider="mock",
            gate="auto",
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert request["workflow"] == "dev"
    assert request["depth"] == "deep"
    assert request["topology"] == "squad"
    assert request["automation"]["intent"] == "dev"
    assert "task:auth" in request["automation"]["repo"]["sensitive_hits"]
    assert list(request["runtime_roles"]) == ["plan", "code", "test", "review"]


def test_auto_design_snake_routes_to_lite_solo(monkeypatch) -> None:
    workspace = _workspace_temp("design_simple")
    monkeypatch.setattr(runtime_config, "detect_providers", lambda: [_probe("mock", ProviderStatus.READY)])
    try:
        request = resolve_task_request(
            workspace=workspace,
            task="设计一个简单的贪吃蛇游戏",
            command_workflow="design",
            provider="mock",
            gate="auto",
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert request["workflow"] == "design-lite"
    assert request["depth"] == "simple"
    assert request["topology"] == "solo"
    assert request["automation"]["intent"] == "design"
    assert request["automation"]["roles"] == ["architect"]
    assert request["runtime_roles"] == {"architect": "auto"}


def test_cli_design_submits_automation_payload(monkeypatch) -> None:
    submitted: list[dict[str, object]] = []

    class FakeClient:
        def submit_task(self, payload: dict[str, object]) -> dict[str, object]:
            submitted.append(payload)
            return {"task_id": "run_design", "run_id": "run_design", "status": "created"}

    monkeypatch.setattr("muxdev.cli.main._daemon_client", lambda *_args, **_kwargs: FakeClient())

    result = runner.invoke(app, ["design", "design persistent memory", "--provider", "mock", "--json"])

    assert result.exit_code == 0
    payload = submitted[0]
    assert payload["workflow"] == "design"
    assert payload["depth"] == "deep"
    assert payload["automation"]["intent"] == "design"
    assert "architect" in payload["automation"]["roles"]


def test_cli_design_simple_override_submits_lite_payload(monkeypatch) -> None:
    submitted: list[dict[str, object]] = []

    class FakeClient:
        def submit_task(self, payload: dict[str, object]) -> dict[str, object]:
            submitted.append(payload)
            return {"task_id": "run_design", "run_id": "run_design", "status": "created"}

    monkeypatch.setattr("muxdev.cli.main._daemon_client", lambda *_args, **_kwargs: FakeClient())

    result = runner.invoke(app, ["design", "snake game", "--simple", "--provider", "mock", "--json"])

    assert result.exit_code == 0
    payload = submitted[0]
    assert payload["workflow"] == "design-lite"
    assert payload["depth"] == "simple"
    assert payload["topology"] == "solo"
    assert payload["automation"]["roles"] == ["architect"]


def test_design_runtime_writes_pack_and_memory_proposal_file_without_auto_memory() -> None:
    workspace = _workspace_temp("design")
    try:
        result = SupervisorRuntime(workspace).run(
            "design persistent memory skeleton",
            provider="mock",
            workflow_name="design",
            require_approval=set(),
            profile="squad",
            gate="auto",
            depth="deep",
            topology="squad",
            automation={
                "intent": "design",
                "depth": "deep",
                "topology": "squad",
                "roles": ["requirements", "architect", "test_strategy", "review", "docs", "memory_curator"],
            },
        )
        contract = result.run_dir / "design" / "design_contract.json"
        proposals = result.run_dir / "design" / "memory_proposals.json"
        with MemoryStore(workspace) as store:
            status = store.status()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert result.status == RunStatus.COMPLETED
    assert json.loads(contract.read_text(encoding="utf-8"))["contract_version"] == "muxdev.design_contract.v1"
    assert json.loads(proposals.read_text(encoding="utf-8"))[0]["status"] == "proposed"
    assert status["counts"].get("proposed", 0) == 0


def test_design_lite_runtime_writes_design_pack_metadata() -> None:
    workspace = _workspace_temp("design_lite_runtime")
    try:
        result = SupervisorRuntime(workspace).run(
            "design a simple snake game",
            provider="mock",
            workflow_name="design-lite",
            require_approval=set(),
            profile="solo",
            gate="auto",
            depth="simple",
            topology="solo",
            automation={
                "intent": "design",
                "depth": "simple",
                "topology": "solo",
                "roles": ["architect"],
            },
        )
        contract = json.loads((result.run_dir / "design" / "design_contract.json").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert result.status == RunStatus.COMPLETED
    assert contract["workflow"] == "design-lite"
    assert contract["depth"] == "simple"
    assert contract["topology"] == "solo"
    assert contract["roles"] == ["architect"]


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
        notes="test",
    )


def _workspace_temp(prefix: str) -> Path:
    path = Path(".test_workspaces") / f"{prefix}_{uuid.uuid4().hex}"
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
