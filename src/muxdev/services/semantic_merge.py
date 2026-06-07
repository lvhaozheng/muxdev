"""Semantic merge review for final patch gates."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..models import utc_now
from ..storage.contracts import canonical_hash, sha256_text, write_json_artifact


SENSITIVE_TERMS = ("auth", "login", "oauth", "permission", "payment", "billing", "secret", "token")


def review_semantic_merge(
    run_dir: Path,
    *,
    run_id: str,
    patch_text: str,
    task: str = "",
) -> tuple[Path, str, dict[str, Any]]:
    """Write a deterministic semantic merge review for the final diff."""
    patch_hash = sha256_text(patch_text)
    findings = _semantic_findings(patch_text, task=task)
    decision = "reject" if any(str(item.get("severity")) == "high" for item in findings) else "accept"
    payload: dict[str, Any] = {
        "contract_version": "muxdev.semantic_merge_review.v1",
        "run_id": run_id,
        "reviewer": "local-semantic-merge-reviewer",
        "decision": decision,
        "patch_hash": patch_hash,
        "findings": findings,
        "minimal_context": ["task", "patch_text"],
    }
    payload["review_hash"] = canonical_hash(payload)
    payload["created_at"] = utc_now()
    path, digest = write_json_artifact(run_dir / "validation" / "semantic_merge_review.json", payload)
    return path, digest, payload


def _semantic_findings(patch_text: str, *, task: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if _has_conflict_markers(patch_text):
        findings.append(
            {
                "severity": "high",
                "type": "merge_conflict_marker",
                "summary": "patch contains unresolved conflict markers",
            }
        )
    if _removes_tests_without_replacement(patch_text):
        findings.append(
            {
                "severity": "medium",
                "type": "test_removal_without_replacement",
                "summary": "patch removes test functions without adding replacement tests",
            }
        )
    lowered = f"{task}\n{patch_text}".lower()
    if any(term in lowered for term in SENSITIVE_TERMS) and not _touches_tests(patch_text):
        findings.append(
            {
                "severity": "medium",
                "type": "sensitive_change_without_tests",
                "summary": "sensitive auth/payment/security text appears without test file changes",
            }
        )
    return findings


def _has_conflict_markers(text: str) -> bool:
    return any(marker in text for marker in ("<<<<<<<", "=======", ">>>>>>>"))


def _touches_tests(text: str) -> bool:
    return bool(re.search(r"^\+\+\+ b/(tests?/|.*test.*\.(py|js|ts|tsx|jsx)$)", text, flags=re.MULTILINE | re.IGNORECASE))


def _removes_tests_without_replacement(text: str) -> bool:
    removed = bool(re.search(r"^-.*\bdef test_|^-.*\bit\(|^-.*\btest\(", text, flags=re.MULTILINE))
    added = bool(re.search(r"^\+.*\bdef test_|^\+.*\bit\(|^\+.*\btest\(", text, flags=re.MULTILINE))
    return removed and not added
