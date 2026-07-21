from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from muxdev.domain import CapabilityGrant, StageExecutionInput
from muxdev.models import WorkflowStage
from muxdev.providers.adapters import _provider_environment
from muxdev.providers.certification import (
    _probe_acp_cancel,
    certification_matches,
    certify_provider,
    provider_fingerprint,
)
from muxdev.runtime.supervisor import ProcessSupervisor
from muxdev.runtime.workspace import (
    WorkspaceConflictError,
    apply_change_set,
    build_change_set,
    snapshot_workspace,
)
from muxdev.services.capabilities import resolve_stage_capabilities


def test_changeset_handles_add_modify_delete_rename_binary_and_conflict(workspace: Path) -> None:
    before_root = workspace / "before"
    staged_root = workspace / "staged"
    destination = workspace / "destination"
    before_root.mkdir()
    (before_root / "modify.txt").write_text("before", encoding="utf-8")
    (before_root / "delete.txt").write_text("delete", encoding="utf-8")
    (before_root / "old-name.bin").write_bytes(b"\x00\x01binary")
    shutil.copytree(before_root, staged_root)
    shutil.copytree(before_root, destination)

    (staged_root / "modify.txt").write_text("after", encoding="utf-8")
    (staged_root / "delete.txt").unlink()
    (staged_root / "old-name.bin").rename(staged_root / "new-name.bin")
    (staged_root / "add.bin").write_bytes(b"\xff\x00new")
    change_set = build_change_set(snapshot_workspace(before_root), snapshot_workspace(staged_root))

    assert {item.operation for item in change_set.operations} == {"add", "modify", "delete", "rename"}
    apply_change_set(change_set, staged_root, destination)
    assert (destination / "modify.txt").read_text(encoding="utf-8") == "after"
    assert not (destination / "delete.txt").exists()
    assert not (destination / "old-name.bin").exists()
    assert (destination / "new-name.bin").read_bytes() == b"\x00\x01binary"

    conflict_destination = workspace / "conflict"
    shutil.copytree(before_root, conflict_destination)
    (conflict_destination / "modify.txt").write_text("user changed this", encoding="utf-8")
    with pytest.raises(WorkspaceConflictError):
        apply_change_set(change_set, staged_root, conflict_destination)
    assert not (conflict_destination / "add.bin").exists()
    assert (conflict_destination / "delete.txt").exists()


def test_workspace_snapshot_rejects_escaping_symlink(workspace: Path) -> None:
    link = workspace / "escape"
    try:
        link.symlink_to(workspace.parent, target_is_directory=True)
    except (OSError, PermissionError):
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(ValueError, match="symlink escapes"):
        snapshot_workspace(workspace)


def test_process_supervisor_bounds_output_and_cleans_timeout(workspace: Path) -> None:
    result = ProcessSupervisor().run(
        [sys.executable, "-c", "import time; print('x' * 10000); time.sleep(2)"],
        cwd=workspace,
        run_id="supervisor-timeout",
        stage_id="test",
        timeout_seconds=0.2,
        output_limit=256,
    )
    assert result.returncode == 124
    assert result.stdout_truncated
    assert ProcessSupervisor.active_pids("supervisor-timeout") == ()


