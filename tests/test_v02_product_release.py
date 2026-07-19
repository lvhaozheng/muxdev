from __future__ import annotations

import json
import hashlib
import time
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from muxdev import __version__
from muxdev.api.web import create_app, render_live_dashboard_html
from muxdev.daemon.paths import default_daemon_paths
from muxdev.daemon.tasks import TaskManager
from muxdev.services.demo import load_demo_scenario
from muxdev.services.local_auth import COOKIE_NAME
from muxdev.services.routing_benchmark import (
    build_benchmark_report,
    evaluate_registered_fixture,
    export_benchmark_results,
    load_registered_routing_suite,
    materialize_registered_fixture,
    registered_benchmark_asset_bytes,
    run_replay_benchmark,
    verify_benchmark_results,
)
from muxdev.storage import Blackboard


@pytest.fixture
def workspace() -> Path:
    path = (Path(".test_workspaces") / f"v02_product_{uuid4().hex}").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _manager(workspace: Path) -> TaskManager:
    return TaskManager(paths=default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure())


def test_rc_version_and_single_task_dashboard() -> None:
    assert __version__ == "0.2.0rc1"
    html = render_live_dashboard_html("run_story", lang="en")
    assert 'data-task-id="run_story"' in html
    assert "/dashboard/tasks?limit=100" in html
    assert "task_story_invalidated" in html
    assert "Why this agent" in html
    assert "Command Palette" not in html


def test_registered_demo_is_hash_bound_and_side_effect_free() -> None:
    first = load_demo_scenario("trusted-delivery-v1")
    second = load_demo_scenario("trusted-delivery-v1")
    assert first["fixture_hash"] == second["fixture_hash"]
    assert first["side_effects"] is False
    assert first["label"] == "SIMULATION / REPLAY"
    assert sum(row["seconds"] for row in first["steps"]) <= 240
    with pytest.raises(ValueError):
        load_demo_scenario("../../arbitrary")


def test_task_story_has_signed_cursor_and_privacy_projection(workspace: Path) -> None:
    manager = _manager(workspace)
    try:
        with manager.board() as board:
            board.create_run(
                run_id="run_story",
                task="explain delivery",
                workflow="dev-lite",
                provider="mock",
                workspace=workspace,
                worktree=workspace / "worktree",
            )
        story = manager.task_story("run_story", limit=1)
        assert story["contract_version"] == "muxdev.task-story.v1"
        assert story["progress"]["current_phase"] == "submitted"
        assert story["progress"]["next_action"]["kind"] == "observe"
        raw = json.dumps(story)
        assert "lease_token" not in raw
        assert "fencing_token" not in raw
        assert "payload_json" not in raw
        if story["timeline"]["next_cursor"]:
            with pytest.raises(ValueError):
                manager.task_story("run_story", cursor=story["timeline"]["next_cursor"] + "tampered")
    finally:
        manager.close(timeout=5)


def test_dashboard_task_pagination_rejects_forged_cursor(workspace: Path) -> None:
    manager = _manager(workspace)
    try:
        with manager.board() as board:
            for index in range(3):
                board.create_run(
                    run_id=f"run_{index}", task=f"task {index}", workflow="dev-lite", provider="mock",
                    workspace=workspace, worktree=workspace / f"worktree-{index}",
                )
        page = manager.dashboard_tasks(limit=2)
        assert len(page["items"]) == 2
        assert page["next_cursor"]
        cursor = str(page["next_cursor"])
        tampered = cursor[:-1] + ("0" if cursor[-1] != "0" else "1")
        with pytest.raises(ValueError):
            manager.dashboard_tasks(cursor=tampered)
    finally:
        manager.close(timeout=5)


