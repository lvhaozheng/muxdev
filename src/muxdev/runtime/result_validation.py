"""Fail-closed validation for provider-produced stage contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from ..models import (
    ChangeResult,
    PlanResult,
    ReviewFinding,
    ReviewResult,
    TestResult,
    VerificationSuggestion,
)


@dataclass(frozen=True)
class ContractValidation:
    schema: str
    valid: bool
    errors: tuple[str, ...]
    parsed: dict[str, Any] | None


FORBIDDEN_DECISION_FIELDS = {"delivery_decision", "confidence", "evidence", "missing_evidence"}


def validate_stage_output(schema: str | None, content: str) -> tuple[dict[str, Any], ContractValidation]:
    """Validate every model-produced stage contract without discarding diagnostics."""
    payload = extract_json(content)
    forbidden = sorted(FORBIDDEN_DECISION_FIELDS & set(payload or {}))
    if forbidden:
        errors = tuple(f"provider-controlled decision field is forbidden: {field}" for field in forbidden)
        return {}, ContractValidation(schema or "JSON object", False, errors, payload)
    if schema == "TestResult":
        result, validation = validate_test_result(
            payload, fallback_summary="Runtime did not receive a valid TestResult."
        )
        return result.model_dump(mode="json"), validation
    if schema == "ReviewResult":
        result, validation = validate_review_result(payload)
        return result.model_dump(mode="json"), validation
    if schema in {"PlanResult", "ChangeResult"}:
        model = PlanResult if schema == "PlanResult" else ChangeResult
        if payload is None:
            return {}, ContractValidation(schema, False, ("missing JSON object",), None)
        try:
            result = model.model_validate(payload)
        except ValidationError as exc:
            errors = tuple(_pydantic_errors(exc))
            return {}, ContractValidation(schema, False, errors, payload)
        return result.model_dump(mode="json"), ContractValidation(schema, True, (), payload)
    if payload is None and schema:
        return {}, ContractValidation(schema, False, ("missing JSON object",), None)
    return payload or {}, ContractValidation(schema or "JSON object", True, (), payload)


def extract_json(content: str) -> dict[str, Any] | None:
    import json

    decoder = json.JSONDecoder()
    for index, char in enumerate(content):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def validate_test_result(parsed: dict[str, Any] | None, *, fallback_summary: str) -> tuple[TestResult, ContractValidation]:
    errors = _base_errors(parsed, required={"checks"}, forbidden=FORBIDDEN_DECISION_FIELDS | {"passed", "command"})
    if parsed and not isinstance(parsed.get("checks"), list):
        errors.append("TestResult.checks must be a list")
    if not errors:
        try:
            result = TestResult.model_validate(parsed)
        except ValidationError as exc:
            errors.append(str(exc))
        else:
            return result, ContractValidation("TestResult", True, (), parsed)
    summary = "; ".join(errors) or "invalid TestResult"
    fallback = TestResult(
        checks=[
            VerificationSuggestion(
                id="invalid_test_output", summary=f"{summary}. Provider summary: {fallback_summary}"
            )
        ]
    )
    return fallback, ContractValidation("TestResult", False, tuple(errors), parsed)


def validate_review_result(parsed: dict[str, Any] | None) -> tuple[ReviewResult, ContractValidation]:
    forbidden = FORBIDDEN_DECISION_FIELDS | {"has_blockers", "blockers"}
    errors = _base_errors(parsed, required={"target_subject", "findings"}, forbidden=forbidden)
    if parsed and not isinstance(parsed.get("findings"), list):
        errors.append("ReviewResult.findings must be a list")
    if not errors:
        try:
            result = ReviewResult.model_validate(parsed)
        except ValidationError as exc:
            errors.append(str(exc))
        else:
            return result, ContractValidation("ReviewResult", True, (), parsed)
    finding = ReviewFinding(
        type="invalid_review_output", severity="high", message="Reviewer returned an invalid structured contract.",
        remediation="Return target_subject, findings, and residual_risk without a delivery decision.",
    )
    return ReviewResult(target_subject="invalid", findings=[finding]), ContractValidation("ReviewResult", False, tuple(errors), parsed)


def _base_errors(parsed: dict[str, Any] | None, *, required: set[str], forbidden: set[str]) -> list[str]:
    if not parsed:
        return ["missing JSON object"]
    errors = [f"missing required field: {field}" for field in sorted(required - set(parsed))]
    errors.extend(f"provider-controlled decision field is forbidden: {field}" for field in sorted(forbidden & set(parsed)))
    return errors


def _pydantic_errors(exc: ValidationError) -> list[str]:
    errors: list[str] = []
    for item in exc.errors(include_url=False):
        location = ".".join(str(part) for part in item.get("loc", ())) or "root"
        errors.append(f"{location}: {item.get('msg', 'invalid value')}")
    return errors or [str(exc)]
