"""Persistent run store and SQLite blackboard.

The blackboard is muxdev's local source of truth for run status, stages, agents,
approvals, artifacts, review blockers, usage, checkpoints, and errors. Runtime,
CLI, TUI, reports, and resume/retry/skip operations all read from the same file
so completed and interrupted runs remain auditable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4

from ..config.loader import path_config
from ..domain.state_events import (
    APPROVAL_DECIDED,
    APPROVAL_REQUESTED,
    LEGACY_RUN_IMPORTED,
    PROVIDER_ACTION_REQUESTED,
    PROVIDER_ACTION_RESPONDED,
    PROVIDER_ACTION_TRANSITIONED,
    RUN_CREATED,
    RUN_TRANSITIONED,
    STAGE_TRANSITIONED,
    WORKER_FAILED,
    StateEventEnvelope,
    initial_run_state,
    reduce_run_state,
    verify_event_hash,
)
from ..domain.routing import ReviewAssignment, RouteCandidate, RouteDecision, TaskFeatureSet, verify_route_decision_hash
from ..models import ApprovalStatus, ProviderActionKind, ProviderActionStatus, RunStatus, StageStatus, TraceEvent, utc_now
from ..core.redaction import redact
from ..providers.harness import CertificationReport, HarnessEvent, HarnessEventSource
from .contracts import canonical_hash
from .sqlite import Migration, SQLiteEngine, UnitOfWork, add_missing_columns, apply_migrations, execute_script


class RunStore:
    """Resolve and create run directories under the configured runtime path."""

    def __init__(self, root: Path, runs_dir: Path | None = None):
        self.root = Path(root).expanduser().resolve()
        self.runs_dir = Path(runs_dir).expanduser().resolve() if runs_dir is not None else path_config(self.root, "runs").resolve()
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def create_run_dir(self, run_id: str) -> Path:
        run_dir = self._run_path(run_id)
        (run_dir / "session").mkdir(parents=True, exist_ok=True)
        return run_dir

    def find_run_dir(self, run_id: str) -> Path:
        run_dir = self._run_path(run_id)
        if not run_dir.exists():
            raise FileNotFoundError(f"run not found: {run_id}")
        return run_dir

    def _run_path(self, run_id: str) -> Path:
        value = str(run_id).strip()
        if not value or Path(value).name != value or "/" in value or "\\" in value:
            raise ValueError(f"invalid run id: {run_id!r}")
        run_dir = (self.runs_dir / value).resolve()
        if run_dir.parent != self.runs_dir:
            raise ValueError(f"run id escapes run store: {run_id!r}")
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
        "evidence_events",
        "evidence_manifests",
        "evidence_evaluations",
        "ledger_events",
        "snapshots",
        "validator_panels",
        "state_events",
        "run_specs",
        "execution_jobs",
        "execution_events",
        "harness_events",
        "task_feature_sets",
        "route_decisions",
        "routing_outcomes",
        "review_assignments",
        "delivery_attestations",
        "attestation_exports",
    }

    def __init__(
        self,
        run_dir: Path,
        db_path: Path | None = None,
        *,
        readonly: bool = False,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
        execution_guard: Any | None = None,
    ):
        self.run_dir = Path(run_dir).expanduser().resolve()
        self.db_path = Path(db_path or self.run_dir / "blackboard.sqlite").expanduser().resolve()
        self._event_sink = event_sink
        self.execution_guard = execution_guard
        self.engine = SQLiteEngine(self.db_path, component="blackboard", readonly=readonly)
        self.conn = self.engine.connection
        try:
            apply_migrations(
                self.engine,
                (
                    Migration(1, "blackboard_baseline", "blackboard-v1-20260718", self._create_v1_schema),
                    Migration(2, "operational_state_events", "blackboard-v2-state-events-attempt-history", self._create_v2_schema),
                    Migration(3, "durable_execution_queue", "blackboard-v3-durable-runtime-20260719", self._create_v3_schema),
                    Migration(4, "certified_agent_harness", "blackboard-v4-certified-harness-20260719-r2", self._create_v4_schema),
                    Migration(5, "quality_routing_and_review", "blackboard-v5-routing-review-20260719-r1", self._create_v5_schema),
                    Migration(6, "signed_delivery_attestation", "blackboard-v6-signed-attestation-20260719-r1", self._create_v6_schema),
                    Migration(7, "trusted_routing_benchmark", "blackboard-v7-trusted-benchmark-20260719-r1", self._create_v7_schema),
                ),
                backup_root=self.db_path.parent / "backups" / "migrations",
            )
        except BaseException:
            self.engine.close()
            raise
        if execution_guard is not None:
            self.engine.commit_validator = self._validate_execution_guard

    def _validate_execution_guard(self) -> None:
        """Fence every Runtime commit against the currently owned execution lease."""
        self.execution_guard.check()
        from .executions import DurableExecutionQueue

        DurableExecutionQueue(self).assert_lease(self.execution_guard.lease)

    def close(self) -> None:
        self.engine.close()

    def __enter__(self) -> "Blackboard":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def _create_v1_schema(self, conn: Any) -> None:
        """Create every table needed by M0-M7 runtime features."""
        execute_script(
            conn,
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
              input_kind TEXT,
              choices_json TEXT,
              default_choice TEXT,
              timeout_seconds INTEGER,
              response_json TEXT,
              auto_policy TEXT,
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
            CREATE TABLE IF NOT EXISTS evidence_events (
              event_id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              stage_id TEXT,
              layer TEXT NOT NULL,
              kind TEXT NOT NULL,
              claim TEXT NOT NULL,
              status TEXT NOT NULL,
              strength TEXT NOT NULL,
              subject_hash TEXT,
              prev_hash TEXT,
              event_hash TEXT NOT NULL,
              artifact_refs_json TEXT NOT NULL,
              metrics_json TEXT NOT NULL,
              tags_json TEXT NOT NULL,
              source TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evidence_manifests (
              run_id TEXT PRIMARY KEY,
              path TEXT NOT NULL,
              manifest_hash TEXT NOT NULL,
              head_hash TEXT,
              event_count INTEGER NOT NULL,
              artifact_count INTEGER NOT NULL,
              required_matrix_json TEXT NOT NULL,
              missing_required_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evidence_evaluations (
              run_id TEXT PRIMARY KEY,
              label TEXT NOT NULL,
              confidence REAL NOT NULL,
              gates_json TEXT NOT NULL,
              components_json TEXT NOT NULL,
              reasons_json TEXT NOT NULL,
              missing_evidence_json TEXT NOT NULL,
              next_actions_json TEXT NOT NULL,
              path TEXT NOT NULL,
              evaluation_hash TEXT NOT NULL,
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
        add_missing_columns(conn, "approvals", {"subject_hash": "TEXT", "subject_json": "TEXT"})
        add_missing_columns(
            conn,
            "provider_actions",
            {
                "input_kind": "TEXT",
                "choices_json": "TEXT",
                "default_choice": "TEXT",
                "timeout_seconds": "INTEGER",
                "response_json": "TEXT",
                "auto_policy": "TEXT",
            },
        )

    def _create_v2_schema(self, conn: Any) -> None:
        add_missing_columns(conn, "runs", {"history_complete": "INTEGER NOT NULL DEFAULT 0"})
        add_missing_columns(conn, "stages", {"attempt": "INTEGER NOT NULL DEFAULT 0"})
        execute_script(
            conn,
            """
            CREATE TABLE IF NOT EXISTS state_events (
              event_id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              sequence INTEGER NOT NULL,
              event_type TEXT NOT NULL,
              event_version INTEGER NOT NULL,
              idempotency_key TEXT NOT NULL,
              causation_id TEXT,
              correlation_id TEXT,
              payload_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              prev_hash TEXT,
              event_hash TEXT NOT NULL,
              UNIQUE(run_id, sequence),
              UNIQUE(run_id, idempotency_key)
            );
            CREATE TABLE IF NOT EXISTS aggregate_versions (
              run_id TEXT PRIMARY KEY,
              version INTEGER NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_state_events_run_created
              ON state_events(run_id, created_at);
            """,
        )

    def _create_v3_schema(self, conn: Any) -> None:
        """Add the durable scheduler without mixing it into Run state events."""
        execute_script(
            conn,
            """
            CREATE TABLE IF NOT EXISTS run_specs (
              run_id TEXT PRIMARY KEY,
              schema_version INTEGER NOT NULL,
              payload_json TEXT NOT NULL,
              payload_hash TEXT NOT NULL,
              legacy INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS execution_jobs (
              job_id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              command TEXT NOT NULL,
              state TEXT NOT NULL,
              idempotency_key TEXT NOT NULL,
              request_json TEXT NOT NULL DEFAULT '{}',
              attempt INTEGER NOT NULL DEFAULT 0,
              max_attempts INTEGER NOT NULL DEFAULT 3,
              available_at_ms INTEGER NOT NULL,
              lease_owner TEXT,
              lease_token TEXT,
              fencing_token INTEGER NOT NULL DEFAULT 0,
              lease_expires_at_ms INTEGER,
              heartbeat_at_ms INTEGER,
              cancel_requested_at_ms INTEGER,
              cancel_reason TEXT,
              last_error TEXT,
              outcome_status TEXT,
              created_at_ms INTEGER NOT NULL,
              updated_at_ms INTEGER NOT NULL,
              completed_at_ms INTEGER,
              UNIQUE(run_id, idempotency_key),
              FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_execution_jobs_claim
              ON execution_jobs(state, available_at_ms, created_at_ms);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_execution_jobs_one_active_run
              ON execution_jobs(run_id)
              WHERE state IN ('queued', 'leased', 'running', 'retry_wait', 'cancel_requested');
            CREATE TABLE IF NOT EXISTS execution_events (
              event_id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              job_id TEXT,
              sequence INTEGER NOT NULL,
              event_type TEXT NOT NULL,
              event_version INTEGER NOT NULL,
              idempotency_key TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              created_at_ms INTEGER NOT NULL,
              UNIQUE(run_id, sequence),
              UNIQUE(run_id, idempotency_key),
              FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
              FOREIGN KEY(job_id) REFERENCES execution_jobs(job_id) ON DELETE SET NULL
            );
            CREATE INDEX IF NOT EXISTS idx_execution_events_run
              ON execution_events(run_id, sequence);
            CREATE TABLE IF NOT EXISTS daemon_instances (
              instance_id TEXT PRIMARY KEY,
              pid INTEGER NOT NULL,
              status TEXT NOT NULL,
              worker_count INTEGER NOT NULL,
              started_at_ms INTEGER NOT NULL,
              heartbeat_at_ms INTEGER NOT NULL,
              stopped_at_ms INTEGER
            );
            """,
        )

    def _create_v4_schema(self, conn: Any) -> None:
        """Persist certifications and tamper-evident Provider Attempt facts."""
        execute_script(
            conn,
            """
            CREATE TABLE IF NOT EXISTS adapter_certifications (
              certification_id TEXT PRIMARY KEY,
              provider TEXT NOT NULL,
              status TEXT NOT NULL,
              mode TEXT NOT NULL,
              adapter_version TEXT NOT NULL,
              provider_version TEXT,
              fingerprint TEXT NOT NULL,
              policy_version TEXT NOT NULL,
              platform TEXT NOT NULL,
              capabilities_json TEXT NOT NULL,
              trust_tier TEXT NOT NULL,
              evidence_json TEXT NOT NULL,
              failure TEXT,
              issued_at TEXT NOT NULL,
              expires_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_adapter_certifications_provider_issued
              ON adapter_certifications(provider, issued_at DESC);
            CREATE TABLE IF NOT EXISTS harness_events (
              event_id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              stage_id TEXT NOT NULL,
              provider TEXT NOT NULL,
              attempt INTEGER NOT NULL,
              sequence INTEGER NOT NULL,
              event_type TEXT NOT NULL,
              event_version INTEGER NOT NULL,
              source TEXT NOT NULL,
              idempotency_key TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              prev_hash TEXT,
              event_hash TEXT NOT NULL,
              UNIQUE(run_id, stage_id, provider, attempt, sequence),
              UNIQUE(run_id, idempotency_key),
              FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_harness_events_attempt
              ON harness_events(run_id, stage_id, provider, attempt, sequence);
            """,
        )
        add_missing_columns(
            conn,
            "provider_attempts",
            {
                "certification_id": "TEXT",
                "adapter_version": "TEXT",
                "trust_tier": "TEXT NOT NULL DEFAULT 'opaque'",
                "isolation_mode": "TEXT NOT NULL DEFAULT 'process'",
                "session_id": "TEXT",
                "waiver_approval_id": "TEXT",
                "cancellation_mode": "TEXT",
            },
        )

    def _create_v5_schema(self, conn: Any) -> None:
        """Persist immutable task routing and heterogeneous review facts."""
        execute_script(
            conn,
            """
            CREATE TABLE IF NOT EXISTS task_feature_sets (
              feature_set_id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              contract_version TEXT NOT NULL,
              extractor_version TEXT NOT NULL,
              source_hash TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE(run_id, source_hash),
              FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_task_feature_sets_run
              ON task_feature_sets(run_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS route_decisions (
              decision_id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              decision_kind TEXT NOT NULL,
              decision_version INTEGER NOT NULL,
              idempotency_key TEXT NOT NULL,
              feature_set_id TEXT NOT NULL,
              feature_hash TEXT NOT NULL,
              candidates_json TEXT NOT NULL,
              selected_main_provider TEXT,
              selected_reviewer_provider TEXT,
              benchmark_snapshot_id TEXT,
              benchmark_snapshot_hash TEXT,
              policy_version TEXT NOT NULL,
              scorer_version TEXT NOT NULL,
              reason_codes_json TEXT NOT NULL,
              routing_policy_json TEXT NOT NULL,
              supersedes_decision_id TEXT,
              created_at TEXT NOT NULL,
              prev_hash TEXT,
              decision_hash TEXT NOT NULL,
              UNIQUE(run_id, idempotency_key),
              FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
              FOREIGN KEY(feature_set_id) REFERENCES task_feature_sets(feature_set_id)
            );
            CREATE INDEX IF NOT EXISTS idx_route_decisions_run
              ON route_decisions(run_id, created_at DESC);
            CREATE TABLE IF NOT EXISTS routing_outcomes (
              outcome_id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              decision_id TEXT,
              provider TEXT NOT NULL,
              task_type TEXT NOT NULL,
              verified_success INTEGER NOT NULL,
              quality_score REAL NOT NULL,
              evidence_complete INTEGER NOT NULL,
              safety_violation INTEGER NOT NULL DEFAULT 0,
              cost_usd REAL NOT NULL DEFAULT 0,
              latency_seconds REAL NOT NULL DEFAULT 0,
              source TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE(run_id, provider, source),
              FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
              FOREIGN KEY(decision_id) REFERENCES route_decisions(decision_id)
            );
            CREATE INDEX IF NOT EXISTS idx_routing_outcomes_provider_task
              ON routing_outcomes(provider, task_type, evidence_complete);
            CREATE TABLE IF NOT EXISTS review_assignments (
              review_id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              route_decision_id TEXT NOT NULL,
              reviewer_provider TEXT,
              main_provider TEXT NOT NULL,
              status TEXT NOT NULL,
              snapshot_hash TEXT,
              attempt INTEGER NOT NULL,
              waiver_approval_id TEXT,
              verdict TEXT,
              findings_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(run_id, route_decision_id, attempt),
              FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
              FOREIGN KEY(route_decision_id) REFERENCES route_decisions(decision_id)
            );
            CREATE INDEX IF NOT EXISTS idx_review_assignments_run
              ON review_assignments(run_id, updated_at DESC);
            CREATE TABLE IF NOT EXISTS benchmark_snapshots (
              snapshot_id TEXT PRIMARY KEY,
              contract_version TEXT NOT NULL,
              suite_name TEXT NOT NULL,
              case_count INTEGER NOT NULL,
              payload_json TEXT NOT NULL,
              snapshot_hash TEXT NOT NULL UNIQUE,
              active INTEGER NOT NULL DEFAULT 1,
              registered_at TEXT NOT NULL
            );
            """,
        )

    def _create_v6_schema(self, conn: Any) -> None:
        """Persist immutable signed delivery records and controlled exports."""
        execute_script(
            conn,
            """
            CREATE TABLE IF NOT EXISTS delivery_attestations (
              attestation_id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              generation INTEGER NOT NULL,
              status TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              payload_hash TEXT NOT NULL,
              signature_json TEXT,
              key_id TEXT,
              public_key_fingerprint TEXT,
              evidence_valid INTEGER NOT NULL DEFAULT 0,
              identity_status TEXT NOT NULL,
              previous_attestation_hash TEXT,
              error TEXT,
              is_current INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL,
              UNIQUE(run_id, generation),
              FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_delivery_attestations_current
              ON delivery_attestations(run_id) WHERE is_current = 1;
            CREATE TABLE IF NOT EXISTS project_signing_key_observations (
              observation_id TEXT PRIMARY KEY,
              project_id TEXT NOT NULL,
              key_id TEXT,
              public_key_fingerprint TEXT,
              permission_status TEXT NOT NULL,
              status TEXT NOT NULL,
              observed_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_signing_observations_project
              ON project_signing_key_observations(project_id, observed_at DESC);
            CREATE TABLE IF NOT EXISTS attestation_exports (
              export_id TEXT PRIMARY KEY,
              run_id TEXT NOT NULL,
              attestation_id TEXT NOT NULL,
              bundle_path TEXT NOT NULL,
              bundle_sha256 TEXT NOT NULL,
              size_bytes INTEGER NOT NULL,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              verified_at TEXT,
              UNIQUE(run_id, attestation_id, bundle_sha256),
              FOREIGN KEY(run_id) REFERENCES runs(run_id) ON DELETE CASCADE,
              FOREIGN KEY(attestation_id) REFERENCES delivery_attestations(attestation_id)
            );
            """,
        )
        add_missing_columns(
            conn,
            "approvals",
            {
                "operator_source": "TEXT",
                "decision_reason": "TEXT",
                "subject_version": "TEXT NOT NULL DEFAULT 'muxdev.approval_subject.v1'",
            },
        )

    def _create_v7_schema(self, conn: Any) -> None:
        """Persist benchmark coordination without mixing it with Run facts."""
        execute_script(
            conn,
            """
            CREATE TABLE IF NOT EXISTS benchmark_executions (
              execution_id TEXT PRIMARY KEY,
              suite_id TEXT NOT NULL,
              suite_hash TEXT NOT NULL,
              mode TEXT NOT NULL,
              status TEXT NOT NULL,
              max_cost_usd REAL,
              observed_cost_usd REAL NOT NULL DEFAULT 0,
              provider_concurrency INTEGER NOT NULL DEFAULT 1,
              plan_json TEXT NOT NULL,
              error TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_benchmark_one_live
              ON benchmark_executions(mode) WHERE mode='live' AND status IN ('planned','queued','running','paused_budget','awaiting_reconciliation');
            CREATE TABLE IF NOT EXISTS benchmark_events (
              event_id TEXT PRIMARY KEY,
              execution_id TEXT NOT NULL,
              sequence INTEGER NOT NULL,
              event_type TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              prev_hash TEXT,
              event_hash TEXT NOT NULL,
              UNIQUE(execution_id, sequence),
              FOREIGN KEY(execution_id) REFERENCES benchmark_executions(execution_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS benchmark_case_results (
              result_id TEXT PRIMARY KEY,
              execution_id TEXT NOT NULL,
              case_id TEXT NOT NULL,
              provider TEXT NOT NULL,
              run_id TEXT,
              status TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              result_hash TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE(execution_id, case_id, provider),
              FOREIGN KEY(execution_id) REFERENCES benchmark_executions(execution_id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS benchmark_reports (
              report_id TEXT PRIMARY KEY,
              execution_id TEXT NOT NULL UNIQUE,
              status TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              report_hash TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(execution_id) REFERENCES benchmark_executions(execution_id) ON DELETE CASCADE
            );
            """,
        )

    def record_delivery_attestation(
        self,
        *,
        attestation_id: str,
        run_id: str,
        generation: int,
        status: str,
        payload: Mapping[str, Any],
        payload_hash: str,
        signature: Mapping[str, Any] | None,
        key_id: str | None,
        public_key_fingerprint: str | None,
        evidence_valid: bool,
        identity_status: str,
        previous_attestation_hash: str | None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Insert one immutable generation inside the caller's transaction."""
        with self.unit_of_work():
            existing = self.conn.execute(
                "SELECT * FROM delivery_attestations WHERE run_id=? AND generation=?",
                (run_id, int(generation)),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_hash"]) != payload_hash:
                    raise ValueError("attestation generation conflicts with different payload")
                return self._attestation_row(existing)
            self.conn.execute("UPDATE delivery_attestations SET is_current=0 WHERE run_id=?", (run_id,))
            created_at = str(payload.get("signed_at") or utc_now())
            self.conn.execute(
                """
                INSERT INTO delivery_attestations(
                  attestation_id, run_id, generation, status, payload_json,
                  payload_hash, signature_json, key_id, public_key_fingerprint,
                  evidence_valid, identity_status, previous_attestation_hash,
                  error, is_current, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    attestation_id, run_id, int(generation), status,
                    json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    payload_hash,
                    json.dumps(dict(signature), ensure_ascii=False, sort_keys=True, separators=(",", ":")) if signature else None,
                    key_id, public_key_fingerprint, int(evidence_valid), identity_status,
                    previous_attestation_hash, error, created_at,
                ),
            )
            if self._event_sink is not None:
                message = {
                    "type": "attestation_created", "version": 1,
                    "task_id": run_id, "run_id": run_id,
                    "attestation": {
                        "attestation_id": attestation_id, "generation": int(generation),
                        "status": status, "payload_hash": payload_hash,
                        "public_key_fingerprint": public_key_fingerprint,
                    },
                }
                self.engine.on_commit(lambda message=message: self._event_sink(message))
        row = self.conn.execute("SELECT * FROM delivery_attestations WHERE attestation_id=?", (attestation_id,)).fetchone()
        if row is None:
            raise RuntimeError("attestation insert did not persist")
        return self._attestation_row(row)

    def latest_delivery_attestation(self, run_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM delivery_attestations WHERE run_id=? AND is_current=1",
            (run_id,),
        ).fetchone()
        return self._attestation_row(row) if row else None

    def get_delivery_attestation(self, attestation_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM delivery_attestations WHERE attestation_id=?",
            (attestation_id,),
        ).fetchone()
        return self._attestation_row(row) if row else None

    def list_delivery_attestations(self, run_id: str) -> list[dict[str, Any]]:
        return [
            self._attestation_row(row)
            for row in self.conn.execute(
                "SELECT * FROM delivery_attestations WHERE run_id=? ORDER BY generation",
                (run_id,),
            )
        ]

    def observe_signing_key(
        self,
        *,
        project_id: str,
        key_id: str | None,
        public_key_fingerprint: str | None,
        permission_status: str,
        status: str,
    ) -> str:
        observation_id = f"keyobs_{uuid4().hex}"
        with self.unit_of_work():
            self.conn.execute(
                """
                INSERT INTO project_signing_key_observations(
                  observation_id, project_id, key_id, public_key_fingerprint,
                  permission_status, status, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (observation_id, project_id, key_id, public_key_fingerprint, permission_status, status, utc_now()),
            )
        return observation_id

    def record_attestation_export(
        self,
        *,
        export_id: str,
        run_id: str,
        attestation_id: str,
        bundle_path: Path,
        bundle_sha256: str,
        size_bytes: int,
        status: str = "created",
    ) -> dict[str, Any]:
        with self.unit_of_work():
            self.conn.execute(
                """
                INSERT OR IGNORE INTO attestation_exports(
                  export_id, run_id, attestation_id, bundle_path, bundle_sha256,
                  size_bytes, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (export_id, run_id, attestation_id, str(bundle_path), bundle_sha256, int(size_bytes), status, utc_now()),
            )
        row = self.conn.execute("SELECT * FROM attestation_exports WHERE export_id=?", (export_id,)).fetchone()
        if row is None:
            row = self.conn.execute(
                """
                SELECT * FROM attestation_exports
                WHERE run_id=? AND attestation_id=? AND bundle_sha256=?
                """,
                (run_id, attestation_id, bundle_sha256),
            ).fetchone()
        if row is None:
            raise RuntimeError("attestation export insert did not persist")
        return dict(row)

    def get_attestation_export(self, export_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM attestation_exports WHERE export_id=?", (export_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _attestation_row(row: Any) -> dict[str, Any]:
        payload = dict(row)
        payload["payload"] = _json_dict(payload.pop("payload_json", "{}"))
        signature_json = payload.pop("signature_json", None)
        payload["signature"] = _json_dict(signature_json) if signature_json else None
        payload["evidence_valid"] = bool(payload.get("evidence_valid"))
        payload["is_current"] = bool(payload.get("is_current"))
        return payload

    def record_task_feature_set(self, features: TaskFeatureSet) -> TaskFeatureSet:
        payload = features.to_dict()
        with self.unit_of_work():
            self.conn.execute(
                """
                INSERT OR IGNORE INTO task_feature_sets(
                  feature_set_id, run_id, contract_version, extractor_version,
                  source_hash, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    features.feature_set_id, features.run_id, str(payload["contract_version"]),
                    features.extractor_version, features.source_hash,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True), features.created_at,
                ),
            )
        stored = self.conn.execute(
            "SELECT payload_json FROM task_feature_sets WHERE run_id=? AND source_hash=?",
            (features.run_id, features.source_hash),
        ).fetchone()
        return TaskFeatureSet.from_dict(json.loads(str(stored["payload_json"]))) if stored else features

    def latest_task_feature_set(self, run_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT payload_json FROM task_feature_sets WHERE run_id=? ORDER BY created_at DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        return _json_dict(row["payload_json"]) if row else None

    def get_route_decision(
        self,
        run_id: str,
        *,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM route_decisions WHERE run_id=? AND idempotency_key=?",
            (run_id, idempotency_key),
        ).fetchone()
        return self._route_decision_row(row)

    def latest_route_decision(self, run_id: str, *, kind: str | None = None) -> dict[str, Any] | None:
        if kind:
            row = self.conn.execute(
                "SELECT * FROM route_decisions WHERE run_id=? AND decision_kind=? ORDER BY created_at DESC LIMIT 1",
                (run_id, kind),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM route_decisions WHERE run_id=? ORDER BY created_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        return self._route_decision_row(row)

    def list_route_decisions(self, run_id: str) -> list[dict[str, Any]]:
        return [
            self._route_decision_row(row) or {}
            for row in self.conn.execute("SELECT * FROM route_decisions WHERE run_id=? ORDER BY created_at", (run_id,))
        ]

    def record_route_decision(self, decision: RouteDecision) -> RouteDecision:
        if not verify_route_decision_hash(decision):
            raise ValueError("route decision hash mismatch")
        feature = self.conn.execute(
            "SELECT source_hash FROM task_feature_sets WHERE feature_set_id=? AND run_id=?",
            (decision.feature_set_id, decision.run_id),
        ).fetchone()
        if feature is None or str(feature["source_hash"]) != decision.feature_hash:
            raise ValueError("route decision feature binding mismatch")
        payload = decision.to_dict()
        with self.unit_of_work():
            self.conn.execute(
                """
                INSERT OR IGNORE INTO route_decisions(
                  decision_id, run_id, decision_kind, decision_version,
                  idempotency_key, feature_set_id, feature_hash, candidates_json,
                  selected_main_provider, selected_reviewer_provider,
                  benchmark_snapshot_id, benchmark_snapshot_hash, policy_version,
                  scorer_version, reason_codes_json, routing_policy_json,
                  supersedes_decision_id, created_at, prev_hash, decision_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.decision_id, decision.run_id, decision.decision_kind,
                    decision.decision_version, decision.idempotency_key,
                    decision.feature_set_id, decision.feature_hash,
                    json.dumps(payload["candidates"], ensure_ascii=False, sort_keys=True),
                    decision.selected_main_provider, decision.selected_reviewer_provider,
                    decision.benchmark_snapshot_id, decision.benchmark_snapshot_hash,
                    decision.policy_version, decision.scorer_version,
                    json.dumps(payload["reason_codes"], ensure_ascii=False, sort_keys=True),
                    json.dumps(payload["routing_policy"], ensure_ascii=False, sort_keys=True),
                    decision.supersedes_decision_id, decision.created_at,
                    decision.prev_hash, decision.decision_hash,
                ),
            )
            if decision.selected_main_provider:
                self.conn.execute(
                    "UPDATE runs SET provider=?, updated_at=? WHERE run_id=?",
                    (decision.selected_main_provider, decision.created_at, decision.run_id),
                )
            if self._event_sink is not None:
                message = {
                    "type": "route_decision", "version": 1,
                    "task_id": decision.run_id, "run_id": decision.run_id,
                    "decision": payload,
                }
                self.engine.on_commit(lambda message=message: self._event_sink(message))
        row = self.get_route_decision(decision.run_id, idempotency_key=decision.idempotency_key)
        return self.route_decision_from_row(row or payload)

    def route_candidate_from_payload(self, payload: Mapping[str, Any]) -> RouteCandidate:
        return RouteCandidate.from_dict(payload)

    def route_decision_from_row(self, row: Mapping[str, Any]) -> RouteDecision:
        return RouteDecision(
            decision_id=str(row["decision_id"]), run_id=str(row["run_id"]),
            decision_kind=str(row["decision_kind"]), decision_version=int(row.get("decision_version") or 1),
            idempotency_key=str(row["idempotency_key"]), feature_set_id=str(row["feature_set_id"]),
            feature_hash=str(row["feature_hash"]),
            candidates=tuple(RouteCandidate.from_dict(item) for item in row.get("candidates", []) if isinstance(item, Mapping)),
            selected_main_provider=str(row["selected_main_provider"]) if row.get("selected_main_provider") else None,
            selected_reviewer_provider=str(row["selected_reviewer_provider"]) if row.get("selected_reviewer_provider") else None,
            benchmark_snapshot_id=str(row["benchmark_snapshot_id"]) if row.get("benchmark_snapshot_id") else None,
            benchmark_snapshot_hash=str(row["benchmark_snapshot_hash"]) if row.get("benchmark_snapshot_hash") else None,
            policy_version=str(row.get("policy_version") or "muxdev.routing-policy/1"),
            scorer_version=str(row.get("scorer_version") or "muxdev.beta-quality/1"),
            reason_codes=tuple(str(item) for item in row.get("reason_codes", [])),
            routing_policy=dict(row.get("routing_policy") or {}),
            supersedes_decision_id=str(row["supersedes_decision_id"]) if row.get("supersedes_decision_id") else None,
            created_at=str(row["created_at"]), prev_hash=str(row["prev_hash"]) if row.get("prev_hash") else None,
            decision_hash=str(row["decision_hash"]),
        )

    def _route_decision_row(self, row: Any | None) -> dict[str, Any] | None:
        if row is None:
            return None
        payload = dict(row)
        payload["candidates"] = _json_list(payload.pop("candidates_json", "[]"))
        payload["reason_codes"] = _json_list(payload.pop("reason_codes_json", "[]"))
        payload["routing_policy"] = _json_dict(payload.pop("routing_policy_json", "{}"))
        return payload

    def record_routing_outcome(
        self,
        *,
        run_id: str,
        decision_id: str | None,
        provider: str,
        task_type: str,
        verified_success: bool,
        quality_score: float,
        evidence_complete: bool,
        safety_violation: bool = False,
        cost_usd: float = 0.0,
        latency_seconds: float = 0.0,
        source: str = "production",
    ) -> str | None:
        if not evidence_complete:
            return None
        outcome_id = f"outcome_{uuid4().hex}"
        with self.unit_of_work():
            self.conn.execute(
                """
                INSERT OR IGNORE INTO routing_outcomes(
                  outcome_id, run_id, decision_id, provider, task_type,
                  verified_success, quality_score, evidence_complete,
                  safety_violation, cost_usd, latency_seconds, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    outcome_id, run_id, decision_id, provider, task_type,
                    int(verified_success), max(0.0, min(1.0, float(quality_score))), 1,
                    int(safety_violation), max(0.0, float(cost_usd)),
                    max(0.0, float(latency_seconds)), source, utc_now(),
                ),
            )
        row = self.conn.execute(
            "SELECT outcome_id FROM routing_outcomes WHERE run_id=? AND provider=? AND source=?",
            (run_id, provider, source),
        ).fetchone()
        return str(row["outcome_id"]) if row else None

    def routing_outcomes(
        self,
        *,
        provider: str | None = None,
        task_type: str | None = None,
        evidence_complete: bool | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[object] = []
        if provider:
            clauses.append("provider=?")
            values.append(provider)
        if task_type:
            clauses.append("task_type=?")
            values.append(task_type)
        if evidence_complete is not None:
            clauses.append("evidence_complete=?")
            values.append(int(evidence_complete))
        if source:
            clauses.append("source=?")
            values.append(source)
        query = "SELECT * FROM routing_outcomes"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at"
        return [dict(row) for row in self.conn.execute(query, values)]

    def record_review_assignment(self, assignment: ReviewAssignment) -> dict[str, Any]:
        with self.unit_of_work():
            self.conn.execute(
                """
                INSERT OR IGNORE INTO review_assignments(
                  review_id, run_id, route_decision_id, reviewer_provider,
                  main_provider, status, snapshot_hash, attempt,
                  waiver_approval_id, verdict, findings_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assignment.review_id, assignment.run_id, assignment.route_decision_id,
                    assignment.reviewer_provider, assignment.main_provider, assignment.status,
                    assignment.snapshot_hash, assignment.attempt, assignment.waiver_approval_id,
                    assignment.verdict, json.dumps([dict(item) for item in assignment.findings], ensure_ascii=False, sort_keys=True),
                    assignment.created_at, assignment.updated_at,
                ),
            )
        return self.get_review_assignment(assignment.review_id) or assignment.to_dict()

    def update_review_assignment(
        self,
        review_id: str,
        *,
        status: str,
        snapshot_hash: str | None = None,
        verdict: str | None = None,
        findings: list[Mapping[str, Any]] | None = None,
        waiver_approval_id: str | None = None,
    ) -> dict[str, Any]:
        with self.unit_of_work():
            self.conn.execute(
                """
                UPDATE review_assignments SET status=?, snapshot_hash=COALESCE(?, snapshot_hash),
                  verdict=COALESCE(?, verdict), findings_json=COALESCE(?, findings_json),
                  waiver_approval_id=COALESCE(?, waiver_approval_id), updated_at=?
                WHERE review_id=?
                """,
                (
                    status, snapshot_hash, verdict,
                    json.dumps([dict(item) for item in findings], ensure_ascii=False, sort_keys=True) if findings is not None else None,
                    waiver_approval_id, utc_now(), review_id,
                ),
            )
            row = self.conn.execute("SELECT run_id FROM review_assignments WHERE review_id=?", (review_id,)).fetchone()
            if self._event_sink is not None and row:
                message = {"type": "review_updated", "version": 1, "task_id": row["run_id"], "run_id": row["run_id"], "review_id": review_id, "status": status}
                self.engine.on_commit(lambda message=message: self._event_sink(message))
        return self.get_review_assignment(review_id) or {}

    def get_review_assignment(self, review_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM review_assignments WHERE review_id=?", (review_id,)).fetchone()
        return self._review_row(row)

    def list_review_assignments(self, run_id: str) -> list[dict[str, Any]]:
        return [self._review_row(row) or {} for row in self.conn.execute(
            "SELECT * FROM review_assignments WHERE run_id=? ORDER BY created_at", (run_id,)
        )]

    def _review_row(self, row: Any | None) -> dict[str, Any] | None:
        if row is None:
            return None
        payload = dict(row)
        payload["findings"] = _json_list(payload.pop("findings_json", "[]"))
        return payload

    def register_benchmark_snapshot(
        self,
        *,
        snapshot_id: str,
        suite_name: str,
        payload: Mapping[str, Any],
        active: bool = True,
    ) -> dict[str, Any]:
        safe_payload = json.loads(redact(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)))
        snapshot_hash = canonical_hash(safe_payload)
        with self.unit_of_work():
            self.conn.execute(
                """
                INSERT OR IGNORE INTO benchmark_snapshots(
                  snapshot_id, contract_version, suite_name, case_count,
                  payload_json, snapshot_hash, active, registered_at
                ) VALUES (?, 'muxdev.trusted-routing-bench.v2', ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id, suite_name, len(safe_payload.get("cases", [])) if isinstance(safe_payload, dict) else 0,
                    json.dumps(safe_payload, ensure_ascii=False, sort_keys=True), snapshot_hash, int(active), utc_now(),
                ),
            )
        return self.latest_benchmark_snapshot(snapshot_id) or {}

    def latest_benchmark_snapshot(self, snapshot_id: str | None = None) -> dict[str, Any] | None:
        if snapshot_id:
            row = self.conn.execute("SELECT * FROM benchmark_snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
        else:
            row = self.conn.execute("SELECT * FROM benchmark_snapshots WHERE active=1 ORDER BY registered_at DESC LIMIT 1").fetchone()
        if row is None:
            return None
        payload = dict(row)
        payload["payload"] = _json_dict(payload.pop("payload_json", "{}"))
        return payload

    def list_benchmark_snapshots(self) -> list[dict[str, Any]]:
        rows = []
        for row in self.conn.execute("SELECT * FROM benchmark_snapshots ORDER BY registered_at DESC"):
            payload = dict(row)
            payload.pop("payload_json", None)
            rows.append(payload)
        return rows

    def create_benchmark_execution(
        self,
        *,
        execution_id: str,
        suite_id: str,
        suite_hash: str,
        mode: str,
        plan: Mapping[str, Any],
        max_cost_usd: float | None,
        status: str = "planned",
    ) -> dict[str, Any]:
        safe_plan = json.loads(redact(json.dumps(dict(plan), ensure_ascii=False, sort_keys=True)))
        created = utc_now()
        with self.unit_of_work():
            self.conn.execute(
                """
                INSERT INTO benchmark_executions(
                  execution_id, suite_id, suite_hash, mode, status,
                  max_cost_usd, observed_cost_usd, provider_concurrency,
                  plan_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, 1, ?, ?, ?)
                """,
                (
                    execution_id, suite_id, suite_hash, mode, status,
                    max_cost_usd, json.dumps(safe_plan, ensure_ascii=False, sort_keys=True), created, created,
                ),
            )
        return self.get_benchmark_execution(execution_id) or {}

    def update_benchmark_execution(
        self,
        execution_id: str,
        *,
        status: str,
        observed_cost_usd: float | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        with self.unit_of_work():
            self.conn.execute(
                """
                UPDATE benchmark_executions
                SET status=?, observed_cost_usd=COALESCE(?, observed_cost_usd),
                    error=?, updated_at=?
                WHERE execution_id=?
                """,
                (status, observed_cost_usd, error, utc_now(), execution_id),
            )
        return self.get_benchmark_execution(execution_id) or {}

    def get_benchmark_execution(self, execution_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM benchmark_executions WHERE execution_id=?", (execution_id,)).fetchone()
        if row is None:
            return None
        payload = dict(row)
        payload["plan"] = _json_dict(payload.pop("plan_json", "{}"))
        payload["events"] = self.list_benchmark_events(execution_id)
        payload["results"] = self.list_benchmark_case_results(execution_id)
        report = self.get_benchmark_report(execution_id)
        if report:
            payload["report"] = report
        return payload

    def list_benchmark_executions(self) -> list[dict[str, Any]]:
        rows = []
        for row in self.conn.execute("SELECT * FROM benchmark_executions ORDER BY created_at DESC"):
            payload = dict(row)
            payload.pop("plan_json", None)
            rows.append(payload)
        return rows

    def append_benchmark_event(self, execution_id: str, event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        safe_payload = json.loads(redact(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)))
        with self.unit_of_work():
            last = self.conn.execute(
                "SELECT sequence, event_hash FROM benchmark_events WHERE execution_id=? ORDER BY sequence DESC LIMIT 1",
                (execution_id,),
            ).fetchone()
            sequence = int(last["sequence"]) + 1 if last else 1
            prev_hash = str(last["event_hash"]) if last else None
            created = utc_now()
            event_id = f"bevt_{uuid4().hex}"
            event_hash = canonical_hash(
                {
                    "event_id": event_id,
                    "execution_id": execution_id,
                    "sequence": sequence,
                    "event_type": event_type,
                    "payload": safe_payload,
                    "created_at": created,
                    "prev_hash": prev_hash,
                }
            )
            self.conn.execute(
                """
                INSERT INTO benchmark_events(
                  event_id, execution_id, sequence, event_type, payload_json,
                  created_at, prev_hash, event_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, execution_id, sequence, event_type,
                    json.dumps(safe_payload, ensure_ascii=False, sort_keys=True), created, prev_hash, event_hash,
                ),
            )
            if self._event_sink is not None:
                message = {
                    "type": "benchmark_event",
                    "version": 1,
                    "execution_id": execution_id,
                    "event_type": event_type,
                    "sequence": sequence,
                }
                self.engine.on_commit(lambda message=message: self._event_sink(message))
        return self.list_benchmark_events(execution_id)[-1]

    def list_benchmark_events(self, execution_id: str) -> list[dict[str, Any]]:
        rows = []
        for row in self.conn.execute(
            "SELECT * FROM benchmark_events WHERE execution_id=? ORDER BY sequence", (execution_id,)
        ):
            item = dict(row)
            item["payload"] = _json_dict(item.pop("payload_json", "{}"))
            rows.append(item)
        return rows

    def record_benchmark_case_result(
        self,
        *,
        execution_id: str,
        case_id: str,
        provider: str,
        status: str,
        payload: Mapping[str, Any],
        run_id: str | None = None,
    ) -> dict[str, Any]:
        safe_payload = json.loads(redact(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)))
        result_hash = canonical_hash(safe_payload)
        with self.unit_of_work():
            self.conn.execute(
                """
                INSERT OR IGNORE INTO benchmark_case_results(
                  result_id, execution_id, case_id, provider, run_id, status,
                  payload_json, result_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"bres_{uuid4().hex}", execution_id, case_id, provider, run_id, status,
                    json.dumps(safe_payload, ensure_ascii=False, sort_keys=True), result_hash, utc_now(),
                ),
            )
        rows = [row for row in self.list_benchmark_case_results(execution_id) if row["case_id"] == case_id and row["provider"] == provider]
        return rows[0]

    def list_benchmark_case_results(self, execution_id: str) -> list[dict[str, Any]]:
        rows = []
        for row in self.conn.execute(
            "SELECT * FROM benchmark_case_results WHERE execution_id=? ORDER BY case_id, provider", (execution_id,)
        ):
            item = dict(row)
            item["payload"] = _json_dict(item.pop("payload_json", "{}"))
            rows.append(item)
        return rows

    def record_benchmark_report(self, execution_id: str, *, status: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        safe_payload = json.loads(redact(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True)))
        report_hash = canonical_hash(safe_payload)
        with self.unit_of_work():
            self.conn.execute(
                """
                INSERT OR IGNORE INTO benchmark_reports(
                  report_id, execution_id, status, payload_json, report_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    f"brep_{uuid4().hex}", execution_id, status,
                    json.dumps(safe_payload, ensure_ascii=False, sort_keys=True), report_hash, utc_now(),
                ),
            )
        return self.get_benchmark_report(execution_id) or {}

    def get_benchmark_report(self, execution_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM benchmark_reports WHERE execution_id=?", (execution_id,)).fetchone()
        if row is None:
            return None
        payload = dict(row)
        payload["payload"] = _json_dict(payload.pop("payload_json", "{}"))
        return payload

    def storage_health(self, *, check_integrity: bool = True) -> dict[str, object]:
        return self.engine.health(check_integrity=check_integrity).to_dict()

    def unit_of_work(self) -> UnitOfWork:
        return UnitOfWork(self.engine)

    def record_adapter_certification(self, report: CertificationReport) -> dict[str, Any]:
        """Store a redacted immutable certification report."""
        payload = report.to_dict()
        with self.unit_of_work():
            self.conn.execute(
                """
                INSERT OR IGNORE INTO adapter_certifications(
                  certification_id, provider, status, mode, adapter_version,
                  provider_version, fingerprint, policy_version, platform,
                  capabilities_json, trust_tier, evidence_json, failure,
                  issued_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.certification_id,
                    report.provider,
                    str(report.status),
                    report.mode,
                    report.adapter_version,
                    report.provider_version,
                    report.fingerprint,
                    report.policy_version,
                    report.platform,
                    json.dumps(payload["capabilities"], ensure_ascii=False, sort_keys=True),
                    str(report.trust_tier),
                    json.dumps(payload["evidence"], ensure_ascii=False, sort_keys=True),
                    redact(report.failure or "") or None,
                    report.issued_at,
                    report.expires_at,
                ),
            )
        return self.get_adapter_certification(report.provider, certification_id=report.certification_id) or payload

    def get_adapter_certification(
        self,
        provider: str,
        *,
        certification_id: str | None = None,
    ) -> dict[str, Any] | None:
        if certification_id:
            row = self.conn.execute(
                "SELECT * FROM adapter_certifications WHERE provider = ? AND certification_id = ?",
                (provider, certification_id),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM adapter_certifications WHERE provider = ? ORDER BY issued_at DESC LIMIT 1",
                (provider,),
            ).fetchone()
        return _certification_row(row)

    def list_adapter_certifications(self, *, provider: str | None = None) -> list[dict[str, Any]]:
        if provider:
            rows = self.conn.execute(
                "SELECT * FROM adapter_certifications WHERE provider = ? ORDER BY issued_at DESC",
                (provider,),
            )
        else:
            rows = self.conn.execute(
                "SELECT * FROM adapter_certifications ORDER BY provider, issued_at DESC"
            )
        return [_certification_row(row) for row in rows]

    def append_harness_events(self, events: list[HarnessEvent] | tuple[HarnessEvent, ...]) -> list[HarnessEvent]:
        inserted: list[HarnessEvent] = []
        with self.unit_of_work():
            for event in events:
                if self._append_harness_event(event):
                    inserted.append(event)
        return inserted

    def _append_harness_event(self, event: HarnessEvent) -> bool:
        if not event.verify_hash():
            raise ValueError(f"invalid Harness Event hash: {event.event_id}")
        existing = self.conn.execute(
            "SELECT event_hash FROM harness_events WHERE run_id = ? AND idempotency_key = ?",
            (event.run_id, event.idempotency_key),
        ).fetchone()
        if existing is not None:
            if str(existing["event_hash"]) != event.event_hash:
                raise ValueError("Harness Event idempotency key conflicts with different content")
            return False
        last = self.conn.execute(
            """
            SELECT sequence, event_hash FROM harness_events
            WHERE run_id = ? AND stage_id = ? AND provider = ? AND attempt = ?
            ORDER BY sequence DESC LIMIT 1
            """,
            (event.run_id, event.stage_id, event.provider, event.attempt),
        ).fetchone()
        expected_sequence = int(last["sequence"]) + 1 if last else 1
        expected_prev_hash = str(last["event_hash"]) if last else None
        if event.sequence != expected_sequence or event.prev_hash != expected_prev_hash:
            raise ValueError(
                f"Harness Event chain conflict for {event.run_id}/{event.stage_id}/{event.attempt}: "
                f"expected sequence {expected_sequence}"
            )
        self.conn.execute(
            """
            INSERT INTO harness_events(
              event_id, run_id, stage_id, provider, attempt, sequence,
              event_type, event_version, source, idempotency_key, payload_json,
              created_at, prev_hash, event_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.run_id,
                event.stage_id,
                event.provider,
                event.attempt,
                event.sequence,
                event.event_type,
                event.event_version,
                str(event.source),
                event.idempotency_key,
                json.dumps(dict(event.payload), ensure_ascii=False, sort_keys=True),
                event.created_at,
                event.prev_hash,
                event.event_hash,
            ),
        )
        if self._event_sink is not None and event.event_type not in {"provider.message.delta"}:
            message = {
                "type": "harness_event",
                "version": 1,
                "task_id": event.run_id,
                "run_id": event.run_id,
                "event": event.to_dict(),
            }
            self.engine.on_commit(lambda message=message: self._event_sink(message))
        return True

    def list_harness_events(
        self,
        run_id: str,
        *,
        stage_id: str | None = None,
    ) -> list[HarnessEvent]:
        if stage_id:
            rows = self.conn.execute(
                "SELECT * FROM harness_events WHERE run_id = ? AND stage_id = ? ORDER BY stage_id, provider, attempt, sequence",
                (run_id, stage_id),
            )
        else:
            rows = self.conn.execute(
                "SELECT * FROM harness_events WHERE run_id = ? ORDER BY stage_id, provider, attempt, sequence",
                (run_id,),
            )
        return [HarnessEvent.from_row(dict(row)) for row in rows]

    def record_harness_cancellation(self, run_id: str, context: dict[str, object]) -> HarnessEvent | None:
        """Let the scheduler record cancellation after the guarded Worker stops."""
        if str(context.get("run_id") or "") != run_id:
            return None
        stage_id = str(context.get("stage_id") or "")
        provider = str(context.get("provider") or "")
        attempt = int(context.get("attempt") or 0)
        if not stage_id or not provider or attempt < 1:
            return None
        with self.unit_of_work():
            existing = self.conn.execute(
                "SELECT * FROM harness_events WHERE run_id = ? AND idempotency_key = ?",
                (run_id, f"cancel:{stage_id}:{provider}:{attempt}"),
            ).fetchone()
            if existing is not None:
                return HarnessEvent.from_row(dict(existing))
            last = self.conn.execute(
                """
                SELECT sequence, event_hash FROM harness_events
                WHERE run_id = ? AND stage_id = ? AND provider = ? AND attempt = ?
                ORDER BY sequence DESC LIMIT 1
                """,
                (run_id, stage_id, provider, attempt),
            ).fetchone()
            event = HarnessEvent.create(
                run_id=run_id,
                stage_id=stage_id,
                provider=provider,
                attempt=attempt,
                sequence=int(last["sequence"]) + 1 if last else 1,
                event_type="harness.attempt_cancelled",
                source=HarnessEventSource.HARNESS,
                idempotency_key=f"cancel:{stage_id}:{provider}:{attempt}",
                payload={
                    "cooperative": bool(context.get("cooperative")),
                    "forced": bool(context.get("forced")),
                    "reason": str(context.get("reason") or "cancellation requested"),
                },
                prev_hash=str(last["event_hash"]) if last else None,
            )
            self._append_harness_event(event)
            mode = "forced" if context.get("forced") else "cooperative"
            self.conn.execute(
                """
                UPDATE provider_attempts
                SET status='cancelled', cancellation_mode=?, summary=?, completed_at=?
                WHERE run_id=? AND stage_id=? AND provider=? AND attempt=?
                """,
                (mode, f"Provider process cancellation: {mode}", utc_now(), run_id, stage_id, provider, attempt),
            )
            return event

    def list_state_events(self, run_id: str) -> list[StateEventEnvelope]:
        rows = self.conn.execute(
            "SELECT * FROM state_events WHERE run_id = ? ORDER BY sequence",
            (run_id,),
        )
        return [StateEventEnvelope.from_row(dict(row)) for row in rows]

    def replay_state(self, run_id: str) -> dict[str, Any]:
        state = initial_run_state(run_id)
        previous_hash: str | None = None
        for event in self.list_state_events(run_id):
            if event.prev_hash != previous_hash or not verify_event_hash(event):
                raise ValueError(f"invalid operational event chain at {run_id}:{event.sequence}")
            state = reduce_run_state(state, event)
            previous_hash = event.event_hash
        return state

    def replay_run(self, run_id: str) -> dict[str, Any]:
        state = self.replay_state(run_id)
        run = self.get_run(run_id)
        projected_stages = {
            str(row["stage_id"]): {"status": str(row["status"]), "attempt": int(row.get("attempt") or 0)}
            for row in self.table_rows("stages", run_id=run_id)
        }
        replayed_stages = {
            key: {"status": str(value.get("status") or ""), "attempt": int(value.get("attempt") or 0)}
            for key, value in dict(state.get("stages") or {}).items()
        }
        differences: list[dict[str, Any]] = []
        if str(run.get("status")) != str(state.get("status")):
            differences.append({"field": "run.status", "projection": run.get("status"), "replay": state.get("status")})
        replayed_run = dict(state.get("run") or {})
        for field in ("task", "workflow", "provider", "workspace", "worktree"):
            if field in replayed_run and str(run.get(field) or "") != str(replayed_run.get(field) or ""):
                differences.append(
                    {"field": f"run.{field}", "projection": run.get(field), "replay": replayed_run.get(field)}
                )
        if projected_stages != replayed_stages:
            differences.append({"field": "stages", "projection": projected_stages, "replay": replayed_stages})
        projected_approvals = {
            str(row["approval_id"]): str(row["status"])
            for row in self.table_rows("approvals", run_id=run_id)
        }
        replayed_approvals = {
            key: str(value.get("status") or "")
            for key, value in dict(state.get("approvals") or {}).items()
        }
        if projected_approvals != replayed_approvals:
            differences.append({"field": "approvals", "projection": projected_approvals, "replay": replayed_approvals})
        projected_actions = {
            str(row["action_id"]): str(row["status"])
            for row in self.table_rows("provider_actions", run_id=run_id)
        }
        replayed_actions = {
            key: str(value.get("status") or "")
            for key, value in dict(state.get("provider_actions") or {}).items()
        }
        if projected_actions != replayed_actions:
            differences.append({"field": "provider_actions", "projection": projected_actions, "replay": replayed_actions})
        return {
            "run_id": run_id,
            "matches": not differences,
            "history_complete": bool(state.get("history_complete")),
            "event_count": int(state.get("last_sequence") or 0),
            "projection_status": run.get("status"),
            "replayed_status": state.get("status"),
            "differences": differences,
        }

    def _append_state_event(
        self,
        *,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
        expected_version: int | None = None,
        causation_id: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[StateEventEnvelope, bool]:
        existing = self.conn.execute(
            "SELECT * FROM state_events WHERE run_id = ? AND idempotency_key = ?",
            (run_id, idempotency_key),
        ).fetchone()
        if existing is not None:
            return StateEventEnvelope.from_row(dict(existing)), False
        version_row = self.conn.execute(
            "SELECT version FROM aggregate_versions WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        version = int(version_row["version"]) if version_row else 0
        if expected_version is not None and version != expected_version:
            raise RuntimeError(f"state version conflict for {run_id}: expected {expected_version}, found {version}")
        last = self.conn.execute(
            "SELECT event_hash FROM state_events WHERE run_id = ? ORDER BY sequence DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        event = StateEventEnvelope.create(
            run_id=run_id,
            sequence=version + 1,
            event_type=event_type,
            idempotency_key=idempotency_key,
            payload=payload,
            causation_id=causation_id,
            correlation_id=correlation_id,
            prev_hash=str(last["event_hash"]) if last else None,
        )
        reduce_run_state(self.replay_state(run_id), event)
        self.conn.execute(
            """
            INSERT INTO state_events(
              event_id, run_id, sequence, event_type, event_version,
              idempotency_key, causation_id, correlation_id, payload_json,
              created_at, prev_hash, event_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id, event.run_id, event.sequence, event.event_type, event.event_version,
                event.idempotency_key, event.causation_id, event.correlation_id,
                json.dumps(dict(event.payload), ensure_ascii=False, sort_keys=True), event.created_at,
                event.prev_hash, event.event_hash,
            ),
        )
        self.conn.execute(
            """
            INSERT INTO aggregate_versions(run_id, version, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET version=excluded.version, updated_at=excluded.updated_at
            """,
            (run_id, event.sequence, event.created_at),
        )
        if self._event_sink is not None:
            message = {"type": "state_event", "version": 1, "task_id": run_id, "run_id": run_id, "event": event.to_dict()}
            self.engine.on_commit(lambda message=message: self._event_sink(message))
        return event, True

    def _ensure_legacy_run_imported(self, run_id: str) -> None:
        row = self.conn.execute("SELECT 1 FROM state_events WHERE run_id = ? LIMIT 1", (run_id,)).fetchone()
        if row is not None:
            return
        run = self.conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if run is None:
            raise FileNotFoundError(f"run not found in blackboard: {run_id}")
        snapshot = {
            "status": str(run["status"]),
            "run": dict(run),
            "stages": [dict(item) for item in self.conn.execute("SELECT * FROM stages WHERE run_id = ?", (run_id,))],
            "approvals": [dict(item) for item in self.conn.execute("SELECT * FROM approvals WHERE run_id = ?", (run_id,))],
            "provider_actions": [dict(item) for item in self.conn.execute("SELECT * FROM provider_actions WHERE run_id = ?", (run_id,))],
        }
        self._append_state_event(
            run_id=run_id,
            event_type=LEGACY_RUN_IMPORTED,
            payload={"snapshot": snapshot},
            idempotency_key="legacy:import",
        )
        self.conn.execute("UPDATE runs SET history_complete = 0 WHERE run_id = ?", (run_id,))

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
        with self.unit_of_work():
            _, inserted = self._append_state_event(
                run_id=run_id,
                event_type=RUN_CREATED,
                payload={
                    "task": task, "workflow": workflow, "provider": provider,
                    "workspace": str(Path(workspace).expanduser().resolve()),
                    "worktree": str(Path(worktree).expanduser().resolve()),
                },
                idempotency_key="run:create",
                expected_version=0,
            )
            if inserted:
                self.conn.execute(
                    """
                    INSERT INTO runs(
                      run_id, task, workflow, provider, status, workspace, worktree,
                      created_at, updated_at, history_complete
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        run_id, task, workflow, provider, RunStatus.CREATED,
                        str(Path(workspace).expanduser().resolve()), str(Path(worktree).expanduser().resolve()), now, now,
                    ),
                )

    def set_run_status(
        self,
        run_id: str,
        status: RunStatus | str,
        *,
        recovery_reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> StateEventEnvelope:
        with self.unit_of_work():
            self._ensure_legacy_run_imported(run_id)
            current = self.conn.execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if current is None:
                raise FileNotFoundError(f"run not found in blackboard: {run_id}")
            previous = str(current["status"])
            target = str(status)
            if previous == str(RunStatus.CREATED) and target not in {
                str(RunStatus.CREATED), str(RunStatus.ROUTING), str(RunStatus.RUNNING), str(RunStatus.BLOCKED), str(RunStatus.ABORTED)
            }:
                self._append_state_event(
                    run_id=run_id,
                    event_type=RUN_TRANSITIONED,
                    payload={
                        "from_status": previous,
                        "to_status": str(RunStatus.RUNNING),
                        "recovery_reason": "compatibility API bridge",
                    },
                    idempotency_key=f"run:compat-running:{uuid4().hex}",
                )
                previous = str(RunStatus.RUNNING)
            if previous in {str(RunStatus.BLOCKED), str(RunStatus.COMPLETED)} and target not in {previous, str(RunStatus.ABORTED)}:
                recovery_reason = recovery_reason or "compatibility API transition"
            event, inserted = self._append_state_event(
                run_id=run_id,
                event_type=RUN_TRANSITIONED,
                payload={"from_status": previous, "to_status": target, "recovery_reason": recovery_reason},
                idempotency_key=idempotency_key or f"run:transition:{uuid4().hex}",
            )
            if inserted:
                self.conn.execute(
                    "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
                    (target, event.created_at, run_id),
                )
            return event

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
        with self.unit_of_work():
            self._ensure_legacy_run_imported(run_id)
            existing = self.conn.execute(
                "SELECT started_at, status, attempt FROM stages WHERE run_id = ? AND stage_id = ?",
                (run_id, stage_id),
            ).fetchone()
            now = utc_now()
            previous = str(existing["status"]) if existing else None
            target = str(status)
            attempt = int(existing["attempt"] or 0) if existing else 0
            retry_reason = None
            if previous is None and target in {str(StageStatus.COMPLETED), str(StageStatus.FAILED)}:
                self._append_state_event(
                    run_id=run_id,
                    event_type=STAGE_TRANSITIONED,
                    payload={
                        "stage_id": stage_id, "role": role, "status": str(StageStatus.RUNNING),
                        "attempt": attempt, "summary": "compatibility API bridge",
                    },
                    idempotency_key=f"stage:{stage_id}:compat-running:{uuid4().hex}",
                )
                previous = str(StageStatus.RUNNING)
            retrying = (
                previous in {str(StageStatus.FAILED), str(StageStatus.COMPLETED), str(StageStatus.SKIPPED)}
                and target in {str(StageStatus.PENDING), str(StageStatus.RUNNING)}
            ) or (previous == str(StageStatus.RUNNING) and target == str(StageStatus.PENDING))
            if retrying:
                attempt += 1
                retry_reason = summary or "compatibility stage retry"
            event, inserted = self._append_state_event(
                run_id=run_id,
                event_type=STAGE_TRANSITIONED,
                payload={
                    "stage_id": stage_id, "role": role, "status": target, "attempt": attempt,
                    "output_path": output_path, "summary": summary, "retry_reason": retry_reason,
                },
                idempotency_key=f"stage:{stage_id}:transition:{uuid4().hex}",
            )
            if not inserted:
                return
            started_at = existing["started_at"] if existing else (now if target == str(StageStatus.RUNNING) else None)
            completed_at = now if target in {str(StageStatus.COMPLETED), str(StageStatus.FAILED), str(StageStatus.SKIPPED)} else None
            self.conn.execute(
                """
                INSERT INTO stages(run_id, stage_id, role, status, started_at, completed_at, output_path, summary, attempt)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, stage_id) DO UPDATE SET
                  role=excluded.role, status=excluded.status, completed_at=excluded.completed_at,
                  output_path=excluded.output_path, summary=excluded.summary, attempt=excluded.attempt
                """,
                (run_id, stage_id, role, target, started_at, completed_at, output_path, summary, attempt),
            )

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
        with self.unit_of_work():
            self._ensure_legacy_run_imported(run_id)
            approval_id = f"appr_{run_id}_{approval_type}_{len(self.list_approvals(run_id=run_id)) + 1}"
            normalized_subject = dict(subject or {})
            if "contract_version" not in normalized_subject:
                normalized_subject = {
                    "contract_version": "muxdev.approval_subject.v1",
                    "run_id": run_id,
                    "stage_id": stage_id,
                    "approval_type": approval_type,
                    "policy_hash": canonical_hash({"approval_type": approval_type}),
                    "content_anchor": canonical_hash({"reason": redact(reason)}),
                    **normalized_subject,
                }
            subject_json = json.dumps(normalized_subject, ensure_ascii=False, sort_keys=True)
            subject_hash = canonical_hash(normalized_subject)
            subject_version = str(normalized_subject.get("contract_version") or "muxdev.approval_subject.v1")
            event, inserted = self._append_state_event(
                run_id=run_id,
                event_type=APPROVAL_REQUESTED,
                payload={
                    "approval_id": approval_id, "stage_id": stage_id, "approval_type": approval_type,
                    "reason": reason, "subject_hash": subject_hash,
                },
                idempotency_key=f"approval:{approval_id}:requested",
            )
            if inserted:
                self.conn.execute(
                    """
                    INSERT INTO approvals(
                      approval_id, run_id, stage_id, type, status, reason,
                      subject_hash, subject_json, subject_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        approval_id, run_id, stage_id, approval_type, ApprovalStatus.PENDING,
                        reason, subject_hash, subject_json, subject_version, event.created_at,
                    ),
                )
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

    def decide_approval(
        self,
        approval_id: str,
        status: ApprovalStatus,
        *,
        operator_source: str | None = None,
        decision_reason: str | None = None,
    ) -> None:
        with self.unit_of_work():
            row = self.conn.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)).fetchone()
            if row is None:
                raise KeyError(f"approval not found: {approval_id}")
            run_id = str(row["run_id"])
            self._ensure_legacy_run_imported(run_id)
            event, inserted = self._append_state_event(
                run_id=run_id,
                event_type=APPROVAL_DECIDED,
                payload={
                    "approval_id": approval_id,
                    "status": str(status),
                    "operator_source": redact(operator_source or "local"),
                    "decision_reason": redact(decision_reason or "")[:500] or None,
                },
                idempotency_key=f"approval:{approval_id}:decision:{status}",
            )
            if inserted:
                self.conn.execute(
                    """
                    UPDATE approvals
                    SET status=?, decided_at=?, operator_source=?, decision_reason=?
                    WHERE approval_id=?
                    """,
                    (
                        str(status), event.created_at, redact(operator_source or "local"),
                        redact(decision_reason or "")[:500] or None, approval_id,
                    ),
                )

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
        input_kind: str | None = None,
        choices: list[dict[str, Any]] | None = None,
        default_choice: str | None = None,
        timeout_seconds: int | None = None,
        auto_policy: str | None = None,
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
        with self.unit_of_work():
            self._ensure_legacy_run_imported(run_id)
            action_id = f"pact_{run_id}_{len(self.list_provider_actions(run_id=run_id)) + 1}"
            action_choices = choices if choices is not None else (options or [])
            action_input_kind = input_kind or _provider_action_input_kind(kind, action_choices)
            action_default = default_choice or _default_choice(action_choices)
            event, inserted = self._append_state_event(
                run_id=run_id,
                event_type=PROVIDER_ACTION_REQUESTED,
                payload={
                    "action_id": action_id, "stage_id": stage_id, "provider": provider,
                    "role": role, "kind": kind, "input_kind": action_input_kind,
                },
                idempotency_key=f"provider-action:{action_id}:requested",
            )
            if not inserted:
                return action_id
            self.conn.execute(
            """
            INSERT INTO provider_actions(
              action_id, run_id, stage_id, provider, role, kind, status, prompt_text,
              options_json, input_kind, choices_json, default_choice, timeout_seconds, response_json, auto_policy,
              transcript_path, chunks_path, attach_command, source_event_hash,
              created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                action_input_kind,
                json.dumps(action_choices, ensure_ascii=False),
                action_default,
                timeout_seconds,
                None,
                auto_policy or "manual",
                transcript_path,
                chunks_path,
                attach_command,
                source_event_hash,
                event.created_at,
                event.created_at,
            ),
        )
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
            try:
                row["choices"] = json.loads(row.get("choices_json") or row.get("options_json") or "[]")
            except json.JSONDecodeError:
                row["choices"] = []
            try:
                row["response"] = json.loads(row.get("response_json") or "null")
            except json.JSONDecodeError:
                row["response"] = None
            row["input_kind"] = row.get("input_kind") or _provider_action_input_kind(str(row.get("kind") or ""), row["choices"])
            row["default_choice"] = row.get("default_choice") or _default_choice(row["choices"])
            row["auto_policy"] = row.get("auto_policy") or "manual"
        return rows

    def update_provider_action_status(self, action_id: str, status: ProviderActionStatus | str) -> None:
        with self.unit_of_work():
            row = self.conn.execute("SELECT run_id FROM provider_actions WHERE action_id = ?", (action_id,)).fetchone()
            if row is None:
                raise KeyError(f"provider action not found: {action_id}")
            run_id = str(row["run_id"])
            self._ensure_legacy_run_imported(run_id)
            event, inserted = self._append_state_event(
                run_id=run_id,
                event_type=PROVIDER_ACTION_TRANSITIONED,
                payload={"action_id": action_id, "status": str(status)},
                idempotency_key=f"provider-action:{action_id}:status:{status}",
            )
            if inserted:
                self.conn.execute(
                    "UPDATE provider_actions SET status = ?, updated_at = ? WHERE action_id = ?",
                    (str(status), event.created_at, action_id),
                )

    def respond_provider_action(
        self,
        action_id: str,
        *,
        response: Any,
        status: ProviderActionStatus | str = ProviderActionStatus.HANDLED,
    ) -> None:
        with self.unit_of_work():
            row = self.conn.execute("SELECT run_id FROM provider_actions WHERE action_id = ?", (action_id,)).fetchone()
            if row is None:
                raise KeyError(f"provider action not found: {action_id}")
            run_id = str(row["run_id"])
            self._ensure_legacy_run_imported(run_id)
            response_hash = canonical_hash(response)
            event, inserted = self._append_state_event(
                run_id=run_id,
                event_type=PROVIDER_ACTION_RESPONDED,
                payload={"action_id": action_id, "status": str(status), "response": response, "response_hash": response_hash},
                idempotency_key=f"provider-action:{action_id}:response:{response_hash}",
            )
            if inserted:
                self.conn.execute(
                    "UPDATE provider_actions SET response_json = ?, status = ?, updated_at = ? WHERE action_id = ?",
                    (json.dumps(response, ensure_ascii=False, sort_keys=True), str(status), event.created_at, action_id),
                )

    def start_provider_attempt(
        self,
        run_id: str,
        stage_id: str,
        *,
        provider: str,
        role: str | None,
        attempt: int,
        certification_id: str | None = None,
        adapter_version: str | None = None,
        trust_tier: str = "opaque",
        isolation_mode: str = "process",
        session_id: str | None = None,
        waiver_approval_id: str | None = None,
    ) -> None:
        """Record the start of one provider execution attempt."""
        self.conn.execute(
            """
            INSERT INTO provider_attempts(
              run_id, stage_id, provider, role, attempt, status, started_at,
              certification_id, adapter_version, trust_tier, isolation_mode,
              session_id, waiver_approval_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, stage_id, provider, attempt) DO UPDATE SET
              role=excluded.role, status=excluded.status, started_at=excluded.started_at,
              certification_id=excluded.certification_id,
              adapter_version=excluded.adapter_version, trust_tier=excluded.trust_tier,
              isolation_mode=excluded.isolation_mode, session_id=excluded.session_id,
              waiver_approval_id=excluded.waiver_approval_id
            """,
            (
                run_id, stage_id, provider, role, attempt, "running", utc_now(),
                certification_id, adapter_version, trust_tier, isolation_mode,
                session_id, waiver_approval_id,
            ),
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
        harness_events: list[HarnessEvent] | tuple[HarnessEvent, ...] = (),
    ) -> None:
        """Complete a provider attempt with normalized outcome metadata."""
        with self.unit_of_work():
            for event in harness_events:
                self._append_harness_event(event)
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

    def update_feedback_event_status(self, feedback_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE feedback_events SET status = ?, updated_at = ? WHERE feedback_id = ?",
            (status, utc_now(), feedback_id),
        )
        self.conn.commit()

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

    def replace_evidence_v2(
        self,
        *,
        run_id: str,
        events: list[dict[str, Any]],
        manifest: dict[str, Any],
        manifest_path: Path,
        manifest_hash: str,
        evaluation: dict[str, Any],
        evaluation_path: Path,
        evaluation_hash: str,
    ) -> None:
        """Replace the derived Evidence v2 rows for one run."""
        self.conn.execute("DELETE FROM evidence_events WHERE run_id = ?", (run_id,))
        self.conn.execute("DELETE FROM evidence_manifests WHERE run_id = ?", (run_id,))
        self.conn.execute("DELETE FROM evidence_evaluations WHERE run_id = ?", (run_id,))
        for event in events:
            self.conn.execute(
                """
                INSERT INTO evidence_events(
                  event_id, run_id, stage_id, layer, kind, claim, status, strength,
                  subject_hash, prev_hash, event_hash, artifact_refs_json,
                  metrics_json, tags_json, source, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["id"],
                    run_id,
                    event.get("stage_id"),
                    event["layer"],
                    event["kind"],
                    redact(str(event.get("claim") or "")),
                    event["status"],
                    event["strength"],
                    event.get("subject_hash"),
                    event.get("prev_hash"),
                    event["event_hash"],
                    json.dumps(event.get("artifact_refs") or [], ensure_ascii=False, sort_keys=True),
                    json.dumps(event.get("metrics") or {}, ensure_ascii=False, sort_keys=True),
                    json.dumps(event.get("tags") or [], ensure_ascii=False),
                    event.get("source") or "muxdev",
                    event["created_at"],
                ),
            )
        self.conn.execute(
            """
            INSERT INTO evidence_manifests(
              run_id, path, manifest_hash, head_hash, event_count, artifact_count,
              required_matrix_json, missing_required_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                str(manifest_path),
                manifest_hash,
                manifest.get("head_hash"),
                int(manifest.get("event_count") or 0),
                int(manifest.get("artifact_count") or 0),
                json.dumps(manifest.get("required_matrix") or {}, ensure_ascii=False, sort_keys=True),
                json.dumps(manifest.get("missing_required") or [], ensure_ascii=False),
                manifest.get("created_at") or utc_now(),
            ),
        )
        self.conn.execute(
            """
            INSERT INTO evidence_evaluations(
              run_id, label, confidence, gates_json, components_json, reasons_json,
              missing_evidence_json, next_actions_json, path, evaluation_hash, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                evaluation.get("label") or "blocked",
                float(evaluation.get("confidence") or 0),
                json.dumps(evaluation.get("gates") or {}, ensure_ascii=False, sort_keys=True),
                json.dumps(evaluation.get("components") or {}, ensure_ascii=False, sort_keys=True),
                json.dumps(evaluation.get("reasons") or [], ensure_ascii=False),
                json.dumps(evaluation.get("missing_evidence") or [], ensure_ascii=False),
                json.dumps(evaluation.get("next_actions") or [], ensure_ascii=False, sort_keys=True),
                str(evaluation_path),
                evaluation_hash,
                evaluation.get("created_at") or utc_now(),
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

    def fail_worker(
        self,
        run_id: str,
        *,
        error_type: str,
        message: str,
        stage_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> StateEventEnvelope:
        """Atomically record a worker failure and block its run."""
        with self.unit_of_work():
            self._ensure_legacy_run_imported(run_id)
            event, inserted = self._append_state_event(
                run_id=run_id,
                event_type=WORKER_FAILED,
                payload={"stage_id": stage_id, "error_type": error_type, "message": redact(message)},
                idempotency_key=idempotency_key or f"worker-failed:{canonical_hash({'type': error_type, 'message': message, 'stage': stage_id})}",
            )
            if inserted:
                self.conn.execute(
                    "INSERT INTO error_details(run_id, stage_id, type, message, created_at) VALUES (?, ?, ?, ?, ?)",
                    (run_id, stage_id, error_type, redact(message), event.created_at),
                )
                self.conn.execute(
                    "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
                    (str(RunStatus.BLOCKED), event.created_at, run_id),
                )
            return event

    def reset_stage(self, run_id: str, stage_id: str) -> None:
        row = self.conn.execute(
            "SELECT role FROM stages WHERE run_id = ? AND stage_id = ?",
            (run_id, stage_id),
        ).fetchone()
        if row is None:
            return
        self.upsert_stage(
            run_id,
            stage_id,
            role=str(row["role"]) if row["role"] else None,
            status=StageStatus.PENDING,
            summary="retry requested",
        )

    def skip_stage(self, run_id: str, stage_id: str, reason: str = "skip requested") -> None:
        row = self.conn.execute(
            "SELECT role FROM stages WHERE run_id = ? AND stage_id = ?",
            (run_id, stage_id),
        ).fetchone()
        self.upsert_stage(
            run_id,
            stage_id,
            role=str(row["role"]) if row and row["role"] else None,
            status=StageStatus.SKIPPED,
            summary=reason,
        )

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
        if table == "evidence_events":
            for row in rows:
                row["artifact_refs"] = _json_list(row.get("artifact_refs_json"))
                row["metrics"] = _json_dict(row.get("metrics_json"))
                row["tags"] = _json_list(row.get("tags_json"))
                row["id"] = row.get("event_id")
        if table == "evidence_manifests":
            for row in rows:
                row["required_matrix"] = _json_dict(row.get("required_matrix_json"))
                row["missing_required"] = _json_list(row.get("missing_required_json"))
        if table == "evidence_evaluations":
            for row in rows:
                row["gates"] = _json_dict(row.get("gates_json"))
                row["components"] = _json_dict(row.get("components_json"))
                row["reasons"] = _json_list(row.get("reasons_json"))
                row["missing_evidence"] = _json_list(row.get("missing_evidence_json"))
                row["next_actions"] = _json_list(row.get("next_actions_json"))
        if table == "task_feature_sets":
            for row in rows:
                row["payload"] = _json_dict(row.get("payload_json"))
        if table == "route_decisions":
            rows = [self._route_decision_row(row) or {} for row in rows]
        if table == "review_assignments":
            rows = [self._review_row(row) or {} for row in rows]
        return rows


def _certification_row(row: Any | None) -> dict[str, Any] | None:
    if row is None:
        return None
    payload = dict(row)
    payload["capabilities"] = _json_dict(payload.pop("capabilities_json", "{}"))
    payload["evidence"] = _json_dict(payload.pop("evidence_json", "{}"))
    return payload


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


def _provider_action_input_kind(kind: str, choices: list[dict[str, Any]] | None = None) -> str:
    choices = choices or []
    if kind == str(ProviderActionKind.CLARIFICATION_REQUIRED):
        return "text" if not choices else "choice"
    if kind == str(ProviderActionKind.CLI_CONFIRMATION):
        return "confirmation"
    if choices:
        return "choice"
    if kind in {str(ProviderActionKind.AUTH_REQUIRED), str(ProviderActionKind.RATE_LIMIT), str(ProviderActionKind.IDLE_TIMEOUT)}:
        return "external"
    return "text"


def _default_choice(choices: list[dict[str, Any]] | None = None) -> str | None:
    for choice in choices or []:
        if choice.get("default") and choice.get("value") is not None:
            return str(choice["value"])
    return None


class TraceWriter:
    def __init__(self, run_dir: Path, run_id: str):
        self.path = run_dir / "trace.jsonl"
        self.run_id = run_id

    def write(self, event_type: str, *, stage: str | None = None, **data: Any) -> None:
        event = TraceEvent(type=event_type, run_id=self.run_id, stage=stage, data=data)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(redact(json.dumps(event.model_dump(), ensure_ascii=False)) + "\n")
