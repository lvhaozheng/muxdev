"""Human-facing evidence scorecard generation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..models import utc_now
from ..models.evidence import ArtifactRef, CoverageRow, EvidenceItem, EvidenceScorecard
from ..storage import Blackboard, sha256_file
from ..storage.contracts import write_json_artifact


COMPONENT_MAX = {
    "task_fit": 15,
    "change_traceability": 15,
    "verification": 20,
    "review_security": 20,
    "reproducibility": 10,
    "integrity_rollback": 15,
    "memory_safety": 5,
}
SENSITIVE_TERMS = {"auth", "oauth", "login", "payment", "billing", "permission", "secret", "token", "migration", "security"}


def write_evidence_scorecard(run_dir: Path, run_id: str, blackboard: Blackboard) -> dict[str, Any]:
    """Write scorecard artifacts and persist their queryable blackboard rows."""
    payload = build_evidence_scorecard(run_dir, run_id, blackboard)
    scorecard = payload["scorecard"]
    coverage = payload["coverage_matrix"]
    items = payload["evidence_items"]

    scorecard_path, scorecard_hash = write_json_artifact(run_dir / "evidence" / "scorecard.json", scorecard)
    coverage_path, coverage_hash = write_json_artifact(run_dir / "evidence" / "coverage_matrix.json", {"run_id": run_id, "items": coverage, "created_at": utc_now()})
    summary_path = run_dir / "evidence" / "human_summary.md"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(render_scorecard_markdown(scorecard, coverage), encoding="utf-8")
    summary_hash = sha256_file(summary_path)

    blackboard.upsert_evidence_scorecard(
        run_id=run_id,
        score=int(scorecard["score"]),
        label=str(scorecard["label"]),
        recommendation=str(scorecard["recommendation"]),
        components={str(key): int(value) for key, value in scorecard["components"].items()},
        risk_penalty=int(scorecard["risk_penalty"]),
        missing_evidence=[str(item) for item in scorecard.get("missing_evidence", [])],
        next_actions=[item for item in scorecard.get("next_actions", []) if isinstance(item, dict)],
        path=scorecard_path,
        scorecard_hash=scorecard_hash,
    )
    for item in items:
        blackboard.upsert_evidence_item(
            run_id=run_id,
            evidence_id=str(item["id"]),
            stage_id=item.get("stage_id"),
            kind=str(item["kind"]),
            strength=str(item["strength"]),
            claim=str(item["claim"]),
            supports=[str(value) for value in item.get("supports", [])],
            relevance=item.get("relevance"),
            confidence=item.get("confidence"),
            artifact_refs=[ref for ref in item.get("artifact_refs", []) if isinstance(ref, dict)],
            human_summary=str(item.get("human_summary") or item.get("summary") or item.get("claim")),
        )
    blackboard.add_artifact(run_id, None, scorecard_path.name, scorecard_path, "evidence_scorecard")
    blackboard.add_artifact(run_id, None, coverage_path.name, coverage_path, "evidence_coverage")
    blackboard.add_artifact(run_id, None, summary_path.name, summary_path, "evidence_summary")
    return {
        "scorecard": scorecard,
        "coverage_matrix": coverage,
        "evidence_items": items,
        "artifacts": {
            "scorecard": str(scorecard_path),
            "scorecard_hash": scorecard_hash,
            "coverage_matrix": str(coverage_path),
            "coverage_hash": coverage_hash,
            "human_summary": str(summary_path),
            "human_summary_hash": summary_hash,
        },
    }


def build_evidence_scorecard(run_dir: Path, run_id: str, blackboard: Blackboard) -> dict[str, Any]:
    run = blackboard.get_run(run_id)
    rows = {
        "stages": blackboard.table_rows("stages", run_id=run_id),
        "artifacts": blackboard.table_rows("artifacts", run_id=run_id),
        "provider_attempts": blackboard.table_rows("provider_attempts", run_id=run_id),
        "tests": blackboard.table_rows("test_results", run_id=run_id),
        "blockers": blackboard.table_rows("review_blockers", run_id=run_id),
        "errors": blackboard.table_rows("error_details", run_id=run_id),
        "contracts": blackboard.table_rows("stage_contracts", run_id=run_id),
        "bundles": blackboard.table_rows("evidence_bundles", run_id=run_id),
        "ledger": blackboard.table_rows("ledger_events", run_id=run_id),
        "snapshots": blackboard.table_rows("snapshots", run_id=run_id),
        "validators": blackboard.table_rows("validator_panels", run_id=run_id),
        "approvals": blackboard.table_rows("approvals", run_id=run_id),
    }
    evidence_items = _evidence_items(run_dir, run, rows)
    components = _score_components(run_dir, run, rows)
    risk_penalty = _risk_penalty(run, rows)
    score = max(0, min(100, sum(components.values()) - risk_penalty))
    label = _score_label(score, rows)
    coverage = _coverage_matrix(str(run.get("task", "")), rows, evidence_items)
    missing = _missing_evidence(run, rows, coverage)
    top_reasons = _top_reasons(run_dir, rows)
    scorecard = EvidenceScorecard(
        run_id=run_id,
        score=score,
        label=label,
        recommendation=_recommendation(label),
        components=components,
        risk_penalty=risk_penalty,
        top_reasons=top_reasons,
        missing_evidence=missing,
        evidence_counts=_evidence_counts(evidence_items),
        coverage_summary=_coverage_summary(coverage),
        next_actions=_next_actions(label, missing),
    ).model_dump()
    return {
        "scorecard": scorecard,
        "coverage_matrix": [row.model_dump() for row in coverage],
        "evidence_items": evidence_items,
    }


def render_scorecard_markdown(scorecard: dict[str, Any], coverage: list[dict[str, Any]] | list[CoverageRow]) -> str:
    rows = [row.model_dump() if isinstance(row, CoverageRow) else row for row in coverage]
    lines = [
        f"# Evidence Scorecard: {scorecard['run_id']}",
        "",
        f"- Delivery Confidence: {scorecard['score']} / 100",
        f"- Label: {scorecard['label']}",
        f"- Recommendation: {scorecard['recommendation']}",
        "",
        "## Why",
    ]
    lines.extend(f"- {reason}" for reason in scorecard.get("top_reasons", []) or ["No positive evidence recorded yet."])
    lines.extend(["", "## Missing Evidence"])
    lines.extend(f"- {item}" for item in scorecard.get("missing_evidence", []) or ["none"])
    lines.extend(["", "## Coverage Matrix", "", "| AC | Criterion | Implementation | Tests | Review | Missing |", "| --- | --- | --- | --- | --- | --- |"])
    for row in rows:
        lines.append(
            f"| {row.get('acceptance_id')} | {row.get('criterion')} | {row.get('implementation')} | {row.get('tests')} | {row.get('review')} | {', '.join(row.get('missing', [])) or '-'} |"
        )
    lines.extend(["", "## Next Actions"])
    for action in scorecard.get("next_actions", []):
        label = action.get("label", "Next action") if isinstance(action, dict) else str(action)
        detail = action.get("command") if isinstance(action, dict) else None
        lines.append(f"- {label}" + (f": `{detail}`" if detail else ""))
    return "\n".join(lines) + "\n"


def render_scorecard_text(payload: dict[str, Any], *, audit: bool = False) -> str:
    scorecard = payload.get("scorecard", payload)
    if not isinstance(scorecard, dict):
        return "No evidence scorecard is available for this run."
    lines = [
        f"run_id: {scorecard.get('run_id', '-')}",
        f"delivery confidence: {scorecard.get('score', 0)} / 100  {scorecard.get('label', '-')}",
        f"recommendation: {scorecard.get('recommendation', '-')}",
        "",
        "why:",
    ]
    lines.extend(f"- {reason}" for reason in scorecard.get("top_reasons", []) or ["No positive evidence recorded yet."])
    lines.append("")
    lines.append("missing evidence:")
    lines.extend(f"- {item}" for item in scorecard.get("missing_evidence", []) or ["none"])
    lines.append("")
    lines.append("next:")
    for action in scorecard.get("next_actions", []):
        if isinstance(action, dict) and action.get("command"):
            lines.append(f"- {action.get('label')}: {action.get('command')}")
        elif isinstance(action, dict):
            lines.append(f"- {action.get('label', action.get('kind', 'next action'))}")
    if audit:
        lines.append("")
        lines.append("audit pack:")
        audit_pack = payload.get("audit_pack", {}) if isinstance(payload.get("audit_pack"), dict) else {}
        for key in ("contracts", "evidence_bundles", "validators", "ledger_events", "snapshots"):
            lines.append(f"- {key}: {audit_pack.get(key, 0)}")
    return "\n".join(lines)


def load_scorecard_artifacts(run_dir: Path, run_id: str, blackboard: Blackboard) -> dict[str, Any]:
    scorecards = blackboard.table_rows("evidence_scorecards", run_id=run_id)
    scorecard_path = run_dir / "evidence" / "scorecard.json"
    coverage_path = run_dir / "evidence" / "coverage_matrix.json"
    scorecard = _read_json(scorecard_path) or (_scorecard_row_payload(scorecards[-1]) if scorecards else None)
    coverage_payload = _read_json(coverage_path) or {"items": []}
    items = blackboard.table_rows("evidence_items", run_id=run_id)
    contracts = blackboard.table_rows("stage_contracts", run_id=run_id)
    bundles = blackboard.table_rows("evidence_bundles", run_id=run_id)
    validators = blackboard.table_rows("validator_panels", run_id=run_id)
    ledger = blackboard.table_rows("ledger_events", run_id=run_id)
    snapshots = blackboard.table_rows("snapshots", run_id=run_id)
    return {
        "scorecard": scorecard,
        "coverage_matrix": coverage_payload.get("items", []) if isinstance(coverage_payload, dict) else [],
        "evidence_items": items,
        "audit_pack": {
            "contracts": len(contracts),
            "evidence_bundles": len(bundles),
            "validators": len(validators),
            "ledger_events": len(ledger),
            "snapshots": len(snapshots),
            "stage_contracts": contracts,
            "raw_evidence_bundles": bundles,
            "validator_panels": validators,
            "raw_ledger_events": ledger,
            "raw_snapshots": snapshots,
        },
    }


def _evidence_items(run_dir: Path, run: dict[str, Any], rows: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    items: list[EvidenceItem] = []
    run_id = str(run.get("run_id") or "run")
    for index, result in enumerate(rows["tests"], start=1):
        passed = bool(result.get("passed"))
        items.append(
            EvidenceItem(
                id=_evidence_id(run_id, f"test-{index:03d}"),
                kind="test_evidence",
                strength="A" if passed else "B",
                claim=f"{result.get('command') or 'test command'} {'passed' if passed else 'failed'}",
                supports=["tests", str(result.get("stage_id") or "test")],
                command=str(result.get("command") or ""),
                exit_code=0 if passed else 1,
                summary=str(result.get("summary") or ""),
                relevance=0.9 if passed else 0.8,
                confidence=0.95 if passed else 0.85,
                human_summary=str(result.get("summary") or result.get("command") or "test result recorded"),
            )
        )
    for index, blocker in enumerate(rows["blockers"], start=1):
        severity = str(blocker.get("severity") or "medium")
        items.append(
            EvidenceItem(
                id=_evidence_id(run_id, f"review-{index:03d}"),
                kind="review_evidence",
                strength="A" if severity == "high" else "B",
                claim=f"{severity} review blocker: {blocker.get('suggestion')}",
                supports=["review", str(blocker.get("file") or "")],
                summary=str(blocker.get("suggestion") or ""),
                relevance=0.85,
                confidence=0.9,
                human_summary=str(blocker.get("suggestion") or "review blocker recorded"),
            )
        )
    for index, validator in enumerate(rows["validators"], start=1):
        path = Path(str(validator.get("path") or ""))
        items.append(
            EvidenceItem(
                id=_evidence_id(run_id, f"validator-{index:03d}"),
                kind="review_evidence",
                strength="A",
                claim=f"blind validator decision: {validator.get('decision')}",
                supports=["validator"],
                artifact_refs=[_artifact_ref(path)],
                summary=str(validator.get("decision") or ""),
                relevance=0.9,
                confidence=0.9,
                human_summary=f"Blind validator returned {validator.get('decision')}",
            )
        )
    for index, bundle in enumerate(rows["bundles"], start=1):
        path = Path(str(bundle.get("path") or ""))
        items.append(
            EvidenceItem(
                id=_evidence_id(run_id, f"bundle-{index:03d}"),
                kind="runtime_evidence",
                strength="B",
                claim=f"stage evidence bundle recorded for {bundle.get('stage_id') or 'run'}",
                supports=[str(bundle.get("stage_id") or "run")],
                artifact_refs=[_artifact_ref(path)],
                relevance=0.7,
                confidence=0.8,
                human_summary=f"Evidence bundle for {bundle.get('stage_id') or 'run'} is present",
            )
        )
    diff_path = run_dir / "diff.patch"
    if diff_path.exists():
        items.append(
            EvidenceItem(
                id=_evidence_id(run_id, "change-001"),
                kind="change_evidence",
                strength="B",
                claim="run diff artifact is available",
                supports=["diff"],
                artifact_refs=[_artifact_ref(diff_path)],
                relevance=0.82,
                confidence=0.86,
                human_summary="Patch diff is available for review",
            )
        )
    for index, approval in enumerate(rows["approvals"], start=1):
        if approval.get("status") != "approved":
            continue
        items.append(
            EvidenceItem(
                id=_evidence_id(run_id, f"human-{index:03d}"),
                kind="human_evidence",
                strength="C",
                claim=f"{approval.get('type')} approval was granted",
                supports=[str(approval.get("type") or "approval")],
                summary=str(approval.get("reason") or ""),
                relevance=0.7,
                confidence=0.75,
                human_summary=f"Human approval recorded for {approval.get('type')}",
            )
        )
    return [item.model_dump() for item in items]


def _score_components(run_dir: Path, run: dict[str, Any], rows: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    completed = sum(1 for row in rows["stages"] if row.get("status") in {"completed", "skipped"})
    total = len(rows["stages"])
    passed_tests = [row for row in rows["tests"] if bool(row.get("passed"))]
    failed_tests = [row for row in rows["tests"] if not bool(row.get("passed"))]
    validators_accept = any(row.get("decision") == "accept" for row in rows["validators"])
    high_findings = _has_high_findings(rows)
    integrity_valid = _integrity_valid(rows)
    diff_exists = (run_dir / "diff.patch").exists()
    full_regression = any(_looks_full_regression(row.get("command")) for row in rows["tests"])
    return {
        "task_fit": 15 if str(run.get("status")) == "completed" else (10 if total and completed / total >= 0.7 else 6),
        "change_traceability": min(15, (5 if rows["bundles"] else 0) + (5 if rows["snapshots"] else 0) + (3 if diff_exists else 0) + (2 if rows["contracts"] else 0)),
        "verification": 0 if failed_tests else (20 if full_regression and passed_tests else (18 if passed_tests else 4)),
        "review_security": 0 if high_findings else (16 if validators_accept and not rows["blockers"] else (12 if not rows["blockers"] and not rows["errors"] else 8)),
        "reproducibility": min(10, (5 if rows["tests"] else 0) + (3 if rows.get("provider_attempts", []) else 0) + (2 if rows["contracts"] else 0)),
        "integrity_rollback": 0 if not integrity_valid else min(15, (5 if rows["ledger"] else 0) + (5 if rows["snapshots"] else 0) + (3 if rows["validators"] else 0) + (2 if diff_exists else 0)),
        "memory_safety": 0 if rows["errors"] else (5 if rows["bundles"] else 3),
    }


def _risk_penalty(run: dict[str, Any], rows: dict[str, list[dict[str, Any]]]) -> int:
    penalty = 0
    if _has_high_findings(rows):
        penalty += 25
    if any(not bool(row.get("passed")) for row in rows["tests"]):
        penalty += 15
    task = str(run.get("task") or "").lower()
    if any(term in task for term in SENSITIVE_TERMS) and not any(bool(row.get("passed")) for row in rows["tests"]):
        penalty += 10
    if not rows["bundles"] or not rows["contracts"]:
        penalty += 8
    if rows["tests"] and not any(_looks_full_regression(row.get("command")) for row in rows["tests"]):
        penalty += 3
    if not rows["tests"]:
        penalty += 8
    return min(45, penalty)


def _coverage_matrix(task: str, rows: dict[str, list[dict[str, Any]]], evidence_items: list[dict[str, Any]]) -> list[CoverageRow]:
    criteria = _acceptance_criteria(task)
    refs = [str(item.get("id")) for item in evidence_items]
    has_change = bool(rows["bundles"] or rows["snapshots"])
    has_passed_tests = any(bool(row.get("passed")) for row in rows["tests"])
    has_failed_tests = any(not bool(row.get("passed")) for row in rows["tests"])
    review_blocked = bool(rows["blockers"] or any(row.get("decision") == "reject" for row in rows["validators"]))
    review_ok = bool(rows["validators"]) and not review_blocked
    matrix: list[CoverageRow] = []
    for acceptance_id, criterion in criteria:
        missing: list[str] = []
        if not has_change:
            missing.append("implementation evidence missing")
        if not has_passed_tests:
            missing.append("passing test evidence missing" if not has_failed_tests else "tests failing")
        if not review_ok:
            missing.append("review/validator evidence missing" if not review_blocked else "review blockers present")
        matrix.append(
            CoverageRow(
                acceptance_id=acceptance_id,
                criterion=criterion,
                implementation="covered" if has_change else "missing",
                tests="covered" if has_passed_tests else ("failed" if has_failed_tests else "missing"),
                review="covered" if review_ok else ("blocked" if review_blocked else "missing"),
                evidence_refs=refs[:6],
                missing=missing,
            )
        )
    return matrix


def _missing_evidence(run: dict[str, Any], rows: dict[str, list[dict[str, Any]]], coverage: list[CoverageRow]) -> list[str]:
    missing: list[str] = []
    if not rows["tests"]:
        missing.append("targeted tests not recorded")
    elif not any(bool(row.get("passed")) for row in rows["tests"]):
        missing.append("passing test evidence missing")
    if rows["tests"] and not any(_looks_full_regression(row.get("command")) for row in rows["tests"]):
        missing.append("full regression not run")
    if any(row.get("severity") == "high" for row in rows["blockers"]):
        missing.append("high review blocker must be resolved")
    if any(row.get("decision") == "reject" for row in rows["validators"]):
        missing.append("blind validator rejected current evidence")
    if not rows["snapshots"]:
        missing.append("rollback snapshot missing")
    if not rows["ledger"]:
        missing.append("hash ledger missing")
    task = str(run.get("task") or "").lower()
    if any(term in task for term in SENSITIVE_TERMS) and not any(bool(row.get("passed")) for row in rows["tests"]):
        missing.append("sensitive path changed without passing targeted tests")
    for row in coverage:
        for item in row.missing:
            if item not in missing:
                missing.append(item)
    return missing[:8]


def _top_reasons(run_dir: Path, rows: dict[str, list[dict[str, Any]]]) -> list[str]:
    reasons: list[str] = []
    if any(bool(row.get("passed")) for row in rows["tests"]):
        reasons.append("targeted tests passed")
    if rows["validators"] and all(row.get("decision") == "accept" for row in rows["validators"]):
        reasons.append("blind validator accepted the run")
    if rows["blockers"]:
        reasons.append("review blockers were recorded")
    else:
        reasons.append("no review blockers recorded")
    if rows["snapshots"]:
        reasons.append("rollback snapshot available")
    if rows["ledger"]:
        reasons.append("hash ledger is present")
    if (run_dir / "diff.patch").exists():
        reasons.append("diff artifact is available")
    return reasons[:5]


def _next_actions(label: str, missing: list[str]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    if any("test" in item or "regression" in item for item in missing):
        actions.append({"kind": "run_command", "label": "Run full regression", "command": "pytest -q"})
    actions.append({"kind": "view_diff", "label": "View diff", "command": "muxdev diff latest"})
    if label in {"ready", "reviewable"}:
        actions.append({"kind": "approve", "label": "Approve merge with noted risk"})
    else:
        actions.append({"kind": "open_audit_pack", "label": "Open audit pack", "command": "muxdev evidence latest --audit"})
    return actions[:4]


def _score_label(score: int, rows: dict[str, list[dict[str, Any]]]) -> str:
    if _has_high_findings(rows) or score < 60:
        return "blocked"
    if score >= 90:
        return "ready"
    if score >= 75:
        return "reviewable"
    return "risky"


def _recommendation(label: str) -> str:
    return {
        "ready": "ready_to_ship",
        "reviewable": "merge_after_review",
        "risky": "needs_more_verification",
        "blocked": "blocked",
    }[label]


def _evidence_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[f"strength_{item.get('strength')}"] = counts.get(f"strength_{item.get('strength')}", 0) + 1
        counts[str(item.get("kind"))] = counts.get(str(item.get("kind")), 0) + 1
    return counts


def _coverage_summary(rows: list[CoverageRow]) -> dict[str, int]:
    return {
        "criteria": len(rows),
        "implemented": sum(1 for row in rows if row.implementation == "covered"),
        "tested": sum(1 for row in rows if row.tests == "covered"),
        "reviewed": sum(1 for row in rows if row.review == "covered"),
        "missing": sum(1 for row in rows if row.missing),
    }


def _acceptance_criteria(task: str) -> list[tuple[str, str]]:
    parts = [part.strip(" -") for part in re.split(r"[\n.;；。]| and |,|，|、", task) if part.strip(" -")]
    compact = [part for part in parts if len(part) >= 6]
    if not compact:
        compact = [f"Complete requested task: {task.strip() or 'current task'}"]
    return [(f"AC-{index}", _clip_text(part, 96)) for index, part in enumerate(compact[:5], start=1)]


def _evidence_id(run_id: str, suffix: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", run_id).strip("-") or "run"
    return f"ev-{safe}-{suffix}"


def _artifact_ref(path: Path) -> ArtifactRef:
    return ArtifactRef(path=str(path), sha256=sha256_file(path) if path.exists() and path.is_file() else None)


def _integrity_valid(rows: dict[str, list[dict[str, Any]]]) -> bool:
    checks = [
        ("contracts", "path", "contract_hash"),
        ("bundles", "path", "bundle_hash"),
        ("validators", "path", "validator_hash"),
    ]
    for group, path_key, hash_key in checks:
        for row in rows[group]:
            path = Path(str(row.get(path_key) or ""))
            if not path.exists() or (row.get(hash_key) and sha256_file(path) != row.get(hash_key)):
                return False
    return True


def _has_high_findings(rows: dict[str, list[dict[str, Any]]]) -> bool:
    if rows["errors"]:
        return True
    if any(row.get("severity") == "high" for row in rows["blockers"]):
        return True
    return any(row.get("decision") == "reject" for row in rows["validators"])


def _looks_full_regression(command: object) -> bool:
    text = str(command or "").lower()
    return text.strip() in {"pytest -q", "python -m pytest -q", "pytest", "python -m pytest"} or " full" in text


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _scorecard_row_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": row.get("run_id"),
        "score": row.get("score"),
        "label": row.get("label"),
        "recommendation": row.get("recommendation"),
        "components": _json_dict(row.get("components_json")),
        "risk_penalty": row.get("risk_penalty"),
        "missing_evidence": _json_list(row.get("missing_evidence_json")),
        "next_actions": _json_list(row.get("next_actions_json")),
        "scorecard_hash": row.get("scorecard_hash"),
        "created_at": row.get("created_at"),
    }


def _json_list(value: object) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _json_dict(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _clip_text(value: str, width: int) -> str:
    return value if len(value) <= width else value[: width - 3] + "..."
