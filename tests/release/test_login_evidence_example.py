from __future__ import annotations

from pathlib import Path

from muxdev.models.evidence import EvidenceReport
from muxdev.services.gate import evaluate_gate


ROOT = Path(__file__).parents[2]


def test_simulated_login_report_is_schema_valid_and_gate_reproducible() -> None:
    path = ROOT / "release-artifacts" / "login-evidence-example.json"
    report = EvidenceReport.model_validate_json(path.read_text(encoding="utf-8"))
    replayed = evaluate_gate(
        report.policy, report.records, subject_digest=str(report.subject["digest"])
    )
    assert report.subject["simulated"] is True
    assert report.decision.status == replayed.status == "PASS"
    assert report.decision.policy_hash == replayed.policy_hash
    assert report.decision.requirements == replayed.requirements
    assert report.decision.blockers == replayed.blockers == []
    assert report.decision.scorecard == replayed.scorecard
    assert {item.requirement_id for item in report.records} == {
        "plan_artifact", "change_artifact", "deterministic_check", "independent_review",
        "security_review", "human_approval", "runtime_health",
    }
