from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path

import pytest

from muxdev.models import ApprovalStatus, ProviderActionStatus, RunStatus
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
    assert result.run_dir.parent == workspace / ".muxdev" / "runs"
    assert (result.run_dir / "blackboard.sqlite").exists()
    assert (result.run_dir / "trace.jsonl").exists()
    assert (result.run_dir / "final_report.md").exists()
    assert (result.run_dir / "diff.patch").read_text(encoding="utf-8")
    assert (result.run_dir / "worktree" / ".git" / "config").exists()
    user_design_doc = workspace / "docs" / "design" / f"{result.run_id}-design.md"
    assert user_design_doc.exists()
    assert "设计文档" in user_design_doc.read_text(encoding="utf-8")
    design_doc = result.run_dir / "worktree" / "docs" / "design" / f"{result.run_id}-design.md"
    assert design_doc.exists()
    assert "设计文档" in design_doc.read_text(encoding="utf-8")

    trace_types = [
        json.loads(line)["type"]
        for line in (result.run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert "run_started" in trace_types
    assert "stage_started" in trace_types
    assert "stage_completed" in trace_types
    assert "project_design_doc_written" in trace_types
    assert "run_completed" in trace_types

    blackboard = Blackboard(result.run_dir)
    try:
        design_artifacts = [row for row in blackboard.table_rows("artifacts") if row["kind"] == "project_design_doc"]
    finally:
        blackboard.close()
    assert design_artifacts[0]["name"] == "Design Document"
    design_artifact_path = Path(design_artifacts[0]["path"])
    assert design_artifact_path.name == f"{result.run_id}-design.md"
    assert design_artifact_path.parent.name == "design"
    assert design_artifact_path.parent.parent.name == "docs"
    assert design_artifact_path == user_design_doc
    assert "## Design Deliverables" in (result.run_dir / "final_report.md").read_text(encoding="utf-8")


def test_design_workflow_publishes_user_visible_design_document(workspace: Path) -> None:
    result = SupervisorRuntime(workspace).run("设计一个贪吃蛇游戏方案", provider="mock", workflow_name="design")

    assert result.status == RunStatus.COMPLETED
    user_design_doc = workspace / "docs" / "design" / f"{result.run_id}-design.md"
    assert user_design_doc.exists()
    content = user_design_doc.read_text(encoding="utf-8")
    assert "# 设计文档" in content
    assert "设计一个贪吃蛇游戏方案" in content
    assert "## 问题陈述" in content
    assert "## 最终设计评审" in content

    blackboard = Blackboard(result.run_dir)
    try:
        design_artifacts = [row for row in blackboard.table_rows("artifacts") if row["kind"] == "project_design_doc"]
    finally:
        blackboard.close()
    assert [Path(row["path"]) for row in design_artifacts] == [user_design_doc]


def test_design_doc_extracts_structured_payload_from_provider_stream(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    class StreamDesignProvider:
        def run_stage(self, *, stage_id: str, task: str, worktree: Path) -> ProviderStageOutput:
            payload = {
                "summary": "已完成贪吃蛇游戏设计。",
                "design_doc": {
                    "problem_statement": "设计一个轻量级贪吃蛇游戏。",
                    "user_preferences": {"visual_style": "像素风", "platform": "浏览器"},
                    "acceptance_criteria": ["可以开始、暂停和重新开始。", "撞墙后进入 gameOver。"],
                    "proposed_design": {
                        "platform": "Vanilla HTML/CSS/JavaScript + Canvas",
                        "modules": ["GameEngine", "Renderer", "InputController"],
                    },
                    "state_model": {"states": ["idle", "running", "paused", "gameOver"]},
                    "data_flow": ["用户输入 -> GameEngine.tick() -> Renderer.draw()"],
                    "implementation_sequence": ["搭建页面", "实现核心逻辑", "补充测试"],
                    "risks_and_mitigations": ["移动端手感需后续验证。"],
                },
            }
            event = {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(payload, ensure_ascii=False)}}
            content = "\n".join(
                [
                    "Reading prompt from stdin...",
                    json.dumps({"type": "thread.started", "thread_id": "thread_test"}, ensure_ascii=False),
                    json.dumps(event, ensure_ascii=False),
                    "",
                    "# Stream Events",
                    "output: " + json.dumps(event, ensure_ascii=False),
                    "",
                    "# Session Archives",
                    "transcript: transcript.log",
                    "chunks: chunks.jsonl",
                ]
            )
            return ProviderStageOutput("design/design_brief.md", content, "done")

    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", lambda name: StreamDesignProvider())

    result = SupervisorRuntime(workspace).run("设计一个贪吃蛇游戏", provider="mock", workflow_name="design-lite")

    assert result.status == RunStatus.COMPLETED
    content = (workspace / "docs" / "design" / f"{result.run_id}-design.md").read_text(encoding="utf-8")
    assert "# 设计文档" in content
    assert "像素风" in content
    assert "Vanilla HTML/CSS/JavaScript + Canvas" in content
    assert "## 验收标准" in content
    assert "## 状态模型" in content
    assert "Stream Events" not in content
    assert "Session Archives" not in content
    assert "transcript:" not in content
    assert '{"type":' not in content


def test_design_provider_question_pauses_and_response_reaches_next_context(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    class DesignQuestionProvider:
        def __init__(self) -> None:
            self.calls = 0
            self.seen_preference = False

        def run_stage(self, *, stage_id: str, task: str, worktree: Path) -> ProviderStageOutput:
            self.calls += 1
            if self.calls == 1:
                return ProviderStageOutput(
                    "session/design_brief.log",
                    "waiting_external_confirmation: 请确认目标用户和视觉风格",
                    "needs design preference",
                    provider_actions=[
                        {
                            "kind": "cli_confirmation",
                            "input_kind": "text",
                            "prompt_text": "请确认目标用户和视觉风格",
                            "options": [],
                            "choices": [],
                        }
                    ],
                )
            match = re.search(r"- path: (.+)", task)
            assert match is not None
            packet = json.loads(Path(match.group(1).strip()).read_text(encoding="utf-8"))
            responses = packet["task"]["provider_action_responses"]
            preference = responses[0]["response"]["text"]
            self.seen_preference = preference == "面向儿童，像素风，浏览器优先"
            payload = {
                "summary": "根据用户偏好完成设计。",
                "design_doc": {
                    "problem_statement": "设计一个贪吃蛇游戏。",
                    "user_preferences": {"style": preference},
                    "acceptance_criteria": ["风格符合用户偏好。"],
                    "proposed_design": {"platform": "Canvas", "modules": ["GameEngine", "Renderer"]},
                    "implementation_sequence": ["实现核心玩法", "打磨像素风表现"],
                    "risks_and_mitigations": ["儿童用户需要更清晰的失败反馈。"],
                },
            }
            return ProviderStageOutput("design/design_brief.md", json.dumps(payload, ensure_ascii=False), "done")

    provider = DesignQuestionProvider()
    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", lambda name: provider)
    runtime = SupervisorRuntime(workspace)

    paused = runtime.run("设计一个贪吃蛇游戏", provider="mock", workflow_name="design-lite")
    blackboard = Blackboard(paused.run_dir)
    try:
        action = blackboard.list_provider_actions(status=str(ProviderActionStatus.PENDING))[0]
        blackboard.respond_provider_action(action["action_id"], response={"text": "面向儿童，像素风，浏览器优先"})
    finally:
        blackboard.close()

    resumed = runtime.resume(paused.run_id)
    content = (workspace / "docs" / "design" / f"{paused.run_id}-design.md").read_text(encoding="utf-8")

    assert paused.status == RunStatus.AWAITING_PROVIDER_ACTION
    assert resumed.status == RunStatus.COMPLETED
    assert provider.seen_preference is True
    assert "面向儿童，像素风，浏览器优先" in content
    assert "## 用户偏好" in content


def test_design_v2_completes_review_gate_and_design_pack(workspace: Path) -> None:
    result = SupervisorRuntime(workspace).run("design reviewed flow", provider="mock", workflow_name="design-v2", require_approval=set())

    assert result.status == RunStatus.COMPLETED
    assert (result.run_dir / "design" / "design_contract.json").exists()
    assert (result.run_dir / "design" / "memory_proposals.json").exists()
    blackboard = Blackboard(result.run_dir)
    try:
        stages = {row["stage_id"]: row["status"] for row in blackboard.table_rows("stages")}
        approvals = blackboard.table_rows("approvals")
    finally:
        blackboard.close()
    assert stages["design_review"] == "completed"
    assert stages["design_revise"] == "skipped"
    assert stages["human_design_approval"] == "completed"
    assert approvals == []


def test_design_v2_pauses_for_design_approval_with_subject_hash(workspace: Path) -> None:
    runtime = SupervisorRuntime(workspace)
    paused = runtime.run("design approval subject", provider="mock", workflow_name="design-v2", require_approval={"design"})

    assert paused.status == RunStatus.AWAITING_APPROVAL
    blackboard = Blackboard(paused.run_dir)
    try:
        approval = blackboard.list_approvals(status="pending")[0]
        subject = json.loads(approval["subject_json"])
        blackboard.decide_approval(approval["approval_id"], ApprovalStatus.APPROVED)
    finally:
        blackboard.close()

    assert approval["type"] == "design"
    assert subject["extra"]["design_contract_hash"]
    assert subject["extra"]["review_result_hash"]
    resumed = runtime.resume(paused.run_id)
    assert resumed.status == RunStatus.COMPLETED


def test_design_v2_review_blockers_drive_revision_loop(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    class DesignReviewProvider:
        def __init__(self) -> None:
            self.review_count = 0
            self.revise_count = 0

        def run_stage(self, *, stage_id: str, task: str, worktree: Path) -> ProviderStageOutput:
            if stage_id == "design_review":
                self.review_count += 1
                if self.review_count == 1:
                    return ProviderStageOutput(
                        "design/design_review.md",
                        '{"has_blockers": true, "blockers": [{"type": "gap", "severity": "high", "suggestion": "add API constraints"}]}',
                        "design blockers",
                    )
                return ProviderStageOutput("design/design_review.md", '{"has_blockers": false, "blockers": []}', "design accepted")
            if stage_id == "design_revise":
                self.revise_count += 1
                return ProviderStageOutput("design/design_revise.md", "# Revised\n\nAdded API constraints.", "revised")
            return ProviderStageOutput(f"{stage_id}.md", f"# {stage_id}", f"{stage_id} ok")

    provider = DesignReviewProvider()
    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", lambda name: provider)

    result = SupervisorRuntime(workspace).run("design loop", provider="mock", workflow_name="design-v2", require_approval=set())

    assert result.status == RunStatus.COMPLETED
    assert provider.review_count == 2
    assert provider.revise_count == 1
    assert "review_loop_iteration" in (result.run_dir / "trace.jsonl").read_text(encoding="utf-8")


def test_design_v2_blocks_after_max_review_fixes(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    class BlockingDesignProvider:
        def run_stage(self, *, stage_id: str, task: str, worktree: Path) -> ProviderStageOutput:
            if stage_id == "design_review":
                return ProviderStageOutput(
                    "design/design_review.md",
                    '{"has_blockers": true, "blockers": [{"type": "gap", "severity": "high", "suggestion": "still missing"}]}',
                    "still blocked",
                )
            if stage_id == "design_revise":
                return ProviderStageOutput("design/design_revise.md", "# Revised\n\nTried once.", "revised")
            return ProviderStageOutput(f"{stage_id}.md", f"# {stage_id}", f"{stage_id} ok")

    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", lambda name: BlockingDesignProvider())

    result = SupervisorRuntime(workspace).run(
        "design max loop",
        provider="mock",
        workflow_name="design-v2",
        require_approval=set(),
        automation={"max_review_fixes": 1},
    )

    assert result.status == RunStatus.BLOCKED
    blackboard = Blackboard(result.run_dir)
    try:
        errors = blackboard.table_rows("error_details")
    finally:
        blackboard.close()
    assert errors[-1]["type"] == "review_blockers"
    assert "design_review blockers remain" in errors[-1]["message"]


def test_design_workflow_blocks_when_user_visible_design_document_cannot_be_written(
    monkeypatch: pytest.MonkeyPatch,
    workspace: Path,
) -> None:
    def fail_user_design_doc(*_args, **_kwargs):
        raise OSError("cannot write user design doc")

    monkeypatch.setattr("muxdev.runtime.supervisor.write_user_design_document", fail_user_design_doc)

    result = SupervisorRuntime(workspace).run("设计一个贪吃蛇游戏方案", provider="mock", workflow_name="design")

    assert result.status == RunStatus.BLOCKED
    blackboard = Blackboard(result.run_dir)
    try:
        errors = blackboard.table_rows("error_details")
    finally:
        blackboard.close()
    assert errors[0]["type"] == "missing_design_document"
    assert "cannot write user design doc" in errors[0]["message"]


def test_software_dev_blocks_when_design_document_cannot_be_written(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    def fail_design_doc(*_args, **_kwargs):
        raise OSError("cannot write design doc")

    monkeypatch.setattr("muxdev.runtime.supervisor._record_project_design_doc", fail_design_doc)

    result = SupervisorRuntime(workspace).run("design doc failure", provider="mock")

    assert result.status == RunStatus.BLOCKED
    blackboard = Blackboard(result.run_dir)
    try:
        stages = {row["stage_id"]: row for row in blackboard.table_rows("stages")}
        errors = blackboard.table_rows("error_details")
        artifacts = blackboard.table_rows("artifacts")
    finally:
        blackboard.close()
    assert stages["design"]["status"] == "failed"
    assert errors[0]["type"] == "missing_design_document"
    assert not [row for row in artifacts if row["kind"] == "project_design_doc"]


def test_blackboard_schema_records_run_stage_approval_and_results(workspace: Path) -> None:
    result = SupervisorRuntime(workspace).run("exercise blackboard", provider="mock")
    blackboard = Blackboard(result.run_dir)
    try:
        run = blackboard.get_run(result.run_id)
        assert run["status"] == "completed"
        assert blackboard.table_rows("stages")
        assert blackboard.table_rows("approvals") == []
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


def test_external_confirmation_output_creates_provider_action_not_approval(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    class ExternalPromptProvider:
        def run_stage(self, *, stage_id: str, task: str, worktree: Path) -> ProviderStageOutput:
            return ProviderStageOutput("session/external.log", "waiting_external_confirmation: continue?", "needs confirmation")

    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", lambda name: ExternalPromptProvider())

    result = SupervisorRuntime(workspace).run("external provider action smoke", provider="mock")

    assert result.status == RunStatus.AWAITING_PROVIDER_ACTION
    blackboard = Blackboard(result.run_dir)
    try:
        approvals = blackboard.list_approvals(status="pending")
        actions = blackboard.list_provider_actions(status=str(ProviderActionStatus.PENDING))
    finally:
        blackboard.close()
    assert approvals == []
    assert actions[0]["kind"] == "cli_confirmation"
    assert "continue?" in actions[0]["prompt_text"]
    assert actions[0]["attach_command"].startswith(f"muxdev attach {result.run_id}")


def test_provider_action_must_be_handled_before_resume(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    class ExternalPromptProvider:
        def __init__(self) -> None:
            self.calls = 0

        def run_stage(self, *, stage_id: str, task: str, worktree: Path) -> ProviderStageOutput:
            self.calls += 1
            if self.calls == 1:
                return ProviderStageOutput("session/external.log", "Apply this change? [y/N]", "needs confirmation")
            return ProviderStageOutput(f"{stage_id}.md", "{}", "ok")

    provider = ExternalPromptProvider()
    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", lambda name: provider)

    runtime = SupervisorRuntime(workspace)
    paused = runtime.run("provider action resume smoke", provider="mock")
    blocked_resume = runtime.resume(paused.run_id)
    blackboard = Blackboard(paused.run_dir)
    try:
        action = blackboard.list_provider_actions(status=str(ProviderActionStatus.PENDING))[0]
        blackboard.update_provider_action_status(action["action_id"], ProviderActionStatus.HANDLED)
    finally:
        blackboard.close()
    resumed = runtime.resume(paused.run_id)

    assert paused.status == RunStatus.AWAITING_PROVIDER_ACTION
    assert blocked_resume.status == RunStatus.AWAITING_PROVIDER_ACTION
    assert resumed.status == RunStatus.COMPLETED


def test_provider_action_response_is_available_in_next_context_packet(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    workflow = workspace / "action_response.yaml"
    workflow.write_text(
        """
name: action-response
stages:
  - id: ask
    role: code
    deps: []
""",
        encoding="utf-8",
    )

    class ChoiceProvider:
        def __init__(self) -> None:
            self.calls = 0
            self.seen_response = False

        def run_stage(self, *, stage_id: str, task: str, worktree: Path) -> ProviderStageOutput:
            self.calls += 1
            if self.calls == 1:
                return ProviderStageOutput(
                    "session/ask.log",
                    "Choose mode",
                    "needs choice",
                    provider_actions=[
                        {
                            "kind": "cli_confirmation",
                            "input_kind": "choice",
                            "prompt_text": "Choose mode",
                            "options": [{"label": "Safe", "value": "safe"}, {"label": "Fast", "value": "fast"}],
                            "choices": [{"label": "Safe", "value": "safe"}, {"label": "Fast", "value": "fast"}],
                        }
                    ],
                )
            match = re.search(r"- path: (.+)", task)
            assert match is not None
            packet = json.loads(Path(match.group(1).strip()).read_text(encoding="utf-8"))
            responses = packet["task"]["provider_action_responses"]
            self.seen_response = responses[0]["response"] == {"choice": "safe"}
            return ProviderStageOutput("ask.md", "done", "done")

    provider = ChoiceProvider()
    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", lambda name: provider)
    runtime = SupervisorRuntime(workspace)
    paused = runtime.run("choose interaction", provider="mock", workflow_name=str(workflow))
    blackboard = Blackboard(paused.run_dir)
    try:
        action = blackboard.list_provider_actions(status=str(ProviderActionStatus.PENDING))[0]
        blackboard.respond_provider_action(action["action_id"], response={"choice": "safe"})
    finally:
        blackboard.close()

    resumed = runtime.resume(paused.run_id)

    assert resumed.status == RunStatus.COMPLETED
    assert provider.seen_response is True
