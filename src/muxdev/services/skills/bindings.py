"""Role binding helpers for skills.toml."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from ...config.runtime import dumps_toml, global_skills_path, normalize_role, project_skills_path


def bind_skill(workspace: Path, role: str, skill: str, *, project: bool = True, env: dict[str, str] | None = None) -> dict[str, object]:
    path = project_skills_path(workspace) if project else global_skills_path(env)
    data = _read_toml(path) if path.exists() else {"version": 1}
    bind = data.setdefault("bind", {})
    if not isinstance(bind, dict):
        bind = {}
        data["bind"] = bind
    role_key = normalize_role(role)
    values = bind.setdefault(role_key, [])
    if not isinstance(values, list):
        values = []
    if skill not in values:
        values.append(skill)
    bind[role_key] = values
    _write_skills_toml(path, data)
    return {"path": str(path), "role": role_key, "skill": skill, "status": "bound"}


def unbind_skill(workspace: Path, role: str, skill: str, *, project: bool = True, env: dict[str, str] | None = None) -> dict[str, object]:
    path = project_skills_path(workspace) if project else global_skills_path(env)
    data = _read_toml(path) if path.exists() else {"version": 1}
    bind = data.setdefault("bind", {})
    if not isinstance(bind, dict):
        bind = {}
        data["bind"] = bind
    role_key = normalize_role(role)
    values = [item for item in bind.get(role_key, []) if item != skill] if isinstance(bind.get(role_key), list) else []
    bind[role_key] = values
    _write_skills_toml(path, data)
    return {"path": str(path), "role": role_key, "skill": skill, "status": "unbound"}


def _read_toml(path: Path) -> dict[str, Any]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _write_skills_toml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_toml(data), encoding="utf-8")
