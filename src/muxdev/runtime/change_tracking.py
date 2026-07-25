"""Content-addressed file baselines, observed activity, and verified reconciliation."""

from __future__ import annotations

import difflib
import hashlib
import os
import sqlite3
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..core.platforms import hidden_subprocess_kwargs
from ..storage import ControlStore
from .activity import ActivityPublisher
from .workspace import WorkspaceSnapshot, build_change_set, snapshot_workspace


MAX_TEXT_PREVIEW_BYTES = 1024 * 1024
IGNORED_PARTS = {
    ".git",
    ".muxdev",
    ".pytest_cache",
    ".test_workspaces",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
}
SENSITIVE_NAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.json",
    "secrets.json",
    "id_rsa",
    "id_ed25519",
}


@dataclass(frozen=True)
class ChangeTrackingContext:
    conversation_id: str
    run_id: str
    worktree: Path
    assignment_id: str | None = None
    session_id: str | None = None
    generation: int | None = None
    author: str = "runtime"


class BlobStore:
    def __init__(self, workspace: Path) -> None:
        self.root = Path(workspace).resolve() / ".muxdev" / "blobs" / "sha256"

    def put(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        path = self.root / digest[:2] / digest
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(f".{os.getpid()}.tmp")
            temporary.write_bytes(data)
            try:
                temporary.replace(path)
            except FileExistsError:
                temporary.unlink(missing_ok=True)
        return f"sha256:{digest}"

    def get(self, digest: str) -> bytes:
        value = digest.removeprefix("sha256:")
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("invalid content digest")
        path = self.root / value[:2] / value
        if not path.is_file():
            raise FileNotFoundError(digest)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != value:
            raise RuntimeError(f"content-addressed blob failed digest verification: {digest}")
        return data


class ChangeTrackingService:
    def __init__(self, workspace: Path, store: ControlStore | None = None) -> None:
        self.workspace = Path(workspace).resolve()
        self.store = store
        self.blobs = BlobStore(self.workspace)

    def capture_baseline(self, context: ChangeTrackingContext) -> int:
        worktree = context.worktree.resolve()
        snapshot = snapshot_workspace(worktree)
        git_oids = _git_index_oids(worktree)
        dirty = _git_dirty_paths(worktree)
        own_store = self.store is None
        store = self.store or ControlStore(self.workspace)
        try:
            for relative, item in snapshot.files.items():
                path = _contained_path(worktree, relative)
                git_oid = git_oids.get(relative)
                if git_oid is None or relative in dirty:
                    self.blobs.put(_read_path_bytes(path))
                store.record_file_baseline(
                    conversation_id=context.conversation_id,
                    run_id=context.run_id,
                    path=relative,
                    existed=True,
                    blob_hash=item.digest,
                    git_oid=git_oid,
                    mode=path.lstat().st_mode,
                    size=item.size,
                )
            ActivityPublisher(store).publish(
                context.conversation_id,
                "workspace.baseline_captured",
                {
                    "run_id": context.run_id,
                    "file_count": len(snapshot.files),
                    "workspace_digest": snapshot.digest,
                },
                actor_id="runtime",
                actor_kind="runtime",
                run_id=context.run_id,
                assignment_id=context.assignment_id,
                session_id=context.session_id,
                generation=context.generation,
                capture_grade="verified",
            )
            return len(snapshot.files)
        finally:
            if own_store:
                store.close()

    def capture_snapshot_baseline(
        self,
        context: ChangeTrackingContext,
        snapshot: WorkspaceSnapshot,
    ) -> int:
        """Register a previously frozen snapshot without rereading changed files."""
        own_store = self.store is None
        store = self.store or ControlStore(self.workspace)
        try:
            git_oids = _git_index_oids(context.worktree)
            for relative, item in snapshot.files.items():
                store.record_file_baseline(
                    conversation_id=context.conversation_id,
                    run_id=context.run_id,
                    path=relative,
                    existed=True,
                    blob_hash=item.digest,
                    git_oid=git_oids.get(relative),
                    size=item.size,
                )
            return len(snapshot.files)
        finally:
            if own_store:
                store.close()

    def observe_paths(
        self,
        context: ChangeTrackingContext,
        paths: Iterable[Path | str],
    ) -> int:
        own_store = self.store is None
        store = self.store or ControlStore(self.workspace)
        count = 0
        try:
            if (
                not store.get_run(context.run_id)
                or not store.get_conversation(context.conversation_id)
            ):
                return 0
            baselines = {
                str(item["path"]): item for item in store.file_baselines(context.run_id)
            }
            latest = _latest_changes(store.file_changes(context.run_id))
            for value in paths:
                relative = _relative_change_path(context.worktree, value)
                if not relative or _ignored(relative):
                    continue
                target = _contained_path(context.worktree, relative)
                baseline = baselines.get(relative)
                previous = latest.get(relative)
                before_hash = (
                    str(previous["after_hash"]) if previous and previous.get("after_hash")
                    else str(baseline["blob_hash"])
                    if baseline else None
                )
                if not baseline:
                    store.record_file_baseline(
                        conversation_id=context.conversation_id,
                        run_id=context.run_id,
                        path=relative,
                        existed=False,
                        blob_hash=None,
                    )
                if target.is_file() and not target.is_symlink():
                    data = target.read_bytes()
                    after_hash = self.blobs.put(data)
                    kind = "modify" if baseline and int(baseline["existed"]) else "add"
                elif baseline and int(baseline["existed"]):
                    data = None
                    after_hash = None
                    kind = "delete"
                else:
                    continue
                if before_hash == after_hash:
                    continue
                patch, additions, deletions = self._patch(
                    context.worktree, baseline, previous, relative, data
                )
                record = store.record_file_change(
                    conversation_id=context.conversation_id,
                    run_id=context.run_id,
                    assignment_id=context.assignment_id,
                    session_id=context.session_id,
                    generation=context.generation,
                    path=relative,
                    kind=kind,
                    before_hash=before_hash,
                    after_hash=after_hash,
                    patch=patch,
                    additions=additions,
                    deletions=deletions,
                    author=context.author,
                    capture_grade="observed",
                )
                latest[relative] = record
                ActivityPublisher(store).publish(
                    context.conversation_id,
                    "file.changed",
                    {
                        "change_id": record["change_id"],
                        "path": relative,
                        "kind": kind,
                        "additions": additions,
                        "deletions": deletions,
                    },
                    actor_id=context.author,
                    actor_kind="agent" if context.author != "runtime" else "runtime",
                    run_id=context.run_id,
                    assignment_id=context.assignment_id,
                    session_id=context.session_id,
                    generation=context.generation,
                    capture_grade="observed",
                )
                count += 1
            if count:
                store.mark_verification_attempts(context.run_id, freshness="stale")
            return count
        finally:
            if own_store:
                store.close()

    def reconcile(self, context: ChangeTrackingContext) -> list[dict[str, Any]]:
        own_store = self.store is None
        store = self.store or ControlStore(self.workspace)
        try:
            baselines = {
                str(item["path"]): item for item in store.file_baselines(context.run_id)
            }
            before = WorkspaceSnapshot(
                root=str(context.worktree),
                files={},
                digest="",
            )
            from .workspace import FileManifestEntry

            before_files = {
                path: FileManifestEntry(
                    path=path,
                    kind="file",
                    size=int(item["size"]),
                    digest=str(item["blob_hash"]),
                )
                for path, item in baselines.items()
                if int(item["existed"])
            }
            before = WorkspaceSnapshot(
                root=str(context.worktree),
                files=before_files,
                digest=_manifest_digest(before_files),
            )
            after = snapshot_workspace(context.worktree)
            change_set = build_change_set(before, after)
            changes: list[dict[str, object]] = []
            for operation in change_set.operations:
                relative = operation.path
                target = _contained_path(context.worktree, relative)
                data = (
                    target.read_bytes()
                    if target.is_file() and not target.is_symlink()
                    else None
                )
                after_hash = self.blobs.put(data) if data is not None else None
                baseline = baselines.get(
                    operation.previous_path or operation.path
                )
                patch, additions, deletions = self._patch(
                    context.worktree, baseline, None, relative, data
                )
                changes.append({
                    "path": relative,
                    "previous_path": operation.previous_path,
                    "kind": operation.operation,
                    "before_hash": operation.expected_before_hash,
                    "after_hash": after_hash or operation.after_hash,
                    "patch": patch,
                    "additions": additions,
                    "deletions": deletions,
                    "author": context.author,
                })
            records = store.replace_verified_file_changes(
                conversation_id=context.conversation_id,
                run_id=context.run_id,
                assignment_id=context.assignment_id,
                session_id=context.session_id,
                generation=context.generation,
                changes=changes,
            )
            ActivityPublisher(store).publish(
                context.conversation_id,
                "workspace.reconciled",
                {
                    "run_id": context.run_id,
                    "file_count": len(records),
                    "before_digest": change_set.before_digest,
                    "after_digest": change_set.after_digest,
                },
                actor_id="runtime",
                actor_kind="runtime",
                run_id=context.run_id,
                assignment_id=context.assignment_id,
                session_id=context.session_id,
                generation=context.generation,
                capture_grade="verified",
            )
            return records
        finally:
            if own_store:
                store.close()

    def rollback(self, context: ChangeTrackingContext) -> list[str]:
        own_store = self.store is None
        store = self.store or ControlStore(self.workspace)
        try:
            baselines = {
                str(item["path"]): item for item in store.file_baselines(context.run_id)
            }
            verified = _latest_changes(
                store.file_changes(context.run_id, capture_grade="verified")
            )
            conflicts: list[str] = []
            for path, change in verified.items():
                target = _contained_path(context.worktree, path)
                current = _file_digest(target) if target.is_file() else None
                if current != change.get("after_hash"):
                    conflicts.append(
                        f"{path}: expected {change.get('after_hash') or 'absent'}, "
                        f"found {current or 'absent'}"
                    )
            if conflicts:
                raise RollbackConflictError(conflicts)

            restored: list[str] = []
            for path in sorted(verified):
                change = verified[path]
                baseline_path = str(change.get("previous_path") or path)
                baseline = baselines.get(baseline_path)
                target = _contained_path(context.worktree, path)
                if not baseline or not int(baseline["existed"]):
                    target.unlink(missing_ok=True)
                else:
                    data = self._baseline_bytes(context.worktree, baseline)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    if baseline.get("mode") is not None:
                        try:
                            target.chmod(int(baseline["mode"]))
                        except OSError:
                            pass
                if change.get("kind") == "rename" and baseline_path != path:
                    old_target = _contained_path(context.worktree, baseline_path)
                    data = self._baseline_bytes(context.worktree, baseline)
                    old_target.parent.mkdir(parents=True, exist_ok=True)
                    old_target.write_bytes(data)
                    target.unlink(missing_ok=True)
                restored.append(path)
            ActivityPublisher(store).publish(
                context.conversation_id,
                "workspace.rolled_back",
                {"run_id": context.run_id, "files": restored},
                actor_id="developer",
                actor_kind="developer",
                run_id=context.run_id,
                assignment_id=context.assignment_id,
                session_id=context.session_id,
                generation=context.generation,
                capture_grade="verified",
            )
            return restored
        finally:
            if own_store:
                store.close()

    def _patch(
        self,
        worktree: Path,
        baseline: Mapping[str, Any] | None,
        previous: Mapping[str, Any] | None,
        relative: str,
        after: bytes | None,
    ) -> tuple[str, int, int]:
        if _sensitive(relative):
            return "", 0, 0
        before: bytes | None = None
        if previous and previous.get("after_hash"):
            try:
                before = self.blobs.get(str(previous["after_hash"]))
            except FileNotFoundError:
                before = None
        elif baseline and int(baseline.get("existed") or 0):
            before = self._baseline_bytes(worktree, baseline)
        if any(
            value is not None and (
                len(value) > MAX_TEXT_PREVIEW_BYTES or b"\0" in value
            )
            for value in (before, after)
        ):
            return "", 0, 0
        try:
            before_text = (before or b"").decode("utf-8").splitlines()
            after_text = (after or b"").decode("utf-8").splitlines()
        except UnicodeDecodeError:
            return "", 0, 0
        lines = list(difflib.unified_diff(
            before_text,
            after_text,
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
            lineterm="",
        ))
        additions = sum(
            1 for line in lines if line.startswith("+") and not line.startswith("+++")
        )
        deletions = sum(
            1 for line in lines if line.startswith("-") and not line.startswith("---")
        )
        return "\n".join(lines), additions, deletions

    def _baseline_bytes(
        self, worktree: Path, baseline: Mapping[str, Any]
    ) -> bytes:
        if baseline.get("blob_hash"):
            try:
                return self.blobs.get(str(baseline["blob_hash"]))
            except FileNotFoundError:
                pass
        git_oid = str(baseline.get("git_oid") or "")
        if not git_oid:
            raise FileNotFoundError(f"baseline content is unavailable: {baseline['path']}")
        result = subprocess.run(
            ["git", "cat-file", "blob", git_oid],
            cwd=worktree,
            capture_output=True,
            check=False,
            **hidden_subprocess_kwargs(),
        )
        if result.returncode != 0:
            raise FileNotFoundError(f"Git baseline blob is unavailable: {git_oid}")
        return result.stdout if isinstance(result.stdout, bytes) else result.stdout.encode()


class RollbackConflictError(RuntimeError):
    def __init__(self, conflicts: list[str]) -> None:
        self.conflicts = conflicts
        super().__init__("workspace changed after review: " + "; ".join(conflicts))


class ChangeMonitor:
    def __init__(self, workspace: Path, context: ChangeTrackingContext) -> None:
        self.workspace = Path(workspace).resolve()
        self.context = context
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        try:
            from watchfiles import watch
        except ImportError:
            self._poll()
            return
        for changes in watch(
            self.context.worktree,
            debounce=200,
            rust_timeout=500,
            yield_on_timeout=True,
            stop_event=self._stop,
        ):
            if self._stop.is_set():
                break
            paths = [Path(path) for _, path in changes]
            if paths:
                try:
                    ChangeTrackingService(self.workspace).observe_paths(
                        self.context, paths
                    )
                except (
                    FileNotFoundError,
                    NotADirectoryError,
                    OSError,
                    sqlite3.IntegrityError,
                ):
                    if not self.context.worktree.is_dir():
                        return

    def _poll(self) -> None:
        previous = snapshot_workspace(self.context.worktree)
        while not self._stop.wait(0.5):
            if not self.context.worktree.is_dir():
                return
            current = snapshot_workspace(self.context.worktree)
            changed = {
                path
                for path in set(previous.files) | set(current.files)
                if previous.files.get(path) != current.files.get(path)
            }
            if changed:
                try:
                    ChangeTrackingService(self.workspace).observe_paths(
                        self.context, changed
                    )
                except (
                    FileNotFoundError,
                    NotADirectoryError,
                    OSError,
                    sqlite3.IntegrityError,
                ):
                    if not self.context.worktree.is_dir():
                        return
            previous = current


class ChangeMonitorManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._monitors: dict[str, ChangeMonitor] = {}

    def start(self, workspace: Path, context: ChangeTrackingContext) -> None:
        with self._lock:
            existing = self._monitors.pop(context.run_id, None)
            if existing:
                existing.stop()
            monitor = ChangeMonitor(workspace, context)
            self._monitors[context.run_id] = monitor
            monitor.start()

    def stop(self, run_id: str) -> None:
        with self._lock:
            monitor = self._monitors.pop(run_id, None)
        if monitor:
            monitor.stop()


change_monitors = ChangeMonitorManager()


def _git_index_oids(root: Path) -> dict[str, str]:
    result = subprocess.run(
        ["git", "ls-files", "-s", "-z"],
        cwd=root,
        capture_output=True,
        check=False,
        **hidden_subprocess_kwargs(),
    )
    if result.returncode != 0:
        return {}
    raw = result.stdout.decode("utf-8", errors="replace")
    values: dict[str, str] = {}
    for entry in raw.split("\0"):
        if not entry or "\t" not in entry:
            continue
        metadata, path = entry.split("\t", 1)
        parts = metadata.split()
        if len(parts) >= 2:
            values[path.replace("\\", "/")] = parts[1]
    return values


def _git_dirty_paths(root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=root,
        capture_output=True,
        check=False,
        **hidden_subprocess_kwargs(),
    )
    if result.returncode != 0:
        return set()
    raw = result.stdout.decode("utf-8", errors="replace")
    return {
        item[3:].split(" -> ")[-1].replace("\\", "/")
        for item in raw.split("\0")
        if len(item) > 3
    }


def _git_digest(value: object) -> str | None:
    return f"git:{value}" if value else None


def _latest_changes(changes: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for change in changes:
        result[str(change["path"])] = dict(change)
    return result


def _relative_change_path(root: Path, value: Path | str) -> str | None:
    raw = Path(value)
    try:
        resolved = raw.resolve()
        relative = resolved.relative_to(root.resolve())
    except ValueError:
        if raw.is_absolute():
            return None
        relative = raw
    text = relative.as_posix()
    return text if text and text != "." else None


def _ignored(relative: str) -> bool:
    path = Path(relative)
    return path.is_absolute() or ".." in path.parts or any(
        part in IGNORED_PARTS for part in path.parts
    )


def _sensitive(relative: str) -> bool:
    lowered = relative.lower()
    name = Path(lowered).name
    return (
        name in SENSITIVE_NAMES
        or name.endswith((".pem", ".key", ".p12", ".pfx"))
        or any(part in {"secrets", ".ssh", ".aws", ".azure"} for part in Path(lowered).parts)
    )


def _contained_path(root: Path, relative: str) -> Path:
    if _ignored(relative):
        raise ValueError(f"unsafe workspace path: {relative}")
    root = root.resolve()
    raw = root.joinpath(*Path(relative).parts)
    current = root
    for part in Path(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"symlink paths are not allowed: {relative}")
    candidate = raw.resolve(strict=False)
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"workspace path escapes root: {relative}")
    return candidate


def _read_path_bytes(path: Path) -> bytes:
    if path.is_symlink():
        return str(path.readlink()).encode()
    return path.read_bytes()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _manifest_digest(files: Mapping[str, Any]) -> str:
    digest = hashlib.sha256()
    for path, item in sorted(files.items()):
        digest.update(path.encode())
        digest.update(str(item.digest).encode())
        digest.update(str(item.size).encode())
    return "sha256:" + digest.hexdigest()


__all__ = [
    "BlobStore",
    "ChangeMonitor",
    "ChangeMonitorManager",
    "ChangeTrackingContext",
    "ChangeTrackingService",
    "RollbackConflictError",
    "change_monitors",
]
