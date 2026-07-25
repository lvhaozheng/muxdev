"""Project scope resolution shared by project-aware HTTP and WebSocket APIs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request, WebSocket


def workspace_for_request(request: Request) -> Path:
    project_id = str(request.path_params.get("project_id") or "")
    if project_id:
        try:
            return request.app.state.workbench.workspace(project_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
    return Path(request.app.state.workspace).resolve()


def workspace_for_websocket(websocket: WebSocket) -> Path:
    project_id = str(websocket.path_params.get("project_id") or "")
    if project_id:
        return websocket.app.state.workbench.workspace(project_id)
    return Path(websocket.app.state.workspace).resolve()


def project_for_request(request: Request) -> dict[str, Any]:
    project_id = str(request.path_params.get("project_id") or "")
    if not project_id:
        raise HTTPException(400, "project_id is required")
    project = request.app.state.workbench.store.get_project(project_id)
    if not project:
        raise HTTPException(404, f"project not found: {project_id}")
    return project


__all__ = [
    "project_for_request",
    "workspace_for_request",
    "workspace_for_websocket",
]
