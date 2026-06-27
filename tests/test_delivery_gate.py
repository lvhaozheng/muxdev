import json
import uuid
import shutil
from pathlib import Path

from muxdev.models import RunStatus
from muxdev.providers.adapters import ProviderStageOutput
from muxdev.runtime.supervisor import SupervisorRuntime
from muxdev.services.delivery_gate import delivery_rule_for_skill_payload, evaluate_delivery_gate, extract_delivery_standard
from muxdev.storage import Blackboard


def test_delivery_standard_extraction_and_rule_hash() -> None:
    content = """# Demo Skill

## Delivery Standard

- Required deliverable: runnable code and verification notes.
- Pass when the changed behavior is verified.
- Block when tests fail or review blockers remain.
- Evidence: stage output, test result, review result, and diff.

## Other
"""
    section = extract_delivery_standard(content)
    rule = delivery_rule_for_skill_payload(
        {
            "name": "demo",
            "role": "code",
            "stage": "implement",
            "content": content,
            "delivery_gate": {"checks": ["no_secret_terms"], "required_evidence": ["stage_output"]},
        }
    )
    changed = delivery_rule_for_skill_payload({"name": "demo", "content": content + "\nextra\n"})

    assert section["required_deliverable"] == "runnable code and verification notes."
    assert rule is not None
    assert "no_failed_tests" in rule["derived_checks"]
    assert "no_open_blockers" in rule["derived_checks"]
    assert "no_secret_terms" in rule["derived_checks"]
    assert rule["delivery_rule_hash"] != changed["delivery_rule_hash"]


def test_evaluate_delivery_gate_blocks_failed_tests_and_review_blockers() -> None:
    workspace = _workspace_temp("delivery-gate")
    run_dir = workspace / "run"
    run_dir.mkdir()
    blackboard = Blackboard(run_dir)
    try:
        blackboard.create_run(
            run_id="run_gate",
            task="gate",
            workflow="custom",
            provider="mock",
            workspace=workspace,
            worktree=workspace / "worktree",
        )
        for stage_id in ("code", "test", "review"):
            artifact = run_dir / f"{stage_id}.md"
            artifact.write_text("ok\n", encoding="utf-8")
            blackboard.add_artifact("run_gate", stage_id, artifact.name, artifact, "stage_output")
        blackboard.add_test_result("run_gate", "test", False, "pytest -q", "pytest failed")
        blackboard.add_review_blocker(
            "run_gate",
            "review",
            type="bug",
            file="app.py",
            line=12,
            severity="high",
            suggestion="fix the regression",
        )

        _, _, payload = evaluate_delivery_gate(
            blackboard,
            run_dir=run_dir,
            run_id="run_gate",
            stage_id="delivery_verify",
            target_stage_ids=["code", "test", "review"],
            skills=[
                {
                    "name": "default-code",
                    "content": "## Delivery Standard\n\n- Required deliverable: code.\n- Pass when tests pass.\n- Block when tests fail or review blockers remain.\n- Evidence: test result and review result.\n",
                }
            ],
        )
    finally:
        blackboard.close()
        shutil.rmtree(workspace, ignore_errors=True)

    blocker_types = {item["type"] for item in payload["blockers"]}
    assert payload["has_blockers"] is True
    assert "test_failure" in blocker_types
    assert "bug" in blocker_types


def test_runtime_delivery_gate_does_not_create_provider_attempt(monkeypatch) -> None:
    workspace = _workspace_temp("delivery-gate-ok")
    workflow = workspace / "gate_ok.yaml"
    workflow.write_text(
        """
name: gate-ok
max_parallel: 1
stages:
  - id: inspect
    role: review
    deps: []
    read_only: true
    output_schema: ReviewResult
  - id: verify
    type: delivery_gate
    role: review
    deps: [inspect]
    read_only: true
    output_schema: ReviewResult
    delivery_targets: [inspect]
""",
        encoding="utf-8",
    )

    class InspectProvider:
        def run_stage(self, *, stage_id: str, task: str, worktree: Path, **_kwargs) -> ProviderStageOutput:
            return ProviderStageOutput("inspect.md", '{"has_blockers": false, "blockers": []}', "inspect ok")

    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", lambda name: InspectProvider())

    try:
        result = SupervisorRuntime(workspace).run("gate ok", provider="mock", workflow_name=str(workflow))

        assert result.status == RunStatus.COMPLETED
        with Blackboard(result.run_dir) as blackboard:
            attempts = blackboard.table_rows("provider_attempts", run_id=result.run_id)
            artifacts = blackboard.table_rows("artifacts", run_id=result.run_id)
        assert [row["stage_id"] for row in attempts] == ["inspect"]
        assert any(row["stage_id"] == "verify" and row["kind"] == "delivery_gate" for row in artifacts)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_runtime_delivery_gate_blocks_failed_test_without_repair(monkeypatch) -> None:
    workspace = _workspace_temp("delivery-gate-failed")
    workflow = workspace / "gate_failed_test.yaml"
    workflow.write_text(
        """
name: gate-failed-test
max_parallel: 1
stages:
  - id: test
    role: test
    deps: []
    allow_shell: true
    output_schema: TestResult
  - id: verify
    type: delivery_gate
    role: review
    deps: [test]
    read_only: true
    output_schema: ReviewResult
    delivery_targets: [test]
""",
        encoding="utf-8",
    )

    class FailingTestProvider:
        def run_stage(self, *, stage_id: str, task: str, worktree: Path, **_kwargs) -> ProviderStageOutput:
            payload = {"passed": False, "command": "pytest -q", "summary": "one test failed"}
            return ProviderStageOutput("test.log", json.dumps(payload), "one test failed")

    monkeypatch.setattr("muxdev.runtime.supervisor.get_runtime_provider", lambda name: FailingTestProvider())

    try:
        result = SupervisorRuntime(workspace).run("gate failed test", provider="mock", workflow_name=str(workflow))

        assert result.status == RunStatus.BLOCKED
        gate_payload = json.loads((result.run_dir / "delivery_gates" / "verify.json").read_text(encoding="utf-8"))
        assert gate_payload["decision"] == "reject"
        assert any(item["type"] == "test_failure" for item in gate_payload["blockers"])
        with Blackboard(result.run_dir) as blackboard:
            errors = blackboard.table_rows("error_details", run_id=result.run_id)
        assert errors[-1]["type"] == "delivery_gate_blockers"
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _workspace_temp(prefix: str) -> Path:
    path = Path(".test_workspaces") / f"{prefix}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()
