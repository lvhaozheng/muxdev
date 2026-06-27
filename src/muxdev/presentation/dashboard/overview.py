"""Project-oriented Mission Control dashboard overview read model."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from ...config.loader import load_config
from ...config.runtime import GATES, PUBLIC_WORKFLOWS, load_runtime_config
from ...services.product_experience import budget_panel, git_safety_panel, project_context_status, rules_skills_panel
from ...services.skills import build_skill_catalog, verify_skill_lock
from ...services.standards import EVIDENCE_LEVELS, RISK_LEVELS, SEVERITY_LEVELS, catalog_payload
from ...services.ux import build_ux_overview
from ...services.validation import list_validation_experiments
from ...services.workflow_plugins import list_workflow_plugins

_STANDARD_CATALOG = catalog_payload()
_STANDARD_IDS = set(SEVERITY_LEVELS) | set(RISK_LEVELS) | set(EVIDENCE_LEVELS)
_DEFAULT_STANDARD_MARKERS = {
    "ready": ("P3", "R1", "E1"),
    "watch": ("P2", "R2", "E1"),
    "blocked": ("P0", "R3", "E1"),
}
MODEL_ROLE_ORDER = ["requirements", "plan", "architect", "code", "test_strategy", "test", "review", "secure", "docs", "memory_curator"]
MODEL_ROLE_ALIASES = {"implementer": "code", "tester": "test", "reviewer": "review", "security": "secure", "doc_writer": "docs"}
MODEL_ROLE_DESCRIPTIONS = {
    "requirements": "Clarifies scope, constraints, and acceptance criteria.",
    "plan": "Turns requirements into implementation steps and risk controls.",
    "architect": "Designs architecture, interfaces, and system tradeoffs.",
    "code": "Implements changes and repairs blockers.",
    "test_strategy": "Chooses verification strategy and coverage priorities.",
    "test": "Runs focused checks and reports evidence.",
    "review": "Finds regressions, blockers, and missing tests.",
    "secure": "Reviews security, auth, secrets, and policy-sensitive changes.",
    "docs": "Updates docs, release notes, and handoff summaries.",
    "memory_curator": "Curates durable project memory from reviewed evidence.",
}


def build_dashboard_overview(
    workspace: Path,
    *,
    daemon: dict[str, Any],
    tasks: list[dict[str, Any]],
    approvals: list[dict[str, Any]],
    provider_actions: list[dict[str, Any]],
    provider_health: dict[str, Any],
    ecosystem: dict[str, Any] | None = None,
    hidden_projects: dict[str, dict[str, Any]] | None = None,
    hidden_tasks: dict[str, dict[str, Any]] | None = None,
    include_hidden: bool = False,
    include_global_config: bool = True,
    memory_governance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the aggregated project/global view used by the live dashboard."""
    workspace = workspace.resolve()
    ecosystem = ecosystem or {}
    hidden_projects = hidden_projects or {}
    hidden_tasks = hidden_tasks or {}
    enriched_tasks = [_task_with_dashboard_context(task, workspace, hidden_tasks) for task in tasks]
    hidden_project_ids = _hidden_project_ids(hidden_projects)
    visible_tasks = enriched_tasks if include_hidden else [
        task for task in enriched_tasks if task.get("project_id") not in hidden_project_ids and str(task.get("task_id") or task.get("run_id") or "") not in hidden_tasks
    ]
    visible_task_ids = {str(task.get("task_id") or task.get("run_id") or "") for task in visible_tasks}
    visible_approvals = approvals if include_hidden else [
        row for row in approvals if str(row.get("run_id") or row.get("task_id") or "") in visible_task_ids
    ]
    visible_provider_actions = provider_actions if include_hidden else [
        row for row in provider_actions if str(row.get("run_id") or row.get("task_id") or "") in visible_task_ids
    ]
    validation_experiments = list_validation_experiments(workspace)[:8]
    governance_summary = _governance_summary(validation_experiments, ecosystem, memory_governance or {})
    action_overview = build_ux_overview(
        daemon=daemon,
        tasks=visible_tasks,
        approvals=visible_approvals,
        provider_actions=visible_provider_actions,
        selected_task=visible_tasks[0] if visible_tasks else None,
    )
    projects = _apply_hidden_projects(_projects(workspace, visible_tasks), hidden_projects, include_hidden=include_hidden)
    counts = dict(action_overview["counts"])
    counts["projects"] = len(projects)
    current_project_id = _project_id(workspace)
    selected_project_id = current_project_id if any(project["id"] == current_project_id for project in projects) else (projects[0]["id"] if projects else "")
    delivery_confidence = _delivery_confidence_overview(visible_tasks)
    health_strip = _health_strip(workspace, daemon, visible_tasks, provider_health, projects)
    global_config = _global_config(workspace, visible_tasks, provider_health, ecosystem) if include_global_config else {}
    standards = _dashboard_standards(
        workspace,
        tasks=visible_tasks,
        approvals=visible_approvals,
        provider_actions=visible_provider_actions,
        provider_health=provider_health,
        delivery_confidence=delivery_confidence,
        validation_experiments=validation_experiments,
        governance_summary=governance_summary,
        health_strip=health_strip,
        projects=projects,
        ecosystem=ecosystem,
    )
    return {
        "workspace": str(workspace),
        "selected_project_id": selected_project_id,
        "headline": action_overview["headline"],
        "counts": counts,
        "current_status": action_overview["current_status"],
        "action_center": action_overview["action_center"],
        "pending_approvals": visible_approvals,
        "pending_provider_actions": visible_provider_actions,
        "projects": projects,
        "task_board": action_overview["task_board"],
        "filters": action_overview["filters"],
        "selected_task": action_overview["selected_task"],
        "delivery_confidence": delivery_confidence,
        "health_strip": health_strip,
        "governance_summary": governance_summary,
        "global_config": global_config,
        "global_config_deferred": not include_global_config,
        "artifact_center": action_overview["artifact_center"],
        "validation": {"experiments": validation_experiments, "summary": governance_summary["validation"]},
        "standards": standards,
    }


def dashboard_hidden_projects_path(data_dir: Path) -> Path:
    """Return the daemon-local hidden project store path."""
    return data_dir / "dashboard_hidden_projects.json"


def dashboard_hidden_tasks_path(data_dir: Path) -> Path:
    """Return the daemon-local hidden task store path."""
    return data_dir / "dashboard_hidden_tasks.json"


def load_hidden_projects(path: Path) -> dict[str, dict[str, Any]]:
    """Read hidden project metadata from a small JSON file."""
    return _load_hidden_records(path, "projects")


def load_hidden_tasks(path: Path) -> dict[str, dict[str, Any]]:
    """Read hidden task metadata from a small JSON file."""
    return _load_hidden_records(path, "tasks")


def _load_hidden_records(path: Path, key: str) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    records = data.get(key, data) if isinstance(data, dict) else {}
    if not isinstance(records, dict):
        return {}
    return {str(record_key): value for record_key, value in records.items() if isinstance(value, dict)}


def hide_dashboard_project(path: Path, project_id: str, *, project: dict[str, Any] | None = None) -> dict[str, Any]:
    """Mark a dashboard project hidden without deleting workspace files or runs."""
    hidden = load_hidden_projects(path)
    metadata = {
        "id": project_id,
        "name": (project or {}).get("name") or project_id,
        "path": (project or {}).get("path") or "",
        "hidden": True,
        "hidden_at": _utc_now(),
    }
    hidden[project_id] = metadata
    _write_hidden_projects(path, hidden)
    return metadata


