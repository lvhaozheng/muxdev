"""Safety policy checks for shell commands, approvals, and budget gates.

The policy layer returns decisions; it does not execute commands or mutate run
state. The supervisor uses those decisions to either continue, request human
approval, or block the run.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..models import PolicyDecision
from .redaction import redact


@dataclass(frozen=True)
class PolicyResult:
    """Decision plus human-readable reason for trace/report output."""

    decision: PolicyDecision
    reason: str


@dataclass(frozen=True)
class SafetyPolicy:
    """Configurable guardrails used by the supervisor runtime."""

    shell_allow: tuple[str, ...] = ("pytest", "pytest *", "python -m pytest*", "npm test *", "pnpm test *", "ruff *", "git diff *")
    shell_deny: tuple[str, ...] = ("rm -rf /", "mkfs*", "dd if=*", "format *", "Remove-Item -Recurse C:\\*")
    max_cost_usd: float = 0.5
    approval_types: set[str] = field(default_factory=set)


class SafetyPolicyEngine:
    """Evaluate runtime actions against a SafetyPolicy instance."""

    def __init__(self, policy: SafetyPolicy | None = None):
        self.policy = policy or SafetyPolicy()

    @classmethod
    def from_file(cls, path: Path) -> "SafetyPolicyEngine":
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        defaults = SafetyPolicy()
        return cls(
            SafetyPolicy(
                shell_allow=tuple(data.get("shell_allow", defaults.shell_allow)),
                shell_deny=tuple(data.get("shell_deny", defaults.shell_deny)),
                max_cost_usd=float(data.get("max_cost_usd", defaults.max_cost_usd)),
                approval_types=set(data.get("approval_types", [])),
            )
        )

    def evaluate_shell(self, command: str) -> PolicyResult:
        """Classify a shell command as allowed, approval-required, or denied."""
        normalized = command.strip()
        for pattern in self.policy.shell_deny:
            if fnmatch.fnmatchcase(normalized, pattern):
                return PolicyResult(PolicyDecision.DENY, f"denied by shell policy: {pattern}")
        for pattern in self.policy.shell_allow:
            if fnmatch.fnmatchcase(normalized, pattern):
                return PolicyResult(PolicyDecision.ALLOW, f"allowed by shell policy: {pattern}")
        return PolicyResult(PolicyDecision.APPROVE, "shell command requires approval")

    def requires_approval(self, approval_type: str) -> bool:
        """Return whether a named approval gate is enabled for this run."""
        return approval_type in self.policy.approval_types

    def evaluate_budget(self, current_cost: float, next_cost: float = 0) -> PolicyResult:
        """Check whether the next estimated provider cost fits the run budget."""
        if current_cost + next_cost > self.policy.max_cost_usd:
            return PolicyResult(PolicyDecision.DENY, "budget max_cost_usd exceeded")
        return PolicyResult(PolicyDecision.ALLOW, "budget within limit")


__all__ = ["PolicyDecision", "PolicyResult", "SafetyPolicy", "SafetyPolicyEngine", "redact"]
