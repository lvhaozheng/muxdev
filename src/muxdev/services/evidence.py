"""Evidence v2 recording, evaluation, verification, and cleanup."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.redaction import redact
from ..core.standards import event_standard, standard_scores
from ..models.evidence import ArtifactRef, EvidenceEvaluation, EvidenceEvent, EvidenceManifest
from ..storage import Blackboard, canonical_hash, sha256_file
from ..storage.contracts import write_json_artifact


LEGACY_EVIDENCE_TABLES = ("evidence_bundles", "evidence_items", "evidence_scorecards")
LEGACY_EVIDENCE_FILES = ("scorecard.json", "coverage_matrix.json", "human_summary.md")


def write_evidence_run(run_dir: Path, run_id: str, blackboard: Blackboard) -> dict[str, Any]:
    """Write Evidence v2 events, manifest, and evaluation for a run."""
    events = _hash_events(_collect_events(run_dir, run_id, blackboard))
    evidence_dir = run_dir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    events_path = evidence_dir / "events.jsonl"
    events_path.write_text("".join(json.dumps(event.model_dump(), ensure_ascii=False, sort_keys=True) + "\n" for event in events), encoding="utf-8")

    manifest = _build_manifest(run_dir, run_id, events)
    evaluation = _build_evaluation(run_id, events, manifest)
    manifest_path, manifest_hash = write_json_artifact(evidence_dir / "manifest.json", manifest.model_dump())
    evaluation_path, evaluation_hash = write_json_artifact(evidence_dir / "evaluation.json", evaluation.model_dump())

    blackboard.replace_evidence_v2(
        run_id=run_id,
        events=[event.model_dump() for event in events],
        manifest=manifest.model_dump(),
        manifest_path=manifest_path,
        manifest_hash=manifest_hash,
        evaluation=evaluation.model_dump(),
        evaluation_path=evaluation_path,
        evaluation_hash=evaluation_hash,
    )
    blackboard.add_artifact(run_id, None, events_path.name, events_path, "evidence_events")
    blackboard.add_artifact(run_id, None, manifest_path.name, manifest_path, "evidence_manifest")
    blackboard.add_artifact(run_id, None, evaluation_path.name, evaluation_path, "evidence_evaluation")
    return {
        "run_id": run_id,
        "events": [event.model_dump() for event in events],
        "manifest": manifest.model_dump(),
        "evaluation": evaluation.model_dump(),
        "artifacts": {
            "events": str(events_path),
            "manifest": str(manifest_path),
            "manifest_hash": manifest_hash,
            "evaluation": str(evaluation_path),
            "evaluation_hash": evaluation_hash,
        },
    }


def load_evidence_artifacts(run_dir: Path, run_id: str, blackboard: Blackboard | None = None) -> dict[str, Any]:
    """Load Evidence v2 artifacts and blackboard rows for display."""
    evidence_dir = run_dir / "evidence"
    manifest = _read_json(evidence_dir / "manifest.json")
    evaluation = _read_json(evidence_dir / "evaluation.json")
    events = _read_events(evidence_dir / "events.jsonl")
    payload: dict[str, Any] = {
        "run_id": run_id,
        "manifest": manifest,
        "evaluation": evaluation,
        "events": events,
        "artifacts": {
            "events": str(evidence_dir / "events.jsonl"),
            "manifest": str(evidence_dir / "manifest.json"),
            "evaluation": str(evidence_dir / "evaluation.json"),
        },
    }
    if blackboard is not None:
        payload["blackboard"] = {
            "events": blackboard.table_rows("evidence_events", run_id=run_id),
            "manifests": blackboard.table_rows("evidence_manifests", run_id=run_id),
            "evaluations": blackboard.table_rows("evidence_evaluations", run_id=run_id),
        }
    return payload


def render_evidence_text(payload: dict[str, Any], *, include_events: bool = False) -> str:
    """Render a concise Evidence v2 summary."""
    manifest = payload.get("manifest") if isinstance(payload.get("manifest"), dict) else {}
    evaluation = payload.get("evaluation") if isinstance(payload.get("evaluation"), dict) else {}
    if not manifest and not evaluation:
        return "No Evidence v2 artifacts are available for this run."
    lines = [
        f"run_id: {payload.get('run_id') or manifest.get('run_id') or evaluation.get('run_id') or '-'}",
        f"label: {evaluation.get('label', '-')}",
        f"confidence: {float(evaluation.get('confidence') or 0):.2f}",
        f"events: {manifest.get('event_count', 0)}",
        f"head_hash: {manifest.get('head_hash') or '-'}",
        "",
        "reasons:",
    ]
    lines.extend(f"- {reason}" for reason in evaluation.get("reasons", []) or ["No evaluation reasons recorded."])
    lines.append("")
    lines.append("missing evidence:")
    lines.extend(f"- {item}" for item in evaluation.get("missing_evidence", []) or ["none"])
    lines.append("")
    lines.append("next:")
    for action in evaluation.get("next_actions", []):
        if isinstance(action, dict):
            label = action.get("label") or action.get("kind") or "next action"
            command = action.get("command")
            lines.append(f"- {label}" + (f": {command}" if command else ""))
    if include_events:
        events = payload.get("events", []) if isinstance(payload.get("events"), list) else []
        lines.extend(["", "events:"])
        for event in events[:20]:
            if isinstance(event, dict):
                lines.append(f"- {event.get('id')}: {event.get('layer')}/{event.get('kind')} {event.get('status')} - {event.get('claim')}")
    return "\n".join(lines)


def verify_run_evidence(run_dir: Path, run_id: str, blackboard: Blackboard | None = None) -> dict[str, Any]:
    """Verify Evidence v2 manifest, event chain, and artifact hashes."""
    owns_blackboard = blackboard is None
    board = blackboard or Blackboard(run_dir)
    try:
        loaded = load_evidence_artifacts(run_dir, run_id, board)
        manifest = loaded.get("manifest") if isinstance(loaded.get("manifest"), dict) else None
        evaluation = loaded.get("evaluation") if isinstance(loaded.get("evaluation"), dict) else None
        events = loaded.get("events") if isinstance(loaded.get("events"), list) else []
        errors: list[str] = []
        if not manifest:
            errors.append("missing artifact: evidence/manifest.json")
        if not evaluation:
            errors.append("missing artifact: evidence/evaluation.json")
        if not events:
            errors.append("missing artifact: evidence/events.jsonl")
        if manifest and manifest.get("run_id") != run_id:
            errors.append("manifest run_id mismatch")
        if evaluation is not None:
            scores = evaluation.get("standard_scores")
            if not isinstance(scores, dict) or not {"severity", "risk", "evidence"} <= set(scores):
                errors.append("evaluation standard_scores missing required P/R/E coverage")
        chain = _verify_event_chain(events)
        errors.extend(chain["errors"])
        if manifest:
            if int(manifest.get("event_count") or 0) != len(events):
                errors.append("manifest event_count mismatch")
            if manifest.get("head_hash") != chain.get("head_hash"):
                errors.append("manifest head_hash mismatch")
        errors.extend(_verify_artifact_refs(events))
        errors.extend(_verify_persisted_hashes(run_dir, run_id, board))
        return {
            "run_id": run_id,
            "valid": not errors,
            "manifest": manifest or {},
            "evaluation": evaluation or {},
            "events": len(events),
            "artifacts": int(manifest.get("artifact_count") or 0) if manifest else 0,
            "head_hash": chain.get("head_hash"),
            "errors": errors,
        }
    finally:
        if owns_blackboard:
            board.close()


def cleanup_legacy_evidence(run_dir: Path, blackboard: Blackboard, *, yes: bool = False) -> dict[str, Any]:
    """Remove v1 evidence tables and artifacts from a run directory."""
    if not yes:
        raise ValueError("cleanup requires --yes")
    removed_files: list[str] = []
    failed_files: list[str] = []
    evidence_dir = run_dir / "evidence"
    if evidence_dir.exists():
        for name in LEGACY_EVIDENCE_FILES:
            path = evidence_dir / name
            if path.exists():
                if _try_unlink(path):
                    removed_files.append(str(path))
                else:
                    failed_files.append(str(path))
        for path in evidence_dir.glob("*.evidence.json"):
            if _try_unlink(path):
                removed_files.append(str(path))
            else:
                failed_files.append(str(path))
    dropped_tables: list[str] = []
    for table in LEGACY_EVIDENCE_TABLES:
        blackboard.conn.execute(f"DROP TABLE IF EXISTS {table}")
        dropped_tables.append(table)
    blackboard.conn.execute(
        "DELETE FROM artifacts WHERE kind IN (?, ?, ?, ?)",
        ("evidence_bundle", "evidence_scorecard", "evidence_coverage", "evidence_summary"),
    )
    blackboard.conn.commit()
    return {"run_dir": str(run_dir), "removed_files": removed_files, "failed_files": failed_files, "dropped_tables": dropped_tables}


def _collect_events(run_dir: Path, run_id: str, blackboard: Blackboard) -> list[EvidenceEvent]:
    run = blackboard.get_run(run_id)
    events: list[EvidenceEvent] = []

    def add(
        *,
        kind: str,
        claim: str,
        layer: str = "core",
        stage_id: str | None = None,
        status: str = "observed",
        strength: str = "C",
        subject_hash: str | None = None,
        artifact_refs: list[ArtifactRef] | None = None,
        metrics: dict[str, object] | None = None,
        tags: list[str] | None = None,
        source: str = "muxdev",
        standard_id: str | None = None,
        severity: str | None = None,
        risk_level: str | None = None,
        evidence_level: str | None = None,
    ) -> None:
        standard = event_standard(kind, status, metrics, tags)
        events.append(
            EvidenceEvent(
                id=_event_id(run_id, len(events) + 1, kind),
                run_id=run_id,
                stage_id=stage_id,
                layer=layer,  # type: ignore[arg-type]
                kind=kind,  # type: ignore[arg-type]
                claim=redact(claim),
                status=status,  # type: ignore[arg-type]
                strength=strength,  # type: ignore[arg-type]
                standard_id=standard_id or standard.standard_id,
                severity=severity or standard.severity,  # type: ignore[arg-type]
                risk_level=risk_level or standard.risk_level,  # type: ignore[arg-type]
                evidence_level=evidence_level or standard.evidence_level,  # type: ignore[arg-type]
                subject_hash=subject_hash,
                artifact_refs=artifact_refs or [],
                metrics=metrics or {},
                tags=tags or [],
                source=source,
            )
        )

    task_path = run_dir / "task.md"
    refs = [_artifact_ref(task_path, producer="runtime")] if task_path.exists() else []
    add(kind="task", claim=f"Task recorded for workflow {run.get('workflow')}", strength="A", artifact_refs=refs)
    diff_path = run_dir / "diff.patch"
    if diff_path.exists():
        add(kind="change", claim="Run diff artifact recorded", strength="A", artifact_refs=[_artifact_ref(diff_path, producer="runtime")])

    for row in blackboard.table_rows("stages", run_id=run_id):
        add(
            kind="stage",
            stage_id=str(row.get("stage_id") or ""),
            claim=f"Stage {row.get('stage_id')} finished with status {row.get('status')}",
            status=_status_from_stage(str(row.get("status") or "")),
            strength="B",
            metrics={"role": row.get("role"), "summary": row.get("summary")},
        )

    for event in _loop_trace_events(run_dir):
        add(
            kind="runtime",
            stage_id=event.get("stage"),
            claim=f"Loop engineering event: {event.get('type')}",
            status="blocked" if event.get("type") == "loop_blocked" else "observed",
            strength="B",
            metrics=event.get("data") if isinstance(event.get("data"), dict) else {},
            tags=["loop_engineering"],
        )

    for event in _policy_trace_events(run_dir):
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        standard = data.get("standard") if isinstance(data.get("standard"), dict) else {}
        decision = str(data.get("decision") or "observed")
        status = "passed" if decision.endswith("allow") or decision == "allow" else ("blocked" if decision.endswith("deny") or decision == "deny" else "observed")
        add(
            kind="policy",
            stage_id=event.get("stage"),
            claim=f"Policy decision for {data.get('approval_type') or data.get('command') or 'runtime gate'}: {decision}",
            status=status,
            strength="B",
            metrics={key: value for key, value in data.items() if key != "standard"},
            tags=["policy"],
            standard_id=str(standard.get("standard_id") or "") or None,
            severity=str(standard.get("severity") or "") or None,
            risk_level=str(standard.get("risk_level") or "") or None,
            evidence_level=str(standard.get("evidence_level") or "") or None,
        )

    for row in blackboard.table_rows("artifacts", run_id=run_id):
        path = Path(str(row.get("path") or ""))
        if path.name in LEGACY_EVIDENCE_FILES or path.suffixes[-2:] == [".evidence", ".json"]:
            continue
        add(
            kind="artifact",
            stage_id=row.get("stage_id"),
            claim=f"Artifact {row.get('name') or path.name} recorded as {row.get('kind')}",
            strength="B",
            artifact_refs=[_artifact_ref(path, producer=str(row.get("kind") or "artifact"))],
            metrics={"kind": row.get("kind")},
        )

    for row in blackboard.table_rows("test_results", run_id=run_id):
        passed = bool(row.get("passed"))
        add(
            kind="test",
            stage_id=str(row.get("stage_id") or "test"),
            claim=f"{row.get('command') or 'test command'} {'passed' if passed else 'failed'}",
            status="passed" if passed else "failed",
            strength="A" if passed else "B",
            metrics={"command": row.get("command"), "summary": row.get("summary")},
            tags=["verification"],
        )

    blockers = blackboard.table_rows("review_blockers", run_id=run_id)
    for row in blockers:
        severity = str(row.get("severity") or "medium")
        add(
            kind="review",
            stage_id=str(row.get("stage_id") or "review"),
            claim=f"{severity} review blocker: {row.get('suggestion')}",
            status="blocked" if severity == "high" else "failed",
            strength="A" if severity == "high" else "B",
            metrics={"type": row.get("type"), "file": row.get("file"), "line": row.get("line"), "severity": severity},
            tags=["review", "blocker"],
        )
    review_stage_completed = any(
        str(row.get("role") or "") == "review" and str(row.get("status") or "") in {"completed", "skipped"}
        for row in blackboard.table_rows("stages", run_id=run_id)
    )
    if review_stage_completed and not blockers:
        add(kind="review", stage_id="review", claim="Review stage completed without blockers", status="passed", strength="B", tags=["review"])

    for row in blackboard.table_rows("validator_panels", run_id=run_id):
        decision = str(row.get("decision") or "")
        path = Path(str(row.get("path") or ""))
        add(
            kind="review",
            claim=f"Blind validator decision: {decision}",
            status="passed" if decision == "accept" else "blocked",
            strength="A",
            artifact_refs=[_artifact_ref(path, producer="blind_validator")] if path else [],
            metrics={"validator_id": row.get("validator_id"), "decision": decision},
            tags=["validator"],
        )

    for row in blackboard.table_rows("approvals", run_id=run_id):
        status = str(row.get("status") or "")
        add(
            layer="approval",
            kind="approval",
            stage_id=row.get("stage_id"),
            claim=f"{row.get('type')} approval {status}",
            status="approved" if status == "approved" else ("rejected" if status == "denied" else "missing"),
            strength="A" if status == "approved" else "B",
            subject_hash=row.get("subject_hash"),
            metrics={"approval_id": row.get("approval_id"), "reason": row.get("reason")},
            tags=["approval"],
        )

    for row in blackboard.table_rows("provider_actions", run_id=run_id):
        action_status = str(row.get("status") or "")
        action_kind = str(row.get("kind") or "provider_action")
        add(
            kind="runtime",
            stage_id=row.get("stage_id"),
            claim=f"{action_kind} action {action_status}",
            status="passed" if action_status == "handled" else ("missing" if action_status == "pending" else "observed"),
            strength="B",
            metrics={
                "action_id": row.get("action_id"),
                "kind": action_kind,
                "input_kind": row.get("input_kind"),
                "response_present": bool(row.get("response_json")),
            },
            tags=["provider_action", action_kind],
        )

    for row in blackboard.table_rows("error_details", run_id=run_id):
        add(
            kind="runtime",
            stage_id=row.get("stage_id"),
            claim=f"{row.get('type')}: {row.get('message')}",
            status="failed",
            strength="A",
            metrics={"type": row.get("type")},
            tags=["error"],
        )

    for row in blackboard.table_rows("semantic_merge_reviews", run_id=run_id):
        decision = str(row.get("decision") or "")
        path = Path(str(row.get("path") or ""))
        add(
            kind="review",
            claim=f"Semantic merge review decision: {decision}",
            status="passed" if decision == "accept" else "blocked",
            strength="A",
            artifact_refs=[_artifact_ref(path, producer="semantic_merge")] if path else [],
            metrics={"review_id": row.get("review_id"), "patch_hash": row.get("patch_hash")},
            tags=["semantic_merge"],
        )

    return events


def _loop_trace_events(run_dir: Path) -> list[dict[str, object]]:
    path = run_dir / "trace.jsonl"
    if not path.exists():
        return []
    events: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_type = str(payload.get("type") or "")
        if event_type.startswith("loop_"):
            events.append(payload)
    return events


def _policy_trace_events(run_dir: Path) -> list[dict[str, object]]:
    path = run_dir / "trace.jsonl"
    if not path.exists():
        return []
    events: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if str(payload.get("type") or "") in {"policy_decision", "approval_auto_allowed"}:
            events.append(payload)
    return events


def _build_manifest(run_dir: Path, run_id: str, events: list[EvidenceEvent]) -> EvidenceManifest:
    matrix = _required_matrix(events)
    missing = [key for key, present in matrix.items() if not present]
    layers: dict[str, int] = {}
    kinds: dict[str, int] = {}
    artifacts = set()
    for event in events:
        layers[event.layer] = layers.get(event.layer, 0) + 1
        kinds[event.kind] = kinds.get(event.kind, 0) + 1
        for ref in event.artifact_refs:
            artifacts.add(ref.path)
    return EvidenceManifest(
        run_id=run_id,
        event_count=len(events),
        artifact_count=len(artifacts),
        layers=layers,
        kinds=kinds,
        required_matrix=matrix,
        missing_required=missing,
        head_hash=events[-1].event_hash if events else None,
        events_path=str(run_dir / "evidence" / "events.jsonl"),
        manifest_path=str(run_dir / "evidence" / "manifest.json"),
    )


def _build_evaluation(run_id: str, events: list[EvidenceEvent], manifest: EvidenceManifest) -> EvidenceEvaluation:
    scores = standard_scores(events)
    failed_tests = [event for event in events if event.kind == "test" and event.status == "failed"]
    passed_tests = [event for event in events if event.kind == "test" and event.status == "passed"]
    high_blockers = [
        event
        for event in events
        if event.kind == "review" and event.status == "blocked" and str(event.metrics.get("severity") or "").lower() == "high"
    ]
    blocked_reviews = [event for event in events if event.kind == "review" and event.status == "blocked"]
    errors = [event for event in events if event.kind == "runtime" and event.status == "failed"]
    denied_approvals = [event for event in events if event.kind == "approval" and event.status == "rejected"]
    pending_approvals = [event for event in events if event.kind == "approval" and event.status == "missing"]

    gates = {
        "required_coverage": "pass" if not manifest.missing_required else "fail",
        "verification_strength": "fail" if failed_tests else ("pass" if passed_tests else "missing"),
        "review_security": "fail" if high_blockers or blocked_reviews else "pass",
        "approval_integrity": "fail" if denied_approvals else ("missing" if pending_approvals else "pass"),
        "risk_controls": "fail" if errors else "pass",
        "standard_coverage": "pass" if scores.get("meets_minimum") else "missing",
    }
    reasons: list[str] = []
    missing = list(manifest.missing_required)
    if failed_tests:
        reasons.append("One or more recorded test commands failed.")
        missing.append("failing tests must be resolved")
    if high_blockers:
        reasons.append("High severity review blockers are present.")
        missing.append("high review blocker must be resolved")
    elif blocked_reviews:
        reasons.append("Review or validation blocked the run.")
        missing.append("blocked review evidence must be resolved")
    if errors:
        reasons.append("Runtime errors were recorded.")
        missing.append("runtime errors must be resolved")
    if denied_approvals:
        reasons.append("A required approval was denied.")
        missing.append("denied approval must be resolved")
    if pending_approvals:
        reasons.append("A required approval is still pending.")
        missing.append("pending approval must be decided")
    if not scores.get("meets_minimum"):
        missing.append("E2/E3 verification evidence is required")
    if not reasons:
        reasons.append("Required evidence is present and no blocking findings were recorded.")

    blocked = any(value == "fail" for value in gates.values()) or bool(manifest.missing_required)
    if blocked:
        label = "blocked"
    elif gates["verification_strength"] == "missing" or gates["approval_integrity"] == "missing":
        label = "risky"
    elif any(event.kind == "review" and event.status == "passed" and "validator" in event.tags for event in events):
        label = "ready"
    else:
        label = "reviewable"

    components = {
        "required_coverage": 1.0 if not manifest.missing_required else max(0.0, 1.0 - len(manifest.missing_required) * 0.25),
        "verification_strength": 1.0 if passed_tests and not failed_tests else (0.0 if failed_tests else 0.25),
        "review_security": 0.0 if high_blockers or blocked_reviews else 1.0,
        "traceability_reproducibility": 1.0 if any(event.kind == "change" for event in events) and manifest.head_hash else 0.5,
        "approval_integrity": 0.0 if denied_approvals else (0.5 if pending_approvals else 1.0),
        "risk_controls": 0.0 if errors else 1.0,
        "standard_evidence": 1.0 if scores.get("meets_minimum") else 0.35,
    }
    confidence = round(sum(components.values()) / len(components), 2)
    if label == "blocked":
        confidence = min(confidence, 0.49)
    next_actions = _next_actions(label, missing)
    return EvidenceEvaluation(
        run_id=run_id,
        label=label,  # type: ignore[arg-type]
        confidence=confidence,
        gates=gates,
        components=components,
        standard_scores=scores,
        reasons=reasons,
        missing_evidence=_dedupe(missing),
        next_actions=next_actions,
    )


def _required_matrix(events: list[EvidenceEvent]) -> dict[str, bool]:
    return {
        "task": any(event.kind == "task" for event in events),
        "change": any(event.kind == "change" for event in events),
        "test": any(event.kind == "test" and event.status == "passed" for event in events),
        "review": any(event.kind == "review" and event.status == "passed" for event in events),
    }


def _next_actions(label: str, missing: list[str]) -> list[dict[str, str]]:
    if label == "blocked":
        return [{"kind": "resolve_evidence", "label": item, "command": "muxdev evidence verify"} for item in _dedupe(missing)[:5]]
    if label == "risky":
        return [{"kind": "strengthen_evidence", "label": "Add missing verification or approval evidence", "command": "muxdev evidence latest"}]
    return [{"kind": "review", "label": "Review diff and final report", "command": "muxdev diff latest"}]


def _hash_events(events: list[EvidenceEvent]) -> list[EvidenceEvent]:
    prev_hash: str | None = None
    hashed: list[EvidenceEvent] = []
    for event in events:
        event.prev_hash = prev_hash
        payload = event.model_dump()
        payload.pop("event_hash", None)
        event.event_hash = canonical_hash(payload)
        prev_hash = event.event_hash
        hashed.append(event)
    return hashed


def _verify_event_chain(events: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    prev_hash: str | None = None
    head_hash: str | None = None
    for index, raw in enumerate(events, start=1):
        try:
            event = EvidenceEvent.model_validate(raw)
        except Exception as exc:
            errors.append(f"invalid event at line {index}: {exc}")
            continue
        if event.prev_hash != prev_hash:
            errors.append(f"event prev_hash mismatch: {event.id}")
        expected_payload = event.model_dump()
        expected_payload.pop("event_hash", None)
        expected = canonical_hash(expected_payload)
        if event.event_hash != expected:
            errors.append(f"event hash mismatch: {event.id}")
        prev_hash = event.event_hash
        head_hash = event.event_hash
    return {"valid": not errors, "head_hash": head_hash, "errors": errors}


def _verify_artifact_refs(events: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for raw in events:
        for ref in raw.get("artifact_refs", []) if isinstance(raw.get("artifact_refs"), list) else []:
            if not isinstance(ref, dict):
                continue
            path = Path(str(ref.get("path") or ""))
            expected = ref.get("sha256")
            if not path.exists():
                errors.append(f"missing artifact: {path}")
                continue
            if expected and sha256_file(path) != expected:
                errors.append(f"hash mismatch: {path}")
    return errors


def _verify_persisted_hashes(run_dir: Path, run_id: str, blackboard: Blackboard) -> list[str]:
    errors: list[str] = []
    manifest_rows = blackboard.table_rows("evidence_manifests", run_id=run_id)
    for row in manifest_rows:
        path = Path(str(row.get("path") or run_dir / "evidence" / "manifest.json"))
        if not path.exists():
            errors.append(f"missing artifact: {path}")
        elif row.get("manifest_hash") and sha256_file(path) != row.get("manifest_hash"):
            errors.append(f"hash mismatch: {path}")
    evaluation_rows = blackboard.table_rows("evidence_evaluations", run_id=run_id)
    for row in evaluation_rows:
        path = Path(str(row.get("path") or run_dir / "evidence" / "evaluation.json"))
        if not path.exists():
            errors.append(f"missing artifact: {path}")
        elif row.get("evaluation_hash") and sha256_file(path) != row.get("evaluation_hash"):
            errors.append(f"hash mismatch: {path}")
    return errors


def _artifact_ref(path: Path, *, producer: str) -> ArtifactRef:
    return ArtifactRef(path=str(path), sha256=sha256_file(path) if path.exists() and path.is_file() else None, producer=producer)


def _event_id(run_id: str, index: int, kind: str) -> str:
    safe_kind = "".join(ch if ch.isalnum() else "_" for ch in kind)[:24] or "event"
    return f"ev_{run_id}_{index:04d}_{safe_kind}"


def _status_from_stage(status: str) -> str:
    if status in {"completed", "skipped"}:
        return "passed"
    if status in {"failed", "blocked", "aborted"}:
        return "failed"
    return "observed"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _try_unlink(path: Path) -> bool:
    try:
        path.unlink()
        return True
    except PermissionError:
        return False
