from __future__ import annotations

import json
import os
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from muxdev.api.web import create_app, render_dashboard_html, render_live_dashboard_html
from muxdev.cli import app
from muxdev.daemon.paths import default_daemon_paths
from muxdev.daemon.tasks import TaskManager
from muxdev.models import RunStatus
from muxdev.providers.adapters import ProviderStageOutput
from muxdev.runtime import SupervisorRuntime
from muxdev.services.advanced_parallel import detect_parallel_conflicts, record_parallel_conflicts
from muxdev.services.multirepo import plan_multi_repo_orchestration
from muxdev.services.provider_learning import refresh_provider_learning
from muxdev.services.semantic_merge import review_semantic_merge
from muxdev.storage import Blackboard, MemoryStore
from muxdev.ui.tui import daemon_help_text


runner = CliRunner()


def test_parallel_conflict_detection_and_recording() -> None:
    workspace = _workspace_temp("p4-parallel")
    try:
        stage_writes = {"code_a": ["src/auth.py", "docs/plan.md"], "code_b": ["src/auth.py"], "docs": ["docs/plan.md"]}
        conflicts = detect_parallel_conflicts(stage_writes)

        assert {conflict["severity"] for conflict in conflicts} == {"high", "medium"}

        with Blackboard(workspace / ".muxdev", db_path=workspace / ".muxdev" / "ecosystem.sqlite") as board:
            rows = record_parallel_conflicts(board, run_id="run_p4", stage_id="parallel_batch", stage_writes=stage_writes)
            listed = board.list_parallel_conflicts(run_id="run_p4")

        assert len(rows) == 2
        assert listed[0]["conflict_id"].startswith("pcf_")
        assert any("src/auth.py" in row["files"] for row in listed)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_semantic_merge_reviewer_blocks_unresolved_conflict_markers(monkeypatch) -> None:
    workspace = _workspace_temp("p4-semantic-runtime")
    workflow = workspace / "semantic.yaml"
    workflow.write_text(
        """
name: semantic-smoke
max_parallel: 1
stages:
  - id: code
    role: code
    deps: []
    allow_write: true
""",
        encoding="utf-8",
    )

    class ConflictProvider:
        def run_stage(self, *, stage_id: str, task: str, worktree: Path, skills=None) -> ProviderStageOutput:
            (worktree / "app.py").write_text("<<<<<<< ours\nx = 1\n=======\nx = 2\n>>>>>>> theirs\n", encoding="utf-8")
            return ProviderStageOutput("code.md", "wrote conflict markers", "ok")

    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", lambda name: ConflictProvider())

    try:
        result = SupervisorRuntime(workspace).run("merge conflict smoke", provider="mock", workflow_name=str(workflow))

        assert result.status == RunStatus.BLOCKED
        with Blackboard(result.run_dir) as board:
            reviews = board.list_semantic_merge_reviews(run_id=result.run_id)
            errors = board.table_rows("error_details", run_id=result.run_id)
        assert reviews[0]["decision"] == "reject"
        assert errors[0]["type"] == "semantic_merge_reject"
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_semantic_merge_service_accepts_medium_only_findings() -> None:
    workspace = _workspace_temp("p4-semantic-service")
    try:
        run_dir = workspace / ".muxdev" / "runs" / "run_p4"
        path, digest, payload = review_semantic_merge(
            run_dir,
            run_id="run_p4",
            task="change auth copy",
            patch_text="diff --git a/src/auth.py b/src/auth.py\n+++ b/src/auth.py\n+TOKEN = 'redacted'\n",
        )

        assert path.exists()
        assert digest.startswith("sha256:")
        assert payload["decision"] == "accept"
        assert payload["findings"][0]["type"] == "sensitive_change_without_tests"
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_provider_learning_snapshot_persists_cross_run_scores() -> None:
    workspace = _workspace_temp("p4-learning")
    try:
        with Blackboard(workspace / ".muxdev", db_path=workspace / ".muxdev" / "ecosystem.sqlite") as board:
            board.create_run(run_id="run_score", task="score", workflow="dev", provider="mock", workspace=workspace, worktree=workspace / "worktree")
            board.start_provider_attempt("run_score", "code", provider="mock", role="code", attempt=1)
            board.complete_provider_attempt("run_score", "code", provider="mock", attempt=1, status="succeeded", returncode=0, summary="ok")
            board.create_provider_action(run_id="run_score", stage_id="review", provider="mock", role="review", kind="cli_confirmation", prompt_text="Approve? [y/N]")
            rows = refresh_provider_learning(board)

        assert any(row["provider"] == "mock" and row["role"] == "code" and row["successes"] == 1 for row in rows)
        assert any(row["provider"] == "mock" and row["role"] == "review" and row["human_actions"] == 1 for row in rows)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_memory_contradiction_detection_auto_quarantines_lower_trust_item() -> None:
    workspace = _workspace_temp("p4-memory")
    try:
        with MemoryStore(workspace) as store:
            active = store.propose_claim(claim="Use pytest for tests", kind="project_convention", role="test", confidence=0.9)
            store.approve(str(active["id"]))
            proposed = store.propose_claim(claim="Do not use pytest for tests", kind="project_convention", role="test", confidence=0.4)

            found = store.detect_contradictions()
            quarantined = store.auto_quarantine_contradictions()

            assert found
            assert quarantined[0]["quarantine_target"] == proposed["id"]
            assert store.get(str(proposed["id"]))["status"] == "quarantined"
            assert store.get(str(active["id"]))["status"] == "active"
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_multi_repo_orchestration_plan_records_repos() -> None:
    workspace = _workspace_temp("p4-multirepo")
    repo_a = workspace / "repo-a"
    repo_b = workspace / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    (repo_a / "pyproject.toml").write_text("[project]\nname='a'\n", encoding="utf-8")
    try:
        with Blackboard(workspace / ".muxdev", db_path=workspace / ".muxdev" / "ecosystem.sqlite") as board:
            payload = plan_multi_repo_orchestration(workspace, repos=[repo_a, repo_b], task="coordinate API change", mode="dev", blackboard=board)
            rows = board.list_multi_repo_orchestrations()

        assert Path(str(payload["plan_path"])).exists()
        assert payload["repos"][0]["workflow"] == "dev"
        assert rows[0]["orchestration_id"] == payload["orchestration_id"]
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_p4_api_and_dashboards_expose_advanced_state() -> None:
    workspace = _workspace_temp("p4-api")
    try:
        manager = TaskManager(paths=default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure())
        with manager.board() as board:
            board.create_run(run_id="run_p4", task="p4", workflow="dev", provider="mock", workspace=workspace, worktree=workspace / "worktree")
            board.add_parallel_conflict(run_id="run_p4", stage_id="parallel_batch", stages=["a", "b"], files=["src/app.py"], severity="high")
            review_path = workspace / "semantic.json"
            review_path.write_text("{}", encoding="utf-8")
            board.add_semantic_merge_review(run_id="run_p4", decision="accept", patch_hash="sha256:abc", findings=[], path=review_path)
            board.upsert_provider_learning(provider="mock", role="code", run_id="run_p4", attempts=1, successes=1, failures=0, human_actions=0, score=1.0)
            board.add_multi_repo_orchestration(orchestration_id="mrepo_1", run_id=None, workspace=workspace, mode="design", task="plan", status="planned", repos=[])
        client = TestClient(create_app(task_manager=manager))

        assert client.get("/api/parallel-conflicts").json()[0]["severity"] == "high"
        assert client.get("/api/tasks/run_p4/semantic-merge-reviews").json()[0]["decision"] == "accept"
        assert client.get("/api/learning/provider").json()[0]["provider"] == "mock"
        assert client.get("/api/multi-repo/orchestrations").json()[0]["orchestration_id"] == "mrepo_1"

        html = render_dashboard_html(
            {
                "app": {"workspace": "workspace"},
                "run": {"run_id": "run_p4", "task": "p4", "status": "running"},
                "summary": {},
                "stages": [],
                "agents": [],
                "approvals": [],
                "provider_actions": [],
                "provider_attempts": [],
                "session_capsules": [],
                "feedback_events": [],
                "ci_rescues": [],
                "cache_entries": [],
                "skill_locks": [],
                "guardrail_events": [],
                "parallel_conflicts": [{"conflict_id": "pcf_1", "severity": "high"}],
                "semantic_merge_reviews": [{"review_id": "smr_1", "decision": "accept"}],
                "provider_learning": [{"provider": "mock", "role": "code", "score": 1.0}],
                "multi_repo_orchestrations": [{"orchestration_id": "mrepo_1", "mode": "design"}],
                "memory_context": [],
                "test_results": [],
                "review_blockers": [],
                "errors": [],
                "artifacts": [],
                "usage": [],
                "trace": [],
            }
        )
        live = render_live_dashboard_html()

        assert "Advanced Parallel" in html
        assert "Semantic Merge" in html
        assert "Provider Learning" in html
        assert "Parallel Conflicts" in live
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_p4_cli_and_tui_surface_commands() -> None:
    workspace = _workspace_temp("p4-cli")
    try:
        plan = workspace / "writes.json"
        plan.write_text(json.dumps({"a": ["src/app.py"], "b": ["src/app.py"]}), encoding="utf-8")
        repo = workspace / "repo"
        repo.mkdir()
        with _chdir(workspace):
            conflicts = runner.invoke(app, ["parallel", "conflicts", "--file", str(plan), "--json"])
            multirepo = runner.invoke(app, ["multirepo", "plan", "coordinate release", "--repo", str(repo), "--mode", "design", "--json"])

        assert conflicts.exit_code == 0
        assert json.loads(conflicts.stdout)["conflicts"][0]["severity"] == "high"
        assert multirepo.exit_code == 0
        assert json.loads(multirepo.stdout)["mode"] == "design"
        assert "/dev <task>" in daemon_help_text()
        assert "/design <task>" in daemon_help_text()
        assert "/parallel" in daemon_help_text()
        assert "/learning" in daemon_help_text()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _workspace_temp(prefix: str) -> Path:
    path = Path(".test_workspaces") / f"{prefix}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


@contextmanager
def _chdir(path: Path):
    cwd = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(cwd)
