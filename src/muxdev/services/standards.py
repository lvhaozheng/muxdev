"""Compatibility entry point for muxdev lightweight standards.

The canonical catalog lives in :mod:`muxdev.core.standards` so runtime and
policy code can depend on it without importing the services layer.  This module
keeps the planned ``services.standards`` path available to dashboard, evidence,
and future extension code.
"""

from __future__ import annotations

from ..core.standards import (
    EVIDENCE_LEVELS,
    EVIDENCE_ORDER,
    RISK_LEVELS,
    RISK_ORDER,
    SECRET_PATTERNS,
    SEVERITY_LEVELS,
    SEVERITY_ORDER,
    STANDARD_CATALOG,
    DESTRUCTIVE_PATTERNS,
    EXTERNAL_SIDE_EFFECT_PATTERNS,
    READ_ONLY_COMMANDS,
    REPRODUCIBLE_CHECK_PATTERNS,
    StandardDecision,
    catalog_payload,
    classify_approval_type,
    classify_shell_command,
    event_standard,
    standard_scores,
)

__all__ = [
    "DESTRUCTIVE_PATTERNS",
    "EVIDENCE_LEVELS",
    "EVIDENCE_ORDER",
    "EXTERNAL_SIDE_EFFECT_PATTERNS",
    "READ_ONLY_COMMANDS",
    "REPRODUCIBLE_CHECK_PATTERNS",
    "RISK_LEVELS",
    "RISK_ORDER",
    "SECRET_PATTERNS",
    "SEVERITY_LEVELS",
    "SEVERITY_ORDER",
    "STANDARD_CATALOG",
    "StandardDecision",
    "catalog_payload",
    "classify_approval_type",
    "classify_shell_command",
    "event_standard",
    "standard_scores",
]
