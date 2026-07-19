from __future__ import annotations

import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from muxdev.api.web import create_app
from muxdev.daemon.paths import default_daemon_paths
from muxdev.daemon.queue import DurableWorkerPool
from muxdev.daemon.tasks import TaskManager
from muxdev.domain import CancellationToken, ExecutionGuard, LeaseLost, RunSpec
from muxdev.clients.sessions.backends import HeadlessSubprocessBackend
from muxdev.storage import Blackboard, DurableExecutionQueue
from muxdev.cli.main import app


@pytest.fixture
def workspace() -> Path:
    root = Path(".test_workspaces") / f"v02_runtime_{uuid4().hex}"
    root.mkdir(parents=True)
    (root / ".muxdev").mkdir()
    try:
        yield root.resolve()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _spec(workspace: Path, run_id: str) -> RunSpec:
    return RunSpec.from_submit_payload(
        run_id=run_id,
        task=f"durable task {run_id}",
        workspace=workspace,
        provider="mock",
        workflow="software-dev",
    )


def _create(queue: DurableExecutionQueue, workspace: Path, run_id: str) -> dict[str, object]:
    return queue.create_initial(_spec(workspace, run_id), worktree=workspace / ".muxdev" / "runs" / run_id / "worktree")


def test_v3_run_spec_job_and_event_are_atomic_and_post_commit(workspace: Path, monkeypatch) -> None:
    database = workspace / "daemon.sqlite"
    published: list[dict[str, object]] = []
    with Blackboard(workspace, db_path=database, event_sink=published.append) as board:
        queue = DurableExecutionQueue(board)
        created = _create(queue, workspace, "run_atomic_queue")
        assert board.storage_health()["schema_version"] == 7
        assert queue.load_run_spec("run_atomic_queue").workspace == workspace
        assert created["state"] == "queued"
        assert [event["event_type"] for event in published if event["type"] == "execution_event"] == ["execution.enqueued"]

    with Blackboard(workspace, db_path=database) as board:
        queue = DurableExecutionQueue(board)
        original = queue._append_event

        def fail_enqueue(*args, **kwargs):
            if args[2] == "execution.enqueued":
                raise RuntimeError("injected enqueue failure")
            return original(*args, **kwargs)

        monkeypatch.setattr(queue, "_append_event", fail_enqueue)
        with pytest.raises(RuntimeError, match="injected"):
            _create(queue, workspace, "run_rolled_back_queue")
        with pytest.raises(FileNotFoundError):
            board.get_run("run_rolled_back_queue")


def test_one_of_100_concurrent_claimers_gets_job(workspace: Path) -> None:
    database = workspace / "claims.sqlite"
    with Blackboard(workspace, db_path=database) as board:
        _create(DurableExecutionQueue(board), workspace, "run_claim")

    def claim(index: int):
        with Blackboard(workspace, db_path=database) as board:
            return DurableExecutionQueue(board).claim(f"owner-{index}")

    with ThreadPoolExecutor(max_workers=20) as pool:
        claims = list(pool.map(claim, range(100)))
    leases = [lease for lease in claims if lease is not None]
    assert len(leases) == 1
    assert leases[0].attempt == 1


def test_heartbeat_cancel_and_fencing_reject_stale_writer(workspace: Path) -> None:
    database = workspace / "fence.sqlite"
    now = [1_000]
    with Blackboard(workspace, db_path=database) as board:
        queue = DurableExecutionQueue(board, clock=lambda: now[0])
        _create(queue, workspace, "run_fence")
        lease = queue.claim("daemon:worker", lease_ms=1_000)
        assert lease is not None
        queue.mark_running(lease)
        now[0] = 1_500
        assert queue.heartbeat(lease, lease_ms=1_000) == "running"
        assert queue.recover_expired() == {"requeued": 0, "cancelled": 0, "reconciliation_required": 0}
        public_events = queue.events_for_run("run_fence")
        assert all("payload_json" not in event for event in public_events)
        assert "lease_token" not in str(public_events)
        assert "fencing_token" not in str(public_events)
        assert queue.request_cancel("run_fence", reason="test cancel")["status"] == "cancel_requested"
        assert queue.heartbeat(lease) == "cancel_requested"
        queue.acknowledge_cancel(lease)
        with pytest.raises(LeaseLost):
            queue.complete(lease, outcome_status="completed")
        assert board.get_run("run_fence")["status"] == "aborted"


