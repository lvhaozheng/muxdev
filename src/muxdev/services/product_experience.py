"""Product-experience helpers for the P4 Mission Control surface.

This module gathers the user-facing pieces that make muxdev feel closer to a
polished coding-agent product: short setup paths, provider setup guidance,
project context, budget visibility, Git safety, and rules/skills visibility.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from ..core.platforms import hidden_subprocess_kwargs
from ..providers import detect_providers
from ..providers.registry import ProviderProbe


PROJECT_CONTEXT_FILE = "MUXDEV.md"
ONE_LINE_INSTALL = "pipx install muxdev"
ALT_INSTALL = "uv tool install muxdev"


def build_product_experience(
    workspace: Path,
    *,
    tasks: list[dict[str, Any]] | None = None,
    provider_health: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the compact P4 product surface used by CLI, API, and dashboard."""
    workspace = workspace.resolve()
    tasks = tasks or []
    provider_health = provider_health or build_provider_setup_wizard(workspace)["provider_health"]
    return {
        "quickstart": {
            "one_line_install": ONE_LINE_INSTALL,
            "alternatives": [ALT_INSTALL, 'python -m pip install -e ".[test]"'],
            "first_run": ["muxdev setup --project", "muxdev doctor", "muxdev demo --mock", "muxdev"],
        },
        "provider_setup": build_provider_setup_wizard(workspace, provider_health=provider_health),
        "project_context": project_context_status(workspace),
        "task_management": _task_management(tasks),
        "git_safety": git_safety_panel(workspace),
        "rules_skills": rules_skills_panel(workspace),
        "budget": budget_panel(tasks),
        "provider_health": provider_health,
        "web_ui": {
            "dashboard": "muxdev dashboard",
            "api": [
                "GET /api/product/experience",
                "GET /api/ux/overview",
                "GET /api/providers/health",
                "GET /api/setup/status",
            ],
            "ide_plugin": "optional: use the web UI or these APIs from an IDE extension",
        },
    }


def build_provider_setup_wizard(
    workspace: Path,
    *,
    probes: list[ProviderProbe] | None = None,
    provider_health: dict[str, Any] | None = None,
) -> dict[str, Any]:
    probes = detect_providers() if probes is None and provider_health is None else probes
    if provider_health is None:
        rows = [probe.to_dict() for probe in probes or []]
        ready = [row for row in rows if str(row.get("status")) == "ready"]
        partial = [row for row in rows if str(row.get("status")) == "partial"]
        unavailable = [row for row in rows if str(row.get("status")) == "unavailable"]
        provider_health = {
            "ready": [row.get("provider") for row in ready],
            "partial": [row.get("provider") for row in partial],
            "unavailable": [row.get("provider") for row in unavailable],
            "total": len(rows),
            "providers": rows,
            "recommendations": _provider_recommendations(rows),
        }
    providers = provider_health.get("providers", []) if isinstance(provider_health, dict) else []
    steps = []
    for row in providers if isinstance(providers, list) else []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("provider") or "")
        status = str(row.get("status") or "unknown")
        installed = bool(row.get("installed"))
        if name == "mock":
            action = "Run `muxdev demo --mock` to exercise the full product loop offline."
        elif not installed:
            action = f"Run `muxdev provider install {name}` to review the install plan."
        elif status != "ready":
            action = f"Run `muxdev provider account {name}` then `muxdev provider doctor {name}`."
        else:
            action = f"{name} is ready; use `muxdev dev --provider {name} \"task\"`."
        steps.append({"provider": name, "status": status, "installed": installed, "action": action})
    return {
        "workspace": str(workspace.resolve()),
        "provider_health": provider_health,
        "steps": steps,
        "recommended_path": [
            "Start with mock if no provider is ready.",
            "Install or log in to one external provider.",
            "Run provider doctor before assigning roles.",
            "Use role routing only after the default provider works.",
        ],
    }


def project_context_status(workspace: Path) -> dict[str, Any]:
    path = workspace / PROJECT_CONTEXT_FILE
    return {
        "path": str(path),
        "exists": path.exists(),
        "command": "muxdev context --write",
        "preview": _project_context_preview(path),
    }


