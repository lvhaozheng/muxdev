"""Skill-driven delivery gate checks.

The gate intentionally evaluates only deterministic, local signals. Skill
Delivery Standard text remains the rule source, while this module enforces the
small subset that can be checked quickly without an LLM or human review.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..models import ProviderActionStatus, StageStatus, utc_now
from ..storage import Blackboard, canonical_hash
from ..storage.contracts import sha256_text, write_json_artifact
from .design import design_document_quality_issues


COMMON_CHECKS = (
    "artifact_nonempty",
    "no_failed_tests",
    "no_open_blockers",
    "no_runtime_errors",
    "no_pending_provider_actions",
)

DELIVERY_KEYS = {
    "required deliverable": "required_deliverable",
    "pass when": "pass_when",
    "block when": "block_when",
    "evidence": "evidence",
}


def delivery_rule_for_skill_payload(skill: dict[str, object]) -> dict[str, object] | None:
    """Return the frozen delivery-rule payload for one active skill."""
    content = str(skill.get("content") or "")
    section = extract_delivery_standard(content)
    if not section.get("delivery_standard_text"):
        return None
    policy = _delivery_gate_policy(skill.get("delivery_gate"))
    checks = _dedupe([*policy.get("checks", []), *_derive_checks(section, skill)])
    evidence = _dedupe([*policy.get("required_evidence", []), *_derive_required_evidence(section)])
    payload: dict[str, object] = {
        "skill_name": str(skill.get("name") or ""),
        "skill_stage": str(skill.get("stage") or "") or None,
        "skill_role": str(skill.get("role") or "") or None,
        "source_hash": sha256_text(content),
        "delivery_standard_text": section["delivery_standard_text"],
        "required_deliverable": section.get("required_deliverable", ""),
        "pass_when": section.get("pass_when", ""),
        "block_when": section.get("block_when", ""),
        "evidence": section.get("evidence", ""),
        "derived_checks": checks or list(COMMON_CHECKS),
        "required_evidence": evidence,
    }
    payload["delivery_rule_hash"] = canonical_hash(payload)
    return payload


def extract_delivery_standard(content: str) -> dict[str, str]:
    """Extract the ## Delivery Standard section and common labeled bullets."""
    lines = content.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if re.match(r"^##\s+Delivery Standard\s*$", line.strip(), flags=re.IGNORECASE):
            start = index + 1
            break
    if start is None:
        return {"delivery_standard_text": ""}

    section_lines: list[str] = []
    for line in lines[start:]:
        if re.match(r"^##\s+\S", line.strip()):
            break
        section_lines.append(line)
    text = "\n".join(section_lines).strip()
    parsed = {"delivery_standard_text": text}
    for raw_line in section_lines:
        stripped = raw_line.strip().lstrip("-").strip()
        if ":" not in stripped:
            continue
        label, value = stripped.split(":", 1)
        key = DELIVERY_KEYS.get(label.strip().lower())
        if key:
            parsed[key] = value.strip()
    return parsed


def evaluate_delivery_gate(
    blackboard: Blackboard,
    *,
    run_dir: Path,
    run_id: str,
    stage_id: str,
    target_stage_ids: list[str],
    skills: list[dict[str, object]],
) -> tuple[Path, str, dict[str, object]]:
    """Evaluate a delivery gate and write its JSON artifact."""
    target_stage_ids = _dedupe(target_stage_ids)
    rules = _rules_for_skills(skills)
    checks = _dedupe(check for rule in rules for check in _string_list(rule.get("derived_checks"))) or list(COMMON_CHECKS)
    blockers: list[dict[str, object]] = []
    warnings: list[dict[str, object]] = []

    if not rules:
        warnings.append(
            {
                "type": "delivery_rules_missing",
                "severity": "low",
                "summary": "No active skill Delivery Standard was available; common checks were used.",
            }
        )
        checks = list(COMMON_CHECKS)

    artifact_checks = _check_stage_artifacts(blackboard, run_id, target_stage_ids)
    if "artifact_nonempty" in checks:
        blockers.extend(artifact_checks["blockers"])

    if "no_failed_tests" in checks:
        blockers.extend(_failed_test_blockers(blackboard, run_id, target_stage_ids))
        blockers.extend(_coverage_blockers(blackboard, run_id, target_stage_ids))

    if "no_open_blockers" in checks:
        blockers.extend(_open_review_blockers(blackboard, run_id, target_stage_ids))

    if "no_runtime_errors" in checks:
        blockers.extend(_runtime_error_blockers(blackboard, run_id, target_stage_ids))

    if "no_pending_provider_actions" in checks:
        blockers.extend(_pending_provider_action_blockers(blackboard, run_id, target_stage_ids))

    if "no_secret_terms" in checks:
        blockers.extend(_secret_term_blockers(blackboard, run_id, target_stage_ids))

    if "design_document_complete" in checks:
        blockers.extend(_design_document_complete_blockers(blackboard, run_id, target_stage_ids))

    payload = {
        "schema": "muxdev.delivery_gate.v1",
        "stage_id": stage_id,
        "target_stage_ids": target_stage_ids,
        "checked_at": utc_now(),
        "has_blockers": bool(blockers),
        "decision": "reject" if blockers else "accept",
        "checks": checks,
        "skills": rules,
        "artifact_checks": artifact_checks,
        "blockers": blockers,
        "warnings": warnings,
    }
    payload["delivery_gate_hash"] = canonical_hash(payload)
    path, digest = write_json_artifact(run_dir / "delivery_gates" / f"{stage_id}.json", payload)
    return path, digest, payload


