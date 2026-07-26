"""Skill discovery and policy loading."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from ...config.loader import deep_merge
from .model import SkillInfo
from .parser import (
    first_heading,
    parse_muxdev_policy,
    parse_skill_document,
    skill_auto,
    skill_file_patterns,
    skill_keywords,
    skill_permissions,
    skill_risk_level,
    skill_roles,
    skill_stages,
    skill_version,
)
from .trust import normalize_trust_state


PROJECT_SCAN_DIRS = [
    ".muxdev/skills",
    ".agents/skills",
    "skills",
    ".codex/skills",
    ".claude/skills",
    ".deepcode/skills",
    ".openhands/skills",
    ".continue/skills",
    ".cline/skills",
    ".roo/skills",
]
GLOBAL_SCAN_DIRS = [
    ".muxdev/skills",
    ".agents/skills",
    ".codex/skills",
    ".claude/skills",
    ".deepcode/skills",
]


def load_skills_config(workspace: Path, *, env: dict[str, str] | None = None) -> dict[str, Any]:
    config: dict[str, Any] = {"version": 1, "dirs": [], "auto": True, "sync": "auto", "bind": {}, "skill": {}}
    for path in (_muxdev_home(env) / "skills.toml", workspace / ".muxdev" / "skills.toml"):
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
    candidates = scan_all_skills(workspace, env=env, include_disabled=include_disabled)
    by_name: dict[str, SkillInfo] = {}
    for skill in sorted(candidates, key=lambda item: item.priority, reverse=True):
        by_name.setdefault(skill.name, skill)
    return sorted(by_name.values(), key=lambda item: (-item.priority, item.name))


def scan_all_skills(workspace: Path, *, env: dict[str, str] | None = None, include_disabled: bool = False) -> list[SkillInfo]:
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
            try:
                info = skill_from_file(skill_md, source=source, priority=priority, config=config)
            except Exception as exc:
                info = SkillInfo(
                    name=skill_md.parent.name,
                    path=str(skill_md.parent),
                    skill_file=str(skill_md),
                    source=source,
                    priority=priority,
                    disabled=True,
                    trust="needs_review",
                    validation_errors=[f"SKILL.md frontmatter invalid: {exc}"],
                )
            if include_disabled or not info.disabled:
                candidates.append(info)
    return sorted(candidates, key=lambda item: (-item.priority, item.name, item.path))


def skill_from_file(path: Path, *, source: str, priority: int, config: dict[str, Any] | None = None) -> SkillInfo:
    meta, body = parse_skill_document(path)
    local_policy = parse_muxdev_policy(path.parent)
    name = str(meta.get("name") or path.parent.name).strip() or path.parent.name
    description = str(meta.get("description") or first_heading(body) or "")
    config_policy = {}
    if config and isinstance(config.get("skill"), dict):
        value = config["skill"].get(name, {})
        config_policy = value if isinstance(value, dict) else {}
    trust_state = normalize_trust_state(config_policy.get("trust"), source=source)
    disabled = bool(config_policy.get("disabled", False))
    version = skill_version(meta)
    roles = skill_roles(meta, local_policy)
    file_patterns = skill_file_patterns(meta, local_policy)
    errors, warnings = _validate_basic(path, name, description, config_policy, local_policy)
    return SkillInfo(
        name=name,
        path=str(path.parent),
        skill_file=str(path),
        description=description,
        keywords=skill_keywords(meta),
        source=source,
        priority=priority,
        disabled=disabled,
        trust=trust_state,
        version=version,
        roles=roles,
        stages=skill_stages(local_policy),
        file_patterns=file_patterns,
        risk_level=skill_risk_level(meta, local_policy),
        permissions=skill_permissions(local_policy),
        auto=skill_auto(local_policy, config_policy),
        source_path=str(path.parent),
        validation_errors=errors,
        validation_warnings=warnings,
    )


def _scan_roots(workspace: Path, config: dict[str, Any], *, env: dict[str, str] | None = None) -> list[tuple[int, str, Path]]:
    roots: list[tuple[int, str, Path]] = []
    for index, item in enumerate(config.get("dirs", [])):
        text = str(item)
        root = Path(text).expanduser() if text.startswith("~") else (workspace / text).resolve()
        roots.append((800 - index, "configured", root))
    roots.extend((700 - index, "project", workspace / item) for index, item in enumerate(PROJECT_SCAN_DIRS))
    home = _muxdev_home(env).parent
    roots.extend((400 - index, "global", home / item) for index, item in enumerate(GLOBAL_SCAN_DIRS))
    roots.append((100, "builtin", _builtin_skills_dir()))
    return roots


def _skill_files(root: Path) -> list[Path]:
    if (root / "SKILL.md").exists():
        return [root / "SKILL.md"]
    return sorted(path for path in root.rglob("SKILL.md") if "__pycache__" not in path.parts)


def _validate_basic(
    path: Path,
    name: str,
    description: str,
    config_policy: dict[str, Any],
    local_policy: dict[str, Any],
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not name:
        errors.append("skill name is missing")
    if not description:
        warnings.append("description is missing; skill cannot be safely auto-selected")
    if path.parent.name != _safe_name(name):
        warnings.append("skill directory name differs from skill name")
    if local_policy and int(local_policy.get("version", 1) or 1) > 1:
        warnings.append("muxdev.skill.toml version is newer than this muxdev build")
    if bool(config_policy.get("disabled", False)):
        warnings.append("skill is disabled by policy")
    if "delivery_gate" in local_policy:
        warnings.append("legacy [delivery_gate] is ignored; move requirements to evidence-policy.yaml")
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        content = ""
    if "## Delivery Standard" in content:
        warnings.append("legacy Delivery Standard text is guidance only and cannot change the Evidence Policy")
    return errors, warnings


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value).strip("-") or "skill"


def _builtin_skills_dir() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "skills"
        if candidate.exists() and candidate != current.parent:
            return candidate
    return current.parents[3] / "skills"


def _muxdev_home(env: dict[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    return Path(values.get("MUXDEV_HOME") or Path.home() / ".muxdev").expanduser()


def _read_toml(path: Path) -> dict[str, Any]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}
