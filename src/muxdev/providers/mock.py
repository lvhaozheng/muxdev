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


class MockProvider:
    id = "mock"

    def run_stage(self, *, stage_id: str, task: str, worktree: Path) -> MockStageOutput:
        safe_task = redact(task)
        if stage_id == "design":
            artifact = PlanArtifact(summary=f"Plan for: {safe_task}", steps=["inspect", "implement", "test", "review"])
            content = "# Plan\n\n" + artifact.summary + "\n\n```json\n" + artifact.model_dump_json(indent=2) + "\n```\n"
            return MockStageOutput("plan.md", content, artifact.summary)
        if stage_id in {"implement", "code"}:
            target = worktree / "muxdev_mock_change.txt"
            target.write_text(f"Mock implementation for task: {safe_task}\n", encoding="utf-8")
            return MockStageOutput(f"session/{stage_id}.log", "mock implementer wrote muxdev_mock_change.txt\n", "mock implementation completed")
        if stage_id == "test":
            result = TestResult(passed=True, command="pytest", summary="mock tests passed")
            content = "mock test log\n" + result.model_dump_json(indent=2) + "\n"
            return MockStageOutput("test.log", content, result.summary)
        if stage_id == "review":
            result = ReviewResult(has_blockers=False, blockers=[])
            content = "# Review\n\nNo blockers.\n\n```json\n" + result.model_dump_json(indent=2) + "\n```\n"
            return MockStageOutput("review.md", content, "no blockers")
        if stage_id == "design_review":
            result = ReviewResult(has_blockers=False, blockers=[])
            content = "# Design Review\n\nNo blockers.\n\n```json\n" + result.model_dump_json(indent=2) + "\n```\n"
            return MockStageOutput("design/design_review.md", content, "design review passed")
        if stage_id == "design_revise":
            content = f"# Revised Design\n\nMock revised design output for task: {safe_task}\n"
            return MockStageOutput("design/design_revise.md", content, "mock design revision completed")
        if stage_id == "fix":
            return MockStageOutput("session/fix.log", "no fix needed\n", "fix skipped")
        if stage_id in {
            "problem_statement",
            "requirements",
            "architecture_options",
            "decision_record",
            "system_design",
            "api_and_data_model",
            "risk_and_threat_model",
            "test_strategy",
            "implementation_roadmap",
            "final_design_review",
            "memory_proposals",
        }:
            title = stage_id.replace("_", " ").title()
            content = f"# {title}\n\nMock design output for task: {safe_task}\n"
            return MockStageOutput(f"design/{stage_id}.md", content, f"mock {stage_id} completed")
        return MockStageOutput(f"{stage_id}.md", json.dumps({"stage": stage_id}), f"mock {stage_id} completed")
