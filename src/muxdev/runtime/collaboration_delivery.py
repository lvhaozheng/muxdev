"""Evidence-backed delivery finalization for Conversation-native collaboration."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from ..core.processes import ProcessSupervisor
from ..models.evidence import (
    ArtifactEvidence,
    CheckEvidence,
    EvidenceReport,
    InteractionEvidence,
    ReviewEvidence,
    ReviewFinding,
    RuntimeEvidence,
    canonical_hash,
)
from ..services.evidence_policy import load_evidence_policy
from ..services.gate import evaluate_gate
from .delivery_standards import (
    custom_items,
    extend_evidence_policy,
    standard_from_contract_policy,
    standard_requirement_id,
)
from .workspace import (
    ChangeSet,
    WorkspaceSnapshot,
    build_change_set,
    diff_text,
    snapshot_workspace,
)


@dataclass(frozen=True)
class DeliveryContext:
    conversation_id: str
    conversation: dict[str, Any]
    contract: dict[str, Any]
    integration: Path
    after: WorkspaceSnapshot
    change_set: ChangeSet
    subject_digest: str
    standard: dict[str, Any]
    policy: Any
    run_id: str
    run_dir: Path
    paths: dict[str, Path]
    artifacts: dict[str, dict[str, Any]]


def finalize_delivery(service: Any, conversation_id: str) -> dict[str, Any]:
    """Create a deterministic Evidence v3 Run over the integrated worktree."""
    context = _prepare_context(service, conversation_id)
    records: list[Any] = []
    requirement_ids = {item.id for item in context.policy.requirements}
    _record_artifacts(service, context, records, requirement_ids)
    check_record = _record_check(service, context, records, requirement_ids)
    review_records = _record_reviews(service, context, records, requirement_ids)
    human_approved = _record_status(service, context, records, requirement_ids)
    _record_custom_standards(
        service,
        context,
        records,
        check_record,
        review_records,
        human_approved,
    )
    decision = evaluate_gate(
        context.policy,
        records,
        subject_digest=context.subject_digest,
    )
    run_status = "completed" if decision.status == "PASS" else (
        "awaiting_approval" if decision.status == "WAITING_HUMAN" else "blocked"
    )
    service.store.update_run(context.run_id, status=run_status)
    report_path = _write_report(service, context, records, decision, review_records)
    return service.conversations._record_result(
        conversation_id,
        context.run_id,
        report_path,
    )


def _prepare_context(service: Any, conversation_id: str) -> DeliveryContext:
    conversation = service._conversation(conversation_id)
    contract = service._active_contract(conversation_id)
    metadata = conversation.get("metadata") if isinstance(conversation.get("metadata"), dict) else {}
    integration = Path(str(metadata.get("worktree") or ""))
    baseline_raw = metadata.get("baseline_manifest")
    if not integration.is_dir() or not isinstance(baseline_raw, dict):
        raise RuntimeError("conversation integration worktree or baseline is missing")
    before = WorkspaceSnapshot.from_dict(baseline_raw)
    after = snapshot_workspace(integration)
    change_set = build_change_set(before, after)
    subject_digest = canonical_hash({"workspace_snapshot": after.digest})
    standard = standard_from_contract_policy(
        str(contract["workflow"]),
        str(contract["profile"]),
        contract.get("policy"),
    )
    policy = extend_evidence_policy(
        load_evidence_policy(
            service.workspace,
            workflow=str(contract["workflow"]),
            profile=str(contract["profile"]),
        ),
        standard,
    )
    run_id = f"collab_{uuid4().hex}"
    run_dir = service.workspace / ".muxdev" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    service.store.create_run(
        run_id=run_id,
        task=str(contract["goal"]),
        workflow=str(contract["workflow"]),
        profile=str(contract["profile"]),
        provider=str(conversation.get("primary_agent_id") or contract["provider"]),
        policy_hash=policy.policy_hash,
        metadata={
            "run_dir": str(run_dir.resolve()),
            "worktree": str(integration.resolve()),
            "defer_apply": True,
            "collaboration_native": True,
            "delivery_context": {
                "conversation_id": conversation_id,
                "contract_id": contract["contract_id"],
                "contract_version": contract["version"],
                "delivery_standard": standard,
            },
        },
    )
    paths = _write_delivery_inputs(service, run_id, run_dir, conversation, contract, change_set, integration)
    artifacts = _register_delivery_artifacts(service, run_id, paths)
    return DeliveryContext(
        conversation_id=conversation_id,
        conversation=conversation,
        contract=contract,
        integration=integration,
        after=after,
        change_set=change_set,
        subject_digest=subject_digest,
        standard=standard,
        policy=policy,
        run_id=run_id,
        run_dir=run_dir,
        paths=paths,
        artifacts=artifacts,
    )


def _write_delivery_inputs(
    service: Any,
    run_id: str,
    run_dir: Path,
    conversation: Mapping[str, Any],
    contract: Mapping[str, Any],
    change_set: ChangeSet,
    integration: Path,
) -> dict[str, Path]:
    paths = {
        "changeset": run_dir / "changeset.json",
        "diff": run_dir / "diff.patch",
        "plan": run_dir / "delivery-plan.json",
    }
    paths["changeset"].write_text(
        json.dumps(change_set.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    paths["diff"].write_text(diff_text(integration), encoding="utf-8")
    active_plan = service.store.get_orchestration_plan(
        str(conversation.get("active_plan_id") or "")
    )
    plan_payload = active_plan.get("plan") if active_plan else {
        "mode": "direct",
        "goal": contract["goal"],
        "acceptance_criteria": contract["acceptance_criteria"],
        "allowed_scope": contract["allowed_scope"],
    }
    paths["plan"].write_text(
        json.dumps(plan_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return paths


def _register_delivery_artifacts(
    service: Any,
    run_id: str,
    paths: Mapping[str, Path],
) -> dict[str, dict[str, Any]]:
    return {
        "changeset": service.store.add_artifact(
            run_id,
            name="changeset.json",
            path=paths["changeset"],
            kind="changeset",
            media_type="application/json",
        ),
        "diff": service.store.add_artifact(
            run_id,
            name="diff.patch",
            path=paths["diff"],
            kind="diff",
            media_type="text/x-diff",
        ),
        "plan": service.store.add_artifact(
            run_id,
            name="delivery-plan.json",
            path=paths["plan"],
            kind="plan",
            media_type="application/json",
        ),
    }


def _append(service: Any, context: DeliveryContext, records: list[Any], record: Any) -> None:
    records.append(record)
    service.store.append_event(
        context.run_id,
        "evidence.record",
        record.model_dump(mode="json"),
        stage_id=record.stage_id,
        event_id=record.record_id,
    )


def _record_artifacts(
    service: Any,
    context: DeliveryContext,
    records: list[Any],
    requirement_ids: set[str],
) -> None:
    definitions = (
        ("plan_artifact", "plan", "plan"),
        ("change_artifact", "changeset", "merge"),
    )
    for requirement_id, key, stage_id in definitions:
        if requirement_id not in requirement_ids:
            continue
        artifact = context.artifacts[key]
        record = ArtifactEvidence(
            record_id=f"evi_{uuid4().hex}",
            run_id=context.run_id,
            stage_id=stage_id,
            requirement_id=requirement_id,
            subject_digest=context.subject_digest,
            producer="muxdev.runtime.collaboration",
            path=str(context.paths[key].resolve()),
            digest=str(artifact["digest"]),
            size=int(artifact["size"]),
            media_type="application/json",
        )
        _append(service, context, records, record)


def _record_check(
    service: Any,
    context: DeliveryContext,
    records: list[Any],
    requirement_ids: set[str],
) -> CheckEvidence:
    check = ProcessSupervisor().run(
        ["git", "diff", "--check", "--", "."],
        cwd=context.integration,
        run_id=context.run_id,
        stage_id="verification",
        timeout_seconds=120,
    )
    observed_digest = canonical_hash(
        {"workspace_snapshot": snapshot_workspace(context.integration).digest}
    )
    record = CheckEvidence(
        record_id=f"evi_{uuid4().hex}",
        run_id=context.run_id,
        stage_id="verification",
        requirement_id="deterministic_check",
        subject_digest=context.subject_digest,
        producer="muxdev.runtime.process-supervisor",
        argv=list(check.argv),
        cwd=".",
        cwd_digest=context.after.digest,
        exit_code=check.returncode,
        declared_exit_code=check.returncode,
        declared_status="passed" if check.returncode == 0 else "failed",
        duration_ms=check.duration_ms,
        stdout_digest=_text_digest(check.stdout),
        stderr_digest=_text_digest(check.stderr),
        stdout_summary=check.stdout[-2000:],
        stderr_summary=check.stderr[-2000:],
        summary="Runtime executed frozen git diff integrity check.",
        reproducible=True,
        integrity_valid=observed_digest == context.subject_digest,
    )
    if "deterministic_check" in requirement_ids:
        _append(service, context, records, record)
    return record


def _record_reviews(
    service: Any,
    context: DeliveryContext,
    records: list[Any],
    requirement_ids: set[str],
) -> dict[str, ReviewEvidence]:
    executor = str(
        context.conversation.get("primary_agent_id") or context.contract["provider"]
    )
    review_records: dict[str, ReviewEvidence] = {}
    definitions = (
        ("review", "review", "review"),
        ("independent_review", "review", "review"),
        ("security_review", "security-review", "security_review"),
    )
    assignments = service.store.list_assignments(context.conversation_id)
    for requirement_id, dispatch_kind, stage_id in definitions:
        if requirement_id not in requirement_ids:
            continue
        assignment = next(
            (
                item for item in reversed(assignments)
                if item["dispatch_kind"] == dispatch_kind and item["status"] == "completed"
            ),
            None,
        )
        if not assignment:
            continue
        report = (assignment.get("metadata") or {}).get("report") or {}
        findings = _review_findings(report)
        if str(report.get("status") or "failed") not in {"passed", "satisfied"}:
            findings.append(ReviewFinding(
                finding_id=f"finding_{uuid4().hex}",
                category="delivery",
                severity="high",
                message=str(report.get("summary") or "Reviewer blocked delivery."),
                remediation="Resolve the reviewer blocker and request another independent review.",
                resolved=False,
            ))
        record = ReviewEvidence(
            record_id=f"evi_{uuid4().hex}",
            run_id=context.run_id,
            stage_id=stage_id,
            requirement_id=requirement_id,
            subject_digest=context.subject_digest,
            producer="muxdev.runtime.collaboration",
            target_digest=context.subject_digest,
            reviewer=str(assignment["agent_id"]),
            executor=executor,
            independent=str(assignment["agent_id"]) != executor,
            findings=findings,
            residual_risk=str(report.get("residual_risk") or ""),
        )
        _append(service, context, records, record)
        review_records[dispatch_kind] = record
    return review_records


def _record_status(
    service: Any,
    context: DeliveryContext,
    records: list[Any],
    requirement_ids: set[str],
) -> bool:
    human_approved = service._human_verification_approved(context.conversation_id)
    if "human_approval" in requirement_ids:
        _append(service, context, records, InteractionEvidence(
            record_id=f"evi_{uuid4().hex}",
            run_id=context.run_id,
            stage_id="approval",
            requirement_id="human_approval",
            subject_digest=context.subject_digest,
            producer="muxdev.runtime.collaboration",
            interaction_type="approval",
            decision="approved" if human_approved else "pending",
            actor="developer" if human_approved else None,
        ))
    blocked = [
        item["assignment_id"]
        for item in service.store.list_assignments(context.conversation_id)
        if item["status"] in {"blocked", "failed"}
    ]
    if "runtime_health" in requirement_ids:
        _append(service, context, records, RuntimeEvidence(
            record_id=f"evi_{uuid4().hex}",
            run_id=context.run_id,
            requirement_id="runtime_health",
            subject_digest=context.subject_digest,
            producer="muxdev.runtime.collaboration",
            event_type="collaboration_health",
            status="failed" if blocked else "passed",
            details={"blocked_assignments": blocked},
        ))
    return human_approved


def _record_custom_standards(
    service: Any,
    context: DeliveryContext,
    records: list[Any],
    check_record: CheckEvidence,
    review_records: Mapping[str, ReviewEvidence],
    human_approved: bool,
) -> None:
    for item in custom_items(context.standard):
        verifier = item.get("verifier") if isinstance(item.get("verifier"), dict) else {}
        verifier_type = str(verifier.get("type") or "agent_review")
        status, sources = _custom_standard_sources(
            service,
            context,
            item,
            verifier,
            verifier_type,
            check_record,
            review_records,
            human_approved,
        )
        _append(service, context, records, RuntimeEvidence(
            record_id=f"evi_{uuid4().hex}",
            run_id=context.run_id,
            stage_id=str(item.get("stage_id") or "") or None,
            requirement_id=standard_requirement_id(str(item["id"])),
            subject_digest=context.subject_digest,
            producer="muxdev.runtime.delivery-standard",
            event_type="delivery_standard_assessment",
            status=status,
            details={
                "standard_id": item["id"],
                "deliverable": item.get("deliverable"),
                "completion": item.get("completion"),
                "proof": item.get("proof"),
                "source_record_ids": sources,
            },
        ))


def _custom_standard_sources(
    service: Any,
    context: DeliveryContext,
    item: Mapping[str, Any],
    verifier: Mapping[str, Any],
    verifier_type: str,
    check_record: CheckEvidence,
    review_records: Mapping[str, ReviewEvidence],
    human_approved: bool,
) -> tuple[str, list[str]]:
    status = "passed"
    sources: list[str] = []
    if item.get("assignment_id"):
        bound = service.store.get_assignment(str(item["assignment_id"]))
        if (
            not bound
            or bound["conversation_id"] != context.conversation_id
            or bound["status"] != "completed"
        ):
            status = "pending"
        elif bound.get("changeset_digest"):
            sources.append(str(bound["changeset_digest"]))
    if verifier_type in {"check", "runtime_check"}:
        passed = check_record.exit_code == 0 and check_record.integrity_valid
        status = "passed" if status == "passed" and passed else "failed"
        sources.append(check_record.record_id)
    elif verifier_type in {"review", "agent_review"}:
        review_kind = (
            "security-review"
            if verifier.get("capability") == "security-review"
            or item.get("stage_id") == "security_review"
            else "review"
        )
        review = review_records.get(review_kind)
        if not review:
            status = "pending"
        elif any(finding.severity == "high" and not finding.resolved for finding in review.findings):
            status = "failed"
        else:
            sources.append(review.record_id)
    elif verifier_type == "human_acceptance":
        status = "passed" if human_approved else "pending"
    elif verifier_type == "artifact":
        sources.append(str(context.artifacts["changeset"]["digest"]))
    return status, sources


def _write_report(
    service: Any,
    context: DeliveryContext,
    records: list[Any],
    decision: Any,
    review_records: Mapping[str, ReviewEvidence],
) -> Path:
    chain_valid, chain_errors = service.store.verify_event_chain(context.run_id)
    chain = service.store.events(context.run_id)
    head = chain[-1] if chain else {}
    executor = str(
        context.conversation.get("primary_agent_id") or context.contract["provider"]
    )
    report = EvidenceReport(
        run_id=context.run_id,
        subject={
            "name": context.conversation_id,
            "kind": "workspace_snapshot",
            "digest": context.subject_digest,
            "artifacts": [
                {
                    "name": context.artifacts[key]["name"],
                    "digest": context.artifacts[key]["digest"],
                }
                for key in ("changeset", "diff", "plan")
            ],
        },
        policy=context.policy,
        records=records,
        decision=decision,
        integrity={
            "valid": chain_valid,
            "event_chain_errors": chain_errors,
            "event_chain_sequence": head.get("sequence", 0),
            "event_chain_head": head.get("event_hash"),
            "records_hash": canonical_hash(
                [item.model_dump(mode="json") for item in records]
            ),
        },
        routing={"mode": context.conversation["mode"], "main_provider": executor},
        reviewer={key: value.reviewer for key, value in review_records.items()},
        harness={
            "changeset": context.change_set.to_dict(),
            "integration_worktree": str(context.integration.resolve()),
            "assignments": [
                item["assignment_id"]
                for item in service.store.list_assignments(context.conversation_id)
            ],
        },
    )
    report_path = context.run_dir / "evidence-report.json"
    report_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    service.store.add_artifact(
        context.run_id,
        name="evidence-report.json",
        path=report_path,
        kind="evidence_report",
        media_type="application/json",
    )
    return report_path


def _text_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _review_findings(report: Mapping[str, object]) -> list[ReviewFinding]:
    raw = report.get("findings") if isinstance(report.get("findings"), list) else []
    findings: list[ReviewFinding] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        findings.append(ReviewFinding(
            finding_id=str(item.get("finding_id") or item.get("id") or f"finding_{uuid4().hex}"),
            category=str(item.get("category") or "correctness"),
            severity=str(item.get("severity") or "medium"),
            message=str(item.get("message") or "Review finding"),
            file=str(item["file"]) if item.get("file") else None,
            line=int(item["line"]) if item.get("line") is not None else None,
            remediation=str(item.get("remediation") or "Address the finding."),
            resolved=bool(item.get("resolved")),
        ))
    return findings


__all__ = ["finalize_delivery"]
