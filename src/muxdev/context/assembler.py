"""Build stable per-stage context packets for provider calls."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.redaction import redact
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
    return "\n".join(
        [
            task,
            "",
            "# muxdev Context Packet",
            f"- path: {packet_path}",
            f"- hash: {packet_hash}",
        ]
    )


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
    trace.write("context_packet_written", stage=stage_id, context_packet_hash=digest, path=str(path))
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
) -> dict[str, object]:
    memory_items = _context_memory_items(automation)
    grouped = _memory_by_layer(memory_items)
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
        },
        "run": {
            "task_memory": grouped.get("run", []),
        },
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


def _memory_by_layer(memory_items: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for item in memory_items:
        grouped.setdefault(str(item.get("layer") or "project"), []).append(item)
    return grouped
