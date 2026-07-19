"""Reliable SQLite connection, transaction, and migration primitives.

The module deliberately uses only the Python standard library.  Both muxdev
databases use this layer so durability and migration behaviour cannot drift.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Sequence


class StorageError(RuntimeError):
    """Base class for storage bootstrap and migration failures."""


class MigrationRequiredError(StorageError):
    """Raised when a read-only database is older than the running code."""


class MigrationChecksumError(StorageError):
    """Raised when an applied migration no longer matches its definition."""


class UnsupportedSchemaError(StorageError):
    """Raised when the database was created by newer muxdev code."""


MigrationApply = Callable[[sqlite3.Connection], None]


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    checksum_seed: str
    apply: MigrationApply

    @property
    def checksum(self) -> str:
        value = f"{self.version}:{self.name}:{self.checksum_seed}"
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StorageHealth:
    path: str
    component: str
    schema_version: int
    latest_schema_version: int
    journal_mode: str
    synchronous: str
    status: str
    migration_required: bool
    integrity: str

    def to_dict(self, *, include_path: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "component": self.component,
            "schema_version": self.schema_version,
            "latest_schema_version": self.latest_schema_version,
            "journal_mode": self.journal_mode,
            "synchronous": self.synchronous,
            "status": self.status,
            "migration_required": self.migration_required,
            "integrity": self.integrity,
        }
        if include_path:
            payload["path"] = self.path
        return payload


class SQLiteEngine:
    """One configured SQLite connection with nested transaction support."""

    def __init__(
        self,
        db_path: Path,
        *,
        component: str,
        readonly: bool = False,
        busy_timeout_ms: int = 5_000,
        commit_validator: Callable[[], None] | None = None,
    ) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.component = component
        self.readonly = readonly
        self.busy_timeout_ms = int(busy_timeout_ms)
        self.commit_validator = commit_validator
        self._lock = threading.RLock()
        self._local = threading.local()
        self._on_commit: list[Callable[[], None]] = []
        self.schema_version = 0
        self.latest_schema_version = 0
        self.migration_required = False
        if not readonly:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(
                self.db_path,
                timeout=max(0.001, busy_timeout_ms / 1_000),
                check_same_thread=False,
            )
        else:
            if not self.db_path.is_file():
                raise FileNotFoundError(f"database not found: {self.db_path}")
            uri = f"file:{self.db_path.as_posix()}?mode=ro"
            self.conn = sqlite3.connect(
                uri,
                uri=True,
                timeout=max(0.001, busy_timeout_ms / 1_000),
                check_same_thread=False,
            )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        self.journal_mode = self._configure_journal_mode()
        if not readonly:
            self.conn.execute("PRAGMA synchronous=FULL")
        self.synchronous = _synchronous_name(self.conn.execute("PRAGMA synchronous").fetchone()[0])
        self.degraded = self.journal_mode != "wal"
        self.connection = ManagedConnection(self)

    @property
    def transaction_depth(self) -> int:
        return int(getattr(self._local, "depth", 0))

    def _configure_journal_mode(self) -> str:
        if self.readonly:
            row = self.conn.execute("PRAGMA journal_mode").fetchone()
            return str(row[0]).lower() if row else "unknown"
        try:
            row = self.conn.execute("PRAGMA journal_mode=WAL").fetchone()
            mode = str(row[0]).lower() if row else "unknown"
            if mode == "wal":
                return mode
        except sqlite3.DatabaseError:
            pass
        row = self.conn.execute("PRAGMA journal_mode=DELETE").fetchone()
        return str(row[0]).lower() if row else "delete"

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Open a BEGIN IMMEDIATE transaction or a nested savepoint."""
        outermost = self.transaction_depth == 0
        savepoint = f"muxdev_sp_{self.transaction_depth}"
        callback_count = len(self._on_commit)
        if outermost:
            self._lock.acquire()
            self.conn.execute("BEGIN IMMEDIATE")
        else:
            self.conn.execute(f"SAVEPOINT {savepoint}")
        self._local.depth = self.transaction_depth + 1
        try:
            yield self.conn
        except BaseException:
            self._local.depth = self.transaction_depth - 1
            if outermost:
                self.conn.rollback()
                self._on_commit.clear()
                self._lock.release()
            else:
                self.conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                del self._on_commit[callback_count:]
            raise
        else:
            self._local.depth = self.transaction_depth - 1
            if outermost:
                callbacks = list(self._on_commit)
                self._on_commit.clear()
                try:
                    if self.commit_validator is not None:
                        self.commit_validator()
                    self.conn.commit()
                except BaseException:
                    self.conn.rollback()
                    raise
                finally:
                    self._lock.release()
                for callback in callbacks:
                    callback()
            else:
                self.conn.execute(f"RELEASE SAVEPOINT {savepoint}")

    def commit(self) -> None:
        """Commit compatibility writes unless an enclosing UoW owns commit."""
        if self.transaction_depth:
            return
        with self._lock:
            try:
                if self.commit_validator is not None:
                    self.commit_validator()
                self.conn.commit()
            except BaseException:
                self.conn.rollback()
                self._on_commit.clear()
                raise
            callbacks = list(self._on_commit)
            self._on_commit.clear()
        for callback in callbacks:
            callback()

    def on_commit(self, callback: Callable[[], None]) -> None:
        if self.transaction_depth:
            self._on_commit.append(callback)
        else:
            callback()

    def health(self, *, check_integrity: bool = True) -> StorageHealth:
        integrity = "not_checked"
        if check_integrity:
            try:
                row = self.conn.execute("PRAGMA quick_check").fetchone()
                integrity = str(row[0]) if row else "unknown"
            except sqlite3.DatabaseError as exc:
                integrity = f"error:{type(exc).__name__}"
        status = "healthy"
        if self.migration_required:
            status = "migration_required"
        elif self.degraded or integrity != "ok":
            status = "degraded"
        return StorageHealth(
            path=str(self.db_path),
            component=self.component,
            schema_version=self.schema_version,
            latest_schema_version=self.latest_schema_version,
            journal_mode=self.journal_mode,
            synchronous=self.synchronous,
            status=status,
            migration_required=self.migration_required,
            integrity=integrity,
        )

    def close(self) -> None:
        self.conn.close()


