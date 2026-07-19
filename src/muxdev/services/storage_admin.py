"""Safe status, backup, verification, restore, and replay operations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator
from uuid import uuid4

from ..config.loader import path_config
from ..core.projects import resolve_project_root
from ..daemon.paths import DaemonPaths, default_daemon_paths
from ..storage import Blackboard, MemoryStore


ARCHIVE_FORMAT = "muxdev.storage-backup.v1"
BACKUP_ID_PATTERN = re.compile(r"^backup_[0-9TZ]+_[0-9a-f]{8}$")
SUPPORTED_SCHEMA = {"blackboard": 7, "memory": 1}


@dataclass(frozen=True)
class DatabaseTarget:
    logical_name: str
    component: str
    path: Path

    def to_dict(self) -> dict[str, str]:
        return {"logical_name": self.logical_name, "component": self.component, "path": str(self.path)}


class StorageArchiveError(ValueError):
    """Raised for unsafe, corrupt, or incompatible storage archives."""


def discover_storage_targets(
    *,
    workspace: Path,
    daemon_paths: DaemonPaths | None = None,
    scope: str = "all",
    include_missing: bool = False,
) -> list[DatabaseTarget]:
    if scope not in {"daemon", "project", "all"}:
        raise ValueError(f"invalid storage scope: {scope}")
    root = resolve_project_root(workspace)
    paths = daemon_paths or default_daemon_paths()
    targets: list[DatabaseTarget] = []
    if scope in {"daemon", "all"}:
        targets.append(DatabaseTarget("daemon", "blackboard", paths.db_path.expanduser().resolve()))
    if scope in {"project", "all"}:
        runtime_root = path_config(root, "runtime_root").expanduser().resolve()
        targets.append(DatabaseTarget("project-memory", "memory", runtime_root / "memory.sqlite"))
        runs_root = path_config(root, "runs").expanduser().resolve()
        if runs_root.is_dir():
            for database in sorted(runs_root.glob("*/blackboard.sqlite")):
                targets.append(DatabaseTarget(f"project-run:{database.parent.name}", "blackboard", database.resolve()))
    unique: dict[Path, DatabaseTarget] = {target.path: target for target in targets}
    values = sorted(unique.values(), key=lambda item: item.logical_name)
    return values if include_missing else [target for target in values if target.path.is_file()]


def storage_status(
    *,
    workspace: Path,
    daemon_paths: DaemonPaths | None = None,
    scope: str = "all",
    include_paths: bool = True,
) -> dict[str, Any]:
    targets = discover_storage_targets(
        workspace=workspace,
        daemon_paths=daemon_paths,
        scope=scope,
        include_missing=True,
    )
    databases: list[dict[str, Any]] = []
    for target in targets:
        if not target.path.is_file():
            row: dict[str, Any] = {
                "logical_name": target.logical_name,
                "component": target.component,
                "status": "missing",
                "migration_required": False,
            }
            if include_paths:
                row["path"] = str(target.path)
            databases.append(row)
            continue
        try:
            if target.component == "memory":
                store: Any = MemoryStore(workspace, db_path=target.path, readonly=True)
            else:
                store = Blackboard(target.path.parent, db_path=target.path, readonly=True)
            with store:
                row = store.engine.health(check_integrity=True).to_dict(include_path=include_paths)
            row["logical_name"] = target.logical_name
        except Exception as exc:
            row = {
                "logical_name": target.logical_name,
                "component": target.component,
                "status": "error",
                "error": type(exc).__name__,
            }
            if include_paths:
                row["path"] = str(target.path)
        databases.append(row)
    overall = "healthy"
    states = {str(row.get("status")) for row in databases}
    if "error" in states or "migration_required" in states:
        overall = "error"
    elif "degraded" in states:
        overall = "degraded"
    return {"scope": scope, "status": overall, "databases": databases}


def create_storage_backup(
    *,
    workspace: Path,
    daemon_paths: DaemonPaths | None = None,
    scope: str = "all",
    output: Path | None = None,
) -> dict[str, Any]:
    paths = daemon_paths or default_daemon_paths()
    targets = discover_storage_targets(workspace=workspace, daemon_paths=paths, scope=scope)
    if not targets:
        raise FileNotFoundError(f"no databases found for storage scope: {scope}")
    backup_id = _new_backup_id()
    archive = Path(output).expanduser().resolve() if output else controlled_backup_path(paths, backup_id)
    if archive.suffix.lower() != ".zip":
        archive = archive.with_suffix(".zip")
    archive.parent.mkdir(parents=True, exist_ok=True)
    temporary_archive = archive.with_name(f".{archive.name}.tmp-{uuid4().hex}")
    entries: list[dict[str, Any]] = []
    try:
        with _staging_directory(archive.parent, "backup") as temp_root:
            for index, target in enumerate(targets, start=1):
                snapshot = temp_root / f"{index:03d}-{_safe_name(target.logical_name)}.sqlite"
                _online_backup(target.path, snapshot)
                metadata = _database_metadata(snapshot, target.component)
                archive_name = f"databases/{snapshot.name}"
                entries.append(
                    {
                        "logical_name": target.logical_name,
                        "component": target.component,
                        "archive_name": archive_name,
                        "schema_version": metadata["schema_version"],
                        "size": snapshot.stat().st_size,
                        "sha256": _sha256(snapshot),
                    }
                )
            manifest = {
                "format": ARCHIVE_FORMAT,
                "backup_id": backup_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "scope": scope,
                "databases": entries,
            }
            with zipfile.ZipFile(temporary_archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
                bundle.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
                for item in entries:
                    source = temp_root / Path(str(item["archive_name"])).name
                    bundle.write(source, str(item["archive_name"]))
        os.replace(temporary_archive, archive)
    finally:
        temporary_archive.unlink(missing_ok=True)
    return {
        "backup_id": backup_id,
        "scope": scope,
        "archive": str(archive),
        "database_count": len(entries),
        "sha256": _sha256(archive),
        "databases": [{key: value for key, value in item.items() if key != "archive_name"} for item in entries],
    }


def verify_storage_backup(archive: Path) -> dict[str, Any]:
    path = Path(archive).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"backup archive not found: {path}")
    errors: list[str] = []
    checked: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(path, "r") as bundle:
            _validate_member_names(bundle.infolist())
            try:
                manifest = json.loads(bundle.read("manifest.json"))
            except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise StorageArchiveError("backup manifest is missing or invalid") from exc
            if manifest.get("format") != ARCHIVE_FORMAT:
                raise StorageArchiveError("unsupported backup archive format")
            entries = manifest.get("databases")
            if not isinstance(entries, list) or not entries:
                raise StorageArchiveError("backup manifest contains no databases")
            logical_names = [str(item.get("logical_name") or "") for item in entries if isinstance(item, dict)]
            if len(logical_names) != len(set(logical_names)) or any(not name for name in logical_names):
                raise StorageArchiveError("backup manifest contains duplicate or empty logical names")
            declared_members = {
                str(item.get("archive_name") or "") for item in entries if isinstance(item, dict)
            }
            actual_members = {info.filename for info in bundle.infolist()}
            if actual_members != {"manifest.json", *declared_members}:
                raise StorageArchiveError("backup contains undeclared or missing members")
            with _staging_directory(path.parent, "verify") as temp_root:
                for index, item in enumerate(entries, start=1):
                    if not isinstance(item, dict):
                        errors.append(f"database entry {index} is invalid")
                        continue
                    member = str(item.get("archive_name") or "")
                    try:
                        raw = bundle.read(member)
                    except KeyError:
                        errors.append(f"missing database member: {member}")
                        continue
                    actual_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
                    if actual_hash != item.get("sha256"):
                        errors.append(f"checksum mismatch: {member}")
                        continue
                    target = temp_root / f"verify-{index}.sqlite"
                    target.write_bytes(raw)
                    component = str(item.get("component") or "")
                    metadata = _database_metadata(target, component)
                    if metadata["integrity"] != "ok":
                        errors.append(f"integrity check failed: {member}")
                    declared_version = int(item.get("schema_version") or 0)
                    if metadata["schema_version"] != declared_version:
                        errors.append(f"schema version mismatch: {member}")
                    supported = SUPPORTED_SCHEMA.get(component)
                    if supported is None or declared_version > supported:
                        errors.append(f"unsupported schema: {component}@{declared_version}")
                    checked.append(
                        {
                            "logical_name": item.get("logical_name"),
                            "component": component,
                            "schema_version": declared_version,
                            "size": len(raw),
                            "integrity": metadata["integrity"],
                        }
                    )
    except zipfile.BadZipFile as exc:
        raise StorageArchiveError("backup is not a valid ZIP archive") from exc
    return {
        "backup_id": manifest.get("backup_id"),
        "valid": not errors,
        "archive": str(path),
        "database_count": len(checked),
        "databases": checked,
        "errors": errors,
    }


def restore_storage_backup(
    archive: Path,
    *,
    workspace: Path,
    daemon_paths: DaemonPaths | None = None,
    daemon_running: bool,
) -> dict[str, Any]:
    if daemon_running:
        raise RuntimeError("stop the muxdev daemon before restoring storage")
    verification = verify_storage_backup(archive)
    if not verification["valid"]:
        raise StorageArchiveError("backup verification failed: " + "; ".join(verification["errors"]))
    paths = daemon_paths or default_daemon_paths()
    archive_path = Path(archive).expanduser().resolve()
    with zipfile.ZipFile(archive_path, "r") as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        targets = _restore_targets(manifest.get("databases") or [], workspace=workspace, daemon_paths=paths)
        existing = [target for _, target in targets if target.path.is_file()]
        pre_restore = None
        if existing:
            pre_restore = create_storage_backup(
                workspace=workspace,
                daemon_paths=paths,
                scope=str(manifest.get("scope") or "all"),
                output=archive_path.parent / f"pre-restore-{_new_backup_id()}.zip",
            )
        rollback_files: dict[Path, Path] = {}
        installed: list[Path] = []
        try:
            for item, target in targets:
                target.path.parent.mkdir(parents=True, exist_ok=True)
                raw = bundle.read(str(item["archive_name"]))
                staged = target.path.with_name(f".{target.path.name}.restore-{uuid4().hex}")
                staged.write_bytes(raw)
                metadata = _database_metadata(staged, target.component)
                if metadata["integrity"] != "ok":
                    staged.unlink(missing_ok=True)
                    raise StorageArchiveError(f"staged database failed integrity check: {target.logical_name}")
                if target.path.exists():
                    rollback = target.path.with_name(f".{target.path.name}.rollback-{uuid4().hex}")
                    os.replace(target.path, rollback)
                    rollback_files[target.path] = rollback
                for suffix in ("-wal", "-shm"):
                    sidecar = Path(str(target.path) + suffix)
                    if sidecar.exists():
                        rollback = sidecar.with_name(f".{sidecar.name}.rollback-{uuid4().hex}")
                        os.replace(sidecar, rollback)
                        rollback_files[sidecar] = rollback
                os.replace(staged, target.path)
                installed.append(target.path)
        except BaseException:
            for target in reversed(installed):
                target.unlink(missing_ok=True)
            for target, rollback in rollback_files.items():
                if rollback.exists():
                    os.replace(rollback, target)
            raise
        else:
            for rollback in rollback_files.values():
                rollback.unlink(missing_ok=True)
    return {
        "restored": True,
        "backup_id": verification.get("backup_id"),
        "database_count": len(targets),
        "pre_restore_backup": pre_restore.get("archive") if pre_restore else None,
    }


def controlled_backup_path(paths: DaemonPaths, backup_id: str) -> Path:
    if not BACKUP_ID_PATTERN.fullmatch(backup_id):
        raise ValueError("invalid backup id")
    root = (paths.data_dir / "backups").expanduser().resolve()
    candidate = (root / f"{backup_id}.zip").resolve()
    if candidate.parent != root:
        raise ValueError("backup id escapes controlled backup directory")
    return candidate


def replay_run_storage(
    run_id: str,
    *,
    workspace: Path,
    daemon_paths: DaemonPaths | None = None,
) -> dict[str, Any]:
    for target in discover_storage_targets(
        workspace=workspace,
        daemon_paths=daemon_paths,
        scope="all",
    ):
        if target.component != "blackboard":
            continue
        try:
            with Blackboard(target.path.parent, db_path=target.path, readonly=True) as board:
                board.get_run(run_id)
                payload = board.replay_run(run_id)
                payload["source"] = target.logical_name
                return payload
        except FileNotFoundError:
            continue
    raise FileNotFoundError(f"run not found in configured storage: {run_id}")


def verify_controlled_backup(paths: DaemonPaths, backup_id: str) -> dict[str, Any]:
    return verify_storage_backup(controlled_backup_path(paths, backup_id))


def _restore_targets(entries: Iterable[dict[str, Any]], *, workspace: Path, daemon_paths: DaemonPaths) -> list[tuple[dict[str, Any], DatabaseTarget]]:
    root = resolve_project_root(workspace)
    runtime_root = path_config(root, "runtime_root").expanduser().resolve()
    runs_root = path_config(root, "runs").expanduser().resolve()
    results: list[tuple[dict[str, Any], DatabaseTarget]] = []
    for item in entries:
        logical = str(item.get("logical_name") or "")
        component = str(item.get("component") or "")
        if logical == "daemon" and component == "blackboard":
            target = daemon_paths.db_path.expanduser().resolve()
        elif logical == "project-memory" and component == "memory":
            target = runtime_root / "memory.sqlite"
        elif logical.startswith("project-run:") and component == "blackboard":
            run_id = logical.split(":", 1)[1]
            if not run_id or Path(run_id).name != run_id or "/" in run_id or "\\" in run_id:
                raise StorageArchiveError("invalid project run id in backup manifest")
            target = (runs_root / run_id / "blackboard.sqlite").resolve()
            if target.parent.parent != runs_root:
                raise StorageArchiveError("project run target escapes runs directory")
        else:
            raise StorageArchiveError(f"unsupported restore target: {logical}/{component}")
        results.append((item, DatabaseTarget(logical, component, target)))
    return results


def _online_backup(source_path: Path, target_path: Path) -> None:
    source = sqlite3.connect(f"file:{source_path.as_posix()}?mode=ro", uri=True, timeout=5)
    destination = sqlite3.connect(target_path)
    try:
        source.execute("PRAGMA busy_timeout=5000")
        source.backup(destination)
        row = destination.execute("PRAGMA integrity_check").fetchone()
        if not row or str(row[0]) != "ok":
            raise StorageArchiveError(f"SQLite backup integrity check failed: {source_path.name}")
    finally:
        destination.close()
        source.close()


def _database_metadata(path: Path, component: str) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        integrity = str(row[0]) if row else "unknown"
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
        version = 0
        if exists:
            version_row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations WHERE component = ?",
                (component,),
            ).fetchone()
            version = int(version_row[0]) if version_row else 0
        return {"integrity": integrity, "schema_version": version}
    finally:
        conn.close()


def _validate_member_names(entries: Iterable[zipfile.ZipInfo]) -> None:
    seen: set[str] = set()
    for info in entries:
        name = info.filename
        path = PurePosixPath(name)
        if not name or name in seen or path.is_absolute() or ".." in path.parts or "\\" in name:
            raise StorageArchiveError(f"unsafe backup member: {name!r}")
        if info.file_size > 2 * 1024 * 1024 * 1024:
            raise StorageArchiveError(f"backup member is too large: {name!r}")
        seen.add(name)


def _new_backup_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"backup_{stamp}_{uuid4().hex[:8]}"


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.") or "database"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


@contextmanager
def _staging_directory(parent: Path, purpose: str) -> Iterator[Path]:
    root = Path(parent).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    candidate = (root / f".muxdev-{purpose}-staging-{uuid4().hex}").resolve()
    if candidate.parent != root:
        raise ValueError("staging directory escapes its parent")
    candidate.mkdir()
    try:
        yield candidate
    finally:
        if candidate.parent == root and candidate.name.startswith(f".muxdev-{purpose}-staging-"):
            shutil.rmtree(candidate, ignore_errors=True)
