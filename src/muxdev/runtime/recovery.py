"""Bounded recovery, prior-attempt feedback, and actionable failure diagnosis."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence
from uuid import uuid4

from ..core.redaction import redact
from ..domain import AttemptFeedback, StageExecutionResult
from ..models.evidence import (
    AnyEvidenceRecord,
    CheckEvidence,
    FailureDiagnosis,
    GateDecision,
    RecoveryAction,
    RecoveryAttempt,
    RecoverySummary,
    ReviewEvidence,
    RuntimeEvidence,
)
from ..storage.control import ControlStore
from .result_validation import ContractValidation
from .workspace import WorkspaceSnapshot, snapshot_workspace


MAX_RECOVERY_ACTIONS = 2
MAX_ERRORS = 20
MAX_PRIOR_OUTPUT_BYTES = 8 * 1024
MAX_STREAM_BYTES = 4 * 1024


@dataclass(frozen=True)
class ProviderFailureProfile:
    """A safe, user-facing classification of one provider failure."""

    code: str
    kind: str
    summary: str
    details: tuple[str, ...]
    transient: bool
    returncode: int | None = None


def classify_provider_failure(
    result: StageExecutionResult | None,
    *,
    exception: BaseException | None = None,
) -> ProviderFailureProfile:
    """Classify failures without exposing the provider's unbounded raw streams."""
    returncode = result.returncode if result is not None else None
    stderr = _bounded(result.stderr_content or "", MAX_STREAM_BYTES) if result else ""
    exception_text = (
        _bounded(f"{type(exception).__name__}: {exception}", MAX_STREAM_BYTES)
        if exception is not None else ""
    )
    searchable = "\n".join((stderr, exception_text)).lower()
    details = [item for item in (f"退出码 {returncode}" if returncode is not None else "", stderr, exception_text) if item]
    if (result and (result.timed_out or returncode == 124)) or "timed out" in searchable or "timeout" in searchable:
        return ProviderFailureProfile(
            "provider_timeout", "provider_timeout", "Provider 执行超时。",
            tuple(details), True, returncode,
        )
    if (
        (result is not None and result.cancelled)
        or (returncode is not None and returncode < 0)
        or any(item in searchable for item in ("segmentation fault", "panic:", "process crashed"))
    ):
        return ProviderFailureProfile(
            "provider_process_failed", "provider_failure", "Provider 进程异常终止。",
            tuple(details), False, returncode,
        )
    rules = (
        (
            ("rate limit", "too many requests", "429", "quota exceeded", "temporarily unavailable"),
            "provider_rate_limited", "Provider 暂时限流或服务不可用。", True,
        ),
        (
            ("connection reset", "connection refused", "dns", "econn", "network", "temporary failure"),
            "provider_network_error", "Provider 网络连接暂时失败。", True,
        ),
        (
            ("unauthorized", "authentication", "api key", "invalid token", "credential", "401"),
            "provider_auth_failed", "Provider 认证失败，请检查登录状态或凭据。", False,
        ),
        (
            ("permission denied", "access is denied", "operation not permitted", "sandbox", "forbidden", "403"),
            "provider_permission_denied", "Provider 被权限或沙箱策略阻止。", False,
        ),
        (
            ("unknown option", "unexpected argument", "invalid argument", "invalid value", "usage:"),
            "provider_command_invalid", "Provider 启动参数或配置无效。", False,
        ),
        (
            ("no such file", "not found", "winerror 2", "filenotfounderror"),
            "provider_process_failed", "Provider 进程无法启动或已异常退出。", False,
        ),
    )
    for patterns, code, summary, transient in rules:
        if any(pattern in searchable for pattern in patterns):
            return ProviderFailureProfile(
                code, "provider_failure", summary, tuple(details), transient, returncode
            )
    return ProviderFailureProfile(
        "provider_failure", "provider_failure",
        f"Provider 异常退出{f'（退出码 {returncode}）' if returncode is not None else ''}。",
        tuple(details), True, returncode,
    )


