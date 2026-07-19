"""SQLite-backed at-least-once execution queue for the local daemon."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..core.redaction import redact
from ..domain import ExecutionLease, ExecutionState, RunSpec
from ..models import RunStatus, utc_now
from .contracts import canonical_hash


Clock = Callable[[], int]
ACTIVE_STATES = ("queued", "leased", "running", "retry_wait", "cancel_requested")
WAIT_STATES = set(ACTIVE_STATES) | {"reconciliation_required"}
TERMINAL_STATES = {"succeeded", "failed", "cancelled"}


class ActiveExecutionError(RuntimeError):
    """Raised when a Run already has an active durable invocation."""


class DurableExecutionQueue:
    """Repository that keeps scheduler projection and audit events atomic."""

    def __init__(self, board: Any, *, clock: Clock | None = None) -> None:
        self.board = board
        self.conn = board.conn
        self.clock = clock or (lambda: int(time.time() * 1000))

    def create_initial(self, spec: RunSpec, *, worktree: Path) -> dict[str, Any]:
        """Atomically persist the Run, immutable request, and first Job."""
        with self.board.unit_of_work():
            self.board.create_run(
                run_id=spec.run_id,
                task=redact(spec.task),
                workflow=spec.workflow,
                provider=spec.default_provider,
                workspace=spec.workspace,
                worktree=worktree,
            )
            self.persist_run_spec(spec)
            return self.enqueue(spec.run_id, command="start", idempotency_key="execution:start")

    def persist_run_spec(self, spec: RunSpec, *, legacy: bool = False) -> None:
        raw = json.dumps(spec.to_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        encoded = redact(raw)
        payload = json.loads(encoded)
        digest = canonical_hash(payload)
        existing = self.conn.execute("SELECT payload_hash FROM run_specs WHERE run_id = ?", (spec.run_id,)).fetchone()
        if existing is not None:
            if str(existing["payload_hash"]) != digest:
                raise ValueError(f"persisted RunSpec does not match immutable run {spec.run_id}")
            return
        self.conn.execute(
            """
            INSERT INTO run_specs(run_id, schema_version, payload_json, payload_hash, legacy, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (spec.run_id, int(payload.get("schema_version") or 4), encoded, digest, int(legacy), utc_now()),
        )

    def load_run_spec(self, run_id: str) -> RunSpec:
        row = self.conn.execute("SELECT payload_json FROM run_specs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise FileNotFoundError(f"durable RunSpec not found: {run_id}")
        payload = json.loads(str(row["payload_json"]))
        if not isinstance(payload, dict):
            raise ValueError(f"invalid durable RunSpec: {run_id}")
        return RunSpec.from_payload(payload)

    def enqueue(
        self,
        run_id: str,
        *,
        command: str,
        idempotency_key: str,
        request: Mapping[str, object] | None = None,
        max_attempts: int = 3,
    ) -> dict[str, Any]:
        if command not in {"start", "resume"}:
            raise ValueError(f"unsupported execution command: {command}")
        now = self.clock()
        with self.board.unit_of_work():
            existing = self.conn.execute(
                "SELECT * FROM execution_jobs WHERE run_id = ? AND idempotency_key = ?",
                (run_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                return dict(existing)
            active = self._active_job(run_id)
            if active is not None:
                raise ActiveExecutionError(f"run already has active execution: {run_id}")
            job_id = f"job_{uuid4().hex}"
            try:
                self.conn.execute(
                    """
                    INSERT INTO execution_jobs(
                      job_id, run_id, command, state, idempotency_key, request_json,
                      attempt, max_attempts, available_at_ms, created_at_ms, updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        run_id,
                        command,
                        str(ExecutionState.QUEUED),
                        idempotency_key,
                        json.dumps(dict(request or {}), ensure_ascii=False, sort_keys=True),
                        max(1, int(max_attempts)),
                        now,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ActiveExecutionError(f"run already has active execution: {run_id}") from exc
            self._append_event(
                run_id,
                job_id,
                "execution.enqueued",
                {"command": command, "max_attempts": max(1, int(max_attempts))},
                idempotency_key=f"{job_id}:enqueued",
            )
            return self.get_job(job_id, internal=True)

    def import_legacy_run(self, run_id: str, *, command: str, ambiguous_reason: str | None = None) -> dict[str, Any]:
        """Create the first scheduler record for a pre-v3 non-terminal Run."""
        now = self.clock()
        with self.board.unit_of_work():
            job = self.enqueue(
                run_id,
                command=command,
                idempotency_key="legacy:execution-import",
                request={"legacy": True},
            )
            if not ambiguous_reason:
                self._append_event(
                    run_id,
                    str(job["job_id"]),
                    "execution.legacy_imported",
                    {"safe_to_retry": True, "command": command},
                    idempotency_key=f"{job['job_id']}:legacy-imported",
                )
                return job
            reason = redact(ambiguous_reason)[:500]
            self.conn.execute(
                "UPDATE execution_jobs SET state='reconciliation_required',last_error=?,updated_at_ms=? WHERE job_id=?",
                (reason, now, job["job_id"]),
            )
            self.board.set_run_status(
                run_id,
                RunStatus.BLOCKED,
                idempotency_key=f"execution:{job['job_id']}:legacy-reconciliation",
            )
            self._append_event(
                run_id,
                str(job["job_id"]),
                "execution.reconciliation_required",
                {"reason": reason, "legacy": True},
                idempotency_key=f"{job['job_id']}:legacy-reconciliation",
            )
            return self.get_job(str(job["job_id"]), internal=True)

    def claim(self, owner: str, *, lease_ms: int = 30_000) -> ExecutionLease | None:
        now = self.clock()
        with self.board.unit_of_work():
            row = self.conn.execute(
                """
                SELECT * FROM execution_jobs
                WHERE state IN ('queued', 'retry_wait') AND available_at_ms <= ?
                ORDER BY available_at_ms, created_at_ms, job_id
                LIMIT 1
                """,
                (now,),
            ).fetchone()
            if row is None:
                return None
            job_id = str(row["job_id"])
            token = uuid4().hex
            fence = int(row["fencing_token"] or 0) + 1
            attempt = int(row["attempt"] or 0) + 1
            changed = self.conn.execute(
                """
                UPDATE execution_jobs
                SET state='leased', lease_owner=?, lease_token=?, fencing_token=?, attempt=?,
                    lease_expires_at_ms=?, heartbeat_at_ms=?, updated_at_ms=?
                WHERE job_id=? AND state IN ('queued', 'retry_wait') AND available_at_ms <= ?
                """,
                (owner, token, fence, attempt, now + max(1, lease_ms), now, now, job_id, now),
            ).rowcount
            if changed != 1:
                return None
            self._append_event(
                str(row["run_id"]),
                job_id,
                "execution.leased",
                {"attempt": attempt, "fencing_token": fence},
                idempotency_key=f"{job_id}:lease:{fence}",
            )
            return ExecutionLease(
                job_id=job_id,
                run_id=str(row["run_id"]),
                command=str(row["command"]),
                lease_owner=owner,
                lease_token=token,
                fencing_token=fence,
                attempt=attempt,
                max_attempts=int(row["max_attempts"]),
            )

    def mark_running(self, lease: ExecutionLease) -> None:
        now = self.clock()
        with self.board.unit_of_work():
            self._lease_update(
                lease,
                "UPDATE execution_jobs SET state='running', updated_at_ms=? WHERE job_id=? AND lease_owner=? AND lease_token=? AND fencing_token=? AND state='leased'",
                (now,),
            )
            self._append_event(
                lease.run_id,
                lease.job_id,
                "execution.started",
                {"attempt": lease.attempt, "fencing_token": lease.fencing_token},
                idempotency_key=f"{lease.job_id}:started:{lease.fencing_token}",
            )

    def heartbeat(self, lease: ExecutionLease, *, lease_ms: int = 30_000) -> str:
        now = self.clock()
        with self.board.unit_of_work():
            row = self.conn.execute(
                "SELECT state, cancel_reason FROM execution_jobs WHERE job_id=? AND lease_owner=? AND lease_token=? AND fencing_token=?",
                (lease.job_id, lease.lease_owner, lease.lease_token, lease.fencing_token),
            ).fetchone()
            if row is None or str(row["state"]) not in {"leased", "running", "cancel_requested"}:
                return "lost"
            state = str(row["state"])
            if state != "cancel_requested":
                self.conn.execute(
                    "UPDATE execution_jobs SET heartbeat_at_ms=?, lease_expires_at_ms=?, updated_at_ms=? WHERE job_id=?",
                    (now, now + max(1, lease_ms), now, lease.job_id),
                )
            return state

    def assert_lease(self, lease: ExecutionLease) -> None:
        now = self.clock()
        row = self.conn.execute(
            """
            SELECT 1 FROM execution_jobs
            WHERE job_id=? AND run_id=? AND lease_owner=? AND lease_token=? AND fencing_token=?
              AND state IN ('leased', 'running') AND lease_expires_at_ms > ?
            """,
            (lease.job_id, lease.run_id, lease.lease_owner, lease.lease_token, lease.fencing_token, now),
        ).fetchone()
        if row is None:
            from ..domain import LeaseLost

            raise LeaseLost(f"stale execution fence for {lease.job_id}")

    def complete(self, lease: ExecutionLease, *, outcome_status: str) -> dict[str, Any]:
        now = self.clock()
        with self.board.unit_of_work():
            self._lease_update(
                lease,
                """
                UPDATE execution_jobs SET state='succeeded', outcome_status=?, completed_at_ms=?, updated_at_ms=?,
                    lease_owner=NULL, lease_token=NULL, lease_expires_at_ms=NULL
                WHERE job_id=? AND lease_owner=? AND lease_token=? AND fencing_token=? AND state IN ('leased','running')
                """,
                (str(outcome_status), now, now),
            )
            self._append_event(
                lease.run_id,
                lease.job_id,
                "execution.succeeded",
                {"attempt": lease.attempt, "run_status": str(outcome_status)},
                idempotency_key=f"{lease.job_id}:succeeded",
            )
            return self.get_job(lease.job_id)

    def retry_or_fail(
        self,
        lease: ExecutionLease,
        error: BaseException,
        *,
        delays_ms: tuple[int, ...] = (1_000, 5_000, 30_000),
        retryable: bool = True,
    ) -> str:
        now = self.clock()
        message = redact(str(error))[:1000]
        with self.board.unit_of_work():
            if not retryable or lease.attempt >= lease.max_attempts:
                self._lease_update(
                    lease,
                    """
                    UPDATE execution_jobs SET state='failed', last_error=?, completed_at_ms=?, updated_at_ms=?,
                        lease_owner=NULL, lease_token=NULL, lease_expires_at_ms=NULL
                    WHERE job_id=? AND lease_owner=? AND lease_token=? AND fencing_token=? AND state IN ('leased','running')
                    """,
                    (message, now, now),
                )
                self._append_event(
                    lease.run_id, lease.job_id, "execution.failed",
                    {"attempt": lease.attempt, "error": message},
                    idempotency_key=f"{lease.job_id}:failed",
                )
                self.board.fail_worker(
                    lease.run_id,
                    error_type="worker_exception",
                    message=message,
                    idempotency_key=f"execution:{lease.job_id}:worker-failed",
                )
                return "failed"
            delay = delays_ms[min(max(lease.attempt - 1, 0), len(delays_ms) - 1)]
            self._lease_update(
                lease,
                """
                UPDATE execution_jobs SET state='retry_wait', last_error=?, available_at_ms=?, updated_at_ms=?,
                    lease_owner=NULL, lease_token=NULL, lease_expires_at_ms=NULL
                WHERE job_id=? AND lease_owner=? AND lease_token=? AND fencing_token=? AND state IN ('leased','running')
                """,
                (message, now + delay, now),
            )
            self._append_event(
                lease.run_id, lease.job_id, "execution.retry_scheduled",
                {"attempt": lease.attempt, "delay_ms": delay, "error": message},
                idempotency_key=f"{lease.job_id}:retry:{lease.attempt}",
            )
            return "retry_wait"

    def release_for_shutdown(self, lease: ExecutionLease) -> str:
        """Fence a draining worker and preserve an honest recovery state."""
        now = self.clock()
        with self.board.unit_of_work():
            provider_running = self.conn.execute(
                "SELECT 1 FROM provider_attempts WHERE run_id=? AND status='running' LIMIT 1", (lease.run_id,)
            ).fetchone()
            target = "reconciliation_required" if provider_running is not None else "retry_wait"
            self._lease_update(
                lease,
                f"""
                UPDATE execution_jobs SET state='{target}', available_at_ms=?, updated_at_ms=?,
                    lease_owner=NULL, lease_token=NULL, lease_expires_at_ms=NULL
                WHERE job_id=? AND lease_owner=? AND lease_token=? AND fencing_token=?
                  AND state IN ('leased','running')
                """,
                (now, now),
            )
            self._append_event(
                lease.run_id,
                lease.job_id,
                "execution.reconciliation_required" if provider_running is not None else "execution.released",
                {"reason": "daemon shutdown", "safe_to_retry": provider_running is None},
                idempotency_key=f"{lease.job_id}:shutdown:{lease.fencing_token}",
            )
            if provider_running is not None:
                self.board.set_run_status(
                    lease.run_id,
                    RunStatus.BLOCKED,
                    idempotency_key=f"execution:{lease.job_id}:shutdown-reconciliation",
                )
            return target

    def require_reconciliation(self, lease: ExecutionLease, reason: str) -> dict[str, Any]:
        now = self.clock()
        clean_reason = redact(reason)[:500]
        with self.board.unit_of_work():
            self._lease_update(
                lease,
                """
                UPDATE execution_jobs SET state='reconciliation_required',last_error=?,updated_at_ms=?,
                    lease_owner=NULL,lease_token=NULL,lease_expires_at_ms=NULL
                WHERE job_id=? AND lease_owner=? AND lease_token=? AND fencing_token=?
                  AND state IN ('leased','running')
                """,
                (clean_reason, now),
            )
            self.board.set_run_status(
                lease.run_id,
                RunStatus.BLOCKED,
                idempotency_key=f"execution:{lease.job_id}:reconciliation-required",
            )
            self._append_event(
                lease.run_id,
                lease.job_id,
                "execution.reconciliation_required",
                {"reason": clean_reason},
                idempotency_key=f"{lease.job_id}:reconciliation:{lease.fencing_token}",
            )
            return self.get_job(lease.job_id)

    def request_cancel(self, run_id: str, *, reason: str = "") -> dict[str, Any]:
        now = self.clock()
        clean_reason = redact(str(reason or "user requested cancellation"))[:500]
        with self.board.unit_of_work():
            job = self._active_job(run_id)
            if job is None:
                run = self.board.get_run(run_id)
                latest = self.latest_for_run(run_id, internal=True)
                if latest is not None and str(latest["state"]) in TERMINAL_STATES:
                    return {
                        "run_id": run_id,
                        "job_id": latest["job_id"],
                        "status": "already_finished",
                        "run_status": str(run["status"]),
                    }
                if str(run["status"]) != str(RunStatus.ABORTED):
                    self.board.set_run_status(run_id, RunStatus.ABORTED, idempotency_key="execution:cancel:no-active")
                self._append_event(run_id, None, "execution.cancelled", {"reason": clean_reason}, idempotency_key="execution:cancel:no-active")
                return {"run_id": run_id, "status": "cancelled", "run_status": str(RunStatus.ABORTED)}
            job_id = str(job["job_id"])
            state = str(job["state"])
            self._append_event(
                run_id,
                job_id,
                "execution.cancel_requested",
                {"reason": clean_reason},
                idempotency_key=f"{job_id}:cancel-requested",
            )
            if state in {"queued", "retry_wait"}:
                self.conn.execute(
                    """
                    UPDATE execution_jobs SET state='cancelled', cancel_requested_at_ms=?, cancel_reason=?,
                        completed_at_ms=?, updated_at_ms=? WHERE job_id=? AND state IN ('queued','retry_wait')
                    """,
                    (now, clean_reason, now, now, job_id),
                )
                self.board.set_run_status(run_id, RunStatus.ABORTED, idempotency_key=f"execution:{job_id}:cancelled")
                self._append_event(run_id, job_id, "execution.cancelled", {"reason": clean_reason}, idempotency_key=f"{job_id}:cancelled")
                return {"run_id": run_id, "job_id": job_id, "status": "cancelled", "run_status": str(RunStatus.ABORTED)}
            if state != "cancel_requested":
                self.conn.execute(
                    """
                    UPDATE execution_jobs SET state='cancel_requested', cancel_requested_at_ms=?, cancel_reason=?, updated_at_ms=?
                    WHERE job_id=? AND state IN ('leased','running')
                    """,
                    (now, clean_reason, now, job_id),
                )
            run_status = str(self.board.get_run(run_id)["status"])
            return {"run_id": run_id, "job_id": job_id, "status": "cancel_requested", "run_status": run_status}

    def acknowledge_cancel(self, lease: ExecutionLease) -> dict[str, Any]:
        now = self.clock()
        with self.board.unit_of_work():
            self._lease_update(
                lease,
                """
                UPDATE execution_jobs SET state='cancelled', completed_at_ms=?, updated_at_ms=?,
                    lease_owner=NULL, lease_token=NULL, lease_expires_at_ms=NULL
                WHERE job_id=? AND lease_owner=? AND lease_token=? AND fencing_token=? AND state='cancel_requested'
                """,
                (now, now),
            )
            self.conn.execute(
                """UPDATE provider_attempts SET status='cancelled',failure_kind='cancelled',completed_at=?
                   WHERE run_id=? AND status='running'""",
                (utc_now(), lease.run_id),
            )
            self.board.set_run_status(lease.run_id, RunStatus.ABORTED, idempotency_key=f"execution:{lease.job_id}:cancelled")
            self._append_event(
                lease.run_id, lease.job_id, "execution.cancelled", {"attempt": lease.attempt},
                idempotency_key=f"{lease.job_id}:cancelled",
            )
            return self.get_job(lease.job_id)

    def recover_expired(self) -> dict[str, int]:
        now = self.clock()
        counts = {"requeued": 0, "cancelled": 0, "reconciliation_required": 0}
        with self.board.unit_of_work():
            rows = list(
                self.conn.execute(
                    """
                    SELECT * FROM execution_jobs
                    WHERE state IN ('leased','running','cancel_requested') AND lease_expires_at_ms <= ?
                    ORDER BY created_at_ms
                    """,
                    (now,),
                )
            )
            for raw in rows:
                row = dict(raw)
                job_id, run_id, state = str(row["job_id"]), str(row["run_id"]), str(row["state"])
                if state == "cancel_requested":
                    self.conn.execute(
                        """UPDATE execution_jobs SET state='cancelled', completed_at_ms=?, updated_at_ms=?,
                           lease_owner=NULL, lease_token=NULL, lease_expires_at_ms=NULL WHERE job_id=?""",
                        (now, now, job_id),
                    )
                    self.conn.execute(
                        """UPDATE provider_attempts SET status='cancelled',failure_kind='cancelled',completed_at=?
                           WHERE run_id=? AND status='running'""",
                        (utc_now(), run_id),
                    )
                    self.board.set_run_status(run_id, RunStatus.ABORTED, idempotency_key=f"execution:{job_id}:expired-cancel")
                    self._append_event(run_id, job_id, "execution.cancelled", {"reason": "expired cancellation lease"}, idempotency_key=f"{job_id}:expired-cancel")
                    counts["cancelled"] += 1
                    continue
                provider_running = self.conn.execute(
                    "SELECT 1 FROM provider_attempts WHERE run_id=? AND status='running' LIMIT 1", (run_id,)
                ).fetchone()
                if provider_running is not None:
                    self.conn.execute(
                        """UPDATE execution_jobs SET state='reconciliation_required', updated_at_ms=?,
                           lease_owner=NULL, lease_token=NULL, lease_expires_at_ms=NULL WHERE job_id=?""",
                        (now, job_id),
                    )
                    self.board.set_run_status(run_id, RunStatus.BLOCKED, idempotency_key=f"execution:{job_id}:reconciliation")
                    self._append_event(
                        run_id, job_id, "execution.reconciliation_required",
                        {"reason": "opaque provider outcome is unknown"},
                        idempotency_key=f"{job_id}:reconciliation-required",
                    )
                    counts["reconciliation_required"] += 1
                else:
                    self.conn.execute(
                        """UPDATE execution_jobs SET state='retry_wait', available_at_ms=?, updated_at_ms=?,
                           lease_owner=NULL, lease_token=NULL, lease_expires_at_ms=NULL WHERE job_id=?""",
                        (now, now, job_id),
                    )
                    self._append_event(
                        run_id, job_id, "execution.recovered", {"reason": "expired lease at safe checkpoint"},
                        idempotency_key=f"{job_id}:recovered:{int(row['fencing_token'])}",
                    )
                    counts["requeued"] += 1
        return counts

    def reconcile(self, run_id: str, *, decision: str, reason: str, acknowledge_duplicate_risk: bool = False) -> dict[str, Any]:
        if decision not in {"retry", "abort"}:
            raise ValueError("reconciliation decision must be retry or abort")
        clean_reason = redact(str(reason).strip())[:500]
        if not clean_reason:
            raise ValueError("reconciliation reason is required")
        if decision == "retry" and not acknowledge_duplicate_risk:
            raise ValueError("retry requires acknowledgement of possible duplicate provider side effects")
        with self.board.unit_of_work():
            previous = self.conn.execute(
                """SELECT * FROM execution_jobs WHERE run_id=? AND state='reconciliation_required'
                   ORDER BY updated_at_ms DESC LIMIT 1""",
                (run_id,),
            ).fetchone()
            if previous is None:
                raise ValueError(f"run does not require reconciliation: {run_id}")
            previous_job = str(previous["job_id"])
            self._append_event(
                run_id,
                previous_job,
                f"execution.reconciled_{decision}",
                {"reason": clean_reason, "duplicate_risk_acknowledged": bool(acknowledge_duplicate_risk)},
                idempotency_key=f"{previous_job}:reconcile:{decision}",
            )
            if decision == "abort":
                self.board.set_run_status(run_id, RunStatus.ABORTED, idempotency_key=f"execution:{previous_job}:reconcile-abort")
                return {"run_id": run_id, "job_id": previous_job, "status": "cancelled", "run_status": str(RunStatus.ABORTED)}
            self.board.set_run_status(
                run_id,
                RunStatus.RUNNING,
                recovery_reason=clean_reason,
                idempotency_key=f"execution:{previous_job}:reconcile-retry",
            )
            job = self.enqueue(
                run_id,
                command="resume",
                idempotency_key=f"reconcile:{previous_job}:retry",
                request={"reconciliation_reason": clean_reason, "duplicate_risk_acknowledged": True},
            )
            return {"run_id": run_id, "job_id": job["job_id"], "status": "queued", "run_status": str(RunStatus.RUNNING)}

    def get_job(self, job_id: str, *, internal: bool = False) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM execution_jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise FileNotFoundError(f"execution job not found: {job_id}")
        return dict(row) if internal else _public_job(dict(row), self.clock())

    def list_for_run(self, run_id: str, *, internal: bool = False) -> list[dict[str, Any]]:
        rows = [dict(row) for row in self.conn.execute("SELECT * FROM execution_jobs WHERE run_id=? ORDER BY created_at_ms", (run_id,))]
        return rows if internal else [_public_job(row, self.clock()) for row in rows]

    def latest_for_run(self, run_id: str, *, internal: bool = False) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM execution_jobs WHERE run_id=? ORDER BY created_at_ms DESC LIMIT 1", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return dict(row) if internal else _public_job(dict(row), self.clock())

    def events_for_run(self, run_id: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for row in self.conn.execute(
            """SELECT event_id, run_id, job_id, sequence, event_type, event_version,
                      idempotency_key, payload_json, created_at_ms
               FROM execution_events WHERE run_id=? ORDER BY sequence""",
            (run_id,),
        ):
            events.append(
                {
                    "event_id": str(row["event_id"]),
                    "run_id": str(row["run_id"]),
                    "job_id": str(row["job_id"]) if row["job_id"] is not None else None,
                    "sequence": int(row["sequence"]),
                    "event_type": str(row["event_type"]),
                    "event_version": int(row["event_version"]),
                    "idempotency_key": str(row["idempotency_key"]),
                    "payload": _public_event_payload(json.loads(str(row["payload_json"]))),
                    "created_at_ms": int(row["created_at_ms"]),
                }
            )
        return events

    def status(self, *, worker_count: int = 0, alive_workers: int = 0, busy_workers: int = 0) -> dict[str, Any]:
        now = self.clock()
        counts = {str(row["state"]): int(row["count"]) for row in self.conn.execute("SELECT state, COUNT(*) AS count FROM execution_jobs GROUP BY state")}
        oldest = self.conn.execute(
            "SELECT MIN(created_at_ms) AS value FROM execution_jobs WHERE state IN ('queued','retry_wait')"
        ).fetchone()
        expired = self.conn.execute(
            "SELECT COUNT(*) AS count FROM execution_jobs WHERE state IN ('leased','running','cancel_requested') AND lease_expires_at_ms <= ?",
            (now,),
        ).fetchone()
        return {
            "accepting": True,
            "workers": {"configured": worker_count, "alive": alive_workers, "busy": busy_workers},
            "queue": counts,
            "oldest_queued_age_ms": max(0, now - int(oldest["value"])) if oldest and oldest["value"] is not None else None,
            "expired_leases": int(expired["count"]) if expired else 0,
            "reconciliation_required": counts.get("reconciliation_required", 0),
        }

    def register_daemon(self, instance_id: str, *, worker_count: int) -> None:
        now = self.clock()
        with self.board.unit_of_work():
            self.conn.execute(
                """INSERT INTO daemon_instances(instance_id,pid,status,worker_count,started_at_ms,heartbeat_at_ms)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(instance_id) DO UPDATE SET status='running',worker_count=excluded.worker_count,heartbeat_at_ms=excluded.heartbeat_at_ms""",
                (instance_id, os.getpid(), "running", worker_count, now, now),
            )

    def heartbeat_daemon(self, instance_id: str) -> None:
        now = self.clock()
        with self.board.unit_of_work():
            self.conn.execute(
                "UPDATE daemon_instances SET heartbeat_at_ms=? WHERE instance_id=? AND status='running'", (now, instance_id)
            )

    def stop_daemon(self, instance_id: str) -> None:
        now = self.clock()
        with self.board.unit_of_work():
            self.conn.execute(
                "UPDATE daemon_instances SET status='stopped',stopped_at_ms=?,heartbeat_at_ms=? WHERE instance_id=?",
                (now, now, instance_id),
            )

    def _active_job(self, run_id: str) -> dict[str, Any] | None:
        placeholders = ",".join("?" for _ in ACTIVE_STATES)
        row = self.conn.execute(
            f"SELECT * FROM execution_jobs WHERE run_id=? AND state IN ({placeholders}) ORDER BY created_at_ms DESC LIMIT 1",
            (run_id, *ACTIVE_STATES),
        ).fetchone()
        return dict(row) if row is not None else None

    def _lease_update(self, lease: ExecutionLease, sql: str, prefix: tuple[object, ...]) -> None:
        values = (*prefix, lease.job_id, lease.lease_owner, lease.lease_token, lease.fencing_token)
        if self.conn.execute(sql, values).rowcount != 1:
            from ..domain import LeaseLost

            raise LeaseLost(f"stale execution fence for {lease.job_id}")

    def _append_event(
        self,
        run_id: str,
        job_id: str | None,
        event_type: str,
        payload: Mapping[str, object],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        existing = self.conn.execute(
            "SELECT * FROM execution_events WHERE run_id=? AND idempotency_key=?", (run_id, idempotency_key)
        ).fetchone()
        if existing is not None:
            return dict(existing)
        row = self.conn.execute("SELECT COALESCE(MAX(sequence),0)+1 AS sequence FROM execution_events WHERE run_id=?", (run_id,)).fetchone()
        sequence = int(row["sequence"])
        event = {
            "event_id": f"evt_exec_{uuid4().hex}",
            "run_id": run_id,
            "job_id": job_id,
            "sequence": sequence,
            "event_type": event_type,
            "event_version": 1,
            "idempotency_key": idempotency_key,
            "payload": dict(payload),
            "created_at_ms": self.clock(),
        }
        self.conn.execute(
            """INSERT INTO execution_events(event_id,run_id,job_id,sequence,event_type,event_version,idempotency_key,payload_json,created_at_ms)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                event["event_id"], run_id, job_id, sequence, event_type, 1, idempotency_key,
                json.dumps(event["payload"], ensure_ascii=False, sort_keys=True), event["created_at_ms"],
            ),
        )
        sink = getattr(self.board, "_event_sink", None)
        if callable(sink):
            public = {
                "type": "execution_event",
                "version": 1,
                "event_id": event["event_id"],
                "run_id": run_id,
                "job_id": job_id,
                "sequence": sequence,
                "event_type": event_type,
                "payload": _public_event_payload(dict(payload)),
                "created_at_ms": event["created_at_ms"],
            }
            self.board.engine.on_commit(lambda: sink(public))
        return event


def _public_job(row: dict[str, Any], now_ms: int) -> dict[str, Any]:
    heartbeat = row.get("heartbeat_at_ms")
    return {
        "job_id": row.get("job_id"),
        "run_id": row.get("run_id"),
        "command": row.get("command"),
        "state": row.get("state"),
        "attempt": int(row.get("attempt") or 0),
        "max_attempts": int(row.get("max_attempts") or 0),
        "cancel_requested": row.get("state") == "cancel_requested",
        "cancel_reason": row.get("cancel_reason"),
        "last_error": row.get("last_error"),
        "outcome_status": row.get("outcome_status"),
        "heartbeat_age_ms": max(0, now_ms - int(heartbeat)) if heartbeat is not None else None,
        "created_at_ms": row.get("created_at_ms"),
        "updated_at_ms": row.get("updated_at_ms"),
        "completed_at_ms": row.get("completed_at_ms"),
        "recovery_action": "retry_or_abort" if row.get("state") == "reconciliation_required" else None,
    }


def _public_event_payload(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    private = {"lease_token", "fencing_token", "lease_owner", "request_json"}
    return {str(key): value for key, value in payload.items() if str(key) not in private}
