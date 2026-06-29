"""Build stable per-stage context packets for provider calls."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.redaction import redact
from ..services.rag import LocalRagIndex
from ..services.rag_policy import RagDecision, decide_rag
from ..storage import TraceWriter, append_ledger_event, sha256_text


def task_with_memory_context(task: str, automation: dict[str, object]) -> str:
    memory_items = automation.get("memory_context", []) if isinstance(automation, dict) else []
    if not isinstance(memory_items, list) or not memory_items:
        return task
    lines = [task, "", "# muxdev Memory Context"]
    for item in memory_items:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "").lower() == "quarantined" or str(item.get("promotion_state") or "").lower() == "quarantined":
            continue
        layer = item.get("layer") or item.get("scope") or "project"
        scope_id = item.get("scope_id") or "global"
        lines.append(f"- [{layer}:{scope_id}] {item.get('id', 'memory')}: {item.get('claim', '')}")
    return "\n".join(lines)


def task_with_context_packet(task: str, packet_path: Path, packet_hash: str) -> str:
    lines = [
        task,
        "",
        "# muxdev Context Packet",
        f"- path: {packet_path}",
        f"- hash: {packet_hash}",
        "- Read this packet before acting when you need prior attempts, memory, review blockers, or human responses.",
        "- For design, review, or revise stages, inspect run.feedback_events and run.upstream_artifacts before judging that a deliverable is missing from the worktree.",
    ]
    handled_responses = _handled_provider_response_lines(packet_path)
    if handled_responses:
        lines.extend(
            [
                "",
                "# Previously Handled Provider Actions",
                "Use these human responses as authoritative input for this stage. Continue the workflow and do not ask the same confirmation again.",
                *handled_responses,
            ]
        )
    return "\n".join(lines)


def write_context_packet(
    blackboard: Any,
    *,
    run_dir: Path,
    run_id: str,
    stage_id: str,
    role: str | None,
    provider: str,
    workflow: str,
    task: str,
    worktree: Path,
    skills: list[dict[str, object]],
    automation: dict[str, object],
    trace: TraceWriter,
    context_sources: list[str] | None = None,
    rag_query: str | None = None,
    loop_state: dict[str, object] | None = None,
) -> tuple[Path, str]:
    packet = build_context_packet(
        run_id=run_id,
        stage_id=stage_id,
        role=role,
        provider=provider,
        workflow=workflow,
        task=task,
        worktree=worktree,
        skills=skills,
        automation=automation,
        provider_attempts=[
            row
            for row in blackboard.table_rows("provider_attempts", run_id=run_id)
            if row.get("stage_id") == stage_id
        ],
        provider_action_responses=[
            _provider_action_response(row)
            for row in blackboard.list_provider_actions(run_id=run_id)
            if row.get("status") == "handled" and row.get("response") is not None
        ],
        feedback_events=blackboard.table_rows("feedback_events", run_id=run_id),
        artifacts=_context_artifacts(
            blackboard.table_rows("artifacts", run_id=run_id),
            completed_stage_ids={
                str(row.get("stage_id") or "")
                for row in blackboard.table_rows("stages", run_id=run_id)
                if str(row.get("status") or "") == "completed"
            },
            current_stage_id=stage_id,
        ),
        review_blockers=blackboard.table_rows("review_blockers", run_id=run_id),
        context_sources=context_sources or [],
        rag_query=rag_query,
        loop_state=loop_state or {},
    )
    body = redact(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True))
    digest = sha256_text(body)
    packet["context_packet_hash"] = digest
    packet["hash_algorithm"] = "sha256:redacted-json"
    body = redact(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True))
    path = run_dir / "context_packets" / f"{stage_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body + "\n", encoding="utf-8")
    blackboard.add_artifact(run_id, stage_id, path.name, path, "context_packet")
    event = append_ledger_event(
        run_dir,
        run_id=run_id,
        event_type="context_packet_written",
        stage_id=stage_id,
        payload={"context_packet_hash": digest, "path": str(path), "memory_refs": memory_refs(automation)},
    )
    blackboard.add_ledger_event(event)
    rag_decision = packet.get("rag_decision") if isinstance(packet.get("rag_decision"), dict) else {}
    trace.write("context_packet_written", stage=stage_id, context_packet_hash=digest, path=str(path), rag_decision=rag_decision)
    return path, digest


def build_context_packet(
    *,
    run_id: str,
    stage_id: str,
    role: str | None,
    provider: str,
    workflow: str,
    task: str,
    worktree: Path,
    skills: list[dict[str, object]],
    automation: dict[str, object],
    provider_attempts: list[dict[str, object]],
    provider_action_responses: list[dict[str, object]] | None = None,
    feedback_events: list[dict[str, object]] | None = None,
    artifacts: list[dict[str, object]] | None = None,
    review_blockers: list[dict[str, object]] | None = None,
    context_sources: list[str] | None = None,
    rag_query: str | None = None,
    loop_state: dict[str, object] | None = None,
) -> dict[str, object]:
    memory_items = _context_memory_items(automation)
    grouped = _memory_by_layer(memory_items)
    rag_decision, rag_context = _build_rag_context(
        task=task,
        stage_id=stage_id,
        role=role,
        worktree=worktree,
        context_sources=context_sources or [],
        rag_query=rag_query,
        memory_items=memory_items,
    )
    return {
        "schema": "muxdev.context_packet.v1",
        "session": {
            "temporary_context": grouped.get("session", []),
        },
        "task": {
            "task_id": run_id,
            "workflow": workflow,
            "current_stage": stage_id,
            "role": role,
            "provider": provider,
            "task": task,
            "worktree": str(worktree),
            "previous_attempts": provider_attempts,
            "skills": [skill.get("name") for skill in skills if isinstance(skill, dict)],
            "provider_action_responses": provider_action_responses or [],
        },
        "run": {
            "task_memory": grouped.get("run", []),
            "review_blockers": review_blockers or [],
            "feedback_events": [_feedback_event(row) for row in (feedback_events or [])],
            "artifacts": artifacts or [],
            "upstream_artifacts": artifacts or [],
        },
        "loop_state": loop_state or {},
        "rag_decision": rag_decision.to_dict(),
        "rag_context": rag_context,
        "branch": {
            "feature_memory": grouped.get("branch", []),
        },
        "project": {
            "long_term_memory": grouped.get("project", []),
            "workspace_memory": grouped.get("workspace", []),
            "review_required": [
                item
                for item in memory_items
                if item.get("layer") in {"project", "workspace", "user"} and item.get("promotion_state") != "approved"
            ],
        },
        "user_preferences": {
            "memory": grouped.get("user", []),
        },
        "memory_policy": {
            "excluded_statuses": ["quarantined"],
            "promotion_required_for_project_context": True,
            "temporary_layers": ["session", "run", "branch"],
            "memory_refs": memory_refs(automation),
        },
    }


def _context_artifacts(
    rows: list[dict[str, object]],
    *,
    completed_stage_ids: set[str],
    current_stage_id: str,
) -> list[dict[str, object]]:
    allowed_kinds = {
        "stage_output",
        "delivery_gate",
        "role_result_contract",
        "design_pack",
        "design_contract",
        "project_design_doc",
        "review_report",
        "plan_summary",
    }
    refs: list[dict[str, object]] = []
    for row in rows:
        kind = str(row.get("kind") or "")
        stage_id = str(row.get("stage_id") or "")
        if kind not in allowed_kinds:
            continue
        if stage_id == current_stage_id:
            continue
        if stage_id and completed_stage_ids and stage_id not in completed_stage_ids:
            continue
        refs.append(_artifact_ref(row))
    return refs[-20:]


def _artifact_ref(row: dict[str, object]) -> dict[str, object]:
    path = Path(str(row.get("path") or ""))
    exists = path.exists()
    return {
        "stage_id": row.get("stage_id"),
        "kind": row.get("kind"),
        "name": row.get("name"),
        "path": str(path),
        "exists": exists,
        "size_bytes": path.stat().st_size if exists and path.is_file() else 0,
        "created_at": row.get("created_at"),
    }


def _feedback_event(row: dict[str, object]) -> dict[str, object]:
    payload = row.get("payload")
    if not isinstance(payload, dict):
        try:
            payload = json.loads(str(row.get("payload_json") or "{}"))
        except json.JSONDecodeError:
            payload = {}
    return {
        "feedback_id": row.get("feedback_id"),
        "kind": row.get("kind"),
        "status": row.get("status"),
        "route_to": row.get("route_to"),
        "severity": row.get("severity"),
        "content": row.get("content"),
        "payload": payload,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _build_rag_context(
    *,
    task: str,
    stage_id: str,
    role: str | None,
    worktree: Path,
    context_sources: list[str],
    rag_query: str | None,
    memory_items: list[dict[str, object]],
) -> tuple[RagDecision, list[dict[str, object]]]:
    index = LocalRagIndex(worktree)
    decision = decide_rag(
        task=task,
        stage_id=stage_id,
        role=role,
        context_sources=context_sources,
        rag_query=rag_query,
        index_path=index.path,
        memory_items=memory_items,
    )
    if not decision.enabled:
        return decision, []
    try:
        rows = index.query(decision.query, limit=decision.top_k)
    except Exception as exc:
        return (
            RagDecision(
                enabled=False,
                reason=f"rag retrieval failed: {type(exc).__name__}",
                query=decision.query,
                top_k=decision.top_k,
                context_sources=decision.context_sources,
                skipped_because="retrieval_failed",
            ),
            [],
        )
    if not rows:
        return (
            RagDecision(
                enabled=False,
                reason="rag retrieval returned no confident hits",
                query=decision.query,
                top_k=decision.top_k,
                context_sources=decision.context_sources,
                skipped_because="no_hits",
            ),
            [],
        )
    return decision, [_rag_hit(row) for row in rows]


def _rag_hit(row: dict[str, object]) -> dict[str, object]:
    path = str(row.get("path") or "")
    start = int(row.get("start_line") or 0)
    end = int(row.get("end_line") or start)
    return {
        "path": path,
        "start_line": start,
        "end_line": end,
        "score": row.get("score"),
        "citation": f"{path}:{start}-{end}" if path else "",
        "text": row.get("text"),
    }


def memory_refs(automation: dict[str, object]) -> list[str]:
    memory_items = automation.get("memory_context", []) if isinstance(automation, dict) else []
    refs: list[str] = []
    if isinstance(memory_items, list):
        for item in memory_items:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            if str(item.get("status") or "").lower() == "quarantined":
                continue
            if str(item.get("promotion_state") or "").lower() == "quarantined":
                continue
            refs.append(str(item["id"]))
    return refs


def _context_memory_items(automation: dict[str, object]) -> list[dict[str, object]]:
    memory_items = automation.get("memory_context", []) if isinstance(automation, dict) else []
    if not isinstance(memory_items, list):
        return []
    result: list[dict[str, object]] = []
    for item in memory_items:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "").lower() == "quarantined":
            continue
        if str(item.get("promotion_state") or "").lower() == "quarantined":
            continue
        result.append(
            {
                "id": item.get("id"),
                "layer": item.get("layer") or item.get("scope") or "project",
                "scope_id": item.get("scope_id"),
                "kind": item.get("kind"),
                "role": item.get("role"),
                "claim": item.get("claim"),
                "promotion_state": item.get("promotion_state") or item.get("status"),
                "source_type": item.get("source_type"),
                "source_uri": item.get("source_uri"),
                "evidence": item.get("evidence", []),
            }
        )
    return result


def _provider_action_response(row: dict[str, object]) -> dict[str, object]:
    return {
        "action_id": row.get("action_id"),
        "stage_id": row.get("stage_id"),
        "provider": row.get("provider"),
        "kind": row.get("kind"),
        "input_kind": row.get("input_kind"),
        "prompt_text": row.get("prompt_text"),
        "response": row.get("response"),
    }


def _handled_provider_response_lines(packet_path: Path, *, limit: int = 5) -> list[str]:
    try:
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(packet, dict):
        return []
    task = packet.get("task")
    if not isinstance(task, dict):
        return []
    responses = task.get("provider_action_responses")
    if not isinstance(responses, list):
        return []
    lines: list[str] = []
    for row in responses[-limit:]:
        if not isinstance(row, dict):
            continue
        response = _compact_provider_response(row.get("response"))
        if not response:
            continue
        stage = str(row.get("stage_id") or "stage")
        kind = str(row.get("kind") or row.get("input_kind") or "provider_action")
        lines.append(f"- {stage} / {kind}: {response}")
    return lines


def _compact_provider_response(value: object, *, max_chars: int = 1200) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        if value.get("text") is not None:
            text = str(value.get("text") or "")
        elif value.get("choice") is not None:
            text = f"choice={value.get('choice')}"
        else:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    elif isinstance(value, (list, tuple)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    text = redact(" ".join(text.split()))
    if len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "..."
    return text


def _memory_by_layer(memory_items: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for item in memory_items:
        grouped.setdefault(str(item.get("layer") or "project"), []).append(item)
    return grouped
