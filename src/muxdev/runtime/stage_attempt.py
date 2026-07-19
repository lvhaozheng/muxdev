"""Provider attempt execution for runtime stages."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..clients.stream import StreamAdapter, looks_like_auth_error
from ..core.redaction import redact
from ..core.text_cleaning import provider_action_text
from ..models import ProviderActionKind
from ..domain import StageExecutionResult
from ..providers.adapters import ProviderAdapter
from .stage_executor import StageExecutor

PROVIDER_MAX_ATTEMPTS = 2
TRANSIENT_RETRY_FAILURES = {"transient_provider_exit"}


def run_provider_stage_with_attempts(
    blackboard: Any,
    trace: Any,
    *,
    run_id: str,
    stage_id: str,
    role: str | None,
    provider: str,
    provider_impl: ProviderAdapter,
    task: str,
    worktree: Path,
    skills: list[dict[str, object]],
    session_dir: Path | None = None,
) -> tuple[StageExecutionResult, int]:
    attempt = next_provider_attempt(blackboard, run_id, stage_id, provider)
    max_attempt = attempt + PROVIDER_MAX_ATTEMPTS - 1
    while attempt <= max_attempt:
        certification = getattr(provider_impl, "certification_report", None)
        blackboard.start_provider_attempt(
            run_id,
            stage_id,
            provider=provider,
            role=role,
            attempt=attempt,
            certification_id=getattr(certification, "certification_id", None),
            adapter_version=getattr(provider_impl, "adapter_version", None),
            trust_tier=str(getattr(provider_impl, "trust_tier", "opaque")),
            isolation_mode=str(getattr(provider_impl, "isolation_mode", "process")),
            waiver_approval_id=getattr(provider_impl, "waiver_approval_id", None),
        )
        trace.write("provider_attempt_started", stage=stage_id, provider=provider, attempt=attempt)
        output = run_provider_stage(
            provider_impl, stage_id=stage_id, task=task, worktree=worktree,
            skills=skills, session_dir=session_dir, run_id=run_id, role=role, provider=provider, attempt=attempt,
        )
        failure_kind = provider_failure_kind(output)
        has_action = bool(provider_actions_from_output(output))
        blackboard.complete_provider_attempt(
            run_id,
            stage_id,
            provider=provider,
            attempt=attempt,
            status=provider_attempt_status(output),
            failure_kind=failure_kind,
            returncode=output.returncode,
            summary=output.summary,
            harness_events=output.harness_events,
        )
        if output.returncode != 0 and not has_action and failure_kind in TRANSIENT_RETRY_FAILURES and attempt < max_attempt:
            blackboard.complete_provider_attempt(
                run_id,
                stage_id,
                provider=provider,
                attempt=attempt,
                status="retried",
                failure_kind=failure_kind,
                returncode=output.returncode,
                summary=output.summary,
            )
            trace.write("provider_retry_scheduled", stage=stage_id, provider=provider, attempt=attempt, failure_kind=failure_kind)
            attempt += 1
            continue
        return output, attempt
    return output, attempt


def next_provider_attempt(blackboard: Any, run_id: str, stage_id: str, provider: str) -> int:
    attempts = [
        int(row.get("attempt") or 0)
        for row in blackboard.table_rows("provider_attempts", run_id=run_id)
        if row.get("stage_id") == stage_id and row.get("provider") == provider
    ]
    return (max(attempts) if attempts else 0) + 1


def provider_attempt_status(output: StageExecutionResult) -> str:
    if provider_actions_from_output(output):
        return "provider_action"
    if output.returncode == 0:
        return "succeeded"
    return "failed"


def provider_failure_kind(output: StageExecutionResult) -> str | None:
    actions = provider_actions_from_output(output)
    if actions:
        return str(actions[0].get("kind") or ProviderActionKind.PROVIDER_BLOCKED)
    text = f"{output.summary}\n{output.content}".lower()
    if output.returncode == 124 or "idle_timeout" in text or "no output for" in text:
        return str(ProviderActionKind.IDLE_TIMEOUT)
    if looks_like_auth_error(text):
        return str(ProviderActionKind.AUTH_REQUIRED)
    if "rate limit" in text or "rate-limit" in text or "too many requests" in text or "quota exceeded" in text:
        return str(ProviderActionKind.RATE_LIMIT)
    if output.returncode != 0 and any(token in text for token in ("temporary", "timed out", "timeout", "connection reset", "network error", "econnreset")):
        return "transient_provider_exit"
    if output.returncode != 0:
        return "provider_exit"
    return None


def provider_actions_from_output(output: StageExecutionResult) -> list[dict[str, object]]:
    if output.provider_actions:
        return output.provider_actions
    provider_text = output.content.split("\n\n# Stream Events\n", 1)[0]
    action_text = provider_action_text(output.summary + "\n" + provider_text)
    if not action_text:
        events = []
    else:
        events = StreamAdapter().parse_chunk(action_text)
    actions = [
        {
            "kind": action.kind,
            "prompt_text": action.prompt_text,
            "options": action.options,
            "input_kind": "confirmation" if action.kind == str(ProviderActionKind.CLI_CONFIRMATION) else ("external" if not action.options else "choice"),
            "choices": action.options,
            "default_choice": _default_choice(action.options),
            "auto_policy": "manual",
        }
        for action in StreamAdapter().provider_actions(events)
    ]
    if actions:
        return actions
    if output.returncode == 124:
        return [
            {
                "kind": str(ProviderActionKind.IDLE_TIMEOUT),
                "prompt_text": output.summary or "provider session timed out without output",
                "options": [],
                "input_kind": "external",
                "choices": [],
                "auto_policy": "manual",
            }
        ]
    return []


def run_provider_stage(
    provider_impl: ProviderAdapter,
    *,
    stage_id: str,
    task: str,
    worktree: Path,
    skills: list[dict[str, object]],
    session_dir: Path | None = None,
    run_id: str | None = None,
    role: str | None = None,
    provider: str | None = None,
    attempt: int = 1,
) -> StageExecutionResult:
    return StageExecutor(provider_impl).execute(
        run_id=run_id or "unbound",
        stage_id=stage_id,
        role=role,
        provider=provider or str(getattr(provider_impl, "id", "unknown")),
        task=task,
        worktree=worktree,
        skills=skills,
        session_dir=session_dir,
        attempt=attempt,
    )


def persist_stage_execution_result(
    blackboard: Any,
    trace: Any,
    *,
    run_dir: Path,
    run_id: str,
    stage_id: str,
    provider: str,
    attempt: int,
    output: StageExecutionResult,
    write_text: Callable[[Path, str], None],
) -> Path:
    """Persist the common Provider result envelope for all schedulers."""
    artifact_path = run_dir / output.artifact_name
    write_text(artifact_path, redact(output.content))
    blackboard.add_usage(run_id, provider, output.tokens, output.cost_usd)
    blackboard.add_artifact(run_id, stage_id, output.artifact_name, artifact_path, "stage_output")
    trace.write(
        "provider_event",
        stage=stage_id,
        provider=provider,
        returncode=output.returncode,
        artifact=str(artifact_path),
    )
    blackboard.complete_provider_attempt(
        run_id,
        stage_id,
        provider=provider,
        attempt=attempt,
        status=provider_attempt_status(output),
        failure_kind=provider_failure_kind(output),
        returncode=output.returncode,
        summary=output.summary,
        artifact_path=str(artifact_path),
        harness_events=output.harness_events,
    )
    return artifact_path


def _default_choice(options: list[dict[str, object]]) -> str | None:
    for option in options:
        if option.get("default") and option.get("value") is not None:
            return str(option["value"])
    return None
