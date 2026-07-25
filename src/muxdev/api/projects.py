"""Global Workbench project and Rule APIs."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..storage import ControlStore
from ..runtime.delivery_standards import available_verification_commands
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
    instructions: str = Field(default="", max_length=12000)
    enforcement: Literal["advisory", "required"] = "required"
    delivery_items: list[RuleDeliveryItemV1] = Field(default_factory=list, max_length=30)
    template: str | None = Field(default=None, max_length=12000)
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


@router.get("/rules")
def list_rules(request: Request) -> list[dict[str, Any]]:
    return [
        dict(item.get("definition") or {})
        for item in request.app.state.workbench.store.list_rules()
    ]


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
    stored = request.app.state.workbench.store.put_rule(body.model_dump(mode="json"))
    return dict(stored.get("definition") or {})


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
            dict(item.get("definition") or {})
            for item in request.app.state.workbench.store.list_rules()
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


__all__ = ["RuleDefinitionV1", "router"]
