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
from ..presentation.dashboard import (
    build_dashboard_overview,
    dashboard_hidden_projects_path,
    dashboard_hidden_tasks_path,
    hide_dashboard_project,
    hide_dashboard_task,
    load_hidden_projects,
    load_hidden_tasks,
    restore_dashboard_project,
    restore_dashboard_task,
)
from ..services.multirepo import plan_multi_repo_orchestration
from ..services.product_experience import build_product_experience
from ..services.skills import activate_skill, build_skill_catalog, scan_skills, score_skill, set_skill_policy, skill_show
from ..services.skills.events import read_skill_events
from ..services.skills import verify_skill_lock, write_skill_lock
from ..services.ux import build_provider_health, build_setup_status, build_task_ux_summary, build_ux_overview
from ..services.validation import list_validation_experiments, load_validation_experiment
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
      {_table(_provider_action_rows(payload.get("provider_actions", [])), ["action_id", "stage_id", "provider", "kind", "input_kind", "status", "prompt", "choices", "default_choice", "response", "attach"])}
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
      <h2>Cache / Skills</h2>
      <h3>CAS Cache</h3>
      {_table(payload.get("cache_entries", []), ["cache_key", "kind", "path", "value_hash"])}
      <h3>Skill Lock</h3>
      {_table(payload.get("skill_locks", []), ["skill_name", "skill_version", "skill_hash", "status", "path"])}
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
        options = row.get("choices") or row.get("options")
        if not isinstance(options, list):
            try:
                options = json.loads(str(row.get("choices_json") or row.get("options_json") or "[]"))
            except json.JSONDecodeError:
                options = []
        normalized.append(
            {
                "action_id": row.get("action_id"),
                "stage_id": row.get("stage_id"),
                "provider": row.get("provider"),
                "kind": row.get("kind"),
                "input_kind": row.get("input_kind"),
                "status": row.get("status"),
                "prompt": row.get("prompt_text"),
                "choices": ", ".join(str(option.get("label") or option.get("value")) for option in options if isinstance(option, dict)) or "-",
                "default_choice": row.get("default_choice") or "-",
                "response": row.get("response") if row.get("response") is not None else "-",
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


class ProviderActionResponseRequest(BaseModel):
    response: Any | None = None
    choice: str | None = None
    text: str | None = None
    max_cost_usd: float = 0.5

    def response_payload(self) -> Any:
        if self.response is not None:
            return self.response
        if self.choice is not None:
            return {"choice": self.choice}
        if self.text is not None:
            return {"text": self.text}
        return {"handled": True}


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

    def _dashboard_project_store() -> Path:
        return dashboard_hidden_projects_path(manager.paths.data_dir)

    def _dashboard_task_store() -> Path:
        return dashboard_hidden_tasks_path(manager.paths.data_dir)

    def _dashboard_payload(root: Path, *, include_hidden: bool = False, include_global_config: bool = True) -> dict[str, object]:
        tasks = manager.list_tasks()
        approvals = manager.approvals(status=str(ApprovalStatus.PENDING))
        actions = manager.provider_actions(status=str(ProviderActionStatus.PENDING))
        return build_dashboard_overview(
            root,
            daemon=manager.daemon_status(),
            tasks=tasks,
            approvals=approvals,
            provider_actions=actions,
            provider_health=_provider_health_payload(),
            ecosystem=manager.ecosystem_state(),
            hidden_projects=load_hidden_projects(_dashboard_project_store()),
            hidden_tasks=load_hidden_tasks(_dashboard_task_store()),
            include_hidden=include_hidden,
            include_global_config=include_global_config,
        )

    @app.get("/api/daemon/status")
    def daemon_status() -> dict[str, object]:
        return manager.daemon_status()

    @app.get("/api/dashboard/overview")
    def dashboard_overview(workspace: str | None = None, include_hidden: bool = False, include_global_config: bool = True) -> dict[str, object]:
        root = Path(workspace or Path.cwd()).resolve()
        return _dashboard_payload(root, include_hidden=include_hidden, include_global_config=include_global_config)

    @app.delete("/api/dashboard/projects/{project_id}")
    def dashboard_hide_project(project_id: str, workspace: str | None = None) -> dict[str, object]:
        root = Path(workspace or Path.cwd()).resolve()
        overview = _dashboard_payload(root, include_hidden=True)
        project = next((item for item in overview.get("projects", []) if item.get("id") == project_id), None)
        return hide_dashboard_project(_dashboard_project_store(), project_id, project=project)

    @app.post("/api/dashboard/projects/{project_id}/restore")
    def dashboard_restore_project(project_id: str) -> dict[str, object]:
        return restore_dashboard_project(_dashboard_project_store(), project_id)

    @app.delete("/api/dashboard/tasks/{task_id}")
    def dashboard_hide_task(task_id: str, workspace: str | None = None) -> dict[str, object]:
        root = Path(workspace or Path.cwd()).resolve()
        overview = _dashboard_payload(root, include_hidden=True, include_global_config=False)
        task = _find_dashboard_task(overview, task_id)
        return hide_dashboard_task(_dashboard_task_store(), task_id, task=task)

    @app.post("/api/dashboard/tasks/{task_id}/restore")
    def dashboard_restore_task(task_id: str) -> dict[str, object]:
        return restore_dashboard_task(_dashboard_task_store(), task_id)

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

    @app.get("/api/validation/experiments")
    def validation_experiments(workspace: str | None = None) -> list[dict[str, object]]:
        root = Path(workspace or Path.cwd()).resolve()
        return list_validation_experiments(root)

    @app.get("/api/validation/experiments/{experiment_id}")
    def validation_experiment(experiment_id: str, workspace: str | None = None) -> dict[str, object]:
        root = Path(workspace or Path.cwd()).resolve()
        try:
            return load_validation_experiment(root, experiment_id).model_dump()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/skills/catalog")
    def skills_catalog(workspace: str | None = None) -> dict[str, object]:
        root = Path(workspace or Path.cwd()).resolve()
        return build_skill_catalog(root).to_dict()

    @app.get("/api/skills/lock")
    def skills_lock(workspace: str | None = None) -> dict[str, object]:
        return verify_skill_lock(Path(workspace or Path.cwd()).resolve())

    @app.post("/api/skills/lock")
    def skills_lock_write(workspace: str | None = None, memory: bool = True) -> dict[str, object]:
        return write_skill_lock(Path(workspace or Path.cwd()).resolve(), promote_memory=memory)

    @app.get("/api/skills/events")
    def skills_events(workspace: str | None = None) -> list[dict[str, object]]:
        return read_skill_events(Path(workspace or Path.cwd()).resolve())

    @app.get("/api/skills")
    def skills_list(workspace: str | None = None, include_disabled: bool = False) -> list[dict[str, object]]:
        return [skill.to_dict() for skill in scan_skills(Path(workspace or Path.cwd()).resolve(), include_disabled=include_disabled)]

    @app.get("/api/skills/scorecards")
    def skills_scorecards(workspace: str | None = None, last: str = "30d") -> list[dict[str, object]]:
        root = Path(workspace or Path.cwd()).resolve()
        return [score_skill(root, str(skill.get("name")), last=last) for skill in build_skill_catalog(root).to_dict().get("skills", []) if skill.get("name")]

    @app.get("/skills", response_class=HTMLResponse)
    def skills_page(workspace: str | None = None) -> str:
        root = Path(workspace or Path.cwd()).resolve()
        skills = build_skill_catalog(root).to_dict().get("skills", [])
        rows = "".join(f"<li><strong>{_escape(row.get('name'))}</strong> <span>{_escape(row.get('description', ''))}</span></li>" for row in skills)
        return f"<!doctype html><title>muxdev Skills</title><h1>muxdev Skills</h1><ul>{rows}</ul>"

    @app.get("/api/skills/{name}")
    def skills_detail(name: str, workspace: str | None = None) -> dict[str, object]:
        try:
            return skill_show(Path(workspace or Path.cwd()).resolve(), name)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/skills/{name}/trust")
    def skills_trust(name: str, trust: str, workspace: str | None = None) -> dict[str, object]:
        return set_skill_policy(Path(workspace or Path.cwd()).resolve(), name, trust=trust)

    @app.post("/api/skills/{name}/activate")
    def skills_activate(name: str, role: str | None = None, provider: str = "mock", workspace: str | None = None) -> dict[str, object]:
        return activate_skill(Path(workspace or Path.cwd()).resolve(), name, role=role, provider=provider).to_dict(include_content=True)

    @app.get("/api/skills/{name}/score")
    def skills_score(name: str, workspace: str | None = None, last: str = "30d") -> dict[str, object]:
        return score_skill(Path(workspace or Path.cwd()).resolve(), name, last=last)

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
        workspace = _feedback_workspace(request)
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

    def _feedback_workspace(request: FeedbackRequest) -> Path:
        if request.workspace:
            return Path(request.workspace).resolve()
        if request.run_id:
            try:
                run = manager.get_run(request.run_id)
                return Path(str(run["workspace"])).resolve()
            except (KeyError, FileNotFoundError):
                pass
        return Path.cwd().resolve()

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
    def provider_action_handled(action_id: str, request: ProviderActionResponseRequest | None = None) -> dict[str, object]:
        try:
            if request is not None:
                return manager.respond_provider_action(action_id, request.response_payload())
            return manager.update_provider_action(action_id, ProviderActionStatus.HANDLED)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/provider-actions/{action_id}/response")
    def provider_action_response(action_id: str, request: ProviderActionResponseRequest) -> dict[str, object]:
        try:
            return manager.respond_provider_action(action_id, request.response_payload())
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/tasks/{task_id}/actions/{action_id}/handled-and-continue")
    def provider_action_handled_and_continue(task_id: str, action_id: str, request: ProviderActionResponseRequest | None = None) -> dict[str, object]:
        try:
            handled = (
                manager.respond_provider_action(action_id, request.response_payload())
                if request is not None and (request.response is not None or request.choice is not None or request.text is not None)
                else manager.update_provider_action(action_id, ProviderActionStatus.HANDLED)
            )
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

    @app.post("/api/tasks/{task_id}/actions/{action_id}/respond-and-continue")
    def provider_action_respond_and_continue(task_id: str, action_id: str, request: ProviderActionResponseRequest) -> dict[str, object]:
        try:
            handled = manager.respond_provider_action(action_id, request.response_payload())
            continued = manager.continue_task(task_id, max_cost_usd=request.max_cost_usd)
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


def _find_dashboard_task(overview: dict[str, object], task_id: str) -> dict[str, object] | None:
    projects = overview.get("projects", [])
    for project in projects if isinstance(projects, list) else []:
        if not isinstance(project, dict):
            continue
        workflows = project.get("workflows", [])
        for workflow in workflows if isinstance(workflows, list) else []:
            if not isinstance(workflow, dict):
                continue
            role_groups = workflow.get("role_groups", [])
            for group in role_groups if isinstance(role_groups, list) else []:
                if not isinstance(group, dict):
                    continue
                tasks = group.get("tasks", [])
                for task in tasks if isinstance(tasks, list) else []:
                    if isinstance(task, dict) and str(task.get("task_id") or task.get("run_id") or "") == task_id:
                        return task
    return None


def render_live_dashboard_html(task_id: str | None = None) -> str:
    """Render the daemon-backed Mission Control dashboard."""
    initial_hash = f"data-task-id=\"{_escape(task_id)}\"" if task_id else ""
    return _LIVE_DASHBOARD_TEMPLATE.replace("__INITIAL_HASH__", initial_hash)


_LIVE_DASHBOARD_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>muxdev 控制台</title>
  <style>
    :root { --bg:#f6f7f9; --panel:#fff; --ink:#17202a; --muted:#64748b; --line:#d9e0ea; --accent:#0f766e; --warn:#a16207; --bad:#b91c1c; --good:#15803d; --soft:#f8fafc; }
    * { box-sizing:border-box; }
    body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.45 ui-sans-serif,system-ui,"Segoe UI",sans-serif; }
    header { padding:16px 24px; border-bottom:1px solid var(--line); background:var(--panel); display:flex; justify-content:space-between; gap:16px; align-items:center; }
    h1 { margin:0; font-size:22px; letter-spacing:0; } h2 { margin:0 0 10px; font-size:16px; } h3 { margin:0 0 8px; font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:0; }
    button { border:1px solid var(--line); background:#fff; color:var(--ink); border-radius:6px; padding:6px 10px; cursor:pointer; white-space:nowrap; }
    button.active, button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
    button.subtle { background:var(--soft); }
    code { background:#eef2f7; border:1px solid #d8dee8; border-radius:4px; padding:1px 5px; }
    textarea { width:100%; border:1px solid var(--line); border-radius:6px; padding:8px; resize:vertical; font:inherit; }
    main { padding:18px 24px 28px; display:grid; gap:14px; }
    .panel,.card { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; min-width:0; }
    .tabs,.subtabs,.actions,.chips { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
    .tab-panel { display:none; } .tab-panel.active { display:block; }
    .metrics { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:10px; }
    .metric strong { display:block; font-size:24px; }
    .project-shell { display:grid; grid-template-columns:minmax(240px,320px) minmax(0,1fr); gap:16px; align-items:start; }
    .project-sidebar { max-height:calc(100vh - 250px); overflow:auto; }
    .project-list { display:grid; gap:10px; }
    .project-item { width:100%; min-width:0; overflow:hidden; display:grid; grid-template-columns:minmax(0,1fr) auto; gap:8px; align-items:center; border:1px solid var(--line); border-radius:8px; padding:8px; background:#fff; }
    .project-item.active { border-color:var(--accent); box-shadow:inset 3px 0 0 var(--accent); }
    .project-select { border:0; padding:0; min-width:0; text-align:left; display:grid; gap:3px; background:transparent; }
    .project-name { font-weight:700; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .project-path { display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
    .meta { color:var(--muted); font-size:12px; overflow-wrap:anywhere; }
    .chips span { border:1px solid var(--line); border-radius:999px; padding:1px 7px; background:#fff; font-size:12px; }
    .board { display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:10px; }
    .column { min-width:0; background:var(--soft); border:1px solid var(--line); border-radius:8px; padding:10px; }
    .task-card { position:relative; border:1px solid var(--line); border-radius:8px; padding:10px; margin-top:8px; background:#fff; }
    .task-card.hidden { opacity:.7; }
    .hover-detail { display:none; margin-top:8px; }
    .task-card:hover .hover-detail,.task-card:focus-within .hover-detail { display:block; }
    .status { display:inline-flex; border:1px solid var(--line); border-radius:999px; padding:1px 7px; font-weight:700; }
    .completed { color:var(--good); background:#f0fdf4; border-color:#bbf7d0; } .running { color:var(--accent); background:#f0fdfa; border-color:#99f6e4; }
    .awaiting_approval,.awaiting_provider_action,.paused_budget { color:var(--warn); background:#fffbeb; border-color:#fde68a; } .blocked,.aborted,.failed { color:var(--bad); background:#fef2f2; border-color:#fecaca; }
    table { width:100%; border-collapse:collapse; table-layout:fixed; } th,td { border-bottom:1px solid #edf1f7; padding:7px 6px; text-align:left; vertical-align:top; overflow-wrap:anywhere; } th { color:var(--muted); font-size:12px; }
    .timeline-step,.action-card { border:1px solid var(--line); border-radius:8px; padding:10px; margin-bottom:8px; background:#fff; }
    .compact-action-center { padding:10px 12px; }
    .action-summary { display:grid; grid-template-columns:auto minmax(0,1fr) auto; gap:12px; align-items:center; }
    .action-count { min-width:34px; height:34px; display:grid; place-items:center; border-radius:8px; background:#fff7ed; color:#9a3412; border:1px solid #fed7aa; font-weight:800; }
    .action-summary.calm .action-count { background:#f0fdf4; color:var(--good); border-color:#bbf7d0; }
    .action-title { font-weight:800; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .action-list { margin-top:10px; display:grid; gap:8px; max-height:280px; overflow:auto; }
    .action-row { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:10px; align-items:start; border:1px solid var(--line); border-radius:8px; padding:10px; background:#fff; }
    .action-context { display:flex; gap:6px; flex-wrap:wrap; margin:4px 0; }
    .action-context span { border:1px solid var(--line); border-radius:999px; padding:1px 7px; background:var(--soft); font-size:12px; }
    .feedback-box { margin-top:8px; display:grid; gap:8px; }
    pre { background:#0f172a; color:#e2e8f0; border-radius:8px; padding:12px; overflow:auto; max-height:300px; }
    @media (max-width:900px){ header{align-items:flex-start;flex-direction:column;} .metrics{grid-template-columns:1fr 1fr;} .project-shell{grid-template-columns:1fr;} .action-summary,.action-row{grid-template-columns:1fr;} }
    @media (max-width:560px){ main{padding:14px;} .metrics{grid-template-columns:1fr;} }
  </style>
</head>
<body __INITIAL_HASH__>
  <header><div><h1>muxdev 控制台</h1><div class="meta">项目 | 工作流 | 角色 | 任务 | 全局配置</div></div><div class="meta" id="daemon-status">连接中</div></header>
  <main>
    <section class="tabs"><button id="tab-projects" class="active" onclick="setMainTab('projects')">项目</button><button id="tab-validation" onclick="setMainTab('validation')">验证</button><button id="tab-global" onclick="setMainTab('global')">全局配置</button></section>
    <section class="panel"><h2>当前状态</h2><div id="current-status" class="metrics"></div></section>
    <section class="panel compact-action-center"><h2>行动中心</h2><div id="action-center"></div></section>
    <section class="panel" style="display:none"><h2>产品体验</h2><code>/product/experience</code></section>
    <section class="panel" style="display:none"><h2>记忆上下文</h2><h2>角色会话</h2></section>
    <section class="panel" style="display:none"><h2>并行冲突</h2><h2>语义合并</h2><h2>Provider 学习</h2><h2>多仓编排</h2></section>
    <section id="panel-projects" class="tab-panel active"><div class="project-shell"><aside class="panel project-sidebar"><h2>项目</h2><div class="meta">当前任务目录归属为项目。</div><div id="project-list" class="project-list"></div></aside><section class="panel"><div style="display:flex;justify-content:space-between;gap:12px;align-items:start"><div><h2 id="project-title">项目</h2><div id="project-path" class="meta"></div></div><div class="subtabs"><button id="subtab-workflows" class="active" onclick="setProjectSubtab('workflows')">工作流</button><button id="subtab-tasks" onclick="setProjectSubtab('tasks')">任务</button><button id="subtab-activity" onclick="setProjectSubtab('activity')">动态</button><button id="subtab-artifacts" onclick="setProjectSubtab('artifacts')">产物</button><button id="subtab-config" onclick="setProjectSubtab('config')">配置</button></div></div><hr><div id="project-workflows"></div><div id="project-tasks" style="display:none"></div><div id="project-activity" style="display:none"><h2>任务时间线</h2><div id="timeline"></div><h2>Provider 操作</h2><div id="provider-actions"></div><h2>审批风险</h2><div id="approvals"></div><h2>最近事件</h2><pre id="events"></pre></div><div id="project-artifacts" style="display:none"><h2>证据 / 产物中心</h2><div id="artifacts-center"></div></div><div id="project-config" style="display:none"></div></section></div></section>
    <section id="panel-validation" class="tab-panel"><section class="panel"><h2>验证实验</h2><div class="meta">对比单 Agent 与多 Agent 运行的证据、可靠性、成本和可观测性。</div><div id="validation-experiments"></div></section></section>
    <section id="panel-global" class="tab-panel"><section class="panel"><h2>MCP</h2><div id="mcp-summary"></div></section><section class="panel"><h2>角色模板</h2><div id="role-templates"></div></section><section class="panel"><h2>工作流模板</h2><div id="workflow-templates"></div></section><section class="panel"><h2>技能</h2><div id="skills-catalog"></div></section></section>
  </main>
<script>
const state={taskId:document.body.dataset.taskId||null,selectedProjectId:null,mainTab:'projects',projectSubtab:'workflows',events:[],globalConfigLoaded:false,actionExpanded:false,actionRows:[]};
const esc=v=>String(v??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const fmt=v=>Array.isArray(v)||(v&&typeof v==='object')?JSON.stringify(v):v;
const statusLabels={completed:'已完成',running:'运行中',awaiting_approval:'待审批',awaiting_provider_action:'待处理',paused_budget:'预算暂停',blocked:'已阻塞',aborted:'已终止',failed:'失败',created:'已创建',queued:'排队中',pending:'等待中'};
const actionLabels={provider_action:'Provider 等待操作',approval:'需要审批',recovery:'任务需要恢复',plan_feedback:'计划可反馈'};
const statusClass=v=>`status ${v||''}`;
const statusText=v=>statusLabels[v]||v||'-';
async function api(path,options){const r=await fetch('/api'+path,options);if(!r.ok)throw new Error(await r.text());return r.json();}
async function optionalApi(path,fallback){try{return await api(path)}catch(_e){return fallback}}
function setMainTab(tab){state.mainTab=tab;document.getElementById('tab-projects').classList.toggle('active',tab==='projects');document.getElementById('tab-validation').classList.toggle('active',tab==='validation');document.getElementById('tab-global').classList.toggle('active',tab==='global');document.getElementById('panel-projects').classList.toggle('active',tab==='projects');document.getElementById('panel-validation').classList.toggle('active',tab==='validation');document.getElementById('panel-global').classList.toggle('active',tab==='global');if(tab==='global'&&!state.globalConfigLoaded)loadGlobalConfig().catch(e=>document.getElementById('daemon-status').textContent=e.message);}
function setProjectSubtab(tab,load=true){state.projectSubtab=tab;for(const name of ['workflows','tasks','activity','artifacts','config']){document.getElementById('subtab-'+name).classList.toggle('active',tab===name);document.getElementById('project-'+name).style.display=tab===name?'':'none';}if(load&&needsTaskDetail())loadSelectedTask().catch(e=>document.getElementById('daemon-status').textContent=e.message);}
const needsTaskDetail=()=>state.taskId&&(state.projectSubtab==='activity'||state.projectSubtab==='artifacts');
async function refresh(){const overview=await api('/dashboard/overview?include_global_config=false');document.getElementById('daemon-status').textContent=`任务=${overview.counts?.tasks??0} 运行=${overview.counts?.active??0} 待处理=${overview.counts?.needs_attention??0}`;const ids=(overview.projects||[]).map(p=>p.id);if(!state.selectedProjectId||!ids.includes(state.selectedProjectId)){state.selectedProjectId=overview.selected_project_id||ids[0]||null;state.taskId=null;}const selected=(overview.projects||[]).find(p=>p.id===state.selectedProjectId)||(overview.projects||[])[0];if(selected&&!state.taskId){const first=firstProjectTask(selected);if(first)state.taskId=first.task_id;}renderCurrentStatus(overview.current_status||{},overview.counts||{});renderActionCenter(overview.action_center||[]);renderValidation(overview.validation||{});renderProjects(overview.projects||[],selected);renderProviderActions(overview.pending_provider_actions||[]);renderApprovals(overview.pending_approvals||[]);if(needsTaskDetail()){await loadSelectedTask(overview.artifact_center||{});}else if(!state.taskId){renderSelectedTask(null,overview.artifact_center||{});}setMainTab(state.mainTab);setProjectSubtab(state.projectSubtab,false);}
async function loadGlobalConfig(){const overview=await api('/dashboard/overview?include_global_config=true');renderGlobalConfig(overview.global_config||{});state.globalConfigLoaded=true;}
async function loadSelectedTask(center={}){if(!state.taskId){renderSelectedTask(null,center);return;}const detail=await optionalApi('/tasks/'+encodeURIComponent(state.taskId),null);renderSelectedTask(detail,center);}
function firstProjectTask(project){for(const workflow of project.workflows||[])for(const group of workflow.role_groups||[])if((group.tasks||[]).length)return group.tasks[0];return null;}
function renderCurrentStatus(status,counts){const rows=[['运行中',status.running??counts.active??0,'正在执行的任务'],['等待中',(status.waiting_provider_action??0)+(status.waiting_muxdev_approval??0),'操作和审批'],['阻塞',status.stuck??0,'失败或中断'],['项目',counts.projects??'-','工作区分组'],['完成',(status.recent_completed||[]).length,'近期交付']];document.getElementById('current-status').innerHTML=rows.map(([a,b,c])=>`<div class="card metric"><h3>${esc(a)}</h3><strong>${esc(b)}</strong><span class="meta">${esc(c)}</span></div>`).join('');}
function renderActionCenter(rows){state.actionRows=rows||[];const el=document.getElementById('action-center');if(!state.actionRows.length){el.innerHTML='<div class="action-summary calm"><div class="action-count">0</div><div><div class="action-title">暂无需要处理的事项</div><div class="meta">任务暂停、审批或恢复请求会出现在这里。</div></div></div>';return;}const first=state.actionRows[0];const details=state.actionExpanded?`<div class="action-list">${state.actionRows.map(actionRow).join('')}</div>`:'';el.innerHTML=`<div class="action-summary"><div class="action-count">${esc(state.actionRows.length)}</div><div><div class="action-title">${esc(state.actionRows.length)} 项需要处理：${esc(actionLabels[first.kind]||first.headline||'待处理')}</div><div class="meta">${actionContext(first)}</div></div><button class="subtle" onclick="toggleActionCenter()">${state.actionExpanded?'收起详情':'展开详情'}</button></div>${details}`;}
function toggleActionCenter(){state.actionExpanded=!state.actionExpanded;renderActionCenter(state.actionRows);}
function actionContext(r){const parts=[];if(r.project_name)parts.push(`项目：${r.project_name}`);if(r.task_title)parts.push(`任务：${r.task_title}`);if(r.run_id)parts.push(`run：${r.run_id}`);if(r.stage_id)parts.push(`阶段：${r.stage_id}`);return esc(parts.join(' / ')||r.why||'');}
function actionRow(r){const label=actionLabels[r.kind]||r.headline||'待处理';const primary=r.endpoint&&r.kind!=='plan_feedback'?`<button class="primary" onclick="post('${esc(r.endpoint).replace('/api','')}')">${actionButtonLabel(r.kind)}</button>`:'';const report=r.secondary_endpoint?`<button onclick="loadText('${esc(r.secondary_endpoint).replace('/api','')}','events')">报告</button>`:'';const rollback=r.rollback_endpoint?`<button onclick="post('${esc(r.rollback_endpoint).replace('/api','')}')">回滚</button>`:'';const hide=r.task_id?`<button onclick="hideTask(event,'${esc(r.task_id)}')">隐藏</button>`:'';return `<div class="action-row"><div><strong>${esc(label)}</strong><div class="action-context"><span title="${esc(r.project_path||'')}">项目：${esc(r.project_name||'-')}</span><span>任务：${esc(r.task_title||r.task_id||'-')}</span><span>run：${esc(r.run_id||'-')}</span>${r.stage_id?`<span>阶段：${esc(r.stage_id)}</span>`:''}</div><div class="meta">${esc(r.why||'')}</div>${r.command?`<code>${esc(r.command)}</code>`:''}${r.kind==='plan_feedback'?planFeedbackForm(r):''}</div><div class="actions">${primary}${report}${rollback}${hide}</div></div>`;}
function actionButtonLabel(kind){return kind==='recovery'?'恢复':kind==='approval'?'批准':'继续';}
function renderValidation(validation){const rows=validation.experiments||[];document.getElementById('validation-experiments').innerHTML=rows.length?tableBlock('最近实验',rows,['experiment_id','suite','winner','strategies','report','updated_at']):'<div class="meta">暂无验证实验。可运行 <code>muxdev validate run validation/suites/example.yaml</code>。</div>';}
function planFeedbackForm(r){const id='feedback-'+String(r.run_id||'latest').replace(/[^a-zA-Z0-9_-]/g,'-');return `<div class="feedback-box"><textarea id="${esc(id)}" rows="3" placeholder="补充约束、修正或偏好"></textarea><div class="actions"><button class="primary" onclick="submitPlanFeedback('${esc(r.run_id||'')}','${esc(id)}')">提交反馈</button></div></div>`;}
async function submitPlanFeedback(runId,fieldId){const field=document.getElementById(fieldId);const content=(field?.value||'').trim();if(!content){document.getElementById('daemon-status').textContent='反馈为空';return;}await api('/feedback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:'manual_feedback',source:'dashboard',content,run_id:runId||null,workspace:document.body.dataset.workspace||null,auto_submit:false})});field.value='';document.getElementById('daemon-status').textContent='反馈已记录';await refresh();}
function renderProjects(projects,selected){document.getElementById('project-list').innerHTML=projects.length?projects.map(p=>`<div class="project-item ${p.id===(selected||{}).id?'active':''}"><button class="project-select" title="${esc(p.path)}" onclick="state.selectedProjectId='${esc(p.id)}';state.taskId=null;refresh()"><span class="project-name">${esc(p.name)}</span><span class="project-path meta">${esc(p.path)}</span><span class="chips"><span>${esc(p.summary?.tasks??0)} 个任务</span><span>${esc(p.summary?.active??0)} 活跃</span><span>$${esc(p.summary?.cost_usd??0)}</span></span></button><button class="project-hide" title="从控制台隐藏项目" onclick="hideProject(event,'${esc(p.id)}')">隐藏</button></div>`).join(''):'<div class="meta">没有可见项目。隐藏项目可通过 include_hidden=true 的 API 恢复。</div>';if(!selected){document.getElementById('project-title').textContent='项目';document.getElementById('project-path').textContent='';document.getElementById('project-workflows').innerHTML='<div class="meta">未选择项目。</div>';document.getElementById('project-tasks').innerHTML='';document.getElementById('timeline').innerHTML='<div class="meta">未选择任务。</div>';document.getElementById('artifacts-center').innerHTML='<div class="meta">暂无产物。</div>';document.getElementById('project-config').innerHTML='';return;}document.getElementById('project-title').textContent=selected.name;document.getElementById('project-path').textContent=selected.path;renderProjectWorkflows(selected);renderProjectTasks(selected);renderProjectConfig(selected.config||{});}
function renderProjectWorkflows(project){document.getElementById('project-workflows').innerHTML='<h2>任务看板</h2>'+((project.workflows||[]).map(w=>`<section><h2>${esc(w.name||w.id)}</h2><div class="meta">${esc(w.stage_count)} 个阶段 | ${esc(w.task_count)} 个任务</div><div class="board">${(w.role_groups||[]).map(g=>`<div class="column"><h3>${esc(g.role)} <span>${(g.tasks||[]).length}</span></h3>${(g.tasks||[]).map(taskCard).join('')||'<div class="meta">暂无任务。</div>'}</div>`).join('')}</div></section>`).join('')||'<div class="meta">暂无工作流。</div>');}
function renderProjectTasks(project){const tasks=[];(project.workflows||[]).forEach(w=>(w.role_groups||[]).forEach(g=>(g.tasks||[]).forEach(t=>tasks.push(t))));document.getElementById('project-tasks').innerHTML=tasks.length?tasks.map(taskCard).join(''):'<div class="meta">暂无任务。</div>';}
function fmtDuration(s){s=Number(s||0);if(!s)return '-';const h=Math.floor(s/3600),m=Math.floor((s%3600)/60),sec=Math.floor(s%60);if(h)return `${h}h ${m}m`;if(m)return `${m}m ${sec}s`;return `${sec}s`;}
function taskCard(t){const err=t.error_summary||{};const reason=err.message||err.type||'';return `<div class="task-card ${t.hidden?'hidden':''}" tabindex="0" onclick="state.taskId='${esc(t.task_id)}';refresh()"><strong>${esc(t.title||t.task_id)}</strong> <span class="${statusClass(t.status)}">${esc(statusText(t.status))}</span>${t.hidden?'<span class="status">已隐藏</span>':''}<div class="meta">${esc(t.provider||'-')} | 阶段=${esc(t.current_stage||'-')} | 工作流=${esc(t.workflow||'-')} | 用时=${fmtDuration(t.elapsed_seconds)}</div>${t.current_activity?`<div class="meta">${esc(t.current_activity)}</div>`:''}<div class="hover-detail"><div class="meta">阶段用时=${fmtDuration(t.current_stage_elapsed_seconds)} 成本=${esc(t.cost_usd||0)} tokens=${esc(t.tokens||0)} 审批=${esc(t.pending_approvals||0)} 操作=${esc(t.pending_provider_actions||0)}</div>${reason?`<div class="meta">错误=${esc(err.stage_id||'run')} ${esc(err.type||'')}: ${esc(err.message||'')}</div>`:''}<div class="actions">${t.recover_endpoint?`<button class="primary" onclick="post('${esc(t.recover_endpoint).replace('/api','')}');event.stopPropagation()">恢复</button>`:''}<button onclick="loadText('${esc(t.report_endpoint||'').replace('/api','')}','events');event.stopPropagation()">报告</button><button onclick="loadText('${esc(t.diff_endpoint||'').replace('/api','')}','events');event.stopPropagation()">Diff</button><button onclick="hideTask(event,'${esc(t.task_id)}')">隐藏</button></div></div></div>`;}
function renderProjectConfig(config){document.getElementById('project-config').innerHTML=`<div class="metrics"><div class="card"><h3>运行档</h3><strong>${esc(config.profile||'-')}</strong><div class="meta">gate=${esc(config.gate||'-')}</div></div><div class="card"><h3>审批</h3><strong>${esc(config.approvals?.pending||0)}</strong><div class="meta">项目待审批门禁</div></div></div>${tableBlock('角色',Object.entries(config.roles||{}).map(([role,value])=>({role,value})),['role','value'])}${tableBlock('技能',config.skills||[],['name','source','trust'])}`;}
function renderGlobalConfig(config){renderMcpSummary(config.mcp||{});document.getElementById('role-templates').innerHTML=tableBlock('角色模板',config.role_templates||[],['name','workflow','roles','providers']);const templates=config.workflow_templates||{};document.getElementById('workflow-templates').innerHTML=tableBlock('工作流模板',templates.templates||[],['name','description','phases','supported_providers']);const skills=config.skills_catalog||{};document.getElementById('skills-catalog').innerHTML=tableBlock('技能目录',(skills.catalog||{}).skills||[],['name','trust','risk_level','source','description'])+tableBlock('技能锁',(skills.lock||{}).skills||[],['name','status']);}
function renderMcpSummary(mcp){const rows=[['状态',mcp.status||'enabled'],['模式',mcp.mode||'local stdio'],['工具',mcp.tools_count??0],['资源',mcp.resources_count??0],['提示词',mcp.prompts_count??0],['写入策略',mcp.write_policy||'guarded'],['护栏',(mcp.recent_guardrails||[]).length],['近期拒绝',mcp.recent_denials??0]];const commands=mcp.commands||{};document.getElementById('mcp-summary').innerHTML=`<div class="metrics">${rows.map(([a,b])=>`<div class="card metric"><h3>${esc(a)}</h3><strong>${esc(b)}</strong></div>`).join('')}</div><div class="actions"><button onclick="copyText('${esc(commands.manifest||'muxdev mcp manifest --json')}')">复制 Manifest</button><button onclick="copyText('${esc(commands.doctor||'muxdev mcp doctor --json')}')">复制 Doctor</button></div>${(mcp.recent_guardrails||[]).length?tableBlock('近期护栏',mcp.recent_guardrails||[],['tool','decision','reason','created_at']):'<div class="meta">暂无近期 MCP 护栏。</div>'}`;}
function renderSelectedTask(payload,center){if(!payload){document.getElementById('timeline').innerHTML='<div class="meta">未选择任务。</div>';document.getElementById('artifacts-center').innerHTML='<div class="meta">暂无产物。</div>';return;}const stages=payload.stages||[];document.getElementById('timeline').innerHTML=stages.length?stages.map(s=>`<div class="timeline-step"><strong>${esc(s.stage_id)}</strong> <span class="${statusClass(s.status)}">${esc(statusText(s.status))}</span><div>${esc(s.summary||'')}</div></div>`).join(''):'<div class="meta">暂无阶段时间线。</div>';document.getElementById('artifacts-center').innerHTML=tableBlock('最终报告 / 产物',payload.artifacts||[],['name','kind','stage_id','path','created_at'])+tableBlock('测试输出',payload.test_results||[],['stage_id','passed','command','summary'])+tableBlock('证据评估',payload.evidence_evaluations||[],['run_id','label','confidence','path']);}
function renderApprovals(rows){document.getElementById('approvals').innerHTML=rows.length?rows.map(r=>`<div class="action-card"><h3>需要审批</h3><strong>${esc(r.type||'policy gate')}</strong><div>${esc(r.reason||'')}</div><div class="actions"><button class="primary" onclick="post('/approvals/${encodeURIComponent(r.approval_id)}/approve')">批准</button><button onclick="post('/approvals/${encodeURIComponent(r.approval_id)}/deny')">拒绝</button></div></div>`).join(''):'<div class="meta">暂无待审批项。</div>';}
function renderProviderActions(rows){document.getElementById('provider-actions').innerHTML=rows.length?rows.map(r=>{const choices=r.choices||r.options||[];const field='action-response-'+String(r.action_id||'').replace(/[^a-zA-Z0-9_-]/g,'-');const choiceButtons=choices.map(c=>{const payload=encodeURIComponent(JSON.stringify({choice:String(c.value??c.label??'')}));return `<button onclick="respondProviderAction('${esc(r.run_id)}','${esc(r.action_id)}','${esc(payload)}')">${esc(c.label??c.value)}</button>`;}).join('');const textBox=(r.input_kind==='text'||!choices.length&&r.input_kind!=='external'&&r.input_kind!=='confirmation')?`<textarea id="${esc(field)}" rows="2" placeholder="请输入响应"></textarea><button onclick="respondProviderActionText('${esc(r.run_id)}','${esc(r.action_id)}','${esc(field)}')">提交响应</button>`:'';return `<div class="action-card"><h3>Provider 需要操作</h3><strong>${esc(r.provider||'provider')} / ${esc(r.input_kind||r.kind||'action')}</strong><p>${esc(r.prompt_text||'')}</p><div class="meta">默认=${esc(r.default_choice||'-')} 自动策略=${esc(r.auto_policy||'manual')}</div><div class="actions">${choiceButtons}${textBox}<button onclick="copyText('${esc(r.attach_command||'')}')">复制 attach 命令</button><button class="primary" onclick="post('/tasks/${encodeURIComponent(r.run_id)}/actions/${encodeURIComponent(r.action_id)}/handled-and-continue')">已处理并继续</button></div></div>`;}).join(''):'<div class="meta">暂无待处理 Provider 操作。</div>';}
function tableBlock(title,rows,cols){rows=rows||[];if(!rows.length)return `<h3>${esc(title)}</h3><div class="meta">暂无记录。</div>`;return `<h3>${esc(title)}</h3><table><thead><tr>${cols.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${cols.map(c=>tableCell(r,c)).join('')}</tr>`).join('')}</tbody></table>`;}
function tableCell(r,c){const v=c==='path'&&r.path_display?r.path_display:r[c];const title=c==='path'&&r.path_title?` title="${esc(r.path_title)}"`:'';return `<td${title}>${esc(fmt(v))}</td>`;}
async function respondProviderAction(runId,actionId,encoded){const response=JSON.parse(decodeURIComponent(encoded));await api('/tasks/'+encodeURIComponent(runId)+'/actions/'+encodeURIComponent(actionId)+'/respond-and-continue',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({response})});await refresh();}
async function respondProviderActionText(runId,actionId,fieldId){const text=(document.getElementById(fieldId)?.value||'').trim();if(!text){document.getElementById('daemon-status').textContent='响应为空';return;}await api('/tasks/'+encodeURIComponent(runId)+'/actions/'+encodeURIComponent(actionId)+'/respond-and-continue',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({response:{text}})});await refresh();}
async function post(path){await api(path,{method:'POST'});await refresh();}
async function hideProject(event,id){event.stopPropagation();if(!confirm('从控制台隐藏这个项目？不会删除工作区、运行记录、证据或文件。'))return;await api('/dashboard/projects/'+encodeURIComponent(id),{method:'DELETE'});if(state.selectedProjectId===id){state.selectedProjectId=null;state.taskId=null;}await refresh();}
async function hideTask(event,id){event.stopPropagation();if(!confirm('隐藏这个任务提醒？不会删除运行记录、证据或文件。'))return;await api('/dashboard/tasks/'+encodeURIComponent(id),{method:'DELETE'});if(state.taskId===id)state.taskId=null;await refresh();}
async function loadText(path,target){const payload=await api(path);document.getElementById(target==='events'?'events':target).textContent=payload[target]||payload.content||payload.diff||JSON.stringify(payload,null,2);}
async function copyText(v){if(navigator.clipboard&&v)await navigator.clipboard.writeText(v);}
function connectEvents(){const socket=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/events`);socket.onmessage=e=>{state.events.push(e.data);state.events=state.events.slice(-20);document.getElementById('events').textContent=state.events.join(String.fromCharCode(10));refresh().catch(()=>{});};socket.onclose=()=>setTimeout(connectEvents,2000);}
refresh().catch(e=>document.getElementById('daemon-status').textContent=e.message);connectEvents();setInterval(()=>refresh().catch(()=>{}),5000);
</script>
</body>
</html>"""
