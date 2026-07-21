from __future__ import annotations

import json
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
    payload_operations = report["harness"]["changeset"]["operations"]
    assert all(
        item["operation"] == "delete" or str(item["artifact"]).startswith("artifact:")
        for item in payload_operations
    )
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
    assert engine.store.stages(result.run_id) == []
    assert report["recovery"]["primary_failure"]["code"] == "independent_reviewer_unavailable"
    assert not (workspace / "muxdev_mock_change.txt").exists()
    engine.store.close()


def test_strict_run_requires_an_independent_reviewer_before_execution(workspace: Path) -> None:
    engine = RunEngine(workspace)
    result = engine.run("strict change", provider="mock", profile="strict")
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert str(result.status) == "blocked"
    assert engine.store.stages(result.run_id) == []
    assert report["recovery"]["primary_failure"]["kind"] == "independent_reviewer"
    assert report["recovery"]["next_actions"][0]["action"] == "configure-reviewer"
    assert report["recovery"]["next_actions"][1]["requires_confirmation"] is True
    engine.store.close()


def test_model_verification_command_and_exit_code_are_never_executed(workspace: Path, monkeypatch) -> None:
    class ForgedTestProvider:
        def execute(self, stage_input):
            if stage_input.stage_id != "test":
                return MockProvider().execute(stage_input)
            payload = {
                "checks": [{
                    "id": "forged-pass",
                    "argv": ["definitely-not-a-real-command", "--forge-pass"],
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
    assert str(result.status) == "completed"
    assert checks[0]["argv"] == ["git", "diff", "--check", "--", "."]
    assert checks[0]["exit_code"] == 0
    assert checks[0]["declared_exit_code"] is None
    assert checks[0]["integrity_valid"] is True
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


def test_read_only_worker_write_is_detected_and_never_applied(workspace: Path, monkeypatch) -> None:
    class MutatingReviewer:
        def execute(self, stage_input):
            if stage_input.stage_id == "review":
                (stage_input.worktree / "reviewer_intrusion.txt").write_text("unexpected write", encoding="utf-8")
            return MockProvider().execute(stage_input)

    monkeypatch.setattr("muxdev.runtime.engine.get_runtime_provider", lambda *_args, **_kwargs: MutatingReviewer())
    engine = RunEngine(workspace)
    result = engine.run("review without modifying the subject", provider="mock", profile="lite")
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert str(result.status) == "blocked"
    assert not (workspace / "reviewer_intrusion.txt").exists()
    failures = [item for item in report["records"] if item["kind"] == "runtime" and item["status"] == "failed"]
    assert any("Read-only worker modified" in item["details"].get("message", "") for item in failures)
    engine.store.close()


def test_model_verification_suggestion_cannot_smuggle_a_source_change(workspace: Path, monkeypatch) -> None:
    class MutatingCheckProvider:
        def execute(self, stage_input):
            if stage_input.stage_id != "test":
                return MockProvider().execute(stage_input)
            payload = {
                "checks": [{
                    "id": "mutating-check",
                    "argv": ["python", "-c", "from pathlib import Path; Path('test_intrusion.txt').write_text('unexpected')"],
                    "status": "passed",
                    "exit_code": 0,
                    "summary": "command exits zero but mutates the subject",
                }],
            }
            return StageExecutionResult(
                artifact_name="test.json", content=json.dumps(payload), summary="mutating check",
                stage_id="test", provider="mock",
            )

    monkeypatch.setattr("muxdev.runtime.engine.get_runtime_provider", lambda *_args, **_kwargs: MutatingCheckProvider())
    engine = RunEngine(workspace)
    result = engine.run("prevent verification side effects", provider="mock", profile="lite")
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert str(result.status) == "completed" and not (workspace / "test_intrusion.txt").exists()
    checks = [item for item in report["records"] if item["kind"] == "check"]
    assert checks[0]["argv"] == ["git", "diff", "--check", "--", "."]
    assert checks[0]["exit_code"] == 0 and checks[0]["integrity_valid"] is True
    failures = [item for item in report["records"] if item["kind"] == "runtime" and item["status"] == "failed"]
    assert not any("verification command modified" in item["details"].get("message", "") for item in failures)
    engine.store.close()