@dataclass(frozen=True)
class WorkspaceCheckpoint:
    """A file-level checkpoint for one isolated write attempt."""

    worktree: Path
    backup: Path
    snapshot: WorkspaceSnapshot

    @classmethod
    def create(cls, worktree: Path, recovery_dir: Path, *, stage_id: str) -> "WorkspaceCheckpoint":
        worktree = worktree.resolve()
        snapshot = snapshot_workspace(worktree)
        backup = (recovery_dir / f"checkpoint-{_safe_name(stage_id)}-{uuid4().hex[:8]}").resolve()
        backup.mkdir(parents=True, exist_ok=False)
        for relative, item in snapshot.files.items():
            if item.kind != "file":
                raise ValueError(f"recovery checkpoint does not support symlink: {relative}")
            source = _contained(worktree, relative)
            target = _contained(backup, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        return cls(worktree=worktree, backup=backup, snapshot=snapshot)

    def restore(self) -> None:
        """Restore only files inside the isolated worktree; never touch the user workspace."""
        current = snapshot_workspace(self.worktree)
        for relative in sorted(set(current.files) - set(self.snapshot.files), reverse=True):
            target = _contained(self.worktree, relative)
            if target.is_file() or target.is_symlink():
                target.unlink()
        for relative, item in self.snapshot.files.items():
            if item.kind != "file":
                raise ValueError(f"recovery checkpoint does not support symlink: {relative}")
            source = _contained(self.backup, relative)
            target = _contained(self.worktree, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        restored = snapshot_workspace(self.worktree)
        if restored.digest != self.snapshot.digest:
            raise RuntimeError("isolated worktree checkpoint restoration failed integrity verification")


def build_attempt_feedback(
    result: StageExecutionResult | None,
    *,
    prior_attempt: int,
    failure_kind: str,
    workspace_diff_digest: str,
    validation: ContractValidation | None = None,
    error_codes: Sequence[str] = (),
    failed_checks: Sequence[Mapping[str, object]] = (),
    review_blockers: Sequence[Mapping[str, object]] = (),
    history: Sequence[Mapping[str, object]] = (),
    exception: BaseException | None = None,
    instruction: str | None = None,
) -> AttemptFeedback:
    errors = list(validation.errors if validation else ())
    if exception is not None:
        errors.append(f"{type(exception).__name__}: {exception}")
    output = result.content if result is not None else ""
    stderr = result.stderr_content if result is not None else ""
    if stderr:
        errors.append("provider stderr: " + _bounded(stderr, MAX_STREAM_BYTES))
    codes = list(error_codes)
    if result is not None:
        if result.timed_out or result.returncode == 124:
            codes.append("provider_timeout")
        elif result.returncode != 0:
            codes.append(f"provider_exit_{result.returncode}")
    return AttemptFeedback(
        prior_attempt=prior_attempt,
        failure_kind=failure_kind,
        error_codes=tuple(dict.fromkeys(codes))[:MAX_ERRORS],
        validation_errors=tuple(_bounded(item, MAX_STREAM_BYTES) for item in errors[:MAX_ERRORS]),
        failed_checks=tuple(_bounded_mapping(item) for item in failed_checks[:MAX_ERRORS]),
        review_blockers=tuple(_bounded_mapping(item) for item in review_blockers[:MAX_ERRORS]),
        prior_output_excerpt=_bounded(output, MAX_PRIOR_OUTPUT_BYTES),
        prior_output_digest=_digest(output),
        workspace_diff_digest=workspace_diff_digest,
        history=tuple(dict(item) for item in history[-MAX_ERRORS:]),
        instruction=instruction or _instruction(failure_kind),
    )


def claim_recovery_action(
    store: ControlStore,
    run_id: str,
    *,
    stage_id: str,
    action: str,
    provider: str,
    feedback: AttemptFeedback,
) -> str | None:
    if recovery_actions_used(store, run_id) >= MAX_RECOVERY_ACTIONS:
        store.append_event(run_id, "recovery.exhausted", {
            "stage_id": stage_id,
            "action": action,
            "max_actions": MAX_RECOVERY_ACTIONS,
        }, stage_id=stage_id)
        return None
    attempt_id = f"recovery_{uuid4().hex}"
    store.append_event(run_id, "recovery.feedback", {
        "attempt_id": attempt_id,
        "stage_id": stage_id,
        "feedback": feedback.to_dict(),
    }, stage_id=stage_id)
    store.append_event(run_id, "recovery.attempt", {
        "attempt_id": attempt_id,
        "action": action,
        "stage_id": stage_id,
        "provider": provider,
        "status": "started",
        "failure_kind": feedback.failure_kind,
        "feedback_summary": [*feedback.error_codes, *feedback.validation_errors[:3]],
    }, stage_id=stage_id)
    return attempt_id


def finish_recovery_action(
    store: ControlStore,
    run_id: str,
    *,
    attempt_id: str,
    stage_id: str,
    action: str,
    provider: str,
    status: str,
    failure_kind: str,
    message: str,
) -> None:
    store.append_event(run_id, "recovery.attempt", {
        "attempt_id": attempt_id,
        "action": action,
        "stage_id": stage_id,
        "provider": provider,
        "status": status,
        "failure_kind": failure_kind,
        "feedback_summary": [_bounded(message, MAX_STREAM_BYTES)],
    }, stage_id=stage_id)


def recovery_actions_used(store: ControlStore, run_id: str) -> int:
    return sum(
        event["type"] == "recovery.attempt"
        and isinstance(event.get("payload"), dict)
        and event["payload"].get("status") == "started"
        for event in store.events(run_id)
    )


def build_recovery_summary(
    run_id: str,
    profile: str,
    records: Sequence[AnyEvidenceRecord],
    decision: GateDecision,
    events: Sequence[Mapping[str, object]],
    *,
    fallback_provider: str | None = None,
) -> RecoverySummary:
    attempts = _attempts(events)
    used = sum(item.status == "started" for item in attempts)
    terminal_attempts = [item for item in attempts if item.status != "started"]
    diagnosis = _diagnose(records, decision)
    if decision.status == "PASS":
        status = "recovered" if used else "not_needed"
    elif used >= MAX_RECOVERY_ACTIONS:
        status = "exhausted"
    else:
        status = "needs_action"
    return RecoverySummary(
        status=status,
        max_actions=MAX_RECOVERY_ACTIONS,
        actions_used=used,
        primary_failure=diagnosis,
        attempts=terminal_attempts or attempts,
        next_actions=(
            [] if decision.status == "PASS"
            else [_exhausted_action(run_id)] if used >= MAX_RECOVERY_ACTIONS
            else _next_actions(run_id, profile, diagnosis, fallback_provider=fallback_provider)
        ),
        workspace_safe=True,
        workspace_message=(
            "Recovery stayed inside the isolated worktree; verified changes were applied after Gate PASS."
            if decision.status == "PASS"
            else "The user workspace was not changed because the run did not pass the Gate."
        ),
    )


def _attempts(events: Sequence[Mapping[str, object]]) -> list[RecoveryAttempt]:
    attempts: list[RecoveryAttempt] = []
    for event in events:
        if event.get("type") != "recovery.attempt":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        action = str(payload.get("action") or "repair")
        if action not in {"fix-output", "retry", "switch-provider", "repair"}:
            action = "repair"
        status = str(payload.get("status") or "failed")
        if status not in {"started", "succeeded", "failed", "skipped"}:
            status = "failed"
        attempts.append(RecoveryAttempt(
            action=action,
            stage_id=str(payload.get("stage_id")) if payload.get("stage_id") else None,
            provider=str(payload.get("provider")) if payload.get("provider") else None,
            status=status,
            failure_kind=str(payload.get("failure_kind")) if payload.get("failure_kind") else None,
            feedback_summary=[str(item) for item in payload.get("feedback_summary", [])][:MAX_ERRORS],
        ))
    return attempts


def _diagnose(records: Sequence[AnyEvidenceRecord], decision: GateDecision) -> FailureDiagnosis | None:
    blockers = list(decision.blockers)
    independent = next((item for item in blockers if item.requirement_id == "independent_review"), None)
    if independent:
        return FailureDiagnosis(
            code="independent_reviewer_unavailable",
            kind="independent_reviewer",
            summary="No independent reviewer is configured for this delivery profile.",
            details=[independent.reason],
            consequences=_consequences(blockers, "independent_review"),
        )
    failed_runtime = [item for item in records if isinstance(item, RuntimeEvidence) and item.status == "failed"]
    if failed_runtime:
        record = failed_runtime[-1]
        message = str(record.details.get("message") or "A runtime or policy failure remains unresolved.")
        code = str(record.details.get("code") or _infer_code(message))
        kind = str(record.details.get("kind") or _kind_for_code(code))
        allowed = {
            "output_contract", "test_failure", "review_blocker", "provider_timeout",
            "provider_failure", "environment", "independent_reviewer", "policy", "unknown",
        }
        return FailureDiagnosis(
            code=code,
            kind=kind if kind in allowed else "unknown",
            stage_id=record.stage_id,
            summary=message,
            details=[str(item) for item in record.details.get("errors", [])][:MAX_ERRORS],
            consequences=_consequences(blockers, record.requirement_id),
        )
    failed_check = next((item for item in blockers if item.requirement_id == "deterministic_check"), None)
    if failed_check:
        return FailureDiagnosis(
            code="deterministic_check_failed", kind="test_failure",
            summary=failed_check.reason, details=[failed_check.remediation],
            consequences=_consequences(blockers, failed_check.requirement_id),
        )
    review = next((item for item in blockers if "review" in item.requirement_id), None)
    if review:
        return FailureDiagnosis(
            code="review_blocker", kind="review_blocker", summary=review.reason,
            details=[review.remediation], consequences=_consequences(blockers, review.requirement_id),
        )
    if blockers:
        blocker = blockers[0]
        return FailureDiagnosis(
            code=blocker.code, kind="policy", summary=blocker.reason,
            details=[blocker.remediation], consequences=_consequences(blockers, blocker.requirement_id),
        )
    return None


def _next_actions(
    run_id: str,
    profile: str,
    diagnosis: FailureDiagnosis | None,
    *,
    fallback_provider: str | None,
) -> list[RecoveryAction]:
    command = f"muxdev resume {run_id} --action auto"
    if diagnosis and diagnosis.kind == "independent_reviewer":
        actions = [RecoveryAction(
            action="configure-reviewer",
            label="配置独立 Reviewer",
            description="配置与执行者不同且已认证的 Reviewer Provider，然后重新运行。",
            command="muxdev provider doctor",
        )]
        if profile in {"standard", "strict"}:
            actions.append(RecoveryAction(
                action="rerun-lite", label="确认以 Lite 重跑",
                description="Lite 会降低独立评审要求，只应在你接受该风险时使用。",
                command=f"muxdev run --profile lite --provider <provider> '<task from {run_id}>'",
                requires_confirmation=True,
            ))
        return actions
    if diagnosis and diagnosis.kind == "output_contract":
        return [RecoveryAction(
            action="fix-output",
            label="修复结构化输出",
            description="仅修复上轮输出契约，不重新执行已完成的写操作。",
            command=f"muxdev resume {run_id} --action fix-output",
        )]
    elif diagnosis and diagnosis.kind in {"provider_timeout", "provider_failure"}:
        permanent = diagnosis.code in {
            "provider_auth_failed", "provider_permission_denied",
            "provider_command_invalid", "provider_configuration_invalid",
            "provider_process_failed",
        }
        actions = [] if permanent else [RecoveryAction(
            action="retry", label="重试当前 Provider",
            description="携带上轮错误重试当前阶段。",
            command=f"muxdev resume {run_id} --action retry",
        )]
        if fallback_provider:
            actions.append(RecoveryAction(
                action="switch-provider", label=f"切换到 {fallback_provider}",
                description="使用 Run 创建时冻结的合格备用 Provider。",
                command=f"muxdev resume {run_id} --action switch-provider",
            ))
        if not actions:
            actions.append(RecoveryAction(
                action="inspect", label="检查 Provider 配置",
                description="认证、权限或启动配置错误不会盲目重试；处理后请创建新的执行。",
                command="muxdev provider doctor",
            ))
        return actions
    return [RecoveryAction(
        action="auto", label="自动恢复", description="在剩余恢复额度内根据首要原因选择安全动作。", command=command,
    )]


def _exhausted_action(run_id: str) -> RecoveryAction:
    return RecoveryAction(
        action="inspect",
        label="复制诊断命令",
        description="两次安全恢复均未解决问题；查看完整错误后人工处理，再创建新的 Run。",
        command=f"muxdev evidence show {run_id}",
    )


def _consequences(blockers: Iterable[object], primary_requirement: str) -> list[str]:
    return [
        str(getattr(item, "requirement_id", "unknown"))
        for item in blockers
        if str(getattr(item, "requirement_id", "")) != primary_requirement
    ]


def _instruction(kind: str) -> str:
    if kind == "output_contract":
        return "Do not edit files or use tools. Return only a corrected JSON object matching the declared schema."
    if kind == "test_failure":
        return "Fix the implementation so every runtime-owned failed check passes; then return the declared JSON contract."
    if kind == "review_blocker":
        return "Address each unresolved review blocker and return the declared JSON contract."
    return "Retry the stage using the failure facts below and return the declared JSON contract."


def _infer_code(message: str) -> str:
    lowered = message.lower()
    if "output contract" in lowered or "structured" in lowered or "schema" in lowered:
        return "output_contract_invalid"
    if "timed out" in lowered or "timeout" in lowered or "124" in lowered:
        return "provider_timeout"
    if "reviewer" in lowered and "independent" in lowered:
        return "independent_reviewer_unavailable"
    if "provider" in lowered or "exited" in lowered:
        return "provider_failure"
    return "runtime_failure"


def _kind_for_code(code: str) -> str:
    if "output_contract" in code:
        return "output_contract"
    if "timeout" in code:
        return "provider_timeout"
    if "reviewer" in code:
        return "independent_reviewer"
    if "test" in code or "check" in code:
        return "test_failure"
    if "review" in code:
        return "review_blocker"
    if "provider" in code:
        return "provider_failure"
    return "unknown"


def _bounded_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    bounded: dict[str, object] = {}
    for key, item in value.items():
        bounded[str(key)] = _bounded(item, MAX_STREAM_BYTES) if isinstance(item, str) else item
    return bounded


def _bounded(value: object, limit: int) -> str:
    text = redact(str(value or ""))
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    suffix = "\n[truncated by muxdev recovery feedback]"
    kept = encoded[: max(0, limit - len(suffix.encode()))].decode("utf-8", errors="ignore")
    return kept + suffix


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)


def _contained(root: Path, relative: str) -> Path:
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"recovery path escaped the isolated root: {relative}")
    return target


