"""Provider adapters with one method: execute(StageExecutionInput)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from ..config.loader import load_config
from ..core.processes import ProcessSupervisor
from ..domain import ProviderEvent, StageExecutionInput, StageExecutionResult
from .contracts import ProviderAdapter
from .mock import MockProvider
from .protocols import (
    CLARIFICATION_PROTOCOL_PROMPT,
    CliInvocation,
    build_cli_invocation,
    parse_cli_output,
)


DEFAULT_PROMPT = "Execute muxdev stage '{stage_id}' for this task: {task}"


class MockProviderAdapter:
    def execute(self, input: StageExecutionInput) -> StageExecutionResult:
        return MockProvider().execute(input)


class HeadlessCliProviderAdapter:
    def __init__(
        self,
        provider: str,
        command: list[str],
        *,
        timeout: float,
        prompt_template: str,
        prompt_transport: str,
        auth_env_vars: tuple[str, ...] = (),
        mcp_config_env: str | None = None,
        mcp_call_events: bool = False,
        isolated_config: bool = False,
        resume_command: list[str] | None = None,
        resume_prompt_transport: str | None = None,
    ) -> None:
        self.provider = provider
        self.command = command
        self.timeout = timeout
        self.prompt_template = prompt_template
        self.prompt_transport = prompt_transport
        self.auth_env_vars = auth_env_vars
        self.mcp_config_env = mcp_config_env
        self.mcp_call_events = mcp_call_events
        self.isolated_config = isolated_config
        self.resume_command = list(resume_command or [])
        self.resume_prompt_transport = resume_prompt_transport or prompt_transport

    def execute(self, input: StageExecutionInput) -> StageExecutionResult:
        prompt = self._prompt(input)
        invocation, resumed_session_id = self._invocation(input, prompt)
        with tempfile.TemporaryDirectory(prefix="muxdev-provider-") as config_dir:
            env = _provider_environment(input, auth_env_vars=self.auth_env_vars)
            if self.isolated_config:
                _isolate_provider_home(env, Path(config_dir))
            if input.capabilities.mcp_servers:
                if not self.mcp_config_env:
                    raise RuntimeError(f"provider {self.provider} has no isolated MCP configuration channel")
                config_path = Path(config_dir) / "mcp.json"
                config_path.write_text(
                    json.dumps(_mcp_config(input), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                env[self.mcp_config_env] = str(config_path)
            completed = ProcessSupervisor().run(
                invocation.argv,
                cwd=input.worktree,
                stdin=invocation.stdin,
                timeout_seconds=min(self.timeout, float(input.policy.get("timeout_seconds") or self.timeout)),
                env=env,
                run_id=input.run_id,
                stage_id=input.stage_id,
            )
        stdout, stderr, returncode = completed.stdout, completed.stderr, completed.returncode
        parsed = parse_cli_output(self.provider, stdout, stderr)
        configured_events = tuple(
            ProviderEvent(
                kind="mcp.configured",
                status="configured",
                details={"server": server.name, "tools": list(server.tools)},
            )
            for server in input.capabilities.mcp_servers
        )
        observed_events = _mcp_call_events(stdout, input.capabilities.mcp_tools) if self.mcp_call_events else ()
        session_events = ()
        if resumed_session_id:
            session_events = (
                ProviderEvent(
                    kind="provider.session_resumed" if returncode == 0 else "provider.session_resume_failed",
                    status="observed",
                    details={"session_id": resumed_session_id},
                ),
            )
        return StageExecutionResult(
            artifact_name=f"{input.stage_id}.log",
            content=parsed.content,
            summary=f"{self.provider} stage exited with {returncode}",
            stage_id=input.stage_id,
            provider=self.provider,
            status="completed" if returncode == 0 else "failed",
            returncode=returncode,
            stdout_hash=_hash(stdout),
            stderr_hash=_hash(stderr),
            stdout_bytes=len(stdout.encode()),
            stderr_bytes=len(stderr.encode()),
            output_refs=tuple(_output_refs(parsed.content)),
            interaction_requests=parsed.interaction_requests,
            protocol=parsed.protocol,
            event_count=parsed.event_count,
            session_id=parsed.session_id or (resumed_session_id if returncode == 0 else None),
            events=(*configured_events, *session_events, *observed_events),
            stdout_content=stdout,
            stderr_content=stderr,
            timed_out=completed.timed_out,
            cancelled=completed.cancelled,
            stdout_truncated=completed.stdout_truncated,
            stderr_truncated=completed.stderr_truncated,
            cost_usd=0.0,
            tokens=0,
        )

    def _invocation(self, input: StageExecutionInput, prompt: str) -> tuple[CliInvocation, str | None]:
        previous_session_id = str(input.context.get("previous_provider_session_id") or "").strip()
        command = self.command
        transport = self.prompt_transport
        resumed_session_id = None
        if previous_session_id and self.resume_command:
            placeholder_count = sum(item.count("{session_id}") for item in self.resume_command)
            if placeholder_count != 1:
                raise ValueError("resume_command requires exactly one {session_id} placeholder")
            command = [item.replace("{session_id}", previous_session_id) for item in self.resume_command]
            transport = self.resume_prompt_transport
            resumed_session_id = previous_session_id
        scoped = _capability_scoped_command(command, input)
        return build_cli_invocation(scoped, prompt, transport=transport), resumed_session_id

    def _prompt(self, input: StageExecutionInput) -> str:
        prompt = self.prompt_template.format(stage_id=input.stage_id, task=input.task)
        schema = input.policy.get("output_schema")
        if schema:
            prompt += f"\nReturn a JSON object matching {schema}; do not declare a gate decision, confidence, or evidence score."
        review_standards = input.context.get("review_standards")
        if schema == "ReviewResult" and review_standards:
            prompt += (
                "\nAssess every frozen conversation standard below in standard_assessments. "
                "For each one return standard_id, status satisfied or failed, and a concise note:\n"
                + json.dumps(review_standards, ensure_ascii=False, indent=2)
            )
        if input.feedback:
            prompt += (
                "\n\n# Previous attempt feedback (runtime-observed)\n"
                + json.dumps(input.feedback.to_dict(), ensure_ascii=False, indent=2)
                + "\nFollow the feedback instruction exactly."
            )
        prompt += "\n\n" + CLARIFICATION_PROTOCOL_PROMPT
        interaction_responses = input.context.get("interaction_responses")
        if interaction_responses:
            prompt += (
                "\n\n# Confirmed clarification responses\n"
                + json.dumps(interaction_responses, ensure_ascii=False, indent=2)
                + "\nContinue the stage using these responses and return the declared stage output."
            )
        repo_map = str(input.context.get("repo_map") or "").strip()
        context_pack = str(input.context.get("context_pack") or "").strip()
        if context_pack:
            prompt += f"\n\n# Runtime context pack\n{context_pack}"
        elif repo_map:
            prompt += f"\n\n# Deterministic repository map\n{repo_map}"
        for skill in input.skills:
            content = str(skill.get("content") or "").strip()
            if content:
                prompt += f"\n\n# Skill: {skill.get('name')}\n{content}"
        return prompt


def get_runtime_provider(
    provider: str,
    *,
    workspace: Path | None = None,
    definition: dict[str, object] | None = None,
) -> ProviderAdapter:
    if definition is not None:
        config = definition
    else:
        providers = load_config(workspace).get("providers", {})
        config = providers.get(provider) if isinstance(providers, dict) else None
    if not isinstance(config, dict):
        raise ValueError(f"unknown runtime provider: {provider}")
    runtime = config.get("runtime") if isinstance(config.get("runtime"), dict) else {}
    if runtime.get("kind") == "mock":
        return MockProviderAdapter()
    if runtime.get("kind") == "acp":
        from .acp import AcpProviderAdapter

        return AcpProviderAdapter(
            provider,
            [str(item) for item in runtime.get("command", [])],
            timeout=float(runtime.get("timeout", 300)),
            auth_env_vars=tuple(str(item) for item in runtime.get("auth_env_vars", [])),
        )
    template = [str(item) for item in runtime.get("command", [])]
    if not template:
        template = [str(item) for item in config.get("commands", [provider])]
    executable = _resolve_executable(template[0], [str(item) for item in config.get("commands", [])])
    if not executable:
        raise ValueError(f"{provider} command is not installed")
    return HeadlessCliProviderAdapter(
        provider,
        [executable, *template[1:]],
        timeout=float(runtime.get("timeout", 300)),
        prompt_template=str(runtime.get("prompt_template", DEFAULT_PROMPT)),
        prompt_transport=str(runtime.get("prompt_transport") or "stdin"),
        auth_env_vars=tuple(str(item) for item in runtime.get("auth_env_vars", [])),
        mcp_config_env=str(runtime.get("mcp_config_env")) if runtime.get("mcp_config_env") else None,
        mcp_call_events=bool(runtime.get("mcp_call_events")),
        isolated_config=bool(runtime.get("isolated_config")),
        resume_command=[str(item) for item in runtime.get("resume_command", [])],
        resume_prompt_transport=(
            str(runtime.get("resume_prompt_transport"))
            if runtime.get("resume_prompt_transport") else None
        ),
    )


def _resolve_executable(primary: str, candidates: list[str]) -> str | None:
    for command in (primary, *candidates):
        if resolved := shutil.which(command):
            return resolved
    return None


def _capability_scoped_command(command: list[str], input: StageExecutionInput) -> list[str]:
    """Project the reduced grant into CLIs with a known sandbox-mode argument."""
    scoped = list(command)
    if input.capabilities.write_workspace:
        return scoped
    for index in range(len(scoped) - 1):
        if scoped[index] == "--sandbox" and scoped[index + 1] == "workspace-write":
            scoped[index + 1] = "read-only"
    return scoped


def _hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _output_refs(content: str) -> list[str]:
    import re

    return sorted(set(re.findall(r"(?:^|\s)([\w./\\-]+\.[A-Za-z0-9]{1,8})(?:\s|$)", content)))[:100]


def _provider_environment(input: StageExecutionInput, *, auth_env_vars: tuple[str, ...]) -> dict[str, str]:
    baseline = {
        "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP",
        "USERPROFILE", "HOME", "APPDATA", "LOCALAPPDATA", "LANG", "LC_ALL",
    }
    allowed = baseline | set(auth_env_vars) | set(input.capabilities.secret_names)
    allowed_upper = {name.upper() for name in allowed}
    env = {name: value for name, value in os.environ.items() if name.upper() in allowed_upper}
    env.update({"MUXDEV_RUN_ID": input.run_id, "MUXDEV_STAGE_ID": input.stage_id})
    return env


def _mcp_config(input: StageExecutionInput) -> dict[str, object]:
    return {
        "contract_version": "muxdev.mcp-projection.v1",
        "servers": [
            {
                "name": server.name,
                "transport": "stdio",
                "command": server.command,
                "args": list(server.args),
                "env": {name: os.environ[name] for name in server.env_names},
                "enabled_tools": list(server.tools),
            }
            for server in input.capabilities.mcp_servers
        ],
    }


def _mcp_call_events(stdout: str, references: tuple[str, ...]) -> tuple[ProviderEvent, ...]:
    observed: set[str] = set()
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        event_kind = " ".join(str(value).lower() for value in (
            event.get("type"), event.get("kind"), item.get("type"), item.get("kind")
        ))
        if "tool" not in event_kind:
            continue
        serialized = json.dumps(event, ensure_ascii=False, default=str)
        for reference in references:
            server, tool = reference.split("/", 1)
            structured_match = any(
                str(source.get("server") or source.get("server_name") or "") == server
                and str(source.get("tool") or source.get("tool_name") or source.get("name") or "") == tool
                for source in (event, item)
            )
            if structured_match or any(pattern in serialized for pattern in (
                reference, f"{server}__{tool}", f"mcp__{server}__{tool}"
            )):
                observed.add(reference)
    return tuple(
        ProviderEvent(kind="mcp.tool_call", status="observed", details={"tool_ref": reference})
        for reference in sorted(observed)
    )


def _isolate_provider_home(env: dict[str, str], root: Path) -> None:
    for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME"):
        env[name] = str(root)
