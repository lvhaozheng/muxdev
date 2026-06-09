"""Static task dashboard writer and lightweight web serving helpers."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ..models import ApprovalStatus, ProviderActionStatus
from ..providers import detect_providers
from ..services.multirepo import plan_multi_repo_orchestration
from ..services.product_experience import build_product_experience
from ..services.ux import build_provider_health, build_setup_status, build_task_ux_summary, build_ux_overview
from ..storage import MemoryStore


def render_dashboard_html(payload: dict[str, Any]) -> str:
    """Render a run-level task collaboration dashboard as static HTML."""
    app = payload.get("app", {})
    run = payload.get("run") or {}
    summary = payload.get("summary", {})
    title_status = _escape(summary.get("dashboard_status", run.get("status", "ready")))
    task = _escape(run.get("task", "No task has been started yet."))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="5">
  <title>muxdev Mission Control task detail</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --ink: #1f2937;
      --muted: #6b7280;
      --line: #d8dee8;
      --accent: #0f766e;
      --warn: #b45309;
      --bad: #b91c1c;
      --good: #15803d;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 14px;
      line-height: 1.45;
    }}
    header {{
      padding: 20px 28px 14px;
      border-bottom: 1px solid var(--line);
      background: #ffffff;
      position: sticky;
      top: 0;
      z-index: 1;
    }}
    h1 {{ margin: 0; font-size: 22px; font-weight: 700; letter-spacing: 0; }}
    h2 {{ margin: 0 0 10px; font-size: 15px; font-weight: 700; letter-spacing: 0; }}
    h3 {{ margin: 0 0 6px; font-size: 13px; color: var(--muted); font-weight: 700; letter-spacing: 0; text-transform: uppercase; }}
    code {{ background: #eef2f7; border: 1px solid #d8dee8; border-radius: 4px; padding: 1px 5px; }}
    .subhead {{ color: var(--muted); margin-top: 4px; overflow-wrap: anywhere; }}
    .layout {{ padding: 18px 28px 32px; display: grid; gap: 14px; grid-template-columns: repeat(12, minmax(0, 1fr)); }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 14px; min-width: 0; }}
    .span-12 {{ grid-column: span 12; }}
    .span-8 {{ grid-column: span 8; }}
    .span-6 {{ grid-column: span 6; }}
    .span-4 {{ grid-column: span 4; }}
    .span-3 {{ grid-column: span 3; }}
    .metric {{ display: grid; gap: 3px; }}
    .metric strong {{ font-size: 20px; }}
    .muted {{ color: var(--muted); }}
    .status {{ display: inline-flex; align-items: center; border-radius: 999px; padding: 2px 8px; border: 1px solid var(--line); font-weight: 700; }}
    .status.completed {{ color: var(--good); border-color: #bbf7d0; background: #f0fdf4; }}
    .status.running {{ color: var(--accent); border-color: #99f6e4; background: #f0fdfa; }}
    .status.awaiting_approval, .status.awaiting_provider_action, .status.needs_approval, .status.paused_budget {{ color: var(--warn); border-color: #fde68a; background: #fffbeb; }}
    .status.blocked, .status.failed, .status.aborted {{ color: var(--bad); border-color: #fecaca; background: #fef2f2; }}
    .progress {{ width: 100%; height: 8px; background: #e5e7eb; border-radius: 999px; overflow: hidden; }}
    .progress > div {{ height: 100%; width: {_number(summary.get("progress", 0))}%; background: var(--accent); }}
    table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
    th, td {{ border-bottom: 1px solid #eef2f7; padding: 8px 6px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }}
    th {{ color: var(--muted); font-size: 12px; font-weight: 700; }}
    tr:last-child td {{ border-bottom: 0; }}
    .task {{ white-space: pre-wrap; overflow-wrap: anywhere; }}
    .commands {{ display: grid; gap: 6px; }}
    .commands code {{ display: block; overflow-x: auto; white-space: nowrap; }}
    .terminal {{ border-color: #c7d2fe; background: #eef2ff; }}
    @media (max-width: 900px) {{
      .layout {{ grid-template-columns: 1fr; padding: 14px; }}
      .span-12, .span-8, .span-6, .span-4, .span-3 {{ grid-column: span 1; }}
      header {{ padding: 16px 14px 12px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>muxdev Mission Control <span class="status {title_status}">{title_status}</span></h1>
    <div class="subhead">{_escape(app.get("workspace", ""))}</div>
  </header>
  <main class="layout">
    <section class="panel span-8">
      <h2>Task</h2>
      <div class="task">{task}</div>
    </section>
    <section class="panel span-4 {_terminal_class(summary)}">
      <h2>Run</h2>
      {_kv("Run ID", run.get("run_id", "none"))}
      {_kv("Status", run.get("status", summary.get("dashboard_status", "ready")))}
      {_kv("Workflow", run.get("workflow", "-"))}
      {_kv("Provider", run.get("provider", "-"))}
      {_kv("Worktree", run.get("worktree", "-"))}
    </section>
    {_ux_section(payload)}
    {_scorecard_section(payload)}
    {_summary_cards(summary)}
    <section class="panel span-8">
      <h2>Task Timeline</h2>
      {_table(payload.get("stages", []), ["stage_id", "role", "status", "started_at", "completed_at", "summary"])}
    </section>
    <section class="panel span-4">
      <h2>Agents / CLI</h2>
      {_table(payload.get("agents", []), ["role", "provider", "session_id", "status"])}
      <h3>Provider Health</h3>
      {_provider_health(app)}
    </section>
    <section class="panel span-6">
      <h2>Approval Risk Review</h2>
      {_table(payload.get("approvals", []), ["approval_id", "stage_id", "type", "status", "reason", "decided_at"])}
    </section>
    <section class="panel span-6">
      <h2>Provider Action Wizard</h2>
      {_table(_provider_action_rows(payload.get("provider_actions", [])), ["action_id", "stage_id", "provider", "kind", "status", "prompt", "options", "attach"])}
    </section>
    <section class="panel span-6">
      <h2>Provider Attempts</h2>
      {_table(payload.get("provider_attempts", []), ["stage_id", "role", "provider", "attempt", "status", "failure_kind", "returncode", "summary"])}
    </section>
    <section class="panel span-6">
      <h2>Session Capsules</h2>
      {_table(payload.get("session_capsules", []), ["capsule_id", "stage_id", "provider", "kind", "status", "path"])}
    </section>
    <section class="panel span-6">
      <h2>Feedback Router</h2>
      {_table(payload.get("feedback_events", []), ["feedback_id", "source", "kind", "status", "route_to", "severity", "content"])}
    </section>
    <section class="panel span-6">
      <h2>CI Rescue</h2>
      {_table(payload.get("ci_rescues", []), ["rescue_id", "feedback_id", "rescue_run_id", "route_to", "status", "summary"])}
    </section>
    <section class="panel span-6">
      <h2>Cache / Skills / Plugins</h2>
      <h3>CAS Cache</h3>
      {_table(payload.get("cache_entries", []), ["cache_key", "kind", "path", "value_hash"])}
      <h3>Skill Lock</h3>
      {_table(payload.get("skill_locks", []), ["skill_name", "skill_version", "skill_hash", "status", "path"])}
      <h3>Plugin Manifest</h3>
      {_table(payload.get("plugin_manifests", []), ["plugin_name", "trust", "status", "manifest_hash", "source"])}
    </section>
    <section class="panel span-6">
      <h2>Memory / Guardrails</h2>
      <h3>Memory Context</h3>
      {_table(payload.get("memory_context", []), ["layer", "scope_id", "id", "kind", "role", "promotion_state", "claim"])}
      <h3>Guardrail Events</h3>
      {_table(payload.get("guardrail_events", []), ["event_id", "tool", "decision", "reason", "created_at"])}
    </section>
    <section class="panel span-6">
      <h2>Advanced Parallel</h2>
      <h3>Parallel Conflicts</h3>
      {_table(payload.get("parallel_conflicts", []), ["conflict_id", "stages", "files", "severity", "status", "resolution"])}
      <h3>Semantic Merge</h3>
      {_table(payload.get("semantic_merge_reviews", []), ["review_id", "decision", "patch_hash", "findings", "path"])}
    </section>
    <section class="panel span-6">
      <h2>Long-Term Learning</h2>
      <h3>Provider Learning</h3>
      {_table(payload.get("provider_learning", []), ["provider", "role", "attempts", "successes", "failures", "human_actions", "score"])}
      <h3>Multi-Repo Orchestration</h3>
      {_table(payload.get("multi_repo_orchestrations", []), ["orchestration_id", "mode", "status", "task", "plan_path"])}
    </section>
    <section class="panel span-6">
      <h2>Next Actions</h2>
      {_action_hints(payload)}
    </section>
    <section class="panel span-6">
      <h2>Results</h2>
      <h3>Tests</h3>
      {_table(payload.get("test_results", []), ["stage_id", "passed", "command", "summary"])}
      <h3>Review Blockers</h3>
      {_table(payload.get("review_blockers", []), ["stage_id", "type", "severity", "file", "line", "suggestion"])}
      <h3>Errors</h3>
      {_table(payload.get("errors", []), ["stage_id", "type", "message", "created_at"])}
    </section>
    <section class="panel span-6">
      <h2>Evidence / Artifacts Center</h2>
      {_table(payload.get("artifacts", []), ["name", "kind", "stage_id", "path", "created_at"])}
    </section>
    <section class="panel span-6">
      <h2>Usage</h2>
      {_table(payload.get("usage", []), ["provider", "tokens", "cost_usd", "created_at"])}
    </section>
    <section class="panel span-6">
      <h2>Recent Trace Events</h2>
      {_trace_table(payload.get("trace", []))}
    </section>
  </main>
</body>
</html>
"""


