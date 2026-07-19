"""HTTP client for the local muxdev daemon API."""

from __future__ import annotations

import os
from typing import Any

import httpx
import click

from ..daemon.paths import DEFAULT_API_PORT, DEFAULT_HOST
from ..daemon.paths import default_daemon_paths


class DaemonConnectionError(click.ClickException):
    """Raised when the muxdev daemon API is unavailable."""

    def __init__(self, message: str, *, suggest_start: bool = False, status_code: int | None = None, path: str | None = None):
        super().__init__(message)
        self.suggest_start = suggest_start
        self.status_code = status_code
        self.path = path


class DaemonClient:
    def __init__(self, *, host: str = DEFAULT_HOST, port: int = DEFAULT_API_PORT, base_url: str | None = None, timeout: float = 30):
        self.base_url = (base_url or os.environ.get("MUXDEV_API_URL") or f"http://{host}:{port}").rstrip("/")
        self.timeout = timeout

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/api/health")

    def daemon_status(self) -> dict[str, Any]:
        return self._request("GET", "/api/daemon/status")

    def ux_overview(self) -> dict[str, Any]:
        return self._request("GET", "/api/ux/overview")

    def setup_status(self) -> dict[str, Any]:
        return self._request("GET", "/api/setup/status")

    def providers_health(self) -> dict[str, Any]:
        return self._request("GET", "/api/providers/health")

    def submit_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/tasks", json=payload)

    def tasks(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/tasks")

    def task(self, task_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/tasks/{task_id}")

    def task_ux(self, task_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/tasks/{task_id}/ux")

    def continue_task(self, task_id: str = "latest", *, max_cost_usd: float = 0.5) -> dict[str, Any]:
        return self._request("POST", f"/api/tasks/{task_id}/continue", json={"max_cost_usd": max_cost_usd})

    def stop_task(self, task_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/tasks/{task_id}/stop")

    def cancel_task(self, task_id: str, *, reason: str = "", wait: bool = False, timeout: float = 30.0) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/tasks/{task_id}/cancel",
            json={"reason": reason, "wait": wait, "timeout": timeout},
        )

    def task_executions(self, task_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/tasks/{task_id}/executions")

    def reconcile_task(
        self,
        task_id: str,
        *,
        decision: str,
        reason: str,
        acknowledge_duplicate_risk: bool = False,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/tasks/{task_id}/reconcile",
            json={
                "decision": decision,
                "reason": reason,
                "acknowledge_duplicate_risk": acknowledge_duplicate_risk,
            },
        )

    def runtime_status(self) -> dict[str, Any]:
        return self._request("GET", "/api/runtime/status")

    def provider_certifications(self, provider: str | None = None) -> Any:
        path = f"/api/providers/{provider}/certification" if provider else "/api/providers/certifications"
        return self._request("GET", path)

    def task_harness_events(self, task_id: str, *, stage: str | None = None) -> dict[str, Any]:
        suffix = f"?stage={stage}" if stage else ""
        return self._request("GET", f"/api/tasks/{task_id}/harness-events{suffix}")

    def task_isolation(self, task_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/tasks/{task_id}/isolation")

    def task_route(self, task_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/tasks/{task_id}/route")

    def task_review(self, task_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/tasks/{task_id}/review")

    def routing_replay(self, task_id: str, *, benchmark_snapshot_id: str | None = None) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/routing/replays",
            json={"run_id": task_id, "benchmark_snapshot_id": benchmark_snapshot_id},
        )

    def routing_snapshots(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/routing/snapshots")

    def routing_benchmark(
        self,
        suite_id: str,
        *,
        live: bool = False,
        acknowledged: bool = False,
        max_cost_usd: float | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/routing/benchmarks",
            json={
                "suite_id": suite_id,
                "live": live,
                "acknowledged": acknowledged,
                "max_cost_usd": max_cost_usd,
            },
        )

    def dashboard_tasks(self, *, cursor: str | None = None, limit: int = 50) -> dict[str, Any]:
        query = f"?limit={max(1, min(int(limit), 100))}"
        if cursor:
            query += f"&cursor={cursor}"
        return self._request("GET", f"/api/dashboard/tasks{query}")

    def task_story(self, task_id: str, *, cursor: str | None = None) -> dict[str, Any]:
        suffix = f"?cursor={cursor}" if cursor else ""
        return self._request("GET", f"/api/tasks/{task_id}/story{suffix}")

    def benchmark_replay(self, suite_id: str = "trusted-routing-v1") -> dict[str, Any]:
        return self._request("POST", "/api/benchmarks/replays", json={"suite_id": suite_id})

    def benchmark_executions(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/benchmarks/executions")

    def benchmark_execution(self, execution_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/benchmarks/executions/{execution_id}")

    def benchmark_report(self, execution_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/benchmarks/executions/{execution_id}/report")

    def benchmark_cancel(self, execution_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/benchmarks/executions/{execution_id}/cancel", json={})

    def approvals(self, *, status: str | None = None) -> list[dict[str, Any]]:
        path = "/api/approvals" + (f"?status={status}" if status else "")
        return self._request("GET", path)

    def provider_actions(self, *, status: str | None = None, task_id: str | None = None) -> list[dict[str, Any]]:
        if task_id:
            path = f"/api/tasks/{task_id}/provider-actions"
        else:
            path = "/api/provider-actions"
        if status:
            path += f"?status={status}"
        return self._request("GET", path)

    def provider_scores(self, *, role: str | None = None) -> list[dict[str, Any]]:
        path = "/api/provider-scores" + (f"?role={role}" if role else "")
        return self._request("GET", path)

    def provider_learning(self, *, role: str | None = None) -> list[dict[str, Any]]:
        path = "/api/learning/provider" + (f"?role={role}" if role else "")
        return self._request("GET", path)

    def parallel_conflicts(self, *, status: str | None = None, task_id: str | None = None) -> list[dict[str, Any]]:
        path = f"/api/tasks/{task_id}/parallel-conflicts" if task_id else "/api/parallel-conflicts"
        if status:
            path += f"?status={status}"
        return self._request("GET", path)

    def semantic_merge_reviews(self, *, task_id: str | None = None) -> list[dict[str, Any]]:
        path = f"/api/tasks/{task_id}/semantic-merge-reviews" if task_id else "/api/semantic-merge-reviews"
        return self._request("GET", path)

    def multi_repo_orchestrations(self, *, status: str | None = None) -> list[dict[str, Any]]:
        path = "/api/multi-repo/orchestrations" + (f"?status={status}" if status else "")
        return self._request("GET", path)

    def multi_repo_plan(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/multi-repo/plan", json=payload)

    def memory_contradictions(self, *, workspace: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        params = []
        if workspace:
            params.append(f"workspace={workspace}")
        if status:
            params.append(f"status={status}")
        path = "/api/memory/contradictions" + (("?" + "&".join(params)) if params else "")
        return self._request("GET", path)

    def memory_quarantine_auto(self, *, workspace: str | None = None) -> list[dict[str, Any]]:
        path = "/api/memory/quarantine-auto" + (f"?workspace={workspace}" if workspace else "")
        return self._request("POST", path)

    def feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/feedback", json=payload)

    def ecosystem(self) -> dict[str, Any]:
        return self._request("GET", "/api/ecosystem")

    def provider_action_handled(self, action_id: str, *, response: Any | None = None) -> dict[str, Any]:
        kwargs = {"json": {"response": response}} if response is not None else {}
        return self._request("POST", f"/api/provider-actions/{action_id}/handled", **kwargs)

    def provider_action_handled_and_continue(self, task_id: str, action_id: str, *, max_cost_usd: float = 0.5) -> dict[str, Any]:
        return self._request("POST", f"/api/tasks/{task_id}/actions/{action_id}/handled-and-continue", json={"max_cost_usd": max_cost_usd})

    def provider_action_response(self, action_id: str, response: Any) -> dict[str, Any]:
        return self._request("POST", f"/api/provider-actions/{action_id}/response", json={"response": response})

    def provider_action_respond_and_continue(self, task_id: str, action_id: str, response: Any, *, max_cost_usd: float = 0.5) -> dict[str, Any]:
        return self._request("POST", f"/api/tasks/{task_id}/actions/{action_id}/respond-and-continue", json={"response": response, "max_cost_usd": max_cost_usd})

    def provider_action_dismiss(self, action_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/provider-actions/{action_id}/dismiss")

    def approve(self, approval_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/approvals/{approval_id}/approve")

    def deny(self, approval_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/approvals/{approval_id}/deny")

    def approval_feedback(self, approval_id: str, feedback: str, *, max_cost_usd: float = 0.5, continue_task: bool = True) -> dict[str, Any]:
        path = f"/api/approvals/{approval_id}/feedback-and-continue" if continue_task else f"/api/approvals/{approval_id}/feedback"
        return self._request("POST", path, json={"feedback": feedback, "max_cost_usd": max_cost_usd})

    def diff(self, task_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/tasks/{task_id}/diff")

    def report(self, task_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/tasks/{task_id}/report")

    def rollback(self, task_id: str, *, to_stage: str | None = None) -> dict[str, Any]:
        suffix = f"?to_stage={to_stage}" if to_stage else ""
        return self._request("POST", f"/api/tasks/{task_id}/rollback{suffix}")

    def attach_command(self, task_id: str, *, agent: str = "implementer") -> dict[str, Any]:
        return self._request("GET", f"/api/tasks/{task_id}/attach-command?agent={agent}")

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            # Local daemon traffic must never be sent through HTTP(S)_PROXY.
            # Some developer environments use proxy placeholders that return
            # 502 for localhost, which made the TUI look broken even when the
            # daemon URL was otherwise correct.
            headers = dict(kwargs.pop("headers", {}) or {})
            try:
                from ..services.local_auth import LocalApiAuth

                headers.update(LocalApiAuth(default_daemon_paths().data_dir).authorization_headers())
            except (OSError, RuntimeError):
                pass
            with httpx.Client(base_url=self.base_url, timeout=self.timeout, trust_env=False, headers=headers) as client:
                response = client.request(method, path, **kwargs)
                response.raise_for_status()
                return response.json()
        except httpx.ConnectError as exc:
            raise DaemonConnectionError(f"muxdev daemon is not reachable at {self.base_url}", suggest_start=True) from exc
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text
            raise DaemonConnectionError(
                f"muxdev daemon request failed at {self.base_url}: {exc.response.status_code} {detail}",
                status_code=exc.response.status_code,
                path=path,
            ) from exc
        except httpx.HTTPError as exc:
            raise DaemonConnectionError(f"muxdev daemon request failed at {self.base_url}: {exc}") from exc
