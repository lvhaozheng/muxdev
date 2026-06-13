from __future__ import annotations

import json
import os
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from muxdev.api.web import create_app
from muxdev.cli import app
from muxdev.daemon.paths import default_daemon_paths
from muxdev.daemon.tasks import TaskManager
from muxdev.models import ApprovalStatus, RunStatus
from muxdev.runtime import SupervisorRuntime
from muxdev.services.evidence import verify_run_evidence
from muxdev.storage import Blackboard


runner = CliRunner()


def test_run_writes_trusted_delivery_evidence() -> None:
    workspace = _workspace_temp("p1-evidence")
    try:
        result = SupervisorRuntime(workspace).run("trusted delivery smoke", provider="mock")

        assert result.status == RunStatus.COMPLETED
        assert (result.run_dir / "ledger.jsonl").exists()
        assert (result.run_dir / "contracts" / "design.stage_contract.json").exists()
        assert (result.run_dir / "contracts" / "implement.role_result.json").exists()
        assert (result.run_dir / "evidence" / "events.jsonl").exists()
        assert (result.run_dir / "evidence" / "manifest.json").exists()
        assert (result.run_dir / "evidence" / "evaluation.json").exists()
        assert (result.run_dir / "snapshots" / "implement.patch").exists()
        assert (result.run_dir / "validation" / "blind_validator_panel.json").exists()

        with Blackboard(result.run_dir) as blackboard:
            payload = verify_run_evidence(result.run_dir, result.run_id, blackboard)
            assert payload["valid"] is True
            assert payload["events"] > 0
            assert payload["manifest"]["contract_version"] == "muxdev.evidence.v2"
            assert blackboard.table_rows("ledger_events", run_id=result.run_id)
            assert blackboard.table_rows("snapshots", run_id=result.run_id)
            assert blackboard.table_rows("validator_panels", run_id=result.run_id)[0]["decision"] == "accept"
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_approval_subject_drift_requires_new_approval() -> None:
    workspace = _workspace_temp("p1-approval")
    try:
        runtime = SupervisorRuntime(workspace)
        paused = runtime.run("approval integrity smoke", provider="mock", require_approval={"plan"})

        assert paused.status == RunStatus.AWAITING_APPROVAL
        with Blackboard(paused.run_dir) as blackboard:
            original = blackboard.list_approvals(status="pending")[0]
            original_hash = original["subject_hash"]
            plan_artifact = next(
                row
                for row in blackboard.table_rows("artifacts", run_id=paused.run_id)
                if row["stage_id"] == "design" and row["kind"] == "stage_output"
            )
            plan_path = Path(str(plan_artifact["path"]))
            plan_path.write_text(plan_path.read_text(encoding="utf-8") + "\nchanged after approval request\n", encoding="utf-8")
            blackboard.decide_approval(original["approval_id"], ApprovalStatus.APPROVED)

        resumed = runtime.resume(paused.run_id)

        assert resumed.status == RunStatus.AWAITING_APPROVAL
        with Blackboard(paused.run_dir) as blackboard:
            pending = blackboard.list_approvals(status="pending")
            assert len(pending) == 1
            assert pending[0]["type"] == "plan"
            assert pending[0]["subject_hash"] != original_hash
            assert "approval_subject_stale" in (paused.run_dir / "trace.jsonl").read_text(encoding="utf-8")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_cli_evidence_verify_reports_valid_run() -> None:
    workspace = _workspace_temp("p1-cli")
    try:
        with _chdir(workspace):
            result = SupervisorRuntime(workspace).run("cli evidence smoke", provider="mock")
            verified = runner.invoke(app, ["evidence", "verify", result.run_id, "--json"])

        assert verified.exit_code == 0
        payload = json.loads(verified.stdout)
        assert payload["valid"] is True
        assert payload["run_id"] == result.run_id
        assert payload["manifest"]["contract_version"] == "muxdev.evidence.v2"
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_daemon_rollback_to_stage_snapshot() -> None:
    workspace = _workspace_temp("p1-rollback")
    try:
        manager = TaskManager(paths=default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure())
        client = TestClient(create_app(task_manager=manager))
        submitted = client.post(
            "/api/tasks",
            json={"task": "rollback stage snapshot smoke", "workspace": str(workspace), "provider": "mock"},
        ).json()
        task_id = submitted["task_id"]
        _wait_for_status(client, task_id, "completed")

        verified = runner.invoke(app, ["evidence", "verify", task_id, "--json"], env={"MUXDEV_HOME": str(workspace / "home")})
        rollback = client.post(f"/api/tasks/{task_id}/rollback?to_stage=implement").json()

        assert verified.exit_code == 0
        verified_payload = json.loads(verified.stdout)
        assert verified_payload["valid"] is True
        assert verified_payload["events"] > 0
        assert rollback["status"] == "rolled_back"
        assert rollback["to_stage"] == "implement"
        assert rollback["snapshot"].endswith("implement.patch")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def _wait_for_status(client: TestClient, task_id: str, expected: str) -> None:
    import time

    for _ in range(80):
        status = client.get(f"/api/tasks/{task_id}").json()["run"]["status"]
        if status == expected:
            return
        time.sleep(0.1)
    raise AssertionError(f"task did not reach {expected}")


@contextmanager
def _chdir(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _workspace_temp(prefix: str) -> Path:
    path = Path(".test_workspaces") / f"{prefix}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()