def test_local_api_auth_bearer_cookie_bootstrap_and_rotation(workspace: Path) -> None:
    manager = _manager(workspace)
    try:
        app = create_app(task_manager=manager, enforce_auth=True)
        with TestClient(app) as client:
            assert client.get("/api/health").status_code == 200
            assert client.get("/api/tasks").status_code == 401
            assert "Local authorization required" in client.get("/").text
            headers = manager.auth.authorization_headers()
            assert client.get("/api/tasks", headers=headers).status_code == 200
            nonce = manager.auth.issue_bootstrap()
            response = client.get(f"/auth/bootstrap?nonce={nonce}", follow_redirects=False)
            assert response.status_code == 303
            assert COOKIE_NAME in response.cookies
            client.cookies.update(response.cookies)
            assert client.get("/api/tasks").status_code == 200
            assert client.get(f"/auth/bootstrap?nonce={nonce}", follow_redirects=False).status_code == 401
        old = headers
        manager.auth.rotate()
        assert manager.auth.verify_bearer(old["Authorization"]) is False
    finally:
        manager.close(timeout=5)


@pytest.mark.release
def test_http_story_demo_benchmark_and_no_live_benchmark(workspace: Path) -> None:
    manager = _manager(workspace)
    try:
        with manager.board() as board:
            board.create_run(
                run_id="run_http_story", task="http story", workflow="dev-lite", provider="mock",
                workspace=workspace, worktree=workspace / "worktree",
            )
        with TestClient(create_app(task_manager=manager)) as client:
            dashboard = client.get("/")
            csp = dashboard.headers["content-security-policy"]
            assert "unsafe-inline" not in csp
            assert "nonce-" in csp and 'nonce="' in dashboard.text
            assert client.get("/api/dashboard/tasks").json()["contract_version"] == "muxdev.dashboard-task-list.v1"
            assert client.get("/api/tasks/run_http_story/story").json()["contract_version"] == "muxdev.task-story.v1"
            assert client.get("/api/demo/scenarios/trusted-delivery-v1").json()["side_effects"] is False
            assert client.post("/api/routing/benchmarks", json={"suite_id": "trusted-routing-v1", "live": True, "acknowledged": True, "max_cost_usd": 1}).status_code == 405
            replay = client.post("/api/benchmarks/replays", json={"suite_id": "trusted-routing-v1"})
            assert replay.status_code == 200
            assert len(replay.json()["results"]) == 48
    finally:
        manager.close(timeout=5)


@pytest.mark.release
def test_replay_benchmark_is_deterministic_and_never_production_learning(workspace: Path) -> None:
    db = workspace / "benchmark.sqlite"
    with Blackboard(workspace, db_path=db) as board:
        execution = run_replay_benchmark(board)
        assert execution["status"] == "completed"
        assert len(execution["results"]) == 48
        report = execution["report"]["payload"]
        assert report["gate"]["status"] == "simulation_only"
        assert report["claims_allowed"] is False
        assert board.routing_outcomes() == []
        assert board.storage_health()["schema_version"] == 7


@pytest.mark.release
def test_benchmark_report_bootstrap_is_byte_stable() -> None:
    suite = load_registered_routing_suite("trusted-routing-v1")
    results = []
    for case in suite["cases"]:
        for provider in ("codex", "qwen"):
            results.append(
                {
                    "case_id": case["id"], "provider": provider, "eligible": not case["risk_tags"],
                    "status": "completed", "quality_score": 0.8, "verified_success": True,
                    "evidence_complete": True, "attestation_valid": True, "mode": "replay",
                }
            )
    first = build_benchmark_report(execution_id="stable", suite=suite, mode="replay", results=results)
    second = build_benchmark_report(execution_id="stable", suite=suite, mode="replay", results=results)
    assert first == second


def test_registered_benchmark_assets_are_hash_bound_and_evaluator_is_separate(workspace: Path) -> None:
    case = load_registered_routing_suite("trusted-routing-v1")["cases"][0]
    fixture = registered_benchmark_asset_bytes(case, kind="fixture")
    evaluator = registered_benchmark_asset_bytes(case, kind="evaluator")
    assert f"sha256:{hashlib.sha256(fixture).hexdigest()}" == case["fixture_hash"]
    assert f"sha256:{hashlib.sha256(evaluator).hexdigest()}" == case["evaluator_hash"]
    target = workspace / "fixture"
    materialize_registered_fixture(case, target)
    assert (target / "BENCHMARK_TASK.md").is_file()
    assert not (target / "evaluator").exists()
    assert evaluate_registered_fixture(case, target)["functional_score"] == 0.0
    (target / "SOLUTION.md").write_text("verified solution\n", encoding="utf-8")
    assert evaluate_registered_fixture(case, target)["functional_score"] == 1.0


