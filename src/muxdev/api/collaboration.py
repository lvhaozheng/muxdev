"""Version 2 HTTP and WebSocket surface for Conversation-native agents."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Literal, Mapping

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from ..runtime import ConversationService, RunEngine
from ..runtime.agent_sessions import (
    AgentSessionManager,
    SessionLifecycleError,
    agent_session_manager,
)
from ..runtime.collaboration_service import CollaborationService
from ..runtime.worktree import WorkspacePrepareError
from ..services.agents import AgentRegistry, AgentUnavailableError
from ..models.evidence import canonical_hash
from ..storage import ControlStore
from .auth import COOKIE_NAME, origin_is_trusted, session_for_app
from .project_context import workspace_for_request, workspace_for_websocket


router = APIRouter(prefix="/api/v2/projects/{project_id}")


class DeliverableRequest(BaseModel):
    type: Literal["code_change", "file", "report", "runnable", "answer", "other"]
    path: str | None = Field(default=None, max_length=500)
    name: str | None = Field(default=None, max_length=200)
    format: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    check_id: str | None = Field(default=None, max_length=80)


class ConversationV2CreateRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=12000)
    title: str | None = Field(default=None, max_length=120)
    mode: str = Field(default="direct", pattern="^(direct|orchestrated|legacy_pipeline)$")
    agent_id: str | None = None
    orchestrator_agent_id: str | None = None
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=50)
    allowed_scope: list[str] = Field(default_factory=list, max_length=100)
    delivery_standard: dict[str, Any] | None = None
    deliverables: list[DeliverableRequest] = Field(default_factory=list, max_length=20)
    auto_start_when_ready: bool = True
    profile: str = Field(default="standard", pattern="^(lite|standard|strict)$")
    max_cost_usd: float = Field(default=0.5, gt=0)
    max_parallel: int = Field(default=4, ge=1, le=4)
    rule_ids: list[str] = Field(default_factory=list, max_length=50)
    collaborator_agent_ids: list[str] = Field(default_factory=list, max_length=20)


class ConversationV2MessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=12000)
    recipients: list[str] = Field(default_factory=list, max_length=20)
    dispatch_kind: str | None = Field(default=None, pattern="^(message|consult|write|review)$")
    interaction_id: str | None = None


class ConversationRuleRevisionRequest(BaseModel):
    rule_ids: list[str] = Field(default_factory=list, max_length=50)


class PlanRequest(BaseModel):
    plan: dict[str, Any]


class PlanApprovalRequest(BaseModel):
    plan_id: str = Field(min_length=1)


class ReassignRequest(BaseModel):
    agent_id: str = Field(min_length=1)


class ReportRequest(BaseModel):
    manifest: dict[str, Any]


class InteractionResponseRequest(BaseModel):
    response: str = Field(min_length=1, max_length=12000)


def _workspace(request: Request) -> Path:
    return workspace_for_request(request)


@contextmanager
def _service(workspace: Path) -> Iterator[CollaborationService]:
    engine = RunEngine(workspace)
    try:
        conversations = ConversationService(engine, engine.store)
        yield CollaborationService(conversations, engine.store)
    finally:
        engine.store.close()


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, WorkspacePrepareError):
        return HTTPException(409, exc.detail())
    if isinstance(exc, (AgentUnavailableError, SessionLifecycleError)):
        return HTTPException(
            422 if isinstance(exc, AgentUnavailableError) else 409,
            exc.detail(),
        )
    if isinstance(exc, (FileNotFoundError, KeyError)):
        return HTTPException(404, str(exc))
    if isinstance(exc, PermissionError):
        return HTTPException(403, str(exc))
    return HTTPException(409 if isinstance(exc, RuntimeError) else 422, str(exc))


def _conversation_rules(
    request: Request,
    workspace: Path,
    selected_rule_ids: list[str],
) -> list[dict[str, Any]]:
    with ControlStore(workspace) as store:
        bindings = store.list_project_rule_bindings(enabled_only=True)
    selected = set(selected_rule_ids)
    references = {
        (str(item["rule_id"]), int(item["version"]))
        for item in bindings
        if not item.get("workflows") or "change" in item.get("workflows", [])
    }
    for rule_id in selected:
        latest = request.app.state.workbench.store.get_rule(rule_id)
        if not latest:
            raise ValueError(f"Rule not found: {rule_id}")
        references.add((rule_id, int(latest["version"])))
    definitions: list[dict[str, Any]] = []
    for rule_id, version in sorted(references):
        stored = request.app.state.workbench.store.get_rule(rule_id, version)
        if not stored:
            raise ValueError(f"Rule is unavailable: {rule_id}@{version}")
        definitions.append(dict(stored.get("definition") or {}))
    return definitions


def _compile_rule_standard(
    current: dict[str, Any] | None,
    rules: list[dict[str, Any]],
) -> dict[str, Any] | None:
    custom_items = list((current or {}).get("custom_items") or [])
    for rule in rules:
        if rule.get("enforcement") != "required":
            continue
        delivery_items = list(rule.get("delivery_items") or [])
        if not delivery_items:
            kind = str(rule.get("kind") or "")
            verifier: dict[str, str] = {"type": "agent_review"}
            stage_id = "review"
            deliverable = str(rule.get("title") or "Rule requirement")
            proof = "结构化 Agent ReviewEvidence"
            if kind == "document_template":
                stage_id = "implement"
                verifier = {"type": "artifact", "artifact_kind": "document"}
                proof = "内容寻址文档 artifact"
            delivery_items = [
                {
                    "stage_id": stage_id,
                    "deliverable": deliverable,
                    "completion": str(
                        rule.get("instructions")
                        or rule.get("description")
                        or f"{deliverable} 已满足"
                    )[:300],
                    "proof": proof,
                    "verifier": verifier,
                }
            ]
        for index, item in enumerate(delivery_items):
            identifier = hashlib.sha256(
                f"{rule['rule_id']}:{rule['version']}:{index}".encode()
            ).hexdigest()[:20]
            custom_items.append(
                {
                    "id": f"rule.{identifier}",
                    "stage_id": item["stage_id"],
                    "deliverable": item["deliverable"],
                    "completion": item["completion"],
                    "proof": item["proof"],
                    "verifier": item["verifier"],
                    "source": "conversation",
                    "required": True,
                }
            )
    return {"custom_items": custom_items} if custom_items else current


@router.get("/agents")
def agents(request: Request) -> list[dict[str, object]]:
    return AgentRegistry(_workspace(request)).list()


@router.post("/conversations", status_code=201)
def create_conversation(body: ConversationV2CreateRequest, request: Request) -> dict[str, Any]:
    workspace = _workspace(request)
    with _service(workspace) as service:
        try:
            collaborators = [
                agent_id
                for agent_id in dict.fromkeys(body.collaborator_agent_ids)
                if agent_id != body.agent_id
            ]
            registry = AgentRegistry(workspace)
            for agent_id in collaborators:
                registry.require_available(agent_id)
            rules = _conversation_rules(request, workspace, body.rule_ids)
            result = service.create(
                body.goal,
                title=body.title,
                mode=body.mode,
                agent_id=body.agent_id,
                orchestrator_agent_id=body.orchestrator_agent_id,
                acceptance_criteria=body.acceptance_criteria,
                allowed_scope=body.allowed_scope,
                delivery_standard=_compile_rule_standard(
                    body.delivery_standard, rules
                ),
                deliverables=[item.model_dump(exclude_none=True) for item in body.deliverables],
                # Freeze the Rule snapshot before a CLI receives its first
                # context pack.  Starting here would race the SessionManager
                # against the snapshot persisted immediately below.
                auto_start_when_ready=False,
                profile=body.profile,
                max_cost_usd=body.max_cost_usd,
                max_parallel=body.max_parallel,
            )
            conversation = dict(result["conversation"])
            contract_id = str(conversation.get("active_contract_id") or "") or None
            service.store.create_conversation_rule_snapshot(
                str(conversation["conversation_id"]),
                contract_id=contract_id,
                rules=rules,
                digest=canonical_hash(rules),
                metadata={"selected_rule_ids": body.rule_ids},
            )
            service.store.update_conversation(
                str(conversation["conversation_id"]),
                metadata={
                    "auto_start_when_ready": body.auto_start_when_ready,
                    "collaborator_agent_ids": collaborators,
                },
            )
            for agent_id in collaborators:
                try:
                    service._ensure_main_session(
                        str(conversation["conversation_id"]),
                        agent_id,
                    )
                except Exception as exc:
                    service.store.append_conversation_event(
                        str(conversation["conversation_id"]),
                        "session.collaborator_failed_to_start",
                        {"agent_id": agent_id, "reason": str(exc)},
                        actor="runtime",
                    )
            requirements = dict(
                (conversation.get("metadata") or {}).get("requirements") or {}
            )
            if body.auto_start_when_ready and requirements.get("status") == "ready":
                result = service.execute(str(conversation["conversation_id"]))
            result["rule_snapshot"] = service.store.latest_conversation_rule_snapshot(
                str(conversation["conversation_id"])
            )
            return result
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


@router.put("/conversations/{conversation_id}/rules")
def revise_conversation_rules(
    conversation_id: str,
    body: ConversationRuleRevisionRequest,
    request: Request,
) -> dict[str, Any]:
    workspace = _workspace(request)
    with _service(workspace) as service:
        try:
            conversation = service.store.get_conversation(conversation_id)
            if not conversation:
                raise FileNotFoundError(conversation_id)
            contract = service._active_contract(conversation_id)
            policy = dict(contract.get("policy") or {})
            current_standard = dict(policy.get("delivery_standard") or {})
            base_items = [
                item
                for item in list(current_standard.get("custom_items") or [])
                if not str(item.get("id") or "").startswith("rule.")
            ]
            rules = _conversation_rules(request, workspace, body.rule_ids)
            compiled = _compile_rule_standard(
                {"custom_items": base_items},
                rules,
            )
            detail = service.conversations.revise_contract(
                conversation_id,
                {"delivery_standard": compiled or {"custom_items": base_items}},
            )
            revised = detail["conversation"]
            new_contract_id = str(revised.get("active_contract_id") or "")
            snapshot = service.store.create_conversation_rule_snapshot(
                conversation_id,
                contract_id=new_contract_id or None,
                rules=rules,
                digest=canonical_hash(rules),
                metadata={"selected_rule_ids": body.rule_ids, "revision": True},
            )
            active_run_id = str(conversation.get("active_run_id") or "")
            if active_run_id:
                service.store.mark_verification_attempts(
                    active_run_id,
                    freshness="stale",
                )
                run = service.store.get_run(active_run_id)
                if run and run.get("review_state") == "pending":
                    service.store.settle_run_review(active_run_id, "superseded")
            service.store.append_conversation_event(
                conversation_id,
                "rules.revised",
                {
                    "snapshot_id": snapshot["snapshot_id"],
                    "contract_id": new_contract_id,
                    "rule_ids": body.rule_ids,
                },
                actor="developer",
            )
            return {
                "conversation": revised,
                "rule_snapshot": snapshot,
            }
        except (FileNotFoundError, KeyError, PermissionError, RuntimeError, ValueError) as exc:
            raise _http_error(exc) from exc


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
                interaction_id=body.interaction_id,
            )
        except (FileNotFoundError, KeyError, PermissionError, RuntimeError, ValueError) as exc:
            raise _http_error(exc) from exc


@router.post("/conversations/{conversation_id}/execute")
def execute_conversation(conversation_id: str, request: Request) -> dict[str, Any]:
    with _service(_workspace(request)) as service:
        try:
            return service.execute(conversation_id)
        except (FileNotFoundError, PermissionError, RuntimeError, ValueError) as exc:
            raise _http_error(exc) from exc


@router.post("/conversations/{conversation_id}/interactions/{interaction_id}/respond")
def respond_conversation_interaction(
    conversation_id: str,
    interaction_id: str,
    body: InteractionResponseRequest,
    request: Request,
) -> dict[str, Any]:
    with _service(_workspace(request)) as service:
        try:
            result = service.add_message(
                conversation_id,
                body.response,
                interaction_id=interaction_id,
            )
            return {**result, "conversation": service.get(conversation_id)}
        except (FileNotFoundError, PermissionError, RuntimeError, ValueError) as exc:
            raise _http_error(exc) from exc


@router.post("/sessions/{session_id}/resume")
def resume_agent_session(session_id: str, request: Request) -> dict[str, Any]:
    workspace = _workspace(request)
    manager = agent_session_manager(workspace)
    try:
        with ControlStore(workspace) as store:
            current = store.get_agent_session(session_id)
        if not current:
            raise FileNotFoundError(session_id)
        if str(current.get("status")) == "failed":
            raise SessionLifecycleError(
                "失败的 Session 必须由用户明确重新启动。",
                code="session_restart_required",
                remediation="点击“重新启动”，不要使用自动恢复。",
                retryable=True,
                session_id=session_id,
            )
        session = manager.ensure_live(session_id)
        with ControlStore(workspace) as store:
            generations = store.list_session_generations(session_id)
        return {"session": session, "generation": generations[-1] if generations else None}
    except (FileNotFoundError, PermissionError, RuntimeError, ValueError) as exc:
        raise _http_error(exc) from exc


@router.post("/sessions/{session_id}/restart")
def restart_agent_session(session_id: str, request: Request) -> dict[str, Any]:
    workspace = _workspace(request)
    with _service(workspace) as service:
        try:
            session = service.store.get_agent_session(session_id)
            if not session:
                raise FileNotFoundError(session_id)
            assignment_id = str(
                session.get("current_assignment_id")
                or session.get("assignment_id")
                or ""
            )
            assignment = (
                service.store.get_assignment(assignment_id)
                if assignment_id
                else None
            )
            if assignment and str(assignment.get("status")) == "failed":
                result = service.retry(assignment_id)
                if str(result.get("status")) in {"failed", "blocked"}:
                    raise SessionLifecycleError(
                        str(
                            (result.get("metadata") or {}).get("failure")
                            or (result.get("metadata") or {}).get("blocked_reason")
                            or "Session restart failed"
                        ),
                        code="session_restart_failed",
                        remediation="运行 Agent Doctor，修复终端能力后重试。",
                        retryable=True,
                        session_id=session_id,
                    )
            else:
                service.sessions.restart(session_id)
            updated = service.store.get_agent_session(session_id) or session
            if str(updated.get("status")) == "failed":
                raise SessionLifecycleError(
                    str(
                        (updated.get("metadata") or {}).get("launch_error")
                        or "Session restart failed"
                    ),
                    code="session_restart_failed",
                    remediation="运行 Agent Doctor，修复终端能力后重试。",
                    retryable=True,
                    session_id=session_id,
                )
            generations = service.store.list_session_generations(session_id)
            return {
                "session": updated,
                "generation": generations[-1] if generations else None,
                "assignment": (
                    service.store.get_assignment(assignment_id)
                    if assignment_id
                    else None
                ),
            }
        except (
            FileNotFoundError,
            KeyError,
            PermissionError,
            RuntimeError,
            ValueError,
        ) as exc:
            raise _http_error(exc) from exc


@router.post("/sessions/{session_id}/interrupt")
def interrupt_agent_session(session_id: str, request: Request) -> dict[str, Any]:
    try:
        return {
            "session": agent_session_manager(_workspace(request)).interrupt(
                session_id,
                actor="developer",
            ),
            "status": "interrupted",
        }
    except (FileNotFoundError, PermissionError, RuntimeError, ValueError) as exc:
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
    try:
        workspace = workspace_for_websocket(websocket)
    except FileNotFoundError as exc:
        await websocket.close(code=4404, reason=str(exc))
        return
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
        web_session = session_for_app(websocket.app.state, token or "")
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
                try:
                    cursor, lease_id, record = await _handle_terminal_message(
                        websocket,
                        manager,
                        workspace,
                        session_id,
                        holder,
                        record,
                        message,
                        cursor,
                        lease_id,
                    )
                except (FileNotFoundError, PermissionError, RuntimeError, ValueError) as exc:
                    detail = (
                        exc.detail()
                        if isinstance(
                            exc,
                            (AgentUnavailableError, SessionLifecycleError),
                        )
                        else {
                            "code": "terminal_operation_failed",
                            "message": str(exc),
                            "remediation": "检查 Session 状态后重试。",
                            "retryable": True,
                        }
                    )
                    await websocket.send_json({"type": "error", **detail})
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


async def _handle_terminal_message(
    websocket: WebSocket,
    manager: AgentSessionManager,
    workspace: Path,
    session_id: str,
    holder: str,
    record: Mapping[str, Any],
    message: Mapping[str, Any],
    cursor: int,
    lease_id: str,
) -> tuple[int, str, Mapping[str, Any]]:
    kind = str(message.get("type") or "")
    if kind == "attach":
        cursor = max(0, int(message.get("after_seq") or 0))
        with ControlStore(workspace) as store:
            record = store.get_agent_session(session_id) or record
        if str(record.get("status")) == "resumable":
            record = manager.ensure_live(session_id)
        snapshot = manager.snapshot(session_id)
        terminal_info = (record.get("metadata") or {}).get("terminal") or {}
        if snapshot["attached"] and terminal_info.get("resize"):
            manager.resize(
                session_id,
                int(message.get("cols") or 120),
                int(message.get("rows") or 32),
            )
        if bool(message.get("request_write")) and snapshot["attached"]:
            lease = manager.acquire_write_lease(
                session_id,
                holder=holder,
                takeover=bool(message.get("takeover")),
            )
            lease_id = str(lease.get("lease_id") or "")
            await websocket.send_json({"type": "lease", **lease})
        elif bool(message.get("request_write")):
            await websocket.send_json({
                "type": "lease",
                "granted": False,
                "reason": "session_not_attached",
            })
        await websocket.send_json({
            "type": "status",
            "session": manager.snapshot(session_id),
        })
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
            session_id,
            holder=holder,
            lease_id=lease_id,
        )
        lease_id = ""
        await websocket.send_json({
            "type": "lease",
            "granted": False,
            "released": released,
        })
    elif kind == "heartbeat":
        if not lease_id:
            raise PermissionError("terminal write lease is not held")
        lease = manager.renew_write_lease(
            session_id,
            holder=holder,
            lease_id=lease_id,
        )
        await websocket.send_json({"type": "lease", **lease})
    elif kind == "interrupt":
        session = manager.interrupt(session_id, actor=holder)
        await websocket.send_json({"type": "status", "session": session})
    else:
        await websocket.send_json({"type": "error", "code": "unsupported_frame"})
    return cursor, lease_id, record


__all__ = ["router"]
