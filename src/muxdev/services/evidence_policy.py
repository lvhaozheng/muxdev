"""Load the single project-overridable EvidencePolicy source."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..config.loader import load_config
from ..models.evidence import EvidencePolicy, EvidenceRequirement


def load_evidence_policy(
    workspace: Path,
    *,
    workflow: str,
    profile: str = "standard",
) -> EvidencePolicy:
    config = load_config(workspace).get("evidence_policies", {})
    override = _project_override(workspace)
    if override:
        config = _merge_policy_config(config, override)
    if not isinstance(config, dict):
        raise ValueError("evidence_policies must be a mapping")
    definitions = config.get("requirements", {})
    policies = config.get("policies", {})
    workflow_profiles = policies.get(workflow, {}) if isinstance(policies, dict) else {}
    requirement_ids = workflow_profiles.get(profile) if isinstance(workflow_profiles, dict) else None
    if not isinstance(requirement_ids, list):
        raise ValueError(f"unknown EvidencePolicy: {workflow}/{profile}")
    requirements = [_requirement(str(identifier), definitions) for identifier in requirement_ids]
    return EvidencePolicy(
        policy_id=f"muxdev.{workflow}.{profile}",
        version=3,
        requirements=requirements,
    )


def _requirement(identifier: str, definitions: Any) -> EvidenceRequirement:
    if not isinstance(definitions, dict) or not isinstance(definitions.get(identifier), dict):
        raise ValueError(f"undefined EvidenceRequirement: {identifier}")
    return EvidenceRequirement(id=identifier, **definitions[identifier])


def _project_override(workspace: Path) -> dict[str, Any]:
    for path in (workspace / "evidence-policy.yaml", workspace / ".muxdev" / "evidence-policy.yaml"):
        if not path.is_file():
            continue
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            raise ValueError(f"project EvidencePolicy must be a mapping: {path}")
        nested = payload.get("evidence_policies")
        return nested if isinstance(nested, dict) else payload
    return {}


def _merge_policy_config(base: Any, override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base) if isinstance(base, dict) else {}
    for key in ("requirements", "policies"):
        current = result.get(key) if isinstance(result.get(key), dict) else {}
        incoming = override.get(key) if isinstance(override.get(key), dict) else {}
        result[key] = {**current, **incoming}
    return result
