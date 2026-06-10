"""Compatibility wrapper around the safety policy engine."""

from __future__ import annotations

from dataclasses import dataclass, field

from ...core.safety import SafetyPolicy, SafetyPolicyEngine


@dataclass
class PolicyEngine:
    safety: SafetyPolicyEngine = field(default_factory=lambda: SafetyPolicyEngine(SafetyPolicy()))

    def evaluate_budget(self, current_cost_usd: float, next_cost_usd: float):
        return self.safety.evaluate_budget(current_cost_usd, next_cost_usd)

    def evaluate_shell(self, command: str):
        return self.safety.evaluate_shell(command)
