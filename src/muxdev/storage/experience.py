"""Conversation memory, Review history, and Rule binding projections."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Mapping
from uuid import uuid4


EXPERIENCE_TABLES = (
    "conversation_memory_checkpoints",
    "review_records",
    "project_rule_bindings",
    "conversation_rule_snapshots",
)


EXPERIENCE_SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS conversation_memory_checkpoints(
      checkpoint_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
      version INTEGER NOT NULL, kind TEXT NOT NULL, through_sequence INTEGER NOT NULL,
      source_hash TEXT NOT NULL, parent_checkpoint_id TEXT, content TEXT NOT NULL,
      created_by TEXT NOT NULL, created_at TEXT NOT NULL, metadata TEXT NOT NULL,
      UNIQUE(conversation_id, version),
      FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
    )""",
    """CREATE TABLE IF NOT EXISTS review_records(
      review_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, run_id TEXT,
      assignment_id TEXT, session_id TEXT, kind TEXT NOT NULL, status TEXT NOT NULL,
      prompt TEXT NOT NULL, options TEXT NOT NULL, response TEXT,
      references_json TEXT NOT NULL, actor_kind TEXT NOT NULL, actor_id TEXT NOT NULL,
      created_at TEXT NOT NULL, resolved_at TEXT, metadata TEXT NOT NULL,
      FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
    )""",
    """CREATE TABLE IF NOT EXISTS project_rule_bindings(
      rule_id TEXT NOT NULL, version INTEGER NOT NULL, enabled INTEGER NOT NULL,
      workflows TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      metadata TEXT NOT NULL, PRIMARY KEY(rule_id, version)
    )""",
    """CREATE TABLE IF NOT EXISTS conversation_rule_snapshots(
      snapshot_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, contract_id TEXT,
      version INTEGER NOT NULL, rules TEXT NOT NULL, digest TEXT NOT NULL,
      created_at TEXT NOT NULL, metadata TEXT NOT NULL,
      UNIQUE(conversation_id, version),
      FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
    )""",
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(row: Any, *json_fields: str) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for field in json_fields:
        if isinstance(result.get(field), str):
            try:
                result[field] = json.loads(str(result[field]))
            except json.JSONDecodeError:
                pass
    return result


