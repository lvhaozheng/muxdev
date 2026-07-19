from __future__ import annotations

import threading
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from muxdev.core.projects import resolve_project_root
from muxdev.daemon.queue import TaskQueue
from muxdev.domain import ids
from muxdev.storage import RunStore


@pytest.fixture
def local_root():
    root = (Path(".test_workspaces") / f"v02_{uuid.uuid4().hex}").resolve()
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_home_muxdev_is_not_treated_as_project_marker(local_root: Path) -> None:
    home = local_root / "home"
    workspace = home / "projects" / "unregistered"
    (home / ".muxdev").mkdir(parents=True)
    workspace.mkdir(parents=True)

    assert resolve_project_root(workspace, user_home=home) == workspace.resolve()


def test_nearest_git_or_project_muxdev_marker_wins(local_root: Path) -> None:
    git_project = local_root / "git-project"
    nested_git = git_project / "src" / "pkg"
    (git_project / ".git").mkdir(parents=True)
    nested_git.mkdir(parents=True)

    mux_project = local_root / "mux-project"
    nested_mux = mux_project / "src" / "pkg"
    (mux_project / ".muxdev").mkdir(parents=True)
    nested_mux.mkdir(parents=True)

    assert resolve_project_root(nested_git, user_home=local_root / "home") == git_project.resolve()
    assert resolve_project_root(nested_mux, user_home=local_root / "home") == mux_project.resolve()


def test_generated_design_directory_resolves_to_outer_project(local_root: Path) -> None:
    project = local_root / "project"
    design = project / "docs" / "design"
    (project / ".muxdev").mkdir(parents=True)
    (design / ".muxdev").mkdir(parents=True)

    assert resolve_project_root(design, user_home=local_root / "home") == project.resolve()


def test_project_root_rejects_missing_and_file_workspaces(local_root: Path) -> None:
    with pytest.raises(FileNotFoundError):
        resolve_project_root(local_root / "missing", user_home=local_root / "home")
    file_path = local_root / "file.txt"
    file_path.write_text("not a workspace", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        resolve_project_root(file_path, user_home=local_root / "home")


def test_run_ids_remain_unique_with_same_millisecond(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ids, "time", lambda: 1234.567)
    with ThreadPoolExecutor(max_workers=32) as pool:
        run_ids = list(pool.map(lambda _: ids.new_run_id(), range(1000)))

    assert len(run_ids) == len(set(run_ids)) == 1000
    assert all(run_id.startswith("run_1234567_") for run_id in run_ids)


def test_task_queue_waits_and_prunes_completed_worker() -> None:
    queue = TaskQueue()
    release = threading.Event()
    worker = threading.Thread(target=lambda: release.wait(1.0), daemon=True)
    queue.start("run_wait", worker)

    assert queue.wait("run_wait", timeout=0.01) is False
    release.set()
    assert queue.wait("run_wait", timeout=1.0) is True
    assert queue.active_task_ids() == []


def test_run_store_rejects_path_escape(local_root: Path) -> None:
    store = RunStore(local_root, runs_dir=local_root / "runs")
    with pytest.raises(ValueError):
        store.create_run_dir("../outside")
    with pytest.raises(ValueError):
        store.find_run_dir("nested/run_id")
    assert not (local_root / "outside").exists()
