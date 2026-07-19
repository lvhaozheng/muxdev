"""Durable execution contracts shared by the daemon and storage layers."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import StrEnum


class ExecutionState(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RECONCILIATION_REQUIRED = "reconciliation_required"


ACTIVE_EXECUTION_STATES = frozenset(
    {
        str(ExecutionState.QUEUED),
        str(ExecutionState.LEASED),
        str(ExecutionState.RUNNING),
        str(ExecutionState.RETRY_WAIT),
        str(ExecutionState.CANCEL_REQUESTED),
    }
)
TERMINAL_EXECUTION_STATES = frozenset(
    {str(ExecutionState.SUCCEEDED), str(ExecutionState.FAILED), str(ExecutionState.CANCELLED)}
)


class ExecutionError(RuntimeError):
    """Base class for durable scheduler errors."""


class LeaseLost(ExecutionError):
    """Raised when a stale worker attempts to mutate durable state."""


class TaskCancelled(ExecutionError):
    """Raised at a cooperative cancellation boundary."""


class ReconciliationRequired(ExecutionError):
    """Raised when replay may duplicate an opaque external side effect."""


class RetryableExecutionError(ExecutionError):
    """Explicitly classified transient daemon/runtime infrastructure failure."""


@dataclass(frozen=True)
class ExecutionLease:
    job_id: str
    run_id: str
    command: str
    lease_owner: str
    lease_token: str
    fencing_token: int
    attempt: int
    max_attempts: int


class CancellationToken:
    """Thread-safe cooperative cancellation and lease-loss signal."""

    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._lease_lost = threading.Event()
        self._reason = ""
        self._metadata_lock = threading.RLock()
        self._provider_attempt: dict[str, object] = {}
        self._process_cancellation: dict[str, object] = {}

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    @property
    def lease_lost(self) -> bool:
        return self._lease_lost.is_set()

    @property
    def reason(self) -> str:
        return self._reason

    def request_cancel(self, reason: str = "") -> None:
        self._reason = str(reason or "cancellation requested")
        self._cancelled.set()

    def mark_lease_lost(self) -> None:
        self._lease_lost.set()

    def bind_provider_attempt(self, *, run_id: str, stage_id: str, provider: str, attempt: int) -> None:
        with self._metadata_lock:
            self._provider_attempt = {
                "run_id": run_id,
                "stage_id": stage_id,
                "provider": provider,
                "attempt": int(attempt),
            }

    def record_process_cancellation(self, *, cooperative: bool, forced: bool) -> None:
        with self._metadata_lock:
            self._process_cancellation = {
                "cooperative": bool(cooperative),
                "forced": bool(forced),
            }

    def cancellation_context(self) -> dict[str, object]:
        with self._metadata_lock:
            return {
                **self._provider_attempt,
                **self._process_cancellation,
                "reason": self._reason,
            }

    def raise_if_stopped(self) -> None:
        if self._lease_lost.is_set():
            raise LeaseLost("execution lease is no longer valid")
        if self._cancelled.is_set():
            raise TaskCancelled(self._reason or "task cancelled")


@dataclass(frozen=True)
class ExecutionGuard:
    lease: ExecutionLease
    cancellation: CancellationToken

    def check(self) -> None:
        self.cancellation.raise_if_stopped()
