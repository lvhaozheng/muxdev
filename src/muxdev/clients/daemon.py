"""HTTP client for the local muxdev daemon API."""

from __future__ import annotations

import os
from typing import Any

import httpx
import click

from ..daemon.paths import DEFAULT_API_PORT, DEFAULT_HOST


class DaemonConnectionError(click.ClickException):
    """Raised when the muxdev daemon API is unavailable."""

    def __init__(self, message: str, *, suggest_start: bool = False):
        super().__init__(message)
        self.suggest_start = suggest_start


class DaemonClient:
    def __init__(self, *, host: str = DEFAULT_HOST, port: int = DEFAULT_API_PORT, base_url: str | None = None, timeout: float = 30):
        self.base_url = (base_url or os.environ.get("MUXDEV_API_URL") or f"http://{host}:{port}").rstrip("/")
        self.timeout = timeout

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/api/health")

    def daemon_status(self) -> dict[str, Any]:
        return self._request("GET", "/api/daemon/status")

    def submit_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/tasks", json=payload)

    def tasks(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/tasks")

    def task(self, task_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/tasks/{task_id}")

    def continue_task(self, task_id: str = "latest", *, max_cost_usd: float = 0.5) -> dict[str, Any]:
        return self._request("POST", f"/api/tasks/{task_id}/continue", json={"max_cost_usd": max_cost_usd})

    def stop_task(self, task_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/tasks/{task_id}/stop")

    def approvals(self, *, status: str | None = None) -> list[dict[str, Any]]:
        path = "/api/approvals" + (f"?status={status}" if status else "")
        return self._request("GET", path)

    def approve(self, approval_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/approvals/{approval_id}/approve")

    def deny(self, approval_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/approvals/{approval_id}/deny")

    def diff(self, task_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/tasks/{task_id}/diff")

    def report(self, task_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/tasks/{task_id}/report")

    def rollback(self, task_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/tasks/{task_id}/rollback")

    def attach_command(self, task_id: str, *, agent: str = "implementer") -> dict[str, Any]:
        return self._request("GET", f"/api/tasks/{task_id}/attach-command?agent={agent}")

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            # Local daemon traffic must never be sent through HTTP(S)_PROXY.
            # Some developer environments use proxy placeholders that return
            # 502 for localhost, which made the TUI look broken even when the
            # daemon URL was otherwise correct.
            with httpx.Client(base_url=self.base_url, timeout=self.timeout, trust_env=False) as client:
                response = client.request(method, path, **kwargs)
                response.raise_for_status()
                return response.json()
        except httpx.ConnectError as exc:
            raise DaemonConnectionError(f"muxdev daemon is not reachable at {self.base_url}", suggest_start=True) from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text
            raise DaemonConnectionError(f"muxdev daemon request failed at {self.base_url}: {exc.response.status_code} {detail}") from exc
        except httpx.HTTPError as exc:
            raise DaemonConnectionError(f"muxdev daemon request failed at {self.base_url}: {exc}") from exc
