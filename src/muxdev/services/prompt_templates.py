"""Config-backed role and workflow prompt composition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config.loader import load_config
from ..config.runtime import normalize_role
from ..models import WorkflowStage


@dataclass(frozen=True)
class StagePrompt:
    """Rendered prompt text and the template pieces that produced it."""

    text: str
    role_key: str
    sections: tuple[str, ...]


def render_stage_prompt(task: str, *, workflow: str, stage: WorkflowStage) -> StagePrompt:
    """Render a provider-ready stage brief from workflow and role templates."""
    config = load_config().get("prompt_templates", {})
    templates = config if isinstance(config, dict) else {}
    role = stage.role or "agent"
    role_key = normalize_role(role)
    values = {
        "task": task,
        "workflow": workflow,
        "stage_id": stage.id,
        "role": role,
        "role_key": role_key,
        "output_schema": stage.output_schema or "",
        "permissions": _permissions(stage),
    }

    body_template = stage.prompt_template or str(templates.get("stage_template") or "{task}")
    sections: list[tuple[str, str]] = [
        ("muxdev Stage Brief", _format(body_template, values)),
    ]

    preamble = str(templates.get("preamble") or "").strip()
    if preamble:
        sections.insert(0, ("muxdev Runtime Contract", _format(preamble, values)))

    role_prompt = _lookup_template(templates.get("roles"), role, role_key)
    if role_prompt:
        sections.append(("Role Instructions", _format(role_prompt, values)))

    configured_stage_prompt = _lookup_stage_template(templates, workflow=workflow, stage=stage)
    if configured_stage_prompt:
        sections.append(("Stage Instructions", _format(configured_stage_prompt, values)))
    if stage.prompt:
        sections.append(("Workflow Stage Override", _format(stage.prompt, values)))

    schema_prompt = _lookup_template(templates.get("schemas"), stage.output_schema or "", stage.output_schema or "")
    if schema_prompt:
        sections.append(("Output Contract", _format(schema_prompt, values)))

    return StagePrompt(
        text="\n\n".join(f"# {title}\n{body.strip()}" for title, body in sections if body.strip()),
        role_key=role_key,
        sections=tuple(title for title, body in sections if body.strip()),
    )


def _permissions(stage: WorkflowStage) -> str:
    flags = []
    if stage.read_only:
        flags.append("read_only")
    if stage.allow_write:
        flags.append("allow_write")
    if stage.allow_shell:
        flags.append("allow_shell")
    if stage.checkpoint:
        flags.append("checkpoint")
    return ", ".join(flags) or "read_only"


def _lookup_template(value: object, *keys: str) -> str:
    if not isinstance(value, dict):
        return ""
    for key in keys:
        if not key:
            continue
        item = value.get(key)
        if item:
            return str(item)
    return ""


def _lookup_stage_template(templates: dict[str, Any], *, workflow: str, stage: WorkflowStage) -> str:
    stages = templates.get("stages")
    if not isinstance(stages, dict):
        return ""
    workflow_stages = stages.get(workflow)
    if isinstance(workflow_stages, dict) and workflow_stages.get(stage.id):
        return str(workflow_stages[stage.id])
    fallback = stages.get(stage.id)
    if isinstance(fallback, str):
        return fallback
    return ""


def _format(template: str, values: dict[str, object]) -> str:
    return template.format_map(_SafeFormat(values))


class _SafeFormat(dict[str, object]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"
