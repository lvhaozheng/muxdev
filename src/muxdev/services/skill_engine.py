"""Skill discovery, policy, and activation for muxdev."""

from __future__ import annotations

import json
import shutil
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..config.runtime import (
    dumps_toml,
    global_skills_path,
    muxdev_home,
    normalize_role,
    project_skills_path,
)
from ..config.loader import deep_merge


PROJECT_SCAN_DIRS = [".agents/skills", ".muxdev/skills", ".claude/skills", "skills"]
GLOBAL_SCAN_DIRS = [".agents/skills", ".muxdev/skills", ".claude/skills", ".codex/skills"]


@dataclass(frozen=True)
class SkillInfo:
    name: str
    path: str
    skill_file: str
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    source: str = ""
    priority: int = 0
    disabled: bool = False
    trust: str = "auto"

    def to_dict(self, *, include_content: bool = False) -> dict[str, object]:
        data = asdict(self)
        if include_content:
            data["content"] = Path(self.skill_file).read_text(encoding="utf-8", errors="replace")
        return data


@dataclass(frozen=True)
class ActivatedSkill:
    skill: SkillInfo
    role: str | None
    reason: str
    injection: str

    def to_dict(self, *, include_content: bool = False) -> dict[str, object]:
        data = self.skill.to_dict(include_content=include_content)
        data.update({"role": self.role, "reason": self.reason, "injection": self.injection})
        return data


def load_skills_config(workspace: Path, *, env: dict[str, str] | None = None) -> dict[str, Any]:
    config: dict[str, Any] = {"version": 1, "dirs": [], "auto": True, "sync": "auto", "bind": {}, "skill": {}}
    for path in (global_skills_path(env), project_skills_path(workspace)):
        if path.exists():
            config = deep_merge(config, _read_toml(path))
    if not isinstance(config.get("bind"), dict):
        config["bind"] = {}
    if not isinstance(config.get("skill"), dict):
        config["skill"] = {}
    if not isinstance(config.get("dirs"), list):
        config["dirs"] = []
    return config


def scan_skills(workspace: Path, *, env: dict[str, str] | None = None, include_disabled: bool = False) -> list[SkillInfo]:
    config = load_skills_config(workspace, env=env)
    candidates: list[SkillInfo] = []
    seen_paths: set[Path] = set()
    for priority, source, root in _scan_roots(workspace, config, env=env):
        resolved = root.expanduser()
        if not resolved.exists():
            continue
        for skill_md in _skill_files(resolved):
            path = skill_md.resolve()
            if path in seen_paths:
                continue
            seen_paths.add(path)
            info = _skill_from_file(skill_md, source=source, priority=priority, config=config)
            if include_disabled or not info.disabled:
                candidates.append(info)
    by_name: dict[str, SkillInfo] = {}
    for skill in sorted(candidates, key=lambda item: item.priority, reverse=True):
        by_name.setdefault(skill.name, skill)
    return sorted(by_name.values(), key=lambda item: (-item.priority, item.name))


def resolve_active_skills(
    workspace: Path,
    *,
    task: str,
    roles: list[str] | None = None,
    stage: str | None = None,
    provider: str = "mock",
    explicit: list[str] | None = None,
    env: dict[str, str] | None = None,
    include_content: bool = True,
) -> list[dict[str, object]]:
    config = load_skills_config(workspace, env=env)
    available = {skill.name: skill for skill in scan_skills(workspace, env=env)}
    activated: list[ActivatedSkill] = []
    activated_names: set[tuple[str, str | None]] = set()

    for spec in explicit or []:
        role, name = _parse_skill_spec(spec)
        skill = _lookup_skill(name, available, workspace)
        if skill:
            _append_activation(activated, activated_names, skill, role, "task_explicit", provider)

    for role in roles or []:
        for name in _role_bindings(config, role):
            skill = available.get(name)
            if skill:
                _append_activation(activated, activated_names, skill, normalize_role(role), "role_binding", provider)

    if bool(config.get("auto", True)):
        task_lc = task.lower()
        for skill in available.values():
            if _matches_task(skill, task_lc):
                _append_activation(activated, activated_names, skill, None, "metadata_match", provider)

    return [item.to_dict(include_content=include_content) for item in activated]


