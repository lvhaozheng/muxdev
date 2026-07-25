from __future__ import annotations

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from muxdev.api import create_app
from muxdev.services.context import build_conversation_context_pack
from muxdev.storage import ControlStore
from muxdev.workbench import WorkbenchRegistry, WorkbenchStore


def _registry(root: Path) -> WorkbenchRegistry:
    return WorkbenchRegistry(WorkbenchStore(root / "workbench.sqlite"))


def test_project_scoped_apis_isolate_databases_and_remove_only_registration(
    workspace: Path,
) -> None:
    project_a = workspace / "project-a"
    project_b = workspace / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    registry = _registry(workspace / ".global")
    registered_a = registry.register(project_a)
    registered_b = registry.register(project_b)
    project_a_id = str(registered_a["project_id"])
    project_b_id = str(registered_b["project_id"])
    with ControlStore(project_a) as store:
        store.create_conversation(
            conversation_id="conv_only_a",
            title="Only A",
            goal="stay isolated",
            status="idle",
            metadata={"worktree": str(project_a)},
            mode="direct",
        )

    client = TestClient(
        create_app(project_a, workbench=registry, instance_id="daemon_test")
    )
    a_api = f"/api/v2/projects/{project_a_id}"
    b_api = f"/api/v2/projects/{project_b_id}"
    assert [item["conversation_id"] for item in client.get(f"{a_api}/conversations").json()] == [
        "conv_only_a"
    ]
    assert client.get(f"{b_api}/conversations").json() == []
    assert client.get(f"{b_api}/conversations/conv_only_a").status_code == 404
    assert client.get("/api/v2/conversations").status_code == 404

    snapshot = client.get(f"/api/v2/projects/{project_a_id}/snapshot")
    assert snapshot.status_code == 200
    assert snapshot.json()["last_conversation_id"] == "conv_only_a"

    removed = client.delete(f"/api/v2/projects/{project_b_id}")
    assert removed.status_code == 200
    assert removed.json()["data_deleted"] is False
    assert project_b.is_dir()
    assert (project_b / ".muxdev" / "control.sqlite").is_file()
    registry.store.close()


def test_v12_to_v13_backup_memory_correction_and_shared_context(
    workspace: Path,
) -> None:
    with ControlStore(workspace) as store:
        store.create_conversation(
            conversation_id="conv_memory",
            title="Memory",
            goal="retain the whole task",
            status="idle",
            metadata={"worktree": str(workspace)},
            mode="direct",
            primary_agent_id="mock",
        )
        store.append_conversation_event(
            "conv_memory",
            "user.message",
            {"content": "Preserve this decision"},
            actor="developer",
        )
    database = workspace / ".muxdev" / "control.sqlite"
    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT OR REPLACE INTO schema_migrations(version, applied_at, checksum) VALUES (12, 'now', 'v12')"
    )
    connection.execute("DELETE FROM schema_migrations WHERE version = 13")
    for table in (
        "conversation_memory_checkpoints",
        "review_records",
        "project_rule_bindings",
        "conversation_rule_snapshots",
    ):
        connection.execute(f"DROP TABLE {table}")
    connection.commit()
    connection.close()

    with ControlStore(workspace) as migrated:
        assert migrated.connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = 13"
        ).fetchone()
        checkpoint = migrated.create_memory_checkpoint(
            "conv_memory",
            kind="automatic",
            through_sequence=1,
            source_hash="sha256:source",
            content={"goal": "retain the whole task", "corrections": []},
            created_by="runtime",
        )
        migrated.append_conversation_event(
            "conv_memory",
            "memory.checkpoint_created",
            {"checkpoint_id": checkpoint["checkpoint_id"]},
            actor="runtime",
        )
        contract = {
            "contract_id": "contract_memory",
            "goal": "retain the whole task",
            "acceptance_criteria": ["memory remains attributable"],
            "allowed_scope": ["**/*"],
            "profile": "standard",
            "policy": {"delivery_standard": {}},
        }
        first = build_conversation_context_pack(
            workspace,
            workspace,
            migrated,
            conversation_id="conv_memory",
            contract=contract,
            task="first agent",
        )
        second = build_conversation_context_pack(
            workspace,
            workspace,
            migrated,
            conversation_id="conv_memory",
            contract=contract,
            task="second agent",
        )
        assert first.manifest["memory_checkpoint_id"] == second.manifest[
            "memory_checkpoint_id"
        ]
        assert "retain the whole task" in first.text

    backups = list(
        (workspace / ".muxdev" / "backups").glob(
            "schema-v12-to-v13-*/control.sqlite"
        )
    )
    assert len(backups) == 1


