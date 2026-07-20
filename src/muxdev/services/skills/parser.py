"""SKILL.md and muxdev.skill.toml parsing helpers."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import yaml

from .model import SkillPermissions


def parse_skill_document(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return parse_frontmatter(text)


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                raw = "\n".join(lines[1:index])
                data = yaml.safe_load(raw) or {}
                return (data if isinstance(data, dict) else {}), "\n".join(lines[index + 1 :])
    return {}, text


def parse_muxdev_policy(skill_dir: Path) -> dict[str, Any]:
    path = skill_dir / "muxdev.skill.toml"
    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def first_heading(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def metadata_map(meta: dict[str, Any]) -> dict[str, Any]:
    value = meta.get("metadata", {})
    return value if isinstance(value, dict) else {}


def string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            stripped = stripped[1:-1]
        return [item.strip().strip("'\"") for item in stripped.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if str(value).strip() else []


def skill_version(meta: dict[str, Any]) -> str | None:
    nested = metadata_map(meta)
    value = meta.get("version", nested.get("version"))
    return str(value) if value not in {None, ""} else None


def skill_roles(meta: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    nested = metadata_map(meta)
    activation = policy.get("activation", {}) if isinstance(policy.get("activation"), dict) else {}
    roles: list[str] = []
    for value in (
        meta.get("compatible_roles"),
        meta.get("role"),
        nested.get("compatible_roles"),
        activation.get("roles"),
    ):
        roles.extend(string_list(value))
    return dedupe(roles)


def skill_stages(policy: dict[str, Any]) -> list[str]:
    activation = policy.get("activation", {}) if isinstance(policy.get("activation"), dict) else {}
    return dedupe(string_list(activation.get("stages")))


def skill_file_patterns(meta: dict[str, Any], policy: dict[str, Any]) -> list[str]:
    nested = metadata_map(meta)
    activation = policy.get("activation", {}) if isinstance(policy.get("activation"), dict) else {}
    patterns: list[str] = []
    patterns.extend(string_list(meta.get("file_patterns")))
    patterns.extend(string_list(nested.get("file_patterns")))
    patterns.extend(string_list(activation.get("file_patterns")))
    return dedupe(patterns)


def skill_keywords(meta: dict[str, Any]) -> list[str]:
    nested = metadata_map(meta)
    keywords: list[str] = []
    keywords.extend(string_list(meta.get("keywords")))
    keywords.extend(string_list(nested.get("tags")))
    return dedupe(keywords)


def skill_risk_level(meta: dict[str, Any], policy: dict[str, Any]) -> str:
    nested = metadata_map(meta)
    value = meta.get("risk_level", nested.get("risk_level"))
    if not value:
        trust = policy.get("trust", {}) if isinstance(policy.get("trust"), dict) else {}
        value = trust.get("risk_level")
    return str(value or "medium")


def skill_permissions(policy: dict[str, Any]) -> SkillPermissions:
    permissions = policy.get("permissions", {}) if isinstance(policy.get("permissions"), dict) else {}
    return SkillPermissions(
        read_workspace=bool(permissions.get("read_workspace", True)),
        write_workspace=bool(permissions.get("write_workspace", False)),
        shell=bool(permissions.get("shell", False)),
        network=bool(permissions.get("network", False)),
        secrets=bool(permissions.get("secrets", False)),
        mcp=tuple(string_list(permissions.get("mcp"))),
    )


def skill_auto(policy: dict[str, Any], policy_override: dict[str, Any]) -> bool:
    activation = policy.get("activation", {}) if isinstance(policy.get("activation"), dict) else {}
    if "auto" in policy_override:
        return bool(policy_override["auto"])
    if "auto" in activation:
        return bool(activation["auto"])
    return True


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
