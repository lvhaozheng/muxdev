"""Repository-local pytest infrastructure."""

from pathlib import Path

import pytest


def pytest_sessionstart() -> None:
    # Pre-creating the target avoids pytest's cross-volume atomic cache rename
    # fallback on locked-down Windows hosts.
    Path(".test_workspaces/.pytest_cache").mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def _close_daemon_workers_after_each_test():
    """Keep one TestClient from polluting later integration cases."""
    yield
    from muxdev.daemon.tasks import TaskManager

    for manager in TaskManager.live_instances():
        assert manager.close(timeout=2.0) == [], "daemon test leaked a live Worker"
