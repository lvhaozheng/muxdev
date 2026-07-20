"""Deterministic worktree diff and apply operations."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ..core.platforms import hidden_subprocess_kwargs


IGNORED_PARTS = {".git", ".muxdev", ".pytest_cache", "__pycache__"}


def git_output(worktree: Path, arguments: list[str]) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=worktree,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        **hidden_subprocess_kwargs(),
    )
    return result.stdout or ""


def changed_files(worktree: Path) -> list[str]:
    tracked = git_output(worktree, ["diff", "--name-only", "--diff-filter=ACMRT", "--", "."])
    untracked = git_output(worktree, ["ls-files", "--others", "--exclude-standard"])
    paths = {line.strip().replace("\\", "/") for line in (tracked + "\n" + untracked).splitlines() if line.strip()}
    return sorted(path for path in paths if _safe_relative(path))


def diff_text(worktree: Path) -> str:
    content = git_output(worktree, ["diff", "--", "."])
    for relative in changed_files(worktree):
        if relative in content or not (worktree / relative).is_file():
            continue
        body = (worktree / relative).read_text(encoding="utf-8", errors="replace")
        lines = body.splitlines()
        content += f"\ndiff --git a/{relative} b/{relative}\nnew file mode 100644\n--- /dev/null\n+++ b/{relative}\n"
        content += f"@@ -0,0 +1,{len(lines)} @@\n" + "\n".join(f"+{line}" for line in lines) + "\n"
    return content


def apply_changes(worktree: Path, workspace: Path) -> list[str]:
    applied: list[str] = []
    for relative in changed_files(worktree):
        source = (worktree / relative).resolve()
        destination = (workspace / relative).resolve()
        if workspace.resolve() not in destination.parents or not source.is_file():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        applied.append(relative)
    return applied


def _safe_relative(value: str) -> bool:
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts and not any(part in IGNORED_PARTS for part in path.parts)
