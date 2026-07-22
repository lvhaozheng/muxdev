"""Version 2 HTTP and WebSocket surface for Conversation-native agents."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from ..runtime import ConversationService, RunEngine
from ..runtime.agent_sessions import agent_session_manager
from ..runtime.collaboration_service import CollaborationService
from ..services.agents import AgentRegistry
from ..storage import ControlStore
from .auth import COOKIE_NAME, origin_is_trusted, session_for_token


router = APIRouter(prefix="/api/v2")


class ConversationV2CreateRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=12000)
    title: str | None = Field(default=None, max_length=120)
    mode: str = Field(default="direct", pattern="^(direct|orchestrated|legacy_pipeline)$")
    agent_id: str | None = None
    orchestrator_agent_id: str | None = None
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=50)
    allowed_scope: list[str] = Field(default_factory=list, max_length=100)
    delivery_standard: dict[str, Any] | None = None
    profile: str = Field(default="standard", pattern="^(lite|standard|strict)$")
    max_cost_usd: float = Field(default=0.5, gt=0)
    max_parallel: int = Field(default=4, ge=1, le=4)


class ConversationV2MessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=12000)
    recipients: list[str] = Field(default_factory=list, max_length=20)
    dispatch_kind: str | None = Field(default=None, pattern="^(message|consult|write)$")


class PlanRequest(BaseModel):
    plan: dict[str, Any]


class PlanApprovalRequest(BaseModel):
    plan_id: str = Field(min_length=1)


class ReassignRequest(BaseModel):
    agent_id: str = Field(min_length=1)


class ReportRequest(BaseModel):
    manifest: dict[str, Any]


def _workspace(request: Request) -> Path:
    return request.app.state.workspace


@contextmanager
def _service(workspace: Path) -> Iterator[CollaborationService]:
    engine = RunEngine(workspace)
    try:
        conversations = ConversationService(engine, engine.store)
        yield CollaborationService(conversations, engine.store)
    finally:
        engine.store.close()


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (FileNotFoundError, KeyError)):
        return HTTPException(404, str(exc))
    if isinstance(exc, PermissionError):
        return HTTPException(403, str(exc))
    return HTTPException(409 if isinstance(exc, RuntimeError) else 422, str(exc))


@router.get("/agents")
def agents(request: Request) -> list[dict[str, object]]:
    return AgentRegistry(_workspace(request)).list()


@router.post("/conversations", status_code=201)
def create_conversation(body: ConversationV2CreateRequest, request: Request) -> dict[str, Any]:
    with _service(_workspace(request)) as service:
        try:
            return service.create(
                body.goal,
                title=body.title,
                mode=body.mode,
                agent_id=body.agent_id,
                orchestrator_agent_id=body.orchestrator_agent_id,
                acceptance_criteria=body.acceptance_criteria,
                allowed_scope=body.allowed_scope,
                delivery_standard=body.delivery_standard,
                profile=body.profile,
                max_cost_usd=body.max_cost_usd,
                max_parallel=body.max_parallel,
            )
        except (FileNotFoundError, KeyError, PermissionError, RuntimeError, ValueError) as exc:
            raise _http_error(exc) from exc


@router.get("/conversations")
def list_conversations(
    request: Request,
    status: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    with _service(_workspace(request)) as service:
        items = service.conversations.list(status=status, limit=limit)
        for item in items:
            item["assignment_count"] = len(service.store.list_assignments(str(item["conversation_id"])))
            item["session_count"] = len(service.store.list_agent_sessions(str(item["conversation_id"])))
        return items


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: str, request: Request) -> dict[str, Any]:
    with _service(_workspace(request)) as service:
        try:
            return service.get(conversation_id)
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            raise _http_error(exc) from exc


@router.post("/conversations/{conversation_id}/messages", status_code=202)
def add_message(
    conversation_id: str,
    body: ConversationV2MessageRequest,
    request: Request,
) -> dict[str, Any]:
    with _service(_workspace(request)) as service:
        try:
            return service.add_message(
                conversation_id,
                body.content,
                recipients=body.recipients,
                dispatch_kind=body.dispatch_kind,
            )
        except (FileNotFoundError, KeyError, PermissionError, RuntimeError, ValueError) as exc:
            raise _http_error(exc) from exc


@router.post("/conversations/{conversation_id}/orchestration/propose")
def propose_plan(conversation_id: str, body: PlanRequest, request: Request) -> dict[str, Any]:
    with _service(_workspace(request)) as service:
        try:
            return service.propose_plan(conversation_id, body.plan)
        except (FileNotFoundError, KeyError, PermissionError, RuntimeError, ValueError) as exc:
            raise _http_error(exc) from exc


@router.post("/conversations/{conversation_id}/orchestration/approve")
def approve_plan(
    conversation_id: str,
    body: PlanApprovalRequest,
    request: Request,
) -> dict[str, Any]:
    with _service(_workspace(request)) as service:
        try:
            return service.approve_plan(conversation_id, body.plan_id)
        except (FileNotFoundError, KeyError, PermissionError, RuntimeError, ValueError) as exc:
            raise _http_error(exc) from exc


@router.post("/conversations/{conversation_id}/orchestration/revise")
def revise_plan(conversation_id: str, body: PlanRequest, request: Request) -> dict[str, Any]:
    with _service(_workspace(request)) as service:
        try:
            return service.revise_plan(conversation_id, body.plan)
        except (FileNotFoundError, KeyError, PermissionError, RuntimeError, ValueError) as exc:
            raise _http_error(exc) from exc


@router.post("/assignments/{assignment_id}/report")
def report_assignment(assignment_id: str, body: ReportRequest, request: Request) -> dict[str, Any]:
    with _service(_workspace(request)) as service:
        try:
            return service.report(assignment_id, body.manifest)
        except (FileNotFoundError, PermissionError, RuntimeError, ValueError) as exc:
            raise _http_error(exc) from exc


@router.post("/assignments/{assignment_id}/retry")
def retry_assignment(assignment_id: str, request: Request) -> dict[str, Any]:
    with _service(_workspace(request)) as service:
        try:
            return service.retry(assignment_id)
        except (FileNotFoundError, PermissionError, RuntimeError, ValueError) as exc:
            raise _http_error(exc) from exc


@router.post("/assignments/{assignment_id}/reassign")
def reassign_assignment(
    assignment_id: str,
    body: ReassignRequest,
    request: Request,
) -> dict[str, Any]:
    with _service(_workspace(request)) as service:
        try:
            return service.reassign(assignment_id, body.agent_id)
        except (FileNotFoundError, KeyError, PermissionError, RuntimeError, ValueError) as exc:
            raise _http_error(exc) from exc


@router.post("/assignments/{assignment_id}/cancel")
def cancel_assignment(assignment_id: str, request: Request) -> dict[str, Any]:
    with _service(_workspace(request)) as service:
        try:
            return service.cancel(assignment_id)
        except (FileNotFoundError, PermissionError, RuntimeError, ValueError) as exc:
            raise _http_error(exc) from exc


@router.post("/conversations/{conversation_id}/verification/approve")
def approve_verification(conversation_id: str, request: Request) -> dict[str, Any]:
    with _service(_workspace(request)) as service:
        try:
            return service.approve_verification(conversation_id)
        except (FileNotFoundError, PermissionError, RuntimeError, ValueError) as exc:
            raise _http_error(exc) from exc


@router.post("/deliveries/{candidate_id}/accept")
def accept_delivery(candidate_id: str, request: Request) -> dict[str, Any]:
    with _service(_workspace(request)) as service:
        candidate = service.store.get_delivery_candidate(candidate_id)
        if not candidate:
            raise HTTPException(404, f"delivery candidate not found: {candidate_id}")
        try:
            accepted = service.conversations.accept_delivery(
                str(candidate["conversation_id"]), candidate_id
            )
        except (FileNotFoundError, PermissionError, RuntimeError, ValueError) as exc:
            raise _http_error(exc) from exc
        return {"candidate": accepted, "status": "accepted"}


@router.websocket("/sessions/{session_id}/terminal")
async def terminal_websocket(websocket: WebSocket, session_id: str) -> None:
    workspace = Path(websocket.app.state.workspace)
    require_auth = bool(getattr(websocket.app.state, "require_auth", False))
    origin = websocket.headers.get("origin", "")
    host = websocket.headers.get("host", "")
    configured = tuple(getattr(websocket.app.state, "trusted_origins", ()))
    if not origin or not origin_is_trusted(origin, host, configured):
        await websocket.close(code=4403, reason="origin is not trusted")
        return
    token = websocket.cookies.get(COOKIE_NAME)
    web_session: Mapping[str, object] | None = None
    if require_auth:
        web_session = session_for_token(workspace, token or "")
        if not web_session:
            await websocket.close(code=4401, reason="authentication required")
            return
    with ControlStore(workspace) as store:
        record = store.get_agent_session(session_id)
    if not record:
        await websocket.close(code=4404, reason="agent session not found")
        return
    holder = str((web_session or {}).get("device_id") or f"local:{websocket.client.host if websocket.client else 'browser'}")
    manager = agent_session_manager(workspace)
    await websocket.accept()
    cursor = 0
    lease_id = ""
    timestamps: list[float] = []
    try:
        while True:
            try:
                frame = await asyncio.wait_for(websocket.receive_text(), timeout=0.2)
            except TimeoutError:
                frame = ""
            if frame:
                if len(frame.encode("utf-8")) > 65536:
                    await websocket.send_json({"type": "error", "code": "frame_too_large"})
                    continue
                now = time.monotonic()
                timestamps = [item for item in timestamps if now - item < 10]
                if len(timestamps) >= 100:
                    await websocket.send_json({"type": "error", "code": "rate_limited"})
                    continue
                timestamps.append(now)
                try:
                    message = json.loads(frame)
                except json.JSONDecodeError:
                    await websocket.send_json({"type": "error", "code": "invalid_json"})
                    continue
                kind = str(message.get("type") or "")
                try:
                    if kind == "attach":
                        cursor = max(0, int(message.get("after_seq") or 0))
                        manager.resize(session_id, int(message.get("cols") or 120), int(message.get("rows") or 32))
                        if bool(message.get("request_write")):
                            lease = manager.acquire_write_lease(
                                session_id,
                                holder=holder,
                                takeover=bool(message.get("takeover")),
                            )
                            lease_id = str(lease.get("lease_id") or "")
                            await websocket.send_json({"type": "lease", **lease})
                        await websocket.send_json({"type": "status", "session": manager.snapshot(session_id)})
                    elif kind == "input":
                        manager.write(
                            session_id,
                            str(message.get("data") or ""),
                            holder=holder,
                            lease_id=lease_id,
                        )
                    elif kind == "resize":
                        session = manager.resize(
                            session_id,
                            int(message.get("cols") or 120),
                            int(message.get("rows") or 32),
                        )
                        await websocket.send_json({"type": "status", "session": session})
                    elif kind == "release_write":
                        released = bool(lease_id) and manager.release_write_lease(
                            session_id, holder=holder, lease_id=lease_id
                        )
                        lease_id = ""
                        await websocket.send_json({"type": "lease", "granted": False, "released": released})
                    else:
                        await websocket.send_json({"type": "error", "code": "unsupported_frame"})
                except (FileNotFoundError, PermissionError, RuntimeError, ValueError) as exc:
                    await websocket.send_json({"type": "error", "message": str(exc)})
            events = manager.events(session_id, after_seq=cursor, limit=1000)
            for event in events:
                cursor = max(cursor, int(event["seq"]))
                if event.get("direction") in {"output", "system"}:
                    await websocket.send_json(
                        {"type": "output", "seq": event["seq"], "data": event["data"]}
                    )
    except WebSocketDisconnect:
        pass
    finally:
        if lease_id:
            manager.release_write_lease(session_id, holder=holder, lease_id=lease_id)


__all__ = ["router"]
