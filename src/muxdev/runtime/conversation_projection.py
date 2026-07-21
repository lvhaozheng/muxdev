"""Read-only Conversation projections for progress and stage-level trust."""

from __future__ import annotations

import json
from typing import Any, Mapping

from ..core.redaction import redact
from ..services.evidence_policy import load_evidence_policy
from ..workflows import load_workflow


ROLE_LABELS = {
    "plan": "规划",
    "code": "实现",
    "test": "测试",
    "review": "评审",
    "secure": "安全评审",
    "architect": "架构设计",
    "test_strategy": "测试策略",
}

_EVIDENCE_BY_ROLE = {
    "plan": {"plan_artifact"},
    "architect": {"plan_artifact"},
    "test_strategy": {"plan_artifact"},
    "code": {"change_artifact"},
    "test": {"deterministic_check"},
    "review": {"review", "independent_review"},
    "secure": {"security_review"},
}


def progress_projection(
    contract: Mapping[str, Any] | None,
    run: Mapping[str, Any] | None,
    workers: list[Mapping[str, Any]],
    interactions: list[Mapping[str, Any]],
) -> dict[str, Any]:
    applicable = [item for item in workers if item.get("status") != "skipped"]
    completed = sum(item.get("status") == "completed" for item in applicable)
    total = len(applicable)
    run_status = str((run or {}).get("status") or "pending")
    pending = [item for item in interactions if item.get("status") == "pending"]
    active = [str(item.get("stage_id")) for item in workers if item.get("status") == "running"]
    failed = [str(item.get("stage_id")) for item in workers if item.get("status") == "failed"]
    if pending:
        state, label = "waiting_user", "等待你的确认"
    elif failed or run_status == "blocked":
        state, label = "failed", "执行遇到问题"
    elif run_status == "completed":
        state, label = "completed", "可信交付阶段已完成"
        completed = total
    elif active:
        state, label = "running", f"正在执行 {', '.join(active)}"
    elif run_status == "created":
        state, label = "initializing", "正在建立 Run 并预检 Agent 团队"
    elif run:
        state, label = run_status, "准备下一阶段"
    else:
        state, label = "pending", "等待开始"
    percent = 0 if not total else round(completed * 100 / total)
    return {
        "method": "stage_completion",
        "percent": max(0, min(percent, 100)),
        "completed_stages": completed,
        "total_stages": total,
        "state": state,
        "label": label,
        "active_stage_ids": active,
        "pending_interactions": len(pending),
        "contract_version": (contract or {}).get("version"),
    }


def stage_delivery_projection(workspace, store, contract, run) -> list[dict[str, Any]]:
    if not contract:
        return []
    workflow = load_workflow(str(contract["workflow"]))
    profile = str(contract.get("profile") or "standard")
    run_id = str((run or {}).get("run_id") or "")
    rows = {str(item["stage_id"]): item for item in store.stages(run_id)} if run_id else {}
    artifacts: dict[str, list[dict[str, Any]]] = {}
    for item in store.artifacts(run_id) if run_id else []:
        stage_id = str(item.get("stage_id") or "")
        if stage_id:
            artifacts.setdefault(stage_id, []).append({
                key: item.get(key) for key in ("artifact_id", "name", "kind", "digest", "size", "media_type")
            })
    evidence: dict[str, list[dict[str, Any]]] = {}
    for event in store.events(run_id) if run_id else []:
        if event.get("type") != "evidence.record" or not isinstance(event.get("payload"), dict):
            continue
        payload = event["payload"]
        stage_id = str(payload.get("stage_id") or "")
        evidence.setdefault(stage_id, []).append({
            "record_id": payload.get("record_id"),
            "kind": payload.get("kind"),
            "requirement_id": payload.get("requirement_id"),
            "status": payload.get("status", "observed"),
        })
    policy = contract.get("policy") if isinstance(contract.get("policy"), dict) else {}
    overrides = policy.get("stage_standards") if isinstance(policy.get("stage_standards"), dict) else {}
    requirements = load_evidence_policy(
        workspace, workflow=str(contract["workflow"]), profile=profile
    ).requirements
    result = []
    for stage in workflow.stages:
        if not _applicable(stage, profile, rows):
            continue
        row = rows.get(stage.id, {})
        stage_result = row.get("result") if isinstance(row.get("result"), dict) else {}
        standards = _stage_standards(stage, contract, overrides, requirements)
        parsed = stage_result.get("parsed")
        output = redact(json.dumps(parsed, ensure_ascii=False, default=str))[:4000] if parsed else ""
        result.append({
            "stage_id": stage.id,
            "role": stage.role,
            "label": ROLE_LABELS.get(str(stage.role), "人工门禁" if stage.type == "human_gate" else stage.id),
            "status": row.get("status", "pending"),
            "attempt": int(row.get("attempt") or 0),
            "provider": row.get("provider"),
            "dependencies": list(stage.deps),
            "read_only": bool(stage.read_only),
            "standards": standards,
            "custom_standards": list(overrides.get(stage.id, [])) if isinstance(overrides.get(stage.id), list) else [],
            "summary": redact(str(stage_result.get("summary") or ""))[:1000],
            "output": output,
            "artifacts": artifacts.get(stage.id, []),
            "evidence": evidence.get(stage.id, []),
        })
    return result


