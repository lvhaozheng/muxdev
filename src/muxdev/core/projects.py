"""Project workspace resolution helpers."""

from __future__ import annotations

from pathlib import Path


def resolve_project_root(workspace: Path) -> Path:
    """Return the stable project root for a workspace or nested project path."""
    root = _resolve(workspace)
    if _is_generated_design_dir(root):
        outer_root = next(
            (candidate for candidate in _candidate_roots(root)[1:] if _has_project_marker(candidate)),
            None,
        )
        if outer_root is not None:
            return outer_root
    for candidate in _candidate_roots(root):
        if _has_project_marker(candidate):
            return candidate
    return root


def _candidate_roots(path: Path) -> list[Path]:
    return [path, *path.parents]


def _has_project_marker(path: Path) -> bool:
    return (path / ".git").exists() or (path / ".muxdev").is_dir()


def _resolve(path: Path) -> Path:
    try:
        return Path(path).expanduser().resolve()
    except OSError:
        return Path(path).expanduser()


def _is_generated_design_dir(path: Path) -> bool:
    parts = tuple(part.lower() for part in path.parts)
    return len(parts) >= 2 and parts[-2:] == ("docs", "design")
