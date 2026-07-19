from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from uuid import uuid4


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".test_workspaces/wheel-runtime").resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["MUXDEV_HOME"] = str(root / "home")

    from fastapi.testclient import TestClient

    from muxdev import __version__
    from muxdev.api.web import create_app
    from muxdev.daemon.paths import default_daemon_paths
    from muxdev.daemon.tasks import TaskManager
    from muxdev.runtime import SupervisorRuntime
    from muxdev.services.attestation import verify_attestation_record
    from muxdev.services.demo import load_demo_scenario
    from muxdev.services.routing_benchmark import run_replay_benchmark
    from muxdev.storage import Blackboard

    manager = TaskManager(paths=default_daemon_paths().ensure(), worker_count=1)
    try:
        with TestClient(create_app(task_manager=manager, enforce_auth=True)) as client:
            health = client.get("/api/health").json()
            assert client.get("/api/tasks").status_code == 401
            assert client.get("/api/tasks", headers=manager.auth.authorization_headers()).status_code == 200
    finally:
        manager.close(timeout=5)

    benchmark_dir = root / "benchmark"
    benchmark_dir.mkdir(parents=True, exist_ok=True)
    with Blackboard(benchmark_dir, db_path=benchmark_dir / "blackboard.sqlite") as board:
        benchmark = run_replay_benchmark(board)
        benchmark_report = benchmark["report"]["payload"]

    project = root / f"mock-project-{uuid4().hex}"
    (project / ".muxdev").mkdir(parents=True)
    run = SupervisorRuntime(project, write_dashboards=False).run(
        "Produce a deterministic signed RC wheel smoke delivery.",
        provider="mock", workflow_name="dev-lite", require_approval=set(),
        gate="auto", depth="simple",
    )
    with Blackboard(run.run_dir) as board:
        attestation = board.latest_delivery_attestation(run.run_id)
    if not attestation or not attestation.get("signature"):
        raise SystemExit("mock wheel smoke did not create a signed attestation")
    verified = verify_attestation_record(attestation["payload"], attestation["signature"], evidence_valid=True)
    if not verified["valid"]:
        raise SystemExit("mock wheel smoke attestation verification failed")

    scenario = load_demo_scenario("trusted-delivery-v1")
    print(json.dumps({
        "version": __version__,
        "daemon_health": health["status"],
        "demo_fixture_hash": scenario["fixture_hash"],
        "benchmark_result_count": benchmark_report["result_count"],
        "benchmark_gate": benchmark_report["gate"]["status"],
        "mock_run_status": str(run.status),
        "attestation_valid": verified["valid"],
        "identity_status": verified["identity_status"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
