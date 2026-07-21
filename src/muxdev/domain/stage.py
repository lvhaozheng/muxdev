"""Stage execution contracts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class McpServerGrant:
    """One trusted local MCP server projected into a provider session."""

    name: str
    command: str
    args: tuple[str, ...] = ()
    env_names: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "transport": "stdio",
            "command": self.command,
            "args": list(self.args),
            "env_names": list(self.env_names),
            "tools": list(self.tools),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "McpServerGrant":
        return cls(
            name=str(value.get("name") or ""),
            command=str(value.get("command") or ""),
            args=tuple(str(item) for item in value.get("args", ()) if isinstance(item, str)),
            env_names=tuple(str(item) for item in value.get("env_names", ()) if isinstance(item, str)),
            tools=tuple(str(item) for item in value.get("tools", ()) if isinstance(item, str)),
        )


@dataclass(frozen=True)
class CapabilityGrant:
    """Frozen authority passed to a provider for exactly one stage."""

    read_workspace: bool = True
    write_workspace: bool = False
    shell: bool = False
    network: bool = False
    secret_names: tuple[str, ...] = ()
    mcp_tools: tuple[str, ...] = ()
    mcp_servers: tuple[McpServerGrant, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "read_workspace": self.read_workspace,
            "write_workspace": self.write_workspace,
            "shell": self.shell,
            "network": self.network,
            "secret_names": list(self.secret_names),
            "mcp_tools": list(self.mcp_tools),
            "mcp_servers": [item.to_dict() for item in self.mcp_servers],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "CapabilityGrant":
        raw_servers = value.get("mcp_servers")
        servers = raw_servers if isinstance(raw_servers, list) else []
        return cls(
            read_workspace=bool(value.get("read_workspace", True)),
            write_workspace=bool(value.get("write_workspace")),
            shell=bool(value.get("shell")),
            network=bool(value.get("network")),
            secret_names=tuple(str(item) for item in value.get("secret_names", ()) if isinstance(item, str)),
            mcp_tools=tuple(str(item) for item in value.get("mcp_tools", ()) if isinstance(item, str)),
            mcp_servers=tuple(McpServerGrant.from_dict(item) for item in servers if isinstance(item, Mapping)),
        )

    @property
    def digest(self) -> str:
        payload = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class ProviderCapabilities:
    """Capabilities proven or declared for one concrete provider runtime."""

    protocol: str
    streaming: bool = False
    cancel: bool = False
    permissions: bool = False
    mcp_config: bool = False
    mcp_tool_allowlist: bool = False
    mcp_call_events: bool = False
    isolated_config: bool = False
    session_resume: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "protocol": self.protocol,
            "streaming": self.streaming,
            "cancel": self.cancel,
            "permissions": self.permissions,
            "mcp_config": self.mcp_config,
            "mcp_tool_allowlist": self.mcp_tool_allowlist,
            "mcp_call_events": self.mcp_call_events,
            "isolated_config": self.isolated_config,
            "session_resume": self.session_resume,
        }


@dataclass(frozen=True)
class ProviderEvent:
    """Protocol-neutral provider observation retained for evidence and audit."""

    kind: str
    status: str = "observed"
    details: Mapping[str, object] | None = None


@dataclass(frozen=True)
class UsageRecord:
    provider: str
    tokens: int = 0
    cost_usd: float = 0.0


@dataclass(frozen=True)
class AttemptFeedback:
    """Bounded, redacted facts from the immediately preceding model attempt."""

    prior_attempt: int
    failure_kind: str
    error_codes: tuple[str, ...] = ()
    validation_errors: tuple[str, ...] = ()
    failed_checks: tuple[Mapping[str, object], ...] = ()
    review_blockers: tuple[Mapping[str, object], ...] = ()
    prior_output_excerpt: str = ""
    prior_output_digest: str = ""
    workspace_diff_digest: str = ""
    history: tuple[Mapping[str, object], ...] = ()
    instruction: str = "Correct the previous attempt and satisfy the declared output contract."

    def to_dict(self) -> dict[str, object]:
        return {
            "prior_attempt": self.prior_attempt,
            "failure_kind": self.failure_kind,
            "error_codes": list(self.error_codes),
            "validation_errors": list(self.validation_errors),
            "failed_checks": [dict(item) for item in self.failed_checks],
            "review_blockers": [dict(item) for item in self.review_blockers],
            "prior_output_excerpt": self.prior_output_excerpt,
            "prior_output_digest": self.prior_output_digest,
            "workspace_diff_digest": self.workspace_diff_digest,
            "history": [dict(item) for item in self.history],
            "instruction": self.instruction,
        }


@dataclass(frozen=True)
class StageExecutionInput:
    run_id: str
    stage_id: str
    role: str | None
    task: str
    worktree: Path
    context: Mapping[str, object]
    capabilities: CapabilityGrant
    provider: str
    policy: Mapping[str, object]
    feedback: AttemptFeedback | None = None
    skills: tuple[Mapping[str, object], ...] = ()
    attempt: int = 1

    @classmethod
    def for_provider(
        cls,
        *,
        stage_id: str,
        task: str,
        worktree: Path,
        provider: str,
        run_id: str = "unbound",
    ) -> "StageExecutionInput":
        """Build the minimal input used by direct adapter checks."""
        return cls(
            run_id=run_id,
            stage_id=stage_id,
            role=None,
            task=task,
            worktree=worktree,
            context={},
            capabilities=CapabilityGrant(),
            provider=provider,
            policy={},
        )


@dataclass(frozen=True)
class StageExecutionResult:
    artifact_name: str
    content: str
    summary: str
    stage_id: str = ""
    provider: str = ""
    status: str = "completed"
    tokens: int = 0
    cost_usd: float = 0.0
    returncode: int = 0
    stdout_hash: str | None = None
    stderr_hash: str | None = None
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    output_refs: tuple[str, ...] = ()
    interaction_requests: tuple[Mapping[str, object], ...] = ()
    protocol: str = "unknown"
    event_count: int = 0
    session_id: str | None = None
    events: tuple[ProviderEvent, ...] = ()
    stdout_content: str | None = None
    stderr_content: str | None = None
    timed_out: bool = False
    cancelled: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False

    @property
    def usage(self) -> UsageRecord:
        return UsageRecord(provider=self.provider or "unknown", tokens=self.tokens, cost_usd=self.cost_usd)