def normalize_stage_standards(workflow_name: str, value: object) -> dict[str, list[str]]:
    if not isinstance(value, Mapping):
        raise ValueError("stage_standards must be a mapping")
    valid = {stage.id for stage in load_workflow(workflow_name).stages}
    result: dict[str, list[str]] = {}
    for stage_id, raw in value.items():
        if str(stage_id) not in valid:
            raise ValueError(f"unknown stage standard: {stage_id}")
        if not isinstance(raw, list):
            raise ValueError(f"stage standard {stage_id} must be a list")
        items = [redact(str(item).strip())[:1000] for item in raw if str(item).strip()]
        if len(items) > 20:
            raise ValueError(f"stage standard {stage_id} has more than 20 items")
        result[str(stage_id)] = items
    return result


def affected_stages(workflow_name: str, changed: set[str]) -> list[str]:
    workflow = load_workflow(workflow_name)
    if changed & {"goal", "acceptance_criteria", "allowed_scope", "workflow", "profile", "provider", "role_providers"}:
        return [stage.id for stage in workflow.stages]
    seeds = {
        item.split(":", 1)[1] for item in changed
        if item.startswith("stage_standard:")
    }
    affected = set(seeds)
    while True:
        expanded = affected | {
            stage.id for stage in workflow.stages if set(stage.deps) & affected
        }
        if expanded == affected:
            break
        affected = expanded
    return [stage.id for stage in workflow.stages if stage.id in affected]


def _applicable(stage, profile: str, rows: Mapping[str, Any]) -> bool:
    if stage.when == "profile.strict" and profile != "strict" and stage.id not in rows:
        return False
    if profile == "lite" and stage.id in {"plan", "test_plan", "approve_plan"} and stage.id not in rows:
        return False
    if stage.id in {"fix", "revise"} and stage.id not in rows:
        return False
    if stage.type == "human_gate" and profile != "strict" and stage.id not in rows:
        return False
    return True


def _stage_standards(stage, contract, overrides, requirements) -> list[dict[str, str]]:
    standards = [
        {"kind": "acceptance", "text": str(item)}
        for item in contract.get("acceptance_criteria", [])
    ]
    standards.extend(
        {"kind": "custom", "text": str(item)}
        for item in overrides.get(stage.id, [])
        if isinstance(overrides.get(stage.id), list)
    )
    if stage.output_schema:
        standards.append({"kind": "output_contract", "text": f"输出必须符合 {stage.output_schema}"})
    standards.extend(
        {"kind": "verification", "text": f"运行检查：{command.id}"}
        for command in stage.verification_commands
    )
    accepted = _EVIDENCE_BY_ROLE.get(str(stage.role), set())
    if stage.type == "human_gate":
        accepted = {"human_approval"}
    standards.extend(
        {"kind": "evidence", "text": requirement.description}
        for requirement in requirements
        if requirement.id in accepted
    )
    return standards


__all__ = [
    "affected_stages",
    "normalize_stage_standards",
    "progress_projection",
    "stage_delivery_projection",
]
