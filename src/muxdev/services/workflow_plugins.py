"""Workflow template catalog and provider command translation.

Templates are lightweight, configuration-defined command templates inspired by
agent workflow systems. They let a project describe phase-specific slash
commands once, then translate the command dialect for providers such as Codex.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..config.loader import load_config

@dataclass(frozen=True)
class WorkflowPlugin:
    """Configuration-backed workflow template definition."""

    name: str
    description: str
    phases: tuple[str, ...]
    supported_providers: tuple[str, ...] = field(default_factory=tuple)
    commands: dict[str, str] = field(default_factory=dict)
    prompts: dict[str, str] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["phases"] = list(self.phases)
        data["supported_providers"] = list(self.supported_providers)
        return data


BUILTIN_WORKFLOW_PLUGINS: dict[str, WorkflowPlugin] = {}


def list_workflow_plugins() -> list[WorkflowPlugin]:
    """Return templates from merged configuration in config order."""
    plugins = load_config().get("workflow_plugins", {})
    return [_plugin_from_config(name, data) for name, data in plugins.items() if isinstance(data, dict)]


def get_workflow_plugin(name: str) -> WorkflowPlugin:
    plugins = {plugin.name: plugin for plugin in list_workflow_plugins()}
    try:
        return plugins[name]
    except KeyError as exc:
        known = ", ".join(plugins)
        raise ValueError(f"unknown workflow template: {name}; known templates: {known}") from exc


def render_plugin_command(plugin_name: str, phase: str, provider: str, task: str) -> dict[str, object]:
    """Render a workflow template phase command and translate it for a provider."""
    plugin = get_workflow_plugin(plugin_name)
    if phase not in plugin.phases:
        raise ValueError(f"plugin {plugin.name} does not define phase: {phase}")
    canonical = plugin.commands.get(phase, "{task}")
    rendered = canonical.replace("{task}", task)
    return {
        "plugin": plugin.name,
        "phase": phase,
        "provider": provider,
        "canonical": rendered,
        "command": translate_agent_command(rendered, provider),
        "prompt": plugin.prompts.get(phase, "").replace("{task}", task),
        "artifact": plugin.artifacts.get(phase, ""),
    }


def translate_agent_command(command: str, provider: str) -> str:
    """Translate `/namespace:command` syntax using provider dialect config."""
    if not command.startswith("/"):
        return command
    dialect = load_config().get("command_dialects", {}).get(provider)
    if not isinstance(dialect, dict):
        return command
    prefix = str(dialect.get("prefix", "/"))
    colon = str(dialect.get("colon", ":"))
    return prefix + command[1:].replace(":", colon)


def _plugin_from_config(name: str, data: dict[str, object]) -> WorkflowPlugin:
    return WorkflowPlugin(
        name=name,
        description=str(data.get("description", "")),
        phases=tuple(str(item) for item in data.get("phases", [])),
        supported_providers=tuple(str(item) for item in data.get("supported_providers", [])),
        commands={str(key): str(value) for key, value in (data.get("commands", {}) or {}).items()},
        prompts={str(key): str(value) for key, value in (data.get("prompts", {}) or {}).items()},
        artifacts={str(key): str(value) for key, value in (data.get("artifacts", {}) or {}).items()},
        notes=str(data.get("notes", "")),
    )


BUILTIN_WORKFLOW_PLUGINS = {plugin.name: plugin for plugin in list_workflow_plugins()}
