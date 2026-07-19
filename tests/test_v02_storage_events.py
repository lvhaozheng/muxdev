from __future__ import annotations

import json
import shutil
import sqlite3
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from muxdev.api.web import create_app
from muxdev.daemon.paths import default_daemon_paths
from muxdev.daemon.tasks import TaskManager
from muxdev.domain.state_events import (
    RUN_CREATED,
    RUN_TRANSITIONED,
    InvalidStateTransition,
    StateEventEnvelope,
    initial_run_state,
    reduce_run_state,
)
from muxdev.services.storage_admin import (
    StorageArchiveError,
    create_storage_backup,
    restore_storage_backup,
    verify_storage_backup,
)
from muxdev.storage import Blackboard, MemoryStore, MigrationChecksumError, UnsupportedSchemaError


@pytest.fixture
def workspace() -> Path:
    root = Path(".test_workspaces") / f"v02_storage_{uuid4().hex}"
    root.mkdir(parents=True)
    (root / ".muxdev").mkdir()
    try:
        yield root.resolve()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _create_run(board: Blackboard, workspace: Path, run_id: str = "run_state") -> None:
    board.create_run(
        run_id=run_id,
        task="state test",
        workflow="software-dev",
        provider="mock",
        workspace=workspace,
        worktree=workspace / ".muxdev" / "runs" / run_id / "worktree",
    )


def test_reducer_is_deterministic_and_requires_recovery_reason() -> None:
    created = StateEventEnvelope.create(
        run_id="run_reducer", sequence=1, event_type=RUN_CREATED,
        idempotency_key="create", payload={"task": "test"},
    )
    completed = StateEventEnvelope.create(
        run_id="run_reducer", sequence=2, event_type=RUN_TRANSITIONED,
        idempotency_key="complete", payload={"from_status": "created", "to_status": "completed"},
        prev_hash=created.event_hash,
    )
    # A compatibility bridge is a storage concern; the pure reducer keeps the
    # public contract strict.
    with pytest.raises(InvalidStateTransition):
        reduce_run_state(reduce_run_state(initial_run_state("run_reducer"), created), completed)

    running = StateEventEnvelope.create(
        run_id="run_reducer", sequence=2, event_type=RUN_TRANSITIONED,
        idempotency_key="running", payload={"from_status": "created", "to_status": "running"},
        prev_hash=created.event_hash,
    )
    done = StateEventEnvelope.create(
        run_id="run_reducer", sequence=3, event_type=RUN_TRANSITIONED,
        idempotency_key="done", payload={"from_status": "running", "to_status": "completed"},
        prev_hash=running.event_hash,
    )
    reopened = StateEventEnvelope.create(
        run_id="run_reducer", sequence=4, event_type=RUN_TRANSITIONED,
        idempotency_key="reopen", payload={"from_status": "completed", "to_status": "running"},
        prev_hash=done.event_hash,
    )
    state = initial_run_state("run_reducer")
    for event in (created, running, done):
        state = reduce_run_state(state, event)
    with pytest.raises(InvalidStateTransition):
        reduce_run_state(state, reopened)
    allowed = StateEventEnvelope.create(
        run_id="run_reducer", sequence=4, event_type=RUN_TRANSITIONED,
        idempotency_key="reopen-safe",
        payload={"from_status": "completed", "to_status": "running", "recovery_reason": "repair"},
        prev_hash=done.event_hash,
    )
    assert reduce_run_state(state, allowed)["status"] == "running"


def test_event_projection_is_atomic_idempotent_and_replayable(workspace: Path) -> None:
    notifications: list[dict[str, object]] = []
    with Blackboard(workspace / ".muxdev" / "runs" / "run_state", event_sink=notifications.append) as board:
        _create_run(board, workspace)
        first = board.set_run_status("run_state", "running", idempotency_key="run:start")
        duplicate = board.set_run_status("run_state", "running", idempotency_key="run:start")
        assert first.event_id == duplicate.event_id
        assert len(board.list_state_events("run_state")) == 2
        assert board.replay_run("run_state")["matches"] is True

        before = len(board.list_state_events("run_state"))
        with pytest.raises(RuntimeError, match="injected"):
            with board.unit_of_work():
                board.set_run_status("run_state", "paused_budget", idempotency_key="run:pause")
                assert len(notifications) == 2
                raise RuntimeError("injected failure")
        assert len(board.list_state_events("run_state")) == before
        assert board.get_run("run_state")["status"] == "running"
        assert len(notifications) == 2


def test_one_hundred_concurrent_events_have_contiguous_sequences(workspace: Path) -> None:
    run_dir = workspace / ".muxdev" / "runs" / "run_concurrent"
    with Blackboard(run_dir) as board:
        _create_run(board, workspace, "run_concurrent")
        board.set_run_status("run_concurrent", "running", idempotency_key="run:start")

        def append(index: int) -> None:
            board.set_run_status("run_concurrent", "running", idempotency_key=f"heartbeat:{index}")

        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(append, range(100)))
        events = board.list_state_events("run_concurrent")
        assert [event.sequence for event in events] == list(range(1, 103))
        assert len({event.event_id for event in events}) == 102
        assert board.replay_run("run_concurrent")["matches"] is True


