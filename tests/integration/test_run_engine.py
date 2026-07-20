from __future__ import annotations

import json
import sys
from pathlib import Path

from muxdev.domain import StageExecutionResult
from muxdev.providers.mock import MockProvider
from muxdev.runtime import RunEngine
from muxdev.services.evidence_verify import verify_evidence_report


def test_lite_run_passes_and_tampering_is_detected(workspace: Path) -> None:
    engine = RunEngine(workspace)
    result = engine.run("add a deterministic marker", provider="mock", profile="lite")
    assert str(result.status) == "completed"
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["decision"]["status"] == "PASS"
    assert set(report["decision"]["scorecard"]) == {"completeness", "reproducibility", "integrity", "independence", "overall"}
    assert (workspace / "muxdev_mock_change.txt").is_file()
    assert verify_evidence_report(result.report_path, store=engine.store)["valid"]
    (result.run_dir / "diff.patch").write_text("tampered", encoding="utf-8")
    assert not verify_evidence_report(result.report_path, store=engine.store)["valid"]
    engine.store.close()


def test_standard_mock_is_blocked_by_independence(workspace: Path) -> None:
    engine = RunEngine(workspace)
    result = engine.run("change safely", provider="mock", profile="standard")
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert str(result.status) == "blocked"
    assert report["decision"]["status"] == "BLOCKED"
    assert "independent_review" in {item["requirement_id"] for item in report["decision"]["blockers"]}
    assert not (workspace / "muxdev_mock_change.txt").exists()
    engine.store.close()


def test_strict_run_pauses_for_human(workspace: Path) -> None:
    engine = RunEngine(workspace)
    result = engine.run("strict change", provider="mock", profile="strict")
    assert str(result.status) == "awaiting_approval"
    frozen_hash = json.loads(result.report_path.read_text(encoding="utf-8"))["policy"]["policy_hash"]
    (workspace / "evidence-policy.yaml").write_text(
        "evidence_policies:\n  policies:\n    change:\n      strict: [runtime_health]\n",
        encoding="utf-8",
    )
    interactions = engine.store.interactions(result.run_id, pending_only=True)
    assert len(interactions) == 1
    engine.store.respond(interactions[0]["interaction_id"], status="approved")
    resumed = engine.resume(result.run_id)
    assert str(resumed.status) == "blocked"  # human approval cannot waive self-review
    assert json.loads(resumed.report_path.read_text(encoding="utf-8"))["policy"]["policy_hash"] == frozen_hash
    engine.store.close()


def test_runtime_exit_code_overrides_forged_test_claim(workspace: Path, monkeypatch) -> None:
    class ForgedTestProvider:
        def execute(self, stage_input):
            if stage_input.stage_id != "test":
                return MockProvider().execute(stage_input)
            payload = {
                "checks": [{
                    "id": "forged-pass",
                    "argv": [sys.executable, "-c", "raise SystemExit(3)"],
                    "status": "passed",
                    "exit_code": 0,
                    "summary": "provider claimed pass",
                }],
            }
            return StageExecutionResult(
                artifact_name="test.json",
                content=json.dumps(payload),
                summary="provider claimed pass",
                stage_id="test",
                provider="mock",
            )

    monkeypatch.setattr("muxdev.runtime.engine.get_runtime_provider", lambda *_args, **_kwargs: ForgedTestProvider())
    engine = RunEngine(workspace)
    result = engine.run("attempt a forged test pass", provider="mock", profile="lite")
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    checks = [item for item in report["records"] if item["kind"] == "check"]
    assert str(result.status) == "blocked"
    assert checks[0]["exit_code"] == 3
    assert checks[0]["declared_exit_code"] == 0
    assert checks[0]["integrity_valid"] is False
    blockers = [item for item in report["decision"]["blockers"] if item["requirement_id"] == "deterministic_check"]
    assert "runtime-captured exit code" in blockers[0]["reason"]
    engine.store.close()


def test_event_chain_tampering_is_detected(workspace: Path) -> None:
    engine = RunEngine(workspace)
    result = engine.run("produce chain-bound evidence", provider="mock", profile="lite")
    engine.store.connection.execute(
        "UPDATE events SET event_hash = ? WHERE run_id = ? AND sequence = 1",
        ("sha256:" + "0" * 64, result.run_id),
    )
    engine.store.connection.commit()
    verification = verify_evidence_report(result.report_path, store=engine.store)
    assert not verification["valid"]
    assert any("event" in error or "chain" in error for error in verification["errors"])
    engine.store.close()
