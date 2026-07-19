"""Project workspace resolution helpers."""

from __future__ import annotations

from pathlib import Path


def canonical_workspace(workspace: Path) -> Path:
    """Return an absolute, existing directory without discovering a parent project."""
    root = _resolve(workspace)
    if not root.exists():
        raise FileNotFoundError(f"workspace does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"workspace is not a directory: {root}")
    return root


def resolve_project_root(workspace: Path, *, user_home: Path | None = None) -> Path:
    """Return the nearest valid project root for an existing directory.

    A home-level ``.muxdev`` directory is global configuration, not a project
    marker. Git remains a strong marker, while a project ``.muxdev`` marker is
    accepted only below or outside the user's home directory itself.
    """
    root = canonical_workspace(workspace)

    home = _resolve(user_home or Path.home())
    candidates = _candidate_roots(root, boundary=home)
    if _is_generated_design_dir(root):
        candidates = candidates[1:]
    for candidate in candidates:
        if _has_project_marker(candidate, user_home=home):
            return candidate
    return root


def _candidate_roots(path: Path, *, boundary: Path | None = None) -> list[Path]:
    candidates = [path, *path.parents]
    if boundary is not None and (path == boundary or boundary in path.parents):
        return candidates[: candidates.index(boundary) + 1]
    return candidates


def _has_project_marker(path: Path, *, user_home: Path) -> bool:
    if (path / ".git").exists():
        return True
    return path != user_home and (path / ".muxdev").is_dir()


def _resolve(path: Path) -> Path:
    return Path(path).expanduser().resolve()


def _is_generated_design_dir(path: Path) -> bool:
    parts = tuple(part.lower() for part in path.parts)
    return len(parts) >= 2 and parts[-2:] == ("docs", "design")
