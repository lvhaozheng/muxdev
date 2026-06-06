"""Local flow definitions.

Flows capture scheduled-run intent as YAML files, but M0-M7 intentionally keeps
execution manual instead of running a resident scheduler. That gives users a
portable control-plane format without surprising background jobs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from ..config.loader import path_config


@dataclass(frozen=True)
class FlowDefinition:
    """A persisted scheduled-run definition."""

    name: str
    schedule: str
    task: str
    provider: str = "mock"
    workflow: str = "software-dev"
    enabled: bool = True
    gate_command: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class FlowRegistry:
    """Read and write flow YAML files under the configured runtime path."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.flows_dir = path_config(workspace, "flows")

    def add(
        self,
        name: str,
        *,
        schedule: str,
        task: str,
        provider: str = "mock",
        workflow: str = "software-dev",
        enabled: bool = True,
        gate_command: str = "",
    ) -> FlowDefinition:
        flow = FlowDefinition(
            name=_safe_flow_name(name),
            schedule=schedule,
            task=task,
            provider=provider,
            workflow=workflow,
            enabled=enabled,
            gate_command=gate_command,
        )
        self.flows_dir.mkdir(parents=True, exist_ok=True)
        self.path_for(flow.name).write_text(yaml.safe_dump(flow.to_dict(), sort_keys=False), encoding="utf-8")
        return flow

    def list(self) -> list[FlowDefinition]:
        if not self.flows_dir.exists():
            return []
        flows: list[FlowDefinition] = []
        for path in sorted(self.flows_dir.glob("*.yaml")):
            flows.append(self.load(path.stem))
        return flows

    def load(self, name: str) -> FlowDefinition:
        path = self.path_for(_safe_flow_name(name))
        if not path.exists():
            raise ValueError(f"flow not found: {name}")
        data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return FlowDefinition(**data)

    def plan_run(self, name: str) -> dict[str, object]:
        """Return the manual execution plan for a flow without daemonizing it."""
        flow = self.load(name)
        return {
            "name": flow.name,
            "status": "planned",
            "schedule": flow.schedule,
            "task": flow.task,
            "provider": flow.provider,
            "workflow": flow.workflow,
            "enabled": flow.enabled,
            "gate_command": flow.gate_command,
            "notes": "Scheduled execution is intentionally not daemonized in M0-M7; use --execute for a manual run.",
        }

    def path_for(self, name: str) -> Path:
        return self.flows_dir / f"{name}.yaml"


def _safe_flow_name(name: str) -> str:
    normalized = name.strip().replace(" ", "-")
    if not normalized:
        raise ValueError("flow name cannot be empty")
    if any(char in normalized for char in "\\/:*?\"<>|"):
        raise ValueError(f"flow name contains unsupported path characters: {name}")
    return normalized
