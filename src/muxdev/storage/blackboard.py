"""Persistent run store and SQLite blackboard.

The blackboard is muxdev's local source of truth for run status, stages, agents,
approvals, artifacts, review blockers, usage, checkpoints, and errors. Runtime,
CLI, TUI, reports, and resume/retry/skip operations all read from the same file
so completed and interrupted runs remain auditable.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..config.loader import path_config
from ..models import ApprovalStatus, ProviderActionStatus, RunStatus, StageStatus, TraceEvent, utc_now
from ..core.redaction import redact
from .contracts import canonical_hash


class RunStore:
    """Resolve and create run directories under the configured runtime path."""

    def __init__(self, root: Path, runs_dir: Path | None = None):
        self.root = root
        self.runs_dir = runs_dir or path_config(root, "runs")
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def create_run_dir(self, run_id: str) -> Path:
        run_dir = self.runs_dir / run_id
        (run_dir / "session").mkdir(parents=True, exist_ok=True)
        return run_dir

    def find_run_dir(self, run_id: str) -> Path:
        run_dir = self.runs_dir / run_id
        if not run_dir.exists():
            raise FileNotFoundError(f"run not found: {run_id}")
        return run_dir

    def latest_run_id(self) -> str | None:
        if not self.runs_dir.exists():
            return None
        dirs = [path for path in self.runs_dir.iterdir() if path.is_dir()]
        if not dirs:
            return None
        return max(dirs, key=lambda path: path.stat().st_mtime).name


class Blackboard:
    """Small SQLite facade used as the run-local coordination database."""

    RUN_FILTERED_TABLES = {
        "runs",
        "stages",
        "agents",
        "approvals",
        "provider_actions",
        "provider_attempts",
        "session_capsules",
        "feedback_events",
        "ci_rescues",
        "cache_entries",
        "skill_locks",
        "plugin_manifests",
        "guardrail_events",
        "parallel_conflicts",
        "semantic_merge_reviews",
        "provider_learning",
        "multi_repo_orchestrations",
        "artifacts",
        "review_blockers",
        "test_results",
        "usage_records",
        "checkpoints",
        "error_details",
        "stage_contracts",
        "evidence_bundles",
        "evidence_items",
        "evidence_scorecards",
        "ledger_events",
        "snapshots",
        "validator_panels",
    }

    def __init__(self, run_dir: Path, db_path: Path | None = None):
        self.run_dir = run_dir
        self.db_path = db_path or run_dir / "blackboard.sqlite"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # The desktop Windows sandbox used for local demos can reject SQLite
        # sidecar journal files. The daemon is the only writer in client-server
        # mode, so a self-contained database is acceptable here too.
        self.conn.execute("PRAGMA journal_mode=OFF")
        if db_path is None:
            # Legacy run-local runs are single-process, so exclusive locking is
            # safe there. The daemon-owned global DB must remain readable by API
            # request handlers using separate connections.
            self.conn.execute("PRAGMA locking_mode=EXCLUSIVE")
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Blackboard":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def _init_schema(self) -> None:
        """Create every table needed by M0-M7 runtime features."""
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
              run_id TEXT PRIMARY KEY,
              task TEXT NOT NULL,
              workflow TEXT NOT NULL,
              provider TEXT NOT NULL,
              status TEXT NOT NULL,
              workspace TEXT NOT NULL,
              worktree TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS stages (
              run_id TEXT NOT NULL,
              stage_id TEXT NOT NULL,
              role TEXT,
              status TEXT NOT NULL,
              started_at TEXT,
              completed_at TEXT,
              output_path TEXT,
              summary TEXT,
              PRIMARY KEY (run_id, stage_id)
            );
            CREATE TABLE IF NOT EXISTS agents (
              run_id TEXT NOT NULL,
              role TEXT NOT NULL,
              provider TEXT NOT NULL,
              session_id TEXT,
              status TEXT NOT NULL,
              PRIMARY KEY (run_id, role)
            );
            CREATE TABLE IF NOT EXISTS approvals (
              approval_id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              stage_id TEXT,
              type TEXT NOT NULL,
              status TEXT NOT NULL,
              reason TEXT NOT NULL,
              subject_hash TEXT,
              subject_json TEXT,
              created_at TEXT NOT NULL,
              decided_at TEXT
            );
            CREATE TABLE IF NOT EXISTS provider_actions (
              action_id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              stage_id TEXT,
              provider TEXT NOT NULL,
              role TEXT,
              kind TEXT NOT NULL,
              status TEXT NOT NULL,
              prompt_text TEXT NOT NULL,
              options_json TEXT NOT NULL,
              transcript_path TEXT,
              chunks_path TEXT,
              attach_command TEXT,
              source_event_hash TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS provider_attempts (
              run_id TEXT NOT NULL,
              stage_id TEXT NOT NULL,
              provider TEXT NOT NULL,
              role TEXT,
              attempt INTEGER NOT NULL,
              status TEXT NOT NULL,
              failure_kind TEXT,
              returncode INTEGER,
              summary TEXT,
              artifact_path TEXT,
              capsule_path TEXT,
              started_at TEXT NOT NULL,
              completed_at TEXT,
              PRIMARY KEY (run_id, stage_id, provider, attempt)
            );
            CREATE TABLE IF NOT EXISTS session_capsules (
              capsule_id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              stage_id TEXT NOT NULL,
              role TEXT,
              provider TEXT NOT NULL,
              kind TEXT NOT NULL,
              status TEXT NOT NULL,
              summary TEXT NOT NULL,
              path TEXT NOT NULL,
              capsule_hash TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS feedback_events (
              feedback_id TEXT PRIMARY KEY,
              run_id TEXT,
              source TEXT NOT NULL,
              kind TEXT NOT NULL,
              severity TEXT NOT NULL,
              status TEXT NOT NULL,
              route_to TEXT,
              content TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ci_rescues (
              rescue_id TEXT PRIMARY KEY,
              feedback_id TEXT NOT NULL,
              run_id TEXT,
              rescue_run_id TEXT,
              route_to TEXT NOT NULL,
              status TEXT NOT NULL,
              summary TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cache_entries (
              cache_key TEXT PRIMARY KEY,
              run_id TEXT,
              kind TEXT NOT NULL,
              path TEXT NOT NULL,
              value_hash TEXT NOT NULL,
              metadata_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              last_accessed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS skill_locks (
              skill_name TEXT PRIMARY KEY,
              run_id TEXT,
              skill_version TEXT,
              skill_hash TEXT NOT NULL,
              path TEXT NOT NULL,
              compatible_roles_json TEXT NOT NULL,
              status TEXT NOT NULL,
              metadata_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS plugin_manifests (
              plugin_name TEXT PRIMARY KEY,
              run_id TEXT,
              source TEXT NOT NULL,
              manifest_path TEXT,
              manifest_hash TEXT NOT NULL,
              trust TEXT NOT NULL,
              permissions_json TEXT NOT NULL,
              status TEXT NOT NULL,
              warnings_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS guardrail_events (
              event_id TEXT PRIMARY KEY,
              run_id TEXT,
              tool TEXT NOT NULL,
              decision TEXT NOT NULL,
              reason TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS parallel_conflicts (
              conflict_id TEXT PRIMARY KEY,
              run_id TEXT,
              stage_id TEXT,
              stages_json TEXT NOT NULL,
              files_json TEXT NOT NULL,
              severity TEXT NOT NULL,
              status TEXT NOT NULL,
              resolution TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS semantic_merge_reviews (
              review_id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              decision TEXT NOT NULL,
              patch_hash TEXT NOT NULL,
              findings_json TEXT NOT NULL,
              path TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS provider_learning (
              provider TEXT NOT NULL,
              role TEXT NOT NULL,
              run_id TEXT,
              attempts INTEGER NOT NULL,
              successes INTEGER NOT NULL,
              failures INTEGER NOT NULL,
              human_actions INTEGER NOT NULL,
              score REAL NOT NULL,
              metadata_json TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (provider, role)
            );
            CREATE TABLE IF NOT EXISTS multi_repo_orchestrations (
              orchestration_id TEXT PRIMARY KEY,
              run_id TEXT,
              workspace TEXT NOT NULL,
              mode TEXT NOT NULL,
              task TEXT NOT NULL,
              status TEXT NOT NULL,
              repos_json TEXT NOT NULL,
              plan_path TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS artifacts (
              run_id TEXT NOT NULL,
              stage_id TEXT,
              name TEXT NOT NULL,
              path TEXT NOT NULL,
              kind TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS review_blockers (
              run_id TEXT NOT NULL,
              stage_id TEXT NOT NULL,
              type TEXT NOT NULL,
              file TEXT,
              line INTEGER,
              severity TEXT NOT NULL,
              suggestion TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS test_results (
              run_id TEXT NOT NULL,
              stage_id TEXT NOT NULL,
              passed INTEGER NOT NULL,
              command TEXT NOT NULL,
              summary TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS usage_records (
              run_id TEXT NOT NULL,
              provider TEXT NOT NULL,
              tokens INTEGER NOT NULL,
              cost_usd REAL NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS checkpoints (
              run_id TEXT NOT NULL,
              stage_id TEXT,
              kind TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS error_details (
              run_id TEXT NOT NULL,
              stage_id TEXT,
              type TEXT NOT NULL,
              message TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS stage_contracts (
              run_id TEXT NOT NULL,
              stage_id TEXT NOT NULL,
              role TEXT,
              provider TEXT NOT NULL,
              path TEXT NOT NULL,
              contract_hash TEXT NOT NULL,
              decision TEXT,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evidence_bundles (
              run_id TEXT NOT NULL,
              stage_id TEXT,
              path TEXT NOT NULL,
              bundle_hash TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evidence_items (
              evidence_id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              stage_id TEXT,
              kind TEXT NOT NULL,
              strength TEXT NOT NULL,
              claim TEXT NOT NULL,
              supports_json TEXT NOT NULL,
              relevance REAL,
              confidence REAL,
              artifact_refs_json TEXT NOT NULL,
              human_summary TEXT,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evidence_scorecards (
              run_id TEXT PRIMARY KEY,
              score INTEGER NOT NULL,
              label TEXT NOT NULL,
              recommendation TEXT NOT NULL,
              components_json TEXT NOT NULL,
              risk_penalty INTEGER NOT NULL,
              missing_evidence_json TEXT,
              next_actions_json TEXT,
              path TEXT NOT NULL,
              scorecard_hash TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS ledger_events (
              run_id TEXT NOT NULL,
              sequence INTEGER NOT NULL,
              event_type TEXT NOT NULL,
              stage_id TEXT,
              prev_hash TEXT,
              event_hash TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY (run_id, sequence)
            );
            CREATE TABLE IF NOT EXISTS snapshots (
              run_id TEXT NOT NULL,
              stage_id TEXT NOT NULL,
              path TEXT NOT NULL,
              patch_hash TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY (run_id, stage_id)
            );
            CREATE TABLE IF NOT EXISTS validator_panels (
              run_id TEXT NOT NULL,
              validator_id TEXT NOT NULL,
              decision TEXT NOT NULL,
              path TEXT NOT NULL,
              validator_hash TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY (run_id, validator_id)
            );
            """
        )
        self._ensure_column("approvals", "subject_hash", "TEXT")
        self._ensure_column("approvals", "subject_json", "TEXT")
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def create_run(
        self,
        *,
        run_id: str,
        task: str,
        workflow: str,
        provider: str,
        workspace: Path,
        worktree: Path,
    ) -> None:
        """Insert the immutable starting record for a run."""
        now = utc_now()
        self.conn.execute(
            """
            INSERT OR REPLACE INTO runs(run_id, task, workflow, provider, status, workspace, worktree, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, task, workflow, provider, RunStatus.CREATED, str(workspace), str(worktree), now, now),
        )
        self.conn.commit()

    def set_run_status(self, run_id: str, status: RunStatus | str) -> None:
        self.conn.execute(
            "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
            (str(status), utc_now(), run_id),
        )
        self.conn.commit()

    def get_run(self, run_id: str) -> dict[str, Any]:
        row = self.conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise FileNotFoundError(f"run not found in blackboard: {run_id}")
        return dict(row)

    def upsert_stage(
        self,
        run_id: str,
        stage_id: str,
        *,
        role: str | None,
        status: StageStatus | str,
        output_path: str | None = None,
        summary: str | None = None,
    ) -> None:
        """Create or update a workflow stage while preserving start time."""
        existing = self.conn.execute(
            "SELECT started_at FROM stages WHERE run_id = ? AND stage_id = ?",
            (run_id, stage_id),
        ).fetchone()
        now = utc_now()
        started_at = existing["started_at"] if existing else (now if str(status) == StageStatus.RUNNING else None)
        completed_at = now if str(status) in {StageStatus.COMPLETED, StageStatus.FAILED, StageStatus.SKIPPED} else None
        self.conn.execute(
            """
            INSERT INTO stages(run_id, stage_id, role, status, started_at, completed_at, output_path, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, stage_id) DO UPDATE SET
              role=excluded.role, status=excluded.status, completed_at=excluded.completed_at,
              output_path=excluded.output_path, summary=excluded.summary
            """,
            (run_id, stage_id, role, str(status), started_at, completed_at, output_path, summary),
        )
        self.conn.commit()

    def add_artifact(self, run_id: str, stage_id: str | None, name: str, path: Path, kind: str) -> None:
        self.conn.execute(
            "INSERT INTO artifacts(run_id, stage_id, name, path, kind, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, stage_id, name, str(path), kind, utc_now()),
        )
        self.conn.commit()

    def upsert_agent(
        self,
        run_id: str,
        role: str,
        provider: str,
        *,
        session_id: str | None = None,
        status: str = "ready",
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO agents(run_id, role, provider, session_id, status)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(run_id, role) DO UPDATE SET
              provider=excluded.provider, session_id=excluded.session_id, status=excluded.status
            """,
            (run_id, role, provider, session_id, status),
        )
        self.conn.commit()

    def create_approval(
        self,
        run_id: str,
        stage_id: str | None,
        approval_type: str,
        reason: str,
        *,
        subject: dict[str, Any] | None = None,
    ) -> str:
        """Record a pending human decision and return its stable id."""
        approval_id = f"appr_{run_id}_{approval_type}_{len(self.list_approvals(run_id=run_id)) + 1}"
        subject_json = json.dumps(subject or {}, ensure_ascii=False, sort_keys=True)
        subject_hash = canonical_hash(subject or {}) if subject else None
        self.conn.execute(
            """
            INSERT INTO approvals(approval_id, run_id, stage_id, type, status, reason, subject_hash, subject_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (approval_id, run_id, stage_id, approval_type, ApprovalStatus.PENDING, reason, subject_hash, subject_json, utc_now()),
        )
        self.conn.commit()
        return approval_id

    def find_approval(self, run_id: str, stage_id: str | None, approval_type: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT * FROM approvals
            WHERE run_id = ? AND stage_id IS ? AND type = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (run_id, stage_id, approval_type),
        ).fetchone()
        return dict(row) if row else None

    def decide_approval(self, approval_id: str, status: ApprovalStatus) -> None:
        self.conn.execute(
            "UPDATE approvals SET status = ?, decided_at = ? WHERE approval_id = ?",
            (str(status), utc_now(), approval_id),
        )
        self.conn.commit()

    def create_provider_action(
        self,
        *,
        run_id: str,
        stage_id: str | None,
        provider: str,
        role: str | None,
        kind: str,
        prompt_text: str,
        options: list[dict[str, Any]] | None = None,
        transcript_path: str | None = None,
        chunks_path: str | None = None,
        attach_command: str | None = None,
        source_event_hash: str | None = None,
    ) -> str:
        """Record a provider-side human action without conflating it with approvals."""
        if source_event_hash:
            existing = self.conn.execute(
                """
                SELECT action_id FROM provider_actions
                WHERE run_id = ? AND stage_id IS ? AND kind = ? AND source_event_hash = ? AND status = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (run_id, stage_id, kind, source_event_hash, str(ProviderActionStatus.PENDING)),
            ).fetchone()
            if existing:
                return str(existing["action_id"])
        action_id = f"pact_{run_id}_{len(self.list_provider_actions(run_id=run_id)) + 1}"
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO provider_actions(
              action_id, run_id, stage_id, provider, role, kind, status, prompt_text,
              options_json, transcript_path, chunks_path, attach_command, source_event_hash,
              created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action_id,
                run_id,
                stage_id,
                provider,
                role,
                kind,
                str(ProviderActionStatus.PENDING),
                redact(prompt_text),
                json.dumps(options or [], ensure_ascii=False),
                transcript_path,
                chunks_path,
                attach_command,
                source_event_hash,
                now,
                now,
            ),
        )
        self.conn.commit()
        return action_id

    def list_provider_actions(self, *, status: str | None = None, run_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM provider_actions"
        filters: list[str] = []
        values: list[str] = []
        if status:
            filters.append("status = ?")
            values.append(status)
        if run_id:
            filters.append("run_id = ?")
            values.append(run_id)
        if filters:
            query += " WHERE " + " AND ".join(filters)
        query += " ORDER BY created_at DESC"
        rows = [dict(row) for row in self.conn.execute(query, values)]
        for row in rows:
            try:
                row["options"] = json.loads(row.get("options_json") or "[]")
            except json.JSONDecodeError:
                row["options"] = []
        return rows

    def update_provider_action_status(self, action_id: str, status: ProviderActionStatus | str) -> None:
        self.conn.execute(
            "UPDATE provider_actions SET status = ?, updated_at = ? WHERE action_id = ?",
            (str(status), utc_now(), action_id),
        )
        self.conn.commit()

    def start_provider_attempt(
        self,
        run_id: str,
        stage_id: str,
        *,
        provider: str,
        role: str | None,
        attempt: int,
    ) -> None:
        """Record the start of one provider execution attempt."""
        self.conn.execute(
            """
            INSERT OR REPLACE INTO provider_attempts(
              run_id, stage_id, provider, role, attempt, status, started_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, stage_id, provider, role, attempt, "running", utc_now()),
        )
        self.conn.commit()

    def complete_provider_attempt(
        self,
        run_id: str,
        stage_id: str,
        *,
        provider: str,
        attempt: int,
        status: str,
        failure_kind: str | None = None,
        returncode: int | None = None,
        summary: str | None = None,
        artifact_path: str | None = None,
        capsule_path: str | None = None,
    ) -> None:
        """Complete a provider attempt with normalized outcome metadata."""
        self.conn.execute(
            """
            UPDATE provider_attempts
            SET status = ?, failure_kind = ?, returncode = ?, summary = ?,
                artifact_path = ?, capsule_path = ?, completed_at = ?
            WHERE run_id = ? AND stage_id = ? AND provider = ? AND attempt = ?
            """,
            (
                status,
                failure_kind,
                returncode,
                redact(summary or ""),
                artifact_path,
                capsule_path,
                utc_now(),
                run_id,
                stage_id,
                provider,
                attempt,
            ),
        )
        self.conn.commit()

    def add_session_capsule(
        self,
        run_id: str,
        stage_id: str,
        *,
        role: str | None,
        provider: str,
        kind: str,
        status: str,
        summary: str,
        path: Path,
        capsule_hash: str,
    ) -> str:
        """Persist a role handoff capsule row and return its id."""
        capsule_id = f"caps_{run_id}_{len(self.table_rows('session_capsules', run_id=run_id)) + 1}"
        self.conn.execute(
            """
            INSERT INTO session_capsules(
              capsule_id, run_id, stage_id, role, provider, kind, status,
              summary, path, capsule_hash, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (capsule_id, run_id, stage_id, role, provider, kind, status, redact(summary), str(path), capsule_hash, utc_now()),
        )
        self.conn.commit()
        return capsule_id

    def add_feedback_event(
        self,
        *,
        run_id: str | None,
        source: str,
        kind: str,
        severity: str,
        status: str,
        route_to: str | None,
        content: str,
        payload: dict[str, Any] | None = None,
    ) -> str:
        feedback_id = f"fb_{len(self.table_rows('feedback_events')) + 1:06d}"
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO feedback_events(
              feedback_id, run_id, source, kind, severity, status, route_to,
              content, payload_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (feedback_id, run_id, source, kind, severity, status, route_to, redact(content), json.dumps(payload or {}, ensure_ascii=False, sort_keys=True), now, now),
        )
        self.conn.commit()
        return feedback_id

    def list_feedback_events(self, *, status: str | None = None, run_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM feedback_events"
        filters: list[str] = []
        values: list[str] = []
        if status:
            filters.append("status = ?")
            values.append(status)
        if run_id:
            filters.append("run_id = ?")
            values.append(run_id)
        if filters:
            query += " WHERE " + " AND ".join(filters)
        query += " ORDER BY created_at DESC"
        rows = [dict(row) for row in self.conn.execute(query, values)]
        for row in rows:
            try:
                row["payload"] = json.loads(row.get("payload_json") or "{}")
            except json.JSONDecodeError:
                row["payload"] = {}
        return rows

    def add_ci_rescue(
        self,
        *,
        feedback_id: str,
        run_id: str | None,
        rescue_run_id: str | None,
        route_to: str,
        status: str,
        summary: str,
    ) -> str:
        rescue_id = f"cires_{len(self.table_rows('ci_rescues')) + 1:06d}"
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO ci_rescues(feedback_id, rescue_id, run_id, rescue_run_id, route_to, status, summary, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (feedback_id, rescue_id, run_id, rescue_run_id, route_to, status, redact(summary), now, now),
        )
        self.conn.commit()
        return rescue_id

    def update_ci_rescue(self, rescue_id: str, *, rescue_run_id: str | None = None, status: str | None = None) -> None:
        row = self.conn.execute("SELECT * FROM ci_rescues WHERE rescue_id = ?", (rescue_id,)).fetchone()
        if row is None:
            return
        self.conn.execute(
            """
            UPDATE ci_rescues
            SET rescue_run_id = ?, status = ?, updated_at = ?
            WHERE rescue_id = ?
            """,
            (rescue_run_id if rescue_run_id is not None else row["rescue_run_id"], status if status is not None else row["status"], utc_now(), rescue_id),
        )
        self.conn.commit()

    def add_cache_entry(
        self,
        *,
        cache_key: str,
        run_id: str | None,
        kind: str,
        path: Path,
        value_hash: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO cache_entries(cache_key, run_id, kind, path, value_hash, metadata_json, created_at, last_accessed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
              run_id=excluded.run_id, kind=excluded.kind, path=excluded.path,
              value_hash=excluded.value_hash, metadata_json=excluded.metadata_json,
              last_accessed_at=excluded.last_accessed_at
            """,
            (cache_key, run_id, kind, str(path), value_hash, json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True), now, now),
        )
        self.conn.commit()

    def upsert_skill_lock(
        self,
        *,
        skill_name: str,
        run_id: str | None,
        skill_version: str | None,
        skill_hash: str,
        path: Path,
        compatible_roles: list[str],
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO skill_locks(skill_name, run_id, skill_version, skill_hash, path, compatible_roles_json, status, metadata_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(skill_name) DO UPDATE SET
              run_id=excluded.run_id, skill_version=excluded.skill_version,
              skill_hash=excluded.skill_hash, path=excluded.path,
              compatible_roles_json=excluded.compatible_roles_json,
              status=excluded.status, metadata_json=excluded.metadata_json,
              updated_at=excluded.updated_at
            """,
            (skill_name, run_id, skill_version, skill_hash, str(path), json.dumps(compatible_roles, ensure_ascii=False), status, json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True), utc_now()),
        )
        self.conn.commit()

    def upsert_plugin_manifest(
        self,
        *,
        plugin_name: str,
        run_id: str | None,
        source: str,
        manifest_path: Path | None,
        manifest_hash: str,
        trust: str,
        permissions: list[str],
        status: str,
        warnings: list[str],
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO plugin_manifests(plugin_name, run_id, source, manifest_path, manifest_hash, trust, permissions_json, status, warnings_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(plugin_name) DO UPDATE SET
              run_id=excluded.run_id, source=excluded.source,
              manifest_path=excluded.manifest_path, manifest_hash=excluded.manifest_hash,
              trust=excluded.trust, permissions_json=excluded.permissions_json,
              status=excluded.status, warnings_json=excluded.warnings_json,
              updated_at=excluded.updated_at
            """,
            (plugin_name, run_id, source, str(manifest_path) if manifest_path else None, manifest_hash, trust, json.dumps(permissions, ensure_ascii=False), status, json.dumps(warnings, ensure_ascii=False), utc_now()),
        )
        self.conn.commit()

    def add_guardrail_event(
        self,
        *,
        run_id: str | None,
        tool: str,
        decision: str,
        reason: str,
        payload: dict[str, Any] | None = None,
    ) -> str:
        event_id = f"guard_{len(self.table_rows('guardrail_events')) + 1:06d}"
        self.conn.execute(
            """
            INSERT INTO guardrail_events(event_id, run_id, tool, decision, reason, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (event_id, run_id, tool, decision, redact(reason), json.dumps(payload or {}, ensure_ascii=False, sort_keys=True), utc_now()),
        )
        self.conn.commit()
        return event_id

    def add_parallel_conflict(
        self,
        *,
        run_id: str | None,
        stage_id: str | None,
        stages: list[str],
        files: list[str],
        severity: str,
        status: str = "open",
        resolution: str | None = None,
    ) -> str:
        conflict_id = f"pcf_{len(self.table_rows('parallel_conflicts')) + 1:06d}"
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO parallel_conflicts(
              conflict_id, run_id, stage_id, stages_json, files_json,
              severity, status, resolution, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conflict_id,
                run_id,
                stage_id,
                json.dumps(stages, ensure_ascii=False),
                json.dumps(files, ensure_ascii=False),
                severity,
                status,
                resolution,
                now,
                now,
            ),
        )
        self.conn.commit()
        return conflict_id

    def list_parallel_conflicts(self, *, status: str | None = None, run_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM parallel_conflicts"
        filters: list[str] = []
        values: list[str] = []
        if status:
            filters.append("status = ?")
            values.append(status)
        if run_id:
            filters.append("run_id = ?")
            values.append(run_id)
        if filters:
            query += " WHERE " + " AND ".join(filters)
        query += " ORDER BY created_at DESC"
        rows = [dict(row) for row in self.conn.execute(query, values)]
        for row in rows:
            row["stages"] = _json_list(row.get("stages_json"))
            row["files"] = _json_list(row.get("files_json"))
        return rows

    def add_semantic_merge_review(
        self,
        *,
        run_id: str,
        decision: str,
        patch_hash: str,
        findings: list[dict[str, Any]],
        path: Path,
    ) -> str:
        review_id = f"smr_{run_id}_{len(self.table_rows('semantic_merge_reviews', run_id=run_id)) + 1}"
        self.conn.execute(
            """
            INSERT INTO semantic_merge_reviews(review_id, run_id, decision, patch_hash, findings_json, path, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (review_id, run_id, decision, patch_hash, json.dumps(findings, ensure_ascii=False, sort_keys=True), str(path), utc_now()),
        )
        self.conn.commit()
        return review_id

    def list_semantic_merge_reviews(self, *, run_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM semantic_merge_reviews"
        values: list[str] = []
        if run_id:
            query += " WHERE run_id = ?"
            values.append(run_id)
        query += " ORDER BY created_at DESC"
        rows = [dict(row) for row in self.conn.execute(query, values)]
        for row in rows:
            try:
                row["findings"] = json.loads(row.get("findings_json") or "[]")
            except json.JSONDecodeError:
                row["findings"] = []
        return rows

    def upsert_provider_learning(
        self,
        *,
        provider: str,
        role: str | None,
        run_id: str | None,
        attempts: int,
        successes: int,
        failures: int,
        human_actions: int,
        score: float,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO provider_learning(
              provider, role, run_id, attempts, successes, failures,
              human_actions, score, metadata_json, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, role) DO UPDATE SET
              run_id=excluded.run_id, attempts=excluded.attempts,
              successes=excluded.successes, failures=excluded.failures,
              human_actions=excluded.human_actions, score=excluded.score,
              metadata_json=excluded.metadata_json, updated_at=excluded.updated_at
            """,
            (
                provider,
                role or "any",
                run_id,
                attempts,
                successes,
                failures,
                human_actions,
                score,
                json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                utc_now(),
            ),
        )
        self.conn.commit()

    def list_provider_learning(self, *, role: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM provider_learning"
        values: list[str] = []
        if role:
            query += " WHERE role = ?"
            values.append(role)
        query += " ORDER BY score DESC, attempts DESC"
        rows = [dict(row) for row in self.conn.execute(query, values)]
        for row in rows:
            try:
                row["metadata"] = json.loads(row.get("metadata_json") or "{}")
            except json.JSONDecodeError:
                row["metadata"] = {}
        return rows

    def add_multi_repo_orchestration(
        self,
        *,
        orchestration_id: str,
        run_id: str | None,
        workspace: Path,
        mode: str,
        task: str,
        status: str,
        repos: list[dict[str, Any]],
        plan_path: Path | None = None,
    ) -> None:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO multi_repo_orchestrations(
              orchestration_id, run_id, workspace, mode, task, status,
              repos_json, plan_path, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(orchestration_id) DO UPDATE SET
              run_id=excluded.run_id, workspace=excluded.workspace,
              mode=excluded.mode, task=excluded.task, status=excluded.status,
              repos_json=excluded.repos_json, plan_path=excluded.plan_path,
              updated_at=excluded.updated_at
            """,
            (
                orchestration_id,
                run_id,
                str(workspace),
                mode,
                redact(task),
                status,
                json.dumps(repos, ensure_ascii=False, sort_keys=True),
                str(plan_path) if plan_path else None,
                now,
                now,
            ),
        )
        self.conn.commit()

    def list_multi_repo_orchestrations(self, *, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM multi_repo_orchestrations"
        values: list[str] = []
        if status:
            query += " WHERE status = ?"
            values.append(status)
        query += " ORDER BY updated_at DESC"
        rows = [dict(row) for row in self.conn.execute(query, values)]
        for row in rows:
            try:
                row["repos"] = json.loads(row.get("repos_json") or "[]")
            except json.JSONDecodeError:
                row["repos"] = []
        return rows

    def add_stage_contract(
        self,
        run_id: str,
        stage_id: str,
        *,
        role: str | None,
        provider: str,
        path: Path,
        contract_hash: str,
        decision: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO stage_contracts(run_id, stage_id, role, provider, path, contract_hash, decision, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, stage_id, role, provider, str(path), contract_hash, decision, utc_now()),
        )
        self.conn.commit()

    def add_evidence_bundle(self, run_id: str, stage_id: str | None, *, path: Path, bundle_hash: str) -> None:
        self.conn.execute(
            "INSERT INTO evidence_bundles(run_id, stage_id, path, bundle_hash, created_at) VALUES (?, ?, ?, ?, ?)",
            (run_id, stage_id, str(path), bundle_hash, utc_now()),
        )
        self.conn.commit()

    def upsert_evidence_item(
        self,
        *,
        run_id: str,
        evidence_id: str,
        stage_id: str | None,
        kind: str,
        strength: str,
        claim: str,
        supports: list[str],
        relevance: float | None,
        confidence: float | None,
        artifact_refs: list[dict[str, Any]],
        human_summary: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO evidence_items(
              evidence_id, run_id, stage_id, kind, strength, claim,
              supports_json, relevance, confidence, artifact_refs_json,
              human_summary, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(evidence_id) DO UPDATE SET
              run_id=excluded.run_id, stage_id=excluded.stage_id, kind=excluded.kind,
              strength=excluded.strength, claim=excluded.claim,
              supports_json=excluded.supports_json, relevance=excluded.relevance,
              confidence=excluded.confidence, artifact_refs_json=excluded.artifact_refs_json,
              human_summary=excluded.human_summary, created_at=excluded.created_at
            """,
            (
                evidence_id,
                run_id,
                stage_id,
                kind,
                strength,
                redact(claim),
                json.dumps(supports, ensure_ascii=False),
                relevance,
                confidence,
                json.dumps(artifact_refs, ensure_ascii=False, sort_keys=True),
                redact(human_summary),
                utc_now(),
            ),
        )
        self.conn.commit()

    def upsert_evidence_scorecard(
        self,
        *,
        run_id: str,
        score: int,
        label: str,
        recommendation: str,
        components: dict[str, int],
        risk_penalty: int,
        missing_evidence: list[str],
        next_actions: list[dict[str, Any]],
        path: Path,
        scorecard_hash: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO evidence_scorecards(
              run_id, score, label, recommendation, components_json,
              risk_penalty, missing_evidence_json, next_actions_json,
              path, scorecard_hash, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
              score=excluded.score, label=excluded.label, recommendation=excluded.recommendation,
              components_json=excluded.components_json, risk_penalty=excluded.risk_penalty,
              missing_evidence_json=excluded.missing_evidence_json,
              next_actions_json=excluded.next_actions_json, path=excluded.path,
              scorecard_hash=excluded.scorecard_hash, created_at=excluded.created_at
            """,
            (
                run_id,
                int(score),
                label,
                recommendation,
                json.dumps(components, ensure_ascii=False, sort_keys=True),
                int(risk_penalty),
                json.dumps(missing_evidence, ensure_ascii=False),
                json.dumps(next_actions, ensure_ascii=False, sort_keys=True),
                str(path),
                scorecard_hash,
                utc_now(),
            ),
        )
        self.conn.commit()

    def add_ledger_event(self, event: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO ledger_events(run_id, sequence, event_type, stage_id, prev_hash, event_hash, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["run_id"],
                int(event["sequence"]),
                event["event_type"],
                event.get("stage_id"),
                event.get("prev_hash"),
                event["event_hash"],
                json.dumps(event.get("payload", {}), ensure_ascii=False, sort_keys=True),
                event["created_at"],
            ),
        )
        self.conn.commit()

    def add_snapshot(self, run_id: str, stage_id: str, *, path: Path, patch_hash: str) -> None:
        self.conn.execute(
            """
            INSERT INTO snapshots(run_id, stage_id, path, patch_hash, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(run_id, stage_id) DO UPDATE SET
              path=excluded.path, patch_hash=excluded.patch_hash, created_at=excluded.created_at
            """,
            (run_id, stage_id, str(path), patch_hash, utc_now()),
        )
        self.conn.commit()

    def add_validator_panel(
        self,
        run_id: str,
        *,
        validator_id: str,
        decision: str,
        path: Path,
        validator_hash: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO validator_panels(run_id, validator_id, decision, path, validator_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id, validator_id, decision, str(path), validator_hash, utc_now()),
        )
        self.conn.commit()

    def list_approvals(self, *, status: str | None = None, run_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM approvals"
        filters: list[str] = []
        values: list[str] = []
        if status:
            filters.append("status = ?")
            values.append(status)
        if run_id:
            filters.append("run_id = ?")
            values.append(run_id)
        if filters:
            query += " WHERE " + " AND ".join(filters)
        query += " ORDER BY created_at DESC"
        return [dict(row) for row in self.conn.execute(query, values)]

    def list_runs(self, *, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM runs"
        values: list[str] = []
        if status:
            query += " WHERE status = ?"
            values.append(status)
        query += " ORDER BY updated_at DESC, created_at DESC"
        return [dict(row) for row in self.conn.execute(query, values)]

    def add_checkpoint(self, run_id: str, stage_id: str | None, kind: str) -> None:
        self.conn.execute(
            "INSERT INTO checkpoints(run_id, stage_id, kind, created_at) VALUES (?, ?, ?, ?)",
            (run_id, stage_id, kind, utc_now()),
        )
        self.conn.commit()

    def add_error(self, run_id: str, stage_id: str | None, type: str, message: str) -> None:
        self.conn.execute(
            "INSERT INTO error_details(run_id, stage_id, type, message, created_at) VALUES (?, ?, ?, ?, ?)",
            (run_id, stage_id, type, redact(message), utc_now()),
        )
        self.conn.commit()

    def reset_stage(self, run_id: str, stage_id: str) -> None:
        self.conn.execute(
            """
            UPDATE stages
            SET status = ?, completed_at = NULL, output_path = NULL, summary = ?
            WHERE run_id = ? AND stage_id = ?
            """,
            (StageStatus.PENDING, "retry requested", run_id, stage_id),
        )
        self.conn.commit()

    def skip_stage(self, run_id: str, stage_id: str, reason: str = "skip requested") -> None:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO stages(run_id, stage_id, status, completed_at, summary)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(run_id, stage_id) DO UPDATE SET
              status=excluded.status, completed_at=excluded.completed_at, summary=excluded.summary
            """,
            (run_id, stage_id, StageStatus.SKIPPED, now, reason),
        )
        self.conn.commit()

    def add_usage(self, run_id: str, provider: str, tokens: int, cost_usd: float) -> None:
        self.conn.execute(
            "INSERT INTO usage_records(run_id, provider, tokens, cost_usd, created_at) VALUES (?, ?, ?, ?, ?)",
            (run_id, provider, tokens, cost_usd, utc_now()),
        )
        self.conn.commit()

    def usage_total_cost(self, run_id: str) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM usage_records WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return float(row["total"])

    def add_test_result(self, run_id: str, stage_id: str, passed: bool, command: str, summary: str) -> None:
        self.conn.execute(
            "INSERT INTO test_results(run_id, stage_id, passed, command, summary) VALUES (?, ?, ?, ?, ?)",
            (run_id, stage_id, int(passed), command, summary),
        )
        self.conn.commit()

    def add_review_blocker(
        self,
        run_id: str,
        stage_id: str,
        *,
        type: str,
        file: str | None,
        line: int | None,
        severity: str,
        suggestion: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO review_blockers(run_id, stage_id, type, file, line, severity, suggestion)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, stage_id, type, file, line, severity, suggestion),
        )
        self.conn.commit()

    def table_rows(self, table: str, *, run_id: str | None = None) -> list[dict[str, Any]]:
        if table not in self.RUN_FILTERED_TABLES:
            raise ValueError(f"unknown blackboard table: {table}")
        if run_id and table in self.RUN_FILTERED_TABLES:
            rows = [dict(row) for row in self.conn.execute(f"SELECT * FROM {table} WHERE run_id = ?", (run_id,))]
        else:
            rows = [dict(row) for row in self.conn.execute(f"SELECT * FROM {table}")]
        if table == "evidence_items":
            for row in rows:
                row["supports"] = _json_list(row.get("supports_json"))
                row["artifact_refs"] = _json_list(row.get("artifact_refs_json"))
        if table == "evidence_scorecards":
            for row in rows:
                row["components"] = _json_dict(row.get("components_json"))
                row["missing_evidence"] = _json_list(row.get("missing_evidence_json"))
                row["next_actions"] = _json_list(row.get("next_actions_json"))
        return rows


def _json_list(value: object) -> list[object]:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _json_dict(value: object) -> dict[str, object]:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class TraceWriter:
    def __init__(self, run_dir: Path, run_id: str):
        self.path = run_dir / "trace.jsonl"
        self.run_id = run_id

    def write(self, event_type: str, *, stage: str | None = None, **data: Any) -> None:
        event = TraceEvent(type=event_type, run_id=self.run_id, stage=stage, data=data)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(redact(json.dumps(event.model_dump(), ensure_ascii=False)) + "\n")