def restore_dashboard_project(path: Path, project_id: str) -> dict[str, Any]:
    """Restore a dashboard project that was hidden."""
    hidden = load_hidden_projects(path)
    metadata = hidden.pop(project_id, {"id": project_id})
    metadata["hidden"] = False
    metadata["restored_at"] = _utc_now()
    _write_hidden_projects(path, hidden)
    return metadata


def hide_dashboard_task(path: Path, task_id: str, *, task: dict[str, Any] | None = None) -> dict[str, Any]:
    """Mark one dashboard task hidden without deleting run data."""
    hidden = load_hidden_tasks(path)
    metadata = {
        "id": task_id,
        "task_id": task_id,
        "run_id": task_id,
        "title": (task or {}).get("task_title") or (task or {}).get("title") or (task or {}).get("task") or task_id,
        "project_id": (task or {}).get("project_id") or "",
        "project_name": (task or {}).get("project_name") or "",
        "project_path": (task or {}).get("project_path") or (task or {}).get("workspace") or "",
        "hidden": True,
        "hidden_at": _utc_now(),
    }
    hidden[task_id] = metadata
    _write_hidden_tasks(path, hidden)
    return metadata


def restore_dashboard_task(path: Path, task_id: str) -> dict[str, Any]:
    """Restore a dashboard task that was hidden."""
    hidden = load_hidden_tasks(path)
    metadata = hidden.pop(task_id, {"id": task_id, "task_id": task_id, "run_id": task_id})
    metadata["hidden"] = False
    metadata["restored_at"] = _utc_now()
    _write_hidden_tasks(path, hidden)
    return metadata