class UnitOfWork:
    """Explicit transaction boundary used by lifecycle application services."""

    def __init__(self, engine: SQLiteEngine):
        self.engine = engine
        self._context = None

    def __enter__(self) -> sqlite3.Connection:
        self._context = self.engine.transaction()
        return self._context.__enter__()

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool | None:
        assert self._context is not None
        return self._context.__exit__(exc_type, exc, tb)


def apply_migrations(
    engine: SQLiteEngine,
    migrations: Sequence[Migration],
    *,
    backup_root: Path | None = None,
    retain_backups: int = 5,
) -> None:
    """Validate and apply ordered component migrations."""
    ordered = sorted(migrations, key=lambda item: item.version)
    if [item.version for item in ordered] != list(range(1, len(ordered) + 1)):
        raise ValueError(f"{engine.component} migrations must be contiguous from version 1")
    engine.latest_schema_version = ordered[-1].version if ordered else 0

    migration_table_exists = _table_exists(engine.conn, "schema_migrations")
    if engine.readonly:
        applied = _read_applied(engine.conn, engine.component) if migration_table_exists else {}
        _validate_applied(engine, ordered, applied)
        engine.schema_version = max(applied, default=0)
        engine.migration_required = engine.schema_version < engine.latest_schema_version
        return

    legacy_tables = _user_tables(engine.conn) - {"schema_migrations"}
    with engine.transaction():
        engine.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              component TEXT NOT NULL,
              version INTEGER NOT NULL,
              name TEXT NOT NULL,
              checksum TEXT NOT NULL,
              applied_at TEXT NOT NULL,
              PRIMARY KEY (component, version)
            )
            """
        )
    applied = _read_applied(engine.conn, engine.component)
    _validate_applied(engine, ordered, applied)
    if legacy_tables and not applied and ordered:
        _migration_backup(engine, backup_root=backup_root, retain=retain_backups)

    for migration in ordered:
        if migration.version in applied:
            continue
        with engine.transaction():
            migration.apply(engine.conn)
            engine.conn.execute(
                """
                INSERT INTO schema_migrations(component, version, name, checksum, applied_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    engine.component,
                    migration.version,
                    migration.name,
                    migration.checksum,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
    engine.schema_version = engine.latest_schema_version
    engine.migration_required = False


def execute_script(conn: sqlite3.Connection, script: str) -> None:
    """Execute the simple DDL scripts used by migrations without implicit commit."""
    for statement in script.split(";"):
        sql = statement.strip()
        if sql:
            conn.execute(sql)


def add_missing_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}
    for column, declaration in columns.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def _validate_applied(engine: SQLiteEngine, migrations: Sequence[Migration], applied: dict[int, tuple[str, str]]) -> None:
    definitions = {item.version: item for item in migrations}
    newer = [version for version in applied if version not in definitions]
    if newer:
        raise UnsupportedSchemaError(
            f"{engine.component} schema {max(newer)} is newer than supported {engine.latest_schema_version}"
        )
    for version, (name, checksum) in applied.items():
        migration = definitions[version]
        if name != migration.name or checksum != migration.checksum:
            raise MigrationChecksumError(
                f"{engine.component} migration {version} checksum mismatch"
            )


def _read_applied(conn: sqlite3.Connection, component: str) -> dict[int, tuple[str, str]]:
    rows = conn.execute(
        "SELECT version, name, checksum FROM schema_migrations WHERE component = ? ORDER BY version",
        (component,),
    )
    return {int(row["version"]): (str(row["name"]), str(row["checksum"])) for row in rows}


def _migration_backup(engine: SQLiteEngine, *, backup_root: Path | None, retain: int) -> None:
    root = (backup_root or engine.db_path.parent / "backups" / "migrations").resolve()
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = root / f"{engine.db_path.stem}-{engine.component}-pre-migration-{stamp}.sqlite"
    destination = sqlite3.connect(target)
    try:
        engine.conn.backup(destination)
    finally:
        destination.close()
    backups = sorted(
        root.glob(f"{engine.db_path.stem}-{engine.component}-pre-migration-*.sqlite"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for stale in backups[max(1, retain) :]:
        stale.unlink(missing_ok=True)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _user_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _synchronous_name(value: object) -> str:
    return {0: "off", 1: "normal", 2: "full", 3: "extra"}.get(int(value), str(value).lower())


class ManagedConnection:
    """Compatibility proxy whose commit respects an enclosing UnitOfWork."""

    def __init__(self, engine: SQLiteEngine):
        self.engine = engine

    def commit(self) -> None:
        self.engine.commit()

    def rollback(self) -> None:
        if self.engine.transaction_depth:
            raise RuntimeError("an enclosing UnitOfWork owns rollback")
        self.engine.conn.rollback()

    def __getattr__(self, name: str):
        return getattr(self.engine.conn, name)
