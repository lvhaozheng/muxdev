"""Versioned HTTP surface for long-running trusted-delivery conversations."""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..runtime import CONVERSATION_ACTIONS, ConversationService, RunEngine
from ..storage import ControlStore


router = APIRouter(prefix="/api/v1")


class ConversationCreateRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=12000)
    title: str | None = Field(default=None, max_length=120)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=20)
    allowed_scope: list[str] = Field(default_factory=list, max_length=50)
    workflow: str = Field(default="change", pattern="^(change|design|review|test)$")
    profile: str = Field(default="standard", pattern="^(lite|standard|strict)$")
    provider: str = Field(default="mock", min_length=1)
    role_providers: dict[str, str] = Field(default_factory=dict, max_length=7)
    max_cost_usd: float = Field(default=0.5, gt=0)
    auto_start: bool = True


class ConversationMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=12000)
    intent: str = Field(default="change", pattern="^(discuss|answer|change|verify)$")
    reply_to: str | None = None
    auto_start: bool = True


class ConversationActionRequest(BaseModel):
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)


def _workspace(request: Request) -> Path:
    return request.app.state.workspace


@contextmanager
def _service(workspace: Path) -> Iterator[ConversationService]:
    engine = RunEngine(workspace)
    try:
        yield ConversationService(engine, engine.store)
    finally:
        engine.store.close()


def _not_found(exc: FileNotFoundError) -> HTTPException:
    return HTTPException(404, str(exc) or "conversation resource not found")


