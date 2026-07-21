"""Conflict-safe workspace snapshots, change sets, diffs, and application."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from ..core.platforms import hidden_subprocess_kwargs


IGNORED_PARTS = {".git", ".muxdev", ".pytest_cache", ".test_workspaces", "__pycache__"}


@dataclass(frozen=True)
class FileManifestEntry:
    path: str
    kind: Literal["file", "symlink"]
    size: int
    digest: str


@dataclass(frozen=True)
class WorkspaceSnapshot:
    root: str
    files: dict[str, FileManifestEntry]
    digest: str

    def to_dict(self) -> dict[str, object]:
        return {
            "root": self.root,
            "digest": self.digest,
            "files": {path: asdict(item) for path, item in sorted(self.files.items())},
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "WorkspaceSnapshot":
        raw_files = value.get("files") if isinstance(value.get("files"), dict) else {}
        files = {
            str(path): FileManifestEntry(**item)
            for path, item in raw_files.items()
            if isinstance(item, dict)
        }
        return cls(root=str(value.get("root") or ""), files=files, digest=str(value.get("digest") or ""))


@dataclass(frozen=True)
class ChangeSetOperation:
    operation: Literal["add", "modify", "delete", "rename"]
    path: str
    expected_before_hash: str | None
    after_hash: str | None
    previous_path: str | None = None
    artifact: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ChangeSet:
    before_digest: str
    after_digest: str
    operations: tuple[ChangeSetOperation, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": "muxdev.changeset.v1",
            "before_digest": self.before_digest,
            "after_digest": self.after_digest,
            "operations": [item.to_dict() for item in self.operations],
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "ChangeSet":
        raw_operations = value.get("operations") if isinstance(value.get("operations"), list) else []
        operations = tuple(
            ChangeSetOperation(**item)
            for item in raw_operations
            if isinstance(item, dict)
        )
        return cls(
            before_digest=str(value.get("before_digest") or ""),
            after_digest=str(value.get("after_digest") or ""),
            operations=operations,
        )


class WorkspaceConflictError(RuntimeError):
    def __init__(self, conflicts: list[str]) -> None:
        self.conflicts = conflicts
        super().__init__("workspace changed since run start: " + "; ".join(conflicts))


def snapshot_workspace(root: Path) -> WorkspaceSnapshot:
    root = root.resolve()
    files: dict[str, FileManifestEntry] = {}
    if not root.exists():
        return WorkspaceSnapshot(str(root), {}, _manifest_digest({}))
    paths = _git_snapshot_paths(root) or sorted(root.rglob("*"), key=lambda item: item.as_posix())
    for path in paths:
        relative = path.relative_to(root).as_posix()
        if not _safe_relative(relative):
            continue
        if path.is_symlink():
            target_path = path.resolve(strict=False)
            if target_path != root and root not in target_path.parents:
                raise ValueError(f"workspace symlink escapes root: {relative}")
            target = str(path.readlink())
            files[relative] = FileManifestEntry(
                path=relative,
                kind="symlink",
                size=len(target.encode()),
                digest=_text_digest(target),
            )
        elif path.is_dir():
            continue
        elif path.is_file():
            files[relative] = FileManifestEntry(
                path=relative,
                kind="file",
                size=path.stat().st_size,
                digest=_file_digest(path),
            )
    return WorkspaceSnapshot(str(root), files, _manifest_digest(files))


def _git_snapshot_paths(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
        check=False,
        **hidden_subprocess_kwargs(),
    )
    if result.returncode != 0:
        return []
    values = result.stdout.decode("utf-8", errors="replace") if isinstance(result.stdout, bytes) else result.stdout
    return sorted((root / item for item in values.split("\0") if item), key=lambda item: item.as_posix())


def build_change_set(before: WorkspaceSnapshot, after: WorkspaceSnapshot) -> ChangeSet:
    before_paths = set(before.files)
    after_paths = set(after.files)
    deleted = set(before_paths - after_paths)
    added = set(after_paths - before_paths)
    operations: list[ChangeSetOperation] = []

    deleted_by_hash: dict[str, list[str]] = {}
    for path in deleted:
        item = before.files[path]
        if item.kind == "file":
            deleted_by_hash.setdefault(item.digest, []).append(path)
    for new_path in sorted(tuple(added)):
        new_item = after.files[new_path]
        candidates = deleted_by_hash.get(new_item.digest, []) if new_item.kind == "file" else []
        if not candidates:
            continue
        old_path = sorted(candidates)[0]
        candidates.remove(old_path)
        deleted.remove(old_path)
        added.remove(new_path)
        operations.append(ChangeSetOperation(
            operation="rename",
            path=new_path,
            previous_path=old_path,
            expected_before_hash=before.files[old_path].digest,
            after_hash=new_item.digest,
            artifact=_artifact_ref(new_path, new_item.digest),
        ))

    for path in sorted(before_paths & after_paths):
        old_item, new_item = before.files[path], after.files[path]
        if old_item == new_item:
            continue
        if old_item.kind != "file" or new_item.kind != "file":
            raise ValueError(f"unsafe symlink or type change in ChangeSet: {path}")
        operations.append(ChangeSetOperation(
            operation="modify",
            path=path,
            expected_before_hash=old_item.digest,
            after_hash=new_item.digest,
            artifact=_artifact_ref(path, new_item.digest),
        ))
    for path in sorted(added):
        item = after.files[path]
        if item.kind != "file":
            raise ValueError(f"unsafe symlink addition in ChangeSet: {path}")
        operations.append(ChangeSetOperation(
            operation="add",
            path=path,
            expected_before_hash=None,
            after_hash=item.digest,
            artifact=_artifact_ref(path, item.digest),
        ))
    for path in sorted(deleted):
        item = before.files[path]
        if item.kind != "file":
            raise ValueError(f"unsafe symlink deletion in ChangeSet: {path}")
        operations.append(ChangeSetOperation(
            operation="delete",
            path=path,
            expected_before_hash=item.digest,
            after_hash=None,
        ))
    operations.sort(key=lambda item: (item.path, item.operation))
    return ChangeSet(before.digest, after.digest, tuple(operations))


def apply_change_set(change_set: ChangeSet, source_root: Path, workspace: Path) -> list[str]:
    """Preflight every hash and path before mutating the user's workspace."""
    source_root = source_root.resolve()
    workspace = workspace.resolve()
    conflicts: list[str] = []
    for operation in change_set.operations:
        destination = _contained_path(workspace, operation.path)
        _reject_symlink_chain(workspace, destination)
        current_hash = _file_digest(destination) if destination.is_file() and not destination.is_symlink() else None
        destination_expected = None if operation.operation in {"add", "rename"} else operation.expected_before_hash
        if current_hash != destination_expected:
            conflicts.append(
                f"{operation.path}: expected {destination_expected or 'absent'}, "
                f"found {current_hash or 'absent/non-file'}"
            )
        if operation.operation == "rename" and operation.previous_path:
            previous = _contained_path(workspace, operation.previous_path)
            _reject_symlink_chain(workspace, previous)
            previous_hash = _file_digest(previous) if previous.is_file() and not previous.is_symlink() else None
            if previous_hash != operation.expected_before_hash:
                conflicts.append(
                    f"{operation.previous_path}: expected {operation.expected_before_hash}, "
                    f"found {previous_hash or 'absent/non-file'}"
                )
            if destination.exists():
                conflicts.append(f"{operation.path}: rename target already exists")
        if operation.operation in {"add", "modify", "rename"}:
            source = _contained_path(source_root, operation.path)
            _reject_symlink_chain(source_root, source)
            source_hash = _file_digest(source) if source.is_file() and not source.is_symlink() else None
            if source_hash != operation.after_hash:
                conflicts.append(
                    f"{operation.path}: staged content expected {operation.after_hash}, "
                    f"found {source_hash or 'absent/non-file'}"
                )
    if conflicts:
        raise WorkspaceConflictError(conflicts)

    applied: list[str] = []
    for operation in change_set.operations:
        if operation.operation == "rename" and operation.previous_path:
            previous = _contained_path(workspace, operation.previous_path)
            previous.unlink()
            applied.append(operation.previous_path)
        destination = _contained_path(workspace, operation.path)
        if operation.operation == "delete":
            destination.unlink()
        else:
            source = _contained_path(source_root, operation.path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        applied.append(operation.path)
    return applied


def git_output(worktree: Path, arguments: list[str]) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=worktree,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **hidden_subprocess_kwargs(),
    )
    return result.stdout or ""