def write_project_context(
    workspace: Path,
    *,
    config: dict[str, Any] | None = None,
    provider_health: dict[str, Any] | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    path = workspace / PROJECT_CONTEXT_FILE
    if path.exists() and not overwrite:
        return {"path": str(path), "written": False, "exists": True, "reason": "already_exists"}
    if config is None:
        from ..config.runtime import load_runtime_config

        config = load_runtime_config(workspace)
    provider_health = provider_health or build_provider_setup_wizard(workspace)["provider_health"]
    content = render_project_context(workspace, config=config, provider_health=provider_health)
    path.write_text(content, encoding="utf-8")
    return {"path": str(path), "written": True, "exists": True}


def render_project_context(workspace: Path, *, config: dict[str, Any], provider_health: dict[str, Any]) -> str:
    roles = config.get("roles", {}) if isinstance(config.get("roles"), dict) else {}
    memory = config.get("memory", {}) if isinstance(config.get("memory"), dict) else {}
    ready = ", ".join(str(item) for item in provider_health.get("ready", [])) or "mock"
    role_lines = "\n".join(f"- {role}: {provider}" for role, provider in sorted(roles.items())) or "- default: auto"
    return f"""# MUXDEV

This file is muxdev's project context anchor. Keep stable project facts here;
temporary run details belong in Memory Inbox or run artifacts.

## Quick Start

- Install: `{ONE_LINE_INSTALL}` or `{ALT_INSTALL}`
- First demo: `muxdev demo --mock`
- Main console: `muxdev`
- Dashboard: `muxdev dashboard`

## Providers

- Ready providers: {ready}
- Setup wizard: `muxdev provider setup`
- Health check: `muxdev provider detect`

## Role Routing

{role_lines}

## Rules And Gates

- Gate: {config.get("gate", "safe")}
- Approvals: plan/write/shell/merge gates are reviewed in Mission Control.
- Provider actions: muxdev never types yes/no into provider CLIs.

## Memory

- Mode: {memory.get("mode", "evidence-grounded")}
- Long-term project memory must be reviewed before promotion.
- Review queue: `muxdev memory inbox`

## Git Safety

- Review patch: `muxdev diff latest`
- Ship safely: `muxdev ship latest --dry-run`
- Roll back task worktree: `muxdev rollback latest`

## Workspace

- Root: {workspace.resolve()}
"""


def budget_panel(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    total_cost = sum(float(task.get("cost_usd") or 0) for task in tasks)
    total_tokens = sum(int(task.get("tokens") or 0) for task in tasks)
    active = [task for task in tasks if str(task.get("status")) not in {"completed", "blocked", "aborted"}]
    high_cost = [task for task in tasks if float(task.get("cost_usd") or 0) > 0.5]
    return {
        "total_cost_usd": round(total_cost, 4),
        "total_tokens": total_tokens,
        "active_tasks": len(active),
        "high_cost_tasks": len(high_cost),
        "default_task_budget_usd": 0.5,
        "controls": ["--max-cost-usd", "gate=safe", "gate=strict"],
    }


def git_safety_panel(workspace: Path) -> dict[str, Any]:
    inside = _git(["rev-parse", "--is-inside-work-tree"], cwd=workspace)
    if inside["returncode"] != 0:
        return {
            "status": "not_git",
            "inside_worktree": False,
            "commands": ["git init", "muxdev demo --mock"],
            "warning": "Git-native safety works best inside a Git repository.",
        }
    branch = _git(["branch", "--show-current"], cwd=workspace)
    status = _git(["status", "--short"], cwd=workspace)
    dirty_lines = [line for line in str(status.get("stdout") or "").splitlines() if line]
    return {
        "status": "dirty" if dirty_lines else "clean",
        "inside_worktree": True,
        "branch": str(branch.get("stdout") or "").strip() or "detached",
        "dirty_files": dirty_lines[:50],
        "commands": [
            "muxdev diff latest",
            "muxdev ship latest --dry-run",
            "muxdev rollback latest",
            "git restore --source=HEAD -- <file>",
            "git revert <reviewed_commit>",
            "git commit -m \"describe the reviewed muxdev change\"",
        ],
    }


def rules_skills_panel(workspace: Path) -> dict[str, Any]:
    from ..config.runtime import GATES, load_runtime_config
    from ..services.skills import scan_skills

    config = load_runtime_config(workspace)
    try:
        skills = [record.to_dict() for record in scan_skills(workspace, include_disabled=True)]
    except Exception:
        skills = []
    return {
        "gate": config.get("gate"),
        "gates": sorted(GATES),
        "roles": config.get("roles", {}),
        "skills": skills,
        "commands": ["muxdev config", "muxdev skill list", "muxdev skill bind review <skill>", "muxdev skill trust <skill> --level trusted"],
    }


def _task_management(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    columns = {"todo": 0, "running": 0, "waiting": 0, "needs_review": 0, "done": 0, "failed": 0}
    for task in tasks:
        status = str(task.get("status") or "")
        if status in {"completed"}:
            columns["done"] += 1
        elif status in {"blocked", "aborted", "failed"}:
            columns["failed"] += 1
        elif status in {"awaiting_approval", "awaiting_provider_action", "paused_budget"}:
            columns["waiting"] += 1
        elif status in {"needs_review", "review"} or str(task.get("current_stage") or "") == "review":
            columns["needs_review"] += 1
        elif status in {"created", "queued", "pending"}:
            columns["todo"] += 1
        else:
            columns["running"] += 1
    return {
        "kanban_columns": columns,
        "filters": ["provider", "workflow", "status", "branch", "risk", "cost"],
        "commands": ["muxdev tasks", "muxdev dashboard", "muxdev continue latest", "muxdev actions"],
    }


def _provider_recommendations(rows: list[dict[str, Any]]) -> list[str]:
    ready = [row for row in rows if str(row.get("status")) == "ready" and row.get("provider") != "mock"]
    installed_not_ready = [row for row in rows if row.get("installed") and str(row.get("status")) != "ready" and row.get("provider") != "mock"]
    missing = [row for row in rows if not row.get("installed") and row.get("provider") != "mock"]
    if ready:
        return ["Run `muxdev dev --provider <ready-provider> \"task\"` or bind providers by role."]
    if installed_not_ready:
        return [f"Finish login/account setup for {installed_not_ready[0].get('provider')}."]
    if missing:
        return [f"Review install plan for {missing[0].get('provider')}, or start with `muxdev demo --mock`."]
    return ["Use `muxdev demo --mock` to start offline."]


def _project_context_preview(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[:600]


def _git(args: list[str], *, cwd: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            **hidden_subprocess_kwargs(),
        )
    except OSError as exc:
        return {"returncode": 127, "stdout": "", "stderr": str(exc)}
    return {"returncode": result.returncode, "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}
