"""Daemon worker queue primitives."""

from __future__ import annotations

import threading
import sqlite3
from time import monotonic
from dataclasses import dataclass, field
from typing import Callable
from uuid import uuid4

from ..domain import CancellationToken, ExecutionLease, LeaseLost, ReconciliationRequired, RetryableExecutionError, TaskCancelled
from ..storage import DurableExecutionQueue


@dataclass
class TaskQueue:
    lock: threading.RLock = field(default_factory=threading.RLock)
    workers: dict[str, threading.Thread] = field(default_factory=dict)

    def start(self, task_id: str, thread: threading.Thread) -> None:
        with self.lock:
            self.workers[task_id] = thread
        thread.start()

    def start_if_idle(self, task_id: str, thread: threading.Thread, *, before_start: Callable[[], None] | None = None) -> bool:
        with self.lock:
            existing = self.workers.get(task_id)
            if existing and existing.is_alive():
                return False
            if before_start is not None:
                before_start()
            self.workers[task_id] = thread
        thread.start()
        return True

    def finished(self, task_id: str) -> None:
        """Prune a worker only after another thread can observe it as stopped.

        A worker cannot safely remove itself: doing so creates a short window in
        which ``wait()`` reports success while the thread is still returning.
        """
        with self.lock:
            worker = self.workers.get(task_id)
            if worker is not None and worker is not threading.current_thread() and not worker.is_alive():
                self.workers.pop(task_id, None)

    def wait(self, task_id: str, timeout: float | None = None) -> bool:
        """Wait for one worker and prune it when it reaches a terminal thread state."""
        with self.lock:
            worker = self.workers.get(task_id)
        if worker is None:
            return True
        worker.join(timeout)
        if worker.is_alive():
            return False
        with self.lock:
            if self.workers.get(task_id) is worker:
                self.workers.pop(task_id, None)
        return True

    def shutdown(self, timeout: float = 30.0) -> list[str]:
        """Wait for known workers and return ids that are still alive."""
        deadline = monotonic() + max(timeout, 0.0)
        with self.lock:
            workers = list(self.workers.items())
        alive: list[str] = []
        for task_id, worker in workers:
            worker.join(max(0.0, deadline - monotonic()))
            if worker.is_alive():
                alive.append(task_id)
                continue
            with self.lock:
                if self.workers.get(task_id) is worker:
                    self.workers.pop(task_id, None)
        return alive

    def active_task_ids(self) -> list[str]:
        with self.lock:
            return sorted(task_id for task_id, worker in self.workers.items() if worker.is_alive())


ExecuteLease = Callable[[ExecutionLease, CancellationToken], str]
BoardFactory = Callable[[], object]
StatePublisher = Callable[[dict[str, object]], None]


