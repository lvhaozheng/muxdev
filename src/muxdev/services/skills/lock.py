"""Skill lock v2 with whole-directory integrity hashes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ...models import utc_now
from ...storage.contracts import sha256_file
from ...storage.memory import MemoryStore
from .discovery import scan_skills
from .model import SkillInfo


LOCK_VERSION = "muxdev.skill_lock.v2"


def write_skill_lock(workspace: Path, *, promote_memory: bool = True) -> dict[str, Any]:
    """Write `.muxdev/skill-lock.json` with directory-level hashes."""
    rows = [_lock_row(skill) for skill in scan_skills(workspace, include_disabled=True)]
    payload = {
        "contract_version": LOCK_VERSION,
        "generated_at": utc_now(),
        "skills": sorted(rows, key=lambda row: str(row["name"])),
    }
    path = workspace / ".muxdev" / "skill-lock.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    memories: list[dict[str, object]] = []
    if promote_memory:
        with MemoryStore(workspace) as store:
            for row in payload["skills"]:
                tree_hash = str(row.get("hashes", {}).get("tree", ""))
                memories.append(
                    store.propose_claim(
                        claim=f"Skill {row['name']} {row.get('version') or ''} is proposed for roles: {', '.join(row['compatible_roles']) or 'any'}",
                        scope="project",
                        kind="skill_memory",
                        role="any",
                        confidence=0.45,
                        evidence=[
                            {
                                "kind": "skill_lock",
                                "path": str(path),
                                "tree_hash": tree_hash,
                                "summary": f"skill lock for {row['name']}",
                            }
                        ],
                    )
                )
    return {"path": str(path), "skills": payload["skills"], "memory_proposals": memories}


def verify_skill_lock(workspace: Path, *, name: str | None = None) -> dict[str, Any]:
    path = workspace / ".muxdev" / "skill-lock.json"
    if not path.exists():
        return {"valid": False, "path": str(path), "errors": ["skill lock not found"], "warnings": [], "skills": []}
    try:
        locked = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"valid": False, "path": str(path), "errors": [f"skill lock is invalid JSON: {exc}"], "warnings": [], "skills": []}
    locked_rows = locked.get("skills", []) if isinstance(locked, dict) else []
    current = {row["name"]: row for row in (_lock_row(skill) for skill in scan_skills(workspace, include_disabled=True))}
    errors: list[str] = []
    warnings: list[str] = []
    rows: list[dict[str, object]] = []
    for row in locked_rows if isinstance(locked_rows, list) else []:
        if not isinstance(row, dict):
            continue
        skill_name = str(row.get("name") or "")
        if name and skill_name != name:
            continue
        current_row = current.get(skill_name)
        if not current_row:
            errors.append(f"locked skill is missing from discovery: {skill_name}")
            rows.append({"name": skill_name, "status": "missing"})
            continue
        status = "valid"
        expected = row.get("hashes", {}) if isinstance(row.get("hashes"), dict) else {}
        actual = current_row.get("hashes", {}) if isinstance(current_row.get("hashes"), dict) else {}
        for key in ("skill_file", "tree", "scripts", "references", "assets"):
            if expected.get(key) != actual.get(key):
                status = "drift"
                errors.append(f"lock drift detected for {skill_name}: {key}")
        rows.append({"name": skill_name, "status": status, "expected": expected, "actual": actual})
    for skill_name in sorted(set(current) - {str(row.get("name") or "") for row in locked_rows if isinstance(row, dict)}):
        if name and skill_name != name:
            continue
        warnings.append(f"discovered skill is not locked: {skill_name}")
        rows.append({"name": skill_name, "status": "unlocked"})
    return {"valid": not errors, "path": str(path), "errors": errors, "warnings": warnings, "skills": rows}


def skill_tree_hash(skill: SkillInfo) -> str:
    return _hash_tree(Path(skill.path))


def _lock_row(skill: SkillInfo) -> dict[str, Any]:
    skill_file = Path(skill.skill_file)
    root = Path(skill.path)
    hashes = {
        "skill_file": sha256_file(skill_file) if skill_file.exists() else "",
        "tree": _hash_tree(root),
        "scripts": _hash_tree(root / "scripts"),
        "references": _hash_tree(root / "references"),
        "assets": _hash_tree(root / "assets"),
    }
    return {
        "name": skill.name,
        "version": skill.version,
        "path": skill.path,
        "skill_file": skill.skill_file,
        "source": {"type": skill.source, "path": skill.source_path or skill.path},
        "hashes": hashes,
        "skill_hash": hashes["tree"],
        "compatible_roles": list(skill.roles),
        "roles": list(skill.roles),
        "permissions": skill.permissions.to_dict(),
        "trust": {"state": skill.trust},
        "trust_state": skill.trust,
        "disabled": skill.disabled,
        "validation": {
            "valid": not skill.validation_errors,
            "errors": list(skill.validation_errors),
            "warnings": list(skill.validation_warnings),
        },
    }


def _hash_tree(root: Path) -> str:
    if not root.exists():
        return ""
    if root.is_file():
        return sha256_file(root)
    digest = hashlib.sha256()
    for path in sorted(child for child in root.rglob("*") if child.is_file() and "__pycache__" not in child.parts):
        rel = path.relative_to(root).as_posix()
        digest.update(rel.encode("utf-8"))
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return "sha256:" + digest.hexdigest()
