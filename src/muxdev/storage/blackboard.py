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
from ..models import ApprovalStatus, RunStatus, StageStatus, TraceEvent, utc_now
from ..core.redaction import redact


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
        "artifacts",
        "review_blockers",
        "test_results",
        "usage_records",
        "checkpoints",
        "error_details",
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
              created_at TEXT NOT NULL,
              decided_at TEXT
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
            """
        )
        self.conn.commit()

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

    def create_approval(self, run_id: str, stage_id: str | None, approval_type: str, reason: str) -> str:
        """Record a pending human decision and return its stable id."""
        approval_id = f"appr_{run_id}_{approval_type}_{len(self.list_approvals(run_id=run_id)) + 1}"
        self.conn.execute(
            """
            INSERT INTO approvals(approval_id, run_id, stage_id, type, status, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (approval_id, run_id, stage_id, approval_type, ApprovalStatus.PENDING, reason, utc_now()),
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
            return [dict(row) for row in self.conn.execute(f"SELECT * FROM {table} WHERE run_id = ?", (run_id,))]
        return [dict(row) for row in self.conn.execute(f"SELECT * FROM {table}")]


class TraceWriter:
    def __init__(self, run_dir: Path, run_id: str):
        self.path = run_dir / "trace.jsonl"
        self.run_id = run_id

    def write(self, event_type: str, *, stage: str | None = None, **data: Any) -> None:
        event = TraceEvent(type=event_type, run_id=self.run_id, stage=stage, data=data)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(redact(json.dumps(event.model_dump(), ensure_ascii=False)) + "\n")
