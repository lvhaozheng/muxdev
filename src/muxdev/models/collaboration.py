"""Public contracts for CLI-native agents and Conversation orchestration."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ConversationMode(StrEnum):
    DIRECT = "direct"
    ORCHESTRATED = "orchestrated"
    LEGACY_PIPELINE = "legacy_pipeline"


class AssignmentStatus(StrEnum):
    PROPOSED = "proposed"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    REPORTED = "reported"
    VERIFYING = "verifying"
    READY_TO_MERGE = "ready_to_merge"
    MERGING = "merging"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentSessionStatus(StrEnum):
    STARTING = "starting"
    READY = "ready"
    BUSY = "busy"
    WAITING_INPUT = "waiting_input"
    DISCONNECTED = "disconnected"
    RESUMABLE = "resumable"
    REBUILT = "rebuilt_context"
    CLOSED = "closed"
    FAILED = "failed"


class OrchestrationPlanStatus(StrEnum):
    DRAFT = "draft"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"


class CliAdapterDefinition(BaseModel):
    """Thin argv-only bridge to a complete interactive coding CLI."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cli_id: str = Field(min_length=1, max_length=80)
    command: list[str] = Field(min_length=1)
    resume_command: list[str] = Field(default_factory=list)
    working_directory_arg: str | None = None
    model_arg: str | None = None
    env_allowlist: list[str] = Field(default_factory=list)
    supports_pty: bool = True
    supports_resize: bool = True
    supports_resume: bool = False
    native_session_id_pattern: str | None = None
    prefer_tmux: bool = False

    @model_validator(mode="after")
    def validate_argv_and_environment(self) -> "CliAdapterDefinition":
        values = self.command + self.resume_command
        if any(not isinstance(item, str) or not item for item in values):
            raise ValueError("CLI commands must be non-empty argv arrays")
        allowed_placeholders = {"{worktree}", "{model}", "{native_session_id}"}
        for item in values:
            for token in re.findall(r"\{[^{}]+\}", item):
                if token not in allowed_placeholders:
                    raise ValueError(f"unsupported CLI argv placeholder: {token}")
        if self.supports_resume and not self.resume_command:
            raise ValueError("resume-capable CLI adapters require resume_command")
        if len(self.env_allowlist) != len(set(self.env_allowlist)):
            raise ValueError("CLI environment allowlist entries must be unique")
        if any(not _ENV_NAME.fullmatch(name) for name in self.env_allowlist):
            raise ValueError("CLI environment allowlist contains an invalid name")
        if self.native_session_id_pattern:
            re.compile(self.native_session_id_pattern)
        return self


class AgentDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_id: str = Field(min_length=1, max_length=80)
    display_name: str = Field(min_length=1, max_length=120)
    cli_id: str = Field(min_length=1, max_length=80)
    model: str | None = None
    role_prompt: str = ""
    capability_tags: list[str] = Field(default_factory=list)
    can_orchestrate: bool = False
    max_concurrency: int = Field(default=1, ge=1, le=4)
    default_permissions: Literal["read", "workspace-write"] = "workspace-write"
    enabled: bool = True

    @model_validator(mode="after")
    def validate_capabilities(self) -> "AgentDefinition":
        if len(self.capability_tags) != len(set(self.capability_tags)):
            raise ValueError("agent capability tags must be unique")
        if self.can_orchestrate and "orchestrate" not in self.capability_tags:
            raise ValueError("orchestrator agents must include the orchestrate capability")
        return self


class DeliveryTriple(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    deliverables: list[str] = Field(min_length=1)
    completion: list[str] = Field(min_length=1)
    proof: list[str] = Field(min_length=1)


class OrchestrationNodeV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=200)
    brief: str = Field(min_length=1)
    agent_id: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=120)
    dependencies: list[str] = Field(default_factory=list)
    work_mode: Literal["consult", "write"]
    deliverables: list[str] = Field(min_length=1)
    completion: list[str] = Field(min_length=1)
    proof: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dependencies(self) -> "OrchestrationNodeV1":
        if self.id in self.dependencies:
            raise ValueError(f"orchestration node {self.id} cannot depend on itself")
        if len(self.dependencies) != len(set(self.dependencies)):
            raise ValueError(f"orchestration node {self.id} has duplicate dependencies")
        return self


class OrchestrationPlanV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["muxdev.orchestration-plan.v1"] = "muxdev.orchestration-plan.v1"
    summary: str = Field(min_length=1)
    nodes: list[OrchestrationNodeV1] = Field(min_length=1)
    max_parallel: int = Field(default=4, ge=1, le=4)

    @model_validator(mode="after")
    def validate_graph(self) -> "OrchestrationPlanV1":
        node_ids = [node.id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("orchestration node ids must be unique")
        known = set(node_ids)
        for node in self.nodes:
            unknown = set(node.dependencies) - known
            if unknown:
                raise ValueError(f"orchestration node {node.id} has unknown dependencies: {sorted(unknown)}")

        visiting: set[str] = set()
        visited: set[str] = set()
        dependencies = {node.id: node.dependencies for node in self.nodes}

        def visit(node_id: str) -> None:
            if node_id in visiting:
                raise ValueError("orchestration plan dependencies must be acyclic")
            if node_id in visited:
                return
            visiting.add(node_id)
            for dependency in dependencies[node_id]:
                visit(dependency)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in node_ids:
            visit(node_id)
        return self

