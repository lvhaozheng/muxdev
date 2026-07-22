"""Validated registry for thin, argv-only coding CLI adapters and agents."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Mapping

from ..config.loader import load_config
from ..models import AgentDefinition, CliAdapterDefinition


class AgentRegistry:
    def __init__(self, workspace: Path, *, config: Mapping[str, object] | None = None) -> None:
        self.workspace = Path(workspace).resolve()
        self.config = dict(config or load_config(self.workspace))
        self.adapters = self._load_adapters()
        self.agents = self._load_agents()

    def _load_adapters(self) -> dict[str, CliAdapterDefinition]:
        raw = self.config.get("cli_adapters")
        if not isinstance(raw, Mapping):
            return {}
        result: dict[str, CliAdapterDefinition] = {}
        for cli_id, definition in raw.items():
            if not isinstance(definition, Mapping):
                raise ValueError(f"CLI adapter {cli_id} must be a mapping")
            model = CliAdapterDefinition.model_validate({"cli_id": str(cli_id), **definition})
            if model.cli_id != str(cli_id):
                raise ValueError(f"CLI adapter key {cli_id} does not match cli_id {model.cli_id}")
            result[model.cli_id] = model
        return result

    def _load_agents(self) -> dict[str, AgentDefinition]:
        raw = self.config.get("agents")
        if not isinstance(raw, Mapping):
            return {}
        result: dict[str, AgentDefinition] = {}
        for agent_id, definition in raw.items():
            if not isinstance(definition, Mapping):
                raise ValueError(f"agent {agent_id} must be a mapping")
            model = AgentDefinition.model_validate({"agent_id": str(agent_id), **definition})
            if model.agent_id != str(agent_id):
                raise ValueError(f"agent key {agent_id} does not match agent_id {model.agent_id}")
            if model.cli_id not in self.adapters:
                raise ValueError(f"agent {agent_id} references unknown CLI adapter {model.cli_id}")
            result[model.agent_id] = model
        return result

    def get(self, agent_id: str, *, require_enabled: bool = True) -> AgentDefinition:
        try:
            agent = self.agents[agent_id]
        except KeyError as exc:
            raise KeyError(f"unknown agent: {agent_id}") from exc
        if require_enabled and not agent.enabled:
            raise ValueError(f"agent is disabled: {agent_id}")
        return agent

    def adapter_for(self, agent: AgentDefinition | str) -> CliAdapterDefinition:
        definition = self.get(agent) if isinstance(agent, str) else agent
        return self.adapters[definition.cli_id]

    def list(self, *, enabled_only: bool = False) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for agent_id in sorted(self.agents):
            agent = self.agents[agent_id]
            if enabled_only and not agent.enabled:
                continue
            adapter = self.adapter_for(agent)
            executable = shutil.which(adapter.command[0])
            available = agent.enabled and (adapter.cli_id == "mock" or executable is not None)
            result.append(
                {
                    **agent.model_dump(),
                    "available": available,
                    "availability_reason": (
                        "built-in deterministic CLI"
                        if adapter.cli_id == "mock"
                        else (f"found {executable}" if executable else f"executable not found: {adapter.command[0]}")
                    ),
                    "terminal": {
                        "pty": adapter.supports_pty,
                        "resize": adapter.supports_resize,
                        "resume": adapter.supports_resume,
                        "tmux": adapter.prefer_tmux,
                    },
                }
            )
        return result

    def doctor(self, agent_id: str | None = None) -> dict[str, object]:
        agents = self.list(enabled_only=False)
        if agent_id:
            agents = [item for item in agents if item["agent_id"] == agent_id]
            if not agents:
                raise KeyError(f"unknown agent: {agent_id}")
        errors = [
            f"{item['agent_id']}: {item['availability_reason']}"
            for item in agents
            if item["enabled"] and not item["available"]
        ]
        return {"healthy": not errors, "agents": agents, "errors": errors}

    def build_argv(
        self,
        agent_id: str,
        *,
        worktree: Path,
        native_session_id: str | None = None,
    ) -> list[str]:
        agent = self.get(agent_id)
        adapter = self.adapter_for(agent)
        template = adapter.command
        if native_session_id:
            if not adapter.supports_resume:
                raise ValueError(f"CLI adapter {adapter.cli_id} does not support native resume")
            template = adapter.resume_command
        replacements = {
            "{worktree}": str(Path(worktree).resolve()),
            "{model}": str(agent.model or ""),
            "{native_session_id}": str(native_session_id or ""),
        }
        argv = [self._replace(item, replacements) for item in template]
        if adapter.working_directory_arg and not any("{worktree}" in item for item in template):
            argv.extend([adapter.working_directory_arg, str(Path(worktree).resolve())])
        if agent.model and adapter.model_arg and not any("{model}" in item for item in template):
            argv.extend([adapter.model_arg, agent.model])
        return argv

    def build_environment(
        self,
        agent_id: str,
        *,
        injected: Mapping[str, str] | None = None,
        source: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        adapter = self.adapter_for(agent_id)
        source = os.environ if source is None else source
        # Preserve only process essentials plus the adapter's explicit secrets/config allowlist.
        essentials = {
            "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP",
            "HOME", "USERPROFILE", "SHELL", "TERM", "COLORTERM", "LANG", "LC_ALL",
        }
        permitted = essentials | set(adapter.env_allowlist)
        env = {key: value for key, value in source.items() if key.upper() in {item.upper() for item in permitted}}
        for key, value in (injected or {}).items():
            if not key.startswith("MUXDEV_"):
                raise ValueError("runtime-injected CLI variables must use the MUXDEV_ prefix")
            env[key] = value
        env.setdefault("TERM", "xterm-256color")
        return env

    @staticmethod
    def _replace(value: str, replacements: Mapping[str, str]) -> str:
        result = value
        for placeholder, replacement in replacements.items():
            result = result.replace(placeholder, replacement)
        return result


__all__ = ["AgentRegistry"]
