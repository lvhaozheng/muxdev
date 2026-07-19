from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from muxdev.api.web import create_app
from muxdev.core.canonical import CanonicalJSONError, canonical_json_bytes
from muxdev.domain import RunSpec
from muxdev.daemon.paths import default_daemon_paths
from muxdev.daemon.tasks import TaskManager
from muxdev.models import RunStatus
from muxdev.services.attestation import AttestationRequiredError, DeliveryAttestationService, verify_attestation_record
from muxdev.services.attestation_bundle import export_attestation_bundle, verify_attestation_bundle
from muxdev.services.evidence import verify_run_evidence, write_evidence_run
from muxdev.services.trust import KeyMaterialMissingError, ProjectSigningKeyStore
from muxdev.storage import Blackboard
from muxdev.storage.executions import DurableExecutionQueue


@pytest.fixture
def workspace() -> Path:
    root = Path(".test_workspaces") / f"v02_attestation_{uuid4().hex}"
    root.mkdir(parents=True)
    (root / ".muxdev").mkdir()
    try:
        yield root.resolve()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _ready_run(workspace: Path, *, run_id: str = "run_signed", task: str = "fix service") -> tuple[Blackboard, Path, Path]:
    run_dir = workspace / ".muxdev" / "runs" / run_id
    run_dir.mkdir(parents=True)
    board = Blackboard(run_dir)
    board.create_run(
        run_id=run_id,
        task=task,
        workflow="software-dev",
        provider="mock",
        workspace=workspace,
        worktree=workspace,
    )
    spec = RunSpec.from_submit_payload(
        run_id=run_id,
        task=task,
        workspace=workspace,
        provider="mock",
        workflow="software-dev",
    )
    with board.unit_of_work():
        DurableExecutionQueue(board).persist_run_spec(spec)
    board.set_run_status(run_id, RunStatus.RUNNING)
    board.upsert_stage(run_id, "review", role="review", status="completed", summary="clean")
    board.add_test_result(run_id, "test", True, "python -m pytest", "all tests passed")
    (run_dir / "task.md").write_text(task, encoding="utf-8")
    diff = run_dir / "diff.patch"
    diff.write_text("diff --git a/a.py b/a.py\n+safe = True\n", encoding="utf-8")
    report = run_dir / "final_report.md"
    report.write_text("# local report\n", encoding="utf-8")
    board.add_artifact(run_id, None, "diff.patch", diff, "diff")
    write_evidence_run(run_dir, run_id, board)
    assert verify_run_evidence(run_dir, run_id, board)["valid"] is True
    return board, run_dir, report


def _finalize(board: Blackboard, workspace: Path, run_dir: Path, report: Path, *, run_id: str = "run_signed") -> dict[str, object]:
    return DeliveryAttestationService(
        board,
        run_dir=run_dir,
        workspace=workspace,
        muxdev_home=workspace / "home",
    ).finalize(
        run_id=run_id,
        task="fix service",
        workflow="software-dev",
        diff_path=run_dir / "diff.patch",
        report_path=report,
    )


def test_canonical_json_normalizes_unicode_and_rejects_non_finite() -> None:
    assert canonical_json_bytes({"b": "e\u0301", "a": 1}) == '{"a":1,"b":"é"}'.encode()
    with pytest.raises(CanonicalJSONError):
        canonical_json_bytes({"bad": float("nan")})


def test_project_identity_is_stable_and_private_key_is_outside_project(workspace: Path) -> None:
    store = ProjectSigningKeyStore(workspace, muxdev_home=workspace / "private-home")
    first = store.initialize()
    second = store.initialize()
    assert first.project_id == second.project_id
    assert first.fingerprint == second.fingerprint
    assert (workspace / ".muxdev" / "trust" / "project-id").is_file()
    assert not list((workspace / ".muxdev").rglob("*.key.pem"))
    assert list((workspace / "private-home" / "data" / "signing").rglob("*.key.pem"))


