"""Strict validation for provider-produced stage contracts.

Provider exit code zero only proves that the CLI process finished.  It does not
prove that a test or review stage returned the contract muxdev asked for.  This
module keeps that distinction explicit and fail-closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from ..models import ReviewBlocker, ReviewResult, TestResult


@dataclass(frozen=True)
class ContractValidation:
    schema: str
    valid: bool
    errors: tuple[str, ...]
    parsed: dict[str, Any] | None


def validate_test_result(parsed: dict[str, Any] | None, *, fallback_summary: str) -> tuple[TestResult, ContractValidation]:
    errors: list[str] = []
    if not parsed:
        errors.append("missing TestResult JSON object")
    else:
        if not isinstance(parsed.get("passed"), bool):
            errors.append("TestResult.passed must be a boolean")
        if not isinstance(parsed.get("command"), str) or not str(parsed.get("command") or "").strip():
            errors.append("TestResult.command must be a non-empty string")
        if not isinstance(parsed.get("exit_code"), int) or isinstance(parsed.get("exit_code"), bool):
            errors.append("TestResult.exit_code must be an integer")
        if not isinstance(parsed.get("summary"), str) or not str(parsed.get("summary") or "").strip():
            errors.append("TestResult.summary must be a non-empty string")
        if isinstance(parsed.get("passed"), bool) and isinstance(parsed.get("exit_code"), int):
            if bool(parsed["passed"]) != (int(parsed["exit_code"]) == 0):
                errors.append("TestResult.passed must agree with exit_code")
    if not errors:
        try:
            result = TestResult.model_validate(parsed)
        except ValidationError as exc:
            errors.append(str(exc))
        else:
            return result, ContractValidation("TestResult", True, (), parsed)

    summary = "; ".join(errors) or "invalid TestResult"
    return (
        TestResult(passed=False, command="unreported", exit_code=-1, summary=f"{summary}. Provider summary: {fallback_summary}"),
        ContractValidation("TestResult", False, tuple(errors), parsed),
    )


def validate_review_result(parsed: dict[str, Any] | None) -> tuple[ReviewResult, ContractValidation]:
    errors: list[str] = []
    if not parsed:
        errors.append("missing ReviewResult JSON object")
    else:
        if not isinstance(parsed.get("has_blockers"), bool):
            errors.append("ReviewResult.has_blockers must be a boolean")
        if not isinstance(parsed.get("blockers"), list):
            errors.append("ReviewResult.blockers must be a list")
    if not errors:
        try:
            result = ReviewResult.model_validate(parsed)
        except ValidationError as exc:
            errors.append(str(exc))
        else:
            if result.has_blockers != bool(result.blockers):
                errors.append("ReviewResult.has_blockers must agree with blockers")
            else:
                return result, ContractValidation("ReviewResult", True, (), parsed)

    blocker = ReviewBlocker(
        type="invalid_review_output",
        severity="high",
        suggestion="Reviewer must return a valid ReviewResult JSON contract; unstructured prose cannot clear the gate.",
    )
    return (
        ReviewResult(has_blockers=True, blockers=[blocker]),
        ContractValidation("ReviewResult", False, tuple(errors), parsed),
    )
