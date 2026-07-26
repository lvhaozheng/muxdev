"""Global Workbench project and Rule APIs."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..storage import ControlStore
from ..runtime.delivery_standards import available_verification_commands
from ..services.rule_sources import (
    RuleSourceError,
    fetch_public_webpage,
    normalize_uploaded_source,
    source_digest,
    store_source_markdown,
)
from ..workbench import utc_now
from ..workflows import load_workflow


router = APIRouter(prefix="/api/v2")


class ProjectCreateRequest(BaseModel):
    path: str = Field(min_length=1, max_length=2000)
    name: str | None = Field(default=None, min_length=1, max_length=120)


class ProjectUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)


class ProjectRuleBindingRequest(BaseModel):
    version: int = Field(ge=1)
    enabled: bool = True
    workflows: list[Literal["change", "design", "review", "test"]] = Field(
        default_factory=list
    )


class RuleVerifierV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["agent_review", "runtime_check", "artifact", "human_acceptance"]
    command_id: str | None = Field(default=None, max_length=80)
    capability: Literal["review", "security-review"] | None = None
    artifact_kind: str | None = Field(default=None, max_length=80)


class RuleDeliveryItemV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage_id: str = Field(min_length=1, max_length=80)
    deliverable: str = Field(min_length=1, max_length=300)
    completion: str = Field(min_length=1, max_length=300)
    proof: str = Field(min_length=1, max_length=300)
    verifier: RuleVerifierV1


class RuleSourceV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=100)
    kind: Literal["file", "url", "builtin"]
    display_name: str = Field(min_length=1, max_length=300)
    original_url: str | None = Field(default=None, max_length=4000)
    local_markdown_path: str | None = Field(default=None, max_length=1000)
    digest: str | None = Field(default=None, max_length=80)
    content_type: str | None = Field(default=None, max_length=120)
    size_bytes: int | None = Field(default=None, ge=0, le=5 * 1024 * 1024)
    captured_at: str | None = Field(default=None, max_length=80)
    license: str | None = Field(default=None, max_length=200)
    revision: str | None = Field(default=None, max_length=80)


class RuleSourceUrlRequest(BaseModel):
    url: str = Field(min_length=1, max_length=4000)


class RuleDefinitionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["muxdev.rule.v1"] = "muxdev.rule.v1"
    rule_id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    version: int = Field(default=1, ge=1)
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    kind: Literal[
        "code_standard",
        "ci_gate",
        "document_template",
        "delivery_standard",
    ]
    scope: Literal["builtin", "user"] = "user"
    workflows: list[Literal["change", "design", "review", "test"]] = Field(
        default_factory=list
    )
    path_patterns: list[str] = Field(default_factory=list, max_length=50)
    agent_roles: list[str] = Field(default_factory=list, max_length=20)
    instructions: str = Field(default="", max_length=262144)
    enforcement: Literal["advisory", "required"] = "required"
    delivery_items: list[RuleDeliveryItemV1] = Field(default_factory=list, max_length=30)
    template: str | None = Field(default=None, max_length=262144)
    source_documents: list[RuleSourceV1] = Field(default_factory=list, max_length=20)
    digest: str = ""

    @model_validator(mode="after")
    def validate_rule(self) -> "RuleDefinitionV1":
        if self.kind == "ci_gate":
            if not self.delivery_items:
                raise ValueError("CI Rule requires at least one delivery item")
            if any(item.verifier.type != "runtime_check" for item in self.delivery_items):
                raise ValueError("CI Rule delivery items must use runtime_check verifiers")
            if any(not item.verifier.command_id for item in self.delivery_items):
                raise ValueError("CI Rule checks must reference registered command IDs")
        if self.kind == "document_template" and not self.template:
            raise ValueError("document template Rule requires template content")
        payload = self.model_dump(exclude={"digest"}, mode="json")
        object.__setattr__(
            self,
            "digest",
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
        )
        return self


@router.get("/workbench/health")
def workbench_health(request: Request) -> dict[str, Any]:
    return {
        "service": "muxdev-workbench",
        "healthy": True,
        "instance_id": str(getattr(request.app.state, "instance_id", "")),
        "pid": os.getpid(),
        "host": str(getattr(request.app.state, "host", "127.0.0.1")),
        "port": int(getattr(request.app.state, "port", 8765)),
    }


@router.get("/projects")
def list_projects(request: Request) -> list[dict[str, Any]]:
    return request.app.state.workbench.list_projects()


@router.post("/projects", status_code=201)
def register_project(body: ProjectCreateRequest, request: Request) -> dict[str, Any]:
    try:
        return request.app.state.workbench.register(Path(body.path), name=body.name)
    except (FileNotFoundError, NotADirectoryError, OSError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/projects/{project_id}/snapshot")
def workbench_project_snapshot(
    project_id: str,
    request: Request,
) -> dict[str, Any]:
    try:
        project = request.app.state.workbench.project_snapshot(project_id)
        workspace = request.app.state.workbench.workspace(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    with ControlStore(workspace) as store:
        conversations = store.list_conversations(limit=200)
    return {
        "schema_version": "muxdev.project-snapshot.v1",
        "project": project,
        "conversations": conversations,
        "last_conversation_id": (
            str(conversations[0]["conversation_id"]) if conversations else None
        ),
    }


@router.get("/projects/{project_id}")
def project_snapshot(project_id: str, request: Request) -> dict[str, Any]:
    try:
        return request.app.state.workbench.project_snapshot(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.patch("/projects/{project_id}")
def update_project(
    project_id: str,
    body: ProjectUpdateRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        return request.app.state.workbench.store.update_project(
            project_id, name=body.name
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.delete("/projects/{project_id}")
def remove_project(project_id: str, request: Request) -> dict[str, Any]:
    project = request.app.state.workbench.store.get_project(project_id)
    if not project:
        raise HTTPException(404, f"project not found: {project_id}")
    path = Path(str(project["path"]))
    if path.is_dir():
        with ControlStore(path) as store:
            active = [
                session
                for conversation in store.list_conversations(limit=1000)
                for session in store.list_agent_sessions(
                    str(conversation["conversation_id"])
                )
                if session.get("status") not in {"closed", "failed"}
            ]
        if active:
            raise HTTPException(
                409,
                {
                    "code": "project_has_active_sessions",
                    "message": "停止项目中的活跃 Agent Session 后才能移除登记。",
                    "remediation": "在各 Conversation 的 Terminal 中停止 Session 后重试。",
                    "retryable": True,
                },
            )
    removed = request.app.state.workbench.store.remove_project(project_id)
    return {
        "project_id": project_id,
        "status": "removed",
        "path": removed["path"],
        "data_deleted": False,
    }


def _rule_response(item: dict[str, Any]) -> dict[str, Any]:
    value = dict(item.get("definition") or {})
    value["status"] = str(item.get("status") or "active")
    return value


def _source_response(request: Request, item: dict[str, Any]) -> dict[str, Any]:
    local_path = str(item.get("local_markdown_path") or "")
    target = (request.app.state.workbench.store.path.parent / local_path).resolve()
    markdown = ""
    try:
        target.relative_to(request.app.state.workbench.store.path.parent.resolve())
        markdown = target.read_text(encoding="utf-8")
    except (OSError, ValueError):
        markdown = ""
    return {
        "source_id": str(item["source_id"]),
        "kind": str(item["kind"]),
        "display_name": str(item["display_name"]),
        "original_url": item.get("original_url"),
        "local_markdown_path": local_path,
        "digest": str(item["digest"]),
        "content_type": str(item["content_type"]),
        "size_bytes": int(item["size_bytes"]),
        "captured_at": str(item["captured_at"]),
        "license": item.get("license"),
        "markdown": markdown,
    }


def _save_rule_source(
    request: Request,
    *,
    kind: Literal["file", "url"],
    markdown: str,
    display_name: str,
    content_type: str,
    original_url: str | None = None,
) -> dict[str, Any]:
    store = request.app.state.workbench.store
    digest, target = store_source_markdown(store.path.parent, markdown)
    identity = hashlib.sha256(
        f"{kind}\0{original_url or display_name}\0{digest}".encode("utf-8")
    ).hexdigest()[:24]
    item = store.put_rule_source(
        {
            "source_id": f"rule-src-{identity}",
            "kind": kind,
            "display_name": display_name,
            "original_url": original_url,
            "local_markdown_path": target.relative_to(store.path.parent).as_posix(),
            "digest": f"sha256:{digest}",
            "content_type": content_type,
            "size_bytes": len(markdown.encode("utf-8")),
            "license": None,
            "captured_at": utc_now(),
            "metadata": {},
        }
    )
    return _source_response(request, item)


@router.get("/rules")
def list_rules(
    request: Request,
    include_archived: bool = Query(default=False),
) -> list[dict[str, Any]]:
    return [
        _rule_response(item)
        for item in request.app.state.workbench.store.list_rules(
            include_archived=include_archived
        )
    ]


@router.get("/rule-sources")
def list_rule_sources(request: Request) -> list[dict[str, Any]]:
    return [
        _source_response(request, item)
        for item in request.app.state.workbench.store.list_rule_sources()
    ]


@router.post("/rule-sources/upload", status_code=201)
async def upload_rule_source(
    request: Request,
    filename: str = Query(min_length=1, max_length=300),
) -> dict[str, Any]:
    payload = await request.body()
    try:
        markdown, metadata = normalize_uploaded_source(
            filename,
            payload,
            content_type=str(request.headers.get("content-type") or ""),
        )
    except RuleSourceError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _save_rule_source(
        request,
        kind="file",
        markdown=markdown,
        display_name=str(metadata["display_name"]),
        content_type=str(metadata["content_type"]),
    )


@router.post("/rule-sources/import-url", status_code=201)
def import_rule_source_url(
    body: RuleSourceUrlRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        markdown, metadata = fetch_public_webpage(body.url)
    except RuleSourceError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _save_rule_source(
        request,
        kind="url",
        markdown=markdown,
        display_name=str(metadata["display_name"]),
        content_type=str(metadata["content_type"]),
        original_url=str(metadata["original_url"]),
    )


@router.post("/rules", status_code=201)
def create_rule(body: RuleDefinitionV1, request: Request) -> dict[str, Any]:
    if body.kind == "ci_gate":
        workflows = body.workflows or ["change"]
        for workflow_name in workflows:
            registered = available_verification_commands(
                load_workflow(workflow_name)
            )
            for item in body.delivery_items:
                command_id = str(item.verifier.command_id or "")
                if command_id not in registered:
                    raise HTTPException(
                        422,
                        {
                            "code": "rule_command_not_registered",
                            "message": (
                                f"CI Rule command is not registered for "
                                f"{workflow_name}: {command_id}"
                            ),
                            "remediation": (
                                "从所选 workflow 已冻结的 argv 化验证命令中选择。"
                            ),
                            "retryable": False,
                        },
                    )
    current = request.app.state.workbench.store.get_rule(body.rule_id)
    if current and int(current["version"]) >= body.version:
        raise HTTPException(
            409,
            "Rule version must be greater than the latest stored version",
        )
    for source in body.source_documents:
        if source.kind == "builtin":
            continue
        stored_source = request.app.state.workbench.store.get_rule_source(source.source_id)
        if not stored_source:
            raise HTTPException(422, f"Rule source not found: {source.source_id}")
        if source.digest and source.digest != stored_source.get("digest"):
            raise HTTPException(409, f"Rule source changed: {source.source_id}")
    stored = request.app.state.workbench.store.put_rule(body.model_dump(mode="json"))
    return _rule_response(stored)


@router.delete("/rules/{rule_id}")
def archive_rule(rule_id: str, request: Request) -> dict[str, Any]:
    try:
        return _rule_response(request.app.state.workbench.store.archive_rule(rule_id))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/rules/{rule_id}/restore")
def restore_rule(rule_id: str, request: Request) -> dict[str, Any]:
    try:
        return _rule_response(request.app.state.workbench.store.restore_rule(rule_id))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/projects/{project_id}/rules")
def project_rules(project_id: str, request: Request) -> dict[str, Any]:
    try:
        workspace = request.app.state.workbench.workspace(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    with ControlStore(workspace) as store:
        bindings = store.list_project_rule_bindings()
    items: list[dict[str, Any]] = []
    for binding in bindings:
        rule = request.app.state.workbench.store.get_rule(
            str(binding["rule_id"]), int(binding["version"])
        )
        items.append(
            {
                "binding": binding,
                "rule": dict((rule or {}).get("definition") or {}),
                "available": bool(rule),
            }
        )
    legacy = workspace / "MUXDEV.md"
    return {
        "project_id": project_id,
        "bindings": items,
        "library": [
            _rule_response(item)
            for item in request.app.state.workbench.store.list_rules()
        ],
        "archived_library": [
            _rule_response(item)
            for item in request.app.state.workbench.store.list_rules(
                include_archived=True
            )
            if str(item.get("status")) == "archived"
        ],
        "legacy_guidance": {
            "path": "MUXDEV.md",
            "available": legacy.is_file(),
            "managed": False,
        },
    }


@router.put("/projects/{project_id}/rules/{rule_id}")
def bind_project_rule(
    project_id: str,
    rule_id: str,
    body: ProjectRuleBindingRequest,
    request: Request,
) -> dict[str, Any]:
    rule = request.app.state.workbench.store.get_rule(rule_id, body.version)
    if not rule:
        raise HTTPException(404, f"Rule not found: {rule_id}@{body.version}")
    try:
        workspace = request.app.state.workbench.workspace(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    with ControlStore(workspace) as store:
        binding = store.bind_project_rule(
            rule_id,
            body.version,
            enabled=body.enabled,
            workflows=list(body.workflows),
        )
    return {
        "binding": binding,
        "rule": dict(rule.get("definition") or {}),
    }


__all__ = ["RuleDefinitionV1", "RuleSourceV1", "router"]