def _projects(workspace: Path, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for task in tasks:
        project_path = _task_workspace(task, workspace)
        project_id = _project_id(project_path)
        grouped.setdefault(project_id, _empty_project(project_path))["tasks"].append(task)
    if not grouped:
        grouped[_project_id(workspace)] = _empty_project(workspace)

    projects = []
    for project in grouped.values():
        project_tasks = list(project.pop("tasks"))
        config = _project_config(Path(str(project["path"])), project_tasks)
        projects.append(
            {
                **project,
                "summary": _project_summary(project_tasks),
                "workflows": _workflow_groups(Path(str(project["path"])), project_tasks),
                "config": config,
                "health": _project_health(project_tasks),
                "shared_state": _shared_state(Path(str(project["path"])), project_tasks, config),
            }
        )
    return sorted(projects, key=lambda item: (item["summary"]["active"] == 0, str(item["name"]).lower()))


def _apply_hidden_projects(
    projects: list[dict[str, Any]],
    hidden_projects: dict[str, dict[str, Any]],
    *,
    include_hidden: bool,
) -> list[dict[str, Any]]:
    visible: list[dict[str, Any]] = []
    seen: set[str] = set()
    for project in projects:
        project_id = str(project.get("id") or "")
        seen.add(project_id)
        if project_id in hidden_projects:
            if include_hidden:
                visible.append({**project, "hidden": True, "hidden_at": hidden_projects[project_id].get("hidden_at")})
            continue
        visible.append({**project, "hidden": False})
    if include_hidden:
        for project_id, metadata in hidden_projects.items():
            if project_id in seen:
                continue
            path = Path(str(metadata.get("path") or metadata.get("name") or project_id))
            visible.append(
                {
                    "id": project_id,
                    "name": metadata.get("name") or path.name or project_id,
                    "path": str(metadata.get("path") or ""),
                    "hidden": True,
                    "hidden_at": metadata.get("hidden_at"),
                    "summary": {"tasks": 0, "active": 0, "waiting": 0, "failed": 0, "cost_usd": 0, "tokens": 0},
                    "health": {"status": "idle", "blocking": 0, "waiting": 0, "failed": 0, "budget": "ok"},
                    "shared_state": {},
                    "workflows": [],
                    "config": {},
                }
            )
    return visible


def _empty_project(path: Path) -> dict[str, Any]:
    return {
        "id": _project_id(path),
        "name": path.name or str(path),
        "path": str(path),
        "tasks": [],
    }


def _project_summary(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    active = [task for task in tasks if str(task.get("status")) not in {"completed", "blocked", "aborted"}]
    waiting = [task for task in tasks if int(task.get("pending_approvals") or 0) or int(task.get("pending_provider_actions") or 0)]
    failed = [task for task in tasks if str(task.get("status")) in {"blocked", "aborted", "failed"} or int(task.get("errors") or 0)]
    return {
        "tasks": len(tasks),
        "active": len(active),
        "waiting": len(waiting),
        "failed": len(failed),
        "cost_usd": round(sum(float(task.get("cost_usd") or 0) for task in tasks), 6),
        "tokens": sum(int(task.get("tokens") or 0) for task in tasks),
    }


def _project_health(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    waiting = [task for task in tasks if int(task.get("pending_approvals") or 0) or int(task.get("pending_provider_actions") or 0)]
    failed = [task for task in tasks if str(task.get("status")) in {"blocked", "aborted", "failed"} or int(task.get("errors") or 0)]
    high_cost = [task for task in tasks if float(task.get("cost_usd") or 0) > 0.5]
    if failed:
        status = "blocked"
    elif waiting:
        status = "needs_attention"
    elif high_cost:
        status = "budget_watch"
    elif any(str(task.get("status") or "") not in {"completed", "blocked", "aborted"} for task in tasks):
        status = "running"
    else:
        status = "ready" if tasks else "idle"
    return {
        "status": status,
        "blocking": len(failed) + len(waiting),
        "waiting": len(waiting),
        "failed": len(failed),
        "budget": "watch" if high_cost else "ok",
    }


def _shared_state(project: Path, tasks: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    context = config.get("project_context") if isinstance(config.get("project_context"), dict) else {}
    memory = config.get("memory") if isinstance(config.get("memory"), dict) else {}
    recent_facts = []
    for task in tasks[:3]:
        title = task.get("task_title") or task.get("task") or task.get("task_id")
        stage = task.get("current_stage") or task.get("status") or ""
        if title:
            recent_facts.append({"task_id": task.get("task_id") or task.get("run_id"), "summary": str(title), "stage": stage})
    return {
        "label": "Shared State / Memory Board",
        "project": str(project),
        "context_exists": bool(context.get("exists")),
        "context_path": context.get("path"),
        "memory_policy": memory.get("promotion", "explicit"),
        "review_queue": memory.get("review_queue", "muxdev memory inbox"),
        "recent_facts": recent_facts,
    }


def _workflow_groups(project: Path, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    workflow_names = {str(task.get("workflow") or "software-dev") for task in tasks}
    if not workflow_names:
        workflow_names.add(_default_workflow(project))
    config_workflows = load_config(project).get("workflows", {})
    groups = []
    for name in sorted(workflow_names):
        definition = config_workflows.get(name, {}) if isinstance(config_workflows, dict) else {}
        workflow_tasks = [task for task in tasks if str(task.get("workflow") or "software-dev") == name]
        stages = _stage_rows(definition)
        role_groups = _role_groups(stages, workflow_tasks)
        model_roles = _model_roles_from_stages(stages)
        groups.append(
            {
                "id": name,
                "name": str(definition.get("name") or name) if isinstance(definition, dict) else name,
                "task_count": len(workflow_tasks),
                "stage_count": len(stages),
                "roles": model_roles,
                "model_roles": model_roles,
                "human_gates": _human_gates_from_stages(stages),
                "delivery_gates": _delivery_gates_from_stages(stages),
                "stages": stages,
                "role_groups": role_groups,
            }
        )
    return groups


def _stage_rows(definition: object) -> list[dict[str, Any]]:
    stages = definition.get("stages", []) if isinstance(definition, dict) else []
    stage_rows = stages if isinstance(stages, list) else []
    rows = []
    for stage in stage_rows:
        if not isinstance(stage, dict):
            continue
        raw_role = str(stage.get("role") or "").strip()
        stage_type = str(stage.get("type") or "agent")
        model_role = _model_role(raw_role) if stage_type == "agent" else ""
        actor_kind = _stage_actor_kind(stage_type, model_role)
        rows.append(
            {
                "id": stage.get("id"),
                "role": model_role,
                "model_role": model_role,
                "raw_role": raw_role,
                "type": stage_type,
                "actor_kind": actor_kind,
                "actor_label": _stage_actor_label(actor_kind, model_role),
                "deps": stage.get("deps", []),
                "read_only": bool(stage.get("read_only")),
                "allow_write": bool(stage.get("allow_write")),
                "allow_shell": bool(stage.get("allow_shell")),
                "approval_type": stage.get("approval_type"),
                "approval_reason": stage.get("approval_reason"),
            }
        )
    return rows


def _model_role(role: str) -> str:
    normalized = str(role or "").strip().replace("-", "_")
    normalized = MODEL_ROLE_ALIASES.get(normalized, normalized)
    return normalized if normalized in MODEL_ROLE_DESCRIPTIONS else ""


def _stage_actor_kind(stage_type: str, model_role: str) -> str:
    if stage_type == "human_gate":
        return "human_gate"
    if stage_type == "delivery_gate":
        return "delivery_gate"
    if stage_type == "agent" and model_role:
        return "model_role"
    return "system_step"


def _stage_actor_label(actor_kind: str, model_role: str) -> str:
    if actor_kind == "model_role":
        return model_role
    if actor_kind == "human_gate":
        return "human review"
    if actor_kind == "delivery_gate":
        return "delivery gate"
    return "system step"


def _model_roles_from_stages(stages: list[dict[str, Any]]) -> list[str]:
    roles: list[str] = []
    for stage in stages:
        role = str(stage.get("model_role") or "")
        if role and role not in roles:
            roles.append(role)
    return roles


def _human_gates_from_stages(stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    for stage in stages:
        if stage.get("actor_kind") != "human_gate":
            continue
        gates.append({"stage": stage.get("id"), "type": stage.get("approval_type") or stage.get("id"), "reason": stage.get("approval_reason") or ""})
    return gates


def _delivery_gates_from_stages(stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []
    for stage in stages:
        if stage.get("actor_kind") == "delivery_gate":
            gates.append({"stage": stage.get("id"), "type": "delivery_gate", "reason": "internal evidence and blocker verification"})
    return gates


def _role_groups(stages: list[dict[str, Any]], tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    roles = []
    for stage in stages:
        role = str(stage.get("model_role") or "")
        if role and role not in roles:
            roles.append(role)
    task_cards = []
    for task in tasks:
        role = _task_role(task, stages)
        if role and role not in roles:
            roles.append(role)
        task_cards.append(_task_card(task, role))
    return [{"role": role, "tasks": [card for card in task_cards if card["role"] == role]} for role in roles]


def _task_card(task: dict[str, Any], role: str) -> dict[str, Any]:
    task_id = str(task.get("task_id") or task.get("run_id") or "")
    stage_context = task.get("stage_context") if isinstance(task.get("stage_context"), dict) else {}
    return {
        "task_id": task_id,
        "run_id": task_id,
        "title": str(task.get("task") or task_id or "muxdev task"),
        "status": task.get("status"),
        "role": role,
        "model_role": role if role in MODEL_ROLE_DESCRIPTIONS else "",
        "stage_actor_kind": stage_context.get("actor_kind") or "",
        "stage_actor_label": stage_context.get("actor_label") or "",
        "workflow": task.get("workflow"),
        "provider": task.get("provider"),
        "current_stage": task.get("current_stage") or "",
        "current_activity": task.get("current_activity") or "",
        "elapsed_seconds": task.get("elapsed_seconds"),
        "current_stage_elapsed_seconds": task.get("current_stage_elapsed_seconds"),
        "latest_provider_attempt": task.get("latest_provider_attempt"),
        "stage_timeline": task.get("stage_timeline") or [],
        "workspace": task.get("workspace") or "",
        "project_id": task.get("project_id") or "",
        "project_name": task.get("project_name") or "",
        "project_path": task.get("project_path") or "",
        "hidden": bool(task.get("hidden")),
        "branch": task.get("branch") or task.get("worktree") or "",
        "risk": task.get("risk") or _task_risk(task),
        "delivery_confidence": task.get("delivery_confidence", {}),
        "evidence_summary": task.get("evidence_summary", {}),
        "cost_usd": task.get("cost_usd", 0),
        "tokens": task.get("tokens", 0),
        "pending_approvals": task.get("pending_approvals", 0),
        "pending_provider_actions": task.get("pending_provider_actions", 0),
        "errors": task.get("errors", 0),
        "error_summary": task.get("error_summary"),
        "gate": task.get("gate") or "",
        "skills": task.get("skills") or [],
        "recover_endpoint": task.get("recover_endpoint") or f"/api/tasks/{task_id}/continue",
        "rollback_endpoint": task.get("rollback_endpoint") or f"/api/tasks/{task_id}/rollback",
        "detail_endpoint": f"/api/tasks/{task_id}",
        "report_endpoint": f"/api/tasks/{task_id}/report",
        "diff_endpoint": f"/api/tasks/{task_id}/diff",
    }


def _task_role(task: dict[str, Any], stages: list[dict[str, Any]]) -> str:
    current = str(task.get("current_stage") or "")
    current_index = -1
    for index, stage in enumerate(stages):
        if str(stage.get("id") or "") == current:
            current_index = index
            role = str(stage.get("model_role") or "")
            if role:
                task["stage_context"] = {"actor_kind": stage.get("actor_kind"), "actor_label": stage.get("actor_label")}
                return role
            task["stage_context"] = {"actor_kind": stage.get("actor_kind"), "actor_label": stage.get("actor_label")}
            break
    if current_index >= 0:
        for stage in reversed(stages[:current_index]):
            role = str(stage.get("model_role") or "")
            if role:
                return role
        for stage in stages[current_index + 1 :]:
            role = str(stage.get("model_role") or "")
            if role:
                return role
    status = str(task.get("status") or "")
    if status == "completed":
        return next((str(stage.get("model_role")) for stage in reversed(stages) if stage.get("model_role")), "")
    if status in {"blocked", "aborted", "failed"} or int(task.get("errors") or 0):
        return next((str(stage.get("model_role")) for stage in stages if stage.get("model_role")), "")
    return next((str(stage.get("model_role")) for stage in stages if stage.get("model_role")), "")


def _project_config(project: Path, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        rules = rules_skills_panel(project)
    except Exception as exc:  # pragma: no cover - defensive for broken project configs
        rules = {"error": str(exc), "gate": "", "roles": {}, "skills": [], "commands": []}
    return {
        "roles": rules.get("roles", {}),
        "gate": rules.get("gate"),
        "skills": rules.get("skills", []),
        "project_context": project_context_status(project),
        "git_safety": git_safety_panel(project),
        "memory": {"review_queue": "muxdev memory inbox", "promotion": "explicit"},
        "approvals": {
            "pending": sum(int(task.get("pending_approvals") or 0) for task in tasks),
            "commands": ["muxdev approvals", "muxdev approve <approval_id>", "muxdev deny <approval_id>"],
        },
    }


def _global_config(workspace: Path, tasks: list[dict[str, Any]], provider_health: dict[str, Any], ecosystem: dict[str, Any]) -> dict[str, Any]:
    runtime = _runtime_config(workspace)
    return {
        "mcp": _mcp_summary(workspace, ecosystem),
        "role_routes": _role_routes(workspace, runtime, provider_health),
        "workflow_templates": _workflow_templates(workspace),
        "skills_catalog": _skills_catalog(workspace),
        "providers": provider_health,
        "budget": budget_panel(tasks),
        "safety": {
            "gate": runtime.get("gate"),
            "gates": GATES,
            "git": git_safety_panel(workspace),
        },
    }


def _delivery_confidence_overview(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for task in tasks:
        confidence = task.get("delivery_confidence") if isinstance(task.get("delivery_confidence"), dict) else {}
        if not confidence:
            continue
        rows.append(
            {
                "task_id": task.get("task_id") or task.get("run_id"),
                "task_title": task.get("task_title") or task.get("task") or task.get("title"),
                "project_name": task.get("project_name") or "",
                "status": task.get("status"),
                "current_stage": task.get("current_stage"),
                "label": confidence.get("label"),
                "confidence": confidence.get("confidence"),
                "score": confidence.get("score"),
                "tests": confidence.get("tests", {}),
                "review": confidence.get("review", {}),
                "rollback": confidence.get("rollback", {}),
                "diff": confidence.get("diff", {}),
                "usage": confidence.get("usage", {}),
                "reasons": confidence.get("reasons", []),
                "missing_evidence": confidence.get("missing_evidence", []),
            }
        )
    priority = {"blocked": 0, "risky": 1, "collecting": 2, "reviewable": 3, "trusted": 4}
    rows.sort(key=lambda row: (priority.get(str(row.get("label")), 2), str(row.get("status")) == "completed", str(row.get("task_title") or "")))
    counts: dict[str, int] = {}
    for row in rows:
        label = str(row.get("label") or "unknown")
        counts[label] = counts.get(label, 0) + 1
    return {"items": rows[:8], "counts": counts}


def _dashboard_standards(
    workspace: Path,
    *,
    tasks: list[dict[str, Any]],
    approvals: list[dict[str, Any]],
    provider_actions: list[dict[str, Any]],
    provider_health: dict[str, Any],
    delivery_confidence: dict[str, Any],
    validation_experiments: list[dict[str, Any]],
    governance_summary: dict[str, Any],
    health_strip: list[dict[str, Any]],
    projects: list[dict[str, Any]],
    ecosystem: dict[str, Any],
) -> dict[str, Any]:
    return {
        "trusted_delivery": _trusted_delivery_standards(tasks, approvals, provider_actions, delivery_confidence),
        "validation": _validation_standards(validation_experiments),
        "governance": _governance_standards(workspace, tasks, provider_health, governance_summary, health_strip, ecosystem),
        "configuration": _configuration_standards(workspace, projects),
    }


def _standard_section(section_id: str, label: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(items)
    passed = sum(1 for item in items if str(item.get("status")) == "ready")
    blocked = any(str(item.get("status")) == "blocked" for item in items)
    status = "blocked" if blocked else ("ready" if total and passed == total else "watch")
    return {
        "id": section_id,
        "label": label,
        "status": status,
        "passed": passed,
        "total": total,
        "summary": f"{passed}/{total} standard(s) met",
        "catalog": _STANDARD_CATALOG,
        "items": items,
    }


def _standard_item(
    item_id: str,
    label: str,
    status: str,
    current: object,
    target: object,
    *,
    evidence: object = "",
    action: str = "none",
    standard_id: str | None = None,
    severity: str | None = None,
    risk_level: str | None = None,
    evidence_level: str | None = None,
) -> dict[str, Any]:
    standard_id, severity, risk_level, evidence_level = _standard_markers(
        status,
        standard_id=standard_id,
        severity=severity,
        risk_level=risk_level,
        evidence_level=evidence_level,
    )
    return {
        "id": item_id,
        "label": label,
        "status": status,
        "current": current,
        "target": target,
        "evidence": evidence,
        "action": action,
        "standard_id": standard_id,
        "severity": severity,
        "risk_level": risk_level,
        "evidence_level": evidence_level,
    }


def _standard_markers(
    status: str,
    *,
    standard_id: str | None,
    severity: str | None,
    risk_level: str | None,
    evidence_level: str | None,
) -> tuple[str, str, str, str]:
    default_severity, default_risk, default_evidence = _DEFAULT_STANDARD_MARKERS.get(
        str(status or "").lower(),
        _DEFAULT_STANDARD_MARKERS["watch"],
    )
    resolved_severity = severity if severity in SEVERITY_LEVELS else default_severity
    resolved_risk = risk_level if risk_level in RISK_LEVELS else default_risk
    resolved_evidence = evidence_level if evidence_level in EVIDENCE_LEVELS else default_evidence
    resolved_standard = (
        standard_id
        if standard_id in _STANDARD_IDS
        else resolved_evidence
        if resolved_evidence != "E1"
        else resolved_risk
    )
    return resolved_standard, resolved_severity, resolved_risk, resolved_evidence


def _trusted_delivery_standards(
    tasks: list[dict[str, Any]],
    approvals: list[dict[str, Any]],
    provider_actions: list[dict[str, Any]],
    delivery_confidence: dict[str, Any],
) -> dict[str, Any]:
    rows = delivery_confidence.get("items") if isinstance(delivery_confidence.get("items"), list) else []
    focus = rows[0] if rows else {}
    confidence = _as_float(focus.get("confidence"), default=0.0)
    tests = [row.get("tests", {}) for row in rows if isinstance(row, dict)]
    reviews = [row.get("review", {}) for row in rows if isinstance(row, dict)]
    rollbacks = [row.get("rollback", {}) for row in rows if isinstance(row, dict)]
    diffs = [row.get("diff", {}) for row in rows if isinstance(row, dict)]
    budget = budget_panel(tasks)
    pending_actions = len(approvals) + len(provider_actions)
    missing = focus.get("missing_evidence") if isinstance(focus.get("missing_evidence"), list) else []
    confidence_status = "watch"
    if rows:
        confidence_status = "ready" if confidence >= 0.85 else ("blocked" if confidence < 0.60 or str(focus.get("label")) in {"blocked", "risky"} else "watch")
    items = [
        _standard_item(
            "confidence",
            "Delivery confidence",
            confidence_status,
            round(confidence, 3) if rows else "missing",
            ">= 0.85 trusted; 0.60-0.84 reviewable; < 0.60 risky",
            evidence=focus.get("task_id") or "",
            action="publish_evidence" if not rows else ("resolve_risk" if confidence_status != "ready" else "none"),
            standard_id="E3",
            severity="P2" if confidence_status != "ready" else "P3",
            risk_level="R2" if confidence_status == "ready" else "R3",
            evidence_level="E3" if confidence_status == "ready" else "E1",
        ),
        _standard_item(
            "tests",
            "Tests",
            "ready" if tests and all(str(row.get("status")) == "passed" for row in tests if isinstance(row, dict)) else ("watch" if not tests else "blocked"),
            _passed_total(tests, "passed", "total"),
            "all recorded tests passed",
            evidence=_task_refs(rows),
            action="add_tests",
            standard_id="E2",
            severity="P1",
            risk_level="R1",
            evidence_level="E2" if tests else "E0",
        ),
        _standard_item(
            "review",
            "Review blockers",
            "ready" if reviews and all(int(row.get("high_blockers") or 0) == 0 for row in reviews if isinstance(row, dict)) else ("watch" if not reviews else "blocked"),
            sum(int(row.get("high_blockers") or 0) for row in reviews if isinstance(row, dict)),
            "0 high review blockers",
            evidence=_task_refs(rows),
            action="complete_review",
            standard_id="E3",
            severity="P0" if reviews and any(int(row.get("high_blockers") or 0) for row in reviews if isinstance(row, dict)) else "P2",
            risk_level="R3" if reviews and any(int(row.get("high_blockers") or 0) for row in reviews if isinstance(row, dict)) else "R2",
            evidence_level="E3" if reviews else "E0",
        ),
        _standard_item(
            "rollback",
            "Rollback",
            "ready" if rollbacks and all(bool(row.get("available")) for row in rollbacks if isinstance(row, dict)) else ("watch" if not rollbacks else "blocked"),
            f"{sum(1 for row in rollbacks if isinstance(row, dict) and row.get('available'))}/{len(rollbacks)} available" if rollbacks else "missing",
            "rollback snapshot available",
            evidence=_task_refs(rows),
            action="enable_rollback",
            standard_id="R2",
            severity="P2",
            risk_level="R2",
            evidence_level="E1",
        ),
        _standard_item(
            "artifacts",
            "Report / diff / evidence",
            "ready" if rows and not missing and all(bool(row.get("available")) for row in diffs if isinstance(row, dict)) else "watch",
            "complete" if rows and not missing else (", ".join(str(item) for item in missing) or "missing"),
            "report, diff, evidence bundle complete",
            evidence=_task_refs(rows),
            action="publish_evidence",
            standard_id="E1",
            severity="P2",
            risk_level="R2",
            evidence_level="E1" if rows else "E0",
        ),
        _standard_item(
            "budget",
            "Budget",
            "ready" if int(budget.get("high_cost_tasks") or 0) == 0 else "watch",
            f"${float(budget.get('total_cost_usd') or 0):.4f}; {budget.get('high_cost_tasks', 0)} high-cost task(s)",
            "no task above default budget",
            evidence=f"default=${budget.get('default_task_budget_usd')}",
            action="review_budget",
            standard_id="R3",
            severity="P1" if int(budget.get("high_cost_tasks") or 0) else "P3",
            risk_level="R3" if int(budget.get("high_cost_tasks") or 0) else "R1",
            evidence_level="E1",
        ),
        _standard_item(
            "human_attention",
            "Human attention",
            "ready" if pending_actions == 0 else "blocked",
            pending_actions,
            "0 pending approvals/provider actions",
            evidence={"approvals": len(approvals), "provider_actions": len(provider_actions)},
            action="resolve_attention",
            standard_id="R3",
            severity="P1" if pending_actions else "P3",
            risk_level="R3" if pending_actions else "R1",
            evidence_level="E1",
        ),
    ]
    return _standard_section("trusted_delivery", "Trusted delivery standards", items)


def _validation_standards(experiments: list[dict[str, Any]]) -> dict[str, Any]:
    latest = experiments[0] if experiments else {}
    strategies = latest.get("strategies") if isinstance(latest.get("strategies"), list) else []
    scores = latest.get("strategy_scores") if isinstance(latest.get("strategy_scores"), dict) else {}
    metrics = latest.get("metrics_summary") if isinstance(latest.get("metrics_summary"), dict) else {}
    muxdev_delta = latest.get("muxdev_delta") if isinstance(latest.get("muxdev_delta"), dict) else {}
    best_score = max((_as_float(value) for value in scores.values()), default=_as_float(metrics.get("score"), default=0.0))
    has_experiment = bool(latest)
    has_baseline = "direct_cli" in {str(item) for item in strategies} or str(latest.get("baseline_strategy") or "") == "direct_cli"
    has_muxdev = any(str(item).startswith("muxdev_") or str(item) in {"single_agent", "multi_agent"} for item in strategies)
    items = [
        _standard_item(
            "experiment_exists",
            "Validation experiment",
            "ready" if has_experiment else "watch",
            latest.get("experiment_id") or "not_run",
            "latest validation experiment exists",
            evidence=latest.get("report") or "",
            action="run_validation",
        ),
        _standard_item(
            "baseline_coverage",
            "Baseline coverage",
            "ready" if has_baseline and has_muxdev else "watch",
            ", ".join(str(item) for item in strategies) or "missing",
            "direct_cli + one muxdev strategy",
            evidence=latest.get("experiment_id") or "",
            action="run_validation",
        ),
        _standard_item(
            "score",
            "Validation score",
            "ready" if best_score >= 0.85 else ("watch" if has_experiment else "watch"),
            round(best_score, 4),
            ">= 0.85",
            evidence=scores,
            action="review_validation",
        ),
        _standard_item(
            "test_pass_rate",
            "Test pass rate",
            "ready" if _as_float(metrics.get("test_pass_rate")) >= 1.0 else ("watch" if has_experiment else "watch"),
            metrics.get("test_pass_rate", 0.0),
            "1.0",
            evidence=latest.get("experiment_id") or "",
            action="add_tests",
        ),
        _standard_item(
            "evidence_confidence",
            "Evidence confidence",
            "ready" if _as_float(metrics.get("evidence_confidence")) >= 0.85 else ("watch" if has_experiment else "watch"),
            metrics.get("evidence_confidence", 0.0),
            ">= 0.85",
            evidence=latest.get("experiment_id") or "",
            action="publish_evidence",
        ),
        _standard_item(
            "safety",
            "Safety",
            "ready" if _as_float(metrics.get("safety_score")) >= 0.80 and int(metrics.get("high_review_blockers") or 0) == 0 else ("blocked" if int(metrics.get("high_review_blockers") or 0) else "watch"),
            {"safety_score": metrics.get("safety_score", 0.0), "high_review_blockers": metrics.get("high_review_blockers", 0)},
            "safety_score >= 0.80 and 0 high blockers",
            evidence=latest.get("experiment_id") or "",
            action="complete_review",
        ),
        _standard_item(
            "rollback_efficiency",
            "Rollback / cost efficiency",
            "ready" if bool(metrics.get("rollback_success")) and has_experiment else ("watch" if has_experiment else "watch"),
            {"rollback_success": bool(metrics.get("rollback_success")), "cost_usd": metrics.get("cost_usd", 0.0), "tokens": metrics.get("tokens", 0)},
            "rollback_success=true with visible cost/tokens",
            evidence=muxdev_delta,
            action="enable_rollback",
        ),
    ]
    return _standard_section("validation", "Validation standards", items)


def _governance_standards(
    workspace: Path,
    tasks: list[dict[str, Any]],
    provider_health: dict[str, Any],
    governance_summary: dict[str, Any],
    health_strip: list[dict[str, Any]],
    ecosystem: dict[str, Any],
) -> dict[str, Any]:
    budget = budget_panel(tasks)
    git = git_safety_panel(workspace)
    mcp = _mcp_summary(workspace, ecosystem)
    memory = governance_summary.get("memory") if isinstance(governance_summary.get("memory"), dict) else {}
    ready_providers = provider_health.get("ready") if isinstance(provider_health.get("ready"), list) else []
    items = [
        _standard_item("provider_ready", "Provider availability", "ready" if ready_providers else "blocked", len(ready_providers), ">= 1 ready provider", evidence=provider_health, action="fix_provider"),
        _standard_item("git_safety", "Git safety", "ready" if str(git.get("status")) in {"clean", "dirty", "not_git"} else "blocked", git.get("status") or "-", "not blocked; branch/dirty state visible", evidence=git.get("branch") or git.get("warning") or "", action="fix_git"),
        _standard_item("budget_guardrail", "Budget guardrail", "ready" if int(budget.get("high_cost_tasks") or 0) == 0 else "watch", budget.get("high_cost_tasks", 0), "0 high-cost tasks", evidence=budget, action="review_budget"),
        _standard_item("mcp_guardrails", "MCP guardrails", "ready" if str(mcp.get("status") or "enabled") in {"enabled", "ok", "ready"} else "watch", mcp.get("status") or "enabled", "enabled", evidence={"tools": mcp.get("tools_count", 0), "write_policy": mcp.get("write_policy", "-")}, action="setup_mcp"),
        _standard_item("skills_lock", "Skills lock", "ready" if any(row.get("id") == "skills" and row.get("status") in {"ok", "ready"} for row in health_strip) else "watch", next((row.get("summary") for row in health_strip if row.get("id") == "skills"), "-"), "skills visible and lock readable", evidence="skills health strip", action="configure_project"),
        _standard_item("memory_conflicts", "Memory conflicts", "ready" if int((memory.get("counts") or {}).get("contradictions") or 0) == 0 else "blocked", int((memory.get("counts") or {}).get("contradictions") or 0), "0 unresolved contradictions", evidence=memory.get("path") or "", action="resolve_memory"),
    ]
    return _standard_section("governance", "Governance standards", items)


def _configuration_standards(workspace: Path, projects: list[dict[str, Any]]) -> dict[str, Any]:
    runtime = _runtime_config(workspace)
    selected = projects[0] if projects else {}
    config = selected.get("config") if isinstance(selected.get("config"), dict) else {}
    context = config.get("project_context") if isinstance(config.get("project_context"), dict) else project_context_status(workspace)
    workflow_count = sum(len(project.get("workflows") or []) for project in projects)
    role_count = len(runtime.get("roles") or {}) if isinstance(runtime.get("roles"), dict) else 0
    items = [
        _standard_item("gate", "Gate policy", "ready" if runtime.get("gate") else "watch", {"gate": runtime.get("gate") or ""}, "gate configured", evidence="muxdev config", action="configure_project"),
        _standard_item("workflow_template", "Workflow template", "ready" if workflow_count else "watch", workflow_count, ">= 1 workflow template visible", evidence=_default_workflow(workspace), action="configure_project"),
        _standard_item("role_template", "Role providers", "ready" if role_count else "watch", role_count, "role providers configured or auto", evidence=runtime.get("roles") or {}, action="configure_project"),
        _standard_item("project_context", "Project context", "ready" if context.get("exists") else "watch", context.get("path") or "missing", "project context exists", evidence=context.get("preview") or "", action="configure_project"),
        _standard_item("project_budget", "Project budget", "ready", "$0.50 default", "default budget visible", evidence="--max-cost-usd", action="review_budget"),
    ]
    return _standard_section("configuration", "Configuration standards", items)


def _task_refs(rows: list[object]) -> list[str]:
    return [str(row.get("task_id") or row.get("run_id") or "") for row in rows if isinstance(row, dict) and (row.get("task_id") or row.get("run_id"))][:6]


def _passed_total(rows: list[object], passed_key: str, total_key: str) -> str:
    passed = sum(int(row.get(passed_key) or 0) for row in rows if isinstance(row, dict))
    total = sum(int(row.get(total_key) or 0) for row in rows if isinstance(row, dict))
    return f"{passed}/{total}" if total else "missing"


def _as_float(value: object, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _health_strip(workspace: Path, daemon: dict[str, Any], tasks: list[dict[str, Any]], provider_health: dict[str, Any], projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    budget = budget_panel(tasks)
    selected_project = projects[0] if projects else {}
    selected_config = selected_project.get("config", {}) if isinstance(selected_project.get("config"), dict) else {}
    git = selected_config.get("git_safety") if isinstance(selected_config.get("git_safety"), dict) else git_safety_panel(workspace)
    skills = selected_config.get("skills", []) if isinstance(selected_config.get("skills"), list) else []
    memory = selected_project.get("shared_state", {}) if isinstance(selected_project.get("shared_state"), dict) else {}
    ready = len(provider_health.get("ready", []) if isinstance(provider_health.get("ready"), list) else [])
    partial = len(provider_health.get("partial", []) if isinstance(provider_health.get("partial"), list) else [])
    unavailable = len(provider_health.get("unavailable", []) if isinstance(provider_health.get("unavailable"), list) else [])
    daemon_status = str(daemon.get("status") or "unknown")
    return [
        {
            "id": "daemon",
            "label": "Daemon",
            "status": "ok" if daemon_status in {"ok", "running"} else "degraded",
            "summary": f"{daemon_status}; {daemon.get('running_tasks', 0)} running",
        },
        {
            "id": "providers",
            "label": "Providers",
            "status": "ok" if ready else ("degraded" if partial else "blocked"),
            "summary": f"{ready} ready / {partial} partial / {unavailable} unavailable",
        },
        {
            "id": "budget",
            "label": "Budget",
            "status": "watch" if int(budget.get("high_cost_tasks") or 0) else "ok",
            "summary": f"${float(budget.get('total_cost_usd') or 0):.4f}; {budget.get('active_tasks', 0)} active",
        },
        {
            "id": "git",
            "label": "Git Safety",
            "status": str(git.get("status") or "unknown"),
            "summary": str(git.get("branch") or git.get("warning") or git.get("status") or "-"),
        },
        {
            "id": "skills",
            "label": "Skills",
            "status": "ok" if skills else "watch",
            "summary": f"{len(skills)} project skill(s)",
        },
        {
            "id": "memory",
            "label": "Memory",
            "status": "ok" if memory.get("context_exists") else "watch",
            "summary": str(memory.get("review_queue") or "muxdev memory inbox"),
        },
    ]


def _governance_summary(validation_experiments: list[dict[str, Any]], ecosystem: dict[str, Any], memory_governance: dict[str, Any]) -> dict[str, Any]:
    provider_learning = _rows(ecosystem.get("provider_learning"))
    parallel_conflicts = _rows(ecosystem.get("parallel_conflicts"))
    semantic_reviews = _rows(ecosystem.get("semantic_merge_reviews"))
    multi_repo = _rows(ecosystem.get("multi_repo_orchestrations"))
    memory = _memory_governance(memory_governance)
    validation = _validation_summary(validation_experiments)
    provider = _provider_learning_summary(provider_learning)
    parallel = _parallel_summary(parallel_conflicts, semantic_reviews)
    repos = _multi_repo_summary(multi_repo)
    items = [
        {
            "id": "validation",
            "label": "Validation",
            "status": validation["status"],
            "summary": validation["summary"],
        },
        {
            "id": "provider_learning",
            "label": "Provider Learning",
            "status": provider["status"],
            "summary": provider["summary"],
        },
        {
            "id": "parallel_control",
            "label": "Parallel Control",
            "status": parallel["status"],
            "summary": parallel["summary"],
        },
        {
            "id": "memory",
            "label": "Memory Governance",
            "status": memory["status"],
            "summary": memory["summary"],
        },
        {
            "id": "multi_repo",
            "label": "Multi-Repo",
            "status": repos["status"],
            "summary": repos["summary"],
        },
    ]
    return {
        "items": items,
        "validation": validation,
        "provider_learning": provider,
        "parallel_control": parallel,
        "memory": memory,
        "multi_repo": repos,
    }


def _validation_summary(experiments: list[dict[str, Any]]) -> dict[str, Any]:
    winners = _count_by(experiments, "winner")
    latest = experiments[0] if experiments else {}
    winner = latest.get("winner") or "-"
    return {
        "status": "ready" if experiments else "watch",
        "count": len(experiments),
        "latest": latest,
        "winners": winners,
        "summary": f"{len(experiments)} experiment(s); latest winner {winner}",
        "items": experiments[:5],
    }


def _provider_learning_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    trend = []
    for row in sorted(rows, key=lambda item: (-float(item.get("score") or 0), str(item.get("provider") or ""), str(item.get("role") or "")))[:8]:
        trend.append(
            {
                "provider": row.get("provider"),
                "role": row.get("role") or "any",
                "score": round(float(row.get("score") or 0), 3),
                "attempts": int(row.get("attempts") or 0),
                "successes": int(row.get("successes") or 0),
                "failures": int(row.get("failures") or 0),
                "human_actions": int(row.get("human_actions") or 0),
                "updated_at": row.get("updated_at"),
            }
        )
    best = trend[0] if trend else {}
    best_label = f"{best.get('provider')}/{best.get('role')}" if best else "-"
    return {
        "status": "ready" if trend else "watch",
        "summary": f"{len(rows)} score row(s); best {best_label}",
        "count": len(rows),
        "trend": trend,
    }


def _parallel_summary(conflicts: list[dict[str, Any]], semantic_reviews: list[dict[str, Any]]) -> dict[str, Any]:
    open_conflicts = [row for row in conflicts if str(row.get("status") or "open") == "open"]
    blocked_reviews = [row for row in semantic_reviews if str(row.get("decision") or "").lower() in {"reject", "blocked"}]
    status = "blocked" if open_conflicts or blocked_reviews else ("ready" if conflicts or semantic_reviews else "watch")
    items = []
    for row in conflicts[:6]:
        items.append(
            {
                "kind": "parallel_conflict",
                "id": row.get("conflict_id"),
                "status": row.get("status") or "open",
                "severity": row.get("severity") or "-",
                "run_id": row.get("run_id"),
                "stage_id": row.get("stage_id"),
                "lanes": len(row.get("stages") or []),
                "files": len(row.get("files") or []),
                "summary": f"{len(row.get('stages') or [])} lane(s) / {len(row.get('files') or [])} file(s)",
            }
        )
    for row in semantic_reviews[:6]:
        findings = row.get("findings") if isinstance(row.get("findings"), list) else []
        items.append(
            {
                "kind": "semantic_merge",
                "id": row.get("review_id"),
                "status": row.get("decision") or "-",
                "severity": "high" if str(row.get("decision") or "").lower() in {"reject", "blocked"} else "medium",
                "run_id": row.get("run_id"),
                "stage_id": "merge",
                "lanes": 1,
                "files": 0,
                "summary": f"{row.get('decision') or '-'}; {len(findings)} finding(s)",
            }
        )
    return {
        "status": status,
        "summary": f"{len(open_conflicts)} open conflict(s); {len(blocked_reviews)} blocked merge review(s)",
        "open_conflicts": len(open_conflicts),
        "semantic_reviews": len(semantic_reviews),
        "items": items[:8],
    }


def _multi_repo_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    active = [row for row in rows if str(row.get("status") or "") not in {"completed", "cancelled", "failed"}]
    items = []
    for row in rows[:6]:
        repos = row.get("repos") if isinstance(row.get("repos"), list) else []
        items.append(
            {
                "orchestration_id": row.get("orchestration_id"),
                "status": row.get("status"),
                "mode": row.get("mode"),
                "task": row.get("task"),
                "repos": len(repos),
                "plan_path": row.get("plan_path"),
                "updated_at": row.get("updated_at"),
            }
        )
    return {
        "status": "ready" if rows else "watch",
        "summary": f"{len(rows)} orchestration(s); {len(active)} active",
        "count": len(rows),
        "active": len(active),
        "items": items,
    }


def _memory_governance(payload: dict[str, Any]) -> dict[str, Any]:
    inbox = payload.get("inbox") if isinstance(payload.get("inbox"), dict) else {}
    counts = payload.get("counts") if isinstance(payload.get("counts"), dict) else {}
    if not counts:
        counts = {key: len(value) for key, value in inbox.items() if isinstance(value, list)}
    pending_contradictions = counts.get("contradictions", 0)
    promotable = counts.get("promotable", 0)
    proposed = counts.get("proposed", 0)
    memory_status = str(payload.get("status") or ("blocked" if pending_contradictions else ("watch" if promotable or proposed else "ready")))
    return {
        "status": memory_status,
        "summary": payload.get("summary") or f"{proposed} proposed; {promotable} promotable; {pending_contradictions} contradiction(s)",
        "counts": counts,
        "path": payload.get("path") or "",
        "proposed": _compact_memory_items(inbox.get("proposed")),
        "promotable": _compact_memory_items(inbox.get("promotable")),
        "contradictions": _compact_contradictions(inbox.get("contradictions")),
        "quarantined": _compact_memory_items(inbox.get("quarantined")),
    }


def _compact_memory_items(rows: object) -> list[dict[str, Any]]:
    result = []
    for row in _rows(rows)[:5]:
        result.append(
            {
                "id": row.get("id"),
                "layer": row.get("layer"),
                "scope_id": row.get("scope_id"),
                "kind": row.get("kind"),
                "role": row.get("role"),
                "status": row.get("status"),
                "promotion_state": row.get("promotion_state"),
                "claim": row.get("claim"),
                "confidence": row.get("confidence"),
            }
        )
    return result


def _compact_contradictions(rows: object) -> list[dict[str, Any]]:
    result = []
    for row in _rows(rows)[:5]:
        result.append(
            {
                "contradiction_id": row.get("contradiction_id"),
                "status": row.get("status"),
                "reason": row.get("reason"),
                "claim": row.get("claim"),
                "conflicting_claim": row.get("conflicting_claim"),
                "quarantine_target": row.get("quarantine_target"),
            }
        )
    return result


def _mcp_summary(workspace: Path, ecosystem: dict[str, Any]) -> dict[str, Any]:
    from ...api.mcp import mcp_summary

    return mcp_summary(workspace, ecosystem=ecosystem)


def _runtime_config(workspace: Path) -> dict[str, Any]:
    try:
        config = load_runtime_config(workspace)
    except Exception:
        return {"gate": "", "roles": {}}
    return config if isinstance(config, dict) else {"gate": "", "roles": {}}


def _role_routes(workspace: Path, runtime: dict[str, Any], provider_health: dict[str, Any]) -> list[dict[str, Any]]:
    configured_roles = runtime.get("roles", {}) if isinstance(runtime.get("roles"), dict) else {}
    cli = runtime.get("cli", {}) if isinstance(runtime.get("cli"), dict) else {}
    fallback = [str(item) for item in cli.get("fallback", [])] if isinstance(cli.get("fallback"), list) else ["mock"]
    fallback_provider = next((provider for provider in fallback if provider), "mock")
    ready = _provider_names(provider_health.get("ready"))
    partial = _provider_names(provider_health.get("partial"))
    unavailable = _provider_names(provider_health.get("unavailable"))
    rows = []
    for role in MODEL_ROLE_ORDER:
        configured = str(configured_roles.get(role) or "").strip()
        provider = configured or fallback_provider
        status = "ready" if provider in ready or provider == "mock" else ("partial" if provider in partial else ("unavailable" if provider in unavailable else "unknown"))
        rows.append(
            {
                "role": role,
                "label": role.replace("_", " "),
                "ability": MODEL_ROLE_DESCRIPTIONS[role],
                "configured_provider": configured or "auto",
                "fallback_provider": fallback_provider,
                "provider": provider,
                "readiness": status,
                "ready": status == "ready",
                "setup_hint": f"muxdev setup --project then set roles.{role} = <provider>",
                "doctor_hint": "muxdev provider doctor",
                "config_key": f"roles.{role}",
            }
        )
    return rows


def _provider_names(value: object) -> set[str]:
    names: set[str] = set()
    rows = value if isinstance(value, list) else []
    for row in rows:
        if isinstance(row, dict):
            provider = str(row.get("provider") or row.get("name") or "").strip()
        else:
            provider = str(row or "").strip()
        if provider:
            names.add(provider)
    return names


def _workflow_templates(workspace: Path) -> dict[str, Any]:
    return {
        "templates": [plugin.to_dict() for plugin in list_workflow_plugins()],
        "definitions": _workflow_definitions(workspace),
        "config_key": "workflow_plugins",
        "sources": ["builtin workflow templates", "project workflow_plugins config", "configured workflow definitions"],
    }


def _workflow_definitions(workspace: Path) -> list[dict[str, Any]]:
    workflows = load_config(workspace).get("workflows", {})
    rows: list[dict[str, Any]] = []
    if not isinstance(workflows, dict):
        return rows
    for name, definition in workflows.items():
        if str(name) not in PUBLIC_WORKFLOWS:
            continue
        if not isinstance(definition, dict):
            continue
        stages = _stage_rows(definition)
        roles = _model_roles_from_stages(stages)
        rows.append(
            {
                "id": str(name),
                "name": str(definition.get("name") or name),
                "description": str(definition.get("description") or ""),
                "best_for": _workflow_best_for(str(name)),
                "stage_count": len(stages),
                "roles": roles,
                "model_roles": roles,
                "stages": stages,
                "human_gates": _human_gates_from_stages(stages),
                "delivery_gates": _delivery_gates_from_stages(stages),
            }
        )
    return rows


def _skills_catalog(workspace: Path) -> dict[str, Any]:
    try:
        catalog = build_skill_catalog(workspace).to_dict()
    except Exception as exc:  # pragma: no cover - bad skill metadata should not break dashboard
        catalog = {"skills": [], "error": str(exc)}
    try:
        lock = verify_skill_lock(workspace)
    except Exception as exc:  # pragma: no cover
        lock = {"valid": False, "errors": [str(exc)], "skills": []}
    return {"catalog": catalog, "lock": lock}


def _default_workflow(workspace: Path) -> str:
    return "dev"


def _workflow_best_for(name: str) -> list[str]:
    return {
        "design": ["Full design planning", "Reviewed design contracts before implementation"],
        "dev-lite": ["Small, low-risk code changes", "Prototype tasks with smoke-test verification"],
        "design-lite": ["Lightweight design briefs", "Early architecture clarification"],
        "dev": ["Standard implementation work", "Plan-review-test-delivery gated changes"],
        "dev-new": ["New project scaffolding", "Zero-to-one implementation setup"],
        "fix": ["Bug fixes with targeted tests", "Regression repair"],
        "refactor": ["Risk-aware restructuring", "Behavior-preserving cleanup"],
        "review": ["Read-only quality review", "Blocker and regression checks"],
        "test": ["Verification-focused tasks", "Test strategy and evidence collection"],
        "docs": ["Documentation updates", "Handoff and release note work"],
        "software-dev": ["Legacy software development workflow", "Compatibility with older run data"],
    }.get(name, ["General muxdev workflow execution"])


def _task_workspace(task: dict[str, Any], fallback: Path) -> Path:
    value = task.get("workspace") or ""
    try:
        return Path(str(value or fallback)).expanduser().resolve()
    except OSError:
        return fallback


def _task_with_dashboard_context(task: dict[str, Any], fallback: Path, hidden_tasks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    task_id = str(task.get("task_id") or task.get("run_id") or "")
    project_path = _task_workspace(task, fallback)
    project_id = _project_id(project_path)
    return {
        **task,
        "task_id": task_id,
        "run_id": task_id,
        "task_title": str(task.get("task") or task.get("title") or task_id or "muxdev task"),
        "project_id": project_id,
        "project_name": project_path.name or str(project_path),
        "project_path": str(project_path),
        "hidden": task_id in hidden_tasks,
        "hidden_at": hidden_tasks.get(task_id, {}).get("hidden_at"),
    }


def _hidden_project_ids(hidden_projects: dict[str, dict[str, Any]]) -> set[str]:
    ids = {str(project_id) for project_id in hidden_projects}
    for metadata in hidden_projects.values():
        path = metadata.get("path") if isinstance(metadata, dict) else None
        if path:
            ids.add(_project_id(_resolve_path(str(path))))
    return ids


def _resolve_path(value: str) -> Path:
    try:
        return Path(value).expanduser().resolve()
    except OSError:
        return Path(value)


def _project_id(path: Path) -> str:
    normalized = str(path).replace("\\", "/").lower()
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
    return f"project_{digest}"


def _task_risk(task: dict[str, Any]) -> str:
    if str(task.get("status") or "") in {"blocked", "aborted", "failed"} or int(task.get("errors") or 0):
        return "high"
    if int(task.get("pending_approvals") or 0) or int(task.get("pending_provider_actions") or 0):
        return "medium"
    if float(task.get("cost_usd") or 0) > 0.5:
        return "medium"
    return "low"


def _rows(value: object) -> list[dict[str, Any]]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def _json_list(value: object) -> list[object]:
    try:
        parsed = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _write_hidden_projects(path: Path, projects: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"projects": projects}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_hidden_tasks(path: Path, tasks: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tasks": tasks}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
