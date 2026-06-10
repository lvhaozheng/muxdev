"""Finalization helpers for completed or blocked runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..services.reports import generate_final_report


@dataclass(frozen=True)
class Finalizer:
    run_dir: Path
    run_id: str
    blackboard: Any

    def report(self) -> Path:
        return generate_final_report(self.run_dir, self.run_id, self.blackboard)
