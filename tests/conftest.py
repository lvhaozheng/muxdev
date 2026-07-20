from __future__ import annotations

from pathlib import Path
import shutil
from uuid import uuid4

import pytest


@pytest.fixture()
def workspace() -> Path:
    path = Path(".test_workspaces") / f"v3_{uuid4().hex}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        for item in path.rglob("*"):
            try:
                item.chmod(0o700 if item.is_dir() else 0o600)
            except OSError:
                pass
        shutil.rmtree(path, ignore_errors=True)
