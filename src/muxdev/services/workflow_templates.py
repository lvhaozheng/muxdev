"""Configuration-backed workflow template catalog."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..config.loader import load_config


@dataclass(frozen=True)
class WorkflowTemplate:
    """A provider-neutral set of phase commands and expected artifacts."""

    name: str
    description: str
    phases: tuple[str, ...]
    best_for: tuple[str, ...] = field(default_factory=tuple)
    supported_providers: tuple[str, ...] = field(default_factory=tuple)
    commands: dict[str, str] = field(default_factory=dict)
    prompts: dict[str, str] = field(default_factory=dict)
    artifacts: dict[str, str] = field(default_factory=dict)
    notes: str = ""

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["best_for"] = list(self.best_for)
        data["phases"] = list(self.phases)
        data["supported_providers"] = list(self.supported_providers)
        return data


def list_workflow_templates() -> list[WorkflowTemplate]:
    """Return templates from merged configuration in config order."""
    templates = load_config().get("workflow_templates", {})
    return [_template_from_config(name, data) for name, data in templates.items() if isinstance(data, dict)]


def get_workflow_template(name: str) -> WorkflowTemplate:
    templates = {template.name: template for template in list_workflow_templates()}
    try:
        return templates[name]
    except KeyError as exc:
        known = ", ".join(templates)
        raise ValueError(f"unknown workflow template: {name}; known templates: {known}") from exc


def render_template_command(template_name: str, phase: str, provider: str, task: str) -> dict[str, object]:
    """Render a phase command and translate it to a provider dialect."""
    template = get_workflow_template(template_name)
    if phase not in template.phases:
        raise ValueError(f"template {template.name} does not define phase: {phase}")
    canonical = template.commands.get(phase, "{task}").replace("{task}", task)
    return {
        "template": template.name,
        "phase": phase,
        "provider": provider,
        "canonical": canonical,
        "command": translate_agent_command(canonical, provider),
        "prompt": template.prompts.get(phase, "").replace("{task}", task),
        "artifact": template.artifacts.get(phase, ""),
    }


def translate_agent_command(command: str, provider: str) -> str:
    """Translate ``/namespace:command`` using a provider dialect."""
    if not command.startswith("/"):
        return command
    dialect = load_config().get("command_dialects", {}).get(provider)
    if not isinstance(dialect, dict):
        return command
    prefix = str(dialect.get("prefix", "/"))
    colon = str(dialect.get("colon", ":"))
    return prefix + command[1:].replace(":", colon)


def _template_from_config(name: str, data: dict[str, object]) -> WorkflowTemplate:
    return WorkflowTemplate(
        name=name,
        description=str(data.get("description", "")),
        best_for=tuple(str(item) for item in data.get("best_for", [])),
        phases=tuple(str(item) for item in data.get("phases", [])),
        supported_providers=tuple(str(item) for item in data.get("supported_providers", [])),
        commands={str(key): str(value) for key, value in (data.get("commands", {}) or {}).items()},
        prompts={str(key): str(value) for key, value in (data.get("prompts", {}) or {}).items()},
        artifacts={str(key): str(value) for key, value in (data.get("artifacts", {}) or {}).items()},
        notes=str(data.get("notes", "")),
    )


BUILTIN_WORKFLOW_TEMPLATES = {template.name: template for template in list_workflow_templates()}
