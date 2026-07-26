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


WORKBENCH_SCHEMA_VERSION = 2


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


_BUILTIN_RULE_DEFINITIONS: tuple[dict[str, Any], ...] = (
        {
            "schema_version": "muxdev.rule.v1",
            "rule_id": "builtin.go.engineering-review",
            "version": 1,
            "title": "Go 工程与代码评审规范",
            "description": "面向 Go 项目的可读性、错误处理、并发、接口和测试约束。",
            "kind": "code_standard",
            "scope": "builtin",
            "workflows": ["change", "review", "test"],
            "path_patterns": ["**/*.go"],
            "agent_roles": [],
            "instructions": (
                "遵循 gofmt 和 Go 的惯用命名；保持包职责单一，避免无意义接口与过度抽象；"
                "错误必须携带上下文并保持可判断性，不得静默忽略；明确 goroutine 生命周期、"
                "取消和资源释放；优先表驱动测试，覆盖失败路径和并发边界；评审时说明 API、"
                "兼容性、性能与可运维性影响。"
            ),
            "enforcement": "required",
            "delivery_items": [],
            "template": None,
            "source_documents": [
                {
                    "source_id": "upstream.go-code-review-comments",
                    "kind": "builtin",
                    "display_name": "Go Code Review Comments",
                    "original_url": "https://go.dev/wiki/CodeReviewComments",
                    "revision": "2aba1bef436aaba8db257a01af767202dd35ef66",
                    "license": "BSD-3-Clause (Go project)",
                },
                {
                    "source_id": "upstream.uber-go-guide",
                    "kind": "builtin",
                    "display_name": "Uber Go Style Guide",
                    "original_url": "https://github.com/uber-go/guide/tree/1d60a91aa5e87d443002e23c21903c49489dbde5",
                    "revision": "1d60a91aa5e87d443002e23c21903c49489dbde5",
                    "license": "Apache-2.0",
                },
            ],
        },
        {
            "schema_version": "muxdev.rule.v1",
            "rule_id": "builtin.docs.requirements",
            "version": 1,
            "title": "软件需求规格说明书",
            "description": "可验证、可追踪的需求文档模板，覆盖范围、角色、功能与非功能需求。",
            "kind": "document_template",
            "scope": "builtin",
            "workflows": ["design", "change", "test"],
            "path_patterns": ["docs/**"],
            "agent_roles": [],
            "instructions": "每条需求使用稳定 ID，写明来源、优先级和可验证验收标准。",
            "enforcement": "advisory",
            "delivery_items": [],
            "template": (
                "# 软件需求规格说明书\n\n"
                "## 1. 背景、目标与成功指标\n\n## 2. 范围\n\n### 2.1 范围内\n\n"
                "### 2.2 范围外\n\n## 3. 术语、角色与利益相关方\n\n"
                "## 4. 用户场景与业务流程\n\n## 5. 功能需求\n\n"
                "| ID | 需求 | 优先级 | 验收标准 | 来源 |\n|---|---|---|---|---|\n"
                "| FR-001 |  |  | Given / When / Then |  |\n\n"
                "## 6. 非功能需求\n\n| ID | 质量属性 | 指标与阈值 | 验证方式 |\n"
                "|---|---|---|---|\n| NFR-001 |  |  |  |\n\n"
                "## 7. 数据、接口与兼容性\n\n## 8. 约束、假设与依赖\n\n"
                "## 9. 风险与待决问题\n\n## 10. 需求追踪矩阵\n"
            ),
            "source_documents": [
                {
                    "source_id": "upstream.srs-template",
                    "kind": "builtin",
                    "display_name": "SRS Template",
                    "original_url": "https://github.com/jam01/SRS-Template/tree/3965e951fa5ea1c1dd1bbc7a66b7f35f7e85c006",
                    "revision": "3965e951fa5ea1c1dd1bbc7a66b7f35f7e85c006",
                    "license": "CC0-1.0",
                }
            ],
        },
        {
            "schema_version": "muxdev.rule.v1",
            "rule_id": "builtin.docs.software-design",
            "version": 1,
            "title": "软件设计说明书",
            "description": "从需求追踪到架构、组件、数据、接口、安全及部署的设计模板。",
            "kind": "document_template",
            "scope": "builtin",
            "workflows": ["design", "change"],
            "path_patterns": ["docs/**"],
            "agent_roles": [],
            "instructions": "设计决策必须关联需求 ID，并记录被否决方案、验证策略与回退路径。",
            "enforcement": "advisory",
            "delivery_items": [],
            "template": (
                "# 软件设计说明书\n\n## 1. 目标、范围与关联需求\n\n"
                "## 2. 约束与质量属性\n\n## 3. 系统上下文与总体架构\n\n"
                "## 4. 组件职责和依赖\n\n## 5. 数据模型与生命周期\n\n"
                "## 6. API、事件与错误契约\n\n## 7. 并发、一致性与幂等\n\n"
                "## 8. 安全、隐私与权限边界\n\n## 9. 可观测性与运维\n\n"
                "## 10. 部署、迁移、兼容与回退\n\n## 11. 方案比较与决策记录\n\n"
                "## 12. 测试与验证设计\n\n## 13. 风险和待决问题\n"
            ),
            "source_documents": [
                {
                    "source_id": "upstream.sdd-template",
                    "kind": "builtin",
                    "display_name": "SDD Template",
                    "original_url": "https://github.com/jam01/SDD-Template/tree/5dca2e58411cfcc453eb49a61983c9d9203ff9e4",
                    "revision": "5dca2e58411cfcc453eb49a61983c9d9203ff9e4",
                    "license": "CC0-1.0",
                }
            ],
        },
        {
            "schema_version": "muxdev.rule.v1",
            "rule_id": "builtin.docs.change-proposal",
            "version": 1,
            "title": "技术方案与变更提案",
            "description": "适合较大变更的目标、非目标、风险、发布与毕业标准模板。",
            "kind": "document_template",
            "scope": "builtin",
            "workflows": ["design", "change", "review"],
            "path_patterns": ["docs/**"],
            "agent_roles": [],
            "instructions": "高影响变更在编码前明确非目标、风险、监控、发布与回滚。",
            "enforcement": "advisory",
            "delivery_items": [],
            "template": (
                "# 技术方案 / 变更提案\n\n## 摘要\n\n## 动机与问题陈述\n\n"
                "## 目标\n\n## 非目标\n\n## 用户故事\n\n## 设计细节\n\n"
                "## API 与兼容性\n\n## 风险与缓解措施\n\n## 安全与隐私\n\n"
                "## 测试计划\n\n## 监控与告警\n\n## 发布、回滚与毕业标准\n\n"
                "## 替代方案\n"
            ),
            "source_documents": [
                {
                    "source_id": "upstream.kubernetes-kep-template",
                    "kind": "builtin",
                    "display_name": "Kubernetes KEP Template",
                    "original_url": "https://github.com/kubernetes/enhancements/tree/64765b4b02389318f2113119dd9e491479f978a0/keps/NNNN-kep-template",
                    "revision": "64765b4b02389318f2113119dd9e491479f978a0",
                    "license": "Apache-2.0",
                }
            ],
        },
        {
            "schema_version": "muxdev.rule.v1",
            "rule_id": "builtin.docs.test-plan-report",
            "version": 1,
            "title": "测试计划与测试报告",
            "description": "覆盖需求追踪、正反向场景、非功能测试和执行证据。",
            "kind": "document_template",
            "scope": "builtin",
            "workflows": ["change", "test", "review"],
            "path_patterns": ["docs/**"],
            "agent_roles": [],
            "instructions": "测试用例必须关联需求和风险，执行结果必须链接可复核证据。",
            "enforcement": "advisory",
            "delivery_items": [],
            "template": (
                "# 测试计划与报告\n\n## 1. 目的、范围与不测试项\n\n"
                "## 2. 被测版本、环境与依赖\n\n## 3. 风险与测试优先级\n\n"
                "## 4. 测试策略\n\n### 4.1 单元与组件\n\n### 4.2 集成与契约\n\n"
                "### 4.3 端到端与验收\n\n### 4.4 性能、安全与可靠性\n\n"
                "## 5. 需求—用例追踪矩阵\n\n"
                "| 用例 ID | 需求 ID | 场景 | 前置条件 | 步骤 | 预期结果 |\n|---|---|---|---|---|---|\n\n"
                "## 6. 执行记录\n\n| 用例 ID | 结果 | 执行时间 | 证据 | 缺陷 |\n|---|---|---|---|---|\n\n"
                "## 7. 回归范围与退出标准\n\n## 8. 结论、残余风险与批准\n"
            ),
            "source_documents": [
                {
                    "source_id": "upstream.microsoft-test-planning",
                    "kind": "builtin",
                    "display_name": "Microsoft Engineering Fundamentals - Test Planning",
                    "original_url": "https://microsoft.github.io/code-with-engineering-playbook/automated-testing/test-planning/",
                    "revision": "016770e43d8a75be87b98c000c049f07c4a6e6f8",
                    "license": "CC-BY-4.0",
                }
            ],
        },
        {
            "schema_version": "muxdev.rule.v1",
            "rule_id": "builtin.delivery.traceable-evidence",
            "version": 1,
            "title": "需求到交付的可信证据",
            "description": "要求需求、设计、测试、结果和交付证据形成可验证追踪链。",
            "kind": "delivery_standard",
            "scope": "builtin",
            "workflows": ["change", "design", "review", "test"],
            "path_patterns": [],
            "agent_roles": [],
            "instructions": (
                "交付必须列出需求 ID、对应设计决策、测试用例、执行结果、变更摘要、"
                "残余风险与人工决定；只把可复核的哈希链、产物或检查结果标记为已验证。"
            ),
            "enforcement": "required",
            "delivery_items": [
                {
                    "stage_id": "review",
                    "deliverable": "需求—设计—测试—证据追踪矩阵",
                    "completion": "所有交付项均有关联证据或明确记录缺口",
                    "proof": "Evidence v3 ReviewEvidence 与签名清单",
                    "verifier": {
                        "type": "agent_review",
                        "command_id": None,
                        "capability": "review",
                        "artifact_kind": None,
                    },
                }
            ],
            "template": None,
            "source_documents": [],
        },
)


