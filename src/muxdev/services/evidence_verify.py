"""Standalone verification for the single Evidence v3 report artifact."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..models.evidence import EvidenceReport, canonical_hash
from ..storage.control import ControlStore
from .gate import evaluate_gate


def verify_evidence_report(path: Path, *, store: ControlStore | None = None) -> dict[str, object]:
    errors: list[str] = []
    try:
        report = EvidenceReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"valid": False, "errors": [f"invalid evidence report: {exc}"], "path": str(path)}
    computed = evaluate_gate(
        report.policy,
        report.records,
        subject_digest=str(report.subject.get("digest") or "") or None,
    ).model_dump(mode="json", exclude={"evaluated_at"})
    recorded = report.decision.model_dump(mode="json", exclude={"evaluated_at"})
    if computed != recorded:
        errors.append("GateDecision is not reproducible from the frozen policy and records")
    expected_records = canonical_hash([item.model_dump(mode="json") for item in report.records])
    if report.integrity.get("records_hash") != expected_records:
        errors.append("evidence record hash mismatch")
    for record in report.records:
        if record.kind != "artifact":
            continue
        artifact_path = Path(record.path)
        if not artifact_path.is_file():
            errors.append(f"artifact is missing: {record.path}")
            continue
        actual = "sha256:" + hashlib.sha256(artifact_path.read_bytes()).hexdigest()
        if actual != record.digest:
            errors.append(f"artifact digest mismatch: {record.path}")
    if store:
        chain_valid, chain_errors = store.verify_event_chain(report.run_id)
        if not chain_valid:
            errors.extend(chain_errors)
        sequence = int(report.integrity.get("event_chain_sequence") or 0)
        head = report.integrity.get("event_chain_head")
        prefix = next((item for item in store.events(report.run_id) if item["sequence"] == sequence), None)
        if not prefix or prefix.get("event_hash") != head:
            errors.append("evidence report event-chain head mismatch")
    return {
        "valid": not errors,
        "errors": errors,
        "path": str(path),
        "run_id": report.run_id,
        "gate_status": report.decision.status,
        "scorecard": report.decision.scorecard.model_dump(mode="json"),
    }