def test_rule_library_binding_freezing_review_and_memory_apis(
    workspace: Path,
) -> None:
    registry = _registry(workspace / ".global")
    project = registry.register(workspace)
    project_id = str(project["project_id"])
    client = TestClient(create_app(workspace, workbench=registry))

    rule = {
        "rule_id": "review.documentation",
        "version": 1,
        "title": "Documentation review",
        "description": "Keep public behavior documented.",
        "kind": "code_standard",
        "workflows": ["change"],
        "instructions": "Update user-facing documentation when behavior changes.",
        "enforcement": "advisory",
    }
    assert client.post("/api/v2/rules", json=rule).status_code == 201
    binding = client.put(
        f"/api/v2/projects/{project_id}/rules/review.documentation",
        json={"version": 1, "enabled": True, "workflows": ["change"]},
    )
    assert binding.status_code == 200

    api = f"/api/v2/projects/{project_id}"
    created = client.post(
        f"{api}/conversations",
        json={
            "goal": "Inspect README.md and explain the result",
            "agent_id": "mock",
            "deliverables": [{"type": "answer"}],
            "auto_start_when_ready": False,
        },
    )
    assert created.status_code == 201, created.text
    conversation_id = created.json()["conversation"]["conversation_id"]
    snapshot = client.get(f"{api}/conversations/{conversation_id}/snapshot").json()
    assert snapshot["tool_summaries"]["rules"]["count"] == 1
    assert snapshot["tool_summaries"]["rules"]["frozen"][0]["rule_id"] == (
        "review.documentation"
    )

    compacted = client.post(
        f"{api}/conversations/{conversation_id}/memory/compact"
    )
    assert compacted.status_code == 200
    corrected = client.post(
        f"{api}/conversations/{conversation_id}/memory/corrections",
        json={"correction": "README.md is the only documentation target."},
    )
    assert corrected.status_code == 201
    checkpoints = client.get(
        f"{api}/conversations/{conversation_id}/memory/checkpoints"
    ).json()
    assert checkpoints[-1]["kind"] == "correction"
    assert checkpoints[-1]["version"] > checkpoints[0]["version"]

    started = time.perf_counter()
    projects = client.get("/api/v2/projects")
    assert projects.status_code == 200
    assert time.perf_counter() - started < 0.2
    registry.store.close()


def test_workbench_store_is_thread_safe_and_lists_100_projects_under_budget(
    workspace: Path,
) -> None:
    registry = _registry(workspace / ".global")
    project_ids: list[str] = []
    for index in range(100):
        path = workspace / f"project-{index:03d}"
        path.mkdir()
        project = registry.store.register_project(path)
        project_ids.append(str(project["project_id"]))

    started = time.perf_counter()
    snapshots = registry.list_projects()
    elapsed = time.perf_counter() - started
    assert len(snapshots) == 100
    assert elapsed < 0.2

    def touch(project_id: str) -> str:
        registry.store.update_project(project_id, status="active", touch=True)
        return str(registry.store.get_project(project_id)["project_id"])

    with ThreadPoolExecutor(max_workers=12) as executor:
        touched = list(executor.map(touch, project_ids * 3))
    assert touched[:100] == project_ids
    registry.store.close()
