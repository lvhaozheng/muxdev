"""Structured, safety-bounded questions raised by stage workers."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Mapping

from ..core.redaction import redact
from ..domain import StageExecutionResult

if TYPE_CHECKING:
    from .engine import PreparedStage


DEFAULT_TIMEOUT_SECONDS = 60
MAX_QUESTION_ROUNDS = 2
_HIGH_RISK_MARKERS = (
    "permission", "credential", "secret", "token", "delete", "overwrite",
    "accept delivery", "weaken", "disable gate", "network access", "sandbox",
    "权限", "凭据", "密钥", "令牌", "删除", "覆盖", "接受交付", "降低门禁", "关闭门禁", "网络访问",
)


class InteractionPendingError(RuntimeError):
    """Signal a deliberate pause, not a provider or runtime failure."""


def normalize_interaction_request(value: Mapping[str, object]) -> dict[str, Any]:
    """Return a bounded question and prevent unsafe timeout defaults."""
    question = redact(str(value.get("question") or value.get("prompt") or "").strip())[:2000]
    if not question:
        raise ValueError("interaction request requires a question")
    options = _normalize_options(value.get("options"))
    joined = " ".join([question, *(str(item["label"]) for item in options)]).casefold()
    requested_risk = str(value.get("risk") or "low").casefold()
    high_risk = requested_risk in {"high", "critical"} or any(
        marker in joined for marker in _HIGH_RISK_MARKERS
    )
    blocking = bool(value.get("blocking")) or high_risk
    recommended = str(value.get("recommended_option_id") or "").strip()
    if not recommended:
        recommended = next(
            (str(item["id"]) for item in options if item.get("recommended")),
            str(options[0]["id"]),
        )
    if recommended not in {str(item["id"]) for item in options}:
        raise ValueError("recommended_option_id must reference an option")
    # The timeout is runtime policy, not an untrusted Provider suggestion.
    timeout = 0 if blocking else DEFAULT_TIMEOUT_SECONDS
    now = datetime.now(UTC)
    expires_at = (now + timedelta(seconds=timeout)).isoformat() if timeout else None
    return {
        "question": question,
        "options": options,
        "allow_custom_input": bool(value.get("allow_custom_input", True)),
        "blocking": blocking,
        "risk": "high" if high_risk else "low",
        "timeout_seconds": timeout,
        "timeout_action": "wait" if blocking else "select_recommended",
        "recommended_option_id": recommended,
        "expires_at": expires_at,
        "reason": redact(str(value.get("reason") or "需要补充信息后继续当前阶段。"))[:1000],
    }


def response_payload(interaction: Mapping[str, Any]) -> dict[str, Any]:
    value = interaction.get("response")
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = {"custom_input": redact(value)[:2000]}
    else:
        parsed = value if isinstance(value, dict) else {}
    return {
        "interaction_id": interaction.get("interaction_id"),
        "question": interaction.get("question") or interaction.get("prompt"),
        "status": interaction.get("status"),
        **parsed,
    }


class InteractiveStageMixin:
    """Pause or briefly wait for structured worker questions, then rerun the stage."""

    def _handle_stage_interactions(
        self, prepared: PreparedStage, output: StageExecutionResult
    ) -> tuple[PreparedStage, StageExecutionResult]:
        current = prepared
        current_output = output
        for _round in range(MAX_QUESTION_ROUNDS):
            if not current_output.interaction_requests:
                return current, current_output
            questions = [normalize_interaction_request(item) for item in current_output.interaction_requests]
            if current.checkpoint:
                current.checkpoint.restore()
            interaction_ids = [
                self.store.request_interaction(
                    current.run_id,
                    kind="clarification",
                    requirement_id="requirement_clarification",
                    prompt=str(question["question"]),
                    stage_id=current.stage.id,
                    details=question,
                )
                for question in questions
            ]
            self.store.append_event(
                current.run_id,
                "worker.waiting",
                {
                    "worker_id": self._worker_id(
                        current.run_id, current.stage.id, current.stage_input.attempt
                    ),
                    "stage_id": current.stage.id,
                    "role": current.stage.role,
                    "provider": current.provider,
                    "attempt": current.stage_input.attempt,
                    "status": "waiting_user",
                    "interaction_ids": interaction_ids,
                },
                stage_id=current.stage.id,
            )
            if any(question["blocking"] for question in questions):
                self.store.upsert_stage(
                    current.run_id,
                    current.stage.id,
                    role=current.stage.role,
                    provider=current.provider,
                    status="waiting_user",
                    attempt=current.stage_input.attempt,
                    result={"summary": "等待人工确认后继续当前阶段。"},
                )
                self.store.update_run(
                    current.run_id, status="awaiting_approval", current_stage=current.stage.id
                )
                raise InteractionPendingError("stage is waiting for an explicit user response")
            responses = self._wait_for_interactions(current.run_id, interaction_ids)
            current = self._interaction_rerun(current, responses)
            next_output = current.adapter.execute(current.stage_input)
            current_output = replace(
                next_output,
                cost_usd=current_output.cost_usd + next_output.cost_usd,
                tokens=current_output.tokens + next_output.tokens,
            )
            if current_output.returncode != 0:
                return current, current_output
        raise RuntimeError("worker requested too many consecutive clarification rounds")

    def _wait_for_interactions(
        self, run_id: str, interaction_ids: list[str]
    ) -> list[dict[str, Any]]:
        pending = set(interaction_ids)
        while pending:
            rows = {
                str(item["interaction_id"]): item
                for item in self.store.interactions(run_id)
                if str(item.get("interaction_id") or "") in pending
            }
            for interaction_id in list(pending):
                item = rows.get(interaction_id)
                if item and item.get("status") != "pending":
                    pending.remove(interaction_id)
                    continue
                expires_at = str((item or {}).get("expires_at") or "")
                if expires_at and datetime.now(UTC) >= datetime.fromisoformat(expires_at):
                    recommended = str((item or {}).get("recommended_option_id") or "")
                    try:
                        self.store.respond(
                            interaction_id,
                            status="responded",
                            response=json.dumps(
                                {"option_id": recommended, "defaulted": True},
                                ensure_ascii=False,
                            ),
                            details={"selected_option_id": recommended, "defaulted": True},
                        )
                    except RuntimeError:
                        pass  # A concurrent user response won the deadline race.
                    pending.remove(interaction_id)
            if pending:
                time.sleep(0.25)
        return [
            response_payload(item)
            for item in self.store.interactions(run_id)
            if str(item.get("interaction_id") or "") in interaction_ids
        ]

    def _interaction_rerun(
        self, prepared: PreparedStage, responses: list[dict[str, Any]]
    ) -> PreparedStage:
        stage_input = replace(
            prepared.stage_input,
            context={**prepared.stage_input.context, "interaction_responses": responses},
            feedback=None,
            attempt=prepared.stage_input.attempt + 1,
        )
        self.store.upsert_stage(
            prepared.run_id,
            prepared.stage.id,
            role=prepared.stage.role,
            provider=prepared.provider,
            status="running",
            attempt=stage_input.attempt,
        )
        self.store.append_event(
            prepared.run_id,
            "worker.started",
            {
                "worker_id": self._worker_id(
                    prepared.run_id, prepared.stage.id, stage_input.attempt
                ),
                "stage_id": prepared.stage.id,
                "role": prepared.stage.role,
                "provider": prepared.provider,
                "attempt": stage_input.attempt,
                "read_only": bool(prepared.stage.read_only),
                "status": "running",
                "reason": "interaction_resolved",
            },
            stage_id=prepared.stage.id,
        )
        return replace(prepared, stage_input=stage_input)


def _normalize_options(value: object) -> list[dict[str, Any]]:
    raw = value if isinstance(value, list) else []
    options: list[dict[str, Any]] = []
    for index, item in enumerate(raw[:4], start=1):
        source = item if isinstance(item, Mapping) else {"label": str(item)}
        label = redact(str(source.get("label") or "").strip())[:200]
        if not label:
            continue
        identifier = str(source.get("id") or f"option_{index}").strip()[:80]
        options.append({
            "id": identifier,
            "label": label,
            "description": redact(str(source.get("description") or ""))[:500],
            "recommended": bool(source.get("recommended")),
        })
    if len(options) < 2:
        options = [
            {"id": "recommended", "label": "采用推荐方案", "description": "按当前上下文的安全默认继续。", "recommended": True},
            {"id": "provide_details", "label": "我来补充信息", "description": "等待我输入更具体的要求。", "recommended": False},
        ]
    if not any(item["recommended"] for item in options):
        options[0]["recommended"] = True
    return options


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "InteractionPendingError",
    "InteractiveStageMixin",
    "normalize_interaction_request",
    "response_payload",
]
