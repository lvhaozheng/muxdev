"""Conversation-oriented v2 APIs over the durable activity ledger."""

from __future__ import annotations

import asyncio
import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Literal
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..core.platforms import hidden_subprocess_kwargs
from ..models import (
    ActivityEventV2,
    ChangeSetViewV1,
    FileChangeView,
    ReviewReference,
    ReviewRecordV1,
    ReviewState,
    ReviewViewV1,
    ConversationSnapshotV1,
    VerificationAttemptView,
)
from ..runtime import ConversationService, RunEngine
from ..runtime.agent_sessions import agent_session_manager
from ..runtime.change_tracking import (
    BlobStore,
    ChangeTrackingContext,
    ChangeTrackingService,
    RollbackConflictError,
)
from ..runtime.collaboration_service import CollaborationService
from ..runtime.workspace import snapshot_workspace
from ..services.agents import AgentRegistry
from ..services.conversation_memory import (
    correct_conversation_checkpoint,
    ensure_conversation_checkpoint,
)
from ..storage import ControlStore
from ..storage.activity_feed import activity_feed
from .project_context import workspace_for_request
from .projects import RuleDefinitionV1


router = APIRouter(prefix="/api/v2/projects/{project_id}")


class RequestChangesBody(BaseModel):
    message: str = Field(min_length=1, max_length=12000)
    references: list[ReviewReference] = Field(default_factory=list, max_length=100)


