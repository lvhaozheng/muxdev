"""Plugin extension contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping


HookHandler = Callable[["PluginContext"], None]


@dataclass(frozen=True)
class PluginContext:
    workspace: Path
    run_id: str | None = None
    stage_id: str | None = None
    payload: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class HookSpec:
    name: str
    handler: HookHandler


@dataclass(frozen=True)
class Plugin:
    name: str
    trust_state: str = "untrusted"
    hooks: tuple[HookSpec, ...] = ()
    capabilities: tuple[str, ...] = ()
    providers: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
