"""Multi-repo design/dev orchestration planner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..storage import Blackboard


def plan_multi_repo_orchestration(
    workspace: Path,
    *,
    repos: list[Path],
    task: str,
    mode: str = "design",
    orchestration_id: str | None = None,
    run_id: str | None = None,
    blackboard: Blackboard | None = None,
) -> dict[str, Any]:
    """Create a local plan for coordinating design/dev across repositories."""
    if mode not in {"design", "dev"}:
        raise ValueError("mode must be design or dev")
    orchestration_id = orchestration_id or "mrepo_" + uuid4().hex[:10]
    root = workspace / ".muxdev" / "multi-repo"
    root.mkdir(parents=True, exist_ok=True)
    repo_rows = [_repo_row(repo, task=task, mode=mode) for repo in repos]
    payload: dict[str, Any] = {
        "contract_version": "muxdev.multi_repo_orchestration.v1",
        "orchestration_id": orchestration_id,
        "run_id": run_id,
        "workspace": str(workspace),
        "mode": mode,
        "task": task,
        "status": "planned",
        "repos": repo_rows,
    }
    path = root / f"{orchestration_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    payload["plan_path"] = str(path)
    if blackboard is not None:
        blackboard.add_multi_repo_orchestration(
            orchestration_id=orchestration_id,
            run_id=run_id,
            workspace=workspace,
            mode=mode,
            task=task,
            status="planned",
            repos=repo_rows,
            plan_path=path,
        )
    return payload


def _repo_row(repo: Path, *, task: str, mode: str) -> dict[str, Any]:
    resolved = repo.resolve()
    exists = resolved.exists()
    command = f'muxdev {mode} "{task}"'
    return {
        "path": str(resolved),
        "exists": exists,
        "workflow": "design" if mode == "design" else "dev",
        "command": command,
        "markers": _repo_markers(resolved) if exists else [],
        "status": "ready" if exists else "missing",
    }


def _repo_markers(repo: Path) -> list[str]:
    return [name for name in ("pyproject.toml", "package.json", "go.mod", "Cargo.toml", ".git") if (repo / name).exists()]
