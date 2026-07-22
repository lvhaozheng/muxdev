"""Durable projections for CLI sessions, assignments, and orchestration plans."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence


COLLABORATION_TABLES = (
    "agent_sessions",
    "orchestration_plans",
    "assignments",
    "assignment_dependencies",
)


COLLABORATION_SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS orchestration_plans(
      plan_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, version INTEGER NOT NULL,
      status TEXT NOT NULL, orchestrator_agent_id TEXT NOT NULL, schema_version TEXT NOT NULL,
      summary TEXT NOT NULL, plan TEXT NOT NULL, scope_digest TEXT NOT NULL,
      approved_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      metadata TEXT NOT NULL, UNIQUE(conversation_id, version),
      FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
    )""",
    """CREATE TABLE IF NOT EXISTS assignments(
      assignment_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, plan_id TEXT,
      node_id TEXT, parent_assignment_id TEXT, agent_id TEXT NOT NULL,
      dispatch_kind TEXT NOT NULL, work_mode TEXT NOT NULL, status TEXT NOT NULL,
      title TEXT NOT NULL, brief TEXT NOT NULL, allowed_scope TEXT NOT NULL,
      deliverables TEXT NOT NULL, completion TEXT NOT NULL, proof TEXT NOT NULL,
      baseline_digest TEXT, worktree TEXT, changeset_digest TEXT,
      recovery_attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL, reported_at TEXT, completed_at TEXT,
      metadata TEXT NOT NULL,
      FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id),
      FOREIGN KEY(plan_id) REFERENCES orchestration_plans(plan_id),
      FOREIGN KEY(parent_assignment_id) REFERENCES assignments(assignment_id)
    )""",
    """CREATE TABLE IF NOT EXISTS assignment_dependencies(
      assignment_id TEXT NOT NULL, depends_on_assignment_id TEXT NOT NULL,
      created_at TEXT NOT NULL,
      PRIMARY KEY(assignment_id, depends_on_assignment_id),
      FOREIGN KEY(assignment_id) REFERENCES assignments(assignment_id),
      FOREIGN KEY(depends_on_assignment_id) REFERENCES assignments(assignment_id)
    )""",
    """CREATE TABLE IF NOT EXISTS agent_sessions(
      session_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
      assignment_id TEXT NOT NULL, agent_id TEXT NOT NULL, cli_id TEXT NOT NULL,
      status TEXT NOT NULL, native_session_id TEXT, worktree TEXT NOT NULL,
      transcript_path TEXT NOT NULL, last_sequence INTEGER NOT NULL DEFAULT 0,
      cols INTEGER NOT NULL DEFAULT 120, rows INTEGER NOT NULL DEFAULT 32,
      recovery_mode TEXT NOT NULL DEFAULT 'fresh', control_token_hash TEXT,
      token_expires_at TEXT, write_lease_id TEXT, write_lease_holder TEXT,
      write_lease_expires_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      metadata TEXT NOT NULL,
      FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id),
      FOREIGN KEY(assignment_id) REFERENCES assignments(assignment_id)
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
                result[field] = json.loads(result[field])
            except json.JSONDecodeError:
                pass
    return result


class CollaborationStoreMixin:
    """SQLite methods mixed into :class:`ControlStore`."""

    connection: Any

    def create_orchestration_plan(
        self,
        *,
        plan_id: str,
        conversation_id: str,
        version: int,
        status: str,
        orchestrator_agent_id: str,
        summary: str,
        plan: Mapping[str, object],
        scope_digest: str,
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        now = _now()
        self.connection.execute(
            """INSERT INTO orchestration_plans(
              plan_id, conversation_id, version, status, orchestrator_agent_id,
              schema_version, summary, plan, scope_digest, approved_at,
              created_at, updated_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)""",
            (
                plan_id, conversation_id, version, status, orchestrator_agent_id,
                str(plan.get("schema_version") or "muxdev.orchestration-plan.v1"),
                summary, _json(plan), scope_digest, now, now, _json(metadata or {}),
            ),
        )
        self.connection.commit()
        return self.get_orchestration_plan(plan_id) or {}

    def get_orchestration_plan(self, plan_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM orchestration_plans WHERE plan_id = ?", (plan_id,)
        ).fetchone()
        return _decode(row, "plan", "metadata")

    def list_orchestration_plans(self, conversation_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM orchestration_plans WHERE conversation_id = ? ORDER BY version",
            (conversation_id,),
        ).fetchall()
        return [_decode(row, "plan", "metadata") or {} for row in rows]

    def update_orchestration_plan(
        self,
        plan_id: str,
        *,
        status: str | None = None,
        approved_at: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        current = self.get_orchestration_plan(plan_id)
        if not current:
            raise FileNotFoundError(plan_id)
        merged = dict(current.get("metadata") or {})
        if metadata:
            merged.update(metadata)
        self.connection.execute(
            """UPDATE orchestration_plans SET status = ?, approved_at = ?,
              updated_at = ?, metadata = ? WHERE plan_id = ?""",
            (
                status or current["status"],
                approved_at if approved_at is not None else current.get("approved_at"),
                _now(), _json(merged), plan_id,
            ),
        )
        self.connection.commit()
        return self.get_orchestration_plan(plan_id) or {}

    def create_assignment(
        self,
        *,
        assignment_id: str,
        conversation_id: str,
        agent_id: str,
        dispatch_kind: str,
        work_mode: str,
        status: str,
        title: str,
        brief: str,
        allowed_scope: Sequence[str],
        deliverables: Sequence[str],
        completion: Sequence[str],
        proof: Sequence[str],
        plan_id: str | None = None,
        node_id: str | None = None,
        parent_assignment_id: str | None = None,
        baseline_digest: str | None = None,
        worktree: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        now = _now()
        self.connection.execute(
            """INSERT INTO assignments(
              assignment_id, conversation_id, plan_id, node_id, parent_assignment_id,
              agent_id, dispatch_kind, work_mode, status, title, brief, allowed_scope,
              deliverables, completion, proof, baseline_digest, worktree,
              changeset_digest, recovery_attempts, created_at, updated_at,
              reported_at, completed_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, ?, ?, NULL, NULL, ?)""",
            (
                assignment_id, conversation_id, plan_id, node_id, parent_assignment_id,
                agent_id, dispatch_kind, work_mode, status, title, brief,
                _json(list(allowed_scope)), _json(list(deliverables)),
                _json(list(completion)), _json(list(proof)), baseline_digest,
                worktree, now, now, _json(metadata or {}),
            ),
        )
        self.connection.commit()
        return self.get_assignment(assignment_id) or {}

    def get_assignment(self, assignment_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM assignments WHERE assignment_id = ?", (assignment_id,)
        ).fetchone()
        return _decode(row, "allowed_scope", "deliverables", "completion", "proof", "metadata")

    def list_assignments(
        self,
        conversation_id: str,
        *,
        statuses: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM assignments WHERE conversation_id = ?"
        params: list[object] = [conversation_id]
        if statuses:
            sql += " AND status IN (" + ",".join("?" for _ in statuses) + ")"
            params.extend(statuses)
        sql += " ORDER BY created_at, assignment_id"
        rows = self.connection.execute(sql, params).fetchall()
        return [
            _decode(row, "allowed_scope", "deliverables", "completion", "proof", "metadata") or {}
            for row in rows
        ]

    def update_assignment(self, assignment_id: str, **changes: object) -> dict[str, Any]:
        current = self.get_assignment(assignment_id)
        if not current:
            raise FileNotFoundError(assignment_id)
        allowed = {
            "agent_id", "status", "baseline_digest", "worktree", "changeset_digest",
            "recovery_attempts", "reported_at", "completed_at",
        }
        assignments: list[str] = []
        params: list[object] = []
        for key, value in changes.items():
            if key == "metadata":
                merged = dict(current.get("metadata") or {})
                if isinstance(value, Mapping):
                    merged.update(value)
                assignments.append("metadata = ?")
                params.append(_json(merged))
            elif key in allowed:
                assignments.append(f"{key} = ?")
                params.append(value)
            else:
                raise ValueError(f"unsupported assignment update field: {key}")
        assignments.append("updated_at = ?")
        params.extend([_now(), assignment_id])
        self.connection.execute(
            f"UPDATE assignments SET {', '.join(assignments)} WHERE assignment_id = ?", params
        )
        self.connection.commit()
        return self.get_assignment(assignment_id) or {}

    def add_assignment_dependencies(self, assignment_id: str, dependencies: Sequence[str]) -> None:
        now = _now()
        self.connection.executemany(
            """INSERT OR IGNORE INTO assignment_dependencies(
              assignment_id, depends_on_assignment_id, created_at
            ) VALUES (?, ?, ?)""",
            [(assignment_id, dependency, now) for dependency in dependencies],
        )
        self.connection.commit()

    def list_assignment_dependencies(self, assignment_id: str) -> list[str]:
        rows = self.connection.execute(
            """SELECT depends_on_assignment_id FROM assignment_dependencies
              WHERE assignment_id = ? ORDER BY depends_on_assignment_id""",
            (assignment_id,),
        ).fetchall()
        return [str(row[0]) for row in rows]

    def create_agent_session(
        self,
        *,
        session_id: str,
        conversation_id: str,
        assignment_id: str,
        agent_id: str,
        cli_id: str,
        status: str,
        worktree: str,
        transcript_path: str,
        recovery_mode: str = "fresh",
        control_token_hash: str | None = None,
        token_expires_at: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        now = _now()
        self.connection.execute(
            """INSERT INTO agent_sessions(
              session_id, conversation_id, assignment_id, agent_id, cli_id, status,
              native_session_id, worktree, transcript_path, last_sequence, cols, rows,
              recovery_mode, control_token_hash, token_expires_at, write_lease_id,
              write_lease_holder, write_lease_expires_at, created_at, updated_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, 0, 120, 32, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?)""",
            (
                session_id, conversation_id, assignment_id, agent_id, cli_id, status,
                worktree, transcript_path, recovery_mode, control_token_hash,
                token_expires_at, now, now, _json(metadata or {}),
            ),
        )
        self.connection.commit()
        return self.get_agent_session(session_id) or {}

    def get_agent_session(self, session_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM agent_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return _decode(row, "metadata")

    def list_agent_sessions(self, conversation_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM agent_sessions WHERE conversation_id = ? ORDER BY created_at",
            (conversation_id,),
        ).fetchall()
        return [_decode(row, "metadata") or {} for row in rows]

    def update_agent_session(self, session_id: str, **changes: object) -> dict[str, Any]:
        current = self.get_agent_session(session_id)
        if not current:
            raise FileNotFoundError(session_id)
        allowed = {
            "status", "native_session_id", "last_sequence", "cols", "rows",
            "recovery_mode", "control_token_hash", "token_expires_at",
            "write_lease_id", "write_lease_holder", "write_lease_expires_at",
        }
        assignments: list[str] = []
        params: list[object] = []
        for key, value in changes.items():
            if key == "metadata":
                merged = dict(current.get("metadata") or {})
                if isinstance(value, Mapping):
                    merged.update(value)
                assignments.append("metadata = ?")
                params.append(_json(merged))
            elif key in allowed:
                assignments.append(f"{key} = ?")
                params.append(value)
            else:
                raise ValueError(f"unsupported session update field: {key}")
        assignments.append("updated_at = ?")
        params.extend([_now(), session_id])
        self.connection.execute(
            f"UPDATE agent_sessions SET {', '.join(assignments)} WHERE session_id = ?", params
        )
        self.connection.commit()
        return self.get_agent_session(session_id) or {}

