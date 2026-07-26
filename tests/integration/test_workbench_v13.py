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


def test_rule_source_upload_archive_restore_preserves_bound_version(
    workspace: Path,
) -> None:
    registry = _registry(workspace / ".global")
    project_id = str(registry.register(workspace)["project_id"])
    client = TestClient(create_app(workspace, workbench=registry))

    uploaded = client.post(
        "/api/v2/rule-sources/upload?filename=design.html",
        content=b"<html><body><main><h1>Design</h1><p>Keep evidence.</p></main></body></html>",
        headers={"content-type": "text/html"},
    )
    assert uploaded.status_code == 201, uploaded.text
    source = uploaded.json()
    assert source["local_markdown_path"].startswith("rule-sources/")
    assert "# Design" in source["markdown"]
    assert (registry.store.path.parent / source["local_markdown_path"]).is_file()

    created = client.post(
        "/api/v2/rules",
        json={
            "rule_id": "team.design",
            "version": 1,
            "title": "Team design",
            "kind": "document_template",
            "workflows": ["design"],
            "instructions": source["markdown"],
            "template": source["markdown"],
            "enforcement": "advisory",
            "source_documents": [
                {key: value for key, value in source.items() if key != "markdown"}
            ],
        },
    )
    assert created.status_code == 201, created.text
    assert client.put(
        f"/api/v2/projects/{project_id}/rules/team.design",
        json={"version": 1, "enabled": True, "workflows": ["design"]},
    ).status_code == 200

    archived = client.delete("/api/v2/rules/team.design")
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert not any(item["rule_id"] == "team.design" for item in client.get("/api/v2/rules").json())
    bound = client.get(f"/api/v2/projects/{project_id}/rules").json()["bindings"][0]
    assert bound["available"] is True
    assert bound["rule"]["rule_id"] == "team.design"

    restored = client.post("/api/v2/rules/team.design/restore")
    assert restored.status_code == 200
    assert restored.json()["status"] == "active"
    registry.store.close()


def test_skill_source_review_binding_drift_and_disconnect_are_non_destructive(
    workspace: Path,
) -> None:
    registry = _registry(workspace / ".global")
    project_id = str(registry.register(workspace)["project_id"])
    client = TestClient(create_app(workspace, workbench=registry))
    source_root = workspace / "external-skill-source"
    skill_root = source_root / "api-skill"
    skill_root.mkdir(parents=True)
    skill_file = skill_root / "SKILL.md"
    skill_file.write_text(
        (
            "---\nname: api-skill\n"
            "description: API managed Skill\n"
            "version: 1.0.0\n---\n\n# API Skill\n\nOriginal.\n"
        ),
        encoding="utf-8",
    )

    created = client.post(
        "/api/v2/skill-sources",
        json={"path": str(source_root), "mode": "connect"},
    )
    assert created.status_code == 201, created.text
    source = created.json()
    source_id = source["source_id"]
    assert source["trust_state"] == "needs_review"
    assert source["enabled"] is False
    assert client.patch(
        f"/api/v2/skill-sources/{source_id}",
        json={"enabled": True},
    ).status_code == 409

    trusted = client.patch(
        f"/api/v2/skill-sources/{source_id}",
        json={"trust_state": "user_trusted", "enabled": True},
    )
    assert trusted.status_code == 200, trusted.text
    catalog = client.get(f"/api/v2/projects/{project_id}/skills").json()
    skill = next(item for item in catalog["catalog"] if item["name"] == "api-skill")
    binding = client.put(
        f"/api/v2/projects/{project_id}/skills/{skill['qualified_name']}/binding",
        json={"required": True, "enabled": True},
    )
    assert binding.status_code == 200, binding.text
    frozen_revision = binding.json()["revision"]

    skill_file.write_text(
        skill_file.read_text(encoding="utf-8").replace("Original.", "Drifted."),
        encoding="utf-8",
    )
    rescanned = client.post(f"/api/v2/skill-sources/{source_id}/rescan")
    assert rescanned.status_code == 200, rescanned.text
    assert rescanned.json()["metadata"]["drifted"] is True
    assert rescanned.json()["revision"] != frozen_revision
    bindings = client.get(f"/api/v2/projects/{project_id}/skills").json()["bindings"]
    assert bindings[0]["revision"] == frozen_revision

    disconnected = client.delete(f"/api/v2/skill-sources/{source_id}")
    assert disconnected.status_code == 200
    assert disconnected.json()["status"] == "disconnected"
    assert skill_file.is_file()
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
