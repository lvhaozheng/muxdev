"""Creation, integrity checking, and reporting of immutable run policy."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from ..config.loader import load_config
from ..models import RunPolicySnapshot, WorkflowDefinition
from ..models.evidence import EvidencePolicy, canonical_hash
from ..services.capabilities import require_provider_capabilities, resolve_stage_capabilities
from ..services.skills import resolve_stage_skills
from ..services.skills.discovery import scan_skills
from ..services.skills.lock import skill_tree_hash
from ..storage.control import ControlStore
from .workspace import ChangeSet, WorkspaceSnapshot


def freeze_policy_snapshot(
    workspace: Path,
    store: ControlStore,
    *,
    run_id: str,
    workflow: WorkflowDefinition,
    profile: str,
    policy: EvidencePolicy,
    provider_request: str,
    route: Mapping[str, object],
    role_providers: Mapping[str, str],
    workspace_snapshot: WorkspaceSnapshot,
    run_dir: Path,
) -> RunPolicySnapshot:
    config = load_config(workspace)
    _reject_inline_secret_config(config)
    providers = config.get("providers") if isinstance(config.get("providers"), dict) else {}
    stage_capabilities: dict[str, dict[str, object]] = {}
    stage_skills: dict[str, list[dict[str, object]]] = {}
    provider_capability_map: dict[str, dict[str, object]] = {}
    selected_skill_names: set[str] = set()
    selected_providers: set[str] = set(str(value) for value in role_providers.values())
    candidates = route.get("candidates") if isinstance(route.get("candidates"), list) else []
    selected_providers.update(
        str(item.get("provider"))
        for item in candidates
        if isinstance(item, dict) and item.get("eligible") and item.get("provider")
    )
    for key in ("main_provider", "reviewer_provider"):
        if route.get(key):
            selected_providers.add(str(route[key]))
    for stage in workflow.stages:
        if stage.type == "human_gate":
            continue
        grant = resolve_stage_capabilities(workspace, stage)
        stage_capabilities[stage.id] = grant.to_dict()
        stage_provider = _stage_provider(stage.role, route, role_providers) if route.get("main_provider") else None
        if stage_provider:
            selected_providers.add(stage_provider)
            provider_capability_map[stage_provider] = require_provider_capabilities(
                workspace, stage_provider, grant, profile=profile
            )
        skills = resolve_stage_skills(
            workspace,
            stage.default_skills,
            role=stage.role,
            stage_id=stage.id,
            allow_write=stage.allow_write,
            allow_shell=stage.allow_shell,
            grant=grant,
            profile=profile,
        )
        stage_skills[stage.id] = [dict(item) for item in skills]
        selected_skill_names.update(str(item.get("name")) for item in skills)
    discovered = {item.name: item for item in scan_skills(workspace, include_disabled=True)}
    skill_lock_digest = canonical_hash([
        {
            "name": name,
            "source": discovered[name].source,
            "trust": discovered[name].trust,
            "tree": skill_tree_hash(discovered[name]),
        }
        for name in sorted(selected_skill_names)
        if name in discovered
    ])
    provider_definitions = {
        name: dict(providers[name]) for name in sorted(selected_providers)
        if isinstance(providers.get(name), dict)
    }
    secret_names = {name for stage in workflow.stages for name in stage.allowed_secrets}
    for definition in provider_definitions.values():
        runtime = definition.get("runtime") if isinstance(definition.get("runtime"), dict) else {}
        secret_names.update(str(item) for item in runtime.get("auth_env_vars", []))
    snapshot = RunPolicySnapshot(
        run_id=run_id,
        workflow=workflow.name,
        profile=profile,
        evidence_policy_hash=policy.policy_hash,
        config_hash=canonical_hash(config),
        provider_request=provider_request,
        provider_route=dict(route),
        workflow_definition=workflow.model_dump(mode="json"),
        workspace_manifest=workspace_snapshot.to_dict(),
        stage_capabilities=stage_capabilities,
        stage_skills=stage_skills,
        provider_capabilities=provider_capability_map,
        provider_definitions=provider_definitions,
        skill_lock_digest=skill_lock_digest,
        secret_names=sorted(secret_names),
    )
    payload = snapshot.model_dump(mode="json")
    snapshot_hash = canonical_hash(payload)
    path = run_dir / "run-policy-snapshot.json"
    path.write_text(snapshot.model_dump_json(indent=2) + "\n", encoding="utf-8")
    store.freeze_run_policy(run_id, payload, snapshot_hash)
    store.add_artifact(
        run_id,
        name=path.name,
        path=path,
        kind="run_policy_snapshot",
        media_type="application/json",
    )
    return snapshot


def load_policy_snapshot(store: ControlStore, run_id: str) -> RunPolicySnapshot:
    run = store.get_run(run_id) or {}
    metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
    raw = metadata.get("policy_snapshot")
    if not isinstance(raw, dict):
        raise RuntimeError(f"run {run_id} has no frozen policy snapshot")
    actual_hash = canonical_hash(raw)
    if actual_hash != metadata.get("policy_snapshot_hash"):
        raise RuntimeError(f"run {run_id} policy snapshot integrity check failed")
    return RunPolicySnapshot.model_validate(raw)


def freeze_preflight_failure(
    workspace: Path,
    store: ControlStore,
    *,
    run_id: str,
    workflow: WorkflowDefinition,
    profile: str,
    policy: EvidencePolicy,
    provider_request: str,
    route: Mapping[str, object],
    workspace_snapshot: WorkspaceSnapshot,
    run_dir: Path,
) -> RunPolicySnapshot:
    """Freeze enough immutable context to report a fail-closed preflight result."""
    snapshot = RunPolicySnapshot(
        run_id=run_id,
        workflow=workflow.name,
        profile=profile,
        evidence_policy_hash=policy.policy_hash,
        config_hash=canonical_hash(load_config(workspace)),
        provider_request=provider_request,
        provider_route=dict(route),
        workflow_definition=workflow.model_dump(mode="json"),
        workspace_manifest=workspace_snapshot.to_dict(),
        secret_names=sorted({name for stage in workflow.stages for name in stage.allowed_secrets}),
    )
    payload = snapshot.model_dump(mode="json")
    path = run_dir / "run-policy-snapshot.json"
    path.write_text(snapshot.model_dump_json(indent=2) + "\n", encoding="utf-8")
    store.freeze_run_policy(run_id, payload, canonical_hash(payload))
    store.add_artifact(
        run_id, name=path.name, path=path, kind="run_policy_snapshot", media_type="application/json"
    )
    return snapshot


def build_harness_summary(
    store: ControlStore,
    run_id: str,
    snapshot: RunPolicySnapshot,
    change_set: ChangeSet,
) -> dict[str, object]:
    run = store.get_run(run_id) or {}
    metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
    stages = store.stages(run_id)
    mcp: dict[str, dict[str, object]] = {}
    acp: list[dict[str, object]] = []
    for stage_id, grant in snapshot.stage_capabilities.items():
        for reference in grant.get("mcp_tools", []):
            mcp[f"{stage_id}:{reference}"] = {"stage": stage_id, "tool": reference, "status": "configured"}
    for stage in stages:
        result = stage.get("result") if isinstance(stage.get("result"), dict) else {}
        events = result.get("provider_events") if isinstance(result.get("provider_events"), list) else []
        for event in events:
            if not isinstance(event, dict):
                continue
            details = event.get("details") if isinstance(event.get("details"), dict) else {}
            reference = str(details.get("tool_ref") or details.get("tool") or "")
            key = f"{stage['stage_id']}:{reference}"
            if reference and key in mcp and str(event.get("status")) in {"observed", "verified"}:
                mcp[key]["status"] = str(event["status"])
        if result.get("protocol") == "acp":
            acp.append({
                "stage": stage["stage_id"],
                "provider": stage.get("provider"),
                "session_id": result.get("session_id"),
                "event_count": result.get("event_count", 0),
            })
    main_provider = str(snapshot.provider_route.get("main_provider") or "")
    certification = store.latest_certification(main_provider) if main_provider else None
    return {
        "policy_snapshot": {
            "contract_version": snapshot.contract_version,
            "hash": metadata.get("policy_snapshot_hash"),
            "config_hash": snapshot.config_hash,
            "evidence_policy_hash": snapshot.evidence_policy_hash,
            "workspace_manifest_hash": snapshot.workspace_manifest.get("digest"),
        },
        "provider_certification": certification or {},
        "skills": {
            "lock_digest": snapshot.skill_lock_digest,
            "enabled": [
                {
                    "stage": stage_id,
                    "name": item.get("name"),
                    "source": item.get("source"),
                    "trust": item.get("trust"),
                    "reason": "workflow_stage_declaration",
                }
                for stage_id, skills in snapshot.stage_skills.items()
                for item in skills
            ],
        },
        "capability_grants": snapshot.stage_capabilities,
        "mcp": {
            "tools": list(mcp.values()),
            "observability_note": "configured means exposed; provider events promote tools to observed or verified",
        },
        "acp_sessions": acp,
        "verification_commands": {
            stage.id: [item.model_dump(mode="json") for item in stage.verification_commands]
            for stage in WorkflowDefinition.model_validate(snapshot.workflow_definition).stages
            if stage.verification_commands
        },
        "changeset": change_set.to_dict(),
    }


def _stage_provider(
    role: str | None,
    route: Mapping[str, object],
    overrides: Mapping[str, str],
) -> str:
    if role and role in overrides:
        return overrides[role]
    if role in {"review", "secure"} and route.get("reviewer_provider"):
        return str(route["reviewer_provider"])
    return str(route["main_provider"])


def _reject_inline_secret_config(value: object, *, path: str = "config") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower()
            if normalized == "env" and isinstance(child, dict) and child:
                raise ValueError(f"inline MCP/Provider env values are forbidden at {path}.{key}; use env_vars")
            is_name_declaration = normalized.endswith(
                ("env", "env_var", "env_vars", "secret_names", "allowed_secrets")
            )
            if not is_name_declaration and any(term in normalized for term in ("password", "token", "api_key", "secret")):
                if child not in (None, "", [], {}):
                    raise ValueError(f"inline Secret values are forbidden; declare an environment name at {path}.{key}")
            _reject_inline_secret_config(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, str):
                normalized = child.lower()
                sensitive_assignment = any(
                    marker in normalized
                    for marker in ("--api-key=", "--password=", "--secret=", "--token=")
                )
                previous = str(value[index - 1]).lower() if index else ""
                sensitive_argument = previous in {"--api-key", "--password", "--secret", "--token"}
                if sensitive_assignment or sensitive_argument:
                    raise ValueError(f"inline Secret command argument is forbidden at {path}[{index}]")
            _reject_inline_secret_config(child, path=f"{path}[{index}]")
