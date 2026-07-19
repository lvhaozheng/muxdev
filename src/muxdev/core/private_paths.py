"""Low-level private data roots shared without crossing architecture layers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


def muxdev_private_home(env: Mapping[str, str] | None = None) -> Path:
    values = os.environ if env is None else env
    return Path(values.get("MUXDEV_HOME") or Path.home() / ".muxdev").expanduser()


def muxdev_private_data_dir(env: Mapping[str, str] | None = None) -> Path:
    return muxdev_private_home(env) / "data"
