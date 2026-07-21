from __future__ import annotations

from muxdev.domain import AttemptFeedback, CapabilityGrant, StageExecutionInput, StageExecutionResult
from muxdev.providers.adapters import HeadlessCliProviderAdapter, _capability_scoped_command
from muxdev.providers.capabilities import provider_capabilities
from muxdev.models.evidence import FailureDiagnosis
from muxdev.runtime.recovery import (
    _next_actions,
    build_attempt_feedback,
    classify_provider_failure,
)
from muxdev.runtime.result_validation import ContractValidation
from muxdev.runtime.run_setup import resumable_session_id


def test_attempt_feedback_is_bounded_and_redacted() -> None:
    result = StageExecutionResult(
        artifact_name="bad.json",
        content="sk-SECRET " + "x" * 20_000,
        summary="invalid",
        returncode=1,
        stderr_content="Bearer private.token " + "e" * 10_000,
    )
    validation = ContractValidation(
        "ChangeResult", False, tuple(f"error {index}" for index in range(30)), None
    )

    feedback = build_attempt_feedback(
        result,
        prior_attempt=1,
        failure_kind="output_contract",
        workspace_diff_digest="sha256:subject",
        validation=validation,
    )

    assert len(feedback.prior_output_excerpt.encode()) <= 8 * 1024
    assert len(feedback.validation_errors) == 20
    assert "sk-SECRET" not in feedback.prior_output_excerpt
    assert all("Bearer private.token" not in error for error in feedback.validation_errors)


def test_headless_prompt_contains_previous_attempt_feedback(workspace) -> None:
    feedback = AttemptFeedback(
        prior_attempt=1,
        failure_kind="output_contract",
        error_codes=("output_contract_invalid",),
        validation_errors=("missing required field: summary",),
        prior_output_excerpt='{"affected_paths": []}',
        instruction="Return corrected JSON only.",
    )
    stage_input = StageExecutionInput(
        run_id="run_feedback",
        stage_id="implement",
        role="code",
        task="change safely",
        worktree=workspace,
        context={},
        capabilities=CapabilityGrant(read_workspace=True),
        provider="example",
        policy={"output_schema": "ChangeResult"},
        feedback=feedback,
    )
    adapter = HeadlessCliProviderAdapter(
        "example", ["example"], timeout=10,
        prompt_template="Stage {stage_id}: {task}", prompt_transport="stdin",
    )

    prompt = adapter._prompt(stage_input)

    assert "Previous attempt feedback" in prompt
    assert "output_contract_invalid" in prompt
    assert "missing required field: summary" in prompt
    assert "Return corrected JSON only." in prompt


def test_reduced_grant_projects_codex_command_to_read_only(workspace) -> None:
    stage_input = StageExecutionInput(
        run_id="run_read_only", stage_id="repair", role="code", task="repair output",
        worktree=workspace, context={}, capabilities=CapabilityGrant(read_workspace=True),
        provider="codex", policy={},
    )

    command = _capability_scoped_command(
        ["codex", "exec", "--sandbox", "workspace-write"], stage_input
    )

    assert command == ["codex", "exec", "--sandbox", "read-only"]


def test_headless_provider_resumes_only_the_explicit_session(workspace) -> None:
    stage_input = StageExecutionInput(
        run_id="run_resume", stage_id="implement", role="code", task="continue",
        worktree=workspace,
        context={"previous_provider_session_id": "session-safe-123"},
        capabilities=CapabilityGrant(read_workspace=True, write_workspace=True),
        provider="example", policy={},
    )
    adapter = HeadlessCliProviderAdapter(
        "example", ["example", "new"], timeout=10,
        prompt_template="{task}", prompt_transport="stdin",
        resume_command=["example", "resume", "{session_id}"],
        resume_prompt_transport="stdin",
    )

    invocation, resumed = adapter._invocation(stage_input, "continue")

    assert invocation.argv == ("example", "resume", "session-safe-123")
    assert invocation.stdin == "continue"
    assert resumed == "session-safe-123"


def test_default_codex_declares_native_session_resume(workspace) -> None:
    assert provider_capabilities(workspace, "codex")["session_resume"] is True


def test_implementer_session_never_leaks_to_reviewer() -> None:
    metadata = {
        "delivery_context": {
            "implementer_session": {"provider": "codex", "session_id": "session-1"}
        }
    }

    assert resumable_session_id(
        metadata, stage_role="code", provider="codex", can_write=True
    ) == "session-1"
    assert resumable_session_id(
        metadata, stage_role="review", provider="codex", can_write=False
    ) == ""


def test_provider_failure_classification_controls_safe_retry() -> None:
    auth = classify_provider_failure(StageExecutionResult(
        artifact_name="stderr.log", content="", summary="failed", returncode=1,
        stderr_content="Authorization: Bearer secret-token\n401 unauthorized",
    ))
    transient = classify_provider_failure(StageExecutionResult(
        artifact_name="stderr.log", content="", summary="failed", returncode=1,
        stderr_content="429 rate limit; try again",
    ))

    assert auth.code == "provider_auth_failed" and auth.transient is False
    assert "secret-token" not in " ".join(auth.details)
    assert transient.code == "provider_rate_limited" and transient.transient is True


def test_switch_provider_action_only_exists_for_a_real_fallback() -> None:
    diagnosis = FailureDiagnosis(
        code="provider_failure", kind="provider_failure", summary="failed"
    )

    without_fallback = _next_actions(
        "run_1", "lite", diagnosis, fallback_provider=None
    )
    with_fallback = _next_actions(
        "run_1", "lite", diagnosis, fallback_provider="backup"
    )

    assert [item.action for item in without_fallback] == ["retry"]
    assert [item.action for item in with_fallback] == ["retry", "switch-provider"]
