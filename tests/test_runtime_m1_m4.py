from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from muxdev.models import ApprovalStatus, ProviderActionStatus, RunStatus
from muxdev.runtime import SupervisorRuntime, WorktreeManager
from muxdev.providers.adapters import ProviderStageOutput
from muxdev.services.deliverables import workflow_deliverable_status
from muxdev.services.design import DESIGN_PACK_FILES, design_document_quality_issues, write_user_design_document
from muxdev.storage import Blackboard


def _complete_design_payload(summary: str = "Complete design") -> dict[str, object]:
    return {
        "summary": summary,
        "design_doc": {
            "problem_statement": "Design a lightweight browser snake game.",
            "scope": ["Single design document", "Browser game handoff"],
            "requirements": ["Readable gameplay", "Clear controls", "Restartable end state"],
            "user_preferences": {"audience": "Casual players", "platform": "Browser", "style": "Simple pixel style"},
            "audience": "Casual players",
            "platform": "Desktop and mobile browser",
            "core_loop": ["Start", "Move", "Eat food", "Score", "Avoid collision", "Restart"],
            "controls": {"keyboard": "Arrow keys or WASD", "mobile": "Direction pad"},
            "ui": ["Board", "Score", "Best score", "Pause", "Restart", "Mobile controls"],
            "states": ["idle", "running", "paused", "gameOver"],
            "rules": ["No instant reverse", "Food avoids snake cells", "Collision ends the game"],
            "scoring": "Food adds 10 points and speed increases gradually.",
            "acceptance_criteria": ["Gameplay is understandable", "Controls and failure conditions are documented", "Restart flow is defined"],
            "test_strategy": ["Inspect complete design sections", "Future smoke tests cover controls, scoring, pause, restart, and collision"],
            "proposed_design": {"platform": "Canvas", "modules": ["GameEngine", "Renderer", "InputController"]},
            "state_model": {"states": ["idle", "running", "paused", "gameOver"]},
            "data_flow": ["Input", "State update", "Rule evaluation", "Render"],
            "implementation_sequence": ["Build board", "Implement movement", "Add scoring", "Verify controls"],
            "risks_and_mitigations": ["Mobile controls need narrow viewport verification."],
            "open_questions": ["Confirm final board size and speed curve."],
        },
    }


