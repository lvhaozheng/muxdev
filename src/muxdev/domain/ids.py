"""Domain-owned identifiers."""

from __future__ import annotations

from time import time
from uuid import uuid4


def new_run_id() -> str:
    """Create a sortable, collision-resistant run id."""
    return f"run_{int(time() * 1000)}_{uuid4().hex[:12]}"
