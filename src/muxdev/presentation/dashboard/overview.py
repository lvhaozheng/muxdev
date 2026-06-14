"""Project-oriented Mission Control dashboard overview read model."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from ...config.loader import load_config
from ...config.runtime import GATES, PROFILES, load_runtime_config
from ...services.product_experience import budget_panel, git_safety_panel, project_context_status, rules_skills_panel
from ...services.skills import build_skill_catalog, verify_skill_lock
from ...services.ux import build_ux_overview
from ...services.validation import list_validation_experiments
from ...services.workflow_plugins import list_workflow_plugins


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
        "global_config": _global_config(workspace, visible_tasks, provider_health, ecosystem) if include_global_config else {},
        "global_config_deferred": not include_global_config,
        "artifact_center": action_overview["artifact_center"],
        "validation": {"experiments": list_validation_experiments(workspace)[:8]},
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
        groups.append(
            {
                "id": name,
                "name": str(definition.get("name") or name) if isinstance(definition, dict) else name,
                "task_count": len(workflow_tasks),
                "stage_count": len(stages),
                "roles": [row["role"] for row in role_groups],
                "stages": stages,
                "role_groups": role_groups,
            }
        )
    return groups


def _stage_rows(definition: object) -> list[dict[str, Any]]:
    stages = definition.get("stages", []) if isinstance(definition, dict) else []
    rows = []
    for stage in stages if isinstance(stages, list) else []:
        if not isinstance(stage, dict):
            continue
        rows.append(
            {
                "id": stage.get("id"),
                "role": stage.get("role") or stage.get("type") or "gate",
                "type": stage.get("type", "agent"),
                "deps": stage.get("deps", []),
                "read_only": bool(stage.get("read_only")),
                "allow_write": bool(stage.get("allow_write")),
                "allow_shell": bool(stage.get("allow_shell")),
            }
        )
    return rows


def _role_groups(stages: list[dict[str, Any]], tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    roles = []
    for stage in stages:
        role = str(stage.get("role") or "task")
        if role not in roles:
            roles.append(role)
    task_cards = []
    for task in tasks:
        role = _task_role(task, stages)
        if role not in roles:
            roles.append(role)
        task_cards.append(_task_card(task, role))
    return [{"role": role, "tasks": [card for card in task_cards if card["role"] == role]} for role in roles]


def _task_card(task: dict[str, Any], role: str) -> dict[str, Any]:
    task_id = str(task.get("task_id") or task.get("run_id") or "")
    return {
        "task_id": task_id,
        "run_id": task_id,
        "title": str(task.get("task") or task_id or "muxdev task"),
        "status": task.get("status"),
        "role": role,
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
        "cost_usd": task.get("cost_usd", 0),
        "tokens": task.get("tokens", 0),
        "pending_approvals": task.get("pending_approvals", 0),
        "pending_provider_actions": task.get("pending_provider_actions", 0),
        "errors": task.get("errors", 0),
        "error_summary": task.get("error_summary"),
        "profile": task.get("profile") or "",
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
    for stage in stages:
        if str(stage.get("id") or "") == current:
            return str(stage.get("role") or "task")
    status = str(task.get("status") or "")
    if status == "completed":
        return "done"
    if status in {"blocked", "aborted", "failed"} or int(task.get("errors") or 0):
        return "recovery"
    return str(stages[0].get("role") or "task") if stages else "task"


def _project_config(project: Path, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        rules = rules_skills_panel(project)
    except Exception as exc:  # pragma: no cover - defensive for broken project configs
        rules = {"error": str(exc), "profile": "", "gate": "", "roles": {}, "skills": [], "commands": []}
    return {
        "roles": rules.get("roles", {}),
        "profile": rules.get("profile"),
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
        "role_templates": _role_templates(workspace, runtime),
        "workflow_templates": _workflow_templates(),
        "skills_catalog": _skills_catalog(workspace),
        "providers": provider_health,
        "budget": budget_panel(tasks),
        "safety": {
            "profile": runtime.get("profile"),
            "gate": runtime.get("gate"),
            "gates": GATES,
            "git": git_safety_panel(workspace),
        },
    }


def _mcp_summary(workspace: Path, ecosystem: dict[str, Any]) -> dict[str, Any]:
    from ...api.mcp import mcp_summary

    return mcp_summary(workspace, ecosystem=ecosystem)


def _runtime_config(workspace: Path) -> dict[str, Any]:
    try:
        config = load_runtime_config(workspace)
    except Exception:
        return {"profile": "", "gate": "", "roles": {}}
    return config if isinstance(config, dict) else {"profile": "", "gate": "", "roles": {}}


def _role_templates(workspace: Path, runtime: dict[str, Any]) -> list[dict[str, Any]]:
    configured_roles = runtime.get("roles", {}) if isinstance(runtime.get("roles"), dict) else {}
    workflows = load_config(workspace).get("workflows", {})
    rows = []
    for name, profile in PROFILES.items():
        workflow_name = str(profile.get("workflow") or "")
        workflow = workflows.get(workflow_name, {}) if isinstance(workflows, dict) else {}
        stages = _stage_rows(workflow)
        roles = [str(role) for role in profile.get("roles", []) if role]
        rows.append(
            {
                "name": name,
                "workflow": workflow_name,
                "roles": roles,
                "providers": {role: configured_roles.get(role, "auto") for role in roles},
                "stages": [stage["id"] for stage in stages],
                "non_interactive": bool(profile.get("non_interactive")),
            }
        )
    return rows


def _workflow_templates() -> dict[str, Any]:
    return {
        "templates": [plugin.to_dict() for plugin in list_workflow_plugins()],
        "config_key": "workflow_plugins",
        "sources": ["builtin workflow templates", "project workflow_plugins config"],
    }


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
    config = _runtime_config(workspace)
    profile = str(config.get("profile") or "squad")
    if profile in PROFILES:
        return str(PROFILES[profile].get("workflow") or "dev")
    return "dev"


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