def test_missing_private_key_is_not_silently_replaced(workspace: Path) -> None:
    store = ProjectSigningKeyStore(workspace, muxdev_home=workspace / "private-home")
    key = store.initialize()
    private = next((workspace / "private-home" / "data" / "signing").rglob("*.key.pem"))
    private.unlink()
    with pytest.raises(KeyMaterialMissingError):
        store.initialize()
    assert store.status()["public_key_fingerprint"] == key.fingerprint


def test_rotation_double_signs_and_preserves_old_public_identity(workspace: Path) -> None:
    store = ProjectSigningKeyStore(workspace, muxdev_home=workspace / "private-home")
    old = store.initialize()
    rotation = store.rotate(reason="scheduled rotation", confirmed=True)
    new = store.load()
    assert old.fingerprint != new.fingerprint
    assert rotation["old_fingerprint"] == old.fingerprint
    assert rotation["new_fingerprint"] == new.fingerprint
    identity = json.loads((workspace / ".muxdev" / "trust" / "identity.json").read_text(encoding="utf-8"))
    assert old.key_id in identity["public_keys"]
    assert not (workspace / "private-home" / "data" / "signing" / "projects" / old.project_id / f"{old.key_id}.key.pem").exists()


def test_atomic_completion_creates_signed_record_and_materialization(workspace: Path) -> None:
    board, run_dir, report = _ready_run(workspace)
    try:
        record = _finalize(board, workspace, run_dir, report)
        assert board.get_run("run_signed")["status"] == "completed"
        assert record["status"] == "signed"
        assert record["evidence_valid"] is True
        assert (run_dir / "attestation" / "attestation.json").is_file()
        verified = verify_attestation_record(record["payload"], record["signature"], evidence_valid=True)
        assert verified["valid"] is True
        assert verified["identity_status"] == "self_asserted"
    finally:
        board.close()


def test_completed_event_and_attestation_roll_back_together(workspace: Path) -> None:
    board, run_dir, report = _ready_run(workspace)
    try:
        service = DeliveryAttestationService(
            board, run_dir=run_dir, workspace=workspace, muxdev_home=workspace / "home"
        )
        with pytest.raises(RuntimeError, match="injected"):
            service.finalize(
                run_id="run_signed", task="fix service", workflow="software-dev",
                diff_path=run_dir / "diff.patch", report_path=report,
                inject_failure="after_completed_event",
            )
        assert board.get_run("run_signed")["status"] == "attesting"
        assert board.latest_delivery_attestation("run_signed") is None
        assert not any(
            event.event_type == "run.transitioned" and event.payload.get("to_status") == "completed"
            for event in board.list_state_events("run_signed")
        )
    finally:
        board.close()


def test_finalize_is_idempotent(workspace: Path) -> None:
    board, run_dir, report = _ready_run(workspace)
    try:
        first = _finalize(board, workspace, run_dir, report)
        second = _finalize(board, workspace, run_dir, report)
        assert first["attestation_id"] == second["attestation_id"]
        assert len(board.list_delivery_attestations("run_signed")) == 1
    finally:
        board.close()