def _builtin_rules() -> tuple[dict[str, Any], ...]:
    result: list[dict[str, Any]] = []
    for definition in _BUILTIN_RULE_DEFINITIONS:
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
            """CREATE TABLE IF NOT EXISTS rule_sources(
              source_id TEXT PRIMARY KEY, kind TEXT NOT NULL, display_name TEXT NOT NULL,
              original_url TEXT, local_markdown_path TEXT NOT NULL, digest TEXT NOT NULL,
              content_type TEXT NOT NULL, size_bytes INTEGER NOT NULL, license TEXT,
              captured_at TEXT NOT NULL, metadata TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS skill_sources(
              source_id TEXT PRIMARY KEY, kind TEXT NOT NULL, display_name TEXT NOT NULL,
              path TEXT NOT NULL, mode TEXT NOT NULL, trust_state TEXT NOT NULL,
              enabled INTEGER NOT NULL, auto_enable INTEGER NOT NULL,
              revision TEXT NOT NULL, status TEXT NOT NULL, last_scanned_at TEXT NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata TEXT NOT NULL
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
        with self._lock:
            self.connection.execute(
                """UPDATE rules SET status = 'deprecated', updated_at = ?
                   WHERE rule_id IN (
                     'builtin.code.safe-change',
                     'builtin.delivery.evidence',
                     'builtin.docs.design-decision'
                   )""",
                (utc_now(),),
            )
            self.connection.commit()

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

    def get_rule(
        self,
        rule_id: str,
        version: int | None = None,
        *,
        include_archived: bool = False,
    ) -> dict[str, Any] | None:
        with self._lock:
            if version is None:
                if include_archived:
                    row = self.connection.execute(
                        """SELECT * FROM rules
                           WHERE rule_id = ? AND status != 'deprecated'
                           ORDER BY version DESC LIMIT 1""",
                        (rule_id,),
                    ).fetchone()
                else:
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

    def list_rules(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            if include_archived:
                rows = self.connection.execute(
                    """SELECT rules.* FROM rules
                       JOIN (
                         SELECT rule_id, MAX(version) AS version FROM rules
                         WHERE status != 'deprecated' GROUP BY rule_id
                       ) latest USING(rule_id, version)
                       ORDER BY status, kind, title"""
                ).fetchall()
            else:
                rows = self.connection.execute(
                    """SELECT rules.* FROM rules
                       JOIN (
                         SELECT rule_id, MAX(version) AS version FROM rules
                         WHERE status = 'active' GROUP BY rule_id
                       ) latest USING(rule_id, version)
                       ORDER BY kind, title"""
                ).fetchall()
            return [_decode(row, "definition") or {} for row in rows]

    def archive_rule(self, rule_id: str) -> dict[str, Any]:
        current = self.get_rule(rule_id, include_archived=True)
        if not current:
            raise FileNotFoundError(f"Rule not found: {rule_id}")
        definition = dict(current.get("definition") or {})
        if definition.get("scope") != "user":
            raise PermissionError("built-in Rules cannot be archived")
        with self._lock:
            self.connection.execute(
                "UPDATE rules SET status = 'archived', updated_at = ? WHERE rule_id = ?",
                (utc_now(), rule_id),
            )
            self.connection.commit()
        return self.get_rule(rule_id, include_archived=True) or {}

    def restore_rule(self, rule_id: str) -> dict[str, Any]:
        current = self.get_rule(rule_id, include_archived=True)
        if not current:
            raise FileNotFoundError(f"Rule not found: {rule_id}")
        definition = dict(current.get("definition") or {})
        if definition.get("scope") != "user":
            raise PermissionError("built-in Rules cannot be restored")
        with self._lock:
            self.connection.execute(
                "UPDATE rules SET status = 'active', updated_at = ? WHERE rule_id = ?",
                (utc_now(), rule_id),
            )
            self.connection.commit()
        return self.get_rule(rule_id) or {}

    def put_rule_source(self, value: Mapping[str, object]) -> dict[str, Any]:
        with self._lock:
            self.connection.execute(
                """INSERT INTO rule_sources(
                  source_id, kind, display_name, original_url, local_markdown_path,
                  digest, content_type, size_bytes, license, captured_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                  display_name=excluded.display_name,
                  original_url=excluded.original_url,
                  local_markdown_path=excluded.local_markdown_path,
                  digest=excluded.digest,
                  content_type=excluded.content_type,
                  size_bytes=excluded.size_bytes,
                  license=excluded.license,
                  captured_at=excluded.captured_at,
                  metadata=excluded.metadata""",
                (
                    str(value["source_id"]),
                    str(value["kind"]),
                    str(value["display_name"]),
                    value.get("original_url"),
                    str(value["local_markdown_path"]),
                    str(value["digest"]),
                    str(value["content_type"]),
                    int(value["size_bytes"]),
                    value.get("license"),
                    str(value["captured_at"]),
                    _json(value.get("metadata") or {}),
                ),
            )
            self.connection.commit()
        return self.get_rule_source(str(value["source_id"])) or {}

    def get_rule_source(self, source_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM rule_sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            return _decode(row, "metadata")

    def list_rule_sources(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT * FROM rule_sources ORDER BY captured_at DESC"
            ).fetchall()
            return [_decode(row, "metadata") or {} for row in rows]

    def put_skill_source(self, value: Mapping[str, object]) -> dict[str, Any]:
        now = utc_now()
        with self._lock:
            self.connection.execute(
                """INSERT INTO skill_sources(
                  source_id, kind, display_name, path, mode, trust_state, enabled,
                  auto_enable, revision, status, last_scanned_at, created_at,
                  updated_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                  display_name=excluded.display_name, path=excluded.path,
                  mode=excluded.mode, trust_state=excluded.trust_state,
                  enabled=excluded.enabled, auto_enable=excluded.auto_enable,
                  revision=excluded.revision, status=excluded.status,
                  last_scanned_at=excluded.last_scanned_at,
                  updated_at=excluded.updated_at, metadata=excluded.metadata""",
                (
                    str(value["source_id"]),
                    str(value.get("kind") or "custom"),
                    str(value["display_name"]),
                    str(value["path"]),
                    str(value.get("mode") or "connect"),
                    str(value.get("trust_state") or "untrusted"),
                    int(bool(value.get("enabled", False))),
                    int(bool(value.get("auto_enable", False))),
                    str(value.get("revision") or ""),
                    str(value.get("status") or "connected"),
                    str(value.get("last_scanned_at") or now),
                    str(value.get("created_at") or now),
                    now,
                    _json(value.get("metadata") or {}),
                ),
            )
            self.connection.commit()
        return self.get_skill_source(str(value["source_id"])) or {}

    def get_skill_source(self, source_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM skill_sources WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            return _decode(row, "metadata")

    def list_skill_sources(self, *, include_disconnected: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            sql = "SELECT * FROM skill_sources"
            if not include_disconnected:
                sql += " WHERE status = 'connected'"
            sql += " ORDER BY display_name, source_id"
            rows = self.connection.execute(sql).fetchall()
            return [_decode(row, "metadata") or {} for row in rows]

    def update_skill_source(
        self,
        source_id: str,
        *,
        trust_state: str | None = None,
        enabled: bool | None = None,
        auto_enable: bool | None = None,
        status: str | None = None,
        revision: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        current = self.get_skill_source(source_id)
        if not current:
            raise FileNotFoundError(f"Skill source not found: {source_id}")
        return self.put_skill_source(
            {
                **current,
                "trust_state": trust_state if trust_state is not None else current["trust_state"],
                "enabled": enabled if enabled is not None else bool(current["enabled"]),
                "auto_enable": (
                    auto_enable if auto_enable is not None else bool(current["auto_enable"])
                ),
                "status": status if status is not None else current["status"],
                "revision": revision if revision is not None else current["revision"],
                "metadata": metadata if metadata is not None else current.get("metadata") or {},
                "created_at": current["created_at"],
            }
        )


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
        from .storage import ControlStore

        for project in self.store.list_projects():
            path = Path(str(project["path"]))
            if not path.is_dir():
                self.store.update_project(str(project["project_id"]), status="offline")
                continue
            with ControlStore(path) as store:
                store.reconcile_message_deliveries()
            agent_session_manager(path).reconcile_orphans()


__all__ = [
    "WORKBENCH_SCHEMA_VERSION",
    "WorkbenchRegistry",
    "WorkbenchStore",
    "daemon_state_path",
    "utc_now",
    "workbench_data_dir",
]
