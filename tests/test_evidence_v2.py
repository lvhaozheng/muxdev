from __future__ import annotations

import json
import os
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

from rich.console import Console
from typer.testing import CliRunner
import pytest

from muxdev.cli import app
from muxdev.models import RunStatus
from muxdev.domain import StageExecutionInput, StageExecutionResult
from muxdev.providers.adapters import HeadlessCliProviderAdapter
from muxdev.runtime import SupervisorRuntime
from muxdev.services.dashboard_run import build_run_dashboard_payload
from muxdev.services.evidence import verify_run_evidence
from muxdev.storage import Blackboard
from muxdev.presentation import status_panel


pytestmark = pytest.mark.integration


runner = CliRunner()


def test_completed_run_writes_evidence_v2_artifacts() -> None:
    workspace = _workspace_temp("evidence-v2-basic")
    try:
        with _chdir(workspace):
            result = SupervisorRuntime(workspace).run("evidence v2 trusted delivery smoke", provider="mock")
            evidence = runner.invoke(app, ["evidence", "latest", "--json"])

        events_path = result.run_dir / "evidence" / "events.jsonl"
        manifest_path = result.run_dir / "evidence" / "manifest.json"
        evaluation_path = result.run_dir / "evidence" / "evaluation.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        evaluation = json.loads(evaluation_path.read_text(encoding="utf-8"))
        events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]

        assert result.status == RunStatus.COMPLETED
        assert events_path.exists()
        assert manifest["contract_version"] == "muxdev.evidence.v2"
        assert evaluation["contract_version"] == "muxdev.evidence_evaluation.v2"
        assert evaluation["label"] in {"ready", "reviewable"}
        assert evaluation["standard_scores"]["max_evidence"] in {"E2", "E3"}
        assert evaluation["standard_scores"]["meets_minimum"] is True
        assert any(event["evidence_level"] in {"E2", "E3"} for event in events)
        test_events = [event for event in events if event["kind"] == "test" and event["status"] == "passed"]
        assert test_events and test_events[-1]["metrics"]["exit_code"] == 0
        assert test_events[-1]["metrics"]["result_contract_valid"] is True
        assert any(ref.get("producer") == "result_validation" for ref in test_events[-1]["artifact_refs"])
        assert all({"standard_id", "severity", "risk_level", "evidence_level"} <= set(event) for event in events)
        assert "memory_safety" not in evaluation["components"]
        assert not (result.run_dir / "evidence" / "scorecard.json").exists()
        assert not (result.run_dir / "evidence" / "coverage_matrix.json").exists()
        assert not list((result.run_dir / "evidence").glob("*.evidence.json"))
        assert evidence.exit_code == 0
        payload = json.loads(evidence.stdout)
        assert payload["evaluation"]["run_id"] == result.run_id
        with Blackboard(result.run_dir) as blackboard:
            verified = verify_run_evidence(result.run_dir, result.run_id, blackboard)
            dashboard_payload = build_run_dashboard_payload(workspace, result.run_dir, result.run_id, blackboard)
            assert verified["valid"] is True
            assert verified["events"] == manifest["event_count"]
            assert blackboard.table_rows("evidence_events", run_id=result.run_id)
            assert dashboard_payload["evidence_evaluation"]["label"] == evaluation["label"]
            console = Console(record=True, width=120)
            console.print(status_panel({**dashboard_payload, "context": {}}))
            assert "Evidence Evaluation" in console.export_text()
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_missing_tests_blocks_evidence_v2_evaluation(monkeypatch) -> None:
    workspace = _workspace_temp("evidence-v2-missing-tests")
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
        def execute(self, input: StageExecutionInput) -> StageExecutionResult:
            _stage_id, _task, _worktree, _skills = input.stage_id, input.task, input.worktree, list(input.skills)
            return StageExecutionResult("inspect.md", "inspection only", "inspection only")

    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", lambda name: InspectProvider())
    try:
        result = SupervisorRuntime(workspace).run("inspect without running tests", provider="mock", workflow_name=str(workflow))
        evaluation = json.loads((result.run_dir / "evidence" / "evaluation.json").read_text(encoding="utf-8"))

        assert evaluation["label"] == "blocked"
        assert "test" in evaluation["missing_evidence"]
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_high_review_blocker_makes_evidence_v2_blocked(monkeypatch) -> None:
    workspace = _workspace_temp("evidence-v2-blocker")
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
        def execute(self, input: StageExecutionInput) -> StageExecutionResult:
            _stage_id, _task, _worktree, _skills = input.stage_id, input.task, input.worktree, list(input.skills)
            return StageExecutionResult(
                "review.md",
                '{"has_blockers": true, "blockers": [{"type": "bug", "file": "x.py", "line": 1, "severity": "high", "suggestion": "fix blocker"}]}',
                "blocker found",
            )

    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", lambda name: BlockingReviewProvider())
    try:
        result = SupervisorRuntime(workspace).run("review with high blocker", provider="mock", workflow_name=str(workflow))
        evaluation = json.loads((result.run_dir / "evidence" / "evaluation.json").read_text(encoding="utf-8"))

        assert result.status == RunStatus.BLOCKED
        assert evaluation["label"] == "blocked"
        assert "high review blocker must be resolved" in evaluation["missing_evidence"]
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_provider_prompt_requests_structured_evidence_v2_events() -> None:
    adapter = HeadlessCliProviderAdapter("mock-cli", ["mock"], timeout=1, prompt_template="Stage {stage_id}: {task}")

    prompt = adapter._prompt("code", "add rate limiting")

    assert "muxdev Evidence v2 Contract" in prompt
    assert "missing_evidence" in prompt
    assert '"kind": "change"' in prompt
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
