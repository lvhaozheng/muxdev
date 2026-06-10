"""Plugin hook dispatch."""

from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import HookSpec, PluginContext


@dataclass
class HookBus:
    hooks: dict[str, list[HookSpec]] = field(default_factory=dict)

    def register(self, hook: HookSpec) -> None:
        self.hooks.setdefault(hook.name, []).append(hook)

    def publish(self, name: str, context: PluginContext) -> None:
        for hook in self.hooks.get(name, []):
            hook.handler(context)