def write_dashboard(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_dashboard_html(payload), encoding="utf-8")
    return path


def _ux_section(payload: dict[str, Any]) -> str:
    ux = payload.get("ux") if isinstance(payload.get("ux"), dict) else {}
    if not ux:
        return ""
    actions = "".join(
        f"<li><strong>{_escape(action.get('label', '-'))}</strong>: {_escape(action.get('description', ''))}"
        + (f"<br><code>{_escape(action.get('command'))}</code>" if action.get("command") else "")
        + (f"<br><span class=\"muted\">{_escape(action.get('endpoint'))}</span>" if action.get("endpoint") else "")
        + "</li>"
        for action in ux.get("next_actions", [])[:5]
        if isinstance(action, dict)
    )
    deliverables = "".join(
        f"<li>{_escape(item.get('label', item.get('kind', '-')))}"
        + (f" <span class=\"muted\">{_escape(item.get('path'))}</span>" if item.get("path") else "")
        + "</li>"
        for item in ux.get("deliverables", [])[:5]
        if isinstance(item, dict)
    )
    return f"""
    <section class="panel span-12">
      <h2>Current Focus</h2>
      <p><span class="status {_escape(ux.get('user_state', ''))}">{_escape(ux.get('user_state', '-'))}</span></p>
      <h3>{_escape(ux.get('headline', '-'))}</h3>
      <p>{_escape(ux.get('why', ''))}</p>
      <h3>Next Actions</h3>
      <ul>{actions or "<li>No immediate action required.</li>"}</ul>
      <h3>Deliverables</h3>
      <ul>{deliverables or "<li>No deliverables yet.</li>"}</ul>
    </section>
    """


def _scorecard_section(payload: dict[str, Any]) -> str:
    scorecard = payload.get("evidence_scorecard")
    if not isinstance(scorecard, dict):
        return ""
    reasons = "".join(f"<li>{_escape(reason)}</li>" for reason in scorecard.get("top_reasons", [])[:4])
    missing = "".join(f"<li>{_escape(item)}</li>" for item in scorecard.get("missing_evidence", [])[:4]) or "<li>none</li>"
    return f"""
    <section class="panel span-12">
      <h2>Delivery Scorecard</h2>
      <div class="metric"><strong>{_escape(scorecard.get('score', 0))} / 100</strong><span class="status {_escape(scorecard.get('label', ''))}">{_escape(scorecard.get('label', '-'))}</span></div>
      {_kv("Recommendation", scorecard.get("recommendation", "-"))}
      <h3>Why</h3>
      <ul>{reasons or "<li>No positive evidence recorded yet.</li>"}</ul>
      <h3>Missing Evidence</h3>
      <ul>{missing}</ul>
    </section>
    """


def _summary_cards(summary: dict[str, Any]) -> str:
    cards = [
        ("Progress", f"{_number(summary.get('progress', 0))}%", '<div class="progress"><div></div></div>'),
        ("Stages", f"{_escape(summary.get('stage_done', 0))}/{_escape(summary.get('stage_total', 0))}", "completed / total"),
        ("Pending Approvals", str(summary.get("pending_approvals", 0)), "human gates"),
        ("Provider Actions", str(summary.get("pending_provider_actions", 0)), "CLI/session handoffs"),
        ("Usage", f"{summary.get('tokens', 0)} tokens", f"${float(summary.get('cost_usd') or 0):.4f}"),
    ]
    return "\n".join(
        f'<section class="panel span-3 metric"><h3>{label}</h3><strong>{value}</strong><span class="muted">{detail}</span></section>'
        for label, value, detail in cards
    )