def _complete_design_stage_output(stage_id: str, summary: str = "complete design") -> ProviderStageOutput:
    payload = _complete_design_payload(summary)
    return ProviderStageOutput(f"design/{stage_id}.md", json.dumps(payload, ensure_ascii=False), summary)


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
    assert (result.run_dir / "workspace_apply.json").exists()
    assert (result.run_dir / "worktree" / ".git" / "config").exists()
    assert (workspace / "muxdev_mock_change.txt").exists()
    workspace_apply = json.loads((result.run_dir / "workspace_apply.json").read_text(encoding="utf-8"))
    assert "muxdev_mock_change.txt" in workspace_apply["files"]
    user_design_doc = workspace / "docs" / "design" / "design.md"
    assert not user_design_doc.exists()
    design_doc = result.run_dir / "worktree" / "docs" / "design" / f"{result.run_id}-design.md"
    assert not design_doc.exists()

    trace_types = [
        json.loads(line)["type"]
        for line in (result.run_dir / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert "run_started" in trace_types
    assert "stage_started" in trace_types
    assert "stage_completed" in trace_types
    assert "project_design_doc_written" not in trace_types
    assert "run_completed" in trace_types

    blackboard = Blackboard(result.run_dir)
    try:
        design_artifacts = [row for row in blackboard.table_rows("artifacts") if row["kind"] == "project_design_doc"]
    finally:
        blackboard.close()
    assert design_artifacts == []


def test_runtime_binds_default_skills_to_workflow_stages(workspace: Path) -> None:
    result = SupervisorRuntime(workspace).run("stage skill routing", provider="mock", workflow_name="software-dev")

    assert result.status == RunStatus.COMPLETED
    context = json.loads((result.run_dir / "task_context.json").read_text(encoding="utf-8"))
    by_stage: dict[str, set[str]] = {}
    for skill in context["skills"]:
        by_stage.setdefault(str(skill.get("stage")), set()).add(str(skill.get("name")))

    assert "default-requirements" in by_stage["task_intake"]
    assert "default-plan" in by_stage["plan"]
    assert "default-review" in by_stage["plan_review"]
    assert "default-plan" in by_stage["plan_revise"]
    assert "default-code" in by_stage["implement"]
    assert "default-test" in by_stage["test"]
    assert "default-review" in by_stage["review"]
    assert "default-code" in by_stage["fix"]
    assert all("default-test-strategy" not in names for names in by_stage.values())
    assert all("delivery_rules" in skill and skill.get("delivery_rule_hash") for skill in context["skills"])


def test_git_worktree_fallback_baselines_no_head_repo_and_ignores_generated_files(
    monkeypatch: pytest.MonkeyPatch,
    workspace: Path,
) -> None:
    (workspace / "app.py").write_text("value = 1\n", encoding="utf-8")
    (workspace / "pytest-cache-files-source").mkdir()
    (workspace / "pytest-cache-files-source" / "README").write_text("cache", encoding="utf-8")
    run_dir = workspace / ".muxdev" / "runs" / "run_no_head"
    run_dir.mkdir(parents=True)

    monkeypatch.setattr(WorktreeManager, "_is_git_repo", staticmethod(lambda path: path == workspace))
    monkeypatch.setattr("muxdev.runtime.worktree.shutil.which", lambda name: "git" if name == "git" else None)
    git_calls: list[list[str]] = []

    def fake_subprocess_run(cmd, **kwargs):
        assert cmd[:3] == ["git", "worktree", "add"]
        return subprocess.CompletedProcess(cmd, 128, "", "fatal: invalid reference: HEAD")

    def fake_run_git(path: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
        git_calls.append(args)
        stdout = "A  app.py\n" if args == ["status", "--porcelain"] else ""
        return subprocess.CompletedProcess(["git", *args], 0, stdout, "")

    monkeypatch.setattr("muxdev.runtime.worktree.subprocess.run", fake_subprocess_run)
    monkeypatch.setattr(WorktreeManager, "_run_git", staticmethod(fake_run_git))

    result = WorktreeManager(workspace).prepare("run_no_head", run_dir)

    assert result.strategy == "git_worktree_fallback_copy"
    assert (result.path / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    assert not (result.path / "pytest-cache-files-source").exists()
    assert git_calls[:3] == [["init"], ["add", "--all"], ["status", "--porcelain"]]
    assert git_calls[3][:4] == ["-c", "user.name=muxdev", "-c", "user.email=muxdev@example.invalid"]
    assert git_calls[3][4] == "commit"
    excludes = (result.path / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert "__pycache__/" in excludes
    assert ".pytest_cache/" in excludes
    assert "pytest-cache-files-*/" in excludes


def test_non_git_workspace_copy_fallback_includes_project_files(
    monkeypatch: pytest.MonkeyPatch,
    workspace: Path,
) -> None:
    (workspace / "app.py").write_text("value = 1\n", encoding="utf-8")
    (workspace / ".muxdev" / "runs" / "old_run").mkdir(parents=True)
    run_dir = workspace / ".muxdev" / "runs" / "run_copy"
    run_dir.mkdir(parents=True)
    init_calls: list[tuple[Path, bool]] = []

    def fake_init(path: Path, *, commit_baseline: bool = False) -> None:
        init_calls.append((path, commit_baseline))
        (path / ".git").mkdir()
        (path / ".git" / "config").write_text("[core]\n", encoding="utf-8")

    monkeypatch.setattr(WorktreeManager, "_is_git_repo", staticmethod(lambda path: False))
    monkeypatch.setattr(WorktreeManager, "_init_fallback_git_repo", staticmethod(fake_init))

    result = WorktreeManager(workspace).prepare("run_copy", run_dir)

    assert result.strategy == "workspace_copy"
    assert (result.path / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    assert not (result.path / ".muxdev").exists()
    assert (result.path / ".git" / "config").exists()
    assert init_calls == [(result.path, True)]


def test_git_repo_detection_requires_workspace_top_level(
    monkeypatch: pytest.MonkeyPatch,
    workspace: Path,
) -> None:
    child = workspace / "nested"
    child.mkdir()

    def fake_subprocess_run(cmd, **kwargs):
        assert cmd == ["git", "rev-parse", "--show-toplevel"]
        return subprocess.CompletedProcess(cmd, 0, str(workspace.resolve()) + "\n", "")

    monkeypatch.setattr("muxdev.runtime.worktree.subprocess.run", fake_subprocess_run)

    assert WorktreeManager._is_git_repo(workspace) is True
    assert WorktreeManager._is_git_repo(child) is False


def test_design_workflow_publishes_user_visible_design_document(workspace: Path) -> None:
    result = SupervisorRuntime(workspace).run("设计一个贪吃蛇游戏方案", provider="mock", workflow_name="design")

    assert result.status == RunStatus.COMPLETED
    user_design_doc = workspace / "docs" / "design" / "design.md"
    assert user_design_doc.exists()
    content = user_design_doc.read_text(encoding="utf-8")
    assert "# 设计文档" in content
    assert "设计一个贪吃蛇游戏方案" in content
    assert "Mock design plan" in content
    assert "Mock design output" in content

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
                    "scope": ["Design a browser-first snake game document.", "Keep the workflow design-only."],
                    "user_preferences": {"visual_style": "像素风", "platform": "浏览器"},
                    "audience": "Casual browser players",
                    "platform": "Desktop and mobile browser",
                    "core_loop": ["Start", "Move snake", "Eat food", "Grow and score", "Avoid collision", "Restart"],
                    "controls": {"keyboard": "Arrow keys and WASD", "pause": "Space", "restart": "Enter"},
                    "ui": ["Board grid", "Score", "Best score", "Pause/restart actions", "Mobile direction pad"],
                    "states": ["idle", "running", "paused", "gameOver"],
                    "rules": ["No instant reverse", "Food never appears on the snake", "Wall or self collision ends the game"],
                    "scoring": "Each food adds 10 points and speed increases gradually.",
                    "acceptance_criteria": ["可以开始、暂停和重新开始。", "撞墙后进入 gameOver。"],
                    "test_strategy": ["Review required design sections.", "During implementation, smoke test keyboard controls, pause, restart, scoring, and collision."],
                    "proposed_design": {
                        "platform": "Vanilla HTML/CSS/JavaScript + Canvas",
                        "modules": ["GameEngine", "Renderer", "InputController"],
                    },
                    "state_model": {"states": ["idle", "running", "paused", "gameOver"]},
                    "data_flow": ["用户输入 -> GameEngine.tick() -> Renderer.draw()"],
                    "implementation_sequence": ["搭建页面", "实现核心逻辑", "补充测试"],
                    "risks_and_mitigations": ["移动端手感需后续验证。"],
                    "open_questions": ["Confirm final visual theme and difficulty tuning."],
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
    content = (workspace / "docs" / "design" / "design.md").read_text(encoding="utf-8")
    assert "# 设计文档" in content
    assert "像素风" in content
    assert "Vanilla HTML/CSS/JavaScript + Canvas" in content
    assert "## 验收标准" in content
    assert "## 状态模型" in content
    assert "Stream Events" not in content
    assert "Session Archives" not in content
    assert "transcript:" not in content
    assert '{"type":' not in content


def test_design_doc_prefers_chinese_design_doc_over_later_intake_payload(workspace: Path) -> None:
    chinese_design = {
        "summary": "已完成中文贪吃蛇游戏设计方案。",
        "claims": ["muxdev internal claim should not be user-facing"],
        "evidence": [{"source": "muxdev trace"}],
        "missing_evidence": ["Missing Evidence internal note"],
        "design_doc": {
            "problem_statement": "目标是设计一个简单、可直接实现的中文贪吃蛇小游戏，范围限定为浏览器单页游戏。",
            "scope": ["提供完整设计文档", "覆盖桌面键盘和移动端触控", "不包含账号系统或在线排行榜"],
            "requirements": [
                "首屏必须直接呈现游戏棋盘、分数区和主要操作入口。",
                "设计需要说明游戏开始、暂停、失败和重新开始之间的状态转换。",
                "移动端方向按钮需要足够大，避免误触并保持棋盘可见。",
            ],
            "constraints": ["棋盘需要响应式布局", "移动端控制区不能遮挡游戏区域", "分数只保存在本地状态"],
            "non_goals": ["不做多人模式", "不做复杂关卡编辑器"],
            "user_preferences": {
                "assumed_defaults": ["目标平台为 Web 浏览器", "默认规则为撞墙失败"],
                "visual_direction": "清晰的像素风",
            },
            "audience": "喜欢短局休闲游戏的中文用户。",
            "platform": "桌面和移动端浏览器。",
            "core_loop": ["开始游戏", "控制蛇移动", "吃到食物", "增长身体并得分", "碰撞后结束", "重新开始"],
            "controls": {"desktop": "方向键或 WASD", "mobile": "底部方向按钮", "input_rules": ["禁止 180 度立即反向"]},
            "ui": [
                "棋盘使用等比例网格呈现蛇身、蛇头和食物。",
                "当前分数和最高分固定在棋盘上方，游戏过程中实时更新。",
                "暂停、继续和重新开始按钮放在主操作区，移动端方向控制放在棋盘下方。",
            ],
            "states": ["待开始", "运行中", "暂停", "游戏结束"],
            "rules": [
                "不能直接反向移动，避免玩家一键让蛇头撞到蛇身。",
                "食物只出现在空白格，蛇吃到食物后身体增长一格。",
                "撞墙或撞到自己即失败，失败后保留本局分数并允许立即重开。",
            ],
            "scoring": "每吃到一个食物增加 10 分，并随分数逐步提升速度。",
            "acceptance_criteria": ["用户可以开始、暂停和重新开始游戏", "蛇能移动、吃食物、增长并计分", "碰撞后进入明确的游戏结束状态"],
            "test_strategy": ["检查设计文档覆盖目标、用户、交互、状态、规则和验收", "实现后冒烟测试键盘、触控、暂停、计分、碰撞和重开"],
            "proposed_design": {"技术方案": "HTML/CSS/JavaScript Canvas", "模块": ["游戏引擎", "渲染器", "输入控制器"]},
            "state_model": {"game": ["待开始", "运行中", "暂停", "游戏结束"], "snake_segment": ["x 坐标", "y 坐标"]},
            "data_flow": ["用户输入进入输入控制器", "游戏引擎更新蛇和食物状态", "渲染器绘制棋盘和分数"],
            "implementation_sequence": [
                "搭建页面骨架、棋盘容器、分数区和基础视觉样式。",
                "实现输入控制、固定节奏 tick、蛇移动、食物生成和碰撞判断。",
                "补充分数、最高分、速度提升、暂停和重新开始流程。",
                "验证桌面键盘、移动端触控、不同屏幕宽度和失败恢复体验。",
            ],
            "open_questions": ["最终棋盘尺寸和速度曲线仍可在实现前微调。"],
            "claims": ["muxdev nested claim should not render"],
            "evidence": [{"file": "muxdev evidence should not render"}],
            "missing_evidence": ["Missing Evidence nested note"],
        },
    }
    later_intake_like_payload = {
        "summary": "Intake classification: actionable design-lite feature request.",
        "problem_statement": "Design a simple Snake game with classic rules.",
        "users": ["Casual browser players"],
        "acceptance_criteria": ["English intake acceptance should not be selected."],
        "constraints": ["English shallow payload generated later by repair."],
        "non_goals": ["No account system."],
        "open_questions": ["English question should not win."],
    }

    path = write_user_design_document(
        workspace=workspace,
        run_id="run_lang",
        task="设计一个简单的贪吃蛇小游戏",
        workflow="design-lite",
        sections=[
            ("design_brief", json.dumps(chinese_design, ensure_ascii=False)),
            ("00_problem_statement", "```json\n" + json.dumps(later_intake_like_payload, ensure_ascii=False) + "\n```"),
        ],
    )

    content = path.read_text(encoding="utf-8")
    assert "目标是设计一个简单、可直接实现的中文贪吃蛇小游戏" in content
    assert "Design a simple Snake game" not in content
    assert "Intake classification" not in content
    assert "Run:" not in content
    assert "Workflow:" not in content
    assert "Assumed Defaults" not in content
    assert "Input Rules" not in content
    assert "Snake Segment" not in content
    assert "默认假设" in content
    assert "输入规则" in content
    assert "蛇身格" in content
    assert "Claims" not in content
    assert "Evidence" not in content
    assert "Missing Evidence" not in content
    assert "muxdev" not in content.lower()


def test_design_doc_extracts_top_level_design_pack(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    class DesignPackProvider:
        def run_stage(self, *, stage_id: str, task: str, worktree: Path) -> ProviderStageOutput:
            if stage_id == "design_brief":
                payload = {
                    "summary": "完成贪吃蛇小游戏设计。",
                    "claims": ["muxdev internal top-level claim"],
                    "evidence": [{"source": "muxdev provider trace"}],
                    "missing_evidence": ["Missing Evidence internal top-level note"],
                    "design_pack": {
                        "name": "贪吃蛇小游戏",
                        "platform": "Web",
                        "audience": "儿童休闲玩家",
                        "style": "像素风",
                        "goals": ["Create a clear browser snake-game design", "Support keyboard and touch play", "Keep the rules easy for casual players"],
                        "scope": ["Design-only handoff", "Single-page browser game", "No backend or account system"],
                        "constraints": ["Responsive board layout", "Readable controls on mobile", "Simple local score state"],
                        "core_loop": ["移动蛇", "吃食物", "增长身体", "避免碰撞"],
                        "controls": {"keyboard": "方向键 / WASD"},
                        "interactions": ["Start from idle", "Pause and resume", "Restart from game over", "Tap mobile direction buttons"],
                        "feedback": ["Score updates immediately", "Collision shows a clear game-over state", "New food appears after eating"],
                        "ui": ["分数", "开始", "暂停", "重新开始"],
                        "screens": ["Start screen", "Running board", "Paused overlay", "Game-over summary"],
                        "rules": ["撞墙或撞到自己即失败"],
                        "scoring": "Each food adds 10 points and speed increases over time.",
                        "entities": ["Snake", "Food", "Board", "Score"],
                        "data_model": {"snake": "ordered grid cells", "food": "one empty grid cell", "score": "integer points"},
                        "states": ["idle", "running", "paused", "gameOver"],
                        "acceptance_criteria": ["可以开始游戏", "失败后可以重新开始"],
                        "test_strategy": ["Inspect complete design sections.", "Future implementation smoke tests cover start, movement, pause, scoring, collision, and restart."],
                        "implementation_sequence": ["Build board UI", "Implement movement and food rules", "Add score and speed", "Verify desktop and mobile controls"],
                        "risks": ["移动端手感需要后续验证"],
                        "risks_and_mitigations": ["Small mobile screens can crowd controls; reserve a fixed control area below the board."],
                        "open_questions": ["Confirm exact board size and speed curve."],
                        "claims": ["muxdev internal nested claim"],
                        "evidence": [{"source": "muxdev nested trace"}],
                        "missing_evidence": ["Missing Evidence nested note"],
                    },
                }
                return ProviderStageOutput("design/design_brief.md", json.dumps(payload, ensure_ascii=False), "done")
            if stage_id == "design_review":
                return ProviderStageOutput("design/design_review.md", '{"has_blockers": false, "blockers": []}', "ok")
            return ProviderStageOutput(f"design/{stage_id}.md", "# stage\n\nok", "ok")

    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", lambda name: DesignPackProvider())

    result = SupervisorRuntime(workspace).run("设计一个贪吃蛇游戏", provider="mock", workflow_name="design-lite")

    assert result.status == RunStatus.COMPLETED
    content = (workspace / "docs" / "design" / "design.md").read_text(encoding="utf-8")
    assert "贪吃蛇小游戏" in content
    assert "## 玩法与交互" in content
    assert "## 验收标准" in content
    assert "Claims" not in content
    assert "Evidence" not in content
    assert "Missing Evidence" not in content
    assert "muxdev" not in content.lower()
    design_dir = result.run_dir / "design"
    for filename in DESIGN_PACK_FILES:
        section = (design_dir / filename).read_text(encoding="utf-8")
        assert "This design pack section is generated by muxdev P0" not in section
        assert "Provider 输出摘录" not in section
        assert "Claims" not in section
        assert "Evidence" not in section
        assert "Missing Evidence" not in section
        assert "muxdev" not in section.lower()
        assert "设计输出摘录" in section or "## " in section


def test_design_pack_without_risks_still_publishes(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    class DesignPackWithoutRisksProvider:
        def run_stage(self, *, stage_id: str, task: str, worktree: Path) -> ProviderStageOutput:
            if stage_id == "design_brief":
                payload = {
                    "summary": "完成贪吃蛇小游戏设计。",
                    "design_pack": {
                        "name": "贪吃蛇小游戏",
                        "platform": "Web",
                        "audience": "休闲玩家",
                        "style": "现代清晰网格",
                        "goals": ["Create a clear browser snake-game design", "Support keyboard play"],
                        "scope": ["Design-only handoff", "Single-page browser game", "No backend"],
                        "constraints": ["Readable controls", "Simple local game state"],
                        "core_loop": ["开始游戏", "移动蛇", "吃食物", "获得分数", "避免碰撞", "重新开始"],
                        "controls": {"keyboard": "方向键 / WASD"},
                        "interactions": ["Start from idle", "Pause and resume", "Restart from game over"],
                        "feedback": ["Score updates immediately", "Collision shows game over"],
                        "ui": ["分数", "开始", "暂停", "重新开始"],
                        "screens": ["Start screen", "Running board", "Paused overlay", "Game-over summary"],
                        "states": ["idle", "running", "paused", "gameOver"],
                        "rules": ["撞墙或撞到自己即失败", "食物只出现在空白格"],
                        "scoring": "Each food adds 10 points.",
                        "entities": ["Snake", "Food", "Board", "Score"],
                        "data_model": {"snake": "ordered grid cells", "food": "one empty grid cell", "score": "integer points"},
                        "acceptance_criteria": ["可以开始游戏", "蛇能移动和吃食物", "失败后可以重新开始"],
                        "test_strategy": ["Inspect complete design sections.", "Future smoke tests cover start, movement, scoring, collision, and restart."],
                        "implementation_sequence": ["Build board UI", "Implement movement and food rules", "Add score", "Verify controls"],
                        "open_questions": ["Confirm exact board size and speed curve."],
                    },
                }
                return ProviderStageOutput("design/design_brief.md", json.dumps(payload, ensure_ascii=False), "done")
            if stage_id == "design_review":
                return ProviderStageOutput("design/design_review.md", '{"has_blockers": false, "blockers": []}', "ok")
            return ProviderStageOutput(f"design/{stage_id}.md", "# stage\n\nok", "ok")

    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", lambda name: DesignPackWithoutRisksProvider())

    result = SupervisorRuntime(workspace).run("设计一个贪吃蛇游戏", provider="mock", workflow_name="design-lite")

    assert result.status == RunStatus.COMPLETED
    content = (workspace / "docs" / "design" / "design.md").read_text(encoding="utf-8")
    assert "贪吃蛇小游戏" in content
    issues = design_document_quality_issues(markdown=content)
    assert not any("risks" in issue for issue in issues)


def test_design_verify_blocks_shallow_design_document(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    class ShallowDesignProvider:
        def run_stage(self, *, stage_id: str, task: str, worktree: Path) -> ProviderStageOutput:
            if stage_id in {"design_brief", "design_revise"}:
                payload = {
                    "summary": "Completed a thin snake design.",
                    "design_doc": {
                        "problem_statement": "Design a snake game.",
                        "acceptance_criteria": ["AC-1: no implementation files were created"],
                    },
                }
                return ProviderStageOutput(f"design/{stage_id}.md", json.dumps(payload, ensure_ascii=False), "thin design")
            if stage_id == "design_review":
                return ProviderStageOutput("design/design_review.md", '{"has_blockers": false, "blockers": []}', "review passed")
            return ProviderStageOutput(f"design/{stage_id}.md", "# stage\n\nok", "ok")

    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", lambda name: ShallowDesignProvider())

    result = SupervisorRuntime(workspace).run(
        "design a snake game",
        provider="mock",
        workflow_name="design-lite",
        require_approval=set(),
        automation={"max_review_fixes": 1},
    )
    gate_payload = json.loads((result.run_dir / "delivery_gates" / "design_verify.json").read_text(encoding="utf-8"))

    assert result.status == RunStatus.BLOCKED
    assert any(item["type"] == "design_document_incomplete" for item in gate_payload["blockers"])


@pytest.mark.parametrize(
    "workflow",
    ["design", "design-lite", "dev", "dev-lite", "dev-new", "fix", "refactor", "review", "test", "docs", "software-dev"],
)
def test_builtin_workflows_publish_required_deliverables(workspace: Path, workflow: str) -> None:
    result = SupervisorRuntime(workspace).run(f"audit output for {workflow}", provider="mock", workflow_name=workflow, require_approval=set())

    assert result.status == RunStatus.COMPLETED
    blackboard = Blackboard(result.run_dir)
    try:
        status = workflow_deliverable_status(
            blackboard,
            run_dir=result.run_dir,
            run_id=result.run_id,
            workflow=workflow,
            require_report=True,
        )
        artifacts = blackboard.table_rows("artifacts", run_id=result.run_id)
    finally:
        blackboard.close()

    assert status["missing"] == []
    kinds = {row["kind"] for row in artifacts}
    assert "report" in kinds
    if workflow in {"design", "design-lite"}:
        assert {"project_design_doc", "design_pack"} <= kinds
        for filename in DESIGN_PACK_FILES:
            assert "This design pack section is generated by muxdev P0" not in (result.run_dir / "design" / filename).read_text(encoding="utf-8")
    if workflow in {"dev", "dev-lite", "dev-new", "fix", "refactor", "software-dev", "test"}:
        assert "test_report" in kinds
    if workflow in {"dev", "dev-lite", "dev-new", "fix", "refactor", "software-dev", "test", "review", "docs"}:
        assert "review_report" in kinds
    if workflow in {"dev-lite", "dev-new"}:
        assert "handoff_summary" in kinds
    if workflow == "docs":
        assert "docs_report" in kinds


def test_completed_continue_repairs_missing_design_deliverables_without_provider(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    runtime = SupervisorRuntime(workspace)
    result = runtime.run("设计一个贪吃蛇游戏", provider="mock", workflow_name="design-lite")
    assert result.status == RunStatus.COMPLETED
    user_doc = workspace / "docs" / "design" / "design.md"
    user_doc.write_text("", encoding="utf-8")
    for filename in DESIGN_PACK_FILES:
        (result.run_dir / "design" / filename).write_text("This design pack section is generated by muxdev P0\n", encoding="utf-8")

    def fail_provider(_name: str):
        raise AssertionError("completed deliverable repair should not call provider")

    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", fail_provider)

    repaired = runtime.resume(result.run_id)

    assert repaired.status == RunStatus.COMPLETED
    assert user_doc.exists()
    assert "This design pack section is generated by muxdev P0" not in (result.run_dir / "design" / "00_problem_statement.md").read_text(encoding="utf-8")


def test_missing_deliverable_continue_repairs_running_run_after_blind_validator_without_provider(
    monkeypatch: pytest.MonkeyPatch, workspace: Path
) -> None:
    runtime = SupervisorRuntime(workspace)
    result = runtime.run("retry missing design deliverables from run artifacts", provider="mock", workflow_name="design-lite")
    assert result.status == RunStatus.COMPLETED
    user_doc = workspace / "docs" / "design" / "design.md"
    user_doc.write_text("", encoding="utf-8")
    for filename in DESIGN_PACK_FILES:
        (result.run_dir / "design" / filename).write_text("This design pack section is generated by muxdev P0\n", encoding="utf-8")

    blackboard = Blackboard(result.run_dir)
    try:
        blackboard.conn.execute(
            "DELETE FROM artifacts WHERE run_id = ? AND kind IN (?, ?, ?, ?)",
            (result.run_id, "project_design_doc", "design_pack", "design_contract", "memory_proposals"),
        )
        blackboard.conn.commit()
        blackboard.add_error(result.run_id, None, "missing_deliverable", "missing required deliverables: project_design_doc, design_pack")
        blackboard.add_error(result.run_id, None, "blind_validator_reject", "blind validator rejected the patch")
        blackboard.conn.execute(
            """
            UPDATE error_details
            SET created_at = ?
            WHERE run_id = ? AND type = ?
            """,
            ("2099-01-01T00:00:00Z", result.run_id, "blind_validator_reject"),
        )
        blackboard.conn.commit()
        blackboard.set_run_status(result.run_id, RunStatus.RUNNING)
    finally:
        blackboard.close()

    def fail_provider(_name: str):
        raise AssertionError("missing deliverable repair should not call provider")

    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", fail_provider)

    repaired = runtime.resume(result.run_id)

    assert repaired.status == RunStatus.COMPLETED
    assert user_doc.exists()
    assert (result.run_dir / "design" / "00_problem_statement.md").exists()
    blackboard = Blackboard(result.run_dir)
    try:
        artifacts = blackboard.table_rows("artifacts", run_id=result.run_id)
        kinds = {row["kind"] for row in artifacts}
        run = blackboard.get_run(result.run_id)
    finally:
        blackboard.close()
    assert run["status"] == str(RunStatus.COMPLETED)
    assert {"project_design_doc", "design_pack"} <= kinds


def test_completed_continue_recovers_design_doc_from_run_design_files(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    runtime = SupervisorRuntime(workspace)
    result = runtime.run("recover design doc from run files", provider="mock", workflow_name="design-lite")
    assert result.status == RunStatus.COMPLETED
    user_doc = workspace / "docs" / "design" / "design.md"
    user_doc.write_text("", encoding="utf-8")

    blackboard = Blackboard(result.run_dir)
    try:
        blackboard.conn.execute(
            "DELETE FROM artifacts WHERE run_id = ? AND kind IN (?, ?)",
            (result.run_id, "stage_output", "project_design_doc"),
        )
        blackboard.conn.commit()
    finally:
        blackboard.close()

    def fail_provider(_name: str):
        raise AssertionError("completed deliverable repair should recover from run design files")

    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", fail_provider)

    repaired = runtime.resume(result.run_id)

    assert repaired.status == RunStatus.COMPLETED
    assert user_doc.exists()
    content = user_doc.read_text(encoding="utf-8")
    assert "Mock design output" in content
    blackboard = Blackboard(result.run_dir)
    try:
        design_artifacts = [row for row in blackboard.table_rows("artifacts", run_id=result.run_id) if row["kind"] == "project_design_doc"]
    finally:
        blackboard.close()
    assert [Path(row["path"]) for row in design_artifacts] == [user_doc]


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
                    "scope": ["Design a browser snake game handoff.", "Keep this run design-only."],
                    "user_preferences": {"style": preference},
                    "audience": "Children and casual browser players",
                    "platform": "Browser desktop and mobile",
                    "core_loop": ["Start", "Move snake", "Eat food", "Grow", "Avoid collision", "Restart"],
                    "controls": {"keyboard": "Arrow keys or WASD", "mobile": "On-screen direction pad"},
                    "ui": ["Grid board", "Score", "Best score", "Pause", "Restart", "Mobile controls"],
                    "states": ["idle", "running", "paused", "gameOver"],
                    "rules": ["No instant reverse", "Food avoids snake cells", "Collision ends the game"],
                    "scoring": "Food adds 10 points and speed increases gradually.",
                    "acceptance_criteria": ["风格符合用户偏好。"],
                    "test_strategy": ["Inspect section completeness.", "Future smoke tests cover controls, pause, restart, scoring, and collision."],
                    "proposed_design": {"platform": "Canvas", "modules": ["GameEngine", "Renderer"]},
                    "implementation_sequence": ["实现核心玩法", "打磨像素风表现"],
                    "risks_and_mitigations": ["儿童用户需要更清晰的失败反馈。"],
                    "open_questions": ["Confirm final visual theme and difficulty tuning."],
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
    content = (workspace / "docs" / "design" / "design.md").read_text(encoding="utf-8")

    assert paused.status == RunStatus.AWAITING_PROVIDER_ACTION
    assert resumed.status == RunStatus.COMPLETED
    assert provider.seen_preference is True
    assert "面向儿童，像素风，浏览器优先" in content
    assert "## 用户偏好" in content


def test_structured_design_feedback_pauses_and_reruns_with_feedback(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    class FeedbackProvider:
        def __init__(self) -> None:
            self.design_brief_calls = 0
            self.saw_feedback = False
            self.saw_upstream_artifact = False

        def run_stage(self, *, stage_id: str, task: str, worktree: Path) -> ProviderStageOutput:
            if stage_id == "design_brief":
                self.design_brief_calls += 1
                if self.design_brief_calls == 1:
                    return ProviderStageOutput(
                        "design/design_brief.md",
                        json.dumps(
                            {
                                "delivery_decision": "needs_feedback",
                                "feedback_request": "Which compliance region should this flow satisfy?",
                                "summary": "Compliance region changes the core design.",
                            },
                            ensure_ascii=False,
                        ),
                        "needs design feedback",
                    )
                match = re.search(r"- path: (.+)", task)
                assert match is not None
                packet = json.loads(Path(match.group(1).strip()).read_text(encoding="utf-8"))
                events = packet["run"]["feedback_events"]
                self.saw_feedback = any(
                    row.get("kind") == "plan_feedback" and "US SOC2" in str(row.get("content") or "")
                    for row in events
                )
                return _complete_design_stage_output(stage_id, "Design uses US SOC2 assumptions")
            if stage_id == "design_review":
                match = re.search(r"- path: (.+)", task)
                assert match is not None
                packet = json.loads(Path(match.group(1).strip()).read_text(encoding="utf-8"))
                upstream = packet["run"]["upstream_artifacts"]
                self.saw_upstream_artifact = any(
                    row.get("stage_id") == "design_brief"
                    and row.get("kind") == "stage_output"
                    and Path(str(row.get("path") or "")).exists()
                    for row in upstream
                )
                return ProviderStageOutput(
                    "design/design_review.md",
                    json.dumps({"blockers": [], "delivery_decision": "complete", "missing_evidence": []}),
                    "review complete",
                )
            return ProviderStageOutput(f"{stage_id}.md", json.dumps({"summary": "ok"}), "ok")

    provider = FeedbackProvider()
    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", lambda name: provider)
    runtime = SupervisorRuntime(workspace)

    paused = runtime.run("design compliance approval flow", provider="mock", workflow_name="design-lite")
    blackboard = Blackboard(paused.run_dir)
    try:
        feedback_requests = [
            row
            for row in blackboard.table_rows("feedback_events", run_id=paused.run_id)
            if row["kind"] == "design_feedback_request" and row["status"] == "pending"
        ]
        provider_actions = blackboard.list_provider_actions(status=str(ProviderActionStatus.PENDING), run_id=paused.run_id)
        assert len(feedback_requests) == 1
        assert provider_actions == []
        request_id = feedback_requests[0]["feedback_id"]
        blackboard.add_feedback_event(
            run_id=paused.run_id,
            source="user",
            kind="plan_feedback",
            severity="medium",
            status="pending",
            route_to="plan",
            content="Use US SOC2 assumptions.",
            payload={"source_feedback_requests": [request_id]},
        )
        blackboard.update_feedback_event_status(request_id, "handled")
        for stage_id in ("design_brief", "design_review", "design_verify", "design_revise", "approve_plan", "design_pack"):
            blackboard.reset_stage(paused.run_id, stage_id)
        blackboard.set_run_status(paused.run_id, RunStatus.RUNNING)
    finally:
        blackboard.close()

    resumed = runtime.resume(paused.run_id)

    assert paused.status == RunStatus.AWAITING_FEEDBACK
    assert resumed.status == RunStatus.COMPLETED
    assert provider.design_brief_calls == 2
    assert provider.saw_feedback is True
    assert provider.saw_upstream_artifact is True


def test_legacy_design_v2_alias_completes_review_gate_and_design_pack(workspace: Path) -> None:
    result = SupervisorRuntime(workspace).run("design reviewed flow", provider="mock", workflow_name="design-v2", require_approval=set())

    assert result.status == RunStatus.COMPLETED
    assert (result.run_dir / "design" / "design_contract.json").exists()
    assert (result.run_dir / "design" / "memory_proposals.json").exists()
    blackboard = Blackboard(result.run_dir)
    try:
        run = blackboard.get_run(result.run_id)
        stages = {row["stage_id"]: row["status"] for row in blackboard.table_rows("stages")}
        approvals = blackboard.table_rows("approvals")
    finally:
        blackboard.close()
    assert stages["design_review"] == "completed"
    assert stages["design_revise"] == "skipped"
    assert stages["approve_plan"] == "completed"
    assert approvals == []
    assert run["workflow"] == "design"


def test_design_pauses_for_design_approval_with_subject_hash(workspace: Path) -> None:
    runtime = SupervisorRuntime(workspace)
    paused = runtime.run("design approval subject", provider="mock", workflow_name="design-v2", require_approval={"plan"})

    assert paused.status == RunStatus.AWAITING_APPROVAL
    blackboard = Blackboard(paused.run_dir)
    try:
        approval = blackboard.list_approvals(status="pending")[0]
        subject = json.loads(approval["subject_json"])
        blackboard.decide_approval(approval["approval_id"], ApprovalStatus.APPROVED)
    finally:
        blackboard.close()

    assert approval["type"] == "plan"
    assert subject["extra"]["plan_hash"]
    resumed = runtime.resume(paused.run_id)
    assert resumed.status == RunStatus.COMPLETED


def test_design_review_blockers_drive_revision_loop(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    class DesignReviewProvider:
        def __init__(self) -> None:
            self.review_count = 0
            self.revise_count = 0

        def run_stage(self, *, stage_id: str, task: str, worktree: Path) -> ProviderStageOutput:
            if stage_id in {"design_plan", "design_brief"}:
                return _complete_design_stage_output(stage_id, "design loop base")
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


def test_design_blocks_after_max_review_fixes(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    class BlockingDesignProvider:
        def run_stage(self, *, stage_id: str, task: str, worktree: Path) -> ProviderStageOutput:
            if stage_id in {"design_plan", "design_brief"}:
                return _complete_design_stage_output(stage_id, "blocking design base")
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
    assert "design_verify blockers remain" in errors[-1]["message"]


def test_design_workflow_blocks_when_user_visible_design_document_cannot_be_written(
    monkeypatch: pytest.MonkeyPatch,
    workspace: Path,
) -> None:
    def fail_user_design_doc(*_args, **_kwargs):
        raise OSError("cannot write user design doc")

    monkeypatch.setattr("muxdev.services.deliverables.write_user_design_document", fail_user_design_doc)

    result = SupervisorRuntime(workspace).run("设计一个贪吃蛇游戏方案", provider="mock", workflow_name="design")

    assert result.status == RunStatus.BLOCKED
    blackboard = Blackboard(result.run_dir)
    try:
        errors = blackboard.table_rows("error_details")
    finally:
        blackboard.close()
    assert errors[0]["type"] == "missing_deliverable"
    assert "cannot write user design doc" in errors[0]["message"]


def test_software_dev_does_not_require_design_document_write(workspace: Path) -> None:
    result = SupervisorRuntime(workspace).run("design doc failure", provider="mock")

    assert result.status == RunStatus.COMPLETED
    report = (result.run_dir / "final_report.md").read_text(encoding="utf-8")
    blackboard = Blackboard(result.run_dir)
    try:
        stages = {row["stage_id"]: row for row in blackboard.table_rows("stages")}
        errors = blackboard.table_rows("error_details")
        artifacts = blackboard.table_rows("artifacts")
    finally:
        blackboard.close()
    assert stages["plan"]["status"] == "completed"
    assert not errors
    assert not [row for row in artifacts if row["kind"] == "project_design_doc"]
    assert "software-dev requires a project design document" not in report


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
            if stage_id == "implement":
                (worktree / "fix_loop.txt").write_text("initial implementation\n", encoding="utf-8")
                return ProviderStageOutput("session/implement.log", "implemented", "implemented")
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
                (worktree / "fix_loop.txt").write_text("fixed blocker\n", encoding="utf-8")
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
            if stage_id == "implement":
                (worktree / "provider_action_resume.txt").write_text("implemented after provider action\n", encoding="utf-8")
                return ProviderStageOutput("session/implement.log", "implemented", "implemented")
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


def test_unclear_task_creates_clarification_action_and_resume_uses_response(workspace: Path) -> None:
    runtime = SupervisorRuntime(workspace)
    paused = runtime.run("?", provider="mock", workflow_name="fix")

    assert paused.status == RunStatus.AWAITING_PROVIDER_ACTION
    blackboard = Blackboard(paused.run_dir)
    try:
        action = blackboard.list_provider_actions(status=str(ProviderActionStatus.PENDING))[0]
        assert action["kind"] == "clarification_required"
        assert action["input_kind"] == "text"
        blackboard.respond_provider_action(action["action_id"], response={"text": "Fix the navbar hover state and verify with tests."})
    finally:
        blackboard.close()

    resumed = runtime.resume(paused.run_id)
    packet = json.loads((resumed.run_dir / "context_packets" / "code.json").read_text(encoding="utf-8"))

    assert resumed.status == RunStatus.COMPLETED
    assert packet["task"]["provider_action_responses"][0]["response"] == {"text": "Fix the navbar hover state and verify with tests."}
