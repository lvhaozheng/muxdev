"""Runtime context contract passed across kernel components."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..domain import RunSpec


@dataclass
class RunContext:
    spec: RunSpec
    run_dir: Path
    worktree: Path
    trace: Any
    repos: Any
    context_snapshot: dict[str, object]
