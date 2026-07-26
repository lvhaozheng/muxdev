"""Conversation projections stored alongside immutable Run facts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any, Mapping
from uuid import uuid4


CONVERSATION_TABLES = (
    "conversations",
    "conversation_events",
    "delivery_contracts",
    "delivery_candidates",
    "devices",
    "web_sessions",
)


CONVERSATION_SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS conversations(
      conversation_id TEXT PRIMARY KEY, title TEXT NOT NULL, goal TEXT NOT NULL,
      status TEXT NOT NULL, workspace TEXT NOT NULL, active_contract_id TEXT,
      active_run_id TEXT, active_candidate_id TEXT,
      mode TEXT NOT NULL DEFAULT 'legacy_pipeline', primary_agent_id TEXT,
      orchestrator_agent_id TEXT, active_plan_id TEXT, created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL, metadata TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS conversation_events(
      event_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, run_id TEXT,
      run_event_id TEXT, actor TEXT NOT NULL, actor_kind TEXT, actor_id TEXT,
      assignment_id TEXT, session_id TEXT, generation INTEGER,
      schema_version INTEGER NOT NULL DEFAULT 1, type TEXT NOT NULL,
      sequence INTEGER NOT NULL, created_at TEXT NOT NULL,
      capture_grade TEXT NOT NULL DEFAULT 'recorded', correlation_id TEXT,
      payload TEXT NOT NULL, previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL,
      UNIQUE(conversation_id, sequence),
      FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
    )""",
    """CREATE TABLE IF NOT EXISTS delivery_contracts(
      contract_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, version INTEGER NOT NULL,
      status TEXT NOT NULL, goal TEXT NOT NULL, acceptance_criteria TEXT NOT NULL,
      allowed_scope TEXT NOT NULL, workflow TEXT NOT NULL, profile TEXT NOT NULL,
      provider TEXT NOT NULL, max_cost_usd REAL NOT NULL, policy TEXT NOT NULL,
      created_at TEXT NOT NULL, superseded_at TEXT, UNIQUE(conversation_id, version),
      FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
    )""",
    """CREATE TABLE IF NOT EXISTS delivery_candidates(
      candidate_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, contract_id TEXT NOT NULL,
      run_id TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL,
      subject_digest TEXT NOT NULL, changeset_digest TEXT NOT NULL,
      message_sequence INTEGER NOT NULL, policy_hash TEXT NOT NULL,
      parent_candidate_id TEXT, evidence_path TEXT, created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL, metadata TEXT NOT NULL, UNIQUE(conversation_id, version),
      FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id),
      FOREIGN KEY(contract_id) REFERENCES delivery_contracts(contract_id),
      FOREIGN KEY(run_id) REFERENCES runs(run_id)
    )""",
    """CREATE TABLE IF NOT EXISTS devices(
      device_id TEXT PRIMARY KEY, label TEXT NOT NULL, status TEXT NOT NULL,
      public_key TEXT, created_at TEXT NOT NULL, last_seen_at TEXT,
      revoked_at TEXT, metadata TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS web_sessions(
      session_id TEXT PRIMARY KEY, device_id TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
      created_at TEXT NOT NULL, expires_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
      revoked_at TEXT, metadata TEXT NOT NULL,
      FOREIGN KEY(device_id) REFERENCES devices(device_id)
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


def _actor_kind(actor: str) -> str:
    if actor == "developer":
        return "developer"
    if actor in {"runtime", "supervisor", "system"}:
        return "runtime"
    return "agent"


def _v2_fact(
    *,
    event_id: str,
    conversation_id: str,
    sequence: int,
    event_type: str,
    actor_kind: str,
    actor_id: str,
    run_id: str | None,
    assignment_id: str | None,
    session_id: str | None,
    generation: int | None,
    occurred_at: str,
    capture_grade: str,
    correlation_id: str | None,
    payload: Mapping[str, object],
    previous_hash: str,
) -> dict[str, object]:
    return {
        "event_id": event_id,
        "conversation_id": conversation_id,
        "sequence": sequence,
        "schema_version": 2,
        "type": event_type,
        "actor": {"kind": actor_kind, "id": actor_id},
        "source": {
            "run_id": run_id,
            "assignment_id": assignment_id,
            "session_id": session_id,
            "generation": generation,
        },
        "occurred_at": occurred_at,
        "capture_grade": capture_grade,
        "correlation_id": correlation_id,
        "payload": dict(payload),
        "previous_hash": previous_hash,
    }


def _insert_v2_event(
    conn: Any,
    *,
    conversation_id: str,
    event_type: str,
    payload: Mapping[str, object],
    actor: str,
    run_id: str | None = None,
    run_event_id: str | None = None,
    event_id: str | None = None,
    assignment_id: str | None = None,
    session_id: str | None = None,
    generation: int | None = None,
    correlation_id: str | None = None,
    capture_grade: str = "recorded",
    actor_kind: str | None = None,
) -> tuple[str, int]:
    if capture_grade not in {"recorded", "observed", "verified"}:
        raise ValueError(f"unsupported capture grade: {capture_grade}")
    resolved_actor_kind = actor_kind or _actor_kind(actor)
    resolved_event_id = event_id or f"cevt_{uuid4().hex}"
    previous = conn.execute(
        """SELECT event_hash FROM conversation_events
           WHERE conversation_id = ? ORDER BY sequence DESC LIMIT 1""",
        (conversation_id,),
    ).fetchone()
    previous_hash = str(previous[0]) if previous else "sha256:" + "0" * 64
    sequence = int(
        conn.execute(
            """SELECT COALESCE(MAX(sequence), 0) + 1
               FROM conversation_events WHERE conversation_id = ?""",
            (conversation_id,),
        ).fetchone()[0]
    )
    created_at = _now()
    fact = _v2_fact(
        event_id=resolved_event_id,
        conversation_id=conversation_id,
        sequence=sequence,
        event_type=event_type,
        actor_kind=resolved_actor_kind,
        actor_id=actor,
        run_id=run_id,
        assignment_id=assignment_id,
        session_id=session_id,
        generation=generation,
        occurred_at=created_at,
        capture_grade=capture_grade,
        correlation_id=correlation_id,
        payload=payload,
        previous_hash=previous_hash,
    )
    event_hash = "sha256:" + hashlib.sha256(_json(fact).encode()).hexdigest()
    conn.execute(
        """INSERT INTO conversation_events(
          event_id, conversation_id, run_id, run_event_id, actor,
          actor_kind, actor_id, assignment_id, session_id, generation,
          schema_version, type, sequence, created_at, capture_grade,
          correlation_id, payload, previous_hash, event_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 2, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            resolved_event_id,
            conversation_id,
            run_id,
            run_event_id,
            actor,
            resolved_actor_kind,
            actor,
            assignment_id,
            session_id,
            generation,
            event_type,
            sequence,
            created_at,
            capture_grade,
            correlation_id,
            _json(payload),
            previous_hash,
            event_hash,
        ),
    )
    return resolved_event_id, sequence