class DurableWorkerPool:
    """Bounded worker pool backed entirely by durable SQLite jobs."""

    def __init__(
        self,
        *,
        board_factory: BoardFactory,
        execute: ExecuteLease,
        publish: StatePublisher,
        worker_count: int = 2,
        lease_ms: int = 30_000,
        heartbeat_ms: int = 5_000,
        poll_ms: int = 250,
    ) -> None:
        self.board_factory = board_factory
        self.execute = execute
        self.publish = publish
        self.worker_count = max(1, int(worker_count))
        self.lease_ms = max(1_000, int(lease_ms))
        self.heartbeat_ms = max(100, int(heartbeat_ms))
        self.poll_ms = max(10, int(poll_ms))
        self.instance_id = f"daemon_{uuid4().hex}"
        self._condition = threading.Condition(threading.RLock())
        self._stop = threading.Event()
        self._started = False
        self._accepting = True
        self._workers: list[threading.Thread] = []
        self._heartbeat_thread: threading.Thread | None = None
        self._active: dict[str, tuple[ExecutionLease, CancellationToken]] = {}

    def start(self) -> None:
        with self._condition:
            if self._started:
                self._condition.notify_all()
                return
            self._started = True
            with self.board_factory() as board:
                queue = DurableExecutionQueue(board)
                queue.register_daemon(self.instance_id, worker_count=self.worker_count)
                recovered = queue.recover_expired()
            self._workers = [
                threading.Thread(
                    target=self._worker_loop,
                    args=(index,),
                    name=f"muxdev-worker-{index}",
                    daemon=True,
                )
                for index in range(self.worker_count)
            ]
            for worker in self._workers:
                worker.start()
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                name="muxdev-worker-heartbeat",
                daemon=True,
            )
            self._heartbeat_thread.start()
            self._condition.notify_all()
        if any(recovered.values()):
            self.publish({"type": "execution_recovered", "version": 1, **recovered})

    def notify(self) -> None:
        self.start()
        with self._condition:
            self._condition.notify_all()

    def signal_cancel(self, run_id: str, reason: str = "") -> None:
        with self._condition:
            active = self._active.get(run_id)
            if active is not None:
                active[1].request_cancel(reason)
            self._condition.notify_all()

    def wait(self, run_id: str, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else monotonic() + max(0.0, timeout)
        while True:
            with self.board_factory() as board:
                latest = DurableExecutionQueue(board).latest_for_run(run_id)
            if latest is None or str(latest["state"]) in {"succeeded", "failed", "cancelled", "reconciliation_required"}:
                return True
            if deadline is not None and monotonic() >= deadline:
                return False
            remaining = self.poll_ms / 1000
            if deadline is not None:
                remaining = min(remaining, max(0.0, deadline - monotonic()))
            with self._condition:
                self._condition.wait(remaining)

    def shutdown(self, timeout: float = 30.0) -> list[str]:
        self._accepting = False
        if not self._started:
            return []
        deadline = monotonic() + max(0.0, timeout)
        while monotonic() < deadline:
            with self._condition:
                if not self._active:
                    break
                self._condition.wait(min(0.1, max(0.0, deadline - monotonic())))
        with self._condition:
            active = list(self._active.values())
        for lease, token in active:
            token.mark_lease_lost()
            try:
                with self.board_factory() as board:
                    DurableExecutionQueue(board).release_for_shutdown(lease)
            except (LeaseLost, FileNotFoundError):
                pass
        self._stop.set()
        with self._condition:
            self._condition.notify_all()
        for worker in self._workers:
            worker.join(max(0.0, deadline - monotonic()))
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(max(0.0, deadline - monotonic()))
        try:
            with self.board_factory() as board:
                DurableExecutionQueue(board).stop_daemon(self.instance_id)
        except Exception:
            pass
        return self.active_task_ids()

    def active_task_ids(self) -> list[str]:
        with self._condition:
            return sorted(self._active)

    def status(self) -> dict[str, object]:
        alive = sum(1 for worker in self._workers if worker.is_alive())
        with self._condition:
            busy = len(self._active)
        with self.board_factory() as board:
            payload = DurableExecutionQueue(board).status(
                worker_count=self.worker_count,
                alive_workers=alive,
                busy_workers=busy,
            )
        payload["accepting"] = self._accepting
        payload["instance_id"] = self.instance_id
        return payload

    def _worker_loop(self, index: int) -> None:
        owner = f"{self.instance_id}:worker-{index}"
        while not self._stop.is_set():
            try:
                with self.board_factory() as board:
                    lease = DurableExecutionQueue(board).claim(owner, lease_ms=self.lease_ms)
            except Exception as exc:
                self.publish({"type": "execution_scheduler_error", "version": 1, "error": str(exc)[:500]})
                self._stop.wait(self.poll_ms / 1000)
                continue
            if lease is None:
                if index == 0:
                    try:
                        with self.board_factory() as board:
                            recovered = DurableExecutionQueue(board).recover_expired()
                        if any(recovered.values()):
                            self.publish({"type": "execution_recovered", "version": 1, **recovered})
                    except Exception:
                        pass
                with self._condition:
                    self._condition.wait(self.poll_ms / 1000)
                continue
            token = CancellationToken()
            with self._condition:
                self._active[lease.run_id] = (lease, token)
            try:
                with self.board_factory() as board:
                    DurableExecutionQueue(board).mark_running(lease)
                outcome = self.execute(lease, token)
                token.raise_if_stopped()
                with self.board_factory() as board:
                    DurableExecutionQueue(board).complete(lease, outcome_status=outcome)
                self.publish({"type": "task_updated", "task_id": lease.run_id, "status": outcome})
            except TaskCancelled:
                self._record_harness_cancellation(lease, token)
                self._acknowledge_cancel(lease)
            except LeaseLost:
                self._acknowledge_cancel(lease, only_if_requested=True)
            except ReconciliationRequired as exc:
                try:
                    with self.board_factory() as board:
                        DurableExecutionQueue(board).require_reconciliation(lease, str(exc))
                    self.publish({"type": "task_updated", "task_id": lease.run_id, "status": "reconciliation_required"})
                except LeaseLost:
                    self._acknowledge_cancel(lease, only_if_requested=True)
            except Exception as exc:
                if token.cancelled:
                    self._acknowledge_cancel(lease)
                else:
                    try:
                        with self.board_factory() as board:
                            state = DurableExecutionQueue(board).retry_or_fail(
                                lease,
                                exc,
                                retryable=_is_retryable_infrastructure_error(exc),
                            )
                        self.publish({"type": "task_updated", "task_id": lease.run_id, "status": state})
                    except LeaseLost:
                        self._acknowledge_cancel(lease, only_if_requested=True)
            finally:
                with self._condition:
                    self._active.pop(lease.run_id, None)
                    self._condition.notify_all()

    def _record_harness_cancellation(self, lease: ExecutionLease, token: CancellationToken) -> None:
        try:
            with self.board_factory() as board:
                board.record_harness_cancellation(lease.run_id, token.cancellation_context())
        except Exception:
            # Cancellation acknowledgement must not be held hostage by optional
            # Attempt diagnostics. Queue state remains the authoritative stop.
            return

    def _acknowledge_cancel(self, lease: ExecutionLease, *, only_if_requested: bool = False) -> None:
        try:
            with self.board_factory() as board:
                queue = DurableExecutionQueue(board)
                if only_if_requested:
                    job = queue.get_job(lease.job_id, internal=True)
                    if str(job["state"]) != "cancel_requested":
                        return
                queue.acknowledge_cancel(lease)
            self.publish({"type": "task_updated", "task_id": lease.run_id, "status": "aborted"})
        except (LeaseLost, FileNotFoundError):
            return

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.heartbeat_ms / 1000):
            with self._condition:
                active = list(self._active.values())
            for lease, token in active:
                try:
                    with self.board_factory() as board:
                        state = DurableExecutionQueue(board).heartbeat(lease, lease_ms=self.lease_ms)
                    if state == "cancel_requested":
                        token.request_cancel("persistent cancellation requested")
                    elif state == "lost":
                        token.mark_lease_lost()
                except Exception:
                    token.mark_lease_lost()
            try:
                with self.board_factory() as board:
                    DurableExecutionQueue(board).heartbeat_daemon(self.instance_id)
            except Exception:
                pass


def _is_retryable_infrastructure_error(exc: BaseException) -> bool:
    if isinstance(exc, (RetryableExecutionError, TimeoutError, ConnectionError)):
        return True
    if isinstance(exc, sqlite3.OperationalError):
        message = str(exc).lower()
        return any(token in message for token in ("locked", "busy", "temporarily", "i/o"))
    return False