def _rules_for_skills(skills: list[dict[str, object]]) -> list[dict[str, object]]:
    rules: list[dict[str, object]] = []
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        frozen = skill.get("delivery_rules")
        if isinstance(frozen, dict) and frozen.get("delivery_standard_text"):
            rules.append(dict(frozen))
            continue
        rule = delivery_rule_for_skill_payload(skill)
        if rule:
            rules.append(rule)
    return rules


def _check_stage_artifacts(blackboard: Blackboard, run_id: str, target_stage_ids: list[str]) -> dict[str, object]:
    rows = [row for row in blackboard.table_rows("artifacts", run_id=run_id) if row.get("kind") == "stage_output"]
    artifacts: list[dict[str, object]] = []
    blockers: list[dict[str, object]] = []
    by_stage: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        stage_id = str(row.get("stage_id") or "")
        by_stage.setdefault(stage_id, []).append(row)
    for target in target_stage_ids:
        target_rows = by_stage.get(target, [])
        valid = []
        for row in target_rows:
            path = Path(str(row.get("path") or ""))
            if path.exists() and path.is_file() and path.stat().st_size > 0:
                valid.append({"stage_id": target, "path": str(path), "bytes": path.stat().st_size})
        artifacts.extend(valid)
        if not valid:
            blockers.append(
                _blocker(
                    "missing_stage_artifact",
                    "high",
                    f"Stage {target} has no non-empty stage output artifact.",
                    file=None,
                    line=None,
                )
            )
    return {"artifacts": artifacts, "blockers": blockers}


def _failed_test_blockers(blackboard: Blackboard, run_id: str, target_stage_ids: list[str]) -> list[dict[str, object]]:
    blockers: list[dict[str, object]] = []
    for row in blackboard.table_rows("test_results", run_id=run_id):
        if str(row.get("stage_id") or "") not in target_stage_ids:
            continue
        if bool(row.get("passed")):
            continue
        blockers.append(_blocker("test_failure", "high", str(row.get("summary") or "A target test stage failed.")))
    return blockers


def _coverage_blockers(blackboard: Blackboard, run_id: str, target_stage_ids: list[str]) -> list[dict[str, object]]:
    blockers: list[dict[str, object]] = []
    for row in blackboard.table_rows("test_results", run_id=run_id):
        if str(row.get("stage_id") or "") not in target_stage_ids:
            continue
        summary = str(row.get("summary") or "").lower()
        if "coverage" not in summary:
            continue
        if any(token in summary for token in ("below", "failed", "fail", "under threshold", "did not meet")):
            blockers.append(_blocker("coverage_threshold", "high", str(row.get("summary") or "Coverage threshold was not met.")))
    return blockers


def _open_review_blockers(blackboard: Blackboard, run_id: str, target_stage_ids: list[str]) -> list[dict[str, object]]:
    blockers: list[dict[str, object]] = []
    for row in blackboard.table_rows("review_blockers", run_id=run_id):
        source_stage = str(row.get("stage_id") or "review")
        if source_stage not in target_stage_ids:
            continue
        if _latest_role_decision(blackboard, run_id, source_stage) == "accept":
            continue
        blockers.append(
            _blocker(
                str(row.get("type") or "review_blocker"),
                _severity(str(row.get("severity") or "medium")),
                str(row.get("suggestion") or "Review blocker remains open."),
                file=str(row.get("file")) if row.get("file") else None,
                line=int(row["line"]) if row.get("line") is not None else None,
            )
        )
    return blockers


def _runtime_error_blockers(blackboard: Blackboard, run_id: str, target_stage_ids: list[str]) -> list[dict[str, object]]:
    stage_status = {
        str(row.get("stage_id") or ""): str(row.get("status") or "")
        for row in blackboard.table_rows("stages", run_id=run_id)
    }
    blockers: list[dict[str, object]] = []
    for row in blackboard.table_rows("error_details", run_id=run_id):
        source_stage = str(row.get("stage_id") or "")
        if source_stage not in target_stage_ids:
            continue
        if stage_status.get(source_stage) in {str(StageStatus.COMPLETED), str(StageStatus.SKIPPED)}:
            continue
        blockers.append(_blocker(str(row.get("type") or "runtime_error"), "high", str(row.get("message") or "Runtime error remains open.")))
    return blockers