class RecoveryContextMixin:
    def _feedback_for_stage(
        self,
        run_id: str,
        stage_id: str,
        existing: Mapping[str, object] | None,
        subject: str,
    ) -> AttemptFeedback | None:
        if existing and existing.get("status") == "waiting_user":
            return None
        result = existing.get("result") if existing and isinstance(existing.get("result"), dict) else {}
        contract_errors = [str(item) for item in result.get("contract_errors", [])]
        failed_checks = [
            {
                "id": item.record_id,
                "argv": item.argv,
                "exit_code": item.exit_code,
                "stdout": item.stdout_summary,
                "stderr": item.stderr_summary,
                "summary": item.summary,
            }
            for item in self._records(run_id)
            if isinstance(item, CheckEvidence) and item.subject_digest == subject and item.exit_code != 0
        ]
        review_blockers = [
            finding.model_dump(mode="json")
            for item in self._records(run_id)
            if isinstance(item, ReviewEvidence) and item.subject_digest == subject
            for finding in item.findings
            if finding.severity == "high" and not finding.resolved
        ]
        if not existing and not failed_checks and not review_blockers:
            return None
        prior = StageExecutionResult(
            artifact_name=f"{stage_id}.prior-output",
            content=str(result.get("output_excerpt") or ""),
            summary=str(result.get("summary") or "Previous stage attempt failed."),
            stage_id=stage_id,
            provider=str(existing.get("provider") or "") if existing else "",
            returncode=int(result.get("returncode") or 0),
            timed_out=bool(result.get("timed_out")),
            stderr_content="",
        ) if existing else None
        if contract_errors:
            kind, codes = "output_contract", ("output_contract_invalid",)
            validation = ContractValidation(
                str(result.get("contract_schema") or "unknown"), False, tuple(contract_errors), None
            )
        elif failed_checks:
            kind, codes, validation = "test_failure", ("deterministic_check_failed",), None
        elif review_blockers:
            kind, codes, validation = "review_blocker", ("review_blocker",), None
        elif prior and prior.timed_out:
            kind, codes, validation = "provider_timeout", ("provider_timeout",), None
        else:
            kind, codes, validation = "provider_failure", ("previous_stage_failed",), None
        history = [
            {
                "action": str(event["payload"].get("action") or ""),
                "status": str(event["payload"].get("status") or ""),
                "failure_kind": str(event["payload"].get("failure_kind") or ""),
            }
            for event in self.store.events(run_id)
            if event["type"] == "recovery.attempt" and isinstance(event.get("payload"), dict)
        ]
        return build_attempt_feedback(
            prior,
            prior_attempt=int(existing.get("attempt") or 1) if existing else 1,
            failure_kind=kind,
            workspace_diff_digest=subject,
            validation=validation,
            error_codes=codes,
            failed_checks=failed_checks,
            review_blockers=review_blockers,
            history=history,
        )

    def _requested_recovery_action(self, run_id: str) -> str:
        for event in reversed(self.store.events(run_id)):
            if event["type"] == "recovery.requested" and isinstance(event.get("payload"), dict):
                return str(event["payload"].get("action") or "auto")
        return "auto"

    def _has_recovery_request(self, run_id: str) -> bool:
        return any(event["type"] == "recovery.requested" for event in self.store.events(run_id))

    def _fallback_provider(self, run_id: str, current: str, *, role: str | None = None) -> str | None:
        snapshot = self._policy_snapshot(run_id)
        candidates = snapshot.provider_route.get("candidates")
        values = candidates if isinstance(candidates, list) else []
        eligible = sorted(
            (
                item for item in values
                if isinstance(item, dict)
                and item.get("eligible")
                and item.get("provider") != current
                and not (
                    role in {"review", "secure"}
                    and item.get("provider") == snapshot.provider_route.get("main_provider")
                )
                and str(item.get("provider")) in snapshot.provider_definitions
            ),
            key=lambda item: (float(item.get("score") or 0), str(item.get("provider"))),
            reverse=True,
        )
        return str(eligible[0]["provider"]) if eligible else None