def test_exact_read_only_mcp_grant_and_secret_minimization(workspace: Path, monkeypatch) -> None:
    config_dir = workspace / ".muxdev"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(yaml.safe_dump({
        "mcp_servers": {
            "docs": {
                "transport": "stdio",
                "command": sys.executable,
                "args": [],
                "env_vars": [],
                "enabled_tools": ["search"],
                "tools": {"search": {"effect": "read"}},
            }
        }
    }), encoding="utf-8")
    stage = WorkflowStage(
        id="plan",
        read_only=True,
        allowed_secrets=["UNUSED_ALLOWED_SECRET"],
        mcp_tools=["docs/search"],
    )
    grant = resolve_stage_capabilities(workspace, stage)
    assert grant.mcp_tools == ("docs/search",)
    assert grant.secret_names == ()
    assert grant.mcp_servers[0].tools == ("search",)

    monkeypatch.setenv("MUXDEV_UNAUTHORIZED_SECRET", "must-not-leak")
    stage_input = StageExecutionInput.for_provider(
        stage_id="plan", task="test", worktree=workspace, provider="mock"
    )
    env = _provider_environment(stage_input, auth_env_vars=())
    assert "MUXDEV_UNAUTHORIZED_SECRET" not in env

    config = yaml.safe_load((config_dir / "config.yaml").read_text(encoding="utf-8"))
    config["mcp_servers"]["docs"]["tools"]["search"]["effect"] = "write"
    (config_dir / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(PermissionError, match="forbidden"):
        resolve_stage_capabilities(workspace, stage)


def test_provider_certification_is_invalidated_by_runtime_config_change(workspace: Path) -> None:
    config_dir = workspace / ".muxdev"
    config_dir.mkdir()
    definition = {
        "mode": "local CLI",
        "commands": [sys.executable],
        "status_hint": "fixture",
        "runtime": {
            "kind": "headless_cli",
            "command": [sys.executable, "-c", "print('{}')"],
            "isolated_config": True,
        },
    }
    config = {"providers": {"fixture": definition}}
    (config_dir / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    fingerprint = provider_fingerprint(workspace, "fixture")
    row = {"payload": {"fingerprint": fingerprint}}
    assert certification_matches(workspace, "fixture", row)

    definition["runtime"]["command"].append("changed")
    (config_dir / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    assert not certification_matches(workspace, "fixture", row)


def test_acp_adapter_initializes_session_and_streams(workspace: Path) -> None:
    pytest.importorskip("acp")
    from muxdev.providers.acp import AcpProviderAdapter

    fixture = Path(__file__).parents[1] / "fixtures" / "acp_echo_agent.py"
    adapter = AcpProviderAdapter("fixture", [sys.executable, str(fixture)], timeout=10)
    stage_input = StageExecutionInput(
        run_id="acp-fixture",
        stage_id="implement",
        role="code",
        task="return a structured result",
        worktree=workspace,
        context={},
        capabilities=CapabilityGrant(read_workspace=True),
        provider="fixture",
        policy={"output_schema": "ChangeResult", "timeout_seconds": 10},
    )
    try:
        result = adapter.execute(stage_input)
    except PermissionError:
        pytest.skip("the Windows test sandbox does not allow asyncio stdio pipes")
    assert result.returncode == 0
    assert result.protocol == "acp"
    assert result.session_id
    assert "ACP fixture completed" in result.content
    assert any(event.kind == "permission.allowed" for event in result.events)
    assert ProcessSupervisor.active_pids("acp-fixture") == ()
    cancel_check = _probe_acp_cancel(adapter, workspace, "fixture")
    assert cancel_check["requested"] is True
    assert cancel_check["cleaned"] is True


def test_official_mcp_client_lists_typed_control_tools(workspace: Path) -> None:
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def exercise() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "muxdev",
                "mcp",
                "serve",
                "--workspace",
                str(workspace),
                "--transport",
                "stdio",
            ],
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                assert len(tools) == 8
                assert all(tool.inputSchema.get("additionalProperties") is False for tool in tools)
                assert all(tool.outputSchema for tool in tools)

    try:
        asyncio.run(exercise())
    except PermissionError:
        pytest.skip("the Windows test sandbox does not allow asyncio stdio pipes")


def test_live_acp_certification_transfers_read_only_mcp_fixture(workspace: Path) -> None:
    pytest.importorskip("acp")
    pytest.importorskip("mcp")
    fixture = Path(__file__).parents[1] / "fixtures" / "acp_echo_agent.py"
    config_dir = workspace / ".muxdev"
    config_dir.mkdir()
    config = {
        "providers": {
            "fixture-acp": {
                "mode": "local ACP",
                "commands": [sys.executable],
                "runtime": {
                    "kind": "acp",
                    "command": [sys.executable, str(fixture.resolve())],
                    "timeout": 15,
                },
            }
        }
    }
    (config_dir / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    certification = certify_provider(workspace, "fixture-acp", live=True)
    if "拒绝访问" in str(certification.get("checks")):
        pytest.skip("the Windows test sandbox does not allow asyncio stdio pipes")
    assert certification["status"] == "live_verified", json.dumps(
        certification["checks"], ensure_ascii=False, default=str
    )
    assert certification["checks"]["mcp_fixture_observed"] is True
    assert certification["checks"]["cancel"]["cleaned"] is True
