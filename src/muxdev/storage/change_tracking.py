"""Durable file-change and verification-history projections."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence
from uuid import uuid4


CHANGE_TRACKING_TABLES = (
    "file_baselines",
    "file_changes",
    "verification_attempts",
)


CHANGE_TRACKING_SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS file_baselines(
      baseline_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, run_id TEXT NOT NULL,
      path TEXT NOT NULL, existed INTEGER NOT NULL, blob_hash TEXT, git_oid TEXT,
      mode INTEGER, size INTEGER NOT NULL, created_at TEXT NOT NULL,
      UNIQUE(run_id, path), FOREIGN KEY(run_id) REFERENCES runs(run_id),
      FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
    )""",
    """CREATE TABLE IF NOT EXISTS file_changes(
      change_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, run_id TEXT NOT NULL,
      assignment_id TEXT, session_id TEXT, generation INTEGER, path TEXT NOT NULL,
      previous_path TEXT, kind TEXT NOT NULL, before_hash TEXT, after_hash TEXT,
      patch TEXT NOT NULL, additions INTEGER NOT NULL, deletions INTEGER NOT NULL,
      author TEXT NOT NULL, capture_grade TEXT NOT NULL, created_at TEXT NOT NULL,
      FOREIGN KEY(run_id) REFERENCES runs(run_id),
      FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
    )""",
    """CREATE TABLE IF NOT EXISTS verification_attempts(
      attempt_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, run_id TEXT NOT NULL,
      command TEXT NOT NULL, status TEXT NOT NULL, freshness TEXT NOT NULL,
      exit_code INTEGER, duration_ms INTEGER, summary TEXT NOT NULL,
      evidence_ref TEXT, created_at TEXT NOT NULL,
      FOREIGN KEY(run_id) REFERENCES runs(run_id),
      FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
    )""",
    """CREATE INDEX IF NOT EXISTS ix_file_changes_run_created
       ON file_changes(run_id, created_at, change_id)""",
    """CREATE INDEX IF NOT EXISTS ix_file_changes_conversation
       ON file_changes(conversation_id, created_at, change_id)""",
    """CREATE INDEX IF NOT EXISTS ix_verification_attempts_run
       ON verification_attempts(run_id, created_at, attempt_id)""",
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ChangeTrackingStoreMixin:
    """SQLite operations mixed into :class:`ControlStore`."""

    connection: Any

    def record_file_baseline(
        self,
        *,
        conversation_id: str,
        run_id: str,
        path: str,
        existed: bool,
        blob_hash: str | None,
        git_oid: str | None = None,
        mode: int | None = None,
        size: int = 0,
    ) -> dict[str, Any]:
        baseline_id = f"base_{uuid4().hex}"
        self.connection.execute(
            """INSERT OR IGNORE INTO file_baselines(
              baseline_id, conversation_id, run_id, path, existed, blob_hash,
              git_oid, mode, size, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                baseline_id,
                conversation_id,
                run_id,
                path,
                int(existed),
                blob_hash,
                git_oid,
                mode,
                max(0, int(size)),
                _now(),
            ),
        )
        self.connection.commit()
        row = self.connection.execute(
            "SELECT * FROM file_baselines WHERE run_id = ? AND path = ?",
            (run_id, path),
        ).fetchone()
        return dict(row) if row else {}

    def file_baselines(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM file_baselines WHERE run_id = ? ORDER BY path",
            (run_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def record_file_change(
        self,
        *,
        conversation_id: str,
        run_id: str,
        path: str,
        kind: str,
        before_hash: str | None,
        after_hash: str | None,
        patch: str = "",
        additions: int = 0,
        deletions: int = 0,
        author: str = "runtime",
        capture_grade: str = "observed",
        assignment_id: str | None = None,
        session_id: str | None = None,
        generation: int | None = None,
        previous_path: str | None = None,
        change_id: str | None = None,
    ) -> dict[str, Any]:
        if kind not in {"add", "modify", "delete", "rename"}:
            raise ValueError(f"unsupported file change kind: {kind}")
        if capture_grade not in {"recorded", "observed", "verified"}:
            raise ValueError(f"unsupported capture grade: {capture_grade}")
        change_id = change_id or f"chg_{uuid4().hex}"
        self.connection.execute(
            """INSERT INTO file_changes(
              change_id, conversation_id, run_id, assignment_id, session_id,
              generation, path, previous_path, kind, before_hash, after_hash,
              patch, additions, deletions, author, capture_grade, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                change_id,
                conversation_id,
                run_id,
                assignment_id,
                session_id,
                generation,
                path,
                previous_path,
                kind,
                before_hash,
                after_hash,
                patch,
                max(0, int(additions)),
                max(0, int(deletions)),
                author,
                capture_grade,
                _now(),
            ),
        )
        self.connection.commit()
        return self.get_file_change(change_id) or {}

    def get_file_change(self, change_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM file_changes WHERE change_id = ?", (change_id,)
        ).fetchone()
        return dict(row) if row else None

    def file_changes(
        self,
        run_id: str,
        *,
        capture_grade: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM file_changes WHERE run_id = ?"
        params: list[object] = [run_id]
        if capture_grade:
            sql += " AND capture_grade = ?"
            params.append(capture_grade)
        sql += " ORDER BY created_at, change_id"
        return [dict(row) for row in self.connection.execute(sql, params).fetchall()]

    def replace_verified_file_changes(
        self,
        *,
        conversation_id: str,
        run_id: str,
        changes: Sequence[Mapping[str, object]],
        assignment_id: str | None = None,
        session_id: str | None = None,
        generation: int | None = None,
    ) -> list[dict[str, Any]]:
        with self.transaction() as conn:
            conn.execute(
                "DELETE FROM file_changes WHERE run_id = ? AND capture_grade = 'verified'",
                (run_id,),
            )
        result: list[dict[str, Any]] = []
        for item in changes:
            result.append(self.record_file_change(
                conversation_id=conversation_id,
                run_id=run_id,
                assignment_id=assignment_id,
                session_id=session_id,
                generation=generation,
                path=str(item["path"]),
                previous_path=(
                    str(item["previous_path"]) if item.get("previous_path") else None
                ),
                kind=str(item["kind"]),
                before_hash=(
                    str(item["before_hash"]) if item.get("before_hash") else None
                ),
                after_hash=(
                    str(item["after_hash"]) if item.get("after_hash") else None
                ),
                patch=str(item.get("patch") or ""),
                additions=int(item.get("additions") or 0),
                deletions=int(item.get("deletions") or 0),
                author=str(item.get("author") or "runtime"),
                capture_grade="verified",
            ))
        return result

    def create_verification_attempt(
        self,
        *,
        conversation_id: str,
        run_id: str,
        command: Sequence[str],
        status: str,
        exit_code: int | None = None,
        duration_ms: int | None = None,
        summary: str = "",
        evidence_ref: str | None = None,
        freshness: str = "current",
    ) -> dict[str, Any]:
        attempt_id = f"verify_{uuid4().hex}"
        self.connection.execute(
            """INSERT INTO verification_attempts(
              attempt_id, conversation_id, run_id, command, status, freshness,
              exit_code, duration_ms, summary, evidence_ref, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                attempt_id,
                conversation_id,
                run_id,
                _json(list(command)),
                status,
                freshness,
                exit_code,
                duration_ms,
                summary,
                evidence_ref,
                _now(),
            ),
        )
        self.connection.commit()
        return self.verification_attempt(attempt_id) or {}

    def verification_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM verification_attempts WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["command"] = json.loads(str(result["command"]))
        return result

    def verification_attempts(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT * FROM verification_attempts
               WHERE run_id = ? ORDER BY created_at, attempt_id""",
            (run_id,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["command"] = json.loads(str(item["command"]))
            result.append(item)
        return result

    def mark_verification_attempts(
        self, run_id: str, *, freshness: str
    ) -> int:
        if freshness not in {"current", "stale", "superseded"}:
            raise ValueError(f"unsupported verification freshness: {freshness}")
        cursor = self.connection.execute(
            """UPDATE verification_attempts SET freshness = ?
               WHERE run_id = ? AND freshness != ?""",
            (freshness, run_id, freshness),
        )
        self.connection.commit()
        return int(cursor.rowcount)


__all__ = [
    "CHANGE_TRACKING_SCHEMA_STATEMENTS",
    "CHANGE_TRACKING_TABLES",
    "ChangeTrackingStoreMixin",
]