def _pending_provider_action_blockers(blackboard: Blackboard, run_id: str, target_stage_ids: list[str]) -> list[dict[str, object]]:
    blockers: list[dict[str, object]] = []
    for row in blackboard.list_provider_actions(status=str(ProviderActionStatus.PENDING), run_id=run_id):
        source_stage = str(row.get("stage_id") or "")
        if source_stage not in target_stage_ids:
            continue
        blockers.append(
            _blocker(
                "pending_provider_action",
                "high",
                str(row.get("prompt_text") or "A provider action is still pending."),
            )
        )
    return blockers


def _secret_term_blockers(blackboard: Blackboard, run_id: str, target_stage_ids: list[str]) -> list[dict[str, object]]:
    blockers: list[dict[str, object]] = []
    secret_patterns = ("api_key", "secret_key", "private_key", "password=", "token=")
    for row in blackboard.table_rows("artifacts", run_id=run_id):
        if str(row.get("stage_id") or "") not in target_stage_ids:
            continue
        if row.get("kind") != "stage_output":
            continue
        path = Path(str(row.get("path") or ""))
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace").lower()
        if any(pattern in text for pattern in secret_patterns):
            blockers.append(_blocker("possible_secret_output", "high", f"Stage {row.get('stage_id')} output may contain secret-like text."))
    return blockers


def _design_document_complete_blockers(blackboard: Blackboard, run_id: str, target_stage_ids: list[str]) -> list[dict[str, object]]:
    design_stage_ids = set(target_stage_ids)
    if any(stage_id.startswith("design") for stage_id in design_stage_ids):
        design_stage_ids.add("design_revise")
    sections: list[tuple[str, str]] = []
    for row in blackboard.table_rows("artifacts", run_id=run_id):
        if row.get("kind") != "stage_output":
            continue
        stage_id = str(row.get("stage_id") or "")
        if stage_id not in design_stage_ids:
            continue
        path = Path(str(row.get("path") or ""))
        if not path.exists() or not path.is_file():
            continue
        sections.append((stage_id, path.read_text(encoding="utf-8", errors="replace")))
    issues = design_document_quality_issues(sections=sections)
    return [
        _blocker(
            "design_document_incomplete",
            "high",
            issue,
        )
        for issue in issues
    ]


def _latest_role_decision(blackboard: Blackboard, run_id: str, stage_id: str) -> str | None:
    rows = [
        row
        for row in blackboard.table_rows("stage_contracts", run_id=run_id)
        if row.get("stage_id") == stage_id and row.get("decision")
    ]
    if not rows:
        return None
    return str(max(rows, key=lambda row: str(row.get("created_at") or ""))["decision"])


def _derive_checks(section: dict[str, str], skill: dict[str, object]) -> list[str]:
    text = " ".join(
        [
            str(section.get("delivery_standard_text") or ""),
            str(section.get("required_deliverable") or ""),
            str(section.get("pass_when") or ""),
            str(section.get("block_when") or ""),
            str(section.get("evidence") or ""),
            str(skill.get("name") or ""),
            str(skill.get("role") or ""),
        ]
    ).lower()
    checks = ["artifact_nonempty", "no_runtime_errors", "no_pending_provider_actions"]
    if any(token in text for token in ("test", "pytest", "coverage", "verify", "verification", "smoke")):
        checks.append("no_failed_tests")
    if any(token in text for token in ("review", "blocker", "severity", "finding", "risk")):
        checks.append("no_open_blockers")
    if any(token in text for token in ("secret", "password", "token", "private key")):
        checks.append("no_secret_terms")
    skill_name = str(skill.get("name") or "").lower()
    skill_stage = str(skill.get("stage") or "").lower()
    skill_role = str(skill.get("role") or "").lower()
    is_design_rule = (
        skill_stage.startswith("design")
        or skill_name == "default-architect"
        or (skill_role in {"architect", "plan"} and "design" in text)
    )
    if is_design_rule and any(token in text for token in ("design document", "design_doc", "complete design", "设计文档")):
        checks.append("design_document_complete")
    return _dedupe(checks)


def _derive_required_evidence(section: dict[str, str]) -> list[str]:
    evidence = str(section.get("evidence") or "").lower()
    result: list[str] = []
    if any(token in evidence for token in ("stage", "output", "artifact", "plan", "design")):
        result.append("stage_output")
    if any(token in evidence for token in ("test", "coverage", "smoke", "command")):
        result.append("test_result")
    if any(token in evidence for token in ("review", "blocker", "finding")):
        result.append("review_result")
    if "diff" in evidence or "changed file" in evidence:
        result.append("diff")
    return _dedupe(result)


def _delivery_gate_policy(value: object) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {"checks": [], "required_evidence": []}
    return {
        "checks": _string_list(value.get("checks")),
        "required_evidence": _string_list(value.get("required_evidence")),
    }


def _blocker(
    type_: str,
    severity: str,
    suggestion: str,
    *,
    file: str | None = None,
    line: int | None = None,
) -> dict[str, object]:
    return {
        "type": type_,
        "severity": _severity(severity),
        "file": file,
        "line": line,
        "suggestion": suggestion,
    }


def _severity(value: str) -> str:
    lowered = value.lower()
    return lowered if lowered in {"low", "medium", "high"} else "medium"


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if str(value).strip() else []


def _dedupe(values: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