class PreviewRegistrationBody(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    process_id: str = Field(min_length=1, max_length=80)
    cwd: str = Field(min_length=1, max_length=1000)


class MemoryCandidateBody(BaseModel):
    rule: str = Field(min_length=3, max_length=2000)
    source_review_id: str | None = Field(default=None, max_length=120)


class MemoryCorrectionBody(BaseModel):
    correction: str = Field(min_length=1, max_length=4000)


def _workspace(request: Request) -> Path:
    return workspace_for_request(request)


@contextmanager
def _collaboration(workspace: Path) -> Iterator[CollaborationService]:
    engine = RunEngine(workspace)
    service = CollaborationService(
        ConversationService(engine, engine.store),
        engine.store,
    )
    service.registry = AgentRegistry(workspace)
    try:
        yield service
    finally:
        engine.store.close()


def _conversation_or_404(
    store: ControlStore, conversation_id: str
) -> dict[str, Any]:
    conversation = store.get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(404, f"conversation not found: {conversation_id}")
    return conversation


def _run_for(
    store: ControlStore,
    conversation: dict[str, Any],
    run_id: str | None,
) -> dict[str, Any]:
    identifier = run_id or str(conversation.get("active_run_id") or "")
    run = store.get_run(identifier) if identifier else None
    if not run or str(run.get("conversation_id") or "") != str(
        conversation["conversation_id"]
    ):
        raise HTTPException(404, f"run not found for conversation: {identifier}")
    return run


def _attention(status: str) -> str:
    if status == "needs_user":
        return "needs_you"
    if status in {"candidate_ready", "awaiting_acceptance"}:
        return "ready"
    if status in {"working", "verifying", "recovering", "clarifying"}:
        return "active"
    return "history"


def _next_actions(conversation: dict[str, Any], run: dict[str, Any] | None) -> list[str]:
    status = str(conversation["status"])
    if status == "needs_user":
        return ["respond", "continue", "close"]
    if status in {"candidate_ready", "awaiting_acceptance"}:
        return ["request_changes", "discard", "accept"]
    if status in {"working", "verifying", "recovering"}:
        return ["pause"]
    if status == "idle":
        return ["continue", "close"]
    if run and run.get("review_state") == "answered":
        return ["continue", "close"]
    return ["reopen"] if status == "closed" else ["continue"]


def _review_record_view(item: dict[str, Any]) -> ReviewRecordV1:
    return ReviewRecordV1(
        review_id=str(item["review_id"]),
        conversation_id=str(item["conversation_id"]),
        run_id=str(item["run_id"]) if item.get("run_id") else None,
        assignment_id=(
            str(item["assignment_id"]) if item.get("assignment_id") else None
        ),
        session_id=str(item["session_id"]) if item.get("session_id") else None,
        kind=str(item["kind"]),
        status=str(item["status"]),
        prompt=str(item.get("prompt") or ""),
        options=list(item.get("options") or []),
        response=str(item["response"]) if item.get("response") else None,
        references=list(item.get("references_json") or []),
        actor={
            "kind": str(item.get("actor_kind") or "runtime"),
            "id": str(item.get("actor_id") or "runtime"),
        },
        created_at=str(item["created_at"]),
        resolved_at=(
            str(item["resolved_at"]) if item.get("resolved_at") else None
        ),
        metadata=dict(item.get("metadata") or {}),
    )


@router.get(
    "/conversations/{conversation_id}/snapshot",
    response_model=ConversationSnapshotV1,
)
def conversation_snapshot(
    conversation_id: str,
    request: Request,
    after: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
) -> ConversationSnapshotV1:
    workspace = _workspace(request)
    with _collaboration(workspace) as service:
        try:
            detail = service.snapshot_context(conversation_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        conversation = dict(detail["conversation"])
        activity = service.store.conversation_activity(
            conversation_id, after=after, limit=limit
        )
        active_run = service.store.get_run(
            str(conversation.get("active_run_id") or "")
        )
        changes = (
            service.store.file_changes(str(active_run["run_id"]), capture_grade="verified")
            if active_run else []
        )
        attempts = (
            service.store.verification_attempts(str(active_run["run_id"]))
            if active_run else []
        )
        memory = service.store.latest_memory_checkpoint(conversation_id)
        rule_snapshot = service.store.latest_conversation_rule_snapshot(conversation_id)
        manager = agent_session_manager(workspace)
        session_snapshots = [
            manager.snapshot(str(item["session_id"]))
            for item in detail.get("sessions") or []
        ]
        return ConversationSnapshotV1(
            conversation=conversation,
            attention=_attention(str(conversation["status"])),
            active_turn=active_run,
            participants=list(detail.get("participants") or []),
            sessions=session_snapshots,
            assignments=list(detail.get("assignments") or []),
            orchestration_plans=list(detail.get("orchestration_plans") or []),
            interactions=list(detail.get("interactions") or []),
            timeline=[ActivityEventV2.model_validate(item) for item in activity],
            tool_summaries={
                "changes": {
                    "files": len(changes),
                    "additions": sum(int(item["additions"]) for item in changes),
                    "deletions": sum(int(item["deletions"]) for item in changes),
                },
                "verification": {
                    "attempts": len(attempts),
                    "current": sum(
                        1 for item in attempts if item["freshness"] == "current"
                    ),
                },
                "terminal": {
                    "sessions": len(detail.get("sessions") or []),
                    "active": sum(
                        1
                        for item in detail.get("sessions") or []
                        if item["status"] not in {"closed", "failed"}
                    ),
                },
                "memory": {
                    "checkpoint_id": (
                        str(memory["checkpoint_id"]) if memory else None
                    ),
                    "version": int(memory["version"]) if memory else 0,
                    "through_sequence": (
                        int(memory["through_sequence"]) if memory else 0
                    ),
                },
                "rules": {
                    "snapshot_id": (
                        str(rule_snapshot["snapshot_id"]) if rule_snapshot else None
                    ),
                    "count": len((rule_snapshot or {}).get("rules") or []),
                    "version": int((rule_snapshot or {}).get("version") or 0),
                    "digest": str((rule_snapshot or {}).get("digest") or ""),
                    "frozen": list((rule_snapshot or {}).get("rules") or []),
                },
            },
            next_actions=_next_actions(conversation, active_run),
            last_sequence=int(activity[-1]["sequence"]) if activity else after,
        )


@router.get("/conversations/{conversation_id}/stream")
def activity_stream(
    conversation_id: str,
    request: Request,
    after: int = Query(0, ge=0),
) -> StreamingResponse:
    try:
        cursor = max(after, int(request.headers.get("last-event-id") or 0))
    except ValueError as exc:
        raise HTTPException(400, "Last-Event-ID must be an integer") from exc
    with ControlStore(_workspace(request)) as store:
        _conversation_or_404(store, conversation_id)
    return StreamingResponse(
        _stream_events(_workspace(request), conversation_id, request, cursor),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _stream_events(
    workspace: Path,
    conversation_id: str,
    request: Request,
    after: int,
):
    cursor = after
    while not await request.is_disconnected():
        with ControlStore(workspace) as store:
            if not store.get_conversation(conversation_id):
                yield 'event: error\ndata: {"error":"conversation not found"}\n\n'
                return
            events = store.conversation_activity(
                conversation_id, after=cursor, limit=1000
            )
        if events:
            for event in events:
                cursor = int(event["sequence"])
                yield (
                    f"id: {cursor}\nevent: activity\ndata: "
                    + json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                    + "\n\n"
                )
            continue
        await asyncio.to_thread(
            activity_feed.wait, workspace, conversation_id, cursor, 1.0
        )
        yield ": heartbeat\n\n"


def _changes_view(
    store: ControlStore,
    conversation_id: str,
    run_id: str,
) -> ChangeSetViewV1:
    records = store.file_changes(run_id, capture_grade="verified")
    files = [
        FileChangeView(
            change_id=str(item["change_id"]),
            path=str(item["path"]),
            kind=str(item["kind"]),
            before_hash=(
                str(item["before_hash"]) if item.get("before_hash") else None
            ),
            after_hash=str(item["after_hash"]) if item.get("after_hash") else None,
            patch=str(item.get("patch") or ""),
            additions=int(item["additions"]),
            deletions=int(item["deletions"]),
            capture_grade=str(item["capture_grade"]),
            created_at=str(item["created_at"]),
        )
        for item in records
    ]
    return ChangeSetViewV1(
        conversation_id=conversation_id,
        run_id=run_id,
        files=files,
        additions=sum(item.additions for item in files),
        deletions=sum(item.deletions for item in files),
        verified=bool(records),
    )


@router.get(
    "/conversations/{conversation_id}/changes",
    response_model=ChangeSetViewV1,
)
def conversation_changes(
    conversation_id: str,
    request: Request,
    run_id: str | None = None,
) -> ChangeSetViewV1:
    with ControlStore(_workspace(request)) as store:
        conversation = _conversation_or_404(store, conversation_id)
        run = _run_for(store, conversation, run_id)
        return _changes_view(store, conversation_id, str(run["run_id"]))


@router.get("/conversations/{conversation_id}/file")
def conversation_file(
    conversation_id: str,
    request: Request,
    path: str = Query(min_length=1, max_length=1000),
    run_id: str | None = None,
    view: Literal["baseline", "current", "diff"] = "current",
) -> dict[str, Any]:
    workspace = _workspace(request)
    with ControlStore(workspace) as store:
        conversation = _conversation_or_404(store, conversation_id)
        run = _run_for(store, conversation, run_id)
        metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
        worktree = Path(str(metadata.get("worktree") or ""))
        if not worktree.is_dir():
            assignment = store.get_assignment(str(run.get("assignment_id") or ""))
            worktree = Path(str((assignment or {}).get("worktree") or ""))
        target = _safe_conversation_path(worktree, path)
        if _sensitive_conversation_path(path):
            raise HTTPException(403, "sensitive file content is metadata-only")
        if view == "diff":
            record = next(
                (
                    item
                    for item in reversed(
                        store.file_changes(
                            str(run["run_id"]), capture_grade="verified"
                        )
                    )
                    if item["path"] == path
                ),
                None,
            )
            content = str((record or {}).get("patch") or "")
        elif view == "baseline":
            baseline = next(
                (
                    item
                    for item in store.file_baselines(str(run["run_id"]))
                    if item["path"] == path
                ),
                None,
            )
            if not baseline or not int(baseline["existed"]):
                content = ""
            else:
                content = _baseline_content(
                    workspace, worktree, baseline
                ).decode("utf-8", errors="replace")
        else:
            if not target.is_file():
                raise HTTPException(404, f"file not found: {path}")
            raw = target.read_bytes()
            if b"\0" in raw:
                return {
                    "path": path,
                    "view": view,
                    "binary": True,
                    "size": len(raw),
                    "content": "",
                    "truncated": False,
                }
            content = raw.decode("utf-8", errors="replace")
        truncated = len(content.encode("utf-8")) > 1024 * 1024
        if truncated:
            content = content.encode("utf-8")[: 1024 * 1024].decode(
                "utf-8", errors="ignore"
            )
        return {
            "path": path,
            "view": view,
            "binary": False,
            "size": len(content.encode("utf-8")),
            "content": content,
            "truncated": truncated,
        }


@router.get(
    "/conversations/{conversation_id}/review",
    response_model=ReviewViewV1,
)
def conversation_review(
    conversation_id: str,
    request: Request,
    run_id: str | None = None,
) -> ReviewViewV1:
    with ControlStore(_workspace(request)) as store:
        conversation = _conversation_or_404(store, conversation_id)
        run = _run_for(store, conversation, run_id)
        identifier = str(run["run_id"])
        candidates = [
            item
            for item in store.delivery_candidates(conversation_id)
            if str(item["run_id"]) == identifier
        ]
        candidate = candidates[-1] if candidates else None
        attempts = [
            VerificationAttemptView.model_validate(item)
            for item in store.verification_attempts(identifier)
        ]
        changes = _changes_view(store, conversation_id, identifier)
        review_state = ReviewState(str(run.get("review_state") or "pending"))
        changed = len(changes.files)
        outcome = {
            ReviewState.ACCEPTED: f"已接受本轮 {changed} 个文件变更",
            ReviewState.ROLLED_BACK: f"已安全回退本轮 {changed} 个文件变更",
            ReviewState.ANSWERED: "已回答",
            ReviewState.SUPERSEDED: "本轮已由下一轮取代",
        }.get(
            review_state,
            f"本轮变更 {changed} 个文件" if changed else "本轮没有已验证的文件变化",
        )
        actions: list[str] = []
        if review_state == ReviewState.PENDING and candidate:
            actions = ["request_changes", "discard", "accept"]
        records = [
            _review_record_view(item)
            for item in store.list_review_records(conversation_id)
        ]
        return ReviewViewV1(
            conversation_id=conversation_id,
            run_id=identifier,
            outcome=outcome,
            review_state=review_state,
            changes=changes,
            verification_attempts=attempts,
            candidate_id=str(candidate["candidate_id"]) if candidate else None,
            available_actions=actions,
            records=records,
        )


@router.get("/conversations/{conversation_id}/review/history")
def review_history(
    conversation_id: str,
    request: Request,
    run_id: str | None = None,
) -> list[ReviewRecordV1]:
    with ControlStore(_workspace(request)) as store:
        _conversation_or_404(store, conversation_id)
        return [
            _review_record_view(item)
            for item in store.list_review_records(conversation_id, run_id=run_id)
        ]


@router.post("/conversations/{conversation_id}/review/request-changes")
def request_changes(
    conversation_id: str,
    body: RequestChangesBody,
    request: Request,
) -> dict[str, Any]:
    workspace = _workspace(request)
    with _collaboration(workspace) as service:
        conversation = service.store.get_conversation(conversation_id)
        if not conversation:
            raise HTTPException(404, f"conversation not found: {conversation_id}")
        run_id = str(conversation.get("active_run_id") or "")
        candidate_id = str(conversation.get("active_candidate_id") or "")
        agent_id = str(conversation.get("primary_agent_id") or "")
        try:
            service.registry.require_available(agent_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
        references = [
            item.model_dump(exclude_none=True) for item in body.references
        ]
        worktree = Path(
            str((conversation.get("metadata") or {}).get("worktree") or "")
        )
        for reference in references:
            _safe_conversation_path(worktree, str(reference["path"]))
        content = body.message
        if references:
            content += "\n\nReview references:\n" + "\n".join(
                f"- {item['path']}:{item.get('line') or 1}"
                for item in references
            )
        try:
            routed = service.add_message(
                conversation_id,
                content,
                recipients=[agent_id],
                dispatch_kind="direct",
            )
        except (RuntimeError, ValueError, PermissionError) as exc:
            raise HTTPException(409, str(exc)) from exc
        recipients = list(routed.get("recipients") or [])
        service.store.append_conversation_event(
            conversation_id,
            "review.changes_requested",
            {"message": body.message, "references": references},
            actor="developer",
            run_id=run_id or None,
        )
        review = service.store.create_review_record(
            conversation_id,
            kind="change_request",
            status="open",
            prompt=body.message,
            response=body.message,
            references=references,
            actor_kind="developer",
            actor_id="developer",
            run_id=run_id or None,
            metadata={
                "target_agent_id": agent_id,
                "routed_assignments": recipients,
            },
        )
        if run_id:
            service.store.settle_run_review(run_id, "superseded")
            service.store.mark_verification_attempts(
                run_id, freshness="superseded"
            )
        if candidate_id:
            candidate = service.store.get_delivery_candidate(candidate_id)
            if candidate and candidate["status"] not in {"accepted", "invalidated"}:
                service.store.update_delivery_candidate(
                    candidate_id, status="invalidated"
                )
        return {
            "conversation_id": conversation_id,
            "status": "working",
            "routed": recipients,
            "review": _review_record_view(review).model_dump(mode="json"),
        }


@router.post("/deliveries/{candidate_id}/discard")
def discard_delivery(candidate_id: str, request: Request) -> dict[str, Any]:
    workspace = _workspace(request)
    with ControlStore(workspace) as store:
        candidate = store.get_delivery_candidate(candidate_id)
        if not candidate:
            raise HTTPException(404, f"delivery candidate not found: {candidate_id}")
        if candidate["status"] == "accepted":
            raise HTTPException(409, "accepted delivery cannot be discarded")
        conversation_id = str(candidate["conversation_id"])
        run = store.get_run(str(candidate["run_id"])) or {}
        metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
        worktree = Path(str(metadata.get("worktree") or ""))
        context = ChangeTrackingContext(
            conversation_id=conversation_id,
            run_id=str(candidate["run_id"]),
            worktree=worktree,
        )
        try:
            restored = ChangeTrackingService(workspace, store).rollback(context)
        except RollbackConflictError as exc:
            raise HTTPException(
                409,
                {
                    "code": "rollback_conflict",
                    "conflicts": exc.conflicts,
                },
            ) from exc
        store.update_delivery_candidate(
            candidate_id,
            status="invalidated",
            metadata={"discarded": True, "restored_files": restored},
        )
        store.append_conversation_event(
            conversation_id,
            "workspace.rolled_back",
            {
                "candidate_id": candidate_id,
                "run_id": str(candidate["run_id"]),
                "restored_files": restored,
            },
            actor="runtime",
            run_id=str(candidate["run_id"]),
            capture_grade="verified",
        )
        store.create_review_record(
            conversation_id,
            kind="decision",
            status="rolled_back",
            prompt="回退本轮交付",
            response="用户确认安全回退本轮变更",
            actor_kind="developer",
            actor_id="developer",
            run_id=str(candidate["run_id"]),
            metadata={"candidate_id": candidate_id, "restored_files": restored},
        )
        store.settle_run_review(str(candidate["run_id"]), "rolled_back")
        store.update_conversation(
            conversation_id,
            status="idle",
            metadata={"baseline_manifest": snapshot_workspace(worktree).to_dict()},
        )
        return {
            "candidate_id": candidate_id,
            "status": "rolled_back",
            "restored_files": restored,
        }


@router.get("/conversations/{conversation_id}/memory/checkpoints")
def memory_checkpoints(
    conversation_id: str,
    request: Request,
) -> list[dict[str, Any]]:
    with ControlStore(_workspace(request)) as store:
        _conversation_or_404(store, conversation_id)
        return store.list_memory_checkpoints(conversation_id)


@router.post("/conversations/{conversation_id}/memory/compact")
def compact_memory(
    conversation_id: str,
    request: Request,
) -> dict[str, Any]:
    with ControlStore(_workspace(request)) as store:
        _conversation_or_404(store, conversation_id)
        checkpoint = ensure_conversation_checkpoint(
            store,
            conversation_id,
            force=True,
            created_by="developer",
        )
        if not checkpoint:
            raise HTTPException(409, "Conversation has no activity to compact")
        return checkpoint


@router.post("/conversations/{conversation_id}/memory/corrections", status_code=201)
def correct_memory(
    conversation_id: str,
    body: MemoryCorrectionBody,
    request: Request,
) -> dict[str, Any]:
    with ControlStore(_workspace(request)) as store:
        _conversation_or_404(store, conversation_id)
        try:
            return correct_conversation_checkpoint(
                store,
                conversation_id,
                body.correction,
                actor_id="developer",
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc


@router.get("/conversations/{conversation_id}/replay")
def conversation_replay(
    conversation_id: str,
    request: Request,
    depth: Literal["recap", "explore", "verify"] = "recap",
) -> dict[str, Any]:
    with ControlStore(_workspace(request)) as store:
        conversation = _conversation_or_404(store, conversation_id)
        events = store.conversation_activity(conversation_id, limit=1000)
        chain_valid, chain_errors = store.verify_conversation_event_chain(
            conversation_id
        )
        if depth == "verify":
            selected = [
                item for item in events if item["capture_grade"] == "verified"
            ]
        elif depth == "recap":
            selected = [
                item
                for item in events
                if item["type"]
                in {
                    "requirements.ready",
                    "interaction.responded",
                    "assignment.started",
                    "assignment.reported",
                    "workspace.reconciled",
                    "delivery.candidate_ready",
                    "delivery.accepted",
                    "delivery.answered",
                    "workspace.rolled_back",
                    "review.changes_requested",
                }
            ][-40:]
        else:
            selected = events
        return {
            "schema_version": "muxdev.replay.v1",
            "conversation_id": conversation_id,
            "title": conversation["title"],
            "depth": depth,
            "chain": {"valid": chain_valid, "errors": chain_errors},
            "events": selected,
            "counts": {
                grade: sum(
                    1 for item in events if item["capture_grade"] == grade
                )
                for grade in ("recorded", "observed", "verified")
            },
        }


@router.post("/sessions/{session_id}/preview")
def register_session_preview(
    session_id: str,
    body: PreviewRegistrationBody,
    request: Request,
) -> dict[str, Any]:
    parsed = urlparse(body.url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(
            422, "preview URL must be an explicit loopback URL with a valid port"
        ) from exc
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or port is None
    ):
        raise HTTPException(422, "preview URL must be an explicit loopback URL with a port")
    with ControlStore(_workspace(request)) as store:
        session = store.get_agent_session(session_id)
        if not session:
            raise HTTPException(404, f"agent session not found: {session_id}")
        generation = store.get_session_generation(
            session_id, int(session.get("generation") or 0)
        )
        if (
            not generation
            or str(generation.get("process_id") or "") != body.process_id
        ):
            raise HTTPException(409, "preview process does not match the active Session Generation")
        worktree = Path(str(session["worktree"])).resolve()
        cwd = Path(body.cwd).resolve()
        if cwd != worktree and worktree not in cwd.parents:
            raise HTTPException(409, "preview cwd is outside the Session worktree")
        preview = {
            "url": body.url,
            "process_id": body.process_id,
            "cwd": str(cwd),
            "generation": int(session["generation"]),
            "sandbox": "allow-scripts allow-forms",
        }
        store.update_agent_session(session_id, metadata={"preview": preview})
        store.append_conversation_event(
            str(session["conversation_id"]),
            "preview.registered",
            {"session_id": session_id, **preview},
            actor=str(session["agent_id"]),
            session_id=session_id,
            generation=int(session["generation"]),
        )
        return preview


@router.get("/sessions/{session_id}/preview")
def get_session_preview(session_id: str, request: Request) -> dict[str, Any]:
    with ControlStore(_workspace(request)) as store:
        session = store.get_agent_session(session_id)
        if not session:
            raise HTTPException(404, f"agent session not found: {session_id}")
        preview = (session.get("metadata") or {}).get("preview")
        if not isinstance(preview, dict):
            raise HTTPException(404, "Session has no registered preview")
        if int(preview.get("generation") or 0) != int(session.get("generation") or 0):
            raise HTTPException(409, "registered preview belongs to an older Session Generation")
        return preview


@router.get("/conversations/{conversation_id}/memory/candidates")
def memory_candidates(conversation_id: str, request: Request) -> list[dict[str, Any]]:
    with ControlStore(_workspace(request)) as store:
        _conversation_or_404(store, conversation_id)
        events = store.conversation_events(conversation_id)
    candidates: dict[str, dict[str, Any]] = {}
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        candidate_id = str(payload.get("candidate_id") or "")
        if not candidate_id:
            continue
        if event["type"] == "memory.candidate":
            candidates[candidate_id] = {
                **payload,
                "status": "pending",
                "created_at": event["created_at"],
            }
        elif event["type"] in {"memory.approved", "memory.rejected"} and candidate_id in candidates:
            candidates[candidate_id]["status"] = event["type"].split(".")[-1]
    return list(candidates.values())


@router.post("/conversations/{conversation_id}/memory/candidates", status_code=201)
def create_memory_candidate(
    conversation_id: str,
    body: MemoryCandidateBody,
    request: Request,
) -> dict[str, Any]:
    candidate_id = f"mem_{uuid4().hex}"
    with ControlStore(_workspace(request)) as store:
        _conversation_or_404(store, conversation_id)
        store.append_conversation_event(
            conversation_id,
            "memory.candidate",
            {
                "candidate_id": candidate_id,
                "rule": body.rule.strip(),
                "source_review_id": body.source_review_id,
            },
            actor="developer",
        )
    return {
        "candidate_id": candidate_id,
        "rule": body.rule.strip(),
        "source_review_id": body.source_review_id,
        "status": "pending",
    }


@router.post("/conversations/{conversation_id}/memory/candidates/{candidate_id}/approve")
def approve_memory_candidate(
    conversation_id: str,
    candidate_id: str,
    request: Request,
) -> dict[str, Any]:
    workspace = _workspace(request)
    candidates = {
        item["candidate_id"]: item
        for item in memory_candidates(conversation_id, request)
    }
    candidate = candidates.get(candidate_id)
    if not candidate:
        raise HTTPException(404, f"memory candidate not found: {candidate_id}")
    if candidate["status"] != "pending":
        raise HTTPException(409, f"memory candidate is already {candidate['status']}")
    rule = str(candidate["rule"]).strip()
    rule_id = f"memory.{candidate_id}"
    definition = RuleDefinitionV1(
        rule_id=rule_id,
        version=1,
        title=rule[:80],
        description="由 Conversation Review 反馈形成并经用户批准的项目规则。",
        kind="code_standard",
        scope="user",
        instructions=rule,
        enforcement="advisory",
    )
    request.app.state.workbench.store.put_rule(definition.model_dump(mode="json"))
    with ControlStore(workspace) as store:
        store.bind_project_rule(
            rule_id,
            1,
            enabled=True,
            metadata={
                "source": "memory_candidate",
                "conversation_id": conversation_id,
                "candidate_id": candidate_id,
            },
        )
        store.append_conversation_event(
            conversation_id,
            "memory.approved",
            {
                "candidate_id": candidate_id,
                "rule": rule,
                "rule_id": rule_id,
                "version": 1,
            },
            actor="developer",
            capture_grade="verified",
        )
    return {
        "candidate_id": candidate_id,
        "status": "approved",
        "rule_id": rule_id,
        "version": 1,
    }


def _safe_conversation_path(root: Path, relative: str) -> Path:
    root = root.resolve()
    value = Path(relative)
    if value.is_absolute() or ".." in value.parts:
        raise HTTPException(400, "file path must stay inside the worktree")
    current = root
    for part in value.parts:
        current = current / part
        if current.is_symlink():
            raise HTTPException(400, "symlink file paths are not allowed")
    target = current.resolve(strict=False)
    if target != root and root not in target.parents:
        raise HTTPException(400, "file path escapes the worktree")
    return target


def _sensitive_conversation_path(relative: str) -> bool:
    value = Path(relative.lower())
    return (
        value.name in {
            ".env", ".npmrc", ".pypirc", "credentials", "credentials.json",
            "secrets.json", "id_rsa", "id_ed25519",
        }
        or value.suffix in {".pem", ".key", ".p12", ".pfx"}
        or any(part in {"secrets", ".ssh", ".aws", ".azure"} for part in value.parts)
    )


def _baseline_content(
    workspace: Path,
    worktree: Path,
    baseline: dict[str, Any],
) -> bytes:
    if baseline.get("blob_hash"):
        try:
            return BlobStore(workspace).get(str(baseline["blob_hash"]))
        except FileNotFoundError:
            pass
    git_oid = str(baseline.get("git_oid") or "")
    if not git_oid:
        raise HTTPException(404, "baseline content is unavailable")
    result = subprocess.run(
        ["git", "cat-file", "blob", git_oid],
        cwd=worktree,
        capture_output=True,
        check=False,
        **hidden_subprocess_kwargs(),
    )
    if result.returncode != 0:
        raise HTTPException(404, "baseline Git blob is unavailable")
    return result.stdout if isinstance(result.stdout, bytes) else result.stdout.encode()


__all__ = ["router"]
