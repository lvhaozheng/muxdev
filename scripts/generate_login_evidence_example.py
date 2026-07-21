"""Generate a schema-valid, simulated login delivery evidence report."""

from __future__ import annotations

import json
from pathlib import Path

from muxdev.models.evidence import (
    ArtifactEvidence,
    CheckEvidence,
    EvidencePolicy,
    EvidenceReport,
    EvidenceRequirement,
    InteractionEvidence,
    ReviewEvidence,
    RuntimeEvidence,
    canonical_hash,
)
from muxdev.services.gate import evaluate_gate


ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "release-artifacts" / "login-evidence-example.json"
CREATED_AT = "2026-07-20T10:30:00+08:00"
RUN_ID = "run_login_demo_001"
SUBJECT = canonical_hash({"diff": "POST /api/login with Argon2id verification and signed access token"})


def main() -> None:
    policy = _policy()
    records = _records()
    decision = evaluate_gate(policy, records, subject_digest=SUBJECT)
    report = EvidenceReport(
        run_id=RUN_ID,
        subject={
            "name": "login-api-change",
            "digest": SUBJECT,
            "simulated": True,
            "artifacts": [{"name": "diff.patch", "digest": canonical_hash("login diff bytes")}],
        },
        policy=policy,
        records=records,
        decision=decision,
        integrity={
            "valid": True,
            "event_chain_errors": [],
            "event_chain_sequence": 18,
            "event_chain_head": canonical_hash("login-demo-event-chain-head"),
            "records_hash": canonical_hash([item.model_dump(mode="json") for item in records]),
        },
        routing={
            "main_provider": "codex",
            "reviewer_provider": "claude-code",
            "security_provider": "qwen",
            "reason": "capability_and_verified_history",
        },
        reviewer={"executor": "codex", "reviewer": "claude-code", "security_reviewer": "qwen"},
        generated_at=CREATED_AT,
    )
    payload = report.model_dump(mode="json")
    payload["decision"]["evaluated_at"] = CREATED_AT
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _policy() -> EvidencePolicy:
    definitions = [
        ("plan_artifact", "artifact", "A structured login implementation plan exists.", False, False, "any"),
        ("change_artifact", "artifact", "The login diff is content-addressed.", False, False, "run"),
        ("deterministic_check", "check", "Login tests are replayed by muxdev Runtime.", True, False, "run"),
        ("independent_review", "review", "A different provider reviews the final login subject.", False, True, "run"),
        ("security_review", "review", "A security worker reviews authentication risks.", False, True, "run"),
        ("human_approval", "interaction", "A human approves the strict delivery.", False, False, "any"),
        ("runtime_health", "runtime", "The runtime completes without unresolved failures.", False, False, "any"),
    ]
    return EvidencePolicy(
        policy_id="muxdev.change.strict.login-demo",
        version=3,
        requirements=[
            EvidenceRequirement(
                id=identifier,
                description=description,
                accepted_kinds=[kind],
                require_reproducible=reproducible,
                require_independent=independent,
                subject_selector=selector,
            )
            for identifier, kind, description, reproducible, independent, selector in definitions
        ],
    )


def _records():
    common = {"run_id": RUN_ID, "subject_digest": SUBJECT, "created_at": CREATED_AT}
    return [
        ArtifactEvidence(
            record_id="ev_plan_login_001", stage_id="plan", requirement_id="plan_artifact",
            producer="muxdev.runtime", path="artifacts/plan/plan.json",
            digest=canonical_hash("login plan bytes"), size=1842, media_type="application/json", **common,
        ),
        ArtifactEvidence(
            record_id="ev_diff_login_001", requirement_id="change_artifact",
            producer="muxdev.runtime", path="diff.patch", digest=canonical_hash("login diff bytes"),
            size=4268, media_type="text/x-diff", **common,
        ),
        CheckEvidence(
            record_id="ev_check_login_success", stage_id="test", requirement_id="deterministic_check",
            producer="muxdev.runtime.command",
            argv=["python", "-m", "pytest", "tests/test_auth.py::test_login_success", "-q"],
            cwd=".", cwd_digest=canonical_hash("login-worktree"), exit_code=0,
            declared_exit_code=0, declared_status="passed", duration_ms=842,
            stdout_digest=canonical_hash("1 passed in 0.42s"), stderr_digest=canonical_hash(""),
            stdout_summary="1 passed in 0.42s", summary="Valid credentials return a signed access token.", **common,
        ),
        CheckEvidence(
            record_id="ev_check_login_failure", stage_id="test", requirement_id="deterministic_check",
            producer="muxdev.runtime.command",
            argv=["python", "-m", "pytest", "tests/test_auth.py::test_login_wrong_password", "-q"],
            cwd=".", cwd_digest=canonical_hash("login-worktree"), exit_code=0,
            declared_exit_code=0, declared_status="passed", duration_ms=799,
            stdout_digest=canonical_hash("1 passed in 0.39s"), stderr_digest=canonical_hash(""),
            stdout_summary="1 passed in 0.39s", summary="Wrong passwords return the same generic 401 response.", **common,
        ),
        ReviewEvidence(
            record_id="ev_review_login_001", stage_id="review", requirement_id="independent_review",
            producer="muxdev.runtime.review", target_digest=SUBJECT, reviewer="claude-code", executor="codex",
            independent=True, findings=[], residual_risk="Refresh-token rotation is outside this change.", **common,
        ),
        ReviewEvidence(
            record_id="ev_security_login_001", stage_id="security_review", requirement_id="security_review",
            producer="muxdev.runtime.review", target_digest=SUBJECT, reviewer="qwen", executor="codex",
            independent=True, findings=[], residual_risk="Production rate-limit tuning requires traffic data.", **common,
        ),
        InteractionEvidence(
            record_id="ev_approval_login_001", stage_id="approve_plan", requirement_id="human_approval",
            producer="muxdev.runtime.interaction", interaction_type="approval", decision="approved", actor="developer", **common,
        ),
        RuntimeEvidence(
            record_id="ev_runtime_login_001", stage_id="finalize", requirement_id="runtime_health",
            producer="muxdev.runtime", event_type="run_health", status="passed",
            details={
                "context_pack_digest": canonical_hash("login context pack"),
                "fanout": ["review", "security_review"],
                "read_only_subject_unchanged": True,
            },
            **common,
        ),
    ]


if __name__ == "__main__":
    main()
