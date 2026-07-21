"""ACP ProviderAdapter using the optional official Python SDK."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any

from ..core.platforms import script_invocation
from ..core.processes import ProcessSupervisor
from ..core.redaction import redact
from ..domain import CapabilityGrant, ProviderEvent, StageExecutionInput, StageExecutionResult
from .protocols import CLARIFICATION_PROTOCOL_PROMPT, parse_cli_output


_ACTIVE_LOCK = threading.RLock()
_ACTIVE: dict[str, tuple[asyncio.AbstractEventLoop, Any, str]] = {}


class AcpProviderAdapter:
    def __init__(
        self,
        provider: str,
        command: list[str],
        *,
        timeout: float = 300,
        auth_env_vars: tuple[str, ...] = (),
    ) -> None:
        if not command:
            raise ValueError(f"ACP provider {provider} has no command")
        executable = shutil.which(command[0]) or (command[0] if Path(command[0]).is_file() else None)
        if not executable:
            raise ValueError(f"ACP provider {provider} command is not installed: {command[0]}")
        self.provider = provider
        self.command = script_invocation(str(executable), tuple(command[1:]))
        self.timeout = timeout
        self.auth_env_vars = auth_env_vars

    def execute(self, input: StageExecutionInput) -> StageExecutionResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._execute(input))
        raise RuntimeError("ACP adapter must run outside an existing asyncio event loop")

    async def _execute(self, input: StageExecutionInput) -> StageExecutionResult:
        try:
            from acp import PROTOCOL_VERSION, spawn_agent_process, text_block
            from acp.interfaces import Client
            from acp.schema import EnvVariable, McpServerStdio
        except ImportError as exc:
            raise RuntimeError("ACP support requires: pip install 'muxdev[interop]'") from exc

        events: list[ProviderEvent] = [
            ProviderEvent(
                kind="mcp.configured",
                status="configured",
                details={"server": server.name, "tools": list(server.tools)},
            )
            for server in input.capabilities.mcp_servers
        ]
        messages: list[str] = []
        client = _make_harness_client(Client, input.capabilities, events, messages)
        mcp_servers = _acp_mcp_servers(input.capabilities, McpServerStdio, EnvVariable)
        env = _acp_environment(input, self.auth_env_vars)
        session_id, prompt_payload, stderr_text, returncode, timed_out = await _run_acp_session(
            input,
            client=client,
            command=self.command,
            timeout=self.timeout,
            env=env,
            mcp_servers=mcp_servers,
            protocol_version=PROTOCOL_VERSION,
            spawn_agent_process=spawn_agent_process,
            text_block=text_block,
            events=events,
        )
        if timed_out:
            returncode = 124
        content = "".join(messages).strip()
        if not content:
            content = json.dumps({"summary": "ACP agent returned no message content"}, ensure_ascii=False)
        usage = prompt_payload.get("usage") if isinstance(prompt_payload.get("usage"), dict) else {}
        interaction_requests = parse_cli_output(self.provider, content, "").interaction_requests
        return StageExecutionResult(
            artifact_name=f"{input.stage_id}.acp.json",
            content=content,
            summary=f"{self.provider} ACP stage stopped: {prompt_payload.get('stopReason', 'unknown')}",
            stage_id=input.stage_id,
            provider=self.provider,
            status="completed" if returncode == 0 else "failed",
            returncode=returncode,
            stderr_hash=_hash(stderr_text),
            stderr_bytes=len(stderr_text.encode()),
            protocol="acp",
            event_count=len(events),
            interaction_requests=interaction_requests,
            session_id=session_id,
            events=tuple(events),
            stderr_content=stderr_text,
            timed_out=timed_out,
            cancelled=any(event.kind == "acp.cancelled" for event in events),
            tokens=sum(int(value) for key, value in usage.items() if "token" in key.lower() and isinstance(value, int)),
        )


def _make_harness_client(
    client_base: type,
    grant: CapabilityGrant,
    events: list[ProviderEvent],
    messages: list[str],
) -> Any:
    class HarnessClient(client_base):
        async def request_permission(self, session_id, tool_call, options, **kwargs):
            del session_id, kwargs
            details = _tool_details(tool_call, grant)
            allowed = _permission_allowed(grant, details)
            option = next((item for item in options if item.kind == "allow_once"), None) if allowed else None
            events.append(ProviderEvent(
                kind="permission.allowed" if option else "permission.denied",
                status="observed",
                details=details,
            ))
            if option:
                return {"outcome": {"outcome": "selected", "optionId": option.option_id}}
            return {"outcome": {"outcome": "cancelled"}}

        async def session_update(self, session_id, update, **kwargs):
            del session_id, kwargs
            details = _update_details(update, grant)
            update_type = str(details.get("session_update") or type(update).__name__)
            event_kind = "mcp.tool_call" if details.get("tool_ref") else f"acp.{update_type}"
            events.append(ProviderEvent(kind=event_kind, status="observed", details=details))
            if text := _content_text(update):
                messages.append(text)

    return HarnessClient()


def _acp_mcp_servers(grant: CapabilityGrant, server_type: type, env_type: type) -> list[Any]:
    return [
        server_type(
            name=server.name,
            command=server.command,
            args=list(server.args),
            env=[env_type(name=name, value=os.environ[name]) for name in server.env_names],
        )
        for server in grant.mcp_servers
    ]


async def _run_acp_session(
    input: StageExecutionInput,
    *,
    client: Any,
    command: list[str],
    timeout: float,
    env: dict[str, str],
    mcp_servers: list[Any],
    protocol_version: int,
    spawn_agent_process: Any,
    text_block: Any,
    events: list[ProviderEvent],
) -> tuple[str | None, dict[str, Any], str, int, bool]:
    session_id: str | None = None
    prompt_payload: dict[str, Any] = {}
    stderr_text = ""
    returncode = 0
    timed_out = False
    process = None
    stderr_task = None
    try:
        with tempfile.TemporaryDirectory(prefix="muxdev-acp-config-") as config_dir:
            _isolate_agent_home(env, Path(config_dir))
            async with spawn_agent_process(
                client,
                command[0],
                *command[1:],
                cwd=input.worktree,
                env=env,
                transport_kwargs={"shutdown_timeout": 2.0},
            ) as (connection, process):
                ProcessSupervisor.register_external_pid(input.run_id, process.pid)
                if process.stderr is not None:
                    stderr_task = asyncio.create_task(_read_limited(process.stderr, 1_000_000))
                initialized = await asyncio.wait_for(
                    connection.initialize(protocol_version=protocol_version), timeout=min(30.0, timeout)
                )
                if initialized.protocol_version != protocol_version:
                    raise RuntimeError(
                        "ACP protocol negotiation failed: "
                        f"expected {protocol_version}, got {initialized.protocol_version}"
                    )
                init_payload = initialized.model_dump(mode="json", by_alias=True)
                session = await asyncio.wait_for(
                    connection.new_session(cwd=str(input.worktree), mcp_servers=mcp_servers),
                    timeout=min(30.0, timeout),
                )
                session_id = session.session_id
                with _ACTIVE_LOCK:
                    _ACTIVE[input.run_id] = (asyncio.get_running_loop(), connection, session_id)
                events.extend((
                    ProviderEvent(
                        kind="acp.initialized",
                        details={
                            "capabilities": init_payload.get("agentCapabilities", {}),
                            "agent_info": init_payload.get("agentInfo"),
                        },
                    ),
                    ProviderEvent(kind="acp.session_created", details={"session_id": session_id}),
                ))
                try:
                    response = await asyncio.wait_for(
                        connection.prompt(session_id=session_id, prompt=[text_block(_prompt(input))]),
                        timeout=min(timeout, float(input.policy.get("timeout_seconds") or timeout)),
                    )
                    prompt_payload = response.model_dump(mode="json", by_alias=True)
                except asyncio.TimeoutError:
                    timed_out = True
                    await connection.cancel(session_id=session_id)
                    events.append(ProviderEvent(
                        kind="acp.cancelled", status="observed", details={"reason": "timeout"}
                    ))
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE.pop(input.run_id, None)
        if process is not None:
            ProcessSupervisor.unregister_external_pid(input.run_id, process.pid)
        if stderr_task is not None:
            stderr_bytes = await stderr_task
            stderr_text = redact(stderr_bytes.decode("utf-8", errors="replace"))
        if process is not None and process.returncode not in {None, 0}:
            returncode = int(process.returncode)
    return session_id, prompt_payload, stderr_text, returncode, timed_out


def cancel_acp_run(run_id: str, *, timeout_seconds: float = 2.0) -> bool:
    with _ACTIVE_LOCK:
        active = _ACTIVE.get(run_id)
    if not active:
        return False
    loop, connection, session_id = active
    future = asyncio.run_coroutine_threadsafe(connection.cancel(session_id=session_id), loop)
    try:
        future.result(timeout=timeout_seconds)
    except Exception:
        return False
    return True


def _permission_allowed(grant: CapabilityGrant, details: dict[str, object]) -> bool:
    if details.get("tool_ref") in grant.mcp_tools:
        return True
    kind = str(details.get("kind") or "other")
    if kind in {"read", "search", "think"}:
        return grant.read_workspace
    if kind == "fetch":
        return grant.network
    if kind in {"edit", "delete", "move"}:
        return grant.write_workspace
    if kind == "execute":
        return grant.shell
    return False


def _tool_details(tool_call: Any, grant: CapabilityGrant) -> dict[str, object]:
    payload = tool_call.model_dump(mode="json", by_alias=False) if hasattr(tool_call, "model_dump") else {}
    details: dict[str, object] = {
        "tool_call_id": payload.get("tool_call_id"),
        "kind": payload.get("kind"),
        "title": payload.get("title"),
    }
    if reference := _find_tool_reference(payload, grant):
        details["tool_ref"] = reference
    return details


def _update_details(update: Any, grant: CapabilityGrant) -> dict[str, object]:
    payload = update.model_dump(mode="json", by_alias=False) if hasattr(update, "model_dump") else {}
    details = {
        key: payload.get(key) for key in ("session_update", "tool_call_id", "kind", "status", "title")
        if payload.get(key) is not None
    }
    if reference := _find_tool_reference(payload, grant):
        details["tool_ref"] = reference
    return details


def _find_tool_reference(payload: object, grant: CapabilityGrant) -> str | None:
    text = json.dumps(payload, ensure_ascii=False, default=str)
    for reference in grant.mcp_tools:
        server, tool = reference.split("/", 1)
        patterns = (reference, f"{server}__{tool}", f"mcp__{server}__{tool}")
        if any(pattern in text for pattern in patterns) or (
            len(grant.mcp_tools) == 1 and f'"{tool}"' in text
        ):
            return reference
    return None


def _content_text(update: Any) -> str:
    content = getattr(update, "content", None)
    if getattr(content, "type", None) == "text" and isinstance(getattr(content, "text", None), str):
        return str(content.text)
    return ""


def _prompt(input: StageExecutionInput) -> str:
    prompt = f"Execute muxdev stage '{input.stage_id}' for this task: {input.task}"
    if schema := input.policy.get("output_schema"):
        prompt += f"\nReturn a JSON object matching {schema}; do not declare a gate decision or evidence score."
    if input.feedback:
        prompt += (
            "\n\n# Previous attempt feedback (runtime-observed)\n"
            + json.dumps(input.feedback.to_dict(), ensure_ascii=False, indent=2)
            + "\nFollow the feedback instruction exactly."
        )
    prompt += "\n\n" + CLARIFICATION_PROTOCOL_PROMPT
    if interaction_responses := input.context.get("interaction_responses"):
        prompt += (
            "\n\n# Confirmed clarification responses\n"
            + json.dumps(interaction_responses, ensure_ascii=False, indent=2)
            + "\nContinue the stage using these responses and return the declared stage output."
        )
    if context := str(input.context.get("context_pack") or "").strip():
        prompt += f"\n\n# Runtime context pack\n{context}"
    for skill in input.skills:
        if content := str(skill.get("content") or "").strip():
            prompt += f"\n\n# Skill: {skill.get('name')}\n{content}"
    return prompt


def _acp_environment(input: StageExecutionInput, auth_env_vars: tuple[str, ...]) -> dict[str, str]:
    baseline = {
        "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP",
        "LANG", "LC_ALL",
    }
    allowed = baseline | set(auth_env_vars) | set(input.capabilities.secret_names)
    allowed_upper = {name.upper() for name in allowed}
    env = {name: value for name, value in os.environ.items() if name.upper() in allowed_upper}
    env.update({"MUXDEV_RUN_ID": input.run_id, "MUXDEV_STAGE_ID": input.stage_id})
    return env


def _hash(value: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _isolate_agent_home(env: dict[str, str], root: Path) -> None:
    for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME"):
        env[name] = str(root)


async def _read_limited(stream: asyncio.StreamReader, limit: int) -> bytes:
    chunks: list[bytes] = []
    retained = 0
    while chunk := await stream.read(64 * 1024):
        if retained < limit:
            kept = chunk[: limit - retained]
            chunks.append(kept)
            retained += len(kept)
    return b"".join(chunks)