def add_skill(workspace: Path, source: str, *, name: str | None = None, global_scope: bool = False) -> SkillInfo:
    root = (Path.home() / ".agents" / "skills") if global_scope else workspace / ".agents" / "skills"
    source_path = Path(source).expanduser()
    if source.startswith("builtin:"):
        skill_name = name or source.split(":", 1)[1]
        target = root / _safe_name(skill_name)
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(f"---\nname: {skill_name}\n---\n# {skill_name}\n\nBuilt-in skill placeholder.\n", encoding="utf-8")
        return _skill_from_file(target / "SKILL.md", source="global" if global_scope else "project", priority=100)
    if source_path.exists():
        skill_name = name or source_path.stem
        if source_path.is_dir() and (source_path / "SKILL.md").exists():
            parsed = _skill_from_file(source_path / "SKILL.md", source="source", priority=0)
            skill_name = name or parsed.name
        target = root / _safe_name(skill_name)
        target.mkdir(parents=True, exist_ok=True)
        if source_path.is_dir():
            _copy_tree_contents(source_path, target)
        else:
            shutil.copy2(source_path, target / source_path.name)
        if not (target / "SKILL.md").exists():
            (target / "SKILL.md").write_text(f"---\nname: {skill_name}\n---\n# {skill_name}\n\nLocal muxdev skill.\n", encoding="utf-8")
        return _skill_from_file(target / "SKILL.md", source="global" if global_scope else "project", priority=100)
    skill_name = name or source
    target = root / _safe_name(skill_name)
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(f"---\nname: {skill_name}\n---\n# {skill_name}\n\nLocal muxdev skill.\n", encoding="utf-8")
    return _skill_from_file(target / "SKILL.md", source="global" if global_scope else "project", priority=100)


def skill_show(workspace: Path, name: str) -> dict[str, object]:
    for skill in scan_skills(workspace, include_disabled=True):
        if skill.name == name:
            return skill.to_dict(include_content=True)
    raise ValueError(f"skill not found: {name}")


def bind_skill(workspace: Path, role: str, skill: str, *, project: bool = True, env: dict[str, str] | None = None) -> dict[str, object]:
    path = project_skills_path(workspace) if project else global_skills_path(env)
    data = _read_toml(path) if path.exists() else {"version": 1}
    bind = data.setdefault("bind", {})
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
    role_key = normalize_role(role)
    values = [item for item in bind.get(role_key, []) if item != skill] if isinstance(bind.get(role_key), list) else []
    bind[role_key] = values
    _write_skills_toml(path, data)
    return {"path": str(path), "role": role_key, "skill": skill, "status": "unbound"}


def set_skill_policy(
    workspace: Path,
    name: str,
    *,
    project: bool = True,
    disabled: bool | None = None,
    trust: str | None = None,
    auto: bool | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, object]:
    path = project_skills_path(workspace) if project else global_skills_path(env)
    data = _read_toml(path) if path.exists() else {"version": 1}
    policies = data.setdefault("skill", {})
    policy = policies.setdefault(name, {})
    if disabled is not None:
        policy["disabled"] = disabled
    if trust is not None:
        policy["trust"] = trust
    if auto is not None:
        policy["auto"] = auto
    _write_skills_toml(path, data)
    return {"path": str(path), "name": name, "policy": policy}


def skill_doctor(workspace: Path) -> dict[str, object]:
    all_skills = scan_skills(workspace, include_disabled=True)
    names: dict[str, list[SkillInfo]] = {}
    warnings: list[str] = []
    errors: list[str] = []
    for skill in all_skills:
        names.setdefault(skill.name, []).append(skill)
        if not Path(skill.skill_file).exists():
            errors.append(f"missing SKILL.md for {skill.name}: {skill.skill_file}")
        if not skill.description:
            warnings.append(f"skill has no description: {skill.name}")
    for name, rows in names.items():
        if len(rows) > 1:
            warnings.append(f"duplicate skill name uses highest priority: {name}")
    config = load_skills_config(workspace)
    available = {skill.name for skill in all_skills}
    for role, values in config.get("bind", {}).items():
        if isinstance(values, list):
            for value in values:
                if value not in available:
                    warnings.append(f"binding points to missing skill: {role}={value}")
    return {"valid": not errors, "errors": errors, "warnings": warnings, "skills": [skill.to_dict() for skill in all_skills]}