@router.post("/conversations", status_code=201)
def create_conversation(
    body: ConversationCreateRequest,
    request: Request,
    background: BackgroundTasks,
) -> dict[str, Any]:
    with _service(_workspace(request)) as service:
        try:
            conversation = service.create(
                body.goal,
                title=body.title,
                acceptance_criteria=body.acceptance_criteria,
                allowed_scope=body.allowed_scope,
                workflow=body.workflow,
                profile=body.profile,
                provider=body.provider,
                role_providers=body.role_providers,
                max_cost_usd=body.max_cost_usd,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    conversation_id = str(conversation["conversation"]["conversation_id"])
    if body.auto_start:
        background.add_task(_start_background, _workspace(request), conversation_id)
    return {**conversation, "queued": body.auto_start}


@router.get("/conversations")
def list_conversations(
    request: Request,
    status: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    with _service(_workspace(request)) as service:
        return service.list(status=status, limit=limit)


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, request: Request) -> dict[str, Any]:
    with _service(_workspace(request)) as service:
        try:
            return service.get(conversation_id)
        except FileNotFoundError as exc:
            raise _not_found(exc) from exc


@router.post("/conversations/{conversation_id}/messages", status_code=202)
def add_message(
    conversation_id: str,
    body: ConversationMessageRequest,
    request: Request,
    background: BackgroundTasks,
) -> dict[str, Any]:
    with _service(_workspace(request)) as service:
        try:
            result = service.add_message(
                conversation_id, body.content, intent=body.intent, reply_to=body.reply_to
            )
        except FileNotFoundError as exc:
            raise _not_found(exc) from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
    resume_required = bool(result.get("resume_required"))
    queued = body.auto_start and body.intent in {"change", "verify"}
    if queued:
        background.add_task(_start_background, _workspace(request), conversation_id)
    elif resume_required:
        queued = True
        background.add_task(_resume_interaction_background, _workspace(request), conversation_id)
    return {**result, "queued": queued}


@router.post("/conversations/{conversation_id}/actions", status_code=202)
def conversation_action(
    conversation_id: str,
    body: ConversationActionRequest,
    request: Request,
    background: BackgroundTasks,
) -> dict[str, Any]:
    if body.action not in CONVERSATION_ACTIONS:
        raise HTTPException(422, f"unsupported conversation action: {body.action}")
    if body.idempotency_key:
        with ControlStore(_workspace(request)) as store:
            if not store.get_conversation(conversation_id):
                raise HTTPException(404, f"conversation not found: {conversation_id}")
            claimed = store.claim_conversation_action(
                conversation_id,
                idempotency_key=body.idempotency_key,
                action=body.action,
                payload=body.payload,
            )
        if not claimed:
            return {"conversation_id": conversation_id, "action": body.action, "status": "duplicate"}
    background_actions = {"start_work", "continue", "verify", "auto_recover"}
    if body.action in background_actions:
        background.add_task(
            _action_background, _workspace(request), conversation_id, body.action, body.payload
        )
        return {"conversation_id": conversation_id, "action": body.action, "status": "queued"}
    with _service(_workspace(request)) as service:
        try:
            result = service.action(conversation_id, body.action, body.payload)
        except FileNotFoundError as exc:
            raise _not_found(exc) from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
    queued = body.action == "respond_interaction" and bool(result.get("resume_required"))
    if queued:
        background.add_task(_resume_interaction_background, _workspace(request), conversation_id)
    return {
        "conversation_id": conversation_id,
        "action": body.action,
        "status": "queued" if queued else "completed",
        "result": result,
    }


@router.get("/conversations/{conversation_id}/events")
def conversation_events(
    conversation_id: str,
    request: Request,
    after: int = Query(0, ge=0),
) -> Any:
    accept = request.headers.get("accept", "")
    cursor = after
    try:
        cursor = max(cursor, int(request.headers.get("last-event-id") or 0))
    except ValueError:
        raise HTTPException(400, "Last-Event-ID must be an integer")
    if "text/event-stream" in accept:
        return StreamingResponse(
            _event_stream(_workspace(request), conversation_id, request, cursor),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    with ControlStore(_workspace(request)) as store:
        if not store.get_conversation(conversation_id):
            raise HTTPException(404, f"conversation not found: {conversation_id}")
        return store.conversation_events(conversation_id, after=cursor)


@router.get("/conversations/{conversation_id}/deliveries")
def conversation_deliveries(conversation_id: str, request: Request) -> list[dict[str, Any]]:
    with ControlStore(_workspace(request)) as store:
        if not store.get_conversation(conversation_id):
            raise HTTPException(404, f"conversation not found: {conversation_id}")
        return [
            item for item in store.delivery_candidates(conversation_id)
            if item["status"] in {"verified", "accepted"}
        ]


@router.get("/deliveries/{candidate_id}/evidence")
def delivery_evidence(candidate_id: str, request: Request) -> dict[str, Any]:
    with ControlStore(_workspace(request)) as store:
        candidate = store.get_delivery_candidate(candidate_id)
    if not candidate:
        raise HTTPException(404, f"delivery candidate not found: {candidate_id}")
    path = Path(str(candidate.get("evidence_path") or ""))
    if not path.is_file():
        raise HTTPException(404, "delivery evidence not found")
    return json.loads(path.read_text(encoding="utf-8"))


@router.post("/deliveries/{candidate_id}/accept")
def accept_delivery(candidate_id: str, request: Request) -> dict[str, Any]:
    with ControlStore(_workspace(request)) as store:
        candidate = store.get_delivery_candidate(candidate_id)
    if not candidate:
        raise HTTPException(404, f"delivery candidate not found: {candidate_id}")
    with _service(_workspace(request)) as service:
        try:
            result = service.accept_delivery(str(candidate["conversation_id"]), candidate_id)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
    return {"candidate": result, "status": "accepted"}


async def _event_stream(workspace: Path, conversation_id: str, request: Request, after: int):
    cursor = after
    while not await request.is_disconnected():
        with ControlStore(workspace) as store:
            if not store.get_conversation(conversation_id):
                yield "event: error\ndata: {\"error\":\"conversation not found\"}\n\n"
                return
            events = store.conversation_events(conversation_id, after=cursor)
        if events:
            for event in events:
                cursor = int(event["sequence"])
                data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                yield f"id: {cursor}\nevent: conversation\ndata: {data}\n\n"
        else:
            yield ": heartbeat\n\n"
        await asyncio.sleep(1)


def _start_background(workspace: Path, conversation_id: str) -> None:
    with _service(workspace) as service:
        try:
            service.start_work(conversation_id)
        except Exception as exc:
            service.record_background_failure(
                conversation_id, "run.failed_to_start", exc,
                run_id=str(service.get(conversation_id)["conversation"].get("active_run_id") or "") or None,
            )


def _action_background(workspace: Path, conversation_id: str, action: str, payload: dict[str, Any]) -> None:
    with _service(workspace) as service:
        try:
            service.action(conversation_id, action, payload)
        except Exception as exc:
            detail = service.get(conversation_id)
            run_id = str(detail["conversation"].get("active_run_id") or "") or None
            event_type = "recovery.failed" if action == "auto_recover" else "run.failed_to_start"
            service.record_background_failure(
                conversation_id, event_type, exc, run_id=run_id
            )


def _resume_interaction_background(workspace: Path, conversation_id: str) -> None:
    with _service(workspace) as service:
        try:
            service.resume_interaction(conversation_id)
        except Exception as exc:
            detail = service.get(conversation_id)
            run_id = str(detail["conversation"].get("active_run_id") or "") or None
            service.record_background_failure(
                conversation_id, "recovery.failed", exc, run_id=run_id
            )
