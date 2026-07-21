from __future__ import annotations

import json
from pathlib import Path

from muxdev.domain import StageExecutionResult
from muxdev.models import ExecutedCheck
from muxdev.models.evidence import canonical_hash
from muxdev.providers.mock import MockProvider
from muxdev.runtime import RunEngine


def _result(stage_input, payload: dict[str, object], *, name: str = "result.json") -> StageExecutionResult:
    return StageExecutionResult(
        artifact_name=name,
        content=json.dumps(payload),
        summary=str(payload.get("summary") or "provider result"),
        stage_id=stage_input.stage_id,
        provider="mock",
    )


def test_invalid_model_output_is_corrected_with_prior_errors_without_rewriting_code(
    workspace: Path, monkeypatch
) -> None:
    feedback_seen = []

    class ContractRepairProvider:
        def execute(self, stage_input):
            if stage_input.stage_id != "implement":
                return MockProvider().execute(stage_input)
            if stage_input.feedback:
                feedback_seen.append(stage_input)
                return _result(stage_input, {
                    "summary": "corrected structured result",
                    "affected_paths": ["recovered_change.txt"],
                    "suggested_verification": ["git diff --check"],
                })
            (stage_input.worktree / "recovered_change.txt").write_text("preserved\n", encoding="utf-8")
            return _result(stage_input, {"affected_paths": ["recovered_change.txt"]})

    monkeypatch.setattr(
        "muxdev.runtime.engine.get_runtime_provider", lambda *_args, **_kwargs: ContractRepairProvider()
    )
    engine = RunEngine(workspace)
    result = engine.run("preserve good code and repair its result", provider="mock", profile="lite")
    report = json.loads(result.report_path.read_text(encoding="utf-8"))

    assert str(result.status) == "completed"
    assert (workspace / "recovered_change.txt").read_text(encoding="utf-8") == "preserved\n"
    assert len(feedback_seen) == 1
    correction = feedback_seen[0]
    assert correction.capabilities.write_workspace is False
    assert correction.capabilities.shell is False and correction.capabilities.network is False
    assert any("summary" in error for error in correction.feedback.validation_errors)
    assert "affected_paths" in correction.feedback.prior_output_excerpt
    assert report["recovery"]["status"] == "recovered"
    assert report["recovery"]["actions_used"] == 1
    engine.store.close()


def test_output_correction_write_is_rolled_back_before_full_stage_retry(
    workspace: Path, monkeypatch
) -> None:
    class MutatingCorrectionProvider:
        def execute(self, stage_input):
            if stage_input.stage_id != "implement":
                return MockProvider().execute(stage_input)
            if stage_input.feedback and not stage_input.capabilities.write_workspace:
                (stage_input.worktree / "correction_intrusion.txt").write_text("forbidden", encoding="utf-8")
                return _result(stage_input, {"summary": "looks valid", "affected_paths": []})
            if stage_input.feedback:
                (stage_input.worktree / "recovered_change.txt").write_text("rerun\n", encoding="utf-8")
                return _result(stage_input, {"summary": "full retry", "affected_paths": ["recovered_change.txt"]})
            (stage_input.worktree / "recovered_change.txt").write_text("first\n", encoding="utf-8")
            return _result(stage_input, {"affected_paths": ["recovered_change.txt"]})

    monkeypatch.setattr(
        "muxdev.runtime.engine.get_runtime_provider", lambda *_args, **_kwargs: MutatingCorrectionProvider()
    )
    engine = RunEngine(workspace)
    result = engine.run("reject output-repair writes", provider="mock", profile="lite")
    report = json.loads(result.report_path.read_text(encoding="utf-8"))

    assert str(result.status) == "completed"
    assert (workspace / "recovered_change.txt").read_text(encoding="utf-8") == "rerun\n"
    assert not (workspace / "correction_intrusion.txt").exists()
    assert report["recovery"]["actions_used"] == 2
    assert [item["status"] for item in report["recovery"]["attempts"]] == ["failed", "succeeded"]
    engine.store.close()


def test_two_failed_output_repairs_stop_with_one_actionable_diagnosis(
    workspace: Path, monkeypatch
) -> None:
    class AlwaysInvalidProvider:
        def execute(self, stage_input):
            if stage_input.stage_id != "implement":
                return MockProvider().execute(stage_input)
            if stage_input.capabilities.write_workspace:
                (stage_input.worktree / "invalid_change.txt").write_text("not delivered\n", encoding="utf-8")
            return _result(stage_input, {"affected_paths": ["invalid_change.txt"]})

    monkeypatch.setattr(
        "muxdev.runtime.engine.get_runtime_provider", lambda *_args, **_kwargs: AlwaysInvalidProvider()
    )
    engine = RunEngine(workspace)
    result = engine.run("stop after bounded output recovery", provider="mock", profile="lite")
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    stages = engine.store.stages(result.run_id)

    assert str(result.status) == "blocked"
    assert report["recovery"]["status"] == "exhausted"
    assert report["recovery"]["actions_used"] == 2
    assert report["recovery"]["primary_failure"]["kind"] == "output_contract"
    assert report["recovery"]["next_actions"][0]["action"] == "inspect"
    assert "implement" in {item["stage_id"] for item in stages}
    assert not {"test", "review"} & {item["stage_id"] for item in stages}
    assert not (workspace / "invalid_change.txt").exists()
    engine.store.close()


