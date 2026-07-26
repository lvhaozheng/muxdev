"""Validated registry for thin, argv-only coding CLI adapters and agents."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Mapping

from ..config.loader import load_config
from ..core.platforms import script_invocation
from ..models import AgentDefinition, CliAdapterDefinition


class AgentUnavailableError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        remediation: str,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.remediation = remediation
        self.retryable = False

    def detail(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": str(self),
            "remediation": self.remediation,
            "retryable": self.retryable,
        }


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
            command_candidates = self._executable_candidates(adapter)
            detected_command, executable = self._resolve_adapter_executable(adapter)
            terminal = _terminal_capabilities(
                supports_pty=adapter.supports_pty,
                prefer_tmux=adapter.prefer_tmux,
            )
            pty_missing = bool(adapter.requires_pty and not terminal["pty"])
            installed = bool(adapter.cli_id == "mock" or executable is not None)
            available = bool(
                agent.enabled
                and installed
                and not pty_missing
            )
            terminal["resume"] = bool(terminal["pty"] and adapter.supports_resume)
            terminal["native_resume"] = bool(adapter.supports_resume)
            terminal["configured_pty"] = bool(adapter.supports_pty)
            terminal["tmux"] = bool(adapter.prefer_tmux and terminal["backend"] == "tmux")
            result.append(
                {
                    **agent.model_dump(),
                    "provider_id": adapter.cli_id,
                    "installed": installed,
                    "detection": "builtin" if adapter.cli_id == "mock" else "path",
                    "detected_command": detected_command,
                    "command_candidates": command_candidates,
                    "available": available,
                    "availability_code": (
                        "disabled"
                        if not agent.enabled
                        else (
                            "executable_not_found"
                            if adapter.cli_id != "mock" and not executable
                            else ("pty_unavailable" if pty_missing else "available")
                        )
                    ),
                    "remediation": (
                        "在当前 Python 环境安装 pywinpty>=3 并重启 muxdev daemon。"
                        if pty_missing and os.name == "nt"
                        else (
                            f"安装 {agent.display_name or agent.agent_id} CLI 并确保命令位于 daemon 的 PATH，"
                            "或修改项目 Agent 配置。"
                            if adapter.cli_id != "mock" and not executable
                            else ""
                        )
                    ),
                    "availability_reason": (
                        "内置确定性测试 Agent"
                        if adapter.cli_id == "mock"
                        else (
                            (
                                "当前 CLI 需要真实 PTY，但系统只能提供 pipe 后端"
                                if pty_missing
                                else (
                                    f"已自动检测到 {detected_command}：{executable}"
                                    if executable
                                    else f"未在 PATH 中检测到：{', '.join(command_candidates)}"
                                )
                            )
                        )
                    ),
                    "terminal": terminal,
                }
            )
        return result

    def require_available(self, agent_id: str) -> AgentDefinition:
        """Reject unavailable CLI-backed Agents before creating durable state."""
        agent = self.get(agent_id, require_enabled=False)
        if not agent.enabled:
            raise AgentUnavailableError(
                f"Agent “{agent.display_name or agent.agent_id}” 已禁用。",
                code="agent_disabled",
                remediation="在项目 Agent 配置中启用该 Agent，或选择其他 Agent。",
            )
        adapter = self.adapter_for(agent)
        command_candidates = self._executable_candidates(adapter)
        _detected_command, executable = self._resolve_adapter_executable(adapter)
        if adapter.cli_id != "mock" and not executable:
            raise AgentUnavailableError(
                f"Agent “{agent.display_name or agent.agent_id}” 当前不可用："
                f"未在 PATH 中检测到命令 {', '.join(command_candidates)}。",
                code="executable_not_found",
                remediation=(
                    f"安装 {agent.display_name or agent.agent_id} CLI 并确保命令位于 daemon 的 PATH，"
                    "或修改项目 Agent 配置。"
                ),
            )
        terminal = _terminal_capabilities(
            supports_pty=adapter.supports_pty,
            prefer_tmux=adapter.prefer_tmux,
        )
        if adapter.requires_pty and not terminal["pty"]:
            remediation = (
                "在当前 Python 环境安装 pywinpty>=3 并重启 muxdev daemon。"
                if os.name == "nt"
                else "安装可用的 PTY 后端并重启 muxdev daemon。"
            )
            raise AgentUnavailableError(
                f"Agent “{agent.display_name or agent.agent_id}” 当前不可用："
                "CLI 需要真实 PTY，但系统只能提供 pipe 后端。",
                code="pty_unavailable",
                remediation=remediation,
            )
        return agent

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
        initial_prompt: str | None = None,
    ) -> list[str]:
        agent = self.get(agent_id)
        adapter = self.adapter_for(agent)
        template = adapter.command
        if native_session_id:
            if not adapter.supports_resume:
                raise ValueError(f"CLI adapter {adapter.cli_id} does not support native resume")
            template = adapter.resume_command
        normalized_worktree = os.path.normcase(str(Path(worktree).resolve()))
        replacements = {
            "{worktree}": str(Path(worktree).resolve()),
            "{worktree_toml}": json.dumps(
                normalized_worktree,
                ensure_ascii=False,
            ),
            "{control_dir}": str((self.workspace / ".muxdev").resolve()),
            "{model}": str(agent.model or ""),
            "{native_session_id}": str(native_session_id or ""),
        }
        argv = [self._replace(item, replacements) for item in template]
        if adapter.working_directory_arg and not any("{worktree}" in item for item in template):
            argv.extend([adapter.working_directory_arg, str(Path(worktree).resolve())])
        if agent.model and adapter.model_arg and not any("{model}" in item for item in template):
            argv.extend([adapter.model_arg, agent.model])
        if initial_prompt:
            if adapter.bootstrap_transport != "argv":
                raise ValueError(
                    f"CLI adapter {adapter.cli_id} does not accept an argv bootstrap"
                )
            argv.append(initial_prompt)
        if argv[0] == adapter.command[0]:
            _detected_command, executable = self._resolve_adapter_executable(adapter)
        else:
            executable = self._resolve_executable(argv[0])
        if not executable:
            raise FileNotFoundError(
                f"Agent “{agent.display_name or agent.agent_id}” 启动失败："
                f"找不到命令 {argv[0]}。请安装对应 CLI 或修改项目 Agent 配置。"
            )
        return script_invocation(executable, tuple(argv[1:]))

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
        if adapter.supports_pty:
            # A daemon may be launched from a non-interactive shell that exports
            # TERM=dumb. Interactive coding CLIs then stop for confirmation
            # before their prompt exists, so PTY adapters need a real terminal
            # capability declaration regardless of the parent shell.
            env["TERM"] = "xterm-256color"
        else:
            env.setdefault("TERM", "xterm-256color")
        return env

    @staticmethod
    def _replace(value: str, replacements: Mapping[str, str]) -> str:
        result = value
        for placeholder, replacement in replacements.items():
            result = result.replace(placeholder, replacement)
        return result

    def _executable_candidates(self, adapter: CliAdapterDefinition) -> list[str]:
        """Use the provider catalog as the single PATH-discovery source.

        A project can deliberately replace an adapter command with a wrapper.
        Provider aliases are only considered when the adapter still uses one of
        the provider's declared commands, so an explicit wrapper is never
        bypassed by an implicit fallback.
        """
        primary = adapter.command[0]
        candidates = [primary]
        providers = self.config.get("providers")
        provider = providers.get(adapter.cli_id) if isinstance(providers, Mapping) else None
        configured = provider.get("commands") if isinstance(provider, Mapping) else None
        provider_commands = (
            [str(item) for item in configured if isinstance(item, str) and item]
            if isinstance(configured, list)
            else []
        )
        if primary in provider_commands:
            candidates.extend(provider_commands)
        unique: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = os.path.normcase(candidate)
            if key not in seen:
                seen.add(key)
                unique.append(candidate)
        return unique

    def _resolve_adapter_executable(
        self,
        adapter: CliAdapterDefinition,
    ) -> tuple[str | None, str | None]:
        for candidate in self._executable_candidates(adapter):
            if executable := self._resolve_executable(candidate):
                return candidate, executable
        return None, None

    @staticmethod
    def _resolve_executable(command: str) -> str | None:
        resolved = shutil.which(command)
        if resolved:
            return str(Path(resolved).resolve())
        candidate = Path(command).expanduser()
        return str(candidate.resolve()) if candidate.is_file() else None


def _terminal_capabilities(*, supports_pty: bool, prefer_tmux: bool = False) -> dict[str, object]:
    if not supports_pty:
        backend = "pipe"
    elif os.name == "nt":
        try:
            from winpty import PtyProcess  # noqa: F401
        except ImportError:
            backend = "pipe"
        else:
            backend = "conpty"
    elif prefer_tmux and shutil.which("tmux"):
        backend = "tmux"
    else:
        backend = "posix-pty"
    real_pty = backend != "pipe"
    return {
        "backend": backend,
        "pty": real_pty,
        "resize": real_pty,
        "process_resume": backend == "tmux",
        "degraded": bool(supports_pty and not real_pty),
        "degradation_reason": (
            "ConPTY/PTY backend is unavailable; interactive resize and resume are disabled"
            if supports_pty and not real_pty else None
        ),
    }


__all__ = ["AgentRegistry", "AgentUnavailableError"]