class ExperienceStoreMixin:
    connection: Any

    def create_memory_checkpoint(
        self,
        conversation_id: str,
        *,
        kind: str,
        through_sequence: int,
        source_hash: str,
        content: Mapping[str, object],
        created_by: str,
        parent_checkpoint_id: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        if kind not in {"automatic", "correction", "consolidated"}:
            raise ValueError(f"unsupported memory checkpoint kind: {kind}")
        version = int(
            self.connection.execute(
                """SELECT COALESCE(MAX(version), 0) + 1
                   FROM conversation_memory_checkpoints WHERE conversation_id = ?""",
                (conversation_id,),
            ).fetchone()[0]
        )
        checkpoint_id = f"memory_{uuid4().hex}"
        self.connection.execute(
            """INSERT INTO conversation_memory_checkpoints(
              checkpoint_id, conversation_id, version, kind, through_sequence,
              source_hash, parent_checkpoint_id, content, created_by, created_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                checkpoint_id,
                conversation_id,
                version,
                kind,
                max(0, int(through_sequence)),
                source_hash,
                parent_checkpoint_id,
                _json(content),
                created_by,
                _now(),
                _json(metadata or {}),
            ),
        )
        self.connection.commit()
        return self.get_memory_checkpoint(checkpoint_id) or {}

    def get_memory_checkpoint(self, checkpoint_id: str) -> dict[str, Any] | None:
        return _decode(
            self.connection.execute(
                """SELECT * FROM conversation_memory_checkpoints
                   WHERE checkpoint_id = ?""",
                (checkpoint_id,),
            ).fetchone(),
            "content",
            "metadata",
        )

    def latest_memory_checkpoint(self, conversation_id: str) -> dict[str, Any] | None:
        return _decode(
            self.connection.execute(
                """SELECT * FROM conversation_memory_checkpoints
                   WHERE conversation_id = ? ORDER BY version DESC LIMIT 1""",
                (conversation_id,),
            ).fetchone(),
            "content",
            "metadata",
        )

    def list_memory_checkpoints(self, conversation_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT * FROM conversation_memory_checkpoints
               WHERE conversation_id = ? ORDER BY version""",
            (conversation_id,),
        ).fetchall()
        return [_decode(row, "content", "metadata") or {} for row in rows]

    def create_review_record(
        self,
        conversation_id: str,
        *,
        kind: str,
        status: str,
        prompt: str,
        actor_kind: str,
        actor_id: str,
        run_id: str | None = None,
        assignment_id: str | None = None,
        session_id: str | None = None,
        options: list[Mapping[str, object]] | None = None,
        response: str | None = None,
        references: list[Mapping[str, object]] | None = None,
        metadata: Mapping[str, object] | None = None,
        review_id: str | None = None,
    ) -> dict[str, Any]:
        if kind not in {"interaction", "decision", "change_request"}:
            raise ValueError(f"unsupported Review kind: {kind}")
        review_id = review_id or f"review_{uuid4().hex}"
        now = _now()
        resolved_at = now if status not in {"pending", "open"} else None
        self.connection.execute(
            """INSERT INTO review_records(
              review_id, conversation_id, run_id, assignment_id, session_id,
              kind, status, prompt, options, response, references_json,
              actor_kind, actor_id, created_at, resolved_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                review_id,
                conversation_id,
                run_id,
                assignment_id,
                session_id,
                kind,
                status,
                prompt,
                _json(options or []),
                response,
                _json(references or []),
                actor_kind,
                actor_id,
                now,
                resolved_at,
                _json(metadata or {}),
            ),
        )
        self.connection.commit()
        return self.get_review_record(review_id) or {}

    def get_review_record(self, review_id: str) -> dict[str, Any] | None:
        return _decode(
            self.connection.execute(
                "SELECT * FROM review_records WHERE review_id = ?", (review_id,)
            ).fetchone(),
            "options",
            "references_json",
            "metadata",
        )

    def resolve_review_record(
        self,
        review_id: str,
        *,
        status: str,
        response: str | None = None,
        actor_id: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        current = self.get_review_record(review_id)
        if not current:
            raise FileNotFoundError(review_id)
        merged = dict(current.get("metadata") or {})
        if metadata:
            merged.update(metadata)
        self.connection.execute(
            """UPDATE review_records SET status = ?, response = ?, actor_id = ?,
               resolved_at = ?, metadata = ? WHERE review_id = ?""",
            (
                status,
                response,
                actor_id or current["actor_id"],
                _now(),
                _json(merged),
                review_id,
            ),
        )
        self.connection.commit()
        return self.get_review_record(review_id) or current

    def list_review_records(
        self,
        conversation_id: str,
        *,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM review_records WHERE conversation_id = ?"
        params: list[object] = [conversation_id]
        if run_id:
            sql += " AND run_id = ?"
            params.append(run_id)
        sql += " ORDER BY created_at, review_id"
        return [
            _decode(row, "options", "references_json", "metadata") or {}
            for row in self.connection.execute(sql, params).fetchall()
        ]

    def bind_project_rule(
        self,
        rule_id: str,
        version: int,
        *,
        enabled: bool = True,
        workflows: list[str] | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        now = _now()
        self.connection.execute(
            """INSERT INTO project_rule_bindings(
              rule_id, version, enabled, workflows, created_at, updated_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(rule_id, version) DO UPDATE SET
              enabled=excluded.enabled, workflows=excluded.workflows,
              updated_at=excluded.updated_at, metadata=excluded.metadata""",
            (
                rule_id,
                int(version),
                int(enabled),
                _json(workflows or []),
                now,
                now,
                _json(metadata or {}),
            ),
        )
        self.connection.commit()
        return self.get_project_rule_binding(rule_id, version) or {}

    def get_project_rule_binding(self, rule_id: str, version: int) -> dict[str, Any] | None:
        return _decode(
            self.connection.execute(
                """SELECT * FROM project_rule_bindings
                   WHERE rule_id = ? AND version = ?""",
                (rule_id, int(version)),
            ).fetchone(),
            "workflows",
            "metadata",
        )

    def list_project_rule_bindings(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM project_rule_bindings"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY rule_id, version"
        return [
            _decode(row, "workflows", "metadata") or {}
            for row in self.connection.execute(sql).fetchall()
        ]

    def create_conversation_rule_snapshot(
        self,
        conversation_id: str,
        *,
        contract_id: str | None,
        rules: list[Mapping[str, object]],
        digest: str,
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        version = int(
            self.connection.execute(
                """SELECT COALESCE(MAX(version), 0) + 1
                   FROM conversation_rule_snapshots WHERE conversation_id = ?""",
                (conversation_id,),
            ).fetchone()[0]
        )
        snapshot_id = f"rules_{uuid4().hex}"
        self.connection.execute(
            """INSERT INTO conversation_rule_snapshots(
              snapshot_id, conversation_id, contract_id, version, rules,
              digest, created_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot_id,
                conversation_id,
                contract_id,
                version,
                _json(rules),
                digest,
                _now(),
                _json(metadata or {}),
            ),
        )
        self.connection.commit()
        return self.latest_conversation_rule_snapshot(conversation_id) or {}

    def latest_conversation_rule_snapshot(
        self, conversation_id: str
    ) -> dict[str, Any] | None:
        return _decode(
            self.connection.execute(
                """SELECT * FROM conversation_rule_snapshots
                   WHERE conversation_id = ? ORDER BY version DESC LIMIT 1""",
                (conversation_id,),
            ).fetchone(),
            "rules",
            "metadata",
        )


__all__ = [
    "EXPERIENCE_SCHEMA_STATEMENTS",
    "EXPERIENCE_TABLES",
    "ExperienceStoreMixin",
]