def test_legacy_baseline_migration_and_readonly_detection(workspace: Path) -> None:
    legacy = workspace / "legacy.sqlite"
    conn = sqlite3.connect(legacy)
    conn.execute(
        """
        CREATE TABLE runs (
          run_id TEXT PRIMARY KEY, task TEXT NOT NULL, workflow TEXT NOT NULL,
          provider TEXT NOT NULL, status TEXT NOT NULL, workspace TEXT NOT NULL,
          worktree TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT INTO runs VALUES ('legacy', 'task', 'software-dev', 'mock', 'created', ?, ?, 'now', 'now')",
        (str(workspace), str(workspace / "worktree")),
    )
    conn.commit()
    conn.close()
    with Blackboard(workspace, db_path=legacy) as board:
        assert board.storage_health()["schema_version"] == 7
        board.set_run_status("legacy", "running")
        assert [event.event_type for event in board.list_state_events("legacy")][:1] == ["legacy_run.imported"]
        assert board.replay_run("legacy")["history_complete"] is False
        assert board.replay_run("legacy")["matches"] is True
    assert list((workspace / "backups" / "migrations").glob("*pre-migration*.sqlite"))

    readonly_legacy = workspace / "readonly-legacy.sqlite"
    conn = sqlite3.connect(readonly_legacy)
    conn.execute("CREATE TABLE runs(run_id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()
    with Blackboard(workspace, db_path=readonly_legacy, readonly=True) as board:
        health = board.storage_health(check_integrity=False)
        assert health["migration_required"] is True
        assert health["schema_version"] == 0


def test_migration_checksum_and_newer_schema_are_rejected(workspace: Path) -> None:
    database = workspace / "schema.sqlite"
    with Blackboard(workspace, db_path=database):
        pass
    conn = sqlite3.connect(database)
    conn.execute("UPDATE schema_migrations SET checksum='tampered' WHERE component='blackboard' AND version=2")
    conn.commit()
    conn.close()
    with pytest.raises(MigrationChecksumError):
        Blackboard(workspace, db_path=database)

    newer = workspace / "newer.sqlite"
    conn = sqlite3.connect(newer)
    conn.execute(
        "CREATE TABLE schema_migrations(component TEXT, version INTEGER, name TEXT, checksum TEXT, applied_at TEXT, PRIMARY KEY(component, version))"
    )
    conn.execute("INSERT INTO schema_migrations VALUES ('blackboard', 99, 'future', 'x', 'now')")
    conn.commit()
    conn.close()
    with pytest.raises(UnsupportedSchemaError):
        Blackboard(workspace, db_path=newer)


def test_blackboard_and_memory_share_durable_sqlite_defaults(workspace: Path) -> None:
    with Blackboard(workspace / ".muxdev" / "runs" / "run_health") as board:
        assert board.storage_health()["journal_mode"] == "wal"
        assert board.storage_health()["synchronous"] == "full"
    with MemoryStore(workspace) as memory:
        assert memory.storage_health()["journal_mode"] == "wal"
        assert memory.storage_health()["synchronous"] == "full"


@pytest.mark.integration
def test_backup_verify_and_restore_round_trip(workspace: Path) -> None:
    paths = default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure()
    with Blackboard(paths.data_dir, db_path=paths.db_path) as board:
        _create_run(board, workspace, "run_backup")
        board.set_run_status("run_backup", "running")
    with MemoryStore(workspace) as memory:
        memory.propose_claim(claim="backup memory")
    archive = workspace / "backup.zip"
    created = create_storage_backup(workspace=workspace, daemon_paths=paths, scope="all", output=archive)
    assert created["database_count"] == 2
    assert verify_storage_backup(archive)["valid"] is True

    with Blackboard(paths.data_dir, db_path=paths.db_path) as board:
        board.set_run_status("run_backup", "blocked")
    with pytest.raises(RuntimeError, match="stop"):
        restore_storage_backup(
            archive, workspace=workspace, daemon_paths=paths, daemon_running=True
        )
    restored = restore_storage_backup(
        archive, workspace=workspace, daemon_paths=paths, daemon_running=False
    )
    assert restored["restored"] is True
    assert restored["pre_restore_backup"]
    with Blackboard(paths.data_dir, db_path=paths.db_path) as board:
        assert board.get_run("run_backup")["status"] == "running"
        assert board.replay_run("run_backup")["matches"] is True


def test_backup_verifier_rejects_path_traversal(workspace: Path) -> None:
    archive = workspace / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escape.sqlite", b"not sqlite")
        bundle.writestr("manifest.json", json.dumps({"format": "muxdev.storage-backup.v1", "databases": []}))
    with pytest.raises(StorageArchiveError, match="unsafe"):
        verify_storage_backup(archive)


@pytest.mark.integration
def test_http_storage_surface_is_path_safe_and_has_no_restore(workspace: Path) -> None:
    paths = default_daemon_paths({"MUXDEV_HOME": str(workspace / "home-http")}).ensure()
    manager = TaskManager(paths=paths)
    try:
        with manager.board() as board:
            _create_run(board, workspace, "run_http")
            board.set_run_status("run_http", "running")
        client = TestClient(create_app(task_manager=manager))
        status = client.get("/api/storage/status?scope=daemon")
        assert status.status_code == 200
        assert "path" not in status.json()["databases"][0]
        blocked = client.post(
            "/api/storage/backups",
            json={"scope": "daemon"},
            headers={"Origin": "https://evil.example"},
        )
        assert blocked.status_code == 403
        backup = client.post("/api/storage/backups", json={"scope": "daemon"})
        assert backup.status_code == 200
        assert "archive" not in backup.json()
        backup_id = backup.json()["backup_id"]
        assert client.post(f"/api/storage/backups/{backup_id}/verify").json()["valid"] is True
        replay = client.get("/api/runs/run_http/replay")
        assert replay.status_code == 200
        assert replay.json()["matches"] is True
        paths_in_openapi = set(client.get("/openapi.json").json()["paths"])
        assert not any("restore" in path for path in paths_in_openapi if path.startswith("/api/storage"))
    finally:
        manager.close(timeout=30)
