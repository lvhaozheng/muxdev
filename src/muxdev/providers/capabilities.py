"""Provider runtime capability declarations kept inside the provider layer."""

from __future__ import annotations

from pathlib import Path

from ..config.loader import load_config
from ..domain import ProviderCapabilities


def provider_capabilities(workspace: Path, provider: str) -> dict[str, object]:
    providers = load_config(workspace).get("providers", {})
    definition = providers.get(provider) if isinstance(providers, dict) else None
    if not isinstance(definition, dict):
        raise ValueError(f"unknown provider: {provider}")
    runtime = definition.get("runtime") if isinstance(definition.get("runtime"), dict) else {}
    kind = str(runtime.get("kind") or "headless_cli")
    if kind == "mock":
        capabilities = ProviderCapabilities(
            protocol="mock",
            streaming=True,
            cancel=True,
            permissions=True,
            isolated_config=True,
        )
    elif kind == "acp":
        capabilities = ProviderCapabilities(
            protocol="acp",
            streaming=True,
            cancel=True,
            permissions=True,
            mcp_config=True,
            mcp_tool_allowlist=True,
            mcp_call_events=True,
            isolated_config=True,
        )
    else:
        capabilities = ProviderCapabilities(
            protocol="headless_cli",
            streaming=bool(runtime.get("streaming")),
            cancel=True,
            permissions=bool(runtime.get("permissions")),
            mcp_config=bool(runtime.get("mcp_config_env")),
            mcp_tool_allowlist=bool(runtime.get("mcp_tool_allowlist")),
            mcp_call_events=bool(runtime.get("mcp_call_events")),
            isolated_config=bool(runtime.get("isolated_config")),
            session_resume=bool(runtime.get("resume_command")),
        )
    return capabilities.to_dict()
