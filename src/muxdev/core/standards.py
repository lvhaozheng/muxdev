"""Lightweight engineering standards shared by policy, evidence, and UI.

The catalog is intentionally small.  It gives muxdev a common language for
severity, runtime risk, and evidence strength without importing a heavy external
governance framework into the first implementation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


SEVERITY_LEVELS: dict[str, dict[str, str]] = {
    "P0": {"label": "blocker", "description": "Must be resolved before delivery."},
    "P1": {"label": "must-fix", "description": "Important defect or missing control."},
    "P2": {"label": "should-fix", "description": "Useful improvement or medium concern."},
    "P3": {"label": "info", "description": "Informational signal or low concern."},
}

RISK_LEVELS: dict[str, dict[str, str]] = {
    "R0": {"label": "read-only", "description": "Inspection-only and non-mutating."},
    "R1": {"label": "local/reversible", "description": "Local, bounded, and easy to retry."},
    "R2": {"label": "workspace write/reviewable", "description": "Workspace change with reviewable diff or rollback."},
    "R3": {"label": "high risk", "description": "Destructive, external, security-sensitive, or budget-sensitive."},
}

EVIDENCE_LEVELS: dict[str, dict[str, str]] = {
    "E0": {"label": "missing", "description": "No useful evidence recorded."},
    "E1": {"label": "artifact/trace", "description": "Trace or artifact exists."},
    "E2": {"label": "reproducible check", "description": "Test or deterministic verification passed."},
    "E3": {"label": "independent review", "description": "Independent review or validator accepted the result."},
}

STANDARD_CATALOG: dict[str, dict[str, dict[str, str]]] = {
    "severity": SEVERITY_LEVELS,
    "risk": RISK_LEVELS,
    "evidence": EVIDENCE_LEVELS,
}

RISK_ORDER = {"R0": 0, "R1": 1, "R2": 2, "R3": 3}
EVIDENCE_ORDER = {"E0": 0, "E1": 1, "E2": 2, "E3": 3}
SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


@dataclass(frozen=True)
class StandardDecision:
    """Classification attached to policy and evidence records."""

    standard_id: str
    severity: str
    risk_level: str
    evidence_level: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "standard_id": self.standard_id,
            "severity": self.severity,
            "risk_level": self.risk_level,
            "evidence_level": self.evidence_level,
            "reason": self.reason,
        }


READ_ONLY_COMMANDS = (
    "git status",
    "git diff",
    "git log",
    "git show",
    "rg ",
    "rg",
    "python --version",
    "node --version",
)

REPRODUCIBLE_CHECK_PATTERNS = (
    re.compile(r"^(python\s+-m\s+)?pytest(\s|$)"),
    re.compile(r"^(npm|pnpm|yarn)\s+(test|run\s+test)(\s|$)"),
    re.compile(r"^ruff(\s|$)"),
    re.compile(r"^mypy(\s|$)"),
    re.compile(r"^pyright(\s|$)"),
    re.compile(r"^tsc(\s|$)"),
)

DESTRUCTIVE_PATTERNS = (
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\bRemove-Item\b.*\b-Recurse\b", re.IGNORECASE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"\bdd\s+if=", re.IGNORECASE),
    re.compile(r"\bformat\b", re.IGNORECASE),
    re.compile(r"\bgit\s+reset\s+--hard\b", re.IGNORECASE),
    re.compile(r"\bgit\s+clean\s+-[^\s]*f", re.IGNORECASE),
)

EXTERNAL_SIDE_EFFECT_PATTERNS = (
    re.compile(r"\bgit\s+push\b", re.IGNORECASE),
    re.compile(r"\bgh\s+pr\s+(create|merge|close)\b", re.IGNORECASE),
    re.compile(r"\b(curl|wget|Invoke-WebRequest)\b", re.IGNORECASE),
    re.compile(r"\b(kubectl|aws|gcloud|az)\b", re.IGNORECASE),
)

SECRET_PATTERNS = (
    re.compile(r"\b(secret|token|password|credential|api[_-]?key)\b", re.IGNORECASE),
)


def catalog_payload() -> dict[str, Any]:
    """Return a JSON-friendly copy of the lightweight standards catalog."""

    return {
        "contract_version": "muxdev.standards.v1",
        "severity": SEVERITY_LEVELS,
        "risk": RISK_LEVELS,
        "evidence": EVIDENCE_LEVELS,
    }


def classify_shell_command(command: str) -> StandardDecision:
    normalized = " ".join(command.strip().split())
    if any(pattern.search(normalized) for pattern in DESTRUCTIVE_PATTERNS):
        return StandardDecision("R3", "P0", "R3", "E1", "destructive shell command")
    if any(pattern.search(normalized) for pattern in SECRET_PATTERNS):
        return StandardDecision("R3", "P1", "R3", "E1", "security-sensitive shell command")
    if any(pattern.search(normalized) for pattern in EXTERNAL_SIDE_EFFECT_PATTERNS):
        return StandardDecision("R3", "P1", "R3", "E1", "external side-effect shell command")
    if any(pattern.search(normalized) for pattern in REPRODUCIBLE_CHECK_PATTERNS):
        return StandardDecision("E2", "P3", "R1", "E2", "reproducible local verification command")
    if any(normalized == prefix or normalized.startswith(prefix) for prefix in READ_ONLY_COMMANDS):
        return StandardDecision("R0", "P3", "R0", "E1", "read-only shell command")
    return StandardDecision("R2", "P2", "R2", "E1", "reviewable local shell command")


def classify_approval_type(approval_type: str, *, command: str | None = None, subject: dict[str, object] | None = None) -> StandardDecision:
    if command:
        return classify_shell_command(command)
    key = str(approval_type or "").lower()
    if key in {"external", "destructive", "security", "secret", "secrets", "cost", "budget", "high_risk"}:
        return StandardDecision("R3", "P1", "R3", "E1", f"{approval_type} requires human confirmation")
    if key == "shell" and subject and subject.get("command"):
        return classify_shell_command(str(subject["command"]))
    if key in {"write", "merge"}:
        return StandardDecision("R2", "P2", "R2", "E1", f"{approval_type} is reviewable with diff evidence")
    if key in {"plan", "design"}:
        return StandardDecision("R1", "P3", "R1", "E1", f"{approval_type} is a reviewable planning checkpoint")
    return StandardDecision("R2", "P2", "R2", "E1", f"{approval_type} is a reviewable policy gate")


def event_standard(kind: str, status: str, metrics: dict[str, object] | None = None, tags: list[str] | None = None) -> StandardDecision:
    metrics = metrics or {}
    tags = tags or []
    if status in {"blocked", "failed", "rejected"}:
        severity = "P0" if metrics.get("severity") == "high" or status in {"blocked", "rejected"} else "P1"
        return StandardDecision(severity, severity, "R3" if severity == "P0" else "R2", "E1", f"{kind} recorded {status}")
    if kind == "test" and status == "passed":
        return StandardDecision("E2", "P3", "R1", "E2", "test evidence passed")
    if kind == "review" and status == "passed" and ("validator" in tags or "semantic_merge" in tags):
        return StandardDecision("E3", "P3", "R2", "E3", "independent review accepted")
    if kind == "review" and status == "passed":
        return StandardDecision("E3", "P3", "R2", "E3", "review evidence passed")
    if kind == "approval":
        return StandardDecision("R3" if status == "missing" else "R2", "P2", "R2", "E1", "approval evidence recorded")
    if kind in {"task", "change", "artifact", "stage", "runtime", "policy"}:
        return StandardDecision("E1", "P3", "R1", "E1", f"{kind} trace evidence recorded")
    return StandardDecision("E1", "P3", "R1", "E1", "trace evidence recorded")


def standard_scores(events: list[Any]) -> dict[str, Any]:
    """Summarize P/R/E annotations across EvidenceEvent-like objects."""

    severity_counts = {key: 0 for key in SEVERITY_LEVELS}
    risk_counts = {key: 0 for key in RISK_LEVELS}
    evidence_counts = {key: 0 for key in EVIDENCE_LEVELS}
    for event in events:
        severity = str(getattr(event, "severity", "") or "P3")
        risk = str(getattr(event, "risk_level", "") or "R1")
        evidence = str(getattr(event, "evidence_level", "") or "E1")
        if severity in severity_counts:
            severity_counts[severity] += 1
        if risk in risk_counts:
            risk_counts[risk] += 1
        if evidence in evidence_counts:
            evidence_counts[evidence] += 1
    max_risk = max((key for key, value in risk_counts.items() if value), key=lambda key: RISK_ORDER[key], default="R0")
    max_evidence = max((key for key, value in evidence_counts.items() if value), key=lambda key: EVIDENCE_ORDER[key], default="E0")
    highest_severity = min((key for key, value in severity_counts.items() if value), key=lambda key: SEVERITY_ORDER[key], default="P3")
    return {
        "severity": severity_counts,
        "risk": risk_counts,
        "evidence": evidence_counts,
        "highest_severity": highest_severity,
        "max_risk": max_risk,
        "max_evidence": max_evidence,
        "meets_minimum": EVIDENCE_ORDER[max_evidence] >= EVIDENCE_ORDER["E2"] and highest_severity != "P0",
    }