def sync_skills(workspace: Path) -> dict[str, object]:
    rows = scan_skills(workspace, include_disabled=True)
    index = workspace / ".muxdev" / "skills-index.json"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(json.dumps([row.to_dict() for row in rows], ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": str(index), "skills": len(rows), "status": "synced"}


def validate_skill_path(path: Path) -> dict[str, object]:
    skill_file = path / "SKILL.md" if path.is_dir() else path
    errors = []
    if not skill_file.exists():
        errors.append(f"SKILL.md not found: {skill_file}")
    else:
        info = _skill_from_file(skill_file, source="validation", priority=0)
        if not info.name:
            errors.append("skill name is missing")
    return {"valid": not errors, "errors": errors, "path": str(skill_file)}


def export_skill(workspace: Path, name: str, output: Path | None = None) -> dict[str, object]:
    data = skill_show(workspace, name)
    source = Path(str(data["path"]))
    target = output or workspace / ".muxdev" / "exports" / f"{name}"
    target.mkdir(parents=True, exist_ok=True)
    _copy_tree_contents(source, target)
    return {"name": name, "path": str(target), "status": "exported"}


def remove_skill(workspace: Path, name: str) -> dict[str, object]:
    for skill in scan_skills(workspace, include_disabled=True):
        if skill.name == name:
            path = Path(skill.path)
            if workspace.resolve() in path.resolve().parents:
                shutil.rmtree(path, ignore_errors=True)
                return {"name": name, "path": str(path), "status": "removed"}
            return {"name": name, "path": str(path), "status": "not_removed", "reason": "outside workspace"}
    raise ValueError(f"skill not found: {name}")


def _scan_roots(workspace: Path, config: dict[str, Any], *, env: dict[str, str] | None = None) -> list[tuple[int, str, Path]]:
    roots: list[tuple[int, str, Path]] = []
    for index, item in enumerate(config.get("dirs", [])):
        roots.append((700 - index, "configured", (workspace / str(item)).resolve() if not str(item).startswith("~") else Path(str(item)).expanduser()))
    roots.extend((600 - index, "project", workspace / item) for index, item in enumerate(PROJECT_SCAN_DIRS))
    home = muxdev_home(env).parent
    roots.extend((400 - index, "global", home / item) for index, item in enumerate(GLOBAL_SCAN_DIRS))
    builtin = _builtin_skills_dir()
    roots.append((100, "builtin", builtin))
    return roots


def _skill_files(root: Path) -> list[Path]:
    if (root / "SKILL.md").exists():
        return [root / "SKILL.md"]
    return sorted(path for path in root.rglob("SKILL.md") if "__pycache__" not in path.parts)


def _skill_from_file(path: Path, *, source: str, priority: int, config: dict[str, Any] | None = None) -> SkillInfo:
    text = path.read_text(encoding="utf-8", errors="replace")
    meta, body = _parse_frontmatter(text)
    name = str(meta.get("name") or path.parent.name)
    description = str(meta.get("description") or _first_heading(body) or "")
    keywords_raw = meta.get("keywords", [])
    if isinstance(keywords_raw, str):
        keywords = [item.strip() for item in keywords_raw.split(",") if item.strip()]
    elif isinstance(keywords_raw, list):
        keywords = [str(item) for item in keywords_raw]
    else:
        keywords = []
    policy = {}
    if config and isinstance(config.get("skill"), dict):
        policy = config["skill"].get(name, {}) if isinstance(config["skill"].get(name, {}), dict) else {}
    return SkillInfo(
        name=name,
        path=str(path.parent),
        skill_file=str(path),
        description=description,
        keywords=keywords,
        source=source,
        priority=priority,
        disabled=bool(policy.get("disabled", False)),
        trust=str(policy.get("trust", "auto")),
    )


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                raw = "\n".join(lines[1:index])
                data = yaml.safe_load(raw) or {}
                return (data if isinstance(data, dict) else {}), "\n".join(lines[index + 1 :])
    return {}, text


def _first_heading(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def _parse_skill_spec(value: str) -> tuple[str | None, str]:
    if "=" in value:
        role, name = value.split("=", 1)
        return normalize_role(role), name.strip()
    return None, value.strip()


def _lookup_skill(name_or_path: str, available: dict[str, SkillInfo], workspace: Path) -> SkillInfo | None:
    if name_or_path in available:
        return available[name_or_path]
    path = (workspace / name_or_path).resolve() if not Path(name_or_path).is_absolute() else Path(name_or_path)
    if path.is_dir() and (path / "SKILL.md").exists():
        return _skill_from_file(path / "SKILL.md", source="task", priority=900)
    if path.name == "SKILL.md" and path.exists():
        return _skill_from_file(path, source="task", priority=900)
    return None


def _role_bindings(config: dict[str, Any], role: str) -> list[str]:
    bind = config.get("bind", {})
    values = bind.get(normalize_role(role), []) if isinstance(bind, dict) else []
    if isinstance(values, str):
        return [values]
    return [str(item) for item in values] if isinstance(values, list) else []


def _matches_task(skill: SkillInfo, task_lc: str) -> bool:
    haystacks = [skill.name.lower(), skill.description.lower(), *[item.lower() for item in skill.keywords]]
    return any(value and value in task_lc for value in haystacks)


def _append_activation(
    activated: list[ActivatedSkill],
    names: set[tuple[str, str | None]],
    skill: SkillInfo,
    role: str | None,
    reason: str,
    provider: str,
) -> None:
    key = (skill.name, role)
    if key in names:
        return
    names.add(key)
    activated.append(ActivatedSkill(skill=skill, role=role, reason=reason, injection=_injection_mode(provider)))


def _injection_mode(provider: str) -> str:
    provider_lc = provider.lower()
    if provider_lc in {"codex", "claude", "claude-code"}:
        return "native_or_passthrough"
    if provider_lc in {"qwen", "kimi"}:
        return "prompt"
    if provider_lc == "mock":
        return "context"
    return "prompt"


def _read_toml(path: Path) -> dict[str, Any]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _write_skills_toml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_toml(data), encoding="utf-8")


def _copy_tree_contents(source: Path, target: Path) -> None:
    for child in source.iterdir():
        destination = target / child.name
        if child.is_dir():
            shutil.copytree(child, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(child, destination)


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value).strip("-") or "skill"


def _builtin_skills_dir() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "skills"
        if candidate.exists():
            return candidate
    return current.parents[1] / "skills"
