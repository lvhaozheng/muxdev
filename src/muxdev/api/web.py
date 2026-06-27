"""Static task dashboard writer and lightweight web serving helpers."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .minimal_dashboard import render_minimal_dashboard_html as render_workbench_dashboard_html
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


def render_run_review_html(payload: dict[str, Any]) -> str:
    """Render a sanitized, shareable review page for one run."""
    run = payload.get("run") if isinstance(payload.get("run"), dict) else {}
    ux = payload.get("ux") if isinstance(payload.get("ux"), dict) else {}
    evaluation = payload.get("evidence_evaluation") if isinstance(payload.get("evidence_evaluation"), dict) else {}
    if not evaluation:
        evaluations = payload.get("evidence_evaluations") if isinstance(payload.get("evidence_evaluations"), list) else []
        evaluation = evaluations[0] if evaluations and isinstance(evaluations[0], dict) else {}
    safe_artifacts = [
        row
        for row in payload.get("artifacts", [])
        if isinstance(row, dict) and str(row.get("kind") or "") not in {"provider_transcript", "session_transcript", "raw_transcript", "provider_raw_output"}
    ]
    next_actions = ux.get("next_actions") if isinstance(ux.get("next_actions"), list) else []
    actions = "".join(
        f"<li><strong>{_escape(action.get('label') or action.get('kind') or 'action')}</strong>"
        + (f"<br><span class=\"muted\">{_escape(action.get('endpoint'))}</span>" if action.get("endpoint") else "")
        + (f"<br><code>{_escape(action.get('command'))}</code>" if action.get("command") else "")
        + "</li>"
        for action in next_actions[:5]
        if isinstance(action, dict)
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>muxdev Shareable Run Review</title>
  <style>
    :root {{ --bg:#f6f7f9; --panel:#fff; --ink:#17202a; --muted:#64748b; --line:#d9e0ea; --accent:#0f766e; --warn:#a16207; --bad:#b91c1c; --good:#15803d; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--ink); font:14px/1.45 ui-sans-serif,system-ui,"Segoe UI",sans-serif; }}
    header {{ padding:18px 24px; background:#fff; border-bottom:1px solid var(--line); }}
    main {{ padding:18px 24px 28px; display:grid; gap:14px; grid-template-columns:repeat(12,minmax(0,1fr)); }}
    h1 {{ margin:0; font-size:22px; letter-spacing:0; }} h2 {{ margin:0 0 10px; font-size:16px; }}
    .panel {{ grid-column:span 12; background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; min-width:0; }}
    .span-6 {{ grid-column:span 6; }}
    .meta,.muted {{ color:var(--muted); font-size:12px; overflow-wrap:anywhere; }}
    .status {{ display:inline-flex; border:1px solid var(--line); border-radius:999px; padding:2px 8px; font-weight:700; }}
    .completed,.trusted {{ color:var(--good); background:#f0fdf4; border-color:#bbf7d0; }}
    .running,.reviewable,.collecting {{ color:var(--accent); background:#f0fdfa; border-color:#99f6e4; }}
    .blocked,.aborted,.failed,.risky {{ color:var(--bad); background:#fef2f2; border-color:#fecaca; }}
    .chips {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; }}
    .chips span {{ border:1px solid var(--line); border-radius:999px; padding:1px 7px; background:#fff; font-size:12px; }}
    table {{ width:100%; border-collapse:collapse; table-layout:fixed; }} th,td {{ border-bottom:1px solid #edf1f7; padding:7px 6px; text-align:left; vertical-align:top; overflow-wrap:anywhere; }} th {{ color:var(--muted); font-size:12px; }}
    code {{ background:#eef2f7; border:1px solid #d8dee8; border-radius:4px; padding:1px 5px; }}
    @media (max-width:900px) {{ main {{ grid-template-columns:1fr; padding:14px; }} .span-6 {{ grid-column:span 1; }} header {{ padding:16px 14px; }} }}
  </style>
</head>
<body>
  <header>
    <h1>muxdev Shareable Run Review <span class="status {_escape(str(run.get('status') or 'ready'))}">{_escape(run.get('status') or '-')}</span></h1>
    <div class="meta">Sensitive transcript hidden. Provider raw output, websocket trace, and local session logs are intentionally excluded.</div>
  </header>
  <main>
    <section class="panel">
      <h2>Delivery Summary</h2>
      <strong>{_escape(ux.get('headline') or run.get('task') or run.get('run_id') or 'muxdev task')}</strong>
      <div>{_escape(run.get('task') or '')}</div>
      <div class="meta">{_escape(ux.get('why') or '')}</div>
      <div class="chips">
        <span>run {_escape(run.get('run_id') or '-')}</span>
        <span>workflow {_escape(run.get('workflow') or '-')}</span>
        <span>provider {_escape(run.get('provider') or '-')}</span>
        <span>stage {_escape(ux.get('current_stage') or run.get('current_stage') or '-')}</span>
      </div>
    </section>
    <section class="panel span-6">
      <h2>Delivery Confidence</h2>
      <p><span class="status {_escape(str(evaluation.get('label') or 'collecting'))}">{_escape(evaluation.get('label') or 'collecting')} {_number(float(evaluation.get('confidence') or 0) * 100)}%</span></p>
      <div class="meta">{_escape(' / '.join(str(reason) for reason in (evaluation.get('reasons') or [])[:4]))}</div>
      {_table([{"missing": ", ".join(str(item) for item in evaluation.get("missing_evidence", []) or []), "path": evaluation.get("path") or ""}], ["missing", "path"])}
    </section>
    <section class="panel span-6">
      <h2>Review Actions</h2>
      <ul>{actions or '<li class="muted">No open review action.</li>'}</ul>
    </section>
    <section class="panel span-6">
      <h2>Tests</h2>
      {_table(payload.get("test_results", []), ["stage_id", "passed", "command", "summary"])}
    </section>
    <section class="panel span-6">
      <h2>Review Blockers</h2>
      {_table(payload.get("review_blockers", []), ["stage_id", "type", "severity", "file", "line", "suggestion"])}
    </section>
    <section class="panel span-6">
      <h2>Evidence Center</h2>
      {_table(safe_artifacts, ["name", "kind", "stage_id", "path", "created_at"])}
    </section>
    <section class="panel span-6">
      <h2>Semantic Merge</h2>
      {_table(payload.get("semantic_merge_reviews", []), ["review_id", "decision", "patch_hash", "findings", "path"])}
    </section>
  </main>
</body>
</html>"""


