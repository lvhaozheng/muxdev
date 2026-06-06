from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest

from muxdev.models import ApprovalStatus, RunStatus
from muxdev.runtime import SupervisorRuntime
from muxdev.providers.adapters import ProviderStageOutput
from muxdev.storage import Blackboard


@pytest.fixture()
def workspace() -> Path:
    path = Path(".test_workspaces") / f"runtime_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def test_mock_run_creates_m1_artifacts(workspace: Path) -> None:
    result = SupervisorRuntime(workspace).run("add rate limiting", provider="mock")

    assert result.status == RunStatus.COMPLETED
    assert (result.run_dir / "blackboard.sqlite").exists()
    assert (result.run_dir / "trace.jsonl").exists()
    assert (result.run_dir / "final_report.md").exists()
    assert (result.run_dir / "diff.patch").read_text(encoding="utf-8")
    assert (result.run_dir / "worktree" / ".git" / "config").exists()

    trace_types = [
        json.loads(line)["type"]
        for line in (result.run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert "run_started" in trace_types
    assert "stage_started" in trace_types
    assert "stage_completed" in trace_types
    assert "run_completed" in trace_types


def test_blackboard_schema_records_run_stage_approval_and_results(workspace: Path) -> None:
    result = SupervisorRuntime(workspace).run("exercise blackboard", provider="mock")
    blackboard = Blackboard(result.run_dir)
    try:
        run = blackboard.get_run(result.run_id)
        assert run["status"] == "completed"
        assert blackboard.table_rows("stages")
        approvals = blackboard.table_rows("approvals")
        assert {approval["type"] for approval in approvals} >= {"plan", "write", "shell", "merge"}
        assert {approval["status"] for approval in approvals} == {"approved"}
        assert blackboard.table_rows("test_results")[0]["passed"] == 1
        assert blackboard.table_rows("checkpoints")
        assert blackboard.table_rows("usage_records")
    finally:
        blackboard.close()


def test_run_can_pause_for_plan_approval(workspace: Path) -> None:
    result = SupervisorRuntime(workspace).run(
        "needs human gate",
        provider="mock",
        require_approval={"plan"},
    )

    assert result.status == RunStatus.AWAITING_APPROVAL
    blackboard = Blackboard(result.run_dir)
    try:
        approvals = blackboard.list_approvals(status="pending")
        assert len(approvals) == 1
        blackboard.decide_approval(approvals[0]["approval_id"], ApprovalStatus.APPROVED)
        assert blackboard.list_approvals(status="approved")
    finally:
        blackboard.close()


def test_resume_continues_after_approved_gate(workspace: Path) -> None:
    runtime = SupervisorRuntime(workspace)
    paused = runtime.run(
        "resume after human gate",
        provider="mock",
        require_approval={"plan"},
    )
    blackboard = Blackboard(paused.run_dir)
    try:
        approval = blackboard.list_approvals(status="pending")[0]
        blackboard.decide_approval(approval["approval_id"], ApprovalStatus.APPROVED)
    finally:
        blackboard.close()

    resumed = runtime.resume(paused.run_id)

    assert resumed.status == RunStatus.COMPLETED
    assert resumed.report_path is not None
    trace = (resumed.run_dir / "trace.jsonl").read_text(encoding="utf-8")
    assert "run_resumed" in trace


def test_retry_resets_stage_and_resumes(workspace: Path) -> None:
    runtime = SupervisorRuntime(workspace)
    result = runtime.run("retry a completed stage", provider="mock")

    retried = runtime.retry(result.run_id, "review")

    assert retried.status == RunStatus.COMPLETED
    blackboard = Blackboard(result.run_dir)
    try:
        stages = {row["stage_id"]: row for row in blackboard.table_rows("stages")}
        assert stages["review"]["status"] == "completed"
    finally:
        blackboard.close()


def test_budget_limit_pauses_run(workspace: Path) -> None:
    result = SupervisorRuntime(workspace).run("tiny budget", provider="mock", max_cost_usd=0)

    assert result.status == RunStatus.PAUSED_BUDGET


def test_run_artifacts_redact_secrets(workspace: Path) -> None:
    result = SupervisorRuntime(workspace).run("secret sk-abc123 Bearer token.value", provider="mock")

    report = (result.run_dir / "final_report.md").read_text(encoding="utf-8")
    trace = (result.run_dir / "trace.jsonl").read_text(encoding="utf-8")
    assert "sk-abc123" not in report
    assert "Bearer token.value" not in report
    assert "sk-abc123" not in trace
    assert "Bearer token.value" not in trace
    assert "[REDACTED]" in report


def test_parallel_workflow_batches_independent_stages(workspace: Path) -> None:
    workflow = workspace / "parallel.yaml"
    workflow.write_text(
        """
name: parallel-smoke
max_parallel: 2
stages:
  - id: alpha
    role: implementer
    deps: []
  - id: beta
    role: tester
    deps: []
  - id: done
    role: reviewer
    deps: [alpha, beta]
""",
        encoding="utf-8",
    )

    result = SupervisorRuntime(workspace).run("parallel smoke", provider="mock", workflow_name=str(workflow))

    assert result.status == RunStatus.COMPLETED
    trace = (result.run_dir / "trace.jsonl").read_text(encoding="utf-8")
    assert "parallel_batch_started" in trace
    blackboard = Blackboard(result.run_dir)
    try:
        stages = {row["stage_id"]: row["status"] for row in blackboard.table_rows("stages")}
    finally:
        blackboard.close()
    assert stages == {"alpha": "completed", "beta": "completed", "done": "completed"}


def test_review_blockers_drive_fix_loop(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    class FlakyReviewProvider:
        def __init__(self) -> None:
            self.review_count = 0

        def run_stage(self, *, stage_id: str, task: str, worktree: Path) -> ProviderStageOutput:
            if stage_id == "review":
                self.review_count += 1
                if self.review_count == 1:
                    return ProviderStageOutput(
                        "review.md",
                        """
```json
{"has_blockers": true, "blockers": [{"type": "bug", "file": "x.py", "line": 1, "severity": "high", "suggestion": "fix it"}]}
```
""",
                        "blockers found",
                    )
                return ProviderStageOutput("review.md", '{"has_blockers": false, "blockers": []}', "no blockers")
            if stage_id == "fix":
                return ProviderStageOutput("session/fix.log", "fixed blocker", "fixed blocker")
            return ProviderStageOutput(f"{stage_id}.md", "{}", f"{stage_id} ok")

    provider = FlakyReviewProvider()
    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", lambda name: provider)

    result = SupervisorRuntime(workspace).run("fix loop smoke", provider="mock")

    assert result.status == RunStatus.COMPLETED
    trace = (result.run_dir / "trace.jsonl").read_text(encoding="utf-8")
    assert "fix_loop_iteration" in trace
    blackboard = Blackboard(result.run_dir)
    try:
        blockers = blackboard.table_rows("review_blockers")
        stages = {row["stage_id"]: row["status"] for row in blackboard.table_rows("stages")}
    finally:
        blackboard.close()
    assert blockers[0]["suggestion"] == "fix it"
    assert stages["fix"] == "skipped"


def test_external_confirmation_output_creates_pending_approval(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    class ExternalPromptProvider:
        def run_stage(self, *, stage_id: str, task: str, worktree: Path) -> ProviderStageOutput:
            return ProviderStageOutput("session/external.log", "waiting_external_confirmation: continue?", "needs confirmation")

    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", lambda name: ExternalPromptProvider())

    result = SupervisorRuntime(workspace).run("external approval smoke", provider="mock")

    assert result.status == RunStatus.AWAITING_APPROVAL
    blackboard = Blackboard(result.run_dir)
    try:
        approvals = blackboard.list_approvals(status="pending")
    finally:
        blackboard.close()
    assert approvals[0]["type"] == "external"