@pytest.mark.release
def test_benchmark_result_zip_verifies_and_detects_tamper(workspace: Path) -> None:
    with Blackboard(workspace, db_path=workspace / "bundle.sqlite") as board:
        execution = run_replay_benchmark(board)
        target = workspace / "results.zip"
        exported = export_benchmark_results(board, execution["execution_id"], target)
        assert exported["size"] > 0
        assert verify_benchmark_results(target)["valid"] is True
    data = bytearray(target.read_bytes())
    data[-10] ^= 1
    target.write_bytes(bytes(data))
    assert verify_benchmark_results(target)["valid"] is False


@pytest.mark.release
def test_benchmark_result_zip_rejects_path_escape(workspace: Path) -> None:
    target = workspace / "unsafe.zip"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("../escape", b"bad")
        archive.writestr("manifest.json", json.dumps({"contract_version": "muxdev.routing-benchmark-results.v1", "members": {}}))
    verified = verify_benchmark_results(target)
    assert verified["valid"] is False
    assert any("unsafe member" in error for error in verified["errors"])


def test_api_rejects_nonlocal_origin_and_large_body(workspace: Path) -> None:
    manager = _manager(workspace)
    try:
        with TestClient(create_app(task_manager=manager)) as client:
            denied = client.post(
                "/api/benchmarks/replays",
                headers={"Origin": "https://evil.example"},
                json={"suite_id": "trusted-routing-v1"},
            )
            assert denied.status_code == 403
            too_large = client.post(
                "/api/benchmarks/replays",
                headers={"Content-Length": str(1024 * 1024 + 1)},
                content=b"{}",
            )
            assert too_large.status_code == 413
            invalid_length = client.post(
                "/api/benchmarks/replays",
                headers={"Content-Length": "invalid"},
                content=b"{}",
            )
            assert invalid_length.status_code == 400
    finally:
        manager.close(timeout=5)


@pytest.mark.release
def test_task_story_and_1000_task_summary_performance(workspace: Path) -> None:
    manager = _manager(workspace)
    try:
        created = "2026-01-01T00:00:00+00:00"
        rows = [
            (
                f"run_perf_{index:04d}", f"performance task {index}", "dev-lite", "mock", "created",
                str(workspace), str(workspace / f"worktree-{index}"), created, created,
            )
            for index in range(1_000)
        ]
        with manager.board() as board:
            with board.unit_of_work():
                board.conn.executemany(
                    "INSERT INTO runs(run_id,task,workflow,provider,status,workspace,worktree,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    rows,
                )
            with board.unit_of_work():
                board.conn.executemany(
                    "INSERT INTO execution_events(event_id,run_id,job_id,sequence,event_type,event_version,idempotency_key,payload_json,created_at_ms) VALUES (?,?,?,?,?,?,?,?,?)",
                    [
                        (f"evt_perf_{index:05d}", "run_perf_0000", None, index + 1, "execution.observed", 1, f"perf:{index}", "{}", index)
                        for index in range(10_000)
                    ],
                )
        list_times: list[float] = []
        for _ in range(20):
            started = time.perf_counter()
            task_page = manager.dashboard_tasks(limit=100)
            list_times.append(time.perf_counter() - started)
        story_times: list[float] = []
        for _ in range(20):
            started = time.perf_counter()
            story = manager.task_story("run_perf_0000", limit=200)
            story_times.append(time.perf_counter() - started)
        assert sorted(list_times)[18] < 0.2
        assert sorted(story_times)[18] < 0.2
        assert len(json.dumps(task_page).encode("utf-8")) < 256 * 1024
        assert len(json.dumps(story).encode("utf-8")) < 256 * 1024
        assert len(task_page["items"]) == 100
        assert len(story["timeline"]["items"]) == 200
    finally:
        manager.close(timeout=5)
