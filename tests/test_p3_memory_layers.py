from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

from typer.testing import CliRunner

from muxdev.cli import app
from muxdev.models import RunStatus
from muxdev.runtime import SupervisorRuntime
from muxdev.storage import Blackboard, MemoryStore


runner = CliRunner()


def test_layered_memory_inbox_promote_and_query_isolates_temporary_context() -> None:
    workspace = _workspace_temp("p3-memory-layers")
    try:
        with MemoryStore(workspace) as store:
            session = store.propose_claim(
                claim="Temporary session note: inspect README first",
                layer="session",
                scope_id="session_1",
                kind="temporary_context",
                role="any",
            )
            project = store.propose_claim(
                claim="Use pytest for tests",
                layer="project",
                kind="test_command",
                role="test",
                source_type="evidence",
                source_uri="tests",
            )
            store.approve(str(project["id"]))

            active = store.query("inspect README first", status="active")
            inbox = store.inbox()
            promoted = store.promote(str(session["id"]), layer="project")
            promoted_hits = store.query("inspect README first", status="active", layers=["project"])

        assert active == []
        assert any(row["id"] == session["id"] for row in inbox["proposed"])
        assert any(row["id"] == session["id"] for row in inbox["promotable"])
        assert promoted["layer"] == "project"
        assert promoted["promotion_state"] == "approved"
        assert any(row["id"] == session["id"] for row in promoted_hits)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_context_packet_is_written_before_provider_stage_and_excludes_quarantined_memory() -> None:
    workspace = _workspace_temp("p3-context-packet")
    try:
        memory_context = [
            {
                "id": "mem_project",
                "layer": "project",
                "kind": "test_command",
                "role": "test",
                "claim": "Use pytest for tests",
                "status": "active",
                "promotion_state": "approved",
                "source_type": "manual",
            },
            {
                "id": "mem_bad",
                "layer": "project",
                "kind": "project_convention",
                "role": "any",
                "claim": "Do not use tests",
                "status": "quarantined",
                "promotion_state": "quarantined",
            },
        ]
        result = SupervisorRuntime(workspace).run(
            "make a tiny change with layered memory",
            provider="mock",
            workflow_name="dev",
            require_approval=set(),
            automation={"memory_context": memory_context, "roles": ["implement", "test"]},
        )
        packets = sorted((result.run_dir / "context_packets").glob("*.json"))
        packet = json.loads(packets[0].read_text(encoding="utf-8"))
        with Blackboard(result.run_dir) as board:
            artifacts = board.table_rows("artifacts", run_id=result.run_id)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert result.status == RunStatus.COMPLETED
    assert packets
    assert packet["schema"] == "muxdev.context_packet.v1"
    assert packet["context_packet_hash"].startswith("sha256:")
    assert packet["task"]["task_id"] == result.run_id
    assert packet["project"]["long_term_memory"][0]["id"] == "mem_project"
    assert "mem_bad" not in json.dumps(packet, ensure_ascii=False)
    assert any(row["kind"] == "context_packet" for row in artifacts)


def test_memory_cli_inbox_and_promote() -> None:
    workspace = _workspace_temp("p3-memory-cli")
    previous = Path.cwd()
    try:
        os.chdir(workspace)
        with MemoryStore(workspace) as store:
            memory = store.propose_claim(claim="Run note worth reviewing", layer="run", scope_id="run_1")

        inbox = runner.invoke(app, ["memory", "inbox", "--json"])
        promote = runner.invoke(app, ["memory", "promote", str(memory["id"]), "--layer", "project", "--json"])

        assert inbox.exit_code == 0
        assert promote.exit_code == 0
        assert json.loads(promote.stdout)["layer"] == "project"
    finally:
        os.chdir(previous)
        shutil.rmtree(workspace, ignore_errors=True)


def _workspace_temp(prefix: str) -> Path:
    path = Path(".test_workspaces") / f"{prefix}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()
