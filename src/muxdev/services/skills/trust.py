"""Trust normalization for discovered prompt-guidance Skills."""

from __future__ import annotations

from typing import cast

from .model import TrustState


TRUST_STATES = {
    "builtin_trusted",
    "user_trusted",
    "project_trusted",
    "org_trusted",
    "untrusted",
    "needs_review",
    "quarantined",
}
LEGACY = {"auto": "untrusted", "manual": "needs_review", "never": "quarantined", "trusted": "project_trusted"}


def normalize_trust_state(value: object, *, source: str) -> TrustState:
    if value in {None, ""}:
        return "builtin_trusted" if source == "builtin" else "untrusted"
    normalized = LEGACY.get(str(value).strip(), str(value).strip())
    if normalized in TRUST_STATES:
        return cast(TrustState, normalized)
    return "builtin_trusted" if source == "builtin" else "untrusted"
