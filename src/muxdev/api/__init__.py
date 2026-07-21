"""API-facing surfaces for local control plane integrations."""

from .mcp import handle_jsonrpc, serve_stdio, server_manifest
from .web import create_app, write_dashboard

__all__ = ["create_app", "handle_jsonrpc", "serve_stdio", "server_manifest", "write_dashboard"]
