"""Worktree preparation for isolated muxdev runs.

The runtime prefers real `git worktree` isolation when possible, but keeps
fallback paths for demo/test environments where the workspace is not a Git repo
or a branch/path collision prevents worktree creation.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from fnmatch import fnmatch
from dataclasses import dataclass
from pathlib import Path

from ..config.loader import path_config
from ..core.platforms import hidden_subprocess_kwargs
from ..domain import ReconciliationRequired


@dataclass(frozen=True)
class WorktreeResult:
    """Prepared execution directory plus the strategy used to create it."""

    path: Path
    strategy: str
    message: str


class WorkspacePrepareError(RuntimeError):
    """A recoverable, user-facing failure while preparing an isolated workspace."""

    code = "workspace_prepare_failed"

    def __init__(self, message: str, *, path: Path, suggestion: str) -> None:
        super().__init__(message)
        self.path = Path(path).resolve()
        self.suggestion = suggestion

    def detail(self) -> dict[str, str]:
        return {
            "code": self.code,
            "message": str(self),
            "path": str(self.path),
            "suggestion": self.suggestion,
        }


class WorktreeManager:
    """Create an isolated filesystem workspace for one run."""

    def __init__(self, workspace: Path, worktrees_root: Path | None = None):
        self.workspace = Path(workspace).expanduser().resolve()
        self.worktrees_root = Path(worktrees_root).expanduser().resolve() if worktrees_root is not None else None

    def prepare(
        self,
        run_id: str,
        run_dir: Path,
        *,
        setup_argv: list[str] | None = None,
    ) -> WorktreeResult:
        """Prepare a run worktree using Git when possible, else a filtered copy."""
        if self._is_git_repo(self.workspace) and not self._is_dirty(self.workspace):
            worktree_path = (self.worktrees_root or path_config(self.workspace, "worktrees")) / run_id
            worktree_path.parent.mkdir(parents=True, exist_ok=True)
            if worktree_path.exists():
                if (worktree_path / ".git").exists():
                    return self._finish_prepare(
                        WorktreeResult(
                            worktree_path,
                            "git_worktree_reused",
                            "reused durable run worktree",
                        ),
                        setup_argv,
                    )
                raise ReconciliationRequired(f"existing worktree is incomplete and was not removed: {worktree_path}")
            result = subprocess.run(
                ["git", "worktree", "add", "--detach", str(worktree_path), "HEAD"],
                cwd=self.workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                **hidden_subprocess_kwargs(),
            )
            if result.returncode == 0:
                self._copy_worktree_includes(worktree_path)
                return self._finish_prepare(
                    WorktreeResult(
                        worktree_path, "git_worktree", result.stdout.strip()
                    ),
                    setup_argv,
                )
            raise WorkspacePrepareError(
                f"Git detached worktree creation failed: {result.stderr.strip() or result.stdout.strip()}",
                path=worktree_path,
                suggestion="修复 Git worktree 锁或权限后重试；muxdev 不会静默复制整个干净仓库。",
            )

        worktree_path = run_dir / "worktree"
        if worktree_path.exists():
            if (worktree_path / ".git").exists():
                return self._finish_prepare(
                    WorktreeResult(
                        worktree_path,
                        "workspace_copy_reused",
                        "reused durable copied worktree",
                    ),
                    setup_argv,
                )
            raise ReconciliationRequired(f"existing copied worktree is incomplete and was not removed: {worktree_path}")
        try:
            shutil.copytree(
                self.workspace,
                worktree_path,
                ignore=self._fallback_copy_ignore(run_dir),
            )
        except (PermissionError, shutil.Error, OSError) as exc:
            raise WorkspacePrepareError(
                f"Filtered workspace copy failed: {exc}",
                path=worktree_path,
                suggestion="关闭占用构建产物的进程，确认目录可读后重试；已忽略的生成目录不会参与复制。",
            ) from exc
        self._init_fallback_git_repo(worktree_path, commit_baseline=True)
        return self._finish_prepare(
            WorktreeResult(
                worktree_path,
                "workspace_copy",
                "workspace is not a git repository root; copied workspace",
            ),
            setup_argv,
        )

    def _finish_prepare(
        self,
        result: WorktreeResult,
        setup_argv: list[str] | None,
    ) -> WorktreeResult:
        argv = setup_argv if setup_argv is not None else self._configured_setup_argv()
        if argv:
            completed = subprocess.run(
                argv,
                cwd=result.path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                **hidden_subprocess_kwargs(),
            )
            if completed.returncode != 0:
                raise WorkspacePrepareError(
                    "Worktree setup failed: "
                    + (completed.stderr.strip() or completed.stdout.strip()),
                    path=result.path,
                    suggestion=(
                        "修复 .muxdev/worktree-setup.json 中的 argv 命令；"
                        "Agent 尚未启动，现有 worktree 已保留用于排查。"
                    ),
                )
            return WorktreeResult(
                result.path,
                result.strategy,
                (result.message + "; worktree setup completed").strip("; "),
            )
        return result

    def _configured_setup_argv(self) -> list[str]:
        path = self.workspace / ".muxdev" / "worktree-setup.json"
        if not path.is_file():
            return []
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkspacePrepareError(
                f"Invalid worktree setup configuration: {exc}",
                path=path,
                suggestion="将配置修复为包含非空 argv 字符串数组的 JSON 对象。",
            ) from exc
        raw = value.get("argv") if isinstance(value, dict) else None
        if (
            not isinstance(raw, list)
            or not raw
            or len(raw) > 64
            or any(not isinstance(item, str) or not item for item in raw)
        ):
            raise WorkspacePrepareError(
                "Invalid worktree setup argv",
                path=path,
                suggestion="argv 必须是 1-64 个非空字符串组成的数组，不通过 shell 执行。",
            )
        return list(raw)

    @staticmethod
    def checkpoint(path: Path, message: str) -> bool:
        """Commit the isolated worktree so an accepted delivery becomes its next baseline."""
        add = WorktreeManager._run_git(path, ["add", "--all"])
        if add.returncode != 0:
            return False
        status = WorktreeManager._run_git(path, ["status", "--porcelain"])
        if status.returncode != 0:
            return False
        if not status.stdout.strip():
            return True
        result = WorktreeManager._run_git(
            path,
            [
                "-c", "user.name=muxdev",
                "-c", "user.email=muxdev@example.invalid",
                "commit", "-m", message, "--no-gpg-sign",
            ],
        )
        return result.returncode == 0

    @staticmethod
    def _is_dirty(path: Path) -> bool:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=path,
            capture_output=True,
            text=True,
            check=False,
            **hidden_subprocess_kwargs(),
        )
        return result.returncode != 0 or bool(result.stdout.strip())

    @staticmethod
    def _is_git_repo(path: Path) -> bool:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=path,
            capture_output=True,
            text=True,
            check=False,
            **hidden_subprocess_kwargs(),
        )
        if result.returncode != 0 or not result.stdout.strip():
            return False
        try:
            return Path(result.stdout.strip()).resolve() == path.resolve()
        except OSError:
            return False

    def _fallback_copy_ignore(self, run_dir: Path):
        """Exclude muxdev runtime roots from copy fallbacks to prevent recursion."""
        targets = [run_dir.resolve()]
        if self.worktrees_root is not None:
            targets.append(self.worktrees_root.resolve())

        gitignore_patterns = self._gitignore_patterns()
        include_patterns = self._worktree_include_patterns()
        known_generated = {
            ".git", ".muxdev", ".pytest_cache", ".test_workspaces",
            "__pycache__", "node_modules", ".venv", "venv", ".tox",
            ".mypy_cache", ".ruff_cache", ".next", "dist", "build",
            "coverage", "release-artifacts",
        }

        def ignore(directory: str, names: list[str]) -> set[str]:
            ignored = {
                name for name in names if name in known_generated
            }
            current = Path(directory).resolve()
            for name in names:
                if name.startswith("pytest-cache-files-"):
                    ignored.add(name)
                    continue
                child = (current / name).resolve()
                try:
                    relative = child.relative_to(self.workspace).as_posix()
                except ValueError:
                    relative = name
                if self._matches_include(relative, include_patterns):
                    continue
                if self._matches_gitignore(relative, gitignore_patterns):
                    ignored.add(name)
                    continue
                if _looks_like_muxdev_home(child):
                    ignored.add(name)
                    continue
                for target in targets:
                    if child == target or child in target.parents:
                        ignored.add(name)
            return ignored

        return ignore

    def _worktree_include_patterns(self) -> list[str]:
        path = self.workspace / ".worktreeinclude"
        if not path.is_file():
            return []
        try:
            lines = path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
        except OSError:
            return []
        return [
            line.strip().lstrip("/")
            for line in lines
            if line.strip() and not line.lstrip().startswith("#")
        ]

    @staticmethod
    def _matches_include(relative: str, patterns: list[str]) -> bool:
        normalized = relative.rstrip("/")
        for pattern in patterns:
            pattern = pattern.rstrip("/")
            if (
                fnmatch(normalized, pattern)
                or fnmatch(normalized, pattern + "/**")
                or pattern.startswith(normalized + "/")
            ):
                return True
        return False

    def _copy_worktree_includes(self, destination: Path) -> None:
        patterns = self._worktree_include_patterns()
        if not patterns:
            return
        for source in sorted(self.workspace.rglob("*"), key=lambda item: item.as_posix()):
            if not source.is_file() or source.is_symlink():
                continue
            relative = source.relative_to(self.workspace).as_posix()
            if not self._matches_include(relative, patterns):
                continue
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def _gitignore_patterns(self) -> list[str]:
        path = self.workspace / ".gitignore"
        if not path.is_file():
            return []
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]

    @staticmethod
    def _matches_gitignore(relative: str, patterns: list[str]) -> bool:
        matched = False
        for raw in patterns:
            negated = raw.startswith("!")
            pattern = raw[1:] if negated else raw
            directory_only = pattern.endswith("/")
            pattern = pattern.rstrip("/").lstrip("/")
            if not pattern:
                continue
            candidates = (relative, relative + "/")
            hit = any(
                fnmatch(candidate, pattern)
                or fnmatch(candidate, pattern + "/**")
                or ("/" not in pattern and fnmatch(Path(candidate).name, pattern))
                for candidate in candidates
            )
            if hit or (directory_only and relative.startswith(pattern + "/")):
                matched = not negated
        return matched

    @staticmethod
    def _init_fallback_git_repo(path: Path, *, commit_baseline: bool = False) -> None:
        """Initialize fallback repos so diff/status output stays useful."""
        if shutil.which("git") is not None:
            init = WorktreeManager._run_git(path, ["init"])
            if init.returncode == 0:
                WorktreeManager._write_git_excludes(path)
                if commit_baseline:
                    WorktreeManager._commit_fallback_baseline(path)
                return

        WorktreeManager._init_minimal_git_repo(path)
        WorktreeManager._write_git_excludes(path)

    @staticmethod
    def _run_git(path: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            **hidden_subprocess_kwargs(),
        )

    @staticmethod
    def _commit_fallback_baseline(path: Path) -> None:
        add = WorktreeManager._run_git(path, ["add", "--all"])
        if add.returncode != 0:
            return
        status = WorktreeManager._run_git(path, ["status", "--porcelain"])
        if status.returncode != 0 or not status.stdout.strip():
            return
        WorktreeManager._run_git(
            path,
            [
                "-c",
                "user.name=muxdev",
                "-c",
                "user.email=muxdev@example.invalid",
                "commit",
                "-m",
                "muxdev fallback baseline",
                "--no-gpg-sign",
            ],
        )

    @staticmethod
    def _write_git_excludes(path: Path) -> None:
        info_dir = path / ".git" / "info"
        info_dir.mkdir(parents=True, exist_ok=True)
        exclude_path = info_dir / "exclude"
        patterns = [
            "__pycache__/",
            "*.py[cod]",
            "*.pyc.*",
            ".pytest_cache/",
            "pytest-cache-files-*/",
            ".muxdev/",
        ]
        existing = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
        existing_lines = set(existing.splitlines())
        missing = [pattern for pattern in patterns if pattern not in existing_lines]
        if missing:
            prefix = existing.rstrip("\n")
            content = "\n".join([line for line in [prefix, *missing] if line])
            exclude_path.write_text(content + "\n", encoding="utf-8")

    @staticmethod
    def _init_minimal_git_repo(path: Path) -> None:
        """Create just enough .git metadata when the git binary is unavailable."""
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


def _looks_like_muxdev_home(path: Path) -> bool:
    return (path / "data" / "muxdev.sqlite").exists() or (path / "data" / "muxdev.pid").exists()


__all__ = ["WorkspacePrepareError", "WorktreeManager", "WorktreeResult"]
