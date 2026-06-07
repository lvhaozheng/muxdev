"""Worktree preparation for isolated muxdev runs.

The runtime prefers real `git worktree` isolation when possible, but keeps
fallback paths for demo/test environments where the workspace is not a Git repo
or a branch/path collision prevents worktree creation.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..config.loader import path_config
from ..core.platforms import hidden_subprocess_kwargs


@dataclass(frozen=True)
class WorktreeResult:
    """Prepared execution directory plus the strategy used to create it."""

    path: Path
    strategy: str
    message: str


class WorktreeManager:
    """Create an isolated filesystem workspace for one run."""

    def __init__(self, workspace: Path, worktrees_root: Path | None = None):
        self.workspace = workspace
        self.worktrees_root = worktrees_root

    def prepare(self, run_id: str, run_dir: Path) -> WorktreeResult:
        """Prepare a run worktree using Git when possible, else fallback copies."""
        if self._is_git_repo(self.workspace):
            worktree_path = (self.worktrees_root or path_config(self.workspace, "worktrees")) / run_id
            worktree_path.parent.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(
                ["git", "worktree", "add", "-b", f"muxdev/{run_id}", str(worktree_path), "HEAD"],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                **hidden_subprocess_kwargs(),
            )
            if result.returncode == 0:
                return WorktreeResult(worktree_path, "git_worktree", result.stdout.strip())
            # Existing branch/path edge cases should not prevent M1 mock runs.
            fallback = run_dir / "worktree"
            shutil.copytree(self.workspace, fallback, ignore=self._fallback_copy_ignore(run_dir))
            self._init_minimal_git_repo(fallback)
            return WorktreeResult(fallback, "git_worktree_fallback_copy", result.stderr.strip())

        worktree_path = run_dir / "worktree"
        worktree_path.mkdir(parents=True, exist_ok=True)
        self._init_minimal_git_repo(worktree_path)
        return WorktreeResult(worktree_path, "temp_git_repo", "workspace is not a git repo; initialized temp repo")

    @staticmethod
    def _is_git_repo(path: Path) -> bool:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=path,
            capture_output=True,
            text=True,
            check=False,
            **hidden_subprocess_kwargs(),
        )
        return result.returncode == 0 and result.stdout.strip() == "true"

    def _fallback_copy_ignore(self, run_dir: Path):
        """Exclude muxdev runtime roots from copy fallbacks to prevent recursion."""
        targets = [run_dir.resolve()]
        if self.worktrees_root is not None:
            targets.append(self.worktrees_root.resolve())

        def ignore(directory: str, names: list[str]) -> set[str]:
            ignored = {".git", ".muxdev", "__pycache__"}
            current = Path(directory).resolve()
            for name in names:
                child = (current / name).resolve()
                for target in targets:
                    if child == target or child in target.parents:
                        ignored.add(name)
            return ignored

        return ignore

    @staticmethod
    def _init_minimal_git_repo(path: Path) -> None:
        """Create just enough .git metadata for diff-oriented tests."""
        git_dir = path / ".git"
        (git_dir / "objects").mkdir(parents=True, exist_ok=True)
        (git_dir / "refs" / "heads").mkdir(parents=True, exist_ok=True)
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
        (git_dir / "config").write_text(
            "[core]\n"
            "\trepositoryformatversion = 0\n"
            "\tfilemode = false\n"
            "\tbare = false\n"
            "\tlogallrefupdates = true\n",
            encoding="utf-8",
        )
