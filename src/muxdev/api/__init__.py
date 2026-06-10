"""API-facing surfaces for local control plane integrations."""

from .mcp import handle_jsonrpc, server_manifest
from .app import create_app
from .web import write_dashboard

__all__ = ["create_app", "handle_jsonrpc", "server_manifest", "write_dashboard"]
