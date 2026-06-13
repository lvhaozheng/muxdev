"""Layered configuration loader for muxdev.

The loader implements muxdev's "defaults plus overrides" contract:

1. package defaults define stable M0-M7 behavior,
2. user config captures machine-specific preferences,
3. project config captures shared repository policy, and
4. MUXDEV_CONFIG provides a highest-priority experimental/CI override.

Runtime code should depend on this module instead of hard-coding provider lists,
workflow definitions, command dialects, or `.muxdev` subpaths.
"""

from __future__ import annotations

import os
import platform
from copy import deepcopy
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG_FILES = (
    "paths.yaml",
    "providers.yaml",
    "accounts.yaml",
    "installers.yaml",
    "workflows.yaml",
    "prompt_templates.yaml",
    "workflow_plugins.yaml",
    "ui.yaml",
)

KNOWN_SECTIONS = {
    "accounts",
    "command_dialects",
    "installers",
    "paths",
    "prompt_templates",
    "providers",
    "ui",
    "workflow_plugins",
    "workflows",
}


@dataclass(frozen=True)
class ConfigSource:
    kind: str
    path: str
    exists: bool

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "path": self.path, "exists": self.exists}


def load_config(workspace: Path | None = None, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Return the fully merged configuration for a workspace.

    Missing external files are skipped silently because the bundled defaults are
    sufficient for a working offline installation. The mock provider is enforced
    after merging so users cannot accidentally remove muxdev's deterministic
    fallback provider.
    """
    workspace = Path.cwd() if workspace is None else workspace
    env = os.environ if env is None else env
    config: dict[str, Any] = {}
    for data in _default_config_parts():
        config = deep_merge(config, data)
    for source in external_config_sources(workspace, env):
        if source.exists:
            config = deep_merge(config, _read_yaml(Path(source.path)))
    return _ensure_mock_provider(config)


def config_sources(workspace: Path | None = None, env: dict[str, str] | None = None) -> list[ConfigSource]:
    """Describe every config source in the exact order used by load_config."""
    workspace = Path.cwd() if workspace is None else workspace
    env = os.environ if env is None else env
    sources = [
        ConfigSource("builtin", f"muxdev.config.defaults/{name}", True)
        for name in DEFAULT_CONFIG_FILES
    ]
    sources.extend(external_config_sources(workspace, env))
    return sources


def external_config_sources(workspace: Path, env: dict[str, str]) -> list[ConfigSource]:
    """Return optional user/project/environment config files."""
    user = user_config_path(env)
    project = workspace / ".muxdev" / "config.yaml"
    env_path = env.get("MUXDEV_CONFIG", "")
    sources = [
        ConfigSource("user", str(user), user.exists()),
        ConfigSource("project", str(project), project.exists()),
    ]
    if env_path:
        path = Path(env_path).expanduser()
        sources.append(ConfigSource("env", str(path), path.exists()))
    return sources


def user_config_path(env: dict[str, str] | None = None) -> Path:
    """Resolve the platform-native user config path without creating it."""
    env = os.environ if env is None else env
    if platform.system().lower() == "windows":
        base = env.get("APPDATA")
        if base:
            return Path(base) / "muxdev" / "config.yaml"
    return Path.home() / ".config" / "muxdev" / "config.yaml"


def validate_config(config: dict[str, Any] | None = None) -> dict[str, object]:
    """Perform lightweight validation while allowing forward-compatible keys."""
    config = load_config() if config is None else config
    warnings: list[str] = []
    errors: list[str] = []
    for section in config:
        if section not in KNOWN_SECTIONS:
            warnings.append(f"unknown top-level section: {section}")
    providers = config.get("providers", {})
    if "mock" not in providers:
        errors.append("mock provider is required")
    for name, definition in _mapping_items(providers):
        for field in ("mode", "commands", "status_hint"):
            if field not in definition:
                errors.append(f"provider {name} is missing {field}")
    for name, workflow in _mapping_items(config.get("workflows", {})):
        if "stages" not in workflow:
            errors.append(f"workflow {name} is missing stages")
    return {"valid": not errors, "errors": errors, "warnings": warnings}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge dictionaries while replacing lists and scalar values."""
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def path_config(workspace: Path, key: str) -> Path:
    """Resolve a runtime path under the configured workspace runtime root."""
    config = load_config(workspace)
    paths = config.get("paths", {})
    root = workspace / str(paths.get("runtime_root", ".muxdev"))
    if key == "runtime_root":
        return root
    return root / str(paths.get(key, key))


def _default_config_parts() -> list[dict[str, Any]]:
    package = resources.files("muxdev.config.defaults")
    return [_read_text_yaml((package / name).read_text(encoding="utf-8")) for name in DEFAULT_CONFIG_FILES]


def _read_yaml(path: Path) -> dict[str, Any]:
    return _read_text_yaml(path.read_text(encoding="utf-8"))


def _read_text_yaml(text: str) -> dict[str, Any]:
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError("muxdev config files must contain a mapping at the top level")
    return data


def _ensure_mock_provider(config: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(config)
    providers = result.setdefault("providers", {})
    providers.setdefault(
        "mock",
        {
            "mode": "builtin",
            "commands": ["mock"],
            "status_hint": "P0",
            "probe": "mock",
            "runtime": {"kind": "mock"},
        },
    )
    return result


def _mapping_items(value: object):
    return value.items() if isinstance(value, dict) else ()
