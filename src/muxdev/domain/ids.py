"""Domain-owned identifiers."""

from __future__ import annotations

from time import time


def new_run_id() -> str:
    """Create a sortable run id based on wall-clock milliseconds."""
    return "run_" + str(int(time() * 1000))
