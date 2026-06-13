"""Trust policy helpers for skills."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from ...config.runtime import dumps_toml, global_skills_path, project_skills_path
from .model import SkillInfo, TrustState


TRUSTED_STATES = {"builtin_trusted", "user_trusted", "project_trusted", "org_trusted"}
VISIBLE_BUT_MANUAL_STATES = {"untrusted", "needs_review"}
BLOCKED_STATES = {"quarantined"}
LEGACY_TRUST_MAP: dict[str, TrustState] = {
    "auto": "untrusted",
    "manual": "needs_review",
    "never": "quarantined",
    "trusted": "project_trusted",
    "project": "project_trusted",
    "user": "user_trusted",
    "org": "org_trusted",
}


def normalize_trust_state(value: object, *, source: str) -> TrustState:
    if value is None or value == "":
        return default_trust_state(source)
    text = str(value).strip()
    mapped = LEGACY_TRUST_MAP.get(text, text)
    if mapped in TRUSTED_STATES | VISIBLE_BUT_MANUAL_STATES | BLOCKED_STATES:
        return cast(TrustState, mapped)
    return default_trust_state(source)


def default_trust_state(source: str) -> TrustState:
    if source == "builtin":
        return "builtin_trusted"
    return "untrusted"


def can_auto_activate(skill: SkillInfo) -> bool:
    if skill.disabled or skill.trust in BLOCKED_STATES:
        return False
    return bool(skill.auto and skill.trust in TRUSTED_STATES)


def can_manual_activate(skill: SkillInfo) -> bool:
    return not skill.disabled and skill.trust not in BLOCKED_STATES


def set_skill_policy(
    workspace: Path,
    name: str,
    *,
    project: bool = True,
    disabled: bool | None = None,
    trust: str | None = None,
    auto: bool | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, object]:
    """Write project/user skill policy into skills.toml."""
    path = project_skills_path(workspace) if project else global_skills_path(env)
    data = _read_toml(path) if path.exists() else {"version": 1}
    policies = data.setdefault("skill", {})
    if not isinstance(policies, dict):
        policies = {}
        data["skill"] = policies
    policy = policies.setdefault(name, {})
    if not isinstance(policy, dict):
        policy = {}
        policies[name] = policy
    if disabled is not None:
        policy["disabled"] = disabled
    if trust is not None:
        policy["trust"] = normalize_trust_state(trust, source="project" if project else "global")
    if auto is not None:
        policy["auto"] = auto
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_toml(data), encoding="utf-8")
    return {"path": str(path), "name": name, "policy": policy}


def _read_toml(path: Path) -> dict[str, Any]:
    import tomllib

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}
