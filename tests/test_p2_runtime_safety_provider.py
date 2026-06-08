from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from muxdev.api.web import create_app
from muxdev.daemon.paths import default_daemon_paths
from muxdev.daemon.tasks import TaskManager
from muxdev.models import ProviderActionStatus, RunStatus
from muxdev.providers.adapters import ProviderStageOutput
from muxdev.runtime import SupervisorRuntime
from muxdev.storage import Blackboard


def test_read_only_stage_write_violation_blocks_and_writes_capsule(monkeypatch) -> None:
    workspace = _workspace_temp("p2-readonly")
    workflow = workspace / "readonly.yaml"
    workflow.write_text(
        """
name: readonly-smoke
max_parallel: 1
stages:
  - id: inspect
    role: review
    deps: []
    read_only: true
""",
        encoding="utf-8",
    )

    class MutatingProvider:
        def run_stage(self, *, stage_id: str, task: str, worktree: Path, skills=None) -> ProviderStageOutput:
            (worktree / "unexpected.txt").write_text("read-only mutation\n", encoding="utf-8")
            return ProviderStageOutput("inspect.md", "inspected", "inspected")

    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", lambda name: MutatingProvider())

    try:
        result = SupervisorRuntime(workspace).run("read only safety", provider="mock", workflow_name=str(workflow))

        assert result.status == RunStatus.BLOCKED
        with Blackboard(result.run_dir) as blackboard:
            errors = blackboard.table_rows("error_details", run_id=result.run_id)
            attempts = blackboard.table_rows("provider_attempts", run_id=result.run_id)
            capsules = blackboard.table_rows("session_capsules", run_id=result.run_id)
            stages = {row["stage_id"]: row["status"] for row in blackboard.table_rows("stages", run_id=result.run_id)}
        assert errors[0]["type"] == "read_only_write_violation"
        assert attempts[0]["status"] == "read_only_violation"
        assert capsules[0]["kind"] == "read_only_write_violation"
        assert Path(capsules[0]["path"]).exists()
        assert stages["inspect"] == "failed"
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_read_only_stage_allows_provider_session_archives_outside_worktree(monkeypatch) -> None:
    workspace = _workspace_temp("p2-readonly-session")
    workflow = workspace / "readonly_session.yaml"
    workflow.write_text(
        """
name: readonly-session-smoke
max_parallel: 1
stages:
  - id: inspect
    role: review
    deps: []
    read_only: true
""",
        encoding="utf-8",
    )

    class SessionArchivingProvider:
        def run_stage(
            self,
            *,
            stage_id: str,
            task: str,
            worktree: Path,
            skills=None,
            session_dir: Path | None = None,
        ) -> ProviderStageOutput:
            assert session_dir is not None
            session_dir.mkdir(parents=True, exist_ok=True)
            (session_dir / f"{stage_id}.log").write_text("session archive only\n", encoding="utf-8")
            return ProviderStageOutput("inspect.md", "inspected", "inspected")

    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", lambda name: SessionArchivingProvider())

    try:
        result = SupervisorRuntime(workspace).run("read only session archive", provider="mock", workflow_name=str(workflow))

        assert result.status == RunStatus.COMPLETED
        assert (result.run_dir / "provider_sessions" / "inspect.log").exists()
        assert not (result.run_dir / "worktree" / ".muxdev" / "provider_sessions").exists()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_read_only_stage_ignores_legacy_worktree_provider_session_archives(monkeypatch) -> None:
    workspace = _workspace_temp("p2-readonly-legacy-session")
    workflow = workspace / "readonly_legacy_session.yaml"
    workflow.write_text(
        """
name: readonly-legacy-session-smoke
max_parallel: 1
stages:
  - id: inspect
    role: review
    deps: []
    read_only: true
""",
        encoding="utf-8",
    )

    class LegacySessionArchivingProvider:
        def run_stage(self, *, stage_id: str, task: str, worktree: Path, skills=None) -> ProviderStageOutput:
            archive_dir = worktree / ".muxdev" / "provider_sessions"
            archive_dir.mkdir(parents=True, exist_ok=True)
            (archive_dir / f"{stage_id}.log").write_text("legacy session archive only\n", encoding="utf-8")
            return ProviderStageOutput("inspect.md", "inspected", "inspected")

    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", lambda name: LegacySessionArchivingProvider())

    try:
        result = SupervisorRuntime(workspace).run("read only legacy session archive", provider="mock", workflow_name=str(workflow))
        diff = (result.run_dir / "diff.patch").read_text(encoding="utf-8")

        assert result.status == RunStatus.COMPLETED
        assert "provider_sessions" not in diff
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_transient_provider_exit_retries_and_records_attempts(monkeypatch) -> None:
    workspace = _workspace_temp("p2-retry")
    workflow = workspace / "retry.yaml"
    workflow.write_text(
        """
name: retry-smoke
max_parallel: 1
stages:
  - id: code
    role: code
    deps: []
""",
        encoding="utf-8",
    )

    class FlakyProvider:
        def __init__(self) -> None:
            self.calls = 0

        def run_stage(self, *, stage_id: str, task: str, worktree: Path, skills=None) -> ProviderStageOutput:
            self.calls += 1
            if self.calls == 1:
                return ProviderStageOutput("session/code.log", "temporary network error", "temporary network error", returncode=1)
            return ProviderStageOutput("session/code.log", "ok", "ok")

    provider = FlakyProvider()
    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", lambda name: provider)

    try:
        result = SupervisorRuntime(workspace).run("retry transient provider", provider="mock", workflow_name=str(workflow))

        assert result.status == RunStatus.COMPLETED
        assert provider.calls == 2
        with Blackboard(result.run_dir) as blackboard:
            attempts = blackboard.table_rows("provider_attempts", run_id=result.run_id)
        assert [row["status"] for row in attempts] == ["retried", "succeeded"]
        assert attempts[0]["failure_kind"] == "transient_provider_exit"
        assert "provider_retry_scheduled" in (result.run_dir / "trace.jsonl").read_text(encoding="utf-8")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_provider_action_writes_session_capsule_and_attempt(monkeypatch) -> None:
    workspace = _workspace_temp("p2-action-capsule")

    class AuthProvider:
        def run_stage(self, *, stage_id: str, task: str, worktree: Path, skills=None) -> ProviderStageOutput:
            return ProviderStageOutput("session/auth.log", "Please sign in to continue.", "auth required", returncode=1)

    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", lambda name: AuthProvider())

    try:
        result = SupervisorRuntime(workspace).run("auth action capsule", provider="mock")

        assert result.status == RunStatus.AWAITING_PROVIDER_ACTION
        with Blackboard(result.run_dir) as blackboard:
            actions = blackboard.list_provider_actions(status=str(ProviderActionStatus.PENDING), run_id=result.run_id)
            attempts = blackboard.table_rows("provider_attempts", run_id=result.run_id)
            capsules = blackboard.table_rows("session_capsules", run_id=result.run_id)
        assert actions[0]["kind"] == "auth_required"
        assert attempts[0]["status"] == "provider_action"
        assert attempts[0]["capsule_path"] == capsules[0]["path"]
        assert Path(capsules[0]["path"]).read_text(encoding="utf-8").find("auth_required") != -1
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_provider_score_api_aggregates_attempts() -> None:
    workspace = _workspace_temp("p2-provider-score")
    try:
        paths = default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure()
        manager = TaskManager(paths=paths)
        with manager.board() as board:
            board.create_run(run_id="run_score", task="score", workflow="dev", provider="mock", workspace=workspace, worktree=workspace / "worktree")
            board.start_provider_attempt("run_score", "code", provider="mock", role="code", attempt=1)
            board.complete_provider_attempt("run_score", "code", provider="mock", attempt=1, status="succeeded", returncode=0, summary="ok")
        client = TestClient(create_app(task_manager=manager))

        rows = client.get("/api/provider-scores?role=code").json()

        assert rows[0]["provider"] == "mock"
        assert rows[0]["role"] == "code"
        assert rows[0]["attempts"] == 1
        assert rows[0]["success_rate"] == 1.0
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _workspace_temp(prefix: str) -> Path:
    path = Path(".test_workspaces") / f"{prefix}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()
