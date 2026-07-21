from __future__ import annotations

from muxdev.models.evidence import (
    ArtifactEvidence,
    CheckEvidence,
    EvidencePolicy,
    EvidenceRequirement,
    InteractionEvidence,
    ReviewEvidence,
    RuntimeEvidence,
)
from muxdev.services.gate import evaluate_gate


SUBJECT = "sha256:subject"


def requirement(identifier: str, kind: str, **values):
    return EvidenceRequirement(id=identifier, description=identifier, accepted_kinds=[kind], **values)


def common_records():
    return [
        ArtifactEvidence(record_id="a", run_id="r", requirement_id="artifact", subject_digest=SUBJECT, producer="runtime", path="x", digest="sha256:x", size=1),
        CheckEvidence(record_id="c", run_id="r", requirement_id="check", subject_digest=SUBJECT, producer="runtime", argv=["pytest"], cwd_digest=SUBJECT, exit_code=0),
        ReviewEvidence(record_id="v", run_id="r", requirement_id="review", subject_digest=SUBJECT, producer="runtime", target_digest=SUBJECT, reviewer="b", executor="a", independent=True),
        RuntimeEvidence(record_id="h", run_id="r", requirement_id="health", subject_digest=SUBJECT, producer="runtime", event_type="health", status="passed"),
    ]


def test_high_score_never_overrides_missing_hard_requirement() -> None:
    policy = EvidencePolicy(policy_id="p", requirements=[
        requirement("artifact", "artifact"), requirement("check", "check", require_reproducible=True),
        requirement("review", "review", require_independent=True), requirement("approval", "interaction"),
        requirement("health", "runtime"),
    ])
    decision = evaluate_gate(policy, common_records())
    assert decision.status == "BLOCKED"
    assert decision.scorecard.overall == 95.0
    assert decision.blockers[0].requirement_id == "approval"


def test_pending_human_is_waiting_and_latest_response_wins() -> None:
    policy = EvidencePolicy(policy_id="p", requirements=[requirement("approval", "interaction")])
    pending = InteractionEvidence(record_id="i1", run_id="r", stage_id="approve", requirement_id="approval", subject_digest=SUBJECT, producer="runtime", interaction_type="approval", decision="pending")
    approved = pending.model_copy(update={"record_id": "i2", "decision": "approved"})
    rejected = pending.model_copy(update={"record_id": "i3", "decision": "rejected", "interaction_type": "rejection"})
    assert evaluate_gate(policy, [pending]).status == "WAITING_HUMAN"
    assert evaluate_gate(policy, [pending, approved]).status == "PASS"
    assert evaluate_gate(policy, [pending, rejected]).status == "BLOCKED"


def test_failed_check_and_self_review_are_hard_blockers() -> None:
    policy = EvidencePolicy(policy_id="p", requirements=[
        requirement("check", "check"), requirement("review", "review", require_independent=True),
    ])
    records = [
        CheckEvidence(record_id="c", run_id="r", requirement_id="check", subject_digest=SUBJECT, producer="runtime", argv=["pytest"], cwd_digest=SUBJECT, exit_code=1),
        ReviewEvidence(record_id="v", run_id="r", requirement_id="review", subject_digest=SUBJECT, producer="runtime", target_digest=SUBJECT, reviewer="same", executor="same", independent=False),
    ]
    decision = evaluate_gate(policy, records)
    assert decision.status == "BLOCKED"
    assert {item.requirement_id for item in decision.blockers} == {"check", "review"}


def test_successful_stage_retry_supersedes_prior_runtime_failure() -> None:
    policy = EvidencePolicy(policy_id="p", requirements=[requirement("health", "runtime")])
    failed = RuntimeEvidence(
        record_id="failed",
        run_id="r",
        stage_id="implement",
        requirement_id="health",
        subject_digest=SUBJECT,
        producer="runtime",
        event_type="policy_or_execution_failure",
        status="failed",
    )
    completed = failed.model_copy(update={
        "record_id": "completed",
        "event_type": "stage_completed",
        "status": "passed",
    })

    decision = evaluate_gate(policy, [failed, completed])

    assert decision.status == "PASS"
    assert decision.requirements[0].record_ids == ["completed"]


def test_runtime_failure_remains_until_the_same_stage_recovers() -> None:
    policy = EvidencePolicy(policy_id="p", requirements=[requirement("health", "runtime")])
    failed = RuntimeEvidence(
        record_id="failed",
        run_id="r",
        stage_id="implement",
        requirement_id="health",
        subject_digest=SUBJECT,
        producer="runtime",
        event_type="policy_or_execution_failure",
        status="failed",
    )
    other_stage_completed = failed.model_copy(update={
        "record_id": "review-completed",
        "stage_id": "review",
        "event_type": "stage_completed",
        "status": "passed",
    })

    decision = evaluate_gate(policy, [failed, other_stage_completed])

    assert decision.status == "BLOCKED"
    assert decision.blockers[0].record_ids == ["failed", "review-completed"]


def test_latest_check_for_the_same_command_supersedes_a_failed_attempt() -> None:
    policy = EvidencePolicy(policy_id="p", requirements=[requirement("check", "check")])
    failed = CheckEvidence(
        record_id="failed",
        run_id="r",
        stage_id="test",
        requirement_id="check",
        subject_digest=SUBJECT,
        producer="runtime",
        argv=["pytest", "-q"],
        cwd=".",
        cwd_digest="sha256:cwd",
        exit_code=1,
    )
    passed = failed.model_copy(update={"record_id": "passed", "exit_code": 0})

    decision = evaluate_gate(policy, [failed, passed])

    assert decision.status == "PASS"
    assert decision.requirements[0].record_ids == ["passed"]


def test_latest_review_supersedes_prior_findings_for_the_same_reviewer() -> None:
    policy = EvidencePolicy(policy_id="p", requirements=[requirement("review", "review")])
    blocked = ReviewEvidence(
        record_id="blocked",
        run_id="r",
        stage_id="review",
        requirement_id="review",
        subject_digest=SUBJECT,
        producer="runtime",
        target_digest=SUBJECT,
        reviewer="reviewer",
        independent=True,
        findings=[{
            "finding_id": "f1",
            "severity": "high",
            "message": "bug",
            "remediation": "fix it",
        }],
    )
    passed = blocked.model_copy(update={"record_id": "passed", "findings": []})

    decision = evaluate_gate(policy, [blocked, passed])

    assert decision.status == "PASS"
    assert decision.requirements[0].record_ids == ["passed"]
