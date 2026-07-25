"""Durable projections for CLI sessions, assignments, and orchestration plans."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence


COLLABORATION_TABLES = (
    "agent_sessions",
    "session_generations",
    "conversation_interactions",
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
      node_id TEXT, parent_assignment_id TEXT, run_id TEXT, agent_id TEXT NOT NULL,
      dispatch_kind TEXT NOT NULL, work_mode TEXT NOT NULL, status TEXT NOT NULL,
      title TEXT NOT NULL, brief TEXT NOT NULL, allowed_scope TEXT NOT NULL,
      deliverables TEXT NOT NULL, completion TEXT NOT NULL, proof TEXT NOT NULL,
      baseline_digest TEXT, worktree TEXT, changeset_digest TEXT,
      recovery_attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL, reported_at TEXT, completed_at TEXT,
      metadata TEXT NOT NULL,
      FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id),
      FOREIGN KEY(plan_id) REFERENCES orchestration_plans(plan_id),
      FOREIGN KEY(parent_assignment_id) REFERENCES assignments(assignment_id),
      FOREIGN KEY(run_id) REFERENCES runs(run_id)
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
      assignment_id TEXT, current_assignment_id TEXT, agent_id TEXT NOT NULL,
      cli_id TEXT NOT NULL, lane_key TEXT NOT NULL DEFAULT 'main',
      lane_type TEXT NOT NULL DEFAULT 'main', generation INTEGER NOT NULL DEFAULT 0,
      status TEXT NOT NULL, native_session_id TEXT, worktree TEXT NOT NULL,
      transcript_path TEXT NOT NULL, last_sequence INTEGER NOT NULL DEFAULT 0,
      cols INTEGER NOT NULL DEFAULT 120, rows INTEGER NOT NULL DEFAULT 32,
      recovery_mode TEXT NOT NULL DEFAULT 'fresh', control_token_hash TEXT,
      token_expires_at TEXT, write_lease_id TEXT, write_lease_holder TEXT,
      write_lease_expires_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      metadata TEXT NOT NULL,
      FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id),
      FOREIGN KEY(assignment_id) REFERENCES assignments(assignment_id),
      FOREIGN KEY(current_assignment_id) REFERENCES assignments(assignment_id),
      UNIQUE(conversation_id, agent_id, lane_key)
    )""",
    """CREATE TABLE IF NOT EXISTS session_generations(
      generation_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, generation INTEGER NOT NULL,
      backend TEXT NOT NULL, process_id TEXT, worktree TEXT NOT NULL,
      native_session_id TEXT, recovery_mode TEXT NOT NULL, started_at TEXT NOT NULL,
      ended_at TEXT, metadata TEXT NOT NULL, UNIQUE(session_id, generation),
      FOREIGN KEY(session_id) REFERENCES agent_sessions(session_id)
    )""",
    """CREATE TABLE IF NOT EXISTS conversation_interactions(
      interaction_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, run_id TEXT,
      assignment_id TEXT, kind TEXT NOT NULL, requirement_id TEXT NOT NULL,
      prompt TEXT NOT NULL, options TEXT NOT NULL, status TEXT NOT NULL,
      response TEXT, created_at TEXT NOT NULL, responded_at TEXT, metadata TEXT NOT NULL,
      FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id),
      FOREIGN KEY(run_id) REFERENCES runs(run_id),
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
        run_id: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        now = _now()
        self.connection.execute(
            """INSERT INTO assignments(
              assignment_id, conversation_id, plan_id, node_id, parent_assignment_id, run_id,
              agent_id, dispatch_kind, work_mode, status, title, brief, allowed_scope,
              deliverables, completion, proof, baseline_digest, worktree,
              changeset_digest, recovery_attempts, created_at, updated_at,
              reported_at, completed_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, ?, ?, NULL, NULL, ?)""",
            (
                assignment_id, conversation_id, plan_id, node_id, parent_assignment_id, run_id,
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
            "recovery_attempts", "reported_at", "completed_at", "run_id",
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
        assignment_id: str | None,
        agent_id: str,
        cli_id: str,
        status: str,
        worktree: str,
        transcript_path: str,
        recovery_mode: str = "fresh",
        control_token_hash: str | None = None,
        token_expires_at: str | None = None,
        lane_key: str = "main",
        lane_type: str = "main",
        current_assignment_id: str | None = None,
        generation: int = 0,
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        now = _now()
        self.connection.execute(
            """INSERT INTO agent_sessions(
              session_id, conversation_id, assignment_id, current_assignment_id,
              agent_id, cli_id, lane_key, lane_type, generation, status,
              native_session_id, worktree, transcript_path, last_sequence, cols, rows,
              recovery_mode, control_token_hash, token_expires_at, write_lease_id,
              write_lease_holder, write_lease_expires_at, created_at, updated_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 0, 120, 32, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?)""",
            (
                session_id, conversation_id, assignment_id, current_assignment_id,
                agent_id, cli_id, lane_key, lane_type, generation, status,
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

    def find_agent_session(
        self, conversation_id: str, agent_id: str, lane_key: str = "main"
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            """SELECT * FROM agent_sessions
               WHERE conversation_id = ? AND agent_id = ? AND lane_key = ?
               ORDER BY created_at LIMIT 1""",
            (conversation_id, agent_id, lane_key),
        ).fetchone()
        return _decode(row, "metadata")

    def update_agent_session(self, session_id: str, **changes: object) -> dict[str, Any]:
        current = self.get_agent_session(session_id)
        if not current:
            raise FileNotFoundError(session_id)
        allowed = {
            "status", "native_session_id", "last_sequence", "cols", "rows",
            "recovery_mode", "control_token_hash", "token_expires_at",
            "write_lease_id", "write_lease_holder", "write_lease_expires_at",
            "assignment_id", "current_assignment_id", "generation", "worktree",
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

    def create_session_generation(
        self,
        *,
        session_id: str,
        generation: int,
        backend: str,
        worktree: str,
        recovery_mode: str,
        native_session_id: str | None = None,
        process_id: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        generation_id = f"sgen_{session_id}_{generation}"
        self.connection.execute(
            """INSERT INTO session_generations(
              generation_id, session_id, generation, backend, process_id, worktree,
              native_session_id, recovery_mode, started_at, ended_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)""",
            (
                generation_id, session_id, generation, backend, process_id, worktree,
                native_session_id, recovery_mode, _now(), _json(metadata or {}),
            ),
        )
        self.connection.commit()
        return self.get_session_generation(session_id, generation) or {}

    def get_session_generation(self, session_id: str, generation: int) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM session_generations WHERE session_id = ? AND generation = ?",
            (session_id, generation),
        ).fetchone()
        return _decode(row, "metadata")

    def list_session_generations(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM session_generations WHERE session_id = ? ORDER BY generation",
            (session_id,),
        ).fetchall()
        return [_decode(row, "metadata") or {} for row in rows]

    def finish_session_generation(self, session_id: str, generation: int) -> None:
        self.connection.execute(
            """UPDATE session_generations SET ended_at = ?
               WHERE session_id = ? AND generation = ? AND ended_at IS NULL""",
            (_now(), session_id, generation),
        )
        self.connection.commit()

    def update_session_generation(
        self,
        session_id: str,
        generation: int,
        *,
        native_session_id: str | None = None,
        process_id: str | None = None,
    ) -> None:
        current = self.get_session_generation(session_id, generation)
        if not current:
            raise FileNotFoundError(f"{session_id}:{generation}")
        self.connection.execute(
            """UPDATE session_generations SET native_session_id = ?, process_id = ?
               WHERE session_id = ? AND generation = ?""",
            (
                native_session_id if native_session_id is not None else current.get("native_session_id"),
                process_id if process_id is not None else current.get("process_id"),
                session_id,
                generation,
            ),
        )
        self.connection.commit()

    def create_conversation_interaction(
        self,
        *,
        conversation_id: str,
        kind: str,
        requirement_id: str,
        prompt: str,
        options: Sequence[Mapping[str, object]] | None = None,
        run_id: str | None = None,
        assignment_id: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        interaction_id = f"cint_{__import__('uuid').uuid4().hex}"
        self.connection.execute(
            """INSERT INTO conversation_interactions(
              interaction_id, conversation_id, run_id, assignment_id, kind,
              requirement_id, prompt, options, status, response, created_at,
              responded_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, ?, NULL, ?)""",
            (
                interaction_id, conversation_id, run_id, assignment_id, kind,
                requirement_id, prompt, _json(list(options or [])), _now(),
                _json(metadata or {}),
            ),
        )
        self.connection.commit()
        interaction = self.get_conversation_interaction(interaction_id) or {}
        self.create_review_record(
            conversation_id,
            review_id=f"review_{interaction_id}",
            kind="interaction",
            status="pending",
            prompt=prompt,
            options=list(options or []),
            actor_kind="agent",
            actor_id=str((metadata or {}).get("agent_id") or "runtime"),
            run_id=run_id,
            assignment_id=assignment_id,
            metadata={
                "interaction_id": interaction_id,
                "requirement_id": requirement_id,
                **dict(metadata or {}),
            },
        )
        return interaction

    def get_conversation_interaction(self, interaction_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM conversation_interactions WHERE interaction_id = ?",
            (interaction_id,),
        ).fetchone()
        return _decode(row, "options", "metadata")

    def list_conversation_interactions(
        self, conversation_id: str, *, pending_only: bool = False
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM conversation_interactions WHERE conversation_id = ?"
        if pending_only:
            sql += " AND status = 'pending'"
        sql += " ORDER BY created_at, interaction_id"
        rows = self.connection.execute(sql, (conversation_id,)).fetchall()
        return [_decode(row, "options", "metadata") or {} for row in rows]

    def respond_conversation_interaction(
        self, interaction_id: str, response: str
    ) -> dict[str, Any]:
        cursor = self.connection.execute(
            """UPDATE conversation_interactions
               SET status = 'responded', response = ?, responded_at = ?
               WHERE interaction_id = ? AND status = 'pending'""",
            (response, _now(), interaction_id),
        )
        self.connection.commit()
        if cursor.rowcount != 1:
            if not self.get_conversation_interaction(interaction_id):
                raise FileNotFoundError(interaction_id)
            raise RuntimeError("interaction has already been answered")
        interaction = self.get_conversation_interaction(interaction_id) or {}
        review = self.get_review_record(f"review_{interaction_id}")
        if review and review.get("status") == "pending":
            self.resolve_review_record(
                str(review["review_id"]),
                status="responded",
                response=response,
                actor_id="developer",
            )
        return interaction
