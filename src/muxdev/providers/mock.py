"""Deterministic offline provider used for tests and safe demos."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..models import PlanArtifact, ReviewResult, TestResult
from ..core.redaction import redact


@dataclass(frozen=True)
class MockStageOutput:
    artifact_name: str
    content: str
    summary: str
    tokens: int = 100
    cost_usd: float = 0.01


def _mock_design_payload(task: str, *, summary: str) -> dict[str, object]:
    return {
        "summary": summary,
        "design_doc": {
            "problem_statement": f"Create a complete design for: {task}",
            "scope": [
                "Produce a single user-facing design document.",
                "Keep the workflow design-only; do not implement code in this stage.",
            ],
            "requirements": [
                "The reader can understand the goal, flow, states, and failure conditions without reading source code.",
                "The design gives enough implementation detail for a lightweight browser game or app.",
            ],
            "constraints": ["Browser-first experience", "Small reversible scope", "No production data migration"],
            "non_goals": ["No implementation files are created by the design workflow."],
            "user_preferences": {
                "audience": "Casual browser users",
                "platform": "Desktop and mobile web browsers",
                "style": "Clear, lightweight, and easy to scan",
            },
            "audience": "Casual browser users",
            "platform": "Web browser",
            "core_loop": ["Start", "Interact with the main board", "Receive immediate feedback", "Restart or continue"],
            "controls": {"keyboard": "Primary keyboard controls where relevant", "mobile": "Touch-friendly controls"},
            "ui": ["Main play surface", "Current score or status", "Primary action buttons", "Mobile controls"],
            "states": ["start", "running", "paused", "completed", "failed"],
            "rules": ["Inputs update state on each tick", "Invalid actions are ignored", "End conditions are visible"],
            "scoring": "Expose score or progress when the task has game mechanics.",
            "acceptance_criteria": [
                "A reader can identify the target users, platform, and scope.",
                "A reader can follow the core interaction loop and state transitions.",
                "A downstream implementer can derive UI, rules, data, and test cases from the document.",
            ],
            "test_strategy": [
                "Inspect the design document for all required sections.",
                "Create future smoke tests for start, interaction, pause, restart, and end-state behavior.",
                "Check desktop and mobile control paths during implementation.",
            ],
            "proposed_design": {
                "approach": "Mock design output uses a small state-machine-oriented browser design.",
                "modules": ["StateModel", "Renderer", "InputController", "RulesEngine"],
            },
            "state_model": {"states": ["start", "running", "paused", "completed", "failed"]},
            "data_flow": ["User input", "State transition", "Rules evaluation", "UI render", "Feedback"],
            "implementation_sequence": [
                "Create the play surface and status UI.",
                "Implement state transitions and input handling.",
                "Add scoring/progress and end-state handling.",
                "Verify keyboard, touch, restart, and responsive behavior.",
            ],
            "risks_and_mitigations": [
                "Mobile controls may feel cramped; reserve stable button areas and verify on a narrow viewport.",
                "State transitions can drift; keep them explicit and test each transition.",
            ],
            "open_questions": ["Confirm final visual theme and exact difficulty tuning before implementation."],
        },
    }


class MockProvider:
    id = "mock"

    def run_stage(self, *, stage_id: str, task: str, worktree: Path) -> MockStageOutput:
        safe_task = redact(task)
        if stage_id == "direct":
            target = worktree / "muxdev_mock_direct.txt"
            target.write_text(f"Mock direct CLI result for task: {safe_task}\n", encoding="utf-8")
            content = "# Direct CLI Result\n\nmock direct CLI completed the task in one pass.\n"
            return MockStageOutput("direct_output.md", content, "mock direct CLI completed", tokens=70, cost_usd=0.005)
        if stage_id == "judge":
            payload = {
                "score": 0.82,
                "pass": True,
                "task_completion": 0.8,
                "answer_quality": 0.8,
                "groundedness": 0.75,
                "safety": 0.9,
                "process_quality": 0.8,
                "reasons": ["mock judge accepted the run"],
                "risks": [],
            }
            return MockStageOutput("validation/judge_mock.json", json.dumps(payload, ensure_ascii=False, indent=2), "mock judge completed", tokens=40, cost_usd=0.002)
        if stage_id in {"design", "plan", "quick_plan", "scaffold_plan"}:
            artifact = PlanArtifact(summary=f"Plan for: {safe_task}", steps=["inspect", "implement", "test", "review"])
            content = "# Plan\n\n" + artifact.summary + "\n\n```json\n" + artifact.model_dump_json(indent=2) + "\n```\n"
            return MockStageOutput(f"{stage_id}.md", content, artifact.summary)
        if stage_id in {"design_brief", "design_plan"}:
            payload = _mock_design_payload(safe_task, summary="Mock design plan")
            content = "# Design Plan\n\nMock design plan\n\n```json\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n```\n"
            return MockStageOutput(f"design/{stage_id}.md", content, "Mock design plan")
        if stage_id == "plan_revise":
            artifact = PlanArtifact(summary=f"Revised plan for: {safe_task}", steps=["adjust plan from feedback", "review", "implement", "verify"])
            content = "# Revised Plan\n\n" + artifact.summary + "\n\n```json\n" + artifact.model_dump_json(indent=2) + "\n```\n"
            return MockStageOutput("plan_revise.md", content, artifact.summary)
        if stage_id in {"implement", "code", "scaffold", "refactor"}:
            target = worktree / "muxdev_mock_change.txt"
            target.write_text(f"Mock implementation for task: {safe_task}\n", encoding="utf-8")
            return MockStageOutput(f"session/{stage_id}.log", "mock implementer wrote muxdev_mock_change.txt\n", "mock implementation completed")
        if stage_id in {"docs_update", "docs_fix"}:
            target = worktree / "docs" / "muxdev_mock_docs.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"# muxdev mock docs\n\nMock documentation update for task: {safe_task}\n", encoding="utf-8")
            content = f"# {stage_id.replace('_', ' ').title()}\n\nUpdated docs/muxdev_mock_docs.md for task: {safe_task}\n"
            return MockStageOutput(f"docs/{stage_id}.md", content, f"mock {stage_id} completed")
        if stage_id in {"test", "targeted_test", "smoke_check", "run_smoke"}:
            result = TestResult(passed=True, command="pytest", summary=f"mock {stage_id} passed")
            content = "mock test log\n" + result.model_dump_json(indent=2) + "\n"
            return MockStageOutput(f"{stage_id}.log", content, result.summary)
        if stage_id in {"review", "plan_review", "light_review", "review_test_result", "docs_review", "final_design_review"}:
            result = ReviewResult(has_blockers=False, blockers=[])
            content = "# Review\n\nNo blockers.\n\n```json\n" + result.model_dump_json(indent=2) + "\n```\n"
            return MockStageOutput(f"{stage_id}.md", content, "no blockers")
        if stage_id == "design_review":
            result = ReviewResult(has_blockers=False, blockers=[])
            content = "# Design Review\n\nNo blockers.\n\n```json\n" + result.model_dump_json(indent=2) + "\n```\n"
            return MockStageOutput("design/design_review.md", content, "design review passed")
        if stage_id == "design_revise":
            payload = _mock_design_payload(safe_task, summary="Mock revised design output")
            content = "# Revised Design\n\nMock revised design output\n\n```json\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n```\n"
            return MockStageOutput("design/design_revise.md", content, "mock design revision completed")
        if stage_id == "fix":
            return MockStageOutput("session/fix.log", "no fix needed\n", "fix skipped")
        if stage_id in {
            "task_intake",
            "project_brief",
            "problem_statement",
            "requirements",
            "architecture_options",
            "decision_record",
            "system_design",
            "api_and_data_model",
            "risk_and_threat_model",
            "test_strategy",
            "implementation_roadmap",
            "design_pack",
            "review_summary",
            "handoff_summary",
            "memory_proposals",
            "impact_check",
        }:
            title = stage_id.replace("_", " ").title()
            content = f"# {title}\n\nMock design output for task: {safe_task}\n"
            return MockStageOutput(f"design/{stage_id}.md", content, f"mock {stage_id} completed")
        return MockStageOutput(f"{stage_id}.md", json.dumps({"stage": stage_id}), f"mock {stage_id} completed")