def changed_files(worktree: Path) -> list[str]:
    tracked = git_output(worktree, ["diff", "--name-only", "--diff-filter=ACMRTD", "--", "."])
    untracked = git_output(worktree, ["ls-files", "--others", "--exclude-standard"])
    paths = {line.strip().replace("\\", "/") for line in (tracked + "\n" + untracked).splitlines() if line.strip()}
    return sorted(path for path in paths if _safe_relative(path))


def diff_text(worktree: Path) -> str:
    content = git_output(worktree, ["diff", "--binary", "--", "."])
    for relative in changed_files(worktree):
        path = worktree / relative
        if relative in content or not path.is_file():
            continue
        data = path.read_bytes()
        binary = b"\0" in data
        try:
            body = "" if binary else data.decode("utf-8")
        except UnicodeDecodeError:
            body = ""
            binary = True
        if binary:
            content += (
                f"\ndiff --git a/{relative} b/{relative}\nnew file mode 100644\n"
                f"Binary files /dev/null and b/{relative} differ\n"
                f"# muxdev-content-digest: {_file_digest(path)}\n"
            )
            continue
        lines = body.splitlines()
        content += f"\ndiff --git a/{relative} b/{relative}\nnew file mode 100644\n--- /dev/null\n+++ b/{relative}\n"
        content += f"@@ -0,0 +1,{len(lines)} @@\n" + "\n".join(f"+{line}" for line in lines) + "\n"
    return content


def apply_changes(worktree: Path, workspace: Path) -> list[str]:
    """Compatibility wrapper for callers that do not retain a start snapshot."""
    before = snapshot_workspace(workspace)
    after = snapshot_workspace(worktree)
    return apply_change_set(build_change_set(before, after), worktree, workspace)


def _safe_relative(value: str) -> bool:
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts and not any(part in IGNORED_PARTS for part in path.parts)


def _contained_path(root: Path, relative: str) -> Path:
    if not _safe_relative(relative):
        raise ValueError(f"unsafe workspace path: {relative}")
    raw = root.joinpath(*Path(relative).parts)
    _reject_symlink_chain(root, raw)
    candidate = raw.resolve(strict=False)
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"workspace path escapes root: {relative}")
    return candidate


def _reject_symlink_chain(root: Path, path: Path) -> None:
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"symlink paths are not allowed: {current.relative_to(root).as_posix()}")


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _text_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _manifest_digest(files: dict[str, FileManifestEntry]) -> str:
    payload = {
        path: {"kind": item.kind, "size": item.size, "digest": item.digest}
        for path, item in sorted(files.items())
    }
    return _text_digest(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _artifact_ref(path: str, digest: str) -> str:
    return f"workspace:{path}@{digest}"
