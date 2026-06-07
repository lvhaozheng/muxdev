"""TOML-first runtime configuration for muxdev's main command path."""

from __future__ import annotations

import json
import os
import shutil
import tomllib
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..providers.registry import ProviderProbe, detect_providers
from .loader import ConfigSource, deep_merge, load_config


DEFAULT_PROFILE = "squad"
DEFAULT_GATE = "safe"
DEFAULT_WORKFLOW = "dev"

PROFILES = {
    "solo": {"workflow": "fix", "roles": ["code"]},
    "pair": {"workflow": "fix", "roles": ["code", "review"]},
    "squad": {"workflow": "dev", "roles": ["plan", "code", "test", "review"]},
    "ci": {"workflow": "dev", "roles": ["plan", "code", "test", "review"], "non_interactive": True},
}

GATES = {
    "auto": {"require_approval": []},
    "safe": {"require_approval": ["plan", "write", "shell", "merge"]},
    "strict": {"require_approval": ["plan", "write", "shell", "merge", "external"]},
    "ci": {"require_approval": ["plan", "write", "shell", "merge", "external"], "block_on_approval": True},
}

WORKFLOW_ALIASES = {
    "design": "design",
    "dev": "dev",
    "fix": "fix",
    "refactor": "dev",
    "review": "review",
    "test": "test",
    "ci": "dev",
    "ci-fix": "dev",
    "docs": "docs",
    "software-dev": "software-dev",
}

ROLE_ALIASES = {
    "supervisor": "lead",
    "architect": "plan",
    "implementer": "code",
    "tester": "test",
    "reviewer": "review",
    "security": "secure",
    "doc_writer": "docs",
}

RUNTIME_ROLE_TO_LEGACY_ROLE = {
    "lead": "architect",
    "plan": "architect",
    "requirements": "architect",
    "architect": "architect",
    "code": "implementer",
    "test": "tester",
    "test_strategy": "tester",
    "review": "reviewer",
    "secure": "reviewer",
    "docs": "implementer",
    "memory_curator": "architect",
}

BUILTIN_RUNTIME_CONFIG: dict[str, Any] = {
    "version": 2,
    "profile": DEFAULT_PROFILE,
    "gate": DEFAULT_GATE,
    "automation": {
        "mode": "auto",
        "profile": "auto",
        "depth": "auto",
        "allow_parallel": True,
    },
    "roles": {},
    "memory": {
        "enabled": True,
        "mode": "evidence-grounded",
        "local_only": True,
        "auto_promote_low_risk": True,
        "require_approval_for": ["architecture_decision", "security_rule", "payment_rule"],
        "ttl_days": 180,
        "max_items_per_role": 8,
        "redact_secrets": True,
    },
    "cli": {
        "fallback": ["codex", "claude-code", "qwen", "mock"],
        "codex": {"command": "codex"},
        "claude-code": {"command": "claude"},
        "qwen": {"command": "qwen"},
        "mock": {"command": "mock"},
    },
}


@dataclass(frozen=True)
class RuntimeConfigSource:
    kind: str
    path: Path | str
    exists: bool

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "path": str(self.path), "exists": self.exists}