def test_expired_safe_job_requeues_but_opaque_attempt_requires_reconciliation(workspace: Path) -> None:
    database = workspace / "recovery.sqlite"
    now = [1_000]
    with Blackboard(workspace, db_path=database) as board:
        queue = DurableExecutionQueue(board, clock=lambda: now[0])
        _create(queue, workspace, "run_safe")
        safe = queue.claim("owner-safe", lease_ms=100)
        assert safe is not None
        queue.mark_running(safe)
        _create(queue, workspace, "run_opaque")
        opaque = queue.claim("owner-opaque", lease_ms=100)
        assert opaque is not None
        queue.mark_running(opaque)
        board.start_provider_attempt("run_opaque", "code", provider="codex", role="code", attempt=1)
        now[0] = 2_000
        recovered = queue.recover_expired()
        assert recovered == {"requeued": 1, "cancelled": 0, "reconciliation_required": 1}
        assert queue.latest_for_run("run_safe")["state"] == "retry_wait"
        assert queue.latest_for_run("run_opaque")["state"] == "reconciliation_required"
        assert board.get_run("run_opaque")["status"] == "blocked"


def test_execution_guard_blocks_commit_after_cancel_request(workspace: Path) -> None:
    database = workspace / "guard.sqlite"
    with Blackboard(workspace, db_path=database) as board:
        queue = DurableExecutionQueue(board)
        _create(queue, workspace, "run_guard")
        lease = queue.claim("guard-owner")
        assert lease is not None
        queue.mark_running(lease)
        queue.request_cancel("run_guard", reason="fence it")
    guard = ExecutionGuard(lease, CancellationToken())
    with Blackboard(workspace, db_path=database, execution_guard=guard) as guarded:
        with pytest.raises(LeaseLost):
            guarded.add_error("run_guard", None, "stale", "must roll back")
    with Blackboard(workspace, db_path=database) as board:
        assert not [row for row in board.table_rows("error_details", run_id="run_guard") if row["type"] == "stale"]


def test_retry_backoff_exhaustion_and_reconcile_acknowledgement(workspace: Path) -> None:
    database = workspace / "retry.sqlite"
    now = [1_000]
    with Blackboard(workspace, db_path=database) as board:
        queue = DurableExecutionQueue(board, clock=lambda: now[0])
        _create(queue, workspace, "run_retry")
        for attempt, expected_delay in ((1, 1_000), (2, 5_000)):
            lease = queue.claim("retry-owner")
            assert lease is not None and lease.attempt == attempt
            queue.mark_running(lease)
            assert queue.retry_or_fail(lease, TimeoutError(f"infra {attempt}")) == "retry_wait"
            job = queue.latest_for_run("run_retry", internal=True)
            assert int(job["available_at_ms"]) == now[0] + expected_delay
            assert queue.claim("too-early") is None
            now[0] += expected_delay
        lease = queue.claim("retry-owner")
        assert lease is not None and lease.attempt == 3
        queue.mark_running(lease)
        assert queue.retry_or_fail(lease, TimeoutError("infra 3")) == "failed"
        assert board.get_run("run_retry")["status"] == "blocked"
        assert queue.latest_for_run("run_retry")["state"] == "failed"

        _create(queue, workspace, "run_reconcile")
        ambiguous = queue.claim("opaque-owner", lease_ms=100)
        assert ambiguous is not None
        queue.mark_running(ambiguous)
        board.start_provider_attempt("run_reconcile", "code", provider="codex", role="code", attempt=1)
        now[0] += 1_000
        queue.recover_expired()
        with pytest.raises(ValueError, match="acknowledgement"):
            queue.reconcile("run_reconcile", decision="retry", reason="operator inspected transcript")
        retried = queue.reconcile(
            "run_reconcile",
            decision="retry",
            reason="operator inspected transcript",
            acknowledge_duplicate_risk=True,
        )
        assert retried["status"] == "queued"
        assert len(queue.list_for_run("run_reconcile")) == 2


@pytest.mark.integration
def test_headless_provider_process_honours_cooperative_cancel(workspace: Path) -> None:
    token = CancellationToken()
    timer = threading.Timer(0.15, lambda: token.request_cancel("test cancellation"))
    timer.start()
    try:
        with pytest.raises(Exception) as caught:
            HeadlessSubprocessBackend().run(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=workspace,
                timeout=60,
                cancellation_token=token,
                cancel_grace_seconds=0.1,
            )
        assert "cancel" in str(caught.value).lower()
    finally:
        timer.cancel()


def test_completed_job_wins_cancel_race_without_becoming_aborted(workspace: Path) -> None:
    with Blackboard(workspace, db_path=workspace / "race.sqlite") as board:
        queue = DurableExecutionQueue(board)
        _create(queue, workspace, "run_race")
        lease = queue.claim("race-owner")
        assert lease is not None
        queue.mark_running(lease)
        board.set_run_status("run_race", "running")
        board.set_run_status("run_race", "completed")
        queue.complete(lease, outcome_status="completed")
        result = queue.request_cancel("run_race", reason="too late")
        assert result["status"] == "already_finished"
        assert board.get_run("run_race")["status"] == "completed"


