"""Provider attempt execution for runtime stages."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from ..clients.stream import StreamAdapter
from ..models import ProviderActionKind
from ..providers.adapters import ProviderAdapter, ProviderStageOutput

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
) -> tuple[ProviderStageOutput, int]:
    attempt = next_provider_attempt(blackboard, run_id, stage_id, provider)
    max_attempt = attempt + PROVIDER_MAX_ATTEMPTS - 1
    while attempt <= max_attempt:
        blackboard.start_provider_attempt(run_id, stage_id, provider=provider, role=role, attempt=attempt)
        trace.write("provider_attempt_started", stage=stage_id, provider=provider, attempt=attempt)
        output = run_provider_stage(provider_impl, stage_id=stage_id, task=task, worktree=worktree, skills=skills, session_dir=session_dir)
        failure_kind = provider_failure_kind(output)
        has_action = bool(provider_actions_from_output(output))
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


def provider_attempt_status(output: ProviderStageOutput) -> str:
    if provider_actions_from_output(output):
        return "provider_action"
    if output.returncode == 0:
        return "succeeded"
    return "failed"


def provider_failure_kind(output: ProviderStageOutput) -> str | None:
    actions = provider_actions_from_output(output)
    if actions:
        return str(actions[0].get("kind") or ProviderActionKind.PROVIDER_BLOCKED)
    text = f"{output.summary}\n{output.content}".lower()
    if output.returncode == 124 or "idle_timeout" in text or "no output for" in text:
        return str(ProviderActionKind.IDLE_TIMEOUT)
    if "please sign in" in text or ("auth" in text and ("login" in text or "error" in text)):
        return str(ProviderActionKind.AUTH_REQUIRED)
    if "rate limit" in text or "rate-limit" in text or "too many requests" in text or "quota exceeded" in text:
        return str(ProviderActionKind.RATE_LIMIT)
    if output.returncode != 0 and any(token in text for token in ("temporary", "timed out", "timeout", "connection reset", "network error", "econnreset")):
        return "transient_provider_exit"
    if output.returncode != 0:
        return "provider_exit"
    return None


def provider_actions_from_output(output: ProviderStageOutput) -> list[dict[str, object]]:
    if output.provider_actions:
        return output.provider_actions
    provider_text = output.content.split("\n\n# Stream Events\n", 1)[0]
    events = StreamAdapter().parse_chunk(output.summary + "\n" + provider_text)
    actions = [
        {
            "kind": action.kind,
            "prompt_text": action.prompt_text,
            "options": action.options,
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
) -> ProviderStageOutput:
    kwargs = provider_stage_kwargs(
        provider_impl,
        stage_id=stage_id,
        task=task,
        worktree=worktree,
        skills=skills,
        session_dir=session_dir,
    )
    return provider_impl.run_stage(**kwargs)


def provider_stage_kwargs(
    provider_impl: ProviderAdapter,
    *,
    stage_id: str,
    task: str,
    worktree: Path,
    skills: list[dict[str, object]],
    session_dir: Path | None,
) -> dict[str, object]:
    kwargs: dict[str, object] = {"stage_id": stage_id, "task": task, "worktree": worktree}
    try:
        signature = inspect.signature(provider_impl.run_stage)
    except (TypeError, ValueError):
        kwargs["skills"] = skills
        if session_dir is not None:
            kwargs["session_dir"] = session_dir
        return kwargs

    parameters = signature.parameters
    accepts_extra = any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
    if accepts_extra or "skills" in parameters:
        kwargs["skills"] = skills
    if session_dir is not None and (accepts_extra or "session_dir" in parameters):
        kwargs["session_dir"] = session_dir
    return kwargs
