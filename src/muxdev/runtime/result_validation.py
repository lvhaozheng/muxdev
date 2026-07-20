"""Fail-closed validation for provider-produced stage contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from ..models import ReviewFinding, ReviewResult, TestCheck, TestResult


@dataclass(frozen=True)
class ContractValidation:
    schema: str
    valid: bool
    errors: tuple[str, ...]
    parsed: dict[str, Any] | None


FORBIDDEN_DECISION_FIELDS = {"delivery_decision", "confidence", "evidence", "missing_evidence"}


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
            TestCheck(
                id="invalid_test_output", status="failed", exit_code=-1,
                summary=f"{summary}. Provider summary: {fallback_summary}",
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