def _activity_view(row: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(row.get("payload") or {})
    if int(row.get("schema_version") or 1) == 1:
        payload.setdefault("_legacy_schema_version", 1)
    return {
        "event_id": str(row["event_id"]),
        "conversation_id": str(row["conversation_id"]),
        "sequence": int(row["sequence"]),
        "schema_version": 2,
        "type": str(row["type"]),
        "actor": {
            "kind": str(row.get("actor_kind") or _actor_kind(str(row["actor"]))),
            "id": str(row.get("actor_id") or row["actor"]),
        },
        "source": {
            "run_id": str(row["run_id"]) if row.get("run_id") else None,
            "assignment_id": (
                str(row["assignment_id"]) if row.get("assignment_id") else None
            ),
            "session_id": str(row["session_id"]) if row.get("session_id") else None,
            "generation": int(row["generation"]) if row.get("generation") else None,
        },
        "occurred_at": str(row["created_at"]),
        "capture_grade": str(row.get("capture_grade") or "recorded"),
        "correlation_id": (
            str(row["correlation_id"]) if row.get("correlation_id") else None
        ),
        "payload": payload,
        "previous_hash": str(row["previous_hash"]),
        "event_hash": str(row["event_hash"]),
    }


class ConversationStoreMixin:
    """SQLite methods mixed into :class:`ControlStore`."""

    connection: Any
    workspace: Any

    def create_conversation(
        self,
        *,
        conversation_id: str,
        title: str,
        goal: str,
        status: str,
        metadata: Mapping[str, object],
        mode: str = "legacy_pipeline",
        primary_agent_id: str | None = None,
        orchestrator_agent_id: str | None = None,
    ) -> dict[str, Any]:
        now = _now()
        self.connection.execute(
            """INSERT INTO conversations(
              conversation_id, title, goal, status, workspace, active_contract_id,
              active_run_id, active_candidate_id, mode, primary_agent_id,
              orchestrator_agent_id, active_plan_id, created_at, updated_at, metadata
            ) VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?, NULL, ?, ?, ?)""",
            (
                conversation_id, title, goal, status, str(self.workspace), mode,
                primary_agent_id, orchestrator_agent_id, now, now, _json(metadata),
            ),
        )
        self.connection.commit()
        return self.get_conversation(conversation_id) or {}

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM conversations WHERE conversation_id = ?", (conversation_id,)
        ).fetchone()
        return _decode(row, "metadata")

    def list_conversations(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM conversations"
        params: list[object] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, min(limit, 1000)))
        return [_decode(row, "metadata") or {} for row in self.connection.execute(sql, params).fetchall()]

    def update_conversation(
        self,
        conversation_id: str,
        *,
        status: str | None = None,
        active_contract_id: str | None = None,
        active_run_id: str | None = None,
        active_candidate_id: str | None = None,
        mode: str | None = None,
        primary_agent_id: str | None = None,
        orchestrator_agent_id: str | None = None,
        active_plan_id: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        current = self.get_conversation(conversation_id)
        if not current:
            raise FileNotFoundError(conversation_id)
        merged = dict(current.get("metadata") or {})
        if metadata:
            merged.update(metadata)
        self.connection.execute(
            """UPDATE conversations SET status = ?, active_contract_id = ?, active_run_id = ?,
              active_candidate_id = ?, mode = ?, primary_agent_id = ?,
              orchestrator_agent_id = ?, active_plan_id = ?, updated_at = ?, metadata = ?
              WHERE conversation_id = ?""",
            (
                status if status is not None else current["status"],
                active_contract_id if active_contract_id is not None else current.get("active_contract_id"),
                active_run_id if active_run_id is not None else current.get("active_run_id"),
                active_candidate_id if active_candidate_id is not None else current.get("active_candidate_id"),
                mode if mode is not None else current.get("mode", "legacy_pipeline"),
                primary_agent_id if primary_agent_id is not None else current.get("primary_agent_id"),
                orchestrator_agent_id if orchestrator_agent_id is not None else current.get("orchestrator_agent_id"),
                active_plan_id if active_plan_id is not None else current.get("active_plan_id"),
                _now(),
                _json(merged),
                conversation_id,
            ),
        )
        self.connection.commit()
        return self.get_conversation(conversation_id) or {}

    def append_conversation_event(
        self,
        conversation_id: str,
        event_type: str,
        payload: Mapping[str, object],
        *,
        actor: str,
        run_id: str | None = None,
        run_event_id: str | None = None,
        event_id: str | None = None,
        assignment_id: str | None = None,
        session_id: str | None = None,
        generation: int | None = None,
        correlation_id: str | None = None,
        capture_grade: str = "recorded",
        actor_kind: str | None = None,
        schema_version: int = 2,
    ) -> str:
        if schema_version != 2:
            raise ValueError("new Conversation events must use schema_version 2")
        with self.transaction() as conn:
            event_id, sequence = _insert_v2_event(
                conn,
                conversation_id=conversation_id,
                event_type=event_type,
                payload=payload,
                actor=actor,
                run_id=run_id,
                run_event_id=run_event_id,
                event_id=event_id,
                assignment_id=assignment_id,
                session_id=session_id,
                generation=generation,
                correlation_id=correlation_id,
                capture_grade=capture_grade,
                actor_kind=actor_kind,
            )
        from .activity_feed import activity_feed

        activity_feed.publish(self.workspace, conversation_id, sequence)
        return event_id

    def create_user_message_with_deliveries(
        self,
        conversation_id: str,
        *,
        content: str,
        recipients: list[str],
        dispatch_kind: str,
        routes: list[Mapping[str, object]],
    ) -> tuple[str, list[dict[str, Any]]]:
        """Atomically record one user message and its durable delivery outbox."""
        event_payload = {
            "content": content,
            "recipients": recipients,
            "dispatch_kind": dispatch_kind,
        }
        deliveries: list[dict[str, Any]] = []
        now = _now()
        with self.transaction() as conn:
            event_id, sequence = _insert_v2_event(
                conn,
                conversation_id=conversation_id,
                event_type="user.message",
                payload=event_payload,
                actor="developer",
            )
            for route in routes:
                delivery_id = f"mdel_{uuid4().hex}"
                recipient = str(route["agent_id"])
                assignment_id = str(route.get("assignment_id") or "") or None
                run_id = str(route.get("run_id") or "") or None
                session_id = str(route.get("session_id") or "") or None
                metadata = {
                    "resumed_assignment": bool(route.get("resumed_assignment")),
                }
                conn.execute(
                    """INSERT INTO message_deliveries(
                      delivery_id, conversation_id, message_event_id,
                      recipient_agent_id, assignment_id, run_id, session_id,
                      target_generation, status, attempts, last_error,
                      created_at, updated_at, dispatched_at, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'pending', 0, NULL,
                      ?, ?, NULL, ?)""",
                    (
                        delivery_id,
                        conversation_id,
                        event_id,
                        recipient,
                        assignment_id,
                        run_id,
                        session_id,
                        now,
                        now,
                        _json(metadata),
                    ),
                )
                deliveries.append(
                    {
                        "delivery_id": delivery_id,
                        "conversation_id": conversation_id,
                        "message_event_id": event_id,
                        "recipient_agent_id": recipient,
                        "assignment_id": assignment_id,
                        "run_id": run_id,
                        "session_id": session_id,
                        "target_generation": None,
                        "status": "pending",
                        "attempts": 0,
                        "last_error": None,
                        "created_at": now,
                        "updated_at": now,
                        "dispatched_at": None,
                        "metadata": metadata,
                        "message_payload": event_payload,
                    }
                )
        from .activity_feed import activity_feed

        activity_feed.publish(self.workspace, conversation_id, sequence)
        return event_id, deliveries

    def claim_conversation_action(
        self,
        conversation_id: str,
        *,
        idempotency_key: str,
        action: str,
        payload: Mapping[str, object],
    ) -> bool:
        digest = hashlib.sha256(f"{conversation_id}:{idempotency_key}".encode()).hexdigest()
        try:
            self.append_conversation_event(
                conversation_id,
                "action.claimed",
                {"action": action, "idempotency_key_hash": f"sha256:{digest}", "payload": dict(payload)},
                actor="developer",
                event_id=f"action_{digest}",
            )
        except sqlite3.IntegrityError:
            return False
        return True

    def conversation_events(self, conversation_id: str, *, after: int = 0) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT * FROM conversation_events
               WHERE conversation_id = ? AND sequence > ? ORDER BY sequence""",
            (conversation_id, after),
        ).fetchall()
        return [_decode(row, "payload") or {} for row in rows]

    def conversation_activity(
        self,
        conversation_id: str,
        *,
        after: int = 0,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT * FROM (
                 SELECT * FROM conversation_events
                 WHERE conversation_id = ? AND sequence > ?
                 ORDER BY sequence DESC LIMIT ?
               ) ORDER BY sequence""",
            (conversation_id, after, max(1, min(limit, 1000))),
        ).fetchall()
        return [_activity_view(_decode(row, "payload") or {}) for row in rows]

    def verify_conversation_event_chain(self, conversation_id: str) -> tuple[bool, list[str]]:
        errors: list[str] = []
        expected_previous = "sha256:" + "0" * 64
        for row in self.conversation_events(conversation_id):
            if int(row.get("schema_version") or 1) == 1:
                fact = {key: row[key] for key in (
                    "event_id", "conversation_id", "run_id", "run_event_id", "actor", "type",
                    "sequence", "created_at", "payload", "previous_hash",
                )}
            else:
                fact = _v2_fact(
                    event_id=str(row["event_id"]),
                    conversation_id=str(row["conversation_id"]),
                    sequence=int(row["sequence"]),
                    event_type=str(row["type"]),
                    actor_kind=str(row.get("actor_kind") or _actor_kind(str(row["actor"]))),
                    actor_id=str(row.get("actor_id") or row["actor"]),
                    run_id=str(row["run_id"]) if row.get("run_id") else None,
                    assignment_id=(
                        str(row["assignment_id"]) if row.get("assignment_id") else None
                    ),
                    session_id=str(row["session_id"]) if row.get("session_id") else None,
                    generation=int(row["generation"]) if row.get("generation") else None,
                    occurred_at=str(row["created_at"]),
                    capture_grade=str(row.get("capture_grade") or "recorded"),
                    correlation_id=(
                        str(row["correlation_id"]) if row.get("correlation_id") else None
                    ),
                    payload=row.get("payload") if isinstance(row.get("payload"), dict) else {},
                    previous_hash=str(row["previous_hash"]),
                )
            expected_hash = "sha256:" + hashlib.sha256(_json(fact).encode()).hexdigest()
            if row["previous_hash"] != expected_previous:
                errors.append(f"conversation event {row['event_id']} has an invalid previous hash")
            if row["event_hash"] != expected_hash:
                errors.append(f"conversation event {row['event_id']} has an invalid content hash")
            expected_previous = str(row["event_hash"])
        return not errors, errors

    def create_delivery_contract(
        self,
        conversation_id: str,
        *,
        goal: str,
        acceptance_criteria: list[str],
        allowed_scope: list[str],
        workflow: str,
        profile: str,
        provider: str,
        max_cost_usd: float,
        policy: Mapping[str, object],
    ) -> dict[str, Any]:
        version = int(self.connection.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM delivery_contracts WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()[0])
        now = _now()
        contract_id = f"contract_{uuid4().hex}"
        self.connection.execute(
            "UPDATE delivery_contracts SET status = 'superseded', superseded_at = ? WHERE conversation_id = ? AND status = 'active'",
            (now, conversation_id),
        )
        self.connection.execute(
            """INSERT INTO delivery_contracts(
              contract_id, conversation_id, version, status, goal, acceptance_criteria,
              allowed_scope, workflow, profile, provider, max_cost_usd, policy, created_at, superseded_at
            ) VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            (
                contract_id, conversation_id, version, goal, _json(acceptance_criteria),
                _json(allowed_scope), workflow, profile, provider, max_cost_usd, _json(policy), now,
            ),
        )
        self.connection.commit()
        self.update_conversation(conversation_id, active_contract_id=contract_id)
        return self.get_delivery_contract(contract_id) or {}

    def get_delivery_contract(self, contract_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM delivery_contracts WHERE contract_id = ?", (contract_id,)
        ).fetchone()
        return _decode(row, "acceptance_criteria", "allowed_scope", "policy")

    def delivery_contracts(self, conversation_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM delivery_contracts WHERE conversation_id = ? ORDER BY version", (conversation_id,)
        ).fetchall()
        return [_decode(row, "acceptance_criteria", "allowed_scope", "policy") or {} for row in rows]

    def create_delivery_candidate(
        self,
        conversation_id: str,
        *,
        contract_id: str,
        run_id: str,
        status: str,
        subject_digest: str,
        changeset_digest: str,
        message_sequence: int,
        policy_hash: str,
        parent_candidate_id: str | None,
        evidence_path: str | None,
        metadata: Mapping[str, object],
    ) -> dict[str, Any]:
        version = int(self.connection.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM delivery_candidates WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()[0])
        candidate_id = f"candidate_{uuid4().hex}"
        now = _now()
        self.connection.execute(
            "UPDATE delivery_candidates SET status = 'invalidated', updated_at = ? WHERE conversation_id = ? AND status = 'verified'",
            (now, conversation_id),
        )
        self.connection.execute(
            """INSERT INTO delivery_candidates(
              candidate_id, conversation_id, contract_id, run_id, version, status,
              subject_digest, changeset_digest, message_sequence, policy_hash,
              parent_candidate_id, evidence_path, created_at, updated_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                candidate_id, conversation_id, contract_id, run_id, version, status,
                subject_digest, changeset_digest, message_sequence, policy_hash,
                parent_candidate_id, evidence_path, now, now, _json(metadata),
            ),
        )
        self.connection.commit()
        self.update_conversation(conversation_id, active_candidate_id=candidate_id)
        return self.get_delivery_candidate(candidate_id) or {}

    def get_delivery_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM delivery_candidates WHERE candidate_id = ?", (candidate_id,)
        ).fetchone()
        return _decode(row, "metadata")

    def delivery_candidates(self, conversation_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM delivery_candidates WHERE conversation_id = ? ORDER BY version", (conversation_id,)
        ).fetchall()
        return [_decode(row, "metadata") or {} for row in rows]

    def update_delivery_candidate(
        self, candidate_id: str, *, status: str, metadata: Mapping[str, object] | None = None
    ) -> dict[str, Any]:
        current = self.get_delivery_candidate(candidate_id)
        if not current:
            raise FileNotFoundError(candidate_id)
        merged = dict(current.get("metadata") or {})
        if metadata:
            merged.update(metadata)
        self.connection.execute(
            "UPDATE delivery_candidates SET status = ?, updated_at = ?, metadata = ? WHERE candidate_id = ?",
            (status, _now(), _json(merged), candidate_id),
        )
        self.connection.commit()
        return self.get_delivery_candidate(candidate_id) or {}

    def sync_run_events(self, conversation_id: str, run_id: str) -> int:
        existing = {
            str(row[0]) for row in self.connection.execute(
                "SELECT run_event_id FROM conversation_events WHERE conversation_id = ? AND run_id = ? AND run_event_id IS NOT NULL",
                (conversation_id, run_id),
            ).fetchall()
        }
        count = 0
        for event in self.events(run_id):
            if str(event["event_id"]) in existing:
                continue
            self._project_run_event(conversation_id, event)
            count += 1
        return count

    def project_run_event(self, run_id: str, event: Mapping[str, Any]) -> bool:
        """Mirror a safe Run-event projection into its active Conversation."""
        run = self.get_run(run_id)
        metadata = run.get("metadata") if run and isinstance(run.get("metadata"), dict) else {}
        delivery = metadata.get("delivery_context") if isinstance(metadata, dict) else {}
        context = delivery if isinstance(delivery, dict) else {}
        conversation_id = str(context.get("conversation_id") or "")
        if not conversation_id or not self.get_conversation(conversation_id):
            return False
        duplicate = self.connection.execute(
            "SELECT 1 FROM conversation_events WHERE run_event_id = ? LIMIT 1",
            (str(event.get("event_id") or ""),),
        ).fetchone()
        if duplicate:
            return False
        self._project_run_event(conversation_id, event)
        return True

    def _project_run_event(self, conversation_id: str, event: Mapping[str, Any]) -> None:
        self.append_conversation_event(
            conversation_id,
            "run.event",
            _run_event_projection(event),
            actor="supervisor",
            run_id=str(event.get("run_id") or ""),
            run_event_id=str(event.get("event_id") or ""),
        )

    def create_device(self, *, label: str, public_key: str | None = None) -> dict[str, Any]:
        device_id = f"device_{uuid4().hex}"
        now = _now()
        self.connection.execute(
            """INSERT INTO devices(
              device_id, label, status, public_key, created_at, last_seen_at, revoked_at, metadata
            ) VALUES (?, ?, 'active', ?, ?, ?, NULL, '{}')""",
            (device_id, label, public_key, now, now),
        )
        self.connection.commit()
        return self.get_device(device_id) or {}

    def get_device(self, device_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,)).fetchone()
        return _decode(row, "metadata")

    def list_devices(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM devices ORDER BY created_at").fetchall()
        return [_decode(row, "metadata") or {} for row in rows]

    def revoke_device(self, device_id: str) -> None:
        now = _now()
        self.connection.execute(
            "UPDATE devices SET status = 'revoked', revoked_at = ? WHERE device_id = ?",
            (now, device_id),
        )
        self.connection.execute(
            "UPDATE web_sessions SET revoked_at = ? WHERE device_id = ? AND revoked_at IS NULL",
            (now, device_id),
        )
        self.connection.commit()

    def create_web_session(
        self,
        *,
        device_id: str,
        token_hash: str,
        expires_at: str,
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        session_id = f"session_{uuid4().hex}"
        now = _now()
        self.connection.execute(
            """INSERT INTO web_sessions(
              session_id, device_id, token_hash, created_at, expires_at, last_seen_at, revoked_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)""",
            (session_id, device_id, token_hash, now, expires_at, now, _json(metadata or {})),
        )
        self.connection.commit()
        return self.web_session(token_hash) or {}

    def web_session(self, token_hash: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            """SELECT web_sessions.*, devices.status AS device_status
               FROM web_sessions JOIN devices USING(device_id)
               WHERE token_hash = ?""",
            (token_hash,),
        ).fetchone()
        return _decode(row, "metadata")

    def touch_web_session(self, token_hash: str) -> None:
        now = _now()
        row = self.connection.execute(
            "SELECT device_id FROM web_sessions WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        if not row:
            return
        self.connection.execute(
            "UPDATE web_sessions SET last_seen_at = ? WHERE token_hash = ?", (now, token_hash)
        )
        self.connection.execute(
            "UPDATE devices SET last_seen_at = ? WHERE device_id = ?", (now, str(row[0]))
        )
        self.connection.commit()

    def revoke_web_session(self, token_hash: str) -> None:
        self.connection.execute(
            "UPDATE web_sessions SET revoked_at = ? WHERE token_hash = ?", (_now(), token_hash)
        )
        self.connection.commit()


def _event_summary(event: Mapping[str, Any]) -> str:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    for key in ("message", "summary", "status", "action"):
        value = payload.get(key)
        if value:
            return str(value)[:500]
    return str(event.get("type") or "Run event")


def _run_event_projection(event: Mapping[str, Any]) -> dict[str, object]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    projected: dict[str, object] = {
        "source_type": str(event.get("type") or ""),
        "source_sequence": int(event.get("sequence") or 0),
        "source_hash": str(event.get("event_hash") or ""),
        "stage_id": event.get("stage_id"),
        "summary": _event_summary(event),
    }
    safe_fields = {
        "worker_id",
        "role",
        "provider",
        "attempt",
        "status",
        "returncode",
        "read_only",
        "current_stage",
        "action",
        "max_actions",
        "feedback_summary",
        "stages",
        "statuses",
        "merge_order",
        "members",
        "mode",
        "max_parallel",
        "contract_errors",
        "interaction_id",
        "interaction_ids",
        "kind",
        "requirement_id",
        "question",
        "options",
        "allow_custom_input",
        "blocking",
        "risk",
        "timeout_seconds",
        "timeout_action",
        "recommended_option_id",
        "selected_option_id",
        "expires_at",
        "defaulted",
        "reason",
    }
    for key in safe_fields:
        if key in payload:
            projected[key] = payload[key]
    return projected
