from __future__ import annotations

import json
import os
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

from rich.console import Console
from typer.testing import CliRunner

from muxdev.cli import app
from muxdev.models import RunStatus
from muxdev.providers.adapters import HeadlessCliProviderAdapter, ProviderStageOutput
from muxdev.runtime import SupervisorRuntime
from muxdev.services.evidence import verify_run_evidence
from muxdev.services.dashboard import build_run_dashboard_payload
from muxdev.storage import Blackboard
from muxdev.ui.tui import status_panel


runner = CliRunner()


def test_completed_run_writes_evidence_scorecard_artifacts() -> None:
    workspace = _workspace_temp("scorecard-basic")
    try:
        with _chdir(workspace):
            result = SupervisorRuntime(workspace).run("scorecard trusted delivery smoke", provider="mock")
            evidence = runner.invoke(app, ["evidence", "latest", "--json"])

        scorecard_path = result.run_dir / "evidence" / "scorecard.json"
        coverage_path = result.run_dir / "evidence" / "coverage_matrix.json"
        summary_path = result.run_dir / "evidence" / "human_summary.md"
        scorecard = json.loads(scorecard_path.read_text(encoding="utf-8"))

        assert result.status == RunStatus.COMPLETED
        assert scorecard["contract_version"] == "muxdev.evidence_scorecard.v1"
        assert scorecard["score"] >= 75
        assert scorecard["label"] in {"ready", "reviewable"}
        assert coverage_path.exists()
        assert "Delivery Confidence" in summary_path.read_text(encoding="utf-8")
        assert evidence.exit_code == 0
        payload = json.loads(evidence.stdout)
        assert payload["scorecard"]["run_id"] == result.run_id
        with Blackboard(result.run_dir) as blackboard:
            verified = verify_run_evidence(result.run_dir, result.run_id, blackboard)
            dashboard_payload = build_run_dashboard_payload(workspace, result.run_dir, result.run_id, blackboard)
            assert verified["valid"] is True
            assert verified["scorecards"] == 1
            assert blackboard.table_rows("evidence_items", run_id=result.run_id)
            assert dashboard_payload["evidence_scorecard"]["score"] == scorecard["score"]
            console = Console(record=True, width=120)
            console.print(status_panel({**dashboard_payload, "context": {}}))
            assert "Delivery Scorecard" in console.export_text()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_scorecard_valid_hashes_but_missing_tests_is_not_high_confidence(monkeypatch) -> None:
    workspace = _workspace_temp("scorecard-missing-tests")
    workflow = workspace / "inspect_only.yaml"
    workflow.write_text(
        """
name: inspect-only
max_parallel: 1
stages:
  - id: inspect
    role: review
    deps: []
    read_only: true
""",
        encoding="utf-8",
    )

    class InspectProvider:
        def run_stage(self, *, stage_id: str, task: str, worktree: Path, skills=None) -> ProviderStageOutput:
            return ProviderStageOutput("inspect.md", "inspection only", "inspection only")

    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", lambda name: InspectProvider())
    try:
        result = SupervisorRuntime(workspace).run("inspect without running tests", provider="mock", workflow_name=str(workflow))
        scorecard = json.loads((result.run_dir / "evidence" / "scorecard.json").read_text(encoding="utf-8"))
        with Blackboard(result.run_dir) as blackboard:
            verified = verify_run_evidence(result.run_dir, result.run_id, blackboard)

        assert verified["valid"] is True
        assert scorecard["score"] < 75
        assert "targeted tests not recorded" in scorecard["missing_evidence"]
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_high_review_blocker_makes_scorecard_blocked(monkeypatch) -> None:
    workspace = _workspace_temp("scorecard-blocker")
    workflow = workspace / "review_only.yaml"
    workflow.write_text(
        """
name: review-only
max_parallel: 1
stages:
  - id: review
    role: review
    deps: []
    read_only: true
""",
        encoding="utf-8",
    )

    class BlockingReviewProvider:
        def run_stage(self, *, stage_id: str, task: str, worktree: Path, skills=None) -> ProviderStageOutput:
            return ProviderStageOutput(
                "review.md",
                '{"has_blockers": true, "blockers": [{"type": "bug", "file": "x.py", "line": 1, "severity": "high", "suggestion": "fix blocker"}]}',
                "blocker found",
            )

    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", lambda name: BlockingReviewProvider())
    try:
        result = SupervisorRuntime(workspace).run("review with high blocker", provider="mock", workflow_name=str(workflow))
        scorecard = json.loads((result.run_dir / "evidence" / "scorecard.json").read_text(encoding="utf-8"))

        assert result.status == RunStatus.BLOCKED
        assert scorecard["label"] == "blocked"
        assert scorecard["recommendation"] == "blocked"
        assert "high review blocker must be resolved" in scorecard["missing_evidence"]
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_provider_prompt_requests_structured_evidence() -> None:
    adapter = HeadlessCliProviderAdapter("mock-cli", ["mock"], timeout=1, prompt_template="Stage {stage_id}: {task}")

    prompt = adapter._prompt("code", "add rate limiting")

    assert "muxdev Evidence Contract" in prompt
    assert "missing_evidence" in prompt
    assert "Model-only judgments must use strength \"D\"" in prompt


def _workspace_temp(prefix: str) -> Path:
    path = Path(".test_workspaces") / f"{prefix}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


@contextmanager
def _chdir(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)