def render_task_terminal_html(payload: dict[str, Any], handoff: dict[str, Any], *, agent: str = "implementer") -> str:
    """Render a read-only browser view of a task's CLI transcript or trace."""
    run = payload.get("run") if isinstance(payload.get("run"), dict) else {}
    run_id = str(run.get("run_id") or handoff.get("run_id") or handoff.get("task_id") or "")
    task = str(run.get("task") or run_id or "muxdev task")
    handoff_payload = handoff.get("handoff") if isinstance(handoff.get("handoff"), dict) else {}
    command = _terminal_command_text(handoff_payload.get("command"))
    mode = str(handoff_payload.get("mode") or "trace")
    session = str(handoff_payload.get("session") or "")
    fallback_reason = str(handoff_payload.get("fallback_reason") or "")
    transcript_path = str(handoff_payload.get("path") or handoff_payload.get("transcript_path") or "")
    transcript = _read_terminal_tail(transcript_path)
    source = transcript_path if transcript else "trace.jsonl"
    if not transcript:
        trace = payload.get("trace") if isinstance(payload.get("trace"), list) else []
        transcript = _trace_tail(trace)
    if not transcript:
        transcript = "No transcript or trace output is available yet."
    live_mode = mode in {"native_cli", "tmux"}
    title = "Live model CLI" if live_mode else "Transcript fallback"
    command_label = "Attach command" if live_mode else "Transcript command"
    command_block = f"<code>{_escape(command)}</code>" if command else "<span class=\"muted\">No attach command is available.</span>"
    session_block = f"<span class=\"pill\">session {_escape(session)}</span>" if session else ""
    fallback_block = f"<span class=\"pill\">{_escape(fallback_reason)}</span>" if fallback_reason else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="3">
  <title>{_escape(title)}</title>
  <style>
    :root {{ --bg:#0e1116; --panel:#151a22; --ink:#e5e7eb; --muted:#9ca3af; --line:#2d3440; --accent:#2dd4bf; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--ink); font:14px/1.45 ui-sans-serif,system-ui,"Segoe UI",sans-serif; }}
    header {{ padding:16px 20px; background:var(--panel); border-bottom:1px solid var(--line); position:sticky; top:0; }}
    h1 {{ margin:0; font-size:18px; letter-spacing:0; }}
    main {{ padding:16px 20px 28px; display:grid; gap:12px; }}
    .meta,.muted {{ color:var(--muted); overflow-wrap:anywhere; }}
    .panel {{ border:1px solid var(--line); border-radius:8px; background:var(--panel); padding:12px; }}
    .toolbar {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:8px; }}
    .pill {{ border:1px solid var(--line); border-radius:999px; padding:2px 8px; color:var(--accent); }}
    code {{ background:#0b0f14; border:1px solid var(--line); border-radius:5px; padding:2px 6px; overflow-wrap:anywhere; }}
    pre {{ margin:0; white-space:pre-wrap; overflow-wrap:anywhere; font:13px/1.45 ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",monospace; }}
    a {{ color:var(--accent); }}
  </style>
</head>
<body>
  <header>
    <h1>{_escape(title)}</h1>
    <div class="meta">{_escape(task)}</div>
    <div class="toolbar">
      <span class="pill">run {_escape(run_id)}</span>
      <span class="pill">agent {_escape(agent)}</span>
      <span class="pill">mode {_escape(mode)}</span>
      {session_block}
      {fallback_block}
      <a href="/tasks/{_escape(run_id)}">dashboard</a>
    </div>
  </header>
  <main>
    <section class="panel">
      <div class="meta">{_escape(command_label)}</div>
      {command_block}
    </section>
    <section class="panel">
      <div class="meta">Model execution output from {_escape(source)}</div>
      <pre>{_escape(transcript)}</pre>
    </section>
  </main>
</body>
</html>"""


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


def _terminal_command_text(command: object) -> str:
    if isinstance(command, list):
        return " ".join(str(part) for part in command)
    if isinstance(command, tuple):
        return " ".join(str(part) for part in command)
    return str(command or "")


def _read_terminal_tail(path: str, *, max_bytes: int = 96_000) -> str:
    if not path:
        return ""
    try:
        transcript = Path(path)
        if not transcript.exists() or not transcript.is_file():
            return ""
        with transcript.open("rb") as handle:
            size = transcript.stat().st_size
            if size > max_bytes:
                handle.seek(size - max_bytes)
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _trace_tail(rows: object, *, limit: int = 120) -> str:
    if not isinstance(rows, list):
        return ""
    tail = rows[-limit:]
    return "\n".join(json.dumps(row, ensure_ascii=False, default=str) for row in tail if isinstance(row, dict))


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


class ApprovalFeedbackRequest(BaseModel):
    feedback: str
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
    def dashboard(lang: str | None = None) -> str:
        return render_live_dashboard_html(lang=lang)

    @app.get("/tasks/{task_id}", response_class=HTMLResponse)
    def task_page(task_id: str, lang: str | None = None) -> str:
        return render_live_dashboard_html(task_id=task_id, lang=lang)

    @app.get("/tasks/{task_id}/terminal", response_class=HTMLResponse)
    def task_terminal_page(task_id: str, agent: str = "implementer") -> str:
        try:
            detail = manager.task_detail(task_id)
            handoff = manager.attach_command(task_id, agent=agent)
            return render_task_terminal_html(detail, handoff, agent=agent)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/review/{task_id}", response_class=HTMLResponse)
    def task_review_page(task_id: str) -> str:
        try:
            return render_run_review_html(manager.task_detail(task_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

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
            memory_governance=_memory_governance_payload(root),
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

    @app.post("/api/approvals/{approval_id}/feedback")
    def approval_feedback(approval_id: str, request: ApprovalFeedbackRequest) -> dict[str, object]:
        try:
            return manager.plan_feedback(approval_id, request.feedback)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/approvals/{approval_id}/feedback-and-continue")
    def approval_feedback_and_continue(approval_id: str, request: ApprovalFeedbackRequest) -> dict[str, object]:
        try:
            feedback = manager.plan_feedback(approval_id, request.feedback)
            continued = manager.continue_task(str(feedback.get("run_id")), max_cost_usd=request.max_cost_usd)
            return {
                "approval": feedback,
                "continue": continued,
                "status": continued.get("status"),
            }
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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


def _memory_governance_payload(workspace: Path) -> dict[str, object]:
    try:
        with MemoryStore(workspace) as store:
            inbox = store.inbox(limit=8)
            status = store.status()
    except Exception as exc:  # pragma: no cover - dashboard should tolerate memory DB migration issues
        return {"status": "blocked", "summary": f"memory unavailable: {exc}", "counts": {}, "path": "", "inbox": {}}
    counts = {key: len(value) for key, value in inbox.items() if isinstance(value, list)}
    pending_contradictions = counts.get("contradictions", 0)
    promotable = counts.get("promotable", 0)
    proposed = counts.get("proposed", 0)
    memory_status = "blocked" if pending_contradictions else ("watch" if promotable or proposed else "ready")
    return {
        "status": memory_status,
        "summary": f"{proposed} proposed; {promotable} promotable; {pending_contradictions} contradiction(s)",
        "counts": counts,
        "path": status.get("path") or "",
        "inbox": inbox,
    }


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


def render_live_dashboard_html(task_id: str | None = None, lang: str | None = None) -> str:
    """Render the daemon-backed live dashboard."""
    return render_workbench_dashboard_html(task_id=task_id, lang=lang)