def test_ordinary_signing_failure_completes_unsigned(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    board, run_dir, report = _ready_run(workspace)
    try:
        service = DeliveryAttestationService(
            board, run_dir=run_dir, workspace=workspace, muxdev_home=workspace / "home"
        )

        def fail(*_: object, **__: object) -> object:
            raise KeyMaterialMissingError("injected missing key")

        monkeypatch.setattr(service.key_store, "get_or_create", fail)
        record = service.finalize(
            run_id="run_signed", task="fix service", workflow="software-dev",
            diff_path=run_dir / "diff.patch", report_path=report,
        )
        assert board.get_run("run_signed")["status"] == "completed"
        assert record["status"] == "unsigned"
        assert record["identity_status"] == "unsigned"
    finally:
        board.close()


def test_high_risk_signing_failure_blocks_completion(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    board, run_dir, report = _ready_run(workspace, task="change authentication security")
    try:
        service = DeliveryAttestationService(
            board, run_dir=run_dir, workspace=workspace, muxdev_home=workspace / "home"
        )

        def fail(*_: object, **__: object) -> object:
            raise KeyMaterialMissingError("injected missing key")

        monkeypatch.setattr(service.key_store, "get_or_create", fail)
        with pytest.raises(AttestationRequiredError, match="missing key"):
            service.finalize(
                run_id="run_signed", task="change authentication security", workflow="software-dev",
                diff_path=run_dir / "diff.patch", report_path=report,
            )
        assert board.get_run("run_signed")["status"] == "blocked"
        assert board.latest_delivery_attestation("run_signed") is None
    finally:
        board.close()


def test_repair_creates_linked_immutable_generation(workspace: Path) -> None:
    board, run_dir, report = _ready_run(workspace)
    try:
        service = DeliveryAttestationService(
            board, run_dir=run_dir, workspace=workspace, muxdev_home=workspace / "home"
        )
        first = service.finalize(
            run_id="run_signed", task="fix service", workflow="software-dev",
            diff_path=run_dir / "diff.patch", report_path=report,
        )
        second = service.finalize(
            run_id="run_signed", task="fix service", workflow="software-dev",
            diff_path=run_dir / "diff.patch", report_path=report, repair=True,
        )
        records = board.list_delivery_attestations("run_signed")
        assert [item["generation"] for item in records] == [1, 2]
        assert records[0]["is_current"] is False
        assert second["previous_attestation_hash"] == first["payload_hash"]
    finally:
        board.close()


def test_payload_tamper_invalidates_signature(workspace: Path) -> None:
    board, run_dir, report = _ready_run(workspace)
    try:
        record = _finalize(board, workspace, run_dir, report)
        payload = dict(record["payload"])
        payload["routing"] = {"main_provider": "tampered"}
        verified = verify_attestation_record(payload, record["signature"], evidence_valid=True)
        assert verified["valid"] is False
        assert verified["integrity_valid"] is False
    finally:
        board.close()


def test_bundle_is_deterministic_and_verifies_with_optional_pinning(workspace: Path) -> None:
    board, run_dir, report = _ready_run(workspace)
    try:
        record = _finalize(board, workspace, run_dir, report)
        first = workspace / "first.muxattest"
        second = workspace / "second.muxattest"
        export_attestation_bundle(board, run_id="run_signed", run_dir=run_dir, output=first)
        export_attestation_bundle(board, run_id="run_signed", run_dir=run_dir, output=second)
        assert first.read_bytes() == second.read_bytes()
        self_asserted = verify_attestation_bundle(first)
        assert self_asserted["valid"] is True
        assert self_asserted["identity_status"] == "self_asserted"
        trusted = verify_attestation_bundle(
            first,
            trusted_fingerprint=str(record["public_key_fingerprint"]),
            require_trusted=True,
        )
        assert trusted["valid"] is True
        assert trusted["identity_status"] == "trusted"
        mismatch = verify_attestation_bundle(first, trusted_fingerprint="00" * 32)
        assert mismatch["valid"] is False
        assert mismatch["identity_status"] == "mismatch"
    finally:
        board.close()


def test_bundle_excludes_private_and_prompt_material(workspace: Path) -> None:
    board, run_dir, report = _ready_run(workspace, task="RAW TASK CONTENT")
    try:
        service = DeliveryAttestationService(
            board, run_dir=run_dir, workspace=workspace, muxdev_home=workspace / "home"
        )
        record = service.finalize(
            run_id="run_signed", task="RAW TASK CONTENT", workflow="software-dev",
            diff_path=run_dir / "diff.patch", report_path=report,
        )
        archive = workspace / "safe.muxattest"
        export_attestation_bundle(board, run_id="run_signed", run_dir=run_dir, output=archive, record=record)
        with zipfile.ZipFile(archive) as bundle:
            names = bundle.namelist()
            content = b"\n".join(bundle.read(name) for name in names)
        assert not any("task.md" in name or "transcript" in name or "provider_state" in name for name in names)
        assert b"RAW TASK CONTENT" not in content
        assert b"PRIVATE KEY" not in content
        assert str(workspace).encode() not in content
    finally:
        board.close()


def test_bundle_rejects_zip_slip_and_undeclared_members(workspace: Path) -> None:
    archive = workspace / "unsafe.muxattest"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escape", b"bad")
        bundle.writestr("manifest.json", b"{}")
    result = verify_attestation_bundle(archive)
    assert result["valid"] is False
    assert "unsafe" in result["errors"][0]


def test_evidence_v2_uses_relative_run_artifact_refs(workspace: Path) -> None:
    board, run_dir, _ = _ready_run(workspace)
    try:
        events = [json.loads(line) for line in (run_dir / "evidence" / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        refs = [ref for event in events for ref in event.get("artifact_refs", [])]
        assert refs
        assert all(not ref.get("path") for ref in refs)
        assert all(not Path(str(ref.get("relative_path") or ".")).is_absolute() for ref in refs)
    finally:
        board.close()


@pytest.mark.integration
def test_http_attestation_surface_uses_controlled_ids_only(workspace: Path) -> None:
    paths = default_daemon_paths({"MUXDEV_HOME": str(workspace / "daemon-home")}).ensure()
    manager = TaskManager(paths=paths, worker_count=1)
    run_id = "run_http_attestation"
    run_dir = workspace / ".muxdev" / "runs" / run_id
    run_dir.mkdir(parents=True)
    try:
        with manager.board() as board:
            board.create_run(
                run_id=run_id, task="fix service", workflow="software-dev", provider="mock",
                workspace=workspace, worktree=workspace,
            )
            spec = RunSpec.from_submit_payload(
                run_id=run_id, task="fix service", workspace=workspace,
                provider="mock", workflow="software-dev",
            )
            with board.unit_of_work():
                DurableExecutionQueue(board).persist_run_spec(spec)
            board.set_run_status(run_id, RunStatus.RUNNING)
            board.upsert_stage(run_id, "review", role="review", status="completed", summary="clean")
            board.add_test_result(run_id, "test", True, "python -m pytest", "passed")
            (run_dir / "task.md").write_text("fix service", encoding="utf-8")
            (run_dir / "diff.patch").write_text("+safe\n", encoding="utf-8")
            (run_dir / "final_report.md").write_text("# report\n", encoding="utf-8")
            write_evidence_run(run_dir, run_id, board)
            DeliveryAttestationService(
                board, run_dir=run_dir, workspace=workspace, muxdev_home=paths.home
            ).finalize(
                run_id=run_id, task="fix service", workflow="software-dev",
                diff_path=run_dir / "diff.patch", report_path=run_dir / "final_report.md",
            )
        with TestClient(create_app(task_manager=manager)) as client:
            summary = client.get(f"/api/tasks/{run_id}/attestation")
            assert summary.status_code == 200
            assert "payload" not in summary.json() and "signature" not in summary.json()
            blocked = client.post(
                f"/api/tasks/{run_id}/attestation-exports",
                headers={"Origin": "https://evil.example"},
            )
            assert blocked.status_code == 403
            arbitrary = client.post(
                f"/api/tasks/{run_id}/attestation-exports",
                json={"path": "C:/arbitrary", "private_key": "bad"},
            )
            assert arbitrary.status_code == 422
            exported = client.post(f"/api/tasks/{run_id}/attestation-exports")
            assert exported.status_code == 200
            export_id = exported.json()["export_id"]
            verified = client.post(f"/api/attestation-exports/{export_id}/verify")
            assert verified.status_code == 200
            assert verified.json()["valid"] is True
            assert client.post("/api/trust/rotate").status_code == 404
            assert client.post(f"/api/attestation-exports/{export_id}/restore").status_code == 404
    finally:
        manager.close(timeout=10.0)