def muxdev_home(env: dict[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("MUXDEV_HOME") or Path.home() / ".muxdev").expanduser()


def global_config_path(env: dict[str, str] | None = None) -> Path:
    return muxdev_home(env) / "config.toml"


def global_skills_path(env: dict[str, str] | None = None) -> Path:
    return muxdev_home(env) / "skills.toml"


def provider_cache_path(env: dict[str, str] | None = None) -> Path:
    return muxdev_home(env) / "cache" / "providers.json"


def project_config_path(workspace: Path) -> Path:
    return workspace / ".muxdev" / "config.toml"


def project_skills_path(workspace: Path) -> Path:
    return workspace / ".muxdev" / "skills.toml"


def runtime_config_sources(workspace: Path | None = None, env: dict[str, str] | None = None) -> list[RuntimeConfigSource]:
    workspace = Path.cwd() if workspace is None else workspace
    env = os.environ if env is None else env
    global_path = global_config_path(env)
    project_path = project_config_path(workspace)
    sources = [
        RuntimeConfigSource("builtin", "muxdev.runtime.defaults", True),
        RuntimeConfigSource("global", global_path, global_path.exists()),
        RuntimeConfigSource("project", project_path, project_path.exists()),
    ]
    task_path = env.get("MUXDEV_TASK_CONFIG", "")
    if task_path:
        path = Path(task_path).expanduser()
        sources.append(RuntimeConfigSource("task", path, path.exists()))
    return sources


def load_runtime_config(
    workspace: Path | None = None,
    *,
    task_config: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    workspace = Path.cwd() if workspace is None else workspace
    env = os.environ if env is None else env
    config = deepcopy(BUILTIN_RUNTIME_CONFIG)
    for path in (global_config_path(env), project_config_path(workspace)):
        if path.exists():
            config = deep_merge(config, _read_toml(path))
    if task_config:
        config = deep_merge(config, task_config)
    return normalize_runtime_config(config)


def normalize_runtime_config(config: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(config)
    result["profile"] = str(result.get("profile") or DEFAULT_PROFILE)
    result["gate"] = str(result.get("gate") or DEFAULT_GATE)
    if result["profile"] not in PROFILES:
        result["profile"] = DEFAULT_PROFILE
    if result["gate"] not in GATES:
        result["gate"] = DEFAULT_GATE
    roles = result.get("roles", {})
    if not isinstance(roles, dict):
        roles = {}
    result["roles"] = {normalize_role(role): str(provider) for role, provider in roles.items() if provider}
    cli = result.get("cli", {})
    result["cli"] = cli if isinstance(cli, dict) else {}
    return result


def load_task_file(path: Path) -> dict[str, Any]:
    data = _read_toml(path)
    task_config: dict[str, Any] = {}
    for key in ("profile", "gate"):
        if key in data:
            task_config[key] = data[key]
    if isinstance(data.get("roles"), dict):
        task_config["roles"] = data["roles"]
    if "cli" in data and isinstance(data["cli"], dict):
        task_config["cli"] = data["cli"]
    if "task" in data:
        task_config["task"] = str(data["task"])
    if "depth" in data:
        task_config["depth"] = str(data["depth"])
    skills = data.get("skill", [])
    if isinstance(skills, dict):
        skills = [skills]
    if isinstance(skills, list):
        task_config["skill"] = skills
    return task_config


def normalize_role(role: str) -> str:
    key = str(role).strip().replace("-", "_")
    return ROLE_ALIASES.get(key, key)


def legacy_role(role: str) -> str:
    return RUNTIME_ROLE_TO_LEGACY_ROLE.get(normalize_role(role), normalize_role(role))


def parse_role_overrides(values: list[str] | None) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for value in values or []:
        if "=" not in value:
            raise ValueError(f"role override must be role=provider: {value}")
        role, provider = value.split("=", 1)
        role = normalize_role(role)
        provider = provider.strip()
        if not role or not provider:
            raise ValueError(f"role override must be role=provider: {value}")
        overrides[role] = provider
    return overrides


def resolve_task_request(
    *,
    workspace: Path,
    task: str | None,
    command_workflow: str,
    provider: str | None = None,
    workflow: str | None = None,
    profile: str | None = None,
    gate: str | None = None,
    depth: str | None = None,
    role_overrides: list[str] | None = None,
    skill_specs: list[str] | None = None,
    task_file: Path | None = None,
    require_approval: set[str] | None = None,
) -> dict[str, Any]:
    from ..services.automation import resolve_automation

    task_config = load_task_file(task_file) if task_file else {}
    effective = load_runtime_config(workspace, task_config=task_config)
    selected_gate = gate or str(task_config.get("gate") or effective["gate"])
    selected_gate = selected_gate if selected_gate in GATES else DEFAULT_GATE
    resolved_task = task or str(task_config.get("task") or "").strip()
    if not resolved_task and command_workflow == "design":
        resolved_task = "design current workspace"
    if not resolved_task and command_workflow == "review":
        resolved_task = "review current workspace"
    if not resolved_task and command_workflow == "test":
        resolved_task = "test current workspace"
    if not resolved_task and command_workflow == "ci":
        resolved_task = "fix CI failures"
    if not resolved_task:
        raise ValueError("task is required")

    requested_profile = profile or (str(task_config.get("profile")) if task_config.get("profile") else None)
    requested_depth = depth or (str(task_config.get("depth")) if task_config.get("depth") else None)
    automation = resolve_automation(
        workspace=workspace,
        command_workflow=command_workflow,
        task=resolved_task,
        config=effective,
        profile=requested_profile,
        workflow=workflow,
        depth=requested_depth,
    )
    selected_profile = automation.profile if automation.profile in PROFILES else DEFAULT_PROFILE
    selected_workflow = automation.workflow
    if selected_workflow == "profile":
        selected_workflow = str(PROFILES[selected_profile]["workflow"])
    selected_workflow = WORKFLOW_ALIASES.get(selected_workflow, selected_workflow)

    configured_roles = {
        normalize_role(role): str(value)
        for role, value in (effective.get("roles", {}) if isinstance(effective.get("roles"), dict) else {}).items()
        if value and str(value) != "auto"
    }
    roles: dict[str, str] = {}
    for role in automation.roles:
        value = configured_roles.get(role) or configured_roles.get(_provider_role_fallback(role))
        if value:
            roles[role] = value
    roles.update(parse_role_overrides(role_overrides))
    default_provider = provider or choose_default_provider(effective)
    role_providers: dict[str, str] = {}
    for role, value in roles.items():
        if not value:
            continue
        role_providers[normalize_role(role)] = value
        role_providers[legacy_role(role)] = value
    if roles and not provider:
        default_provider = next(iter(roles.values()))
    approvals = set(GATES[selected_gate]["require_approval"])
    approvals.update(require_approval or set())
    skill_inputs = list(skill_specs or [])
    for item in task_config.get("skill", []) if isinstance(task_config.get("skill"), list) else []:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("skill") or "").strip()
            role_name = str(item.get("role") or "").strip()
            if name:
                skill_inputs.append(f"{role_name}={name}" if role_name else name)
        elif item:
            skill_inputs.append(str(item))

    return {
        "task": resolved_task,
        "workspace": str(workspace),
        "provider": default_provider,
        "workflow": selected_workflow,
        "profile": selected_profile,
        "gate": selected_gate,
        "depth": automation.depth,
        "topology": automation.topology,
        "require_approval": sorted(approvals),
        "role_providers": role_providers,
        "runtime_roles": {role: roles.get(role, "auto") for role in automation.roles},
        "skill_specs": skill_inputs,
        "ci_block_on_approval": bool(GATES[selected_gate].get("block_on_approval")),
        "automation": automation.to_dict(),
        "config": effective,
    }


def choose_default_provider(config: dict[str, Any], *, probes: list[ProviderProbe] | None = None) -> str:
    probes = detect_providers() if probes is None else probes
    ready = {probe.provider for probe in probes if str(probe.status) == "ready" or probe.provider == "mock"}
    fallback = config.get("cli", {}).get("fallback", ["mock"]) if isinstance(config.get("cli"), dict) else ["mock"]
    for provider in fallback:
        if str(provider) in ready:
            return str(provider)
    return "mock"


def _provider_role_fallback(role: str) -> str:
    if role in {"requirements", "architect", "memory_curator"}:
        return "plan"
    if role == "test_strategy":
        return "test"
    return role


def recommended_config(probes: list[ProviderProbe] | None = None) -> dict[str, Any]:
    probes = detect_providers() if probes is None else probes
    config = deepcopy(BUILTIN_RUNTIME_CONFIG)
    preferred = choose_default_provider(config, probes=probes)
    ready = {probe.provider for probe in probes if str(probe.status) == "ready"}
    roles = {
        "plan": "claude-code" if "claude-code" in ready else preferred,
        "code": "codex" if "codex" in ready else preferred,
        "test": "qwen" if "qwen" in ready else preferred,
        "review": "codex" if "codex" in ready else preferred,
        "secure": "claude-code" if "claude-code" in ready else preferred,
        "docs": preferred,
        "architect": "codex" if "codex" in ready else preferred,
        "memory_curator": preferred,
    }
    config["roles"] = roles
    return config


def setup_muxdev(
    workspace: Path,
    *,
    global_config: bool = True,
    project: bool = False,
    check: bool = False,
    yes: bool = False,
    full: bool = False,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    env = os.environ if env is None else env
    probes = detect_providers()
    cache = provider_cache_path(env)
    target = project_config_path(workspace) if project else global_config_path(env)
    config = recommended_config(probes)
    payload: dict[str, Any] = {
        "status": "checked" if check else "configured",
        "target": str(target),
        "scope": "project" if project else "global",
        "providers_cache": str(cache),
        "providers": [probe.to_dict() for probe in probes],
        "config": config,
        "full": full,
        "written": False,
    }
    if check:
        payload["status"] = "ok"
        return payload
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps([probe.to_dict() for probe in probes], ensure_ascii=False, indent=2), encoding="utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = _read_toml(target) if target.exists() else {}
    merged = deep_merge(existing, config)
    target.write_text(dumps_toml(merged), encoding="utf-8")
    payload["written"] = True
    if full:
        payload["presets"] = write_full_presets(workspace)
    return payload


def write_full_presets(workspace: Path) -> dict[str, str]:
    root = workspace / ".muxdev" / "presets"
    paths: dict[str, str] = {}
    for kind, name, data in (
        ("workflows", "dev", {"name": "dev", "workflow": "dev"}),
        ("gates", "safe", GATES["safe"]),
        ("roles", "review", {"role": "review", "provider": "codex"}),
    ):
        path = root / kind / f"{name}.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dumps_toml(data), encoding="utf-8")
        paths[f"{kind}.{name}"] = str(path)
    return paths


def set_runtime_config_value(workspace: Path, dotted_key: str, value: str, *, project: bool, env: dict[str, str] | None = None) -> dict[str, Any]:
    path = project_config_path(workspace) if project else global_config_path(env)
    data = _read_toml(path) if path.exists() else {"version": 1}
    target = data
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        current = target.setdefault(part, {})
        if not isinstance(current, dict):
            raise ValueError(f"cannot set nested key below scalar: {part}")
        target = current
    target[parts[-1]] = _parse_scalar(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_toml(data), encoding="utf-8")
    return {"path": str(path), "key": dotted_key, "value": target[parts[-1]]}


def config_check(workspace: Path) -> dict[str, Any]:
    effective = load_runtime_config(workspace)
    warnings: list[str] = []
    errors: list[str] = []
    if effective["profile"] not in PROFILES:
        errors.append(f"unknown profile: {effective['profile']}")
    if effective["gate"] not in GATES:
        errors.append(f"unknown gate: {effective['gate']}")
    legacy = load_config(workspace)
    if "mock" not in legacy.get("providers", {}):
        errors.append("legacy provider config is missing mock")
    return {"valid": not errors, "errors": errors, "warnings": warnings, "effective": effective}


def _read_toml(path: Path) -> dict[str, Any]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"muxdev TOML config must contain a mapping: {path}")
    return data


def dumps_toml(data: dict[str, Any]) -> str:
    lines: list[str] = []
    scalar_items = {key: value for key, value in data.items() if not isinstance(value, dict)}
    for key, value in scalar_items.items():
        lines.append(f"{key} = {_toml_value(value)}")
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        lines.append("")
        _dump_table(lines, key, value)
    return "\n".join(lines).strip() + "\n"


def _dump_table(lines: list[str], name: str, table: dict[str, Any]) -> None:
    lines.append(f"[{name}]")
    nested: list[tuple[str, dict[str, Any]]] = []
    for key, value in table.items():
        if isinstance(value, dict):
            nested.append((key, value))
        else:
            lines.append(f"{key} = {_toml_value(value)}")
    for key, value in nested:
        lines.append("")
        _dump_table(lines, f"{name}.{key}", value)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def _parse_scalar(value: str) -> object:
    text = value.strip()
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text