def _table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return '<div class="muted">No records yet.</div>'
    head = "".join(f"<th>{_escape(column)}</th>" for column in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{_format_cell(row.get(column, ''))}</td>" for column in columns) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _trace_table(rows: list[dict[str, Any]]) -> str:
    normalized = [
        {
            "time": row.get("time", ""),
            "type": row.get("type", ""),
            "stage": row.get("stage", ""),
            "data": json.dumps(row.get("data", {}), ensure_ascii=False),
        }
        for row in rows[-12:]
    ]
    return _table(normalized, ["time", "type", "stage", "data"])


def _action_hints(payload: dict[str, Any]) -> str:
    pending = [row for row in payload.get("approvals", []) if row.get("status") == "pending"]
    provider_actions = [row for row in payload.get("provider_actions", []) if row.get("status") == "pending"]
    run_id = (payload.get("run") or {}).get("run_id", "latest")
    commands = []
    if provider_actions:
        action = provider_actions[0]
        if action.get("attach_command"):
            commands.append(str(action["attach_command"]))
        commands.append(f"muxdev action handled {action.get('action_id', '<action_id>')}")
        commands.append(f"muxdev continue {action.get('run_id', run_id)}")
    if pending:
        approval_id = pending[0].get("approval_id", "<approval_id>")
        commands.extend([f"muxdev approve {approval_id}", f"muxdev deny {approval_id}"])
    commands.extend(
        [
            f"muxdev continue {run_id}",
            f"muxdev report {run_id}",
            f"muxdev trace view {run_id}",
            f"muxdev diff {run_id}",
        ]
    )
    return '<div class="commands">' + "".join(f"<code>{_escape(command)}</code>" for command in commands) + "</div>"


def _provider_action_rows(rows: object) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        options = row.get("options")
        if not isinstance(options, list):
            try:
                options = json.loads(str(row.get("options_json") or "[]"))
            except json.JSONDecodeError:
                options = []
        normalized.append(
            {
                "action_id": row.get("action_id"),
                "stage_id": row.get("stage_id"),
                "provider": row.get("provider"),
                "kind": row.get("kind"),
                "status": row.get("status"),
                "prompt": row.get("prompt_text"),
                "options": ", ".join(str(option.get("label") or option.get("value")) for option in options if isinstance(option, dict)) or "-",
                "attach": row.get("attach_command") or row.get("transcript_path"),
            }
        )
    return normalized


def _provider_health(app: dict[str, Any]) -> str:
    providers = app.get("providers", {}) if isinstance(app, dict) else {}
    return (
        f"{_kv('Ready', ', '.join(providers.get('ready', [])) or '-')}"
        f"{_kv('Partial', ', '.join(providers.get('partial', [])) or '-')}"
        f"{_kv('Known', providers.get('total', 0))}"
    )


def _kv(label: str, value: object) -> str:
    return f'<p><strong>{_escape(label)}:</strong> {_format_cell(value)}</p>'


def _format_cell(value: object) -> str:
    if value is None or value == "":
        return '<span class="muted">-</span>'
    if isinstance(value, (list, dict)):
        return _escape(json.dumps(value, ensure_ascii=False))
    return _escape(value)


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _number(value: object) -> int:
    try:
        return max(0, min(100, int(float(value))))
    except (TypeError, ValueError):
        return 0


def _terminal_class(summary: dict[str, Any]) -> str:
    return "terminal" if summary.get("terminal") else ""


class TaskCreateRequest(BaseModel):
    task: str
    workspace: str | None = None
    provider: str = "mock"
    workflow: str = "software-dev"
    profile: str | None = None
    gate: str | None = None
    require_approval: list[str] = Field(default_factory=list)
    max_cost_usd: float = 0.5
    role_providers: dict[str, str] = Field(default_factory=dict)
    skills: list[dict[str, Any]] = Field(default_factory=list)
    ci_block_on_approval: bool = False
    depth: str | None = None
    topology: str | None = None
    automation: dict[str, Any] = Field(default_factory=dict)


class ContinueRequest(BaseModel):
    max_cost_usd: float = 0.5


class MultiRepoPlanRequest(BaseModel):
    task: str
    repos: list[str] = Field(default_factory=list)
    mode: str = "design"
    workspace: str | None = None


class FeedbackRequest(BaseModel):
    kind: str
    source: str = "manual"
    content: str
    workspace: str | None = None
    run_id: str | None = None
    severity: str = "medium"
    provider: str = "mock"
    payload: dict[str, Any] = Field(default_factory=dict)
    auto_submit: bool = True


def create_app(*, task_manager: object | None = None, paths: object | None = None) -> FastAPI:
    """Create the daemon FastAPI app used by API and Dashboard ports."""
    from ..daemon.tasks import TaskManager

    if task_manager is not None:
        manager = task_manager
    elif paths is not None:
        manager = TaskManager(paths=paths)
    else:
        manager = TaskManager()
    app = FastAPI(title="muxdev daemon", version="0.1.0")

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        return render_live_dashboard_html()

    @app.get("/tasks/{task_id}", response_class=HTMLResponse)
    def task_page(task_id: str) -> str:
        return render_live_dashboard_html(task_id=task_id)

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {**manager.daemon_status(), "status": "ok", "service": "muxdev"}

    @app.get("/api/daemon/status")
    def daemon_status() -> dict[str, object]:
        return manager.daemon_status()

    @app.get("/api/ux/overview")
    def ux_overview() -> dict[str, object]:
        tasks = manager.list_tasks()
        approvals = manager.approvals(status=str(ApprovalStatus.PENDING))
        actions = manager.provider_actions(status=str(ProviderActionStatus.PENDING))
        return build_ux_overview(
            daemon=manager.daemon_status(),
            tasks=tasks,
            approvals=approvals,
            provider_actions=actions,
            selected_task=tasks[0] if tasks else None,
        )

    @app.get("/api/setup/status")
    def setup_status() -> dict[str, object]:
        payload = build_setup_status(
            workspace=str(Path.cwd()),
            daemon={**manager.daemon_status(), "status": "ok"},
            provider_health=_provider_health_payload(),
        )
        payload["product_experience"] = build_product_experience(Path.cwd(), tasks=manager.list_tasks(), provider_health=payload["provider_health"])
        return payload

    @app.get("/api/providers/health")
    def providers_health() -> dict[str, object]:
        return _provider_health_payload()

    @app.get("/api/product/experience")
    def product_experience(workspace: str | None = None) -> dict[str, object]:
        root = Path(workspace or Path.cwd()).resolve()
        return build_product_experience(root, tasks=manager.list_tasks(), provider_health=_provider_health_payload())

    @app.post("/api/tasks")
    def create_task(request: TaskCreateRequest) -> dict[str, object]:
        workspace = Path(request.workspace or Path.cwd()).resolve()
        return manager.submit_task(
            task=request.task,
            workspace=workspace,
            provider=request.provider,
            workflow=request.workflow,
            profile=request.profile,
            gate=request.gate,
            require_approval=set(request.require_approval),
            max_cost_usd=request.max_cost_usd,
            role_providers=request.role_providers,
            skills=request.skills,
            ci_block_on_approval=request.ci_block_on_approval,
            depth=request.depth,
            topology=request.topology,
            automation=request.automation,
        )

    @app.get("/api/tasks")
    def list_tasks() -> list[dict[str, object]]:
        return manager.list_tasks()

    @app.get("/api/tasks/{task_id}")
    def task_detail(task_id: str) -> dict[str, object]:
        try:
            return manager.task_detail(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/tasks/{task_id}/ux")
    def task_ux(task_id: str) -> dict[str, object]:
        try:
            return build_task_ux_summary(manager.task_detail(task_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/tasks/{task_id}/continue")
    def continue_task(task_id: str, request: ContinueRequest | None = None) -> dict[str, object]:
        try:
            return manager.continue_task(task_id, max_cost_usd=(request.max_cost_usd if request else 0.5))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/tasks/{task_id}/stop")
    def stop_task(task_id: str) -> dict[str, object]:
        try:
            return manager.stop_task(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/tasks/{task_id}/diff")
    def task_diff(task_id: str) -> dict[str, object]:
        try:
            return manager.diff(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/tasks/{task_id}/report")
    def task_report(task_id: str) -> dict[str, object]:
        try:
            return manager.report(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/tasks/{task_id}/rollback")
    def rollback_task(task_id: str, to_stage: str | None = None) -> dict[str, object]:
        try:
            return manager.rollback(task_id, to_stage=to_stage)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/tasks/{task_id}/attach-command")
    def attach_command(task_id: str, agent: str = "implementer") -> dict[str, object]:
        try:
            return manager.attach_command(task_id, agent=agent)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/provider-actions")
    def provider_actions(status: str | None = None) -> list[dict[str, object]]:
        return manager.provider_actions(status=status)

    @app.get("/api/provider-scores")
    def provider_scores(role: str | None = None) -> list[dict[str, object]]:
        return manager.provider_scores(role=role)

    @app.get("/api/learning/provider")
    def provider_learning(role: str | None = None) -> list[dict[str, object]]:
        return manager.provider_learning(role=role)

    @app.get("/api/parallel-conflicts")
    def parallel_conflicts(status: str | None = None) -> list[dict[str, object]]:
        return manager.parallel_conflicts(status=status)

    @app.get("/api/semantic-merge-reviews")
    def semantic_merge_reviews() -> list[dict[str, object]]:
        return manager.semantic_merge_reviews()

    @app.get("/api/multi-repo/orchestrations")
    def multi_repo_orchestrations(status: str | None = None) -> list[dict[str, object]]:
        return manager.multi_repo_orchestrations(status=status)

    @app.post("/api/multi-repo/plan")
    def multi_repo_plan(request: MultiRepoPlanRequest) -> dict[str, object]:
        workspace = Path(request.workspace or Path.cwd()).resolve()
        with manager.board() as board:
            return plan_multi_repo_orchestration(
                workspace,
                repos=[Path(repo) for repo in request.repos],
                task=request.task,
                mode=request.mode,
                blackboard=board,
            )

    @app.post("/api/feedback")
    def feedback(request: FeedbackRequest) -> dict[str, object]:
        workspace = Path(request.workspace or Path.cwd()).resolve()
        return manager.ingest_feedback(
            kind=request.kind,
            source=request.source,
            content=request.content,
            workspace=workspace,
            run_id=request.run_id,
            severity=request.severity,
            provider=request.provider,
            payload=request.payload,
            auto_submit=request.auto_submit,
        )

    @app.get("/api/ecosystem")
    def ecosystem() -> dict[str, object]:
        return manager.ecosystem_state()

    @app.get("/api/tasks/{task_id}/provider-actions")
    def task_provider_actions(task_id: str, status: str | None = None) -> list[dict[str, object]]:
        try:
            return manager.provider_actions(status=status, task_id=task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/tasks/{task_id}/parallel-conflicts")
    def task_parallel_conflicts(task_id: str, status: str | None = None) -> list[dict[str, object]]:
        try:
            return manager.parallel_conflicts(status=status, task_id=task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/tasks/{task_id}/semantic-merge-reviews")
    def task_semantic_merge_reviews(task_id: str) -> list[dict[str, object]]:
        try:
            return manager.semantic_merge_reviews(task_id=task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/memory/contradictions")
    def memory_contradictions(workspace: str | None = None, status: str | None = None) -> list[dict[str, object]]:
        with MemoryStore(Path(workspace or Path.cwd()).resolve()) as store:
            if status is None:
                store.detect_contradictions()
            return store.list_contradictions(status=status)

    @app.get("/api/memory/inbox")
    def memory_inbox(workspace: str | None = None, limit: int = 50) -> dict[str, object]:
        with MemoryStore(Path(workspace or Path.cwd()).resolve()) as store:
            return store.inbox(limit=limit)

    @app.post("/api/memory/{memory_id}/promote")
    def memory_promote(memory_id: str, workspace: str | None = None, layer: str = "project", scope_id: str | None = None) -> dict[str, object]:
        try:
            with MemoryStore(Path(workspace or Path.cwd()).resolve()) as store:
                return store.promote(memory_id, layer=layer, scope_id=scope_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/memory/quarantine-auto")
    def memory_quarantine_auto(workspace: str | None = None) -> list[dict[str, object]]:
        with MemoryStore(Path(workspace or Path.cwd()).resolve()) as store:
            store.detect_contradictions()
            return store.auto_quarantine_contradictions()

    @app.post("/api/provider-actions/{action_id}/handled")
    def provider_action_handled(action_id: str) -> dict[str, object]:
        try:
            return manager.update_provider_action(action_id, ProviderActionStatus.HANDLED)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/tasks/{task_id}/actions/{action_id}/handled-and-continue")
    def provider_action_handled_and_continue(task_id: str, action_id: str, request: ContinueRequest | None = None) -> dict[str, object]:
        try:
            handled = manager.update_provider_action(action_id, ProviderActionStatus.HANDLED)
            continued = manager.continue_task(task_id, max_cost_usd=(request.max_cost_usd if request else 0.5))
            return {
                "task_id": task_id,
                "run_id": task_id,
                "action": handled,
                "continue": continued,
                "status": continued.get("status"),
            }
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/provider-actions/{action_id}/dismiss")
    def provider_action_dismiss(action_id: str) -> dict[str, object]:
        try:
            return manager.update_provider_action(action_id, ProviderActionStatus.DISMISSED)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/approvals")
    def approvals(status: str | None = None) -> list[dict[str, object]]:
        return manager.approvals(status=status)

    @app.post("/api/approvals/{approval_id}/approve")
    def approve(approval_id: str) -> dict[str, object]:
        try:
            return manager.decide_approval(approval_id, ApprovalStatus.APPROVED)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/approvals/{approval_id}/deny")
    def deny(approval_id: str) -> dict[str, object]:
        try:
            return manager.decide_approval(approval_id, ApprovalStatus.DENIED)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def _events(websocket: WebSocket) -> None:
        await websocket.accept()
        queue = await manager.subscribe()
        try:
            while True:
                await websocket.send_json(await queue.get())
        except WebSocketDisconnect:
            manager.unsubscribe(queue)

    app.websocket("/events")(_events)
    app.websocket("/api/events")(_events)
    return app


def _provider_health_payload() -> dict[str, object]:
    return build_provider_health([probe.to_dict() for probe in detect_providers()])


def render_live_dashboard_html(task_id: str | None = None) -> str:
    """Render the daemon-backed Mission Control dashboard."""
    initial_hash = f"data-task-id=\"{_escape(task_id)}\"" if task_id else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>muxdev Mission Control</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --ink: #17202a;
      --muted: #64748b;
      --line: #d9e0ea;
      --accent: #0f766e;
      --warn: #a16207;
      --bad: #b91c1c;
      --good: #15803d;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: var(--bg); color: var(--ink); font: 14px/1.45 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    header {{ padding: 16px 24px; border-bottom: 1px solid var(--line); background: var(--panel); display: flex; gap: 16px; align-items: center; justify-content: space-between; }}
    h1 {{ margin: 0; font-size: 22px; letter-spacing: 0; }}
    h2 {{ margin: 0 0 10px; font-size: 15px; letter-spacing: 0; }}
    h3 {{ margin: 0 0 8px; font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 0; }}
    button {{ border: 1px solid var(--line); background: #fff; color: var(--ink); border-radius: 6px; padding: 6px 10px; cursor: pointer; }}
    button.primary {{ background: var(--accent); border-color: var(--accent); color: #fff; }}
    main {{ padding: 18px 24px 28px; display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 14px; }}
    section {{ min-width: 0; }}
    .panel, .task, .card {{ background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 14px; }}
    .span-12 {{ grid-column: span 12; }}
    .span-8 {{ grid-column: span 8; }}
    .span-6 {{ grid-column: span 6; }}
    .span-4 {{ grid-column: span 4; }}
    .span-3 {{ grid-column: span 3; }}
    .stack {{ display: grid; gap: 10px; }}
    .metrics {{ display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 10px; }}
    .metric strong {{ display: block; font-size: 22px; }}
    .task {{ cursor: pointer; }}
    .task.active {{ border-color: var(--accent); box-shadow: inset 3px 0 0 var(--accent); }}
    .meta {{ color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }}
    .status {{ display: inline-flex; border: 1px solid var(--line); border-radius: 999px; padding: 1px 7px; font-weight: 700; }}
    .completed {{ color: var(--good); background: #f0fdf4; border-color: #bbf7d0; }}
    .running {{ color: var(--accent); background: #f0fdfa; border-color: #99f6e4; }}
    .awaiting_approval, .awaiting_provider_action, .paused_budget {{ color: var(--warn); background: #fffbeb; border-color: #fde68a; }}
    .blocked, .aborted {{ color: var(--bad); background: #fef2f2; border-color: #fecaca; }}
    table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
    th, td {{ border-bottom: 1px solid #edf1f7; padding: 7px 6px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }}
    th {{ color: var(--muted); font-size: 12px; }}
    pre {{ background: #0f172a; color: #e2e8f0; border-radius: 8px; padding: 12px; overflow: auto; max-height: 340px; }}
    .actions {{ display: flex; gap: 8px; flex-wrap: wrap; margin: 10px 0; }}
    .callout {{ border: 1px solid var(--line); border-left: 4px solid var(--accent); border-radius: 8px; padding: 12px; margin-bottom: 12px; background: #f8fafc; }}
    .callout.needs_action, .callout.needs_approval {{ border-left-color: var(--warn); background: #fffbeb; }}
    .callout.failed {{ border-left-color: var(--bad); background: #fef2f2; }}
    .callout.deliverable {{ border-left-color: var(--good); background: #f0fdf4; }}
    .action-card {{ border: 1px solid var(--line); border-radius: 8px; padding: 10px; margin-bottom: 8px; background: #fff; }}
    .risk-high {{ border-left: 4px solid var(--bad); }}
    .risk-medium {{ border-left: 4px solid var(--warn); }}
    .risk-low {{ border-left: 4px solid var(--accent); }}
    .board {{ display: grid; grid-template-columns: repeat(6, minmax(190px, 1fr)); gap: 10px; overflow-x: auto; }}
    .column {{ min-width: 190px; background: #f8fafc; border: 1px solid var(--line); border-radius: 8px; padding: 10px; }}
    .column h3 {{ display: flex; justify-content: space-between; align-items: center; }}
    .filter-row {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    .filter-row span {{ border: 1px solid var(--line); border-radius: 999px; padding: 2px 8px; background: #fff; }}
    .timeline {{ display: grid; gap: 8px; }}
    .timeline-step {{ display: grid; grid-template-columns: 160px minmax(0,1fr); gap: 10px; align-items: start; border-bottom: 1px solid #edf1f7; padding-bottom: 8px; }}
    .timeline-step:last-child {{ border-bottom: 0; }}
    .deliverables {{ display: flex; gap: 8px; flex-wrap: wrap; margin: 8px 0; }}
    .deliverables span {{ border: 1px solid var(--line); border-radius: 999px; padding: 2px 8px; background: #fff; }}
    @media (max-width: 1100px) {{ .span-8, .span-6, .span-4, .span-3 {{ grid-column: span 12; }} .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} }}
    @media (max-width: 860px) {{ main {{ grid-template-columns: 1fr; padding: 14px; }} header {{ padding: 14px; align-items: flex-start; flex-direction: column; }} .span-12, .span-8, .span-6, .span-4, .span-3 {{ grid-column: span 1; }} .metrics {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body {initial_hash}>
  <header>
    <div>
      <h1>muxdev Mission Control</h1>
      <div class="meta">current status | next actions | task board | timeline | evidence</div>
    </div>
    <div class="meta" id="daemon-status">connecting</div>
  </header>
  <main>
    <section class="panel span-12">
      <h2>Current Status</h2>
      <div id="current-status" class="metrics"></div>
    </section>
    <section class="panel span-8">
      <h2>Action Center</h2>
      <div id="action-center" class="meta">Loading.</div>
    </section>
    <section class="panel span-4">
      <h2>Filters</h2>
      <div id="filters" class="stack"></div>
    </section>
    <section class="panel span-12">
      <h2>Product Experience</h2>
      <div id="product-experience" class="stack"></div>
    </section>
    <section class="panel span-12">
      <h2>Task Board</h2>
      <div id="task-board" class="board"></div>
    </section>
    <section class="panel span-8">
      <h2>Task Timeline</h2>
      <div id="timeline" class="timeline meta">Select a task.</div>
    </section>
    <section class="panel span-4">
      <h2>Task Detail</h2>
      <div id="detail" class="meta">Select a task.</div>
    </section>
    <section class="panel span-12">
      <h2>Evidence / Artifacts Center</h2>
      <div id="artifacts-center" class="stack"></div>
    </section>
    <section class="panel span-6">
      <h2>Provider Action Wizard</h2>
      <div id="provider-actions"></div>
    </section>
    <section class="panel span-6">
      <h2>Approval Risk Review</h2>
      <div id="approvals"></div>
    </section>
    <section class="panel span-12">
      <h2>Recent Events</h2>
      <pre id="events"></pre>
    </section>
  </main>
  <script>
    const state = {{ taskId: document.body.dataset.taskId || null, events: [] }};
    const statusClass = value => `status ${{value || ''}}`;
    const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}})[ch]);
    const fmt = value => Array.isArray(value) || (value && typeof value === 'object') ? JSON.stringify(value) : value;
    async function api(path, options) {{
      const response = await fetch('/api' + path, options);
      if (!response.ok) throw new Error(await response.text());
      return response.json();
    }}
    async function optionalApi(path, fallback) {{
      try {{ return await api(path); }} catch (_error) {{ return fallback; }}
    }}
    async function refresh() {{
      const health = await api('/health');
      document.getElementById('daemon-status').textContent = `tasks=${{health.tasks}} running=${{health.running_tasks}} queue=${{health.queue_length}}`;
      const overview = await optionalApi('/ux/overview', {{ action_center: [], counts: {{}} }});
      renderCurrentStatus(overview.current_status || {{}}, overview.counts || {{}});
      renderActionCenter(overview.action_center || []);
      renderFilters(overview.filters || {{}});
      renderTaskBoard(overview.task_board || []);
      renderProductExperience(await optionalApi('/product/experience', null));
      const tasks = await api('/tasks');
      if (!state.taskId && tasks.length) state.taskId = tasks[0].task_id;
      renderProviderActions(await optionalApi('/provider-actions?status=pending', []));
      renderApprovals(await api('/approvals?status=pending'));
      if (state.taskId) {{
        const detail = await api('/tasks/' + encodeURIComponent(state.taskId));
        renderDetail(detail);
        renderTimeline(detail);
        renderArtifactsCenter(detail, overview.artifact_center || {{}});
      }} else {{
        renderArtifactsCenter({{}}, overview.artifact_center || {{}});
      }}
    }}
    function renderCurrentStatus(status, counts) {{
      const cards = [
        ['Running', status.running ?? counts.active ?? 0, 'active tasks'],
        ['Stuck', status.stuck ?? 0, 'blocked or errored'],
        ['Provider Actions', status.waiting_provider_action ?? status.pending_provider_actions ?? 0, 'external CLI waits'],
        ['muxdev Approvals', status.waiting_muxdev_approval ?? status.pending_approvals ?? 0, 'policy gates'],
        ['Done', (status.recent_completed || []).length, 'recent deliverables']
      ];
      document.getElementById('current-status').innerHTML = cards.map(([label, value, hint]) => `
        <div class="card metric">
          <h3>${{esc(label)}}</h3>
          <strong>${{esc(value)}}</strong>
          <span class="meta">${{esc(hint)}}</span>
        </div>`).join('');
    }}
    function renderFilters(filters) {{
      const labels = ['provider', 'workflow', 'status', 'branch', 'risk', 'cost'];
      document.getElementById('filters').innerHTML = labels.map(label => {{
        const values = filters[label] || [];
        return `<div><h3>${{esc(label)}}</h3><div class="filter-row">${{values.length ? values.map(value => `<span>${{esc(value)}}</span>`).join('') : '<span>-</span>'}}</div></div>`;
      }}).join('');
    }}
    function renderTaskBoard(columns) {{
      document.getElementById('task-board').innerHTML = columns.length ? columns.map(column => `
        <div class="column">
          <h3><span>${{esc(column.label)}}</span><span>${{(column.tasks || []).length}}</span></h3>
          <div class="stack">
            ${{(column.tasks || []).map(task => taskCard(task)).join('') || '<div class="meta">No tasks.</div>'}}
          </div>
        </div>`).join('') : '<div class="meta">No tasks yet.</div>';
    }}
    function renderProductExperience(payload) {{
      if (!payload) {{
        document.getElementById('product-experience').innerHTML = '<div class="meta">Product surface unavailable.</div>';
        return;
      }}
      const setup = payload.provider_setup || {{}};
      const health = payload.provider_health || {{}};
      const budget = payload.budget || {{}};
      const git = payload.git_safety || {{}};
      const rules = payload.rules_skills || {{}};
      const context = payload.project_context || {{}};
      document.getElementById('product-experience').innerHTML = `
        <div class="metrics">
          <div class="card metric"><h3>Install</h3><strong>${{esc(payload.quickstart?.one_line_install || '-')}}</strong><span class="meta">${{esc((payload.quickstart?.first_run || []).join(' -> '))}}</span></div>
          <div class="card metric"><h3>Budget</h3><strong>$${{esc(budget.total_cost_usd ?? 0)}}</strong><span class="meta">${{esc(budget.total_tokens ?? 0)}} tokens, default $${{esc(budget.default_task_budget_usd ?? 0.5)}}</span></div>
          <div class="card metric"><h3>Providers</h3><strong>${{esc((health.ready || []).join(', ') || 'mock')}}</strong><span class="meta">partial=${{esc((health.partial || []).length || 0)}} unavailable=${{esc((health.unavailable || []).length || 0)}}</span></div>
          <div class="card metric"><h3>Git Safety</h3><strong>${{esc(git.status || '-')}}</strong><span class="meta">${{esc(git.branch || git.warning || '')}}</span></div>
          <div class="card metric"><h3>Rules / Skills</h3><strong>${{esc(rules.gate || '-')}}</strong><span class="meta">${{esc((rules.skills || []).length)}} skills, profile=${{esc(rules.profile || '-')}}</span></div>
        </div>
        <div class="deliverables">
          <span>MUXDEV.md: ${{context.exists ? 'ready' : 'missing'}}</span>
          <span>${{esc(context.command || 'muxdev context --write')}}</span>
          <span>Kanban filters: provider / workflow / status / branch / risk / cost</span>
          <span>IDE/Web: ${{esc(payload.web_ui?.ide_plugin || 'optional')}}</span>
        </div>
        ${{tableBlock('Provider Setup Steps', setup.steps || [], ['provider','status','installed','action'])}}
        ${{tableBlock('Git Commands', (git.commands || []).map(command => ({{command}})), ['command'])}}
        ${{tableBlock('Rules And Skills Commands', (rules.commands || []).map(command => ({{command}})), ['command'])}}
      `;
    }}
    function taskCard(task) {{
      const id = task.task_id || task.run_id || '';
      return `<div class="task risk-${{esc(task.risk || 'low')}} ${{id === state.taskId ? 'active' : ''}}" onclick="state.taskId='${{esc(id)}}'; refresh()">
        <strong>${{esc(id)}}</strong> <span class="${{statusClass(task.status)}}">${{esc(task.status || '-')}}</span>
        <div>${{esc(task.task || '')}}</div>
        <div class="meta">provider=${{esc(task.provider || '-')}} workflow=${{esc(task.workflow || '-')}} stage=${{esc(task.current_stage || '-')}}</div>
        <div class="meta">risk=${{esc(task.risk || '-')}} cost=${{esc(task.cost_usd || 0)}} tokens=${{esc(task.tokens || 0)}} approvals=${{esc(task.pending_approvals || 0)}} actions=${{esc(task.pending_provider_actions || 0)}}</div>
      </div>`;
    }}
    function renderDetail(payload) {{
      const run = payload.run || {{}};
      const stages = payload.stages || [];
      const context = payload.context || {{}};
      const ux = payload.ux || {{}};
      document.getElementById('detail').innerHTML = `
        ${{uxBlock(ux)}}
        <div><strong>${{esc(run.run_id)}}</strong> <span class="${{statusClass(run.status)}}">${{esc(run.status)}}</span></div>
        <div>${{esc(run.task || '')}}</div>
        <div class="meta">workspace=${{esc(run.workspace || '')}}</div>
        <div class="meta">profile=${{esc(context.profile || '-')}} gate=${{esc(context.gate || '-')}} skills=${{esc((context.skills || []).map(skill => skill.name || skill).join(', ') || '-')}}</div>
        <div class="actions">
          <button class="primary" onclick="post('/tasks/${{encodeURIComponent(run.run_id)}}/continue')">Continue</button>
          <button onclick="post('/tasks/${{encodeURIComponent(run.run_id)}}/stop')">Stop</button>
          <button onclick="loadText('/tasks/${{encodeURIComponent(run.run_id)}}/diff','diff')">Diff</button>
          <button onclick="loadText('/tasks/${{encodeURIComponent(run.run_id)}}/report','content')">Report</button>
          <button onclick="post('/tasks/${{encodeURIComponent(run.run_id)}}/rollback')">Rollback point</button>
        </div>
        ${{scorecardBlock(payload.evidence_scorecard)}}
        ${{tableBlock('Memory Context', payload.memory_context || [], ['layer','scope_id','id','kind','role','promotion_state','claim'])}}
        ${{tableBlock('Provider Attempts', payload.provider_attempts || [], ['stage_id','role','provider','attempt','status','failure_kind','summary'])}}
        ${{tableBlock('Role Sessions', payload.session_capsules || [], ['stage_id','provider','kind','status','path'])}}
        ${{tableBlock('Feedback Router', payload.feedback_events || [], ['feedback_id','kind','status','route_to','content'])}}
        ${{tableBlock('CI Rescue', payload.ci_rescues || [], ['rescue_id','feedback_id','rescue_run_id','route_to','status','summary'])}}
        ${{tableBlock('CAS Cache', payload.cache_entries || [], ['cache_key','kind','path','value_hash'])}}
        ${{tableBlock('Skill Lock', payload.skill_locks || [], ['skill_name','skill_hash','status','path'])}}
        ${{tableBlock('Plugin Manifest', payload.plugin_manifests || [], ['plugin_name','trust','status','manifest_hash'])}}
        ${{tableBlock('Guardrail Events', payload.guardrail_events || [], ['tool','decision','reason','created_at'])}}
        ${{tableBlock('Parallel Conflicts', payload.parallel_conflicts || [], ['conflict_id','severity','status','stages','files'])}}
        ${{tableBlock('Semantic Merge Reviews', payload.semantic_merge_reviews || [], ['review_id','decision','patch_hash','path'])}}
        ${{tableBlock('Provider Learning', payload.provider_learning || [], ['provider','role','attempts','successes','failures','human_actions','score'])}}
        ${{tableBlock('Multi-Repo Orchestration', payload.multi_repo_orchestrations || [], ['orchestration_id','mode','status','task','plan_path'])}}
        ${{tableBlock('Rollback Snapshots', payload.snapshots || [], ['stage_id','snapshot_id','path','created_at'])}}`;
    }}
    function renderTimeline(payload) {{
      const stages = payload.stages || [];
      document.getElementById('timeline').innerHTML = stages.length ? stages.map(row => `
        <div class="timeline-step">
          <div><strong>${{esc(row.stage_id)}}</strong><div class="meta">${{esc(row.role || '-')}}</div></div>
          <div><span class="${{statusClass(row.status)}}">${{esc(row.status || '-')}}</span><div>${{esc(row.summary || '')}}</div><div class="meta">${{esc(row.started_at || '')}} ${{esc(row.completed_at || '')}}</div></div>
        </div>`).join('') : '<div class="meta">No timeline yet.</div>';
    }}
    function renderArtifactsCenter(payload, overviewCenter) {{
      const recent = (overviewCenter.recent_completed || []).map(item => `
        <div class="action-card">
          <strong>${{esc(item.task_id)}}</strong>
          <div>${{esc(item.task || '')}}</div>
          <div class="actions">
            <button onclick="loadText('${{esc(item.report_endpoint || '')}}'.replace('/api',''),'content')">Report</button>
            <button onclick="loadText('${{esc(item.diff_endpoint || '')}}'.replace('/api',''),'diff')">Diff</button>
          </div>
        </div>`).join('');
      const parts = [
        tableBlock('Final Reports / Artifacts', payload.artifacts || [], ['name','kind','stage_id','path','created_at']),
        tableBlock('Test Output', payload.test_results || [], ['stage_id','passed','command','summary']),
        tableBlock('Provider Transcript', payload.session_capsules || [], ['stage_id','provider','kind','status','path']),
        tableBlock('Stage Contracts', payload.stage_contracts || [], ['stage_id','contract_hash','path']),
        tableBlock('Evidence Bundles', payload.evidence_bundles || [], ['stage_id','bundle_hash','path']),
        tableBlock('Evidence Items', payload.evidence_items || [], ['evidence_id','kind','strength','claim','human_summary']),
        tableBlock('Semantic Merge Result', payload.semantic_merge_reviews || [], ['review_id','decision','patch_hash','path']),
        tableBlock('Rollback Points', payload.snapshots || [], ['stage_id','snapshot_id','path','created_at'])
      ];
      document.getElementById('artifacts-center').innerHTML = `${{recent ? `<h3>Recently Completed</h3>${{recent}}` : ''}}${{parts.join('')}}`;
    }}
    function renderActionCenter(rows) {{
      document.getElementById('action-center').innerHTML = rows.length ? rows.map(row => `
        <div class="action-card">
          <strong>${{esc(row.headline)}}</strong>
          <div>${{esc(row.why || '')}}</div>
          ${{row.command ? `<code>${{esc(row.command)}}</code>` : ''}}
          <div class="actions">
            ${{row.endpoint ? `<button class="primary" onclick="post('${{esc(row.endpoint).replace('/api','')}}')">Take action</button>` : ''}}
          </div>
        </div>`).join('') : '<div class="meta">Nothing needs your attention. Running tasks will appear here if they pause.</div>';
    }}
    function uxBlock(ux) {{
      if (!ux || !ux.headline) return '';
      const actions = (ux.next_actions || []).map(action => actionButton(action)).join('');
      const deliverables = (ux.deliverables || []).map(item => `<span>${{esc(item.label || item.kind)}}</span>`).join('') || '<span>none yet</span>';
      return `<div class="callout ${{esc(ux.user_state || '')}}">
        <div><span class="${{statusClass(ux.user_state)}}">${{esc(ux.user_state || '-')}}</span> risk=${{esc(ux.risk || '-')}}</div>
        <h2>${{esc(ux.headline)}}</h2>
        <div>${{esc(ux.why || '')}}</div>
        <div class="actions">${{actions || '<span class="meta">No immediate action.</span>'}}</div>
        <div class="deliverables">${{deliverables}}</div>
      </div>`;
    }}
    function actionButton(action) {{
      const cls = action.danger ? '' : 'primary';
      if (action.endpoint) return `<button class="${{cls}}" onclick="post('${{esc(action.endpoint).replace('/api','')}}')">${{esc(action.label)}}</button>`;
      if (action.command) return `<code>${{esc(action.command)}}</code>`;
      return `<span class="meta">${{esc(action.label || action.kind)}}</span>`;
    }}
    function tableBlock(title, rows, columns) {{
      if (!rows.length) return `<h2>${{esc(title)}}</h2><div class="meta">No records.</div>`;
      return `<h2>${{esc(title)}}</h2><table><thead><tr>${{columns.map(column => `<th>${{esc(column)}}</th>`).join('')}}</tr></thead><tbody>${{rows.map(row => `<tr>${{columns.map(column => `<td>${{esc(fmt(row[column]))}}</td>`).join('')}}</tr>`).join('')}}</tbody></table>`;
    }}
    function scorecardBlock(scorecard) {{
      if (!scorecard) return '';
      const reasons = (scorecard.top_reasons || []).slice(0, 4).map(item => `<li>${{esc(item)}}</li>`).join('') || '<li>No positive evidence recorded yet.</li>';
      const missing = (scorecard.missing_evidence || []).slice(0, 4).map(item => `<li>${{esc(item)}}</li>`).join('') || '<li>none</li>';
      return `<h2>Delivery Scorecard</h2>
        <div><strong>${{esc(scorecard.score)}} / 100</strong> <span class="${{statusClass(scorecard.label)}}">${{esc(scorecard.label)}}</span></div>
        <div class="meta">recommendation=${{esc(scorecard.recommendation)}} risk_penalty=${{esc(scorecard.risk_penalty)}}</div>
        <h2>Why</h2><ul>${{reasons}}</ul>
        <h2>Missing Evidence</h2><ul>${{missing}}</ul>`;
    }}
    function renderApprovals(rows) {{
      document.getElementById('approvals').innerHTML = rows.length ? rows.map(row => `
        <div class="action-card risk-medium">
          <h3>Approval Required</h3>
          <strong>${{esc(row.type || 'policy gate')}}</strong>
          <div>${{esc(row.reason || 'A muxdev policy gate needs a decision.')}}</div>
          <div class="meta">Task=${{esc(row.run_id || row.task_id || '-')}} Stage=${{esc(row.stage_id || '-')}} Approval=${{esc(row.approval_id)}}</div>
          <ul>
            <li>Files: ${{esc(row.files || row.files_json || 'review diff/evidence')}}</li>
            <li>Shell: ${{esc(row.shell || row.command || 'unknown')}}</li>
            <li>Network: ${{esc(row.network || 'unknown')}} Dependencies: ${{esc(row.dependencies || 'unknown')}}</li>
            <li>Delete/secret/database/deploy risk: ${{esc(row.risk || row.subject_hash || 'check evidence')}}</li>
            <li>Rollback point: ${{esc(row.rollback || 'available from task snapshots when recorded')}}</li>
          </ul>
          <div class="actions">
            <button class="primary" onclick="post('/approvals/${{encodeURIComponent(row.approval_id)}}/approve')">Approve</button>
            <button onclick="post('/approvals/${{encodeURIComponent(row.approval_id)}}/deny')">Deny</button>
            <button onclick="loadText('/tasks/${{encodeURIComponent(row.run_id || row.task_id)}}/diff','diff')">View diff</button>
            <button onclick="state.taskId='${{esc(row.run_id || row.task_id || '')}}'; refresh()">View evidence</button>
          </div>
        </div>`).join('') : '<div class="meta">No pending approvals.</div>';
    }}
    function renderProviderActions(rows) {{
      document.getElementById('provider-actions').innerHTML = rows.length ? rows.map(row => {{
        const attach = row.attach_command || row.transcript_path || '';
        const options = (row.options || []).map(option => option.label || option.value).join(', ') || '-';
        return `<div class="action-card risk-medium">
          <h3>Provider Action Required</h3>
          <strong>${{esc(row.provider || 'provider')}}</strong>
          <div class="meta">Reason=${{esc(row.kind || 'provider prompt')}} Task=${{esc(row.run_id || '-')}} Stage=${{esc(row.stage_id || '-')}}</div>
          <p>${{esc(row.prompt_text || '')}}</p>
          <div class="meta">Options: ${{esc(options)}}</div>
          <ol>
            <li>Open provider session <code>${{esc(attach || '-')}}</code></li>
            <li>Handle the prompt in the provider CLI.</li>
            <li>Return here and continue.</li>
          </ol>
          <div class="actions">
            <button onclick="copyText('${{esc(attach)}}')">Copy attach command</button>
            <button class="primary" onclick="post('/tasks/${{encodeURIComponent(row.run_id)}}/actions/${{encodeURIComponent(row.action_id)}}/handled-and-continue')">Mark handled and continue</button>
            <button onclick="post('/provider-actions/${{encodeURIComponent(row.action_id)}}/dismiss')">Dismiss</button>
            <button onclick="post('/tasks/${{encodeURIComponent(row.run_id)}}/stop')">Stop task</button>
          </div>
        </div>`;
      }}).join('') : '<div class="meta">No pending provider actions.</div>';
    }}
    async function post(path) {{ await api(path, {{ method: 'POST' }}); await refresh(); }}
    async function loadText(path, key) {{ const payload = await api(path); document.getElementById('events').textContent = payload[key] || ''; }}
    async function copyText(value) {{ if (navigator.clipboard && value) await navigator.clipboard.writeText(value); }}
    function connectEvents() {{
      const socket = new WebSocket(`${{location.protocol === 'https:' ? 'wss' : 'ws'}}://${{location.host}}/events`);
      socket.onmessage = event => {{ state.events.push(event.data); state.events = state.events.slice(-20); document.getElementById('events').textContent = state.events.join('\\n'); refresh().catch(() => {{}}); }};
      socket.onclose = () => setTimeout(connectEvents, 2000);
    }}
    refresh().catch(error => document.getElementById('daemon-status').textContent = error.message);
    connectEvents();
    setInterval(() => refresh().catch(() => {{}}), 5000);
  </script>
</body>
</html>"""