def test_provider_timeout_retry_receives_redacted_previous_failure(
    workspace: Path, monkeypatch
) -> None:
    retry_inputs = []

    class TimeoutThenSuccessProvider:
        def execute(self, stage_input):
            if stage_input.stage_id != "implement":
                return MockProvider().execute(stage_input)
            if stage_input.feedback:
                retry_inputs.append(stage_input)
                (stage_input.worktree / "timeout_recovery.txt").write_text("done\n", encoding="utf-8")
                return _result(stage_input, {"summary": "retry succeeded", "affected_paths": ["timeout_recovery.txt"]})
            (stage_input.worktree / "timeout_recovery.txt").write_text("partial\n", encoding="utf-8")
            return StageExecutionResult(
                artifact_name="timeout.log", content="partial output", summary="provider timed out",
                stage_id="implement", provider="mock", status="failed", returncode=124,
                stderr_content="reconnect failed using sk-SECRET123", timed_out=True,
            )

    monkeypatch.setattr(
        "muxdev.runtime.engine.get_runtime_provider", lambda *_args, **_kwargs: TimeoutThenSuccessProvider()
    )
    engine = RunEngine(workspace)
    result = engine.run("retry a transient timeout", provider="mock", profile="lite")
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    serialized_events = json.dumps(engine.store.events(result.run_id), ensure_ascii=False)

    assert str(result.status) == "completed"
    assert (workspace / "timeout_recovery.txt").read_text(encoding="utf-8") == "done\n"
    assert retry_inputs[0].feedback.failure_kind == "provider_timeout"
    assert "provider_timeout" in retry_inputs[0].feedback.error_codes
    assert "sk-SECRET123" not in serialized_events and "[REDACTED]" in serialized_events
    assert report["recovery"]["actions_used"] == 1
    engine.store.close()


def test_failed_runtime_check_is_sent_to_the_fix_model(
    workspace: Path, monkeypatch
) -> None:
    fix_inputs = []
    checks = 0

    class FeedbackFixProvider:
        def execute(self, stage_input):
            if stage_input.stage_id == "fix":
                fix_inputs.append(stage_input)
            return MockProvider().execute(stage_input)

    def run_check(_self, command, _worktree, _run_id, _stage_id):
        nonlocal checks
        checks += 1
        failed = checks == 1
        return ExecutedCheck(
            id=command.id,
            argv=command.argv,
            cwd=command.cwd,
            cwd_digest="sha256:cwd",
            exit_code=1 if failed else 0,
            duration_ms=1,
            stdout_digest=canonical_hash(""),
            stderr_digest=canonical_hash("assertion failed" if failed else ""),
            stderr_summary="assertion failed" if failed else "",
        )

    monkeypatch.setattr(
        "muxdev.runtime.engine.get_runtime_provider", lambda *_args, **_kwargs: FeedbackFixProvider()
    )
    monkeypatch.setattr(RunEngine, "_run_check", run_check)
    engine = RunEngine(workspace)
    result = engine.run("repair a failing runtime check", provider="mock", profile="lite")
    report = json.loads(result.report_path.read_text(encoding="utf-8"))

    assert str(result.status) == "completed"
    assert len(fix_inputs) == 1
    feedback = fix_inputs[0].feedback
    assert feedback and feedback.failure_kind == "test_failure"
    assert feedback.failed_checks[0]["exit_code"] == 1
    assert "assertion failed" in str(feedback.failed_checks[0]["stderr"])
    assert report["recovery"]["status"] == "recovered"
    engine.store.close()


def test_review_blocker_is_sent_to_the_fix_model(
    workspace: Path, monkeypatch
) -> None:
    fix_inputs = []
    review_calls = 0

    class ReviewThenFixProvider:
        def execute(self, stage_input):
            nonlocal review_calls
            if stage_input.stage_id == "fix":
                fix_inputs.append(stage_input)
                return MockProvider().execute(stage_input)
            if stage_input.stage_id == "review":
                review_calls += 1
                findings = [] if review_calls > 1 else [{
                    "type": "correctness",
                    "severity": "high",
                    "message": "A required edge case is missing.",
                    "remediation": "Handle the empty input path.",
                    "file": "muxdev_mock_change.txt",
                }]
                return _result(stage_input, {
                    "target_subject": stage_input.context["subject_digest"],
                    "findings": findings,
                    "residual_risk": "none",
                })
            return MockProvider().execute(stage_input)

    monkeypatch.setattr(
        "muxdev.runtime.engine.get_runtime_provider", lambda *_args, **_kwargs: ReviewThenFixProvider()
    )
    engine = RunEngine(workspace)
    result = engine.run("repair a review blocker", provider="mock", profile="lite")
    report = json.loads(result.report_path.read_text(encoding="utf-8"))

    assert str(result.status) == "completed"
    assert len(fix_inputs) == 1
    feedback = fix_inputs[0].feedback
    assert feedback and feedback.failure_kind == "review_blocker"
    assert feedback.review_blockers[0]["message"] == "A required edge case is missing."
    assert feedback.review_blockers[0]["remediation"] == "Handle the empty input path."
    assert report["recovery"]["actions_used"] == 1
    engine.store.close()
