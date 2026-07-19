"""Isolated worker workspaces and deterministic content-addressed patch merge."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..core.platforms import hidden_subprocess_kwargs
from ..storage.contracts import sha256_file, write_json_artifact


class ParallelMergeError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkerWorkspace:
    stage_id: str
    path: Path
    base_hash: str


@dataclass(frozen=True)
class WorkerPatch:
    stage_id: str
    path: Path
    patch_hash: str
    base_hash: str
    touched_files: tuple[str, ...]


def workspace_content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for file_path in _workspace_files(path):
        relative = file_path.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        with file_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def prepare_worker_workspace(base: Path, *, workers_root: Path, stage_id: str) -> WorkerWorkspace:
    workers_root = workers_root.resolve()
    workers_root.mkdir(parents=True, exist_ok=True)
    target = (workers_root / _safe_stage_id(stage_id)).resolve()
    if target.parent != workers_root:
        raise ParallelMergeError("worker workspace escaped workers root")
    if target.exists():
        shutil.rmtree(target, onexc=_remove_readonly)
    shutil.copytree(base, target, ignore=shutil.ignore_patterns(".git", ".muxdev", "__pycache__", "*.pyc"))
    _git(target, "init", "--quiet")
    _git(target, "config", "user.email", "muxdev@local.invalid")
    _git(target, "config", "user.name", "muxdev parallel worker")
    _git(target, "add", "-A")
    _git(target, "commit", "--quiet", "--allow-empty", "-m", "muxdev parallel worker baseline")
    return WorkerWorkspace(stage_id=stage_id, path=target, base_hash=workspace_content_hash(target))


def capture_worker_patch(worker: WorkerWorkspace, *, patches_root: Path) -> WorkerPatch:
    _git(worker.path, "add", "-N", ".", allow_failure=True)
    status = _git(worker.path, "status", "--porcelain=v1", "--untracked-files=all")
    touched = tuple(sorted(_status_paths(status)))
    diff = _git(worker.path, "diff", "--binary", "--", ".")
    patches_root.mkdir(parents=True, exist_ok=True)
    patch_path = (patches_root / f"{_safe_stage_id(worker.stage_id)}.patch").resolve()
    patch_path.write_text(diff, encoding="utf-8")
    patch_hash = sha256_file(patch_path)
    write_json_artifact(
        patches_root / f"{_safe_stage_id(worker.stage_id)}.manifest.json",
        {
            "contract_version": "muxdev.parallel_patch.v1",
            "stage_id": worker.stage_id,
            "base_hash": worker.base_hash,
            "patch_hash": patch_hash,
            "touched_files": list(touched),
        },
    )
    return WorkerPatch(worker.stage_id, patch_path, patch_hash, worker.base_hash, touched)


def merge_worker_patches(
    base: Path,
    patches: list[WorkerPatch],
    *,
    expected_base_hash: str,
    fencing_check: Callable[[], None],
) -> list[dict[str, object]]:
    fencing_check()
    actual_base_hash = workspace_content_hash(base)
    if actual_base_hash != expected_base_hash:
        raise ParallelMergeError(f"base workspace drifted before merge: expected {expected_base_hash}, found {actual_base_hash}")
    owners: dict[str, str] = {}
    for patch in patches:
        for path in patch.touched_files:
            previous = owners.get(path)
            if previous is not None:
                raise ParallelMergeError(f"parallel write conflict on {path}: {previous} and {patch.stage_id}")
            owners[path] = patch.stage_id
    ordered = sorted(patches, key=lambda item: item.stage_id)
    for patch in ordered:
        if patch.path.stat().st_size:
            _git(base, "apply", "--check", str(patch.path))
    merged: list[dict[str, object]] = []
    for patch in ordered:
        fencing_check()
        if patch.path.stat().st_size:
            _git(base, "apply", str(patch.path))
        merged.append(
            {
                "stage_id": patch.stage_id,
                "patch": str(patch.path),
                "patch_hash": patch.patch_hash,
                "touched_files": list(patch.touched_files),
            }
        )
    return merged


def cleanup_worker_workspaces(workers_root: Path) -> None:
    resolved = workers_root.resolve()
    if not resolved.exists() or resolved.name != "parallel_worktrees":
        return
    shutil.rmtree(resolved, onexc=_remove_readonly)


def _workspace_files(path: Path) -> list[Path]:
    return sorted(
        file_path
        for file_path in path.rglob("*")
        if file_path.is_file() and ".git" not in file_path.parts and ".muxdev" not in file_path.parts and "__pycache__" not in file_path.parts
    )


def _status_paths(output: str) -> list[str]:
    paths: list[str] = []
    for line in output.splitlines():
        if len(line) < 4:
            continue
        value = line[3:].strip()
        if " -> " in value:
            value = value.split(" -> ", 1)[1]
        if value:
            paths.append(value.replace("\\", "/"))
    return paths


def _safe_stage_id(stage_id: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in stage_id)
    if not safe:
        raise ParallelMergeError("stage id cannot be mapped to a worker directory")
    return safe


def _remove_readonly(function: Callable[[str], object], path: str, _: BaseException) -> None:
    os.chmod(path, stat.S_IWRITE)
    function(path)


def _git(cwd: Path, *args: str, allow_failure: bool = False) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **hidden_subprocess_kwargs(),
    )
    if result.returncode != 0 and not allow_failure:
        raise ParallelMergeError(f"git {' '.join(args)} failed: {(result.stderr or result.stdout).strip()}")
    return result.stdout or ""
