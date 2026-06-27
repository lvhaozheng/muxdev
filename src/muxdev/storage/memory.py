"""Evidence-grounded layered memory store.

Memory is intentionally local and reviewable. P3 keeps the original
propose/approve/query loop, then adds layer, scope, promotion, inbox, and
context-packet metadata so temporary session/task facts do not silently become
long-term project context.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from ..config.loader import path_config
from ..models import utc_now


DEFAULT_TTL_DAYS = 180
MEMORY_LAYERS = ("session", "run", "branch", "project", "workspace", "user")
CONTEXT_LAYERS = ("session", "run", "branch", "project", "user")
PROJECT_MEMORY_LAYERS = {"project", "workspace", "user"}


MEMORY_ITEM_COLUMNS: dict[str, str] = {
    "layer": "TEXT NOT NULL DEFAULT 'project'",
    "scope_id": "TEXT",
    "source_type": "TEXT NOT NULL DEFAULT 'manual'",
    "source_uri": "TEXT",
    "valid_from": "TEXT",
    "valid_until": "TEXT",
    "last_used_at": "TEXT",
    "usage_count": "INTEGER NOT NULL DEFAULT 0",
    "promotion_state": "TEXT NOT NULL DEFAULT 'proposed'",
    "supersedes": "TEXT",
    "visibility": "TEXT NOT NULL DEFAULT 'default'",
    "tags_json": "TEXT NOT NULL DEFAULT '[]'",
    "embedding_ref": "TEXT",
}


class MemoryStore:
    """Project-local memory database facade."""

    def __init__(self, workspace: Path, db_path: Path | None = None):
        self.workspace = workspace
        self.db_path = db_path or path_config(workspace, "runtime_root") / "memory.sqlite"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=OFF")
        self._init_schema()

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS memory_items (
              id TEXT PRIMARY KEY,
              scope TEXT NOT NULL,
              layer TEXT NOT NULL DEFAULT 'project',
              scope_id TEXT,
              kind TEXT NOT NULL,
              claim TEXT NOT NULL,
              role TEXT,
              status TEXT NOT NULL,
              confidence REAL NOT NULL,
              ttl_days INTEGER NOT NULL,
              created_from_run TEXT,
              source_type TEXT NOT NULL DEFAULT 'manual',
              source_uri TEXT,
              valid_from TEXT,
              valid_until TEXT,
              last_used_at TEXT,
              usage_count INTEGER NOT NULL DEFAULT 0,
              promotion_state TEXT NOT NULL DEFAULT 'proposed',
              supersedes TEXT,
              visibility TEXT NOT NULL DEFAULT 'default',
              tags_json TEXT NOT NULL DEFAULT '[]',
              embedding_ref TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              expires_at TEXT
            );
            CREATE TABLE IF NOT EXISTS memory_evidence (
              id TEXT PRIMARY KEY,
              memory_id TEXT NOT NULL,
              kind TEXT NOT NULL,
              path TEXT NOT NULL,
              sha256 TEXT,
              summary TEXT,
              created_at TEXT NOT NULL,
              FOREIGN KEY(memory_id) REFERENCES memory_items(id)
            );
            CREATE TABLE IF NOT EXISTS memory_contradictions (
              contradiction_id TEXT PRIMARY KEY,
              memory_id TEXT NOT NULL,
              conflicting_memory_id TEXT NOT NULL,
              claim TEXT NOT NULL,
              conflicting_claim TEXT NOT NULL,
              reason TEXT NOT NULL,
              status TEXT NOT NULL,
              quarantine_target TEXT,
              metadata_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            """
        )
        self._ensure_memory_item_columns()
        self.conn.commit()

    def status(self) -> dict[str, object]:
        rows = self.conn.execute("SELECT status, COUNT(*) AS count FROM memory_items GROUP BY status").fetchall()
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        layers = self.conn.execute("SELECT layer, COUNT(*) AS count FROM memory_items GROUP BY layer").fetchall()
        layer_counts = {str(row["layer"]): int(row["count"]) for row in layers}
        return {"path": str(self.db_path), "counts": counts, "layers": layer_counts, "inbox": self.inbox_counts(), "total": sum(counts.values())}

    def list_items(
        self,
        *,
        status: str | None = None,
        layer: str | None = None,
        scope_id: str | None = None,
        promotion_state: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, object]]:
        query = "SELECT * FROM memory_items"
        params: list[object] = []
        conditions: list[str] = []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if layer:
            conditions.append("layer = ?")
            params.append(layer)
        if scope_id:
            conditions.append("scope_id = ?")
            params.append(scope_id)
        if promotion_state:
            conditions.append("promotion_state = ?")
            params.append(promotion_state)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        return [self._decode_item(dict(row)) for row in self.conn.execute(query, params)]

    def query(
        self,
        text: str,
        *,
        roles: list[str] | None = None,
        status: str = "active",
        layers: list[str] | None = None,
        scope_ids: list[str] | None = None,
        limit: int = 8,
    ) -> list[dict[str, object]]:
        roles = [role for role in roles or [] if role]
        terms = {term.lower() for term in text.replace("/", " ").replace("\\", " ").split() if len(term) > 1}
        layers = [layer for layer in layers or [] if layer]
        scope_ids = [scope for scope in scope_ids or [] if scope]
        rows = self.list_items(status=status, limit=500)
        scored: list[tuple[int, dict[str, object]]] = []
        for row in rows:
            claim = str(row.get("claim", "")).lower()
            role = str(row.get("role") or "")
            layer = str(row.get("layer") or "project")
            scope_id = str(row.get("scope_id") or "")
            if layers and layer not in layers:
                continue
            if scope_ids and scope_id and scope_id not in scope_ids:
                continue
            if _is_expired(row):
                continue
            if roles and role and role not in roles and role != "any":
                continue
            text_score = sum(1 for term in terms if term in claim)
            role_score = 1 if role in roles else 0
            if terms and text_score == 0 and role_score == 0:
                continue
            score = text_score
            if not terms:
                score = 1
            score += role_score
            score += _layer_score(layer)
            if score > 0:
                scored.append((score, row))
        scored.sort(key=lambda item: (-item[0], str(item[1].get("updated_at", ""))))
        result = [row for _, row in scored[:limit]]
        self._mark_used([str(row["id"]) for row in result if row.get("id")])
        return result

    def propose_claim(
        self,
        *,
        claim: str,
        scope: str = "project",
        layer: str = "project",
        scope_id: str | None = None,
        kind: str = "project_convention",
        role: str | None = None,
        confidence: float = 0.6,
        ttl_days: int = DEFAULT_TTL_DAYS,
        created_from_run: str | None = None,
        source_type: str = "manual",
        source_uri: str | None = None,
        valid_from: str | None = None,
        valid_until: str | None = None,
        promotion_state: str | None = None,
        supersedes: str | None = None,
        visibility: str = "default",
        tags: list[str] | None = None,
        embedding_ref: str | None = None,
        evidence: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        now = utc_now()
        layer = _normalize_layer(layer)
        promotion_state = promotion_state or "proposed"
        memory_id = "mem_" + uuid4().hex[:10]
        expires_at = _expires_at(ttl_days)
        self.conn.execute(
            """
            INSERT INTO memory_items(
              id, scope, layer, scope_id, kind, claim, role, status, confidence, ttl_days,
              created_from_run, source_type, source_uri, valid_from, valid_until,
              promotion_state, supersedes, visibility, tags_json, embedding_ref,
              created_at, updated_at, expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                scope,
                layer,
                scope_id,
                kind,
                claim,
                role,
                "proposed",
                confidence,
                ttl_days,
                created_from_run,
                source_type,
                source_uri,
                valid_from or now,
                valid_until,
                promotion_state,
                supersedes,
                visibility,
                json.dumps(tags or [], ensure_ascii=False),
                embedding_ref,
                now,
                now,
                expires_at,
            ),
        )
        for item in evidence or []:
            self.add_evidence(
                memory_id,
                kind=str(item.get("kind") or "artifact"),
                path=Path(str(item.get("path") or "")),
                summary=str(item.get("summary") or ""),
            )
        self.conn.commit()
        return self.get(memory_id)

    def propose_from_run(self, run_dir: Path, run_id: str | None = None) -> list[dict[str, object]]:
        run_id = run_id or run_dir.name
        task_path = run_dir / "task.md"
        report_path = run_dir / "final_report.md"
        design_contract = run_dir / "design" / "design_contract.json"
        task = task_path.read_text(encoding="utf-8", errors="replace").strip() if task_path.exists() else run_id
        evidence: list[dict[str, object]] = []
        if report_path.exists():
            evidence.append({"kind": "final_report", "path": str(report_path), "summary": "validated final report"})
        if design_contract.exists():
            evidence.append({"kind": "design_contract", "path": str(design_contract), "summary": "design contract"})
        claim = f"Run {run_id} completed work for: {task}"
        kind = "architecture_decision" if design_contract.exists() else "project_convention"
        return [
            self.propose_claim(
                claim=claim,
                layer="run",
                scope_id=run_id,
                kind=kind,
                role="any",
                confidence=0.7 if evidence else 0.5,
                created_from_run=run_id,
                source_type="run",
                source_uri=str(run_dir),
                evidence=evidence,
            )
        ]

    def approve(self, memory_id: str) -> dict[str, object]:
        self._set_status(memory_id, "active", promotion_state="approved")
        return self.get(memory_id)

    def promote(self, memory_id: str, *, layer: str = "project", scope_id: str | None = None) -> dict[str, object]:
        layer = _normalize_layer(layer)
        now = utc_now()
        cursor = self.conn.execute(
            """
            UPDATE memory_items
            SET layer = ?, scope = ?, scope_id = ?, status = 'active',
                promotion_state = 'approved', updated_at = ?
            WHERE id = ?
            """,
            (layer, layer, scope_id, now, memory_id),
        )
        self.conn.commit()
        if cursor.rowcount == 0:
            raise KeyError(f"memory item not found: {memory_id}")
        return self.get(memory_id)

    def quarantine(self, memory_id: str) -> dict[str, object]:
        self._set_status(memory_id, "quarantined", promotion_state="quarantined")
        return self.get(memory_id)

    def inbox(self, *, limit: int = 50) -> dict[str, object]:
        self.detect_contradictions()
        return {
            "proposed": self.list_items(status="proposed", limit=limit),
            "promotable": [
                row
                for row in self.list_items(limit=limit * 2)
                if row.get("layer") in {"session", "run", "branch"} and row.get("status") in {"active", "proposed"}
            ][:limit],
            "contradictions": self.list_contradictions(status="pending", limit=limit),
            "quarantined": self.list_items(status="quarantined", limit=limit),
            "expired": [row for row in self.list_items(limit=limit * 2) if _is_expired(row)][:limit],
        }

    def inbox_counts(self) -> dict[str, int]:
        inbox = self.inbox(limit=20)
        return {key: len(value) for key, value in inbox.items() if isinstance(value, list)}

    def list_contradictions(self, *, status: str | None = None, limit: int = 50) -> list[dict[str, object]]:
        query = "SELECT * FROM memory_contradictions"
        params: list[object] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = [dict(row) for row in self.conn.execute(query, params)]
        for row in rows:
            try:
                row["metadata"] = json.loads(str(row.get("metadata_json") or "{}"))
            except json.JSONDecodeError:
                row["metadata"] = {}
        return rows

    def detect_contradictions(self, *, statuses: list[str] | None = None) -> list[dict[str, object]]:
        """Find obvious contradictory memory pairs and persist pending records."""
        statuses = statuses or ["active", "proposed"]
        placeholders = ",".join("?" for _ in statuses)
        rows = [
            dict(row)
            for row in self.conn.execute(
                f"SELECT * FROM memory_items WHERE status IN ({placeholders}) ORDER BY created_at",
                statuses,
            )
        ]
        found: list[dict[str, object]] = []
        for index, left in enumerate(rows):
            for right in rows[index + 1 :]:
                reason = _contradiction_reason(left, right)
                if not reason:
                    continue
                found.append(
                    self.record_contradiction(
                        memory_id=str(left["id"]),
                        conflicting_memory_id=str(right["id"]),
                        claim=str(left["claim"]),
                        conflicting_claim=str(right["claim"]),
                        reason=reason,
                        metadata={"left_status": left.get("status"), "right_status": right.get("status")},
                    )
                )
        return found

    def record_contradiction(
        self,
        *,
        memory_id: str,
        conflicting_memory_id: str,
        claim: str,
        conflicting_claim: str,
        reason: str,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, object]:
        pair = sorted([memory_id, conflicting_memory_id])
        contradiction_id = "mcon_" + hashlib.sha256("|".join(pair).encode("utf-8")).hexdigest()[:12]
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO memory_contradictions(
              contradiction_id, memory_id, conflicting_memory_id, claim, conflicting_claim,
              reason, status, quarantine_target, metadata_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(contradiction_id) DO UPDATE SET
              claim=excluded.claim, conflicting_claim=excluded.conflicting_claim,
              reason=excluded.reason, metadata_json=excluded.metadata_json,
              updated_at=excluded.updated_at
            """,
            (
                contradiction_id,
                memory_id,
                conflicting_memory_id,
                claim,
                conflicting_claim,
                reason,
                "pending",
                None,
                json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                now,
                now,
            ),
        )
        self.conn.commit()
        return self.get_contradiction(contradiction_id)

    def get_contradiction(self, contradiction_id: str) -> dict[str, object]:
        row = self.conn.execute("SELECT * FROM memory_contradictions WHERE contradiction_id = ?", (contradiction_id,)).fetchone()
        if row is None:
            raise KeyError(f"memory contradiction not found: {contradiction_id}")
        result = dict(row)
        try:
            result["metadata"] = json.loads(str(result.get("metadata_json") or "{}"))
        except json.JSONDecodeError:
            result["metadata"] = {}
        return result

    def auto_quarantine_contradictions(self) -> list[dict[str, object]]:
        """Quarantine the lower-confidence side of each pending contradiction."""
        quarantined: list[dict[str, object]] = []
        pending = self.list_contradictions(status="pending", limit=500)
        for row in pending:
            left = self._get_item_or_none(str(row["memory_id"]))
            right = self._get_item_or_none(str(row["conflicting_memory_id"]))
            if left is None or right is None:
                self._update_contradiction_status(str(row["contradiction_id"]), "stale", None)
                continue
            target = _quarantine_target(left, right)
            if str(target.get("status")) != "quarantined":
                self._set_status(str(target["id"]), "quarantined", promotion_state="quarantined")
            self._update_contradiction_status(str(row["contradiction_id"]), "quarantined", str(target["id"]))
            updated = self.get_contradiction(str(row["contradiction_id"]))
            updated["target"] = self.get(str(target["id"]))
            quarantined.append(updated)
        return quarantined

    def get(self, memory_id: str) -> dict[str, object]:
        row = self.conn.execute("SELECT * FROM memory_items WHERE id = ?", (memory_id,)).fetchone()
        if row is None:
            raise KeyError(f"memory item not found: {memory_id}")
        result = self._decode_item(dict(row))
        result["evidence"] = [
            dict(item)
            for item in self.conn.execute(
                "SELECT * FROM memory_evidence WHERE memory_id = ? ORDER BY created_at",
                (memory_id,),
            )
        ]
        return result

    def add_evidence(self, memory_id: str, *, kind: str, path: Path, summary: str = "") -> dict[str, object]:
        evidence_id = "ev_" + uuid4().hex[:10]
        digest = _sha256(path) if path.exists() and path.is_file() else None
        self.conn.execute(
            """
            INSERT INTO memory_evidence(id, memory_id, kind, path, sha256, summary, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (evidence_id, memory_id, kind, str(path), digest, summary, utc_now()),
        )
        return {"id": evidence_id, "memory_id": memory_id, "kind": kind, "path": str(path), "sha256": digest, "summary": summary}

    def _set_status(self, memory_id: str, status: str, *, promotion_state: str | None = None) -> None:
        now = utc_now()
        if promotion_state:
            cursor = self.conn.execute(
                "UPDATE memory_items SET status = ?, promotion_state = ?, updated_at = ? WHERE id = ?",
                (status, promotion_state, now, memory_id),
            )
        else:
            cursor = self.conn.execute(
                "UPDATE memory_items SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, memory_id),
            )
        self.conn.commit()
        if cursor.rowcount == 0:
            raise KeyError(f"memory item not found: {memory_id}")

    def _get_item_or_none(self, memory_id: str) -> dict[str, object] | None:
        row = self.conn.execute("SELECT * FROM memory_items WHERE id = ?", (memory_id,)).fetchone()
        return self._decode_item(dict(row)) if row else None

    def _update_contradiction_status(self, contradiction_id: str, status: str, quarantine_target: str | None) -> None:
        self.conn.execute(
            """
            UPDATE memory_contradictions
            SET status = ?, quarantine_target = ?, updated_at = ?
            WHERE contradiction_id = ?
            """,
            (status, quarantine_target, utc_now(), contradiction_id),
        )
        self.conn.commit()

    def _ensure_memory_item_columns(self) -> None:
        existing = {row["name"] for row in self.conn.execute("PRAGMA table_info(memory_items)").fetchall()}
        for column, declaration in MEMORY_ITEM_COLUMNS.items():
            if column not in existing:
                self.conn.execute(f"ALTER TABLE memory_items ADD COLUMN {column} {declaration}")

    def _mark_used(self, memory_ids: list[str]) -> None:
        if not memory_ids:
            return
        now = utc_now()
        self.conn.executemany(
            "UPDATE memory_items SET last_used_at = ?, usage_count = usage_count + 1 WHERE id = ?",
            [(now, memory_id) for memory_id in memory_ids],
        )
        self.conn.commit()

    def _decode_item(self, row: dict[str, object]) -> dict[str, object]:
        try:
            row["tags"] = json.loads(str(row.get("tags_json") or "[]"))
        except json.JSONDecodeError:
            row["tags"] = []
        row.setdefault("layer", row.get("scope") or "project")
        row.setdefault("promotion_state", row.get("status") or "proposed")
        return row


def _normalize_layer(layer: str) -> str:
    normalized = str(layer or "project").replace("_memory", "").strip().lower()
    return normalized if normalized in MEMORY_LAYERS else "project"


def _is_expired(row: dict[str, object]) -> bool:
    value = row.get("valid_until") or row.get("expires_at")
    if not value:
        return False
    try:
        deadline = datetime.fromisoformat(str(value))
    except ValueError:
        return False
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return deadline < datetime.now(timezone.utc)


def _layer_score(layer: str) -> int:
    return {
        "session": 6,
        "run": 5,
        "branch": 4,
        "project": 3,
        "workspace": 2,
        "user": 1,
    }.get(layer, 0)


def _expires_at(ttl_days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


NEGATION_PATTERNS = (
    r"\bdo\s+not\b",
    r"\bdon't\b",
    r"\bnever\b",
    r"\bmust\s+not\b",
    r"\bshould\s+not\b",
    r"\bavoid\b",
    r"\bforbid(?:den)?\b",
    r"\bno\s+longer\b",
    "不要",
    "不能",
    "禁止",
    "避免",
    "不用",
)

STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "must",
    "should",
    "use",
    "uses",
    "using",
    "project",
    "run",
}


def _contradiction_reason(left: dict[str, object], right: dict[str, object]) -> str | None:
    left_claim = str(left.get("claim") or "")
    right_claim = str(right.get("claim") or "")
    left_negated = _is_negated(left_claim)
    right_negated = _is_negated(right_claim)
    if left_negated == right_negated:
        return None
    shared = _claim_terms(left_claim) & _claim_terms(right_claim)
    if len(shared) < 2:
        return None
    if left.get("kind") and right.get("kind") and left.get("kind") != right.get("kind"):
        return None
    return "negated memory claim overlaps with an active/proposed claim"


def _is_negated(claim: str) -> bool:
    lowered = claim.lower()
    return any(re.search(pattern, lowered) if pattern.startswith("\\") else pattern in lowered for pattern in NEGATION_PATTERNS)


def _claim_terms(claim: str) -> set[str]:
    normalized = claim.lower()
    for pattern in NEGATION_PATTERNS:
        normalized = re.sub(pattern, " ", normalized) if pattern.startswith("\\") else normalized.replace(pattern, " ")
    terms = {term for term in re.findall(r"[\w\-]+", normalized) if len(term) >= 3 and term not in STOPWORDS}
    return terms


def _quarantine_target(left: dict[str, object], right: dict[str, object]) -> dict[str, object]:
    left_status = str(left.get("status") or "")
    right_status = str(right.get("status") or "")
    if left_status == "active" and right_status != "active":
        return right
    if right_status == "active" and left_status != "active":
        return left
    left_conf = float(left.get("confidence") or 0)
    right_conf = float(right.get("confidence") or 0)
    if left_conf != right_conf:
        return left if left_conf < right_conf else right
    return left if str(left.get("created_at") or "") > str(right.get("created_at") or "") else right
