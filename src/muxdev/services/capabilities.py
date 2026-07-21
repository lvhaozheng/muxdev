"""Resolve stage, project, Skill, and provider authority without widening it."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from ..config.loader import load_config
from ..domain import CapabilityGrant, McpServerGrant
from ..models import WorkflowStage
from ..providers.capabilities import provider_capabilities


MCP_REF = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
DYNAMIC_MCP_LAUNCHERS = {"bunx", "npx", "pipx", "uvx"}


def resolve_stage_capabilities(workspace: Path, stage: WorkflowStage) -> CapabilityGrant:
    """Compute StageAuthority ∩ ProjectPolicy for a single immutable grant."""
    config = load_config(workspace)
    definitions = config.get("mcp_servers") if isinstance(config.get("mcp_servers"), dict) else {}
    requested_by_server: dict[str, set[str]] = {}
    for reference in stage.mcp_tools:
        if not MCP_REF.fullmatch(reference):
            raise ValueError(f"MCP permission must be an exact server/tool reference: {reference}")
        server, tool = reference.split("/", 1)
        requested_by_server.setdefault(server, set()).add(tool)

    grants: list[McpServerGrant] = []
    for server_name, requested_tools in sorted(requested_by_server.items()):
        definition = definitions.get(server_name)
        if not isinstance(definition, dict):
            raise ValueError(f"stage references unknown MCP server: {server_name}")
        if str(definition.get("transport") or "stdio") != "stdio":
            raise ValueError(f"v1 only accepts local stdio MCP servers: {server_name}")
        command = _resolve_local_command(str(definition.get("command") or ""))
        if not command:
            raise ValueError(f"MCP server command is not installed locally: {server_name}")
        enabled = {str(item) for item in definition.get("enabled_tools", [])}
        if requested_tools != enabled:
            raise PermissionError(
                f"stage tool set for {server_name} must equal its isolated enabled_tools; "
                f"requested={sorted(requested_tools)}, enabled={sorted(enabled)}"
            )
        tool_definitions = definition.get("tools") if isinstance(definition.get("tools"), dict) else {}
        for tool in requested_tools:
            tool_definition = tool_definitions.get(tool)
            if not isinstance(tool_definition, dict):
                raise ValueError(f"MCP tool is not declared: {server_name}/{tool}")
            if str(tool_definition.get("effect") or "") != "read":
                raise PermissionError(f"write/destructive MCP tool is forbidden in v1: {server_name}/{tool}")
        env_names = tuple(sorted({str(item) for item in definition.get("env_vars", []) if str(item)}))
        undeclared = set(env_names) - set(stage.allowed_secrets)
        if undeclared:
            raise PermissionError(f"MCP server {server_name} requests undeclared secrets: {sorted(undeclared)}")
        missing = [name for name in env_names if name not in os.environ]
        if missing:
            raise RuntimeError(f"MCP server {server_name} is missing required secret environment names: {missing}")
        if bool(definition.get("requires_network")) and not stage.allow_network:
            raise PermissionError(f"MCP server {server_name} requires network outside stage authority")
        grants.append(McpServerGrant(
            name=server_name,
            command=command,
            args=tuple(str(item) for item in definition.get("args", [])),
            env_names=env_names,
            tools=tuple(sorted(requested_tools)),
        ))
    return CapabilityGrant(
        read_workspace=True,
        write_workspace=stage.allow_write,
        shell=stage.allow_shell,
        network=stage.allow_network,
        secret_names=tuple(sorted({name for server in grants for name in server.env_names})),
        mcp_tools=tuple(sorted(set(stage.mcp_tools))),
        mcp_servers=tuple(grants),
    )


def require_provider_capabilities(
    workspace: Path,
    provider: str,
    grant: CapabilityGrant,
    *,
    profile: str,
) -> dict[str, object]:
    capabilities = provider_capabilities(workspace, provider)
    if grant.mcp_tools:
        required = ("mcp_config", "mcp_tool_allowlist", "isolated_config")
        missing = [name for name in required if not capabilities.get(name)]
        if missing:
            raise RuntimeError(f"provider {provider} cannot enforce the MCP grant: missing {missing}")
    if profile in {"standard", "strict"} and not capabilities.get("isolated_config"):
        raise RuntimeError(f"provider {provider} cannot isolate global configuration in {profile} mode")
    return capabilities


def _resolve_local_command(command: str) -> str | None:
    if not command:
        return None
    path = Path(command)
    if path.is_absolute():
        resolved = str(path.resolve()) if path.is_file() else None
    else:
        resolved = shutil.which(command)
    if resolved and Path(resolved).stem.lower() in DYNAMIC_MCP_LAUNCHERS:
        raise PermissionError(f"dynamic MCP launcher is forbidden in Trusted Harness v1: {command}")
    return resolved
