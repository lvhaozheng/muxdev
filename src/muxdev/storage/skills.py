"""Product Skill bindings, immutable snapshots, and audited usage facts."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Mapping
from uuid import uuid4


SKILL_TABLES = ("skill_bindings", "skill_snapshots", "skill_usage")
SKILL_SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS skill_bindings(
      binding_id TEXT PRIMARY KEY, scope TEXT NOT NULL, conversation_id TEXT,
      assignment_id TEXT, qualified_name TEXT NOT NULL, revision TEXT NOT NULL,
      required INTEGER NOT NULL, enabled INTEGER NOT NULL, created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL, metadata TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS skill_snapshots(
      snapshot_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, assignment_id TEXT,
      qualified_name TEXT NOT NULL, version TEXT NOT NULL, revision TEXT NOT NULL,
      source_id TEXT NOT NULL, snapshot_path TEXT NOT NULL, permissions TEXT NOT NULL,
      created_at TEXT NOT NULL, metadata TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS skill_usage(
      usage_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, assignment_id TEXT,
      session_id TEXT, generation INTEGER, qualified_name TEXT NOT NULL,
      version TEXT NOT NULL, revision TEXT NOT NULL, source_id TEXT NOT NULL,
      relative_file TEXT NOT NULL, digest TEXT NOT NULL, consumer TEXT NOT NULL,
      activation TEXT NOT NULL, capture_grade TEXT NOT NULL,
      created_at TEXT NOT NULL, metadata TEXT NOT NULL
    )""",
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    result = dict(row)
    for field in ("metadata", "permissions"):
        if isinstance(result.get(field), str):
            try:
                result[field] = json.loads(result[field])
            except json.JSONDecodeError:
                pass
    return result


def ensure_skill_schema(connection: Any) -> None:
    connection.execute(
        """CREATE INDEX IF NOT EXISTS ix_skill_bindings_conversation
           ON skill_bindings(conversation_id, enabled)"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS ix_skill_usage_conversation
           ON skill_usage(conversation_id, created_at)"""
    )


class SkillStoreMixin:
    connection: Any

    def bind_skill(
        self,
        qualified_name: str,
        revision: str,
        *,
        scope: str = "project",
        conversation_id: str | None = None,
        assignment_id: str | None = None,
        required: bool = False,
        enabled: bool = True,
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        identity = "\0".join(
            [scope, conversation_id or "", assignment_id or "", qualified_name]
        )
        binding_id = (
            "skillbind_"
            + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        )
        now = _now()
        self.connection.execute(
            """INSERT INTO skill_bindings(
              binding_id, scope, conversation_id, assignment_id, qualified_name,
              revision, required, enabled, created_at, updated_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(binding_id) DO UPDATE SET
              revision=excluded.revision, required=excluded.required,
              enabled=excluded.enabled, updated_at=excluded.updated_at,
              metadata=excluded.metadata""",
            (
                binding_id,
                scope,
                conversation_id,
                assignment_id,
                qualified_name,
                revision,
                int(required),
                int(enabled),
                now,
                now,
                _json(metadata or {}),
            ),
        )
        self.connection.commit()
        row = self.connection.execute(
            "SELECT * FROM skill_bindings WHERE binding_id = ?",
            (binding_id,),
        ).fetchone()
        return _decode(row)

    def list_skill_bindings(
        self,
        *,
        conversation_id: str | None = None,
        assignment_id: str | None = None,
        enabled_only: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[object] = []
        if conversation_id is not None:
            clauses.append("(scope = 'project' OR conversation_id = ?)")
            values.append(conversation_id)
        if assignment_id is not None:
            clauses.append("(assignment_id IS NULL OR assignment_id = ?)")
            values.append(assignment_id)
        if enabled_only:
            clauses.append("enabled = 1")
        sql = "SELECT * FROM skill_bindings"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY qualified_name"
        return [
            _decode(row)
            for row in self.connection.execute(sql, values).fetchall()
        ]

    def record_skill_snapshot(
        self,
        *,
        conversation_id: str,
        assignment_id: str | None,
        qualified_name: str,
        version: str,
        revision: str,
        source_id: str,
        snapshot_path: str,
        permissions: Mapping[str, object],
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        identity = "\0".join(
            [conversation_id, assignment_id or "", qualified_name, revision]
        )
        snapshot_id = (
            "skillsnap_"
            + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        )
        self.connection.execute(
            """INSERT INTO skill_snapshots(
              snapshot_id, conversation_id, assignment_id, qualified_name,
              version, revision, source_id, snapshot_path, permissions,
              created_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_id) DO UPDATE SET metadata=excluded.metadata""",
            (
                snapshot_id,
                conversation_id,
                assignment_id,
                qualified_name,
                version,
                revision,
                source_id,
                snapshot_path,
                _json(permissions),
                _now(),
                _json(metadata or {}),
            ),
        )
        self.connection.commit()
        row = self.connection.execute(
            "SELECT * FROM skill_snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        return _decode(row)

    def list_skill_snapshots(
        self,
        conversation_id: str,
        *,
        assignment_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM skill_snapshots WHERE conversation_id = ?"
        values: list[object] = [conversation_id]
        if assignment_id is not None:
            sql += " AND (assignment_id IS NULL OR assignment_id = ?)"
            values.append(assignment_id)
        sql += " ORDER BY qualified_name, created_at DESC"
        return [
            _decode(row)
            for row in self.connection.execute(sql, values).fetchall()
        ]

    def record_skill_usage(
        self,
        *,
        conversation_id: str,
        assignment_id: str | None,
        session_id: str | None,
        generation: int | None,
        qualified_name: str,
        version: str,
        revision: str,
        source_id: str,
        relative_file: str,
        digest: str,
        consumer: str,
        activation: str,
        capture_grade: str,
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        usage_id = f"skilluse_{uuid4().hex}"
        self.connection.execute(
            """INSERT INTO skill_usage(
              usage_id, conversation_id, assignment_id, session_id, generation,
              qualified_name, version, revision, source_id, relative_file,
              digest, consumer, activation, capture_grade, created_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                usage_id,
                conversation_id,
                assignment_id,
                session_id,
                generation,
                qualified_name,
                version,
                revision,
                source_id,
                relative_file,
                digest,
                consumer,
                activation,
                capture_grade,
                _now(),
                _json(metadata or {}),
            ),
        )
        self.connection.commit()
        row = self.connection.execute(
            "SELECT * FROM skill_usage WHERE usage_id = ?",
            (usage_id,),
        ).fetchone()
        return _decode(row)

    def list_skill_usage(
        self,
        *,
        conversation_id: str | None = None,
        assignment_id: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[object] = []
        if conversation_id is not None:
            clauses.append("conversation_id = ?")
            values.append(conversation_id)
        if assignment_id is not None:
            clauses.append("assignment_id = ?")
            values.append(assignment_id)
        sql = "SELECT * FROM skill_usage"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC"
        return [
            _decode(row)
            for row in self.connection.execute(sql, values).fetchall()
        ]


__all__ = [
    "SKILL_SCHEMA_STATEMENTS",
    "SKILL_TABLES",
    "SkillStoreMixin",
    "ensure_skill_schema",
]
