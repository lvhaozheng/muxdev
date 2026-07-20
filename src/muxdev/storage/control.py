"""Compact SQLite fact store for the trusted-delivery control plane.

The schema deliberately contains twelve tables.  Flexible facts live in typed
JSON payloads; new product features therefore do not imply new projection
tables.  Legacy v7 blackboards are imported as immutable events and artifacts.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence
from uuid import uuid4


SCHEMA_VERSION = 8
CORE_TABLES = (
    "schema_migrations",
    "runs",
    "stages",
    "jobs",
    "interactions",
    "events",
    "artifacts",
    "provider_certifications",
    "provider_outcomes",
    "routing_decisions",
    "attestations",
    "skill_locks",
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for key in ("payload", "metadata", "policy", "result"):
        if key in result and isinstance(result[key], str):
            try:
                result[key] = json.loads(result[key])
            except json.JSONDecodeError:
                pass
    return result


class ControlStore:
    """Explicit repository over the compact control-plane schema."""

    def __init__(self, workspace: Path | str, *, database: Path | None = None) -> None:
        self.workspace = Path(workspace).resolve()
        self.root = self.workspace / ".muxdev"
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = (database or self.root / "control.sqlite").resolve()
        self.connection = sqlite3.connect(self.path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._create_schema()

    def __enter__(self) -> "ControlStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            yield self.connection
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def _create_schema(self) -> None:
        statements = _schema_statements()
        with self.transaction() as conn:
            for statement in statements:
                conn.execute(statement)
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at, checksum) VALUES (?, ?, ?)",
                (SCHEMA_VERSION, utc_now(), _schema_checksum(statements)),
            )

    def table_names(self) -> tuple[str, ...]:
        rows = self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def create_run(
        self,
        *,
        run_id: str,
        task: str,
        workflow: str,
        profile: str,
        provider: str,
        policy_hash: str,
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        self.connection.execute(
            """INSERT INTO runs(
                run_id, task, workflow, profile, provider, status, policy_hash,
                current_stage, created_at, updated_at, metadata
            ) VALUES (?, ?, ?, ?, ?, 'created', ?, NULL, ?, ?, ?)""",
            (run_id, task, workflow, profile, provider, policy_hash, now, now, _json(metadata or {})),
        )
        self.connection.commit()
        self.append_event(run_id, "run.created", {"workflow": workflow, "profile": profile, "provider": provider})
        return self.get_run(run_id) or {}

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return _decode_row(row)

    def list_runs(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM runs"
        params: list[object] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(limit, 1000)))
        return [_decode_row(row) or {} for row in self.connection.execute(sql, params).fetchall()]

    def update_run(self, run_id: str, *, status: str, current_stage: str | None = None) -> None:
        self.connection.execute(
            "UPDATE runs SET status = ?, current_stage = ?, updated_at = ? WHERE run_id = ?",
            (status, current_stage, utc_now(), run_id),
        )
        self.connection.commit()
        self.append_event(run_id, "run.status_changed", {"status": status, "current_stage": current_stage})

    def cancel_run(self, run_id: str) -> None:
        self.update_run(run_id, status="aborted")

    def start_job(self, run_id: str, *, kind: str, payload: Mapping[str, object] | None = None) -> str:
        job_id = f"job_{uuid4().hex}"
        self.connection.execute(
            """INSERT INTO jobs(
              job_id, run_id, kind, status, attempt, available_at, lease_until, payload
            ) VALUES (?, ?, ?, 'running', 1, ?, NULL, ?)""",
            (job_id, run_id, kind, utc_now(), _json(payload or {})),
        )
        self.connection.commit()
        self.append_event(run_id, "job.started", {"job_id": job_id, "kind": kind})
        return job_id

    def finish_job(self, job_id: str, *, status: str) -> None:
        row = self.connection.execute("SELECT run_id FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            raise KeyError(job_id)
        self.connection.execute("UPDATE jobs SET status = ?, lease_until = NULL WHERE job_id = ?", (status, job_id))
        self.connection.commit()
        self.append_event(str(row["run_id"]), "job.finished", {"job_id": job_id, "status": status})

    def jobs(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM jobs WHERE run_id = ? ORDER BY rowid", (run_id,)).fetchall()
        return [_decode_row(row) or {} for row in rows]

    def upsert_stage(
        self,
        run_id: str,
        stage_id: str,
        *,
        role: str | None,
        provider: str | None,
        status: str,
        attempt: int = 1,
        result: Mapping[str, object] | None = None,
    ) -> None:
        now = utc_now()
        self.connection.execute(
            """INSERT INTO stages(run_id, stage_id, role, provider, status, attempt, started_at, completed_at, result)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, stage_id) DO UPDATE SET
              role=excluded.role, provider=excluded.provider, status=excluded.status,
              attempt=excluded.attempt, completed_at=excluded.completed_at, result=excluded.result""",
            (
                run_id,
                stage_id,
                role,
                provider,
                status,
                attempt,
                now,
                now if status in {"completed", "failed", "skipped"} else None,
                _json(result or {}),
            ),
        )
        self.connection.commit()

    def stages(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM stages WHERE run_id = ? ORDER BY rowid", (run_id,)
        ).fetchall()
        return [_decode_row(row) or {} for row in rows]

    def append_event(
        self,
        run_id: str,
        event_type: str,
        payload: Mapping[str, object],
        *,
        stage_id: str | None = None,
        event_id: str | None = None,
    ) -> str:
        previous = self.connection.execute(
            "SELECT event_hash FROM events WHERE run_id = ? ORDER BY sequence DESC LIMIT 1", (run_id,)
        ).fetchone()
        previous_hash = str(previous[0]) if previous else "sha256:" + "0" * 64
        created_at = utc_now()
        sequence = int(
            self.connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
        )
        event_id = event_id or f"evt_{uuid4().hex}"
        fact = {
            "event_id": event_id,
            "run_id": run_id,
            "stage_id": stage_id,
            "type": event_type,
            "sequence": sequence,
            "created_at": created_at,
            "payload": payload,
            "previous_hash": previous_hash,
        }
        event_hash = "sha256:" + hashlib.sha256(_json(fact).encode()).hexdigest()
        self.connection.execute(
            """INSERT INTO events(
              event_id, run_id, stage_id, type, sequence, created_at, payload, previous_hash, event_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_id, run_id, stage_id, event_type, sequence, created_at, _json(payload), previous_hash, event_hash),
        )
        self.connection.commit()
        return event_id

    def events(self, run_id: str, *, after: int = 0) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM events WHERE run_id = ? AND sequence > ? ORDER BY sequence", (run_id, after)
        ).fetchall()
        return [_decode_row(row) or {} for row in rows]

    def verify_event_chain(self, run_id: str) -> tuple[bool, list[str]]:
        errors: list[str] = []
        expected_previous = "sha256:" + "0" * 64
        for row in self.events(run_id):
            fact = {key: row[key] for key in (
                "event_id", "run_id", "stage_id", "type", "sequence", "created_at", "payload", "previous_hash"
            )}
            expected_hash = "sha256:" + hashlib.sha256(_json(fact).encode()).hexdigest()
            if row["previous_hash"] != expected_previous:
                errors.append(f"event {row['event_id']} has an invalid previous hash")
            if row["event_hash"] != expected_hash:
                errors.append(f"event {row['event_id']} has an invalid content hash")
            expected_previous = str(row["event_hash"])
        return not errors, errors

    def add_artifact(
        self,
        run_id: str,
        *,
        name: str,
        path: Path,
        kind: str,
        stage_id: str | None = None,
        media_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        resolved = path.resolve()
        data = resolved.read_bytes()
        artifact_id = f"art_{uuid4().hex}"
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        self.connection.execute(
            """INSERT INTO artifacts(
              artifact_id, run_id, stage_id, name, kind, path, digest, size, media_type, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (artifact_id, run_id, stage_id, name, kind, str(resolved), digest, len(data), media_type, utc_now()),
        )
        self.connection.commit()
        return self.artifact(artifact_id) or {}

    def artifact(self, artifact_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone()
        return _decode_row(row)

    def artifacts(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM artifacts WHERE run_id = ? ORDER BY created_at, artifact_id", (run_id,)
        ).fetchall()
        return [_decode_row(row) or {} for row in rows]

    def request_interaction(
        self,
        run_id: str,
        *,
        kind: str,
        requirement_id: str,
        prompt: str,
        stage_id: str | None = None,
    ) -> str:
        interaction_id = f"int_{uuid4().hex}"
        now = utc_now()
        self.connection.execute(
            """INSERT INTO interactions(
              interaction_id, run_id, stage_id, kind, requirement_id, prompt, status, response, created_at, responded_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', NULL, ?, NULL)""",
            (interaction_id, run_id, stage_id, kind, requirement_id, prompt, now),
        )
        self.connection.commit()
        self.append_event(run_id, "interaction.requested", {"interaction_id": interaction_id, "kind": kind})
        return interaction_id

    def respond(self, interaction_id: str, *, status: str, response: str | None = None) -> dict[str, Any]:
        if status not in {"approved", "rejected", "responded"}:
            raise ValueError("interaction status must be approved, rejected, or responded")
        self.connection.execute(
            "UPDATE interactions SET status = ?, response = ?, responded_at = ? WHERE interaction_id = ?",
            (status, response, utc_now(), interaction_id),
        )
        self.connection.commit()
        row = self.connection.execute(
            "SELECT * FROM interactions WHERE interaction_id = ?", (interaction_id,)
        ).fetchone()
        result = _decode_row(row)
        if result is None:
            raise KeyError(interaction_id)
        self.append_event(str(result["run_id"]), "interaction.responded", {"interaction_id": interaction_id, "status": status})
        return result

    def interactions(self, run_id: str, *, pending_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM interactions WHERE run_id = ?"
        if pending_only:
            sql += " AND status = 'pending'"
        sql += " ORDER BY created_at"
        return [_decode_row(row) or {} for row in self.connection.execute(sql, (run_id,)).fetchall()]

    def record_routing(self, run_id: str, payload: Mapping[str, object]) -> str:
        decision_id = f"route_{uuid4().hex}"
        self.connection.execute(
            "INSERT INTO routing_decisions(decision_id, run_id, created_at, payload) VALUES (?, ?, ?, ?)",
            (decision_id, run_id, utc_now(), _json(payload)),
        )
        self.connection.commit()
        return decision_id

    def routing(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM routing_decisions WHERE run_id = ? ORDER BY created_at", (run_id,)
        ).fetchall()
        return [_decode_row(row) or {} for row in rows]

    def record_outcome(self, run_id: str, provider: str, payload: Mapping[str, object]) -> str:
        outcome_id = f"out_{uuid4().hex}"
        self.connection.execute(
            "INSERT INTO provider_outcomes(outcome_id, run_id, provider, created_at, payload) VALUES (?, ?, ?, ?, ?)",
            (outcome_id, run_id, provider, utc_now(), _json(payload)),
        )
        self.connection.commit()
        return outcome_id

    def record_certification(self, provider: str, *, status: str, payload: Mapping[str, object]) -> str:
        certification_id = f"cert_{uuid4().hex}"
        self.connection.execute(
            """INSERT INTO provider_certifications(
              certification_id, provider, status, created_at, expires_at, payload
            ) VALUES (?, ?, ?, ?, NULL, ?)""",
            (certification_id, provider, status, utc_now(), _json(payload)),
        )
        self.connection.commit()
        return certification_id

    def latest_certification(self, provider: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM provider_certifications WHERE provider = ? ORDER BY created_at DESC LIMIT 1",
            (provider,),
        ).fetchone()
        return _decode_row(row)

    def outcomes(self, provider: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM provider_outcomes"
        params: Sequence[object] = ()
        if provider:
            sql += " WHERE provider = ?"
            params = (provider,)
        sql += " ORDER BY created_at"
        return [_decode_row(row) or {} for row in self.connection.execute(sql, params).fetchall()]

    def add_attestation(self, run_id: str, *, kind: str, path: Path, digest: str, payload: Mapping[str, object]) -> str:
        attestation_id = f"att_{uuid4().hex}"
        self.connection.execute(
            """INSERT INTO attestations(attestation_id, run_id, kind, path, digest, created_at, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (attestation_id, run_id, kind, str(path.resolve()), digest, utc_now(), _json(payload)),
        )
        self.connection.commit()
        return attestation_id

    def record_skill_lock(self, skill_name: str, version: str, digest: str, payload: Mapping[str, object]) -> str:
        lock_id = f"lock_{uuid4().hex}"
        self.connection.execute(
            """INSERT INTO skill_locks(lock_id, skill_name, version, digest, created_at, payload)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(skill_name, version) DO UPDATE SET
              lock_id=excluded.lock_id, digest=excluded.digest,
              created_at=excluded.created_at, payload=excluded.payload""",
            (lock_id, skill_name, version, digest, utc_now(), _json(payload)),
        )
        self.connection.commit()
        return lock_id


def _schema_checksum(statements: Sequence[str]) -> str:
    return hashlib.sha256("\n".join(statements).encode()).hexdigest()


def _schema_statements() -> tuple[str, ...]:
    return (
        "CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, checksum TEXT NOT NULL)",
        """CREATE TABLE IF NOT EXISTS runs(
          run_id TEXT PRIMARY KEY, task TEXT NOT NULL, workflow TEXT NOT NULL, profile TEXT NOT NULL,
          provider TEXT NOT NULL, status TEXT NOT NULL, policy_hash TEXT NOT NULL, current_stage TEXT,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS stages(
          run_id TEXT NOT NULL, stage_id TEXT NOT NULL, role TEXT, provider TEXT, status TEXT NOT NULL,
          attempt INTEGER NOT NULL, started_at TEXT, completed_at TEXT, result TEXT NOT NULL,
          PRIMARY KEY(run_id, stage_id), FOREIGN KEY(run_id) REFERENCES runs(run_id)
        )""",
        """CREATE TABLE IF NOT EXISTS jobs(
          job_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
          attempt INTEGER NOT NULL, available_at TEXT NOT NULL, lease_until TEXT, payload TEXT NOT NULL,
          FOREIGN KEY(run_id) REFERENCES runs(run_id)
        )""",
        """CREATE TABLE IF NOT EXISTS interactions(
          interaction_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, stage_id TEXT, kind TEXT NOT NULL,
          requirement_id TEXT NOT NULL, prompt TEXT NOT NULL, status TEXT NOT NULL, response TEXT,
          created_at TEXT NOT NULL, responded_at TEXT, FOREIGN KEY(run_id) REFERENCES runs(run_id)
        )""",
        """CREATE TABLE IF NOT EXISTS events(
          event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, stage_id TEXT, type TEXT NOT NULL,
          sequence INTEGER NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL,
          previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL, UNIQUE(run_id, sequence)
        )""",
        """CREATE TABLE IF NOT EXISTS artifacts(
          artifact_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, stage_id TEXT, name TEXT NOT NULL,
          kind TEXT NOT NULL, path TEXT NOT NULL, digest TEXT NOT NULL, size INTEGER NOT NULL,
          media_type TEXT NOT NULL, created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS provider_certifications(
          certification_id TEXT PRIMARY KEY, provider TEXT NOT NULL, status TEXT NOT NULL,
          created_at TEXT NOT NULL, expires_at TEXT, payload TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS provider_outcomes(
          outcome_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, provider TEXT NOT NULL,
          created_at TEXT NOT NULL, payload TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS routing_decisions(
          decision_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS attestations(
          attestation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, kind TEXT NOT NULL, path TEXT NOT NULL,
          digest TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS skill_locks(
          lock_id TEXT PRIMARY KEY, skill_name TEXT NOT NULL, version TEXT NOT NULL, digest TEXT NOT NULL,
          created_at TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(skill_name, version)
        )""",
    )


def compact_database_status(workspace: Path | str) -> dict[str, object]:
    root = Path(workspace).resolve() / ".muxdev"
    target = root / "control.sqlite"
    legacy = sorted(root.glob("runs/*/blackboard.sqlite"))
    return {
        "target": str(target),
        "exists": target.exists(),
        "schema_version": SCHEMA_VERSION if target.exists() else None,
        "legacy_databases": [str(path) for path in legacy],
    }


def migrate_workspace(workspace: Path | str) -> dict[str, object]:
    """Atomically import v7 per-run databases into the compact store."""
    workspace = Path(workspace).resolve()
    root = workspace / ".muxdev"
    root.mkdir(parents=True, exist_ok=True)
    target = root / "control.sqlite"
    legacy = sorted(root.glob("runs/*/blackboard.sqlite"))
    if target.exists() or not legacy:
        return {**compact_database_status(workspace), "migrated": False}
    lock = root / "migration.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError("another muxdev migration is in progress") from exc
    os.close(descriptor)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = root / "backups" / stamp
    temporary = root / f"control.{uuid4().hex}.tmp.sqlite"
    try:
        backup.mkdir(parents=True)
        for source in legacy:
            shutil.copy2(source, backup / f"{source.parent.name}.sqlite")
        counts = _import_legacy(workspace, temporary, legacy)
        os.replace(temporary, target)
        for source in legacy:
            source.chmod(0o444)
        return {**compact_database_status(workspace), "migrated": True, "backup": str(backup), **counts}
    finally:
        temporary.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)


def _import_legacy(workspace: Path, target: Path, sources: Sequence[Path]) -> dict[str, int]:
    run_count = stage_count = artifact_count = 0
    with ControlStore(workspace, database=target) as store:
        for source in sources:
            connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            try:
                runs = connection.execute("SELECT * FROM runs").fetchall()
                for row in runs:
                    _import_legacy_run(store, dict(row))
                    run_count += 1
                if _legacy_has_table(connection, "stages"):
                    stages = connection.execute("SELECT * FROM stages").fetchall()
                    for row in stages:
                        _import_legacy_stage(store, dict(row))
                        stage_count += 1
                if _legacy_has_table(connection, "artifacts"):
                    artifacts = connection.execute("SELECT * FROM artifacts").fetchall()
                    for row in artifacts:
                        artifact_count += int(_import_legacy_artifact(store, source.parent, dict(row)))
            finally:
                connection.close()
        _verify_import(store, runs=run_count, stages=stage_count, artifacts=artifact_count)
    return {"runs": run_count, "stages": stage_count, "artifacts": artifact_count}


def _import_legacy_run(store: ControlStore, row: Mapping[str, object]) -> None:
    run_id = str(row.get("run_id") or row.get("id"))
    if store.get_run(run_id):
        return
    store.create_run(
        run_id=run_id,
        task=str(row.get("task") or "legacy run"),
        workflow=str(row.get("workflow") or "change"),
        profile="legacy",
        provider=str(row.get("provider") or "unknown"),
        policy_hash="legacy:v2-unscored",
        metadata={"legacy": True, "source_schema": 7},
    )
    store.update_run(run_id, status=str(row.get("status") or "blocked"))
    store.append_event(run_id, "legacy_evidence", {"source_schema": 7, "rescored": False, "row": dict(row)})


def _import_legacy_stage(store: ControlStore, row: Mapping[str, object]) -> None:
    run_id = str(row.get("run_id") or "")
    if not store.get_run(run_id):
        return
    store.upsert_stage(
        run_id,
        str(row.get("stage_id") or "legacy"),
        role=str(row.get("role")) if row.get("role") else None,
        provider=None,
        status=str(row.get("status") or "failed"),
        attempt=max(1, int(row.get("attempt") or 1)),
        result={"legacy": True, "summary": str(row.get("summary") or ""), "output_path": row.get("output_path")},
    )


def _import_legacy_artifact(store: ControlStore, run_dir: Path, row: Mapping[str, object]) -> bool:
    run_id = str(row.get("run_id") or "")
    if not store.get_run(run_id):
        return False
    candidate = Path(str(row.get("path") or ""))
    if not candidate.is_absolute():
        candidate = run_dir / candidate
    if not candidate.is_file():
        store.append_event(run_id, "legacy.artifact_missing", {"name": str(row.get("name") or candidate.name)})
        return False
    store.add_artifact(
        run_id,
        name=str(row.get("name") or candidate.name),
        path=candidate,
        kind=str(row.get("kind") or "legacy"),
        stage_id=str(row.get("stage_id")) if row.get("stage_id") else None,
    )
    return True


def _verify_import(store: ControlStore, *, runs: int, stages: int, artifacts: int) -> None:
    expected = {"runs": runs, "stages": stages, "artifacts": artifacts}
    for table, count in expected.items():
        actual = int(store.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        if actual != count:
            raise RuntimeError(f"migration verification failed for {table}: expected {count}, imported {actual}")
    for row in store.connection.execute("SELECT path, digest FROM artifacts").fetchall():
        path = Path(str(row["path"]))
        actual = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if actual != row["digest"]:
            raise RuntimeError(f"migration artifact digest verification failed: {path}")


def _legacy_has_table(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table,)
    ).fetchone() is not None