def test_legacy_opaque_run_is_baselined_for_manual_reconciliation(workspace: Path) -> None:
    paths = default_daemon_paths({"MUXDEV_HOME": str(workspace / "legacy-home")}).ensure()
    worktree = workspace / "legacy-worktree"
    worktree.mkdir()
    with Blackboard(paths.data_dir, db_path=paths.db_path) as board:
        board.create_run(
            run_id="run_legacy_opaque",
            task="legacy opaque",
            workflow="software-dev",
            provider="codex",
            workspace=workspace,
            worktree=worktree,
        )
        board.set_run_status("run_legacy_opaque", "running")
        board.start_provider_attempt("run_legacy_opaque", "code", provider="codex", role="code", attempt=1)
    manager = TaskManager(paths=paths)
    try:
        history = manager.task_executions("run_legacy_opaque")
        assert history["jobs"][-1]["state"] == "reconciliation_required"
        assert manager.get_run("run_legacy_opaque")["status"] == "blocked"
    finally:
        manager.close(timeout=2)


def test_durable_runtime_cli_contract(monkeypatch) -> None:
    class FakeClient:
        def cancel_task(self, task_id, *, reason="", wait=False, timeout=30.0):
            return {"run_id": task_id, "status": "cancel_requested", "reason": reason, "wait": wait}

        def task_executions(self, task_id):
            return {"run_id": task_id, "jobs": [{"job_id": "job_1", "command": "resume", "state": "succeeded", "attempt": 1, "max_attempts": 3, "heartbeat_age_ms": 1}], "events": []}

        def reconcile_task(self, task_id, *, decision, reason, acknowledge_duplicate_risk=False):
            return {"run_id": task_id, "status": "queued", "decision": decision, "ack": acknowledge_duplicate_risk}

        def runtime_status(self):
            return {"workers": {"configured": 2, "alive": 2, "busy": 0}, "queue": {}}

    import muxdev.cli.main as cli_main

    monkeypatch.setattr(cli_main, "_daemon_client", lambda host, port: FakeClient())
    runner = CliRunner()
    assert runner.invoke(app, ["task", "cancel", "run_cli", "--reason", "operator", "--json"]).exit_code == 0
    assert runner.invoke(app, ["task", "executions", "run_cli", "--json"]).exit_code == 0
    retry = runner.invoke(app, ["task", "reconcile", "run_cli", "--retry", "--reason", "reviewed", "--yes", "--json"])
    assert retry.exit_code == 0
    assert runner.invoke(app, ["runtime", "status", "--json"]).exit_code == 0


def test_fixed_pool_applies_backpressure_and_reclaims_workers(workspace: Path) -> None:
    database = workspace / "pool.sqlite"

    def board_factory():
        return Blackboard(workspace, db_path=database)

    with board_factory() as board:
        queue = DurableExecutionQueue(board)
        for index in range(3):
            _create(queue, workspace, f"run_pool_{index}")
    release = threading.Event()
    lock = threading.Lock()
    current = 0
    maximum = 0

    def execute(lease, token):
        nonlocal current, maximum
        with lock:
            current += 1
            maximum = max(maximum, current)
        release.wait(2)
        token.raise_if_stopped()
        with lock:
            current -= 1
        return "completed"

    pool = DurableWorkerPool(board_factory=board_factory, execute=execute, publish=lambda event: None, worker_count=2, heartbeat_ms=100)
    pool.start()
    deadline = time.time() + 2
    while len(pool.active_task_ids()) < 2 and time.time() < deadline:
        time.sleep(0.01)
    with board_factory() as board:
        states = [job["state"] for index in range(3) for job in DurableExecutionQueue(board).list_for_run(f"run_pool_{index}")]
    assert len(pool.active_task_ids()) == 2
    assert states.count("queued") == 1
    release.set()
    for index in range(3):
        assert pool.wait(f"run_pool_{index}", timeout=3)
    assert pool.shutdown(timeout=3) == []
    assert maximum == 2


@pytest.mark.integration
def test_http_runtime_cancel_execution_and_reconciliation_contract(workspace: Path) -> None:
    paths = default_daemon_paths({"MUXDEV_HOME": str(workspace / "home")}).ensure()
    manager = TaskManager(paths=paths, worker_count=1)
    try:
        with manager.board() as board:
            queue = DurableExecutionQueue(board)
            _create(queue, workspace, "run_http_cancel")
        client = TestClient(create_app(task_manager=manager))
        status = client.get("/api/runtime/status")
        assert status.status_code == 200
        assert "lease_token" not in str(status.json())
        blocked = client.post(
            "/api/tasks/run_http_cancel/cancel",
            json={"reason": "evil"},
            headers={"Origin": "https://evil.example"},
        )
        assert blocked.status_code == 403
        cancelled = client.post("/api/tasks/run_http_cancel/cancel", json={"reason": "user"})
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        history = client.get("/api/tasks/run_http_cancel/executions")
        assert history.status_code == 200
        assert "lease_token" not in str(history.json())
        assert "fencing_token" not in str(history.json())
        assert not any(route.path.endswith("/force-claim") for route in client.app.routes)
    finally:
        manager.close(timeout=3)
