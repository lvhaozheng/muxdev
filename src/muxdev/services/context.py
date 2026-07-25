"""Budgeted context assembly and evidence-grounded local retrieval.

Long-term context is a derived view, not an authority: only prior runs whose
gate passed and whose evidence still verifies are eligible for retrieval.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..core.redaction import redact
from ..models.evidence import canonical_hash
from ..storage.control import ControlStore
from .evidence_verify import verify_evidence_report
from .repo_map import build_repo_map
from .conversation_memory import ensure_conversation_checkpoint


@dataclass(frozen=True)
class MemoryHit:
    run_id: str
    task: str
    subject_digest: str
    score: float
    summary: str


@dataclass(frozen=True)
class ContextPack:
    text: str
    manifest: dict[str, object]


def build_context_pack(
    workspace: Path,
    worktree: Path,
    store: ControlStore,
    *,
    run_id: str,
    task: str,
    max_chars: int = 12_000,
) -> ContextPack:
    """Build a deterministic, provenance-carrying context pack."""
    upstream = _upstream_context(store.stages(run_id), max_chars=3_500)
    repo_map = build_repo_map(worktree, task, max_chars=6_000)
    hits = retrieve_verified_memory(workspace, store, task, exclude_run_id=run_id, limit=3)
    memory = "\n".join(
        f"- [{hit.run_id}] task={hit.task}; subject={hit.subject_digest}; {hit.summary}"
        for hit in hits
    )
    project_rules_path = workspace / "MUXDEV.md"
    project_rules = (
        project_rules_path.read_text(encoding="utf-8", errors="replace")[:4_000]
        if project_rules_path.is_file()
        else ""
    )
    sections = [
        ("Upstream structured facts", upstream),
        ("Approved MuxDev project rules", project_rules),
        ("Deterministic repository map", repo_map),
        ("Verified delivery memory", memory),
    ]
    rendered: list[str] = []
    truncated: list[str] = []
    remaining = max_chars
    for title, body in sections:
        if not body or remaining <= len(title) + 6:
            continue
        header = f"## {title}\n"
        allowed = remaining - len(header)
        clipped = _clip(body, allowed)
        if clipped != body:
            truncated.append(title)
        rendered.append(header + clipped)
        remaining -= len(header) + len(clipped) + 2
    text = "\n\n".join(rendered)
    manifest: dict[str, object] = {
        "schema": "muxdev.context-pack.v1",
        "max_chars": max_chars,
        "used_chars": len(text),
        "truncated_sections": truncated,
        "upstream_stage_ids": [
            str(item["stage_id"]) for item in store.stages(run_id) if item.get("status") == "completed"
        ],
        "memory_run_ids": [hit.run_id for hit in hits],
        "repo_map_digest": canonical_hash(repo_map),
        "context_digest": canonical_hash(text),
    }
    return ContextPack(text=text, manifest=manifest)


def build_conversation_context_pack(
    workspace: Path,
    worktree: Path,
    store: ControlStore,
    *,
    conversation_id: str,
    contract: Mapping[str, Any],
    task: str,
    max_chars: int = 12_000,
) -> ContextPack:
    """Build the Conversation-native pack with frozen facts taking priority."""
    checkpoint = ensure_conversation_checkpoint(
        store,
        conversation_id,
        force=False,
        created_by="runtime",
    )
    critical_section, pending, rule_snapshot = _conversation_critical_section(
        store, conversation_id, contract, max_chars=max_chars
    )
    sections, facts = _conversation_context_sections(
        workspace,
        worktree,
        store,
        conversation_id=conversation_id,
        task=task,
        checkpoint=checkpoint,
    )
    text, truncated = _render_context_sections(
        critical_section, sections, max_chars=max_chars
    )
    assignments = facts["assignments"]
    hits = facts["hits"]
    return ContextPack(
        text=text,
        manifest={
            "schema": "muxdev.context-pack.v3",
            "conversation_id": conversation_id,
            "max_chars": max_chars,
            "used_chars": len(text),
            "critical_sections_truncated": False,
            "truncated_sections": truncated,
            "pending_interaction_ids": [item["interaction_id"] for item in pending],
            "assignment_ids": [item["assignment_id"] for item in assignments],
            "memory_checkpoint_id": (
                str(checkpoint["checkpoint_id"]) if checkpoint else None
            ),
            "memory_checkpoint_version": (
                int(checkpoint["version"]) if checkpoint else None
            ),
            "memory_through_sequence": facts["checkpoint_sequence"],
            "rule_snapshot_id": (
                str(rule_snapshot["snapshot_id"]) if rule_snapshot else None
            ),
            "memory_run_ids": [hit.run_id for hit in hits],
            "project_rules_digest": canonical_hash(facts["project_rules"]),
            "repo_map_digest": canonical_hash(facts["repo_map"]),
            "context_digest": canonical_hash(text),
        },
    )


def _conversation_critical_section(
    store: ControlStore,
    conversation_id: str,
    contract: Mapping[str, Any],
    *,
    max_chars: int,
) -> tuple[str, list[dict[str, Any]], dict[str, Any] | None]:
    pending = store.list_conversation_interactions(conversation_id, pending_only=True)
    rule_snapshot = store.latest_conversation_rule_snapshot(conversation_id)
    critical_payload = {
        "contract": {
            "contract_id": contract.get("contract_id"),
            "goal": contract.get("goal"),
            "acceptance_criteria": contract.get("acceptance_criteria"),
            "allowed_scope": contract.get("allowed_scope"),
            "profile": contract.get("profile"),
            "delivery_standard": (contract.get("policy") or {}).get("delivery_standard"),
        },
        "pending_questions": [
            {
                "interaction_id": item.get("interaction_id"),
                "requirement_id": item.get("requirement_id"),
                "prompt": item.get("prompt"),
                "options": item.get("options"),
            }
            for item in pending
        ],
        "frozen_rules": list((rule_snapshot or {}).get("rules") or []),
    }
    critical = json.dumps(critical_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    critical_section = "## Frozen contract and unresolved questions\n" + critical
    if len(critical_section) > max_chars:
        raise ValueError(
            "frozen contract and unresolved questions exceed the 12,000 character context budget"
        )
    return critical_section, pending, rule_snapshot


def _conversation_context_sections(
    workspace: Path,
    worktree: Path,
    store: ControlStore,
    *,
    conversation_id: str,
    task: str,
    checkpoint: Mapping[str, Any] | None,
) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    assignments = store.list_assignments(conversation_id)
    assignment_outputs = "\n".join(
        json.dumps(
            {
                "assignment_id": item.get("assignment_id"),
                "agent_id": item.get("agent_id"),
                "status": item.get("status"),
                "report": (item.get("metadata") or {}).get("report"),
                "changeset_digest": item.get("changeset_digest"),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for item in assignments
        if item.get("status") in {"reported", "ready_to_merge", "completed"}
    )
    events = store.conversation_events(conversation_id)
    checkpoint_sequence = int((checkpoint or {}).get("through_sequence") or 0)
    recent = "\n".join(
        json.dumps(
            {"type": item.get("type"), "actor": item.get("actor"), "payload": item.get("payload")},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for item in events
        if int(item.get("sequence") or 0) > checkpoint_sequence
        if item.get("type") in {
            "user.message", "requirements.ready", "interaction.responded", "assignment.reported"
        }
    )
    repo_map = build_repo_map(worktree, task, max_chars=5_000)
    hits = retrieve_verified_memory(workspace, store, task, limit=3)
    memory = "\n".join(
        f"- [{hit.run_id}] task={hit.task}; subject={hit.subject_digest}; {hit.summary}"
        for hit in hits
    )
    project_rules_path = workspace / "MUXDEV.md"
    project_rules = (
        project_rules_path.read_text(encoding="utf-8", errors="replace")[:4_000]
        if project_rules_path.is_file()
        else ""
    )
    sections = [
        (
            "Shared Conversation memory checkpoint",
            json.dumps(
                (checkpoint or {}).get("content") or {},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if checkpoint
            else "",
        ),
        ("Dependency Assignment outputs", assignment_outputs),
        ("Facts after the memory checkpoint", recent),
        ("Legacy MUXDEV.md advisory guidance", project_rules),
        ("Deterministic repository map", repo_map),
        ("Still-verifiable PASS delivery memory", memory),
    ]
    return sections, {
        "assignments": assignments,
        "hits": hits,
        "checkpoint_sequence": checkpoint_sequence,
        "project_rules": project_rules,
        "repo_map": repo_map,
    }


def _render_context_sections(
    critical_section: str,
    sections: list[tuple[str, str]],
    *,
    max_chars: int,
) -> tuple[str, list[str]]:
    rendered = [critical_section]
    truncated: list[str] = []
    remaining = max_chars - len(critical_section) - 2
    for title, body in sections:
        if not body or remaining <= len(title) + 6:
            continue
        header = f"## {title}\n"
        clipped = _clip(body, remaining - len(header))
        if clipped != body:
            truncated.append(title)
        rendered.append(header + clipped)
        remaining -= len(header) + len(clipped) + 2
    text = "\n\n".join(rendered)
    return text, truncated


def retrieve_verified_memory(
    workspace: Path,
    store: ControlStore,
    query: str,
    *,
    exclude_run_id: str | None = None,
    limit: int = 3,
) -> list[MemoryHit]:
    """Retrieve BM25-ranked facts only from still-verifiable PASS reports."""
    candidates: list[tuple[dict[str, Any], str, str, str]] = []
    for run in store.list_runs(status="completed", limit=100):
        run_id = str(run["run_id"])
        if run_id == exclude_run_id:
            continue
        metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
        report_path = Path(str(metadata.get("run_dir") or workspace / ".muxdev" / "runs" / run_id)) / "evidence-report.json"
        verification = verify_evidence_report(report_path, store=store)
        if not verification.get("valid") or verification.get("gate_status") != "PASS":
            continue
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        subject = str((report.get("subject") or {}).get("digest") or "")
        summary = _memory_summary(store.stages(run_id))
        searchable = f"{run.get('task', '')} {summary}"
        candidates.append((run, subject, summary, searchable))
    scores = _bm25(query, [item[3] for item in candidates])
    ranked = sorted(
        ((score, index) for index, score in enumerate(scores) if score > 0),
        key=lambda item: (-item[0], str(candidates[item[1]][0]["run_id"])),
    )[:limit]
    return [
        MemoryHit(
            run_id=str(candidates[index][0]["run_id"]),
            task=redact(str(candidates[index][0].get("task") or "")),
            subject_digest=candidates[index][1],
            score=round(score, 6),
            summary=candidates[index][2],
        )
        for score, index in ranked
    ]


def _upstream_context(stages: list[dict[str, Any]], *, max_chars: int) -> str:
    rows: list[str] = []
    for stage in stages:
        if stage.get("status") != "completed":
            continue
        result = stage.get("result") if isinstance(stage.get("result"), dict) else {}
        parsed = result.get("parsed") if isinstance(result.get("parsed"), dict) else {}
        fact = {
            "stage_id": stage.get("stage_id"),
            "role": stage.get("role"),
            "provider": stage.get("provider"),
            "summary": result.get("summary"),
            "output": parsed,
        }
        rows.append(json.dumps(fact, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return _clip("\n".join(rows), max_chars)


def _memory_summary(stages: list[dict[str, Any]]) -> str:
    facts: list[str] = []
    for stage in stages:
        result = stage.get("result") if isinstance(stage.get("result"), dict) else {}
        parsed = result.get("parsed") if isinstance(result.get("parsed"), dict) else {}
        paths = parsed.get("affected_paths") if isinstance(parsed.get("affected_paths"), list) else []
        summary = str(result.get("summary") or "").strip()
        if summary:
            facts.append(summary)
        facts.extend(str(path) for path in paths[:20])
    return _clip("; ".join(dict.fromkeys(facts)), 1_000)


def _bm25(query: str, documents: list[str], *, k1: float = 1.5, b: float = 0.75) -> list[float]:
    if not documents:
        return []
    query_tokens = _tokens(query)
    tokenized = [_tokens(document) for document in documents]
    average_length = sum(len(tokens) for tokens in tokenized) / len(tokenized) or 1.0
    document_frequency = Counter(token for tokens in tokenized for token in set(tokens))
    scores: list[float] = []
    for tokens in tokenized:
        frequencies = Counter(tokens)
        score = 0.0
        for token in query_tokens:
            frequency = frequencies[token]
            if not frequency:
                continue
            documents_with_token = document_frequency[token]
            inverse_frequency = math.log(1 + (len(documents) - documents_with_token + 0.5) / (documents_with_token + 0.5))
            denominator = frequency + k1 * (1 - b + b * len(tokens) / average_length)
            score += inverse_frequency * frequency * (k1 + 1) / denominator
        scores.append(score)
    return scores


def _tokens(value: str) -> list[str]:
    lowered = value.lower()
    words = re.findall(r"[a-z0-9_]{2,}", lowered)
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", lowered))
    return [*words, *(chinese[index : index + 2] for index in range(max(0, len(chinese) - 1)))]


def _clip(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    if limit <= 1:
        return value[:limit]
    return value[: limit - 1] + "…"
