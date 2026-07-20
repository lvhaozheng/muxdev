"""Small deterministic Provider used for offline control-plane tests."""

from __future__ import annotations

import json
import sys

from pydantic import BaseModel

from ..core.redaction import redact
from ..domain import StageExecutionInput, StageExecutionResult
from ..models import ChangeResult, PlanResult, ReviewResult, TestResult


class MockProvider:
    id = "mock"

    def execute(self, input: StageExecutionInput) -> StageExecutionResult:
        task = redact(input.task)
        if input.stage_id in {"plan", "test_plan", "design", "revise"}:
            result = PlanResult(
                summary=f"Plan for: {task}",
                steps=["inspect", "change", "verify", "review"],
                risks=["Mock output proves orchestration behavior, not Provider quality."],
            )
            return self._result(input, result, "plan.json")
        if input.stage_id in {"implement", "fix"}:
            target = input.worktree / "muxdev_mock_change.txt"
            target.write_text(f"Mock implementation for task: {task}\n", encoding="utf-8")
            result = ChangeResult(
                summary="mock implementation completed",
                affected_paths=[target.name],
                suggested_verification=["runtime mock check"],
            )
            return self._result(input, result, "change.json")
        if input.stage_id == "test":
            result = TestResult(
                checks=[{
                    "id": "mock-runtime-check",
                    "criteria_ids": ["mock-acceptance"],
                    "argv": [sys.executable, "-c", "print('muxdev mock check')"],
                    "status": "passed",
                    "exit_code": 0,
                    "summary": "runtime-reproducible mock check",
                }],
                criteria_covered=["mock-acceptance"],
            )
            return self._result(input, result, "test.json")
        if input.stage_id in {"review", "security_review"}:
            result = ReviewResult(
                target_subject=str(input.context.get("subject_digest") or "runtime-bound"),
                findings=[],
                residual_risk="Mock review is simulation-only.",
            )
            return self._result(input, result, "review.json")
        return StageExecutionResult(
            artifact_name="unsupported.json",
            content=json.dumps({"error": f"unsupported mock stage: {input.stage_id}"}),
            summary="unsupported mock stage",
            stage_id=input.stage_id,
            provider=self.id,
            status="failed",
            returncode=2,
        )

    def _result(self, input: StageExecutionInput, result: BaseModel, artifact_name: str) -> StageExecutionResult:
        content = result.model_dump_json(indent=2)
        return StageExecutionResult(
            artifact_name=artifact_name,
            content=content,
            summary=str(getattr(result, "summary", input.stage_id)),
            stage_id=input.stage_id,
            provider=self.id,
            tokens=100,
            cost_usd=0.01,
        )
