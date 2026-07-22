"""Read-only Conversation projections for progress and stage-level trust."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ..core.redaction import redact
from ..services.evidence_policy import load_evidence_policy
from ..workflows import load_workflow
from .delivery_standards import (
    applicable_custom_stage_ids,
    standard_from_contract_policy,
    standard_requirement_id,
)


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


def delivery_standard_projection(
    contract: Mapping[str, Any] | None,
    report: Mapping[str, Any] | None,
    *,
    candidate_status: str | None = None,
) -> dict[str, Any]:
    if not contract:
        return {
            "schema_version": "muxdev.delivery-standard.v1",
            "baseline_version": "sdlc-v1",
            "stages": [],
            "available_checks": [],
            "summary": {"passed": 0, "total": 0, "blocking": 0},
        }
    policy = contract.get("policy") if isinstance(contract.get("policy"), Mapping) else {}
    standard = standard_from_contract_policy(
        str(contract["workflow"]), str(contract.get("profile") or "standard"), policy
    )
    decision = report.get("decision") if isinstance(report, Mapping) and isinstance(report.get("decision"), Mapping) else {}
    evaluations = {
        str(item.get("requirement_id") or ""): item
        for item in decision.get("requirements", [])
        if isinstance(item, Mapping)
    }
    stages, all_items, custom_by_stage = _project_delivery_standard_stages(
        standard, evaluations, decision, candidate_status == "accepted"
    )
    workflow = load_workflow(str(contract["workflow"]))
    editable_ids = applicable_custom_stage_ids(
        workflow, str(contract.get("profile") or "standard")
    )
    editable_stages = [
        {
            "stage_id": stage.id,
            "label": ROLE_LABELS.get(str(stage.role), stage.id),
        }
        for stage in workflow.stages if stage.id in editable_ids
    ]
    available_checks = [
        {
            "command_id": command.id,
            "stage_id": stage.id,
            "label": f"{ROLE_LABELS.get(str(stage.role), stage.id)} · {command.id}",
        }
        for stage in workflow.stages
        for command in stage.verification_commands
    ]
    blocking = [
        item for item in all_items
        if item.get("status") == "failed"
        and item.get("id") != "acceptance.explicit"
    ]
    pre_accept = [item for item in all_items if item.get("id") != "acceptance.explicit"]
    return {
        "schema_version": standard.get("schema_version"),
        "baseline_version": standard.get("baseline_version"),
        "stages": stages,
        "custom_items": [item for items in custom_by_stage.values() for item in items],
        "available_checks": available_checks,
        "editable_stages": editable_stages,
        "summary": {
            "passed": sum(item.get("status") == "passed" for item in all_items),
            "total": len(all_items),
            "pre_accept_passed": sum(item.get("status") == "passed" for item in pre_accept),
            "pre_accept_total": len(pre_accept),
            "blocking": len(blocking),
        },
        "blocking_items": [
            {"id": item.get("id"), "text": item.get("text"), "reason": item.get("reason")}
            for item in blocking
        ],
    }


def _project_delivery_standard_stages(
    standard: Mapping[str, Any],
    evaluations: Mapping[str, Mapping[str, Any]],
    decision: Mapping[str, Any],
    accepted: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    custom_by_stage: dict[str, list[dict[str, Any]]] = {}
    for raw in standard.get("custom_items", []):
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        item.update(_standard_item_status(
            evaluations.get(standard_requirement_id(str(item.get("id") or ""))),
            decision,
        ))
        custom_by_stage.setdefault(str(item.get("stage_id") or ""), []).append(item)
    stages: list[dict[str, Any]] = []
    all_items: list[dict[str, Any]] = []
    for raw_stage in standard.get("stages", []):
        if not isinstance(raw_stage, Mapping):
            continue
        stage = dict(raw_stage)
        baseline = [
            _project_baseline_item(item, evaluations, decision, accepted)
            for item in stage.get("baseline_items", [])
            if isinstance(item, Mapping)
        ]
        custom = custom_by_stage.get(str(stage.get("stage_id") or ""), [])
        stage_items = [*baseline, *custom]
        stage.update({
            "baseline_items": baseline,
            "custom_items": custom,
            "status": _combined_standard_status(stage_items),
        })
        stages.append(stage)
        all_items.extend(stage_items)
    known_stages = {str(item.get("stage_id") or "") for item in stages}
    for stage_id, custom in custom_by_stage.items():
        if stage_id in known_stages:
            continue
        stages.append({
            "stage_id": stage_id,
            "label": ROLE_LABELS.get(stage_id, stage_id),
            "deliverable": "此阶段按会话补充标准完成。",
            "completion": "全部会话标准均有有效的检查或评审证据。",
            "proof": "CheckEvidence 或 ReviewEvidence",
            "baseline_items": [],
            "custom_items": custom,
            "status": _combined_standard_status(custom),
        })
        all_items.extend(custom)
    return stages, all_items, custom_by_stage


def _project_baseline_item(
    raw_item: Mapping[str, Any],
    evaluations: Mapping[str, Mapping[str, Any]],
    decision: Mapping[str, Any],
    accepted: bool,
) -> dict[str, Any]:
    item = dict(raw_item)
    identifier = str(item.get("id") or "")
    if identifier == "acceptance.explicit":
        state = {
            "status": "passed" if accepted else "pending",
            "reason": "交付已由用户接受并写入项目。" if accepted else "等待用户显式接受。",
            "evidence_refs": [],
        }
    elif identifier == "acceptance.gate":
        gate_status = str(decision.get("status") or "")
        state = {
            "status": "passed" if gate_status == "PASS" else ("failed" if gate_status == "BLOCKED" else "pending"),
            "reason": "全部交付门禁已通过。" if gate_status == "PASS" else "仍有交付门禁未通过。",
            "evidence_refs": [],
        }
    else:
        state = _standard_item_status(
            evaluations.get(str(item.get("evidence_requirement_id") or "")), decision
        )
    item.update(state)
    return item


def active_delivery_projection(
    contract: Mapping[str, Any] | None,
    candidate: Mapping[str, Any] | None,
    report: Mapping[str, Any] | None,
    stage_deliveries: list[Mapping[str, Any]],
    standard: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not candidate or not contract:
        return None
    decision = report.get("decision") if isinstance(report, Mapping) and isinstance(report.get("decision"), Mapping) else {}
    integrity = report.get("integrity") if isinstance(report, Mapping) and isinstance(report.get("integrity"), Mapping) else {}
    harness = report.get("harness") if isinstance(report, Mapping) and isinstance(report.get("harness"), Mapping) else {}
    changeset = harness.get("changeset") if isinstance(harness.get("changeset"), Mapping) else {}
    operations = [dict(item) for item in changeset.get("operations", []) if isinstance(item, Mapping)]
    files = list(dict.fromkeys(
        str(item.get("path") or item.get("previous_path") or "") for item in operations
        if item.get("path") or item.get("previous_path")
    ))
    summary = next(
        (
            str(item.get("summary") or "")
            for item in stage_deliveries
            if item.get("role") in {"code", "architect", "test_strategy"}
            and item.get("summary")
        ),
        "",
    )
    records = report.get("records") if isinstance(report, Mapping) and isinstance(report.get("records"), list) else []
    checks = [
        {
            "stage_id": item.get("stage_id"),
            "summary": item.get("summary") or "运行检查",
            "status": "passed" if int(item.get("exit_code") or 0) == 0 else "failed",
            "criteria_ids": list(item.get("criteria_ids") or []),
        }
        for item in records if isinstance(item, Mapping) and item.get("kind") == "check"
    ]
    reviews = [
        {
            "stage_id": item.get("stage_id"),
            "independent": bool(item.get("independent")),
            "high_risk_open": sum(
                finding.get("severity") == "high" and not finding.get("resolved")
                for finding in item.get("findings", []) if isinstance(finding, Mapping)
            ),
            "standard_assessments": list(item.get("standard_assessments") or []),
        }
        for item in records if isinstance(item, Mapping) and item.get("kind") == "review"
    ]
    status = str(candidate.get("status") or "")
    state, label = {
        "accepted": ("written", "已写入项目"),
        "verified": ("ready", "已验证，可交付"),
        "invalidated": ("stale", "需要重新验证"),
        "verifying": ("verifying", "正在验证"),
    }.get(status, ("blocked", "暂不能交付"))
    gate_status = str(decision.get("status") or "")
    integrity_valid = bool(integrity.get("valid")) if report else False
    pre_accept = standard.get("summary") if isinstance(standard.get("summary"), Mapping) else {}
    can_accept = (
        status == "verified"
        and gate_status == "PASS"
        and integrity_valid
        and int(pre_accept.get("pre_accept_passed") or 0) == int(pre_accept.get("pre_accept_total") or 0)
    )
    blockers = [
        {
            "requirement_id": item.get("requirement_id"),
            "reason": item.get("reason"),
            "remediation": item.get("remediation"),
        }
        for item in decision.get("blockers", []) if isinstance(item, Mapping)
    ]
    return {
        "candidate_id": candidate.get("candidate_id"),
        "candidate_version": candidate.get("version"),
        "contract_version": contract.get("version"),
        "state": state,
        "status_label": label,
        "task_goal": contract.get("goal"),
        "change_summary": summary or ("未产生文件变更。" if not files else f"共变更 {len(files)} 个文件。"),
        "changed_files": files,
        "operations": [
            {key: item.get(key) for key in ("operation", "path", "previous_path")}
            for item in operations
        ],
        "gate_status": gate_status or None,
        "standard_summary": dict(pre_accept),
        "blocking_items": blockers,
        "checks": checks,
        "reviews": reviews,
        "evidence_integrity": {
            "available": bool(report),
            "valid": integrity_valid,
        },
        "can_accept": can_accept,
        "acceptance_effect": "接受时会再次核验证据与候选内容、检查项目冲突，然后才把变更写入项目。",
    }


def conversation_delivery_projections(
    contract: Mapping[str, Any] | None,
    candidates: list[Mapping[str, Any]],
    conversation: Mapping[str, Any],
    stage_deliveries: list[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    active_candidate = next(
        (
            item for item in reversed(candidates)
            if item.get("candidate_id") == conversation.get("active_candidate_id")
        ),
        candidates[-1] if candidates else None,
    )
    report = _candidate_report(active_candidate)
    standard = delivery_standard_projection(
        contract,
        report,
        candidate_status=str(active_candidate.get("status") or "") if active_candidate else None,
    )
    return standard, active_delivery_projection(
        contract, active_candidate, report, stage_deliveries, standard
    )


def _candidate_report(candidate: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not candidate:
        return None
    path = Path(str(candidate.get("evidence_path") or ""))
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _standard_item_status(
    evaluation: Mapping[str, Any] | None,
    decision: Mapping[str, Any],
) -> dict[str, Any]:
    if not evaluation:
        return {"status": "pending", "reason": "尚无有效证据。", "evidence_refs": []}
    raw = str(evaluation.get("status") or "")
    status = "passed" if raw == "satisfied" else ("failed" if raw in {"failed", "missing"} and decision.get("status") == "BLOCKED" else "pending")
    return {
        "status": status,
        "reason": str(evaluation.get("reason") or ""),
        "evidence_refs": list(evaluation.get("record_ids") or []),
    }


def _combined_standard_status(items: list[Mapping[str, Any]]) -> str:
    if not items:
        return "pending"
    if any(item.get("status") == "failed" for item in items):
        return "failed"
    if all(item.get("status") == "passed" for item in items):
        return "passed"
    return "pending"


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
        if item.startswith("stage_standard:") or item.startswith("delivery_standard:")
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
    "active_delivery_projection",
    "affected_stages",
    "conversation_delivery_projections",
    "delivery_standard_projection",
    "normalize_stage_standards",
    "progress_projection",
    "stage_delivery_projection",
]
