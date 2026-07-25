"""User-scoped multi-project Workbench registry.

Project execution facts intentionally remain in each workspace's
``.muxdev/control.sqlite``.  This store only owns daemon-wide concerns:
registered projects, browser devices, reusable Rules, and singleton state.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Mapping
from uuid import uuid4


WORKBENCH_SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def workbench_data_dir() -> Path:
    """Return the per-user data directory without introducing a platform dependency."""
    configured = os.environ.get("MUXDEV_HOME", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        if base:
            return (Path(base) / "muxdev").resolve()
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    if xdg:
        return (Path(xdg) / "muxdev").expanduser().resolve()
    return (Path.home() / ".local" / "share" / "muxdev").resolve()


def daemon_state_path(root: Path | None = None) -> Path:
    return (root or workbench_data_dir()) / "daemon.json"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(row: sqlite3.Row | None, *json_fields: str) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for field in json_fields:
        if isinstance(result.get(field), str):
            try:
                result[field] = json.loads(str(result[field]))
            except json.JSONDecodeError:
                pass
    return result


def _builtin_rules() -> tuple[dict[str, Any], ...]:
    definitions: tuple[dict[str, Any], ...] = (
        {
            "schema_version": "muxdev.rule.v1",
            "rule_id": "builtin.code.safe-change",
            "version": 1,
            "title": "安全代码变更",
            "description": "限制修改范围，保留既有行为，并用聚焦回归证明结果。",
            "kind": "code_standard",
            "scope": "builtin",
            "workflows": ["change", "test"],
            "path_patterns": [],
            "agent_roles": [],
            "instructions": (
                "仅修改任务范围内的文件；不得覆盖无关的未提交变更；"
                "交付前运行与变化相称的聚焦回归并报告证据。"
            ),
            "enforcement": "advisory",
            "delivery_items": [],
            "template": None,
        },
        {
            "schema_version": "muxdev.rule.v1",
            "rule_id": "builtin.delivery.evidence",
            "version": 1,
            "title": "可信交付证据",
            "description": "交付必须说明变化、验证结果、残余风险和人工决策。",
            "kind": "delivery_standard",
            "scope": "builtin",
            "workflows": ["change", "review", "test"],
            "path_patterns": [],
            "agent_roles": [],
            "instructions": "使用结构化 Evidence v3 完成可追溯交付。",
            "enforcement": "required",
            "delivery_items": [
                {
                    "stage_id": "review",
                    "deliverable": "结构化交付摘要",
                    "completion": "变化、验证、风险和决策均已记录",
                    "proof": "Evidence v3 Agent ReviewEvidence",
                    "verifier": {
                        "type": "agent_review",
                        "command_id": None,
                        "capability": "review",
                        "artifact_kind": None,
                    },
                }
            ],
            "template": None,
        },
        {
            "schema_version": "muxdev.rule.v1",
            "rule_id": "builtin.docs.design-decision",
            "version": 1,
            "title": "设计决策文档",
            "description": "用一致模板记录背景、方案、取舍、验收和回退。",
            "kind": "document_template",
            "scope": "builtin",
            "workflows": ["design", "change"],
            "path_patterns": ["docs/**"],
            "agent_roles": [],
            "instructions": "涉及产品或架构取舍时，按模板生成设计决策文档。",
            "enforcement": "advisory",
            "delivery_items": [],
            "template": (
                "# 决策标题\n\n## 背景与目标\n\n## 约束\n\n"
                "## 方案与取舍\n\n## 验收标准\n\n## 风险与回退\n"
            ),
        },
    )
    result: list[dict[str, Any]] = []
    for definition in definitions:
        payload = dict(definition)
        payload["digest"] = "sha256:" + hashlib.sha256(
            _json(payload).encode("utf-8")
        ).hexdigest()
        result.append(payload)
    return tuple(result)


class WorkbenchStore:
    """SQLite repository for daemon-wide state."""

    def __init__(self, database: Path | None = None) -> None:
        self.path = (database or (workbench_data_dir() / "workbench.sqlite")).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self._ensure_schema()

    def __enter__(self) -> "WorkbenchStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                yield self.connection
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise

    def _ensure_schema(self) -> None:
        statements = (
            """CREATE TABLE IF NOT EXISTS workbench_schema(
              version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS projects(
              project_id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL UNIQUE,
              status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              last_opened_at TEXT, metadata TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS devices(
              device_id TEXT PRIMARY KEY, label TEXT NOT NULL, status TEXT NOT NULL,
              public_key TEXT, created_at TEXT NOT NULL, last_seen_at TEXT,
              revoked_at TEXT, metadata TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS web_sessions(
              session_id TEXT PRIMARY KEY, device_id TEXT NOT NULL,
              token_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
              expires_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
              revoked_at TEXT, metadata TEXT NOT NULL,
              FOREIGN KEY(device_id) REFERENCES devices(device_id)
            )""",
            """CREATE TABLE IF NOT EXISTS rules(
              rule_id TEXT NOT NULL, version INTEGER NOT NULL, title TEXT NOT NULL,
              description TEXT NOT NULL, kind TEXT NOT NULL, scope TEXT NOT NULL,
              definition TEXT NOT NULL, digest TEXT NOT NULL, status TEXT NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              PRIMARY KEY(rule_id, version)
            )""",
        )
        with self.transaction() as connection:
            for statement in statements:
                connection.execute(statement)
            connection.execute(
                "INSERT OR IGNORE INTO workbench_schema(version, applied_at) VALUES (?, ?)",
                (WORKBENCH_SCHEMA_VERSION, utc_now()),
            )
        for definition in _builtin_rules():
            if not self.get_rule(str(definition["rule_id"]), int(definition["version"])):
                self.put_rule(definition)

    def register_project(
        self,
        workspace: Path | str,
        *,
        name: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            path = Path(workspace).expanduser().resolve()
            if not path.exists():
                raise FileNotFoundError(f"project directory does not exist: {path}")
            if not path.is_dir():
                raise NotADirectoryError(f"project path is not a directory: {path}")
            current = self.project_for_path(path)
            now = utc_now()
            if current:
                self.connection.execute(
                    """UPDATE projects SET name = ?, status = 'active',
                       updated_at = ?, last_opened_at = ? WHERE project_id = ?""",
                    (
                        (name or str(current["name"])).strip() or path.name,
                        now,
                        now,
                        str(current["project_id"]),
                    ),
                )
                self.connection.commit()
                return self.get_project(str(current["project_id"])) or current
            project_id = f"project_{uuid4().hex}"
            self.connection.execute(
                """INSERT INTO projects(
                  project_id, name, path, status, created_at, updated_at,
                  last_opened_at, metadata
                ) VALUES (?, ?, ?, 'active', ?, ?, ?, '{}')""",
                (
                    project_id,
                    (name or path.name or str(path)).strip(),
                    str(path),
                    now,
                    now,
                    now,
                ),
            )
            self.connection.commit()
            return self.get_project(project_id) or {}

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            return _decode(row, "metadata")

    def project_for_path(self, workspace: Path | str) -> dict[str, Any] | None:
        with self._lock:
            path = str(Path(workspace).expanduser().resolve())
            row = self.connection.execute(
                "SELECT * FROM projects WHERE path = ?", (path,)
            ).fetchone()
            return _decode(row, "metadata")

    def list_projects(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.connection.execute(
                """SELECT * FROM projects
                   ORDER BY COALESCE(last_opened_at, created_at) DESC, name"""
            ).fetchall()
            return [_decode(row, "metadata") or {} for row in rows]

    def update_project(
        self,
        project_id: str,
        *,
        name: str | None = None,
        status: str | None = None,
        metadata: Mapping[str, object] | None = None,
        touch: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            current = self.get_project(project_id)
            if not current:
                raise FileNotFoundError(project_id)
            merged = dict(current.get("metadata") or {})
            if metadata:
                merged.update(metadata)
            now = utc_now()
            self.connection.execute(
                """UPDATE projects SET name = ?, status = ?, updated_at = ?,
                   last_opened_at = ?, metadata = ? WHERE project_id = ?""",
                (
                    name.strip() if name is not None else current["name"],
                    status if status is not None else current["status"],
                    now,
                    now if touch else current.get("last_opened_at"),
                    _json(merged),
                    project_id,
                ),
            )
            self.connection.commit()
            return self.get_project(project_id) or current

    def remove_project(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            current = self.get_project(project_id)
            if not current:
                raise FileNotFoundError(project_id)
            self.connection.execute(
                "DELETE FROM projects WHERE project_id = ?", (project_id,)
            )
            self.connection.commit()
            return current

    # Global browser-device methods intentionally mirror ControlStore's API.
    def create_device(self, *, label: str, public_key: str | None = None) -> dict[str, Any]:
        with self._lock:
            device_id = f"device_{uuid4().hex}"
            now = utc_now()
            self.connection.execute(
                """INSERT INTO devices(
                  device_id, label, status, public_key, created_at, last_seen_at,
                  revoked_at, metadata
                ) VALUES (?, ?, 'active', ?, ?, ?, NULL, '{}')""",
                (device_id, label, public_key, now, now),
            )
            self.connection.commit()
            return self.get_device(device_id) or {}

    def get_device(self, device_id: str) -> dict[str, Any] | None:
        with self._lock:
            return _decode(
                self.connection.execute(
                    "SELECT * FROM devices WHERE device_id = ?", (device_id,)
                ).fetchone(),
                "metadata",
            )

    def list_devices(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                _decode(row, "metadata") or {}
                for row in self.connection.execute(
                    "SELECT * FROM devices ORDER BY created_at"
                ).fetchall()
            ]

    def revoke_device(self, device_id: str) -> None:
        with self._lock:
            now = utc_now()
            self.connection.execute(
                "UPDATE devices SET status = 'revoked', revoked_at = ? WHERE device_id = ?",
                (now, device_id),
            )
            self.connection.execute(
                """UPDATE web_sessions SET revoked_at = ?
                   WHERE device_id = ? AND revoked_at IS NULL""",
                (now, device_id),
            )
            self.connection.commit()

    def create_web_session(
        self,
        *,
        device_id: str,
        token_hash: str,
        expires_at: str,
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            session_id = f"session_{uuid4().hex}"
            now = utc_now()
            self.connection.execute(
                """INSERT INTO web_sessions(
                  session_id, device_id, token_hash, created_at, expires_at,
                  last_seen_at, revoked_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)""",
                (
                    session_id,
                    device_id,
                    token_hash,
                    now,
                    expires_at,
                    now,
                    _json(metadata or {}),
                ),
            )
            self.connection.commit()
            return self.web_session(token_hash) or {}

    def web_session(self, token_hash: str) -> dict[str, Any] | None:
        with self._lock:
            return _decode(
                self.connection.execute(
                    """SELECT web_sessions.*, devices.status AS device_status
                       FROM web_sessions JOIN devices USING(device_id)
                       WHERE token_hash = ?""",
                    (token_hash,),
                ).fetchone(),
                "metadata",
            )

    def touch_web_session(self, token_hash: str) -> None:
        with self._lock:
            now = utc_now()
            row = self.connection.execute(
                "SELECT device_id FROM web_sessions WHERE token_hash = ?", (token_hash,)
            ).fetchone()
            if not row:
                return
            self.connection.execute(
                "UPDATE web_sessions SET last_seen_at = ? WHERE token_hash = ?",
                (now, token_hash),
            )
            self.connection.execute(
                "UPDATE devices SET last_seen_at = ? WHERE device_id = ?",
                (now, str(row[0])),
            )
            self.connection.commit()

    def revoke_web_session(self, token_hash: str) -> None:
        with self._lock:
            self.connection.execute(
                "UPDATE web_sessions SET revoked_at = ? WHERE token_hash = ?",
                (utc_now(), token_hash),
            )
            self.connection.commit()

    def import_project_devices(self, project_connection: sqlite3.Connection) -> int:
        """Idempotently move legacy device facts into daemon-wide ownership."""
        available = {
            str(row[0])
            for row in project_connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if not {"devices", "web_sessions"} <= available:
            return 0
        devices = project_connection.execute("SELECT * FROM devices").fetchall()
        sessions = project_connection.execute("SELECT * FROM web_sessions").fetchall()
        imported = 0
        with self.transaction() as connection:
            for row in devices:
                value = dict(row)
                cursor = connection.execute(
                    """INSERT OR IGNORE INTO devices(
                      device_id, label, status, public_key, created_at, last_seen_at,
                      revoked_at, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        value["device_id"],
                        value["label"],
                        value["status"],
                        value.get("public_key"),
                        value["created_at"],
                        value.get("last_seen_at"),
                        value.get("revoked_at"),
                        value.get("metadata") or "{}",
                    ),
                )
                imported += int(cursor.rowcount > 0)
            for row in sessions:
                value = dict(row)
                connection.execute(
                    """INSERT OR IGNORE INTO web_sessions(
                      session_id, device_id, token_hash, created_at, expires_at,
                      last_seen_at, revoked_at, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        value["session_id"],
                        value["device_id"],
                        value["token_hash"],
                        value["created_at"],
                        value["expires_at"],
                        value["last_seen_at"],
                        value.get("revoked_at"),
                        value.get("metadata") or "{}",
                    ),
                )
        return imported

    def put_rule(self, definition: Mapping[str, object]) -> dict[str, Any]:
        with self._lock:
            rule_id = str(definition["rule_id"])
            version = int(definition["version"])
            now = utc_now()
            self.connection.execute(
                """INSERT INTO rules(
                  rule_id, version, title, description, kind, scope, definition,
                  digest, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                ON CONFLICT(rule_id, version) DO UPDATE SET
                  title=excluded.title, description=excluded.description,
                  kind=excluded.kind, scope=excluded.scope,
                  definition=excluded.definition, digest=excluded.digest,
                  status=excluded.status, updated_at=excluded.updated_at""",
                (
                    rule_id,
                    version,
                    str(definition["title"]),
                    str(definition.get("description") or ""),
                    str(definition["kind"]),
                    str(definition.get("scope") or "user"),
                    _json(definition),
                    str(definition["digest"]),
                    now,
                    now,
                ),
            )
            self.connection.commit()
            return self.get_rule(rule_id, version) or {}

    def get_rule(self, rule_id: str, version: int | None = None) -> dict[str, Any] | None:
        with self._lock:
            if version is None:
                row = self.connection.execute(
                    """SELECT * FROM rules WHERE rule_id = ? AND status = 'active'
                       ORDER BY version DESC LIMIT 1""",
                    (rule_id,),
                ).fetchone()
            else:
                row = self.connection.execute(
                    "SELECT * FROM rules WHERE rule_id = ? AND version = ?",
                    (rule_id, version),
                ).fetchone()
            return _decode(row, "definition")

    def list_rules(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.connection.execute(
                """SELECT rules.* FROM rules
                   JOIN (
                     SELECT rule_id, MAX(version) AS version FROM rules
                     WHERE status = 'active' GROUP BY rule_id
                   ) latest USING(rule_id, version)
                   ORDER BY kind, title"""
            ).fetchall()
            return [_decode(row, "definition") or {} for row in rows]


class WorkbenchRegistry:
    """Resolve registered project IDs to isolated workspace runtimes."""

    def __init__(self, store: WorkbenchStore) -> None:
        self.store = store
        self.max_cli_processes = 8
        self.max_project_cli_processes = 4

    @classmethod
    def user(cls) -> "WorkbenchRegistry":
        return cls(WorkbenchStore())

    @classmethod
    def local(cls, workspace: Path) -> "WorkbenchRegistry":
        root = workspace.resolve() / ".muxdev"
        return cls(WorkbenchStore(root / "workbench.sqlite"))

    def register(self, workspace: Path | str, *, name: str | None = None) -> dict[str, Any]:
        project = self.store.register_project(workspace, name=name)
        # Initializing ControlStore is intentionally per-project and never moves its data.
        from .storage import ControlStore

        with ControlStore(Path(str(project["path"]))) as control:
            self.store.import_project_devices(control.connection)
        return self.project_snapshot(str(project["project_id"]))

    def workspace(self, project_id: str) -> Path:
        project = self.store.get_project(project_id)
        if not project:
            raise FileNotFoundError(f"project not found: {project_id}")
        path = Path(str(project["path"])).resolve()
        if not path.is_dir():
            self.store.update_project(project_id, status="offline")
            raise FileNotFoundError(f"project directory is offline: {path}")
        self.store.update_project(project_id, status="active", touch=True)
        return path

    def project_snapshot(self, project_id: str) -> dict[str, Any]:
        project = self.store.get_project(project_id)
        if not project:
            raise FileNotFoundError(project_id)
        path = Path(str(project["path"])).resolve()
        snapshot = dict(project)
        snapshot["available"] = path.is_dir()
        snapshot["conversation_count"] = 0
        snapshot["needs_you"] = 0
        snapshot["active_sessions"] = 0
        if not path.is_dir():
            snapshot["status"] = "offline"
            return snapshot
        from .storage import ControlStore

        with ControlStore(path) as control:
            conversations = control.list_conversations(limit=1000)
            snapshot["conversation_count"] = len(conversations)
            snapshot["needs_you"] = sum(
                1 for item in conversations if item.get("status") == "needs_user"
            )
            snapshot["active_sessions"] = sum(
                1
                for conversation in conversations
                for item in control.list_agent_sessions(str(conversation["conversation_id"]))
                if item.get("status") not in {"closed", "failed"}
            )
        self.store.update_project(
            project_id,
            metadata={
                "metrics": {
                    "conversation_count": snapshot["conversation_count"],
                    "needs_you": snapshot["needs_you"],
                    "active_sessions": snapshot["active_sessions"],
                    "captured_at": utc_now(),
                }
            },
        )
        return snapshot

    def list_projects(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for index, item in enumerate(self.store.list_projects()):
            if index < 8:
                result.append(self.project_snapshot(str(item["project_id"])))
                continue
            path = Path(str(item["path"]))
            metrics = dict((item.get("metadata") or {}).get("metrics") or {})
            result.append(
                {
                    **item,
                    "available": path.is_dir(),
                    "status": item["status"] if path.is_dir() else "offline",
                    "conversation_count": int(
                        metrics.get("conversation_count") or 0
                    ),
                    "needs_you": int(metrics.get("needs_you") or 0),
                    "active_sessions": int(
                        metrics.get("active_sessions") or 0
                    ),
                }
            )
        return result

    def reconcile(self) -> None:
        from .runtime.agent_sessions import agent_session_manager

        for project in self.store.list_projects():
            path = Path(str(project["path"]))
            if not path.is_dir():
                self.store.update_project(str(project["project_id"]), status="offline")
                continue
            agent_session_manager(path).reconcile_orphans()


__all__ = [
    "WORKBENCH_SCHEMA_VERSION",
    "WorkbenchRegistry",
    "WorkbenchStore",
    "daemon_state_path",
    "utc_now",
    "workbench_data_dir",
]
