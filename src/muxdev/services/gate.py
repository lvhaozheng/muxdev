"""Deterministic Evidence v3 policy evaluation."""

from __future__ import annotations

from collections.abc import Iterable

from ..models.evidence import (
    AnyEvidenceRecord,
    CheckEvidence,
    EvidencePolicy,
    EvidenceScorecard,
    GateBlocker,
    GateDecision,
    InteractionEvidence,
    RequirementEvaluation,
    ReviewEvidence,
    RuntimeEvidence,
    ScoreDimension,
)


def evaluate_gate(
    policy: EvidencePolicy,
    records: Iterable[AnyEvidenceRecord],
    *,
    subject_digest: str | None = None,
) -> GateDecision:
    evidence = _latest_interactions(list(records))
    evaluations: list[RequirementEvaluation] = []
    blockers: list[GateBlocker] = []
    waiting = False
    for requirement in policy.requirements:
        matched = [item for item in evidence if item.requirement_id == requirement.id and item.kind in requirement.accepted_kinds]
        matched = _subject_records(matched, subject_digest, requirement.subject_selector)
        status, reason, remediation = _evaluate_requirement(requirement, matched, subject_digest)
        evaluations.append(
            RequirementEvaluation(
                requirement_id=requirement.id,
                status=status,
                record_ids=[item.record_id for item in matched],
                reason=reason,
                remediation=remediation,
            )
        )
        if status == "missing" and any(isinstance(item, InteractionEvidence) and item.decision == "pending" for item in matched):
            waiting = True
        if requirement.required and status in {"missing", "failed"}:
            blockers.append(
                GateBlocker(
                    code=f"requirement_{status}",
                    requirement_id=requirement.id,
                    reason=reason,
                    record_ids=[item.record_id for item in matched],
                    remediation=remediation or f"Provide valid evidence for {requirement.id}",
                )
            )
    scoped = [
        record
        for requirement in policy.requirements
        for record in _subject_records(
            [item for item in evidence if item.requirement_id == requirement.id],
            subject_digest,
            requirement.subject_selector,
        )
    ]
    scorecard = _scorecard(policy, scoped, evaluations)
    status = "WAITING_HUMAN" if waiting else "BLOCKED" if blockers else "PASS"
    return GateDecision(
        status=status,
        policy_hash=policy.policy_hash,
        requirements=evaluations,
        blockers=blockers,
        scorecard=scorecard,
    )


def _latest_interactions(records: list[AnyEvidenceRecord]) -> list[AnyEvidenceRecord]:
    """An append-only interaction stream is evaluated by its latest state."""
    latest: dict[tuple[str, str | None], InteractionEvidence] = {}
    other: list[AnyEvidenceRecord] = []
    for record in records:
        if isinstance(record, InteractionEvidence):
            latest[(record.requirement_id, record.stage_id)] = record
        else:
            other.append(record)
    return [*other, *latest.values()]


def _subject_records(
    records: list[AnyEvidenceRecord], subject_digest: str | None, selector: str
) -> list[AnyEvidenceRecord]:
    if not subject_digest or selector == "any":
        return records
    if selector == "run":
        return [item for item in records if item.subject_digest == subject_digest]
    raise ValueError(f"unsupported subject selector: {selector}")


def _evaluate_requirement(
    requirement,
    records: list[AnyEvidenceRecord],
    subject_digest: str | None,
) -> tuple[str, str, str | None]:
    if not records:
        if requirement.required:
            return "missing", "Required evidence is missing.", "Produce the declared evidence and rerun verification."
        return "not_applicable", "Optional evidence was not produced.", None
    checks = [item for item in records if isinstance(item, CheckEvidence)]
    contradictory = [
        item for item in checks
        if item.declared_exit_code is not None
        and (
            item.declared_exit_code != item.exit_code
            or (item.declared_status == "passed") != item.passed
        )
    ]
    if contradictory:
        return (
            "failed",
            "TestResult contradicts the runtime-captured exit code.",
            "Use the observed command result and rerun the test stage.",
        )
    invalid = [item for item in records if not item.integrity_valid]
    if requirement.require_integrity and invalid:
        return "failed", "Evidence integrity verification failed.", "Regenerate evidence from the unchanged subject."
    if any(not item.passed for item in checks):
        return "failed", "A deterministic check failed.", "Fix the failing check and capture a new execution record."
    runtime = [item for item in records if isinstance(item, RuntimeEvidence)]
    if any(item.status == "failed" for item in runtime):
        return "failed", "An unresolved runtime or policy failure remains.", "Resolve the recorded runtime failure and resume the run."
    if any(item.status == "pending" for item in runtime):
        return "missing", "Runtime completion is still pending.", "Resume or cancel the pending run."
    reviews = [item for item in records if isinstance(item, ReviewEvidence)]
    if any(item.target_digest != (subject_digest or item.subject_digest) for item in reviews):
        return "failed", "Review target does not match the delivered subject.", "Review the current subject digest."
    if requirement.require_independent and any(not item.independent for item in reviews):
        return "failed", "Independent review is required.", "Assign a reviewer different from the executor."
    if any(any(finding.severity == "high" and not finding.resolved for finding in item.findings) for item in reviews):
        return "failed", "An unresolved high-severity finding remains.", "Resolve or explicitly waive the finding."
    interactions = [item for item in records if isinstance(item, InteractionEvidence)]
    if any(item.decision == "rejected" for item in interactions):
        return "failed", "Required human interaction was rejected.", "Address the rejection and request a new decision."
    if any(item.decision == "pending" for item in interactions):
        return "missing", "Required human interaction is pending.", "Wait for an explicit human decision."
    if requirement.require_reproducible and checks and any(not item.reproducible for item in checks):
        return "failed", "A reproducible check was required.", "Run the check through the runtime command wrapper."
    return "satisfied", "Requirement is satisfied by verified evidence.", None


def _dimension(numerator: int, denominator: int, failed: list[str]) -> ScoreDimension:
    percent = round(100 * numerator / denominator, 1) if denominator else None
    return ScoreDimension(numerator=numerator, denominator=denominator, percent=percent, failed_requirements=failed)


def _scorecard(policy, records, evaluations) -> EvidenceScorecard:
    required = [item for item in evaluations if next(req for req in policy.requirements if req.id == item.requirement_id).required]
    complete = [item for item in required if item.status == "satisfied"]
    reproducible = [
        item for item in records
        if isinstance(item, CheckEvidence)
        and next(req for req in policy.requirements if req.id == item.requirement_id).require_reproducible
    ]
    integrity = [item for item in records if next(req for req in policy.requirements if req.id == item.requirement_id).require_integrity]
    independent = [item for item in records if isinstance(item, ReviewEvidence) and next(req for req in policy.requirements if req.id == item.requirement_id).require_independent]
    dimensions = {
        "completeness": _dimension(len(complete), len(required), [item.requirement_id for item in required if item.status != "satisfied"]),
        "reproducibility": _dimension(
            sum(item.passed and item.reproducible for item in reproducible),
            len(reproducible),
            [item.requirement_id for item in reproducible if not (item.passed and item.reproducible)],
        ),
        "integrity": _dimension(sum(item.integrity_valid for item in integrity), len(integrity), [item.requirement_id for item in integrity if not item.integrity_valid]),
        "independence": _dimension(sum(item.independent for item in independent), len(independent), [item.requirement_id for item in independent if not item.independent]),
    }
    applicable = [item.percent for item in dimensions.values() if item.percent is not None]
    return EvidenceScorecard(**dimensions, overall=round(sum(applicable) / len(applicable), 1) if applicable else None)
