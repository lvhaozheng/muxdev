"""Run contracts shared by entrypoints, daemon services, and runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .ids import new_run_id


@dataclass(frozen=True)
class SkillRef:
    """A normalized skill reference bound to a run or workflow stage."""

    name: str
    role: str | None = None
    path: str | None = None
    injection: str = "prompt"
    reason: str | None = None
    content: str | None = None

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "SkillRef":
        return cls(
            name=str(payload.get("name") or payload.get("id") or "skill"),
            role=str(payload["role"]) if payload.get("role") else None,
            path=str(payload.get("path") or payload.get("skill_file") or "") or None,
            injection=str(payload.get("injection") or "prompt"),
            reason=str(payload["reason"]) if payload.get("reason") else None,
            content=str(payload["content"]) if payload.get("content") else None,
        )

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {"name": self.name, "injection": self.injection}
        if self.role:
            payload["role"] = self.role
        if self.path:
            payload["path"] = self.path
        if self.reason:
            payload["reason"] = self.reason
        if self.content:
            payload["content"] = self.content
        return payload


@dataclass(frozen=True)
class AutomationDecision:
    """Automation metadata chosen before a run is submitted."""

    payload: Mapping[str, object] = field(default_factory=dict)

    @property
    def intent(self) -> str | None:
        value = self.payload.get("intent")
        return str(value) if value else None

    @classmethod
    def from_payload(cls, payload: Mapping[str, object] | None) -> "AutomationDecision":
        return cls(dict(payload or {}))

    def to_payload(self) -> dict[str, object]:
        return dict(self.payload)


@dataclass(frozen=True)
class PolicySpec:
    """Policy knobs that should be stable across submit/resume."""

    approval_types: frozenset[str] = frozenset()
    max_cost_usd: float = 0.5

    @classmethod
    def from_payload(cls, payload: Mapping[str, object] | None, *, max_cost_usd: float = 0.5) -> "PolicySpec":
        policy = payload or {}
        approvals = policy.get("approval_types", [])
        if not isinstance(approvals, list):
            approvals = []
        return cls(approval_types=frozenset(str(item) for item in approvals), max_cost_usd=float(policy.get("max_cost_usd", max_cost_usd)))

    def to_payload(self) -> dict[str, object]:
        return {"approval_types": sorted(self.approval_types), "max_cost_usd": self.max_cost_usd}


@dataclass(frozen=True)
class RunSpec:
    """The typed run request passed from gateway/application into runtime."""

    run_id: str
    task: str
    workspace: Path
    workflow: str = "software-dev"
    default_provider: str = "mock"
    profile: str | None = None
    gate: str | None = None
    role_providers: Mapping[str, str] = field(default_factory=dict)
    skills: tuple[SkillRef, ...] = ()
    automation: AutomationDecision = field(default_factory=AutomationDecision)
    policy: PolicySpec = field(default_factory=PolicySpec)
    ci_block_on_approval: bool = False
    depth: str | None = None
    topology: str | None = None

    @classmethod
    def from_submit_payload(
        cls,
        *,
        task: str,
        workspace: Path,
        provider: str = "mock",
        workflow: str = "software-dev",
        run_id: str | None = None,
        profile: str | None = None,
        gate: str | None = None,
        require_approval: set[str] | None = None,
        max_cost_usd: float = 0.5,
        role_providers: Mapping[str, str] | None = None,
        skills: list[Mapping[str, object]] | None = None,
        ci_block_on_approval: bool = False,
        depth: str | None = None,
        topology: str | None = None,
        automation: Mapping[str, object] | None = None,
    ) -> "RunSpec":
        return cls(
            run_id=run_id or new_run_id(),
            task=task,
            workspace=workspace,
            workflow=workflow,
            default_provider=provider,
            profile=profile,
            gate=gate,
            role_providers={str(key): str(value) for key, value in (role_providers or {}).items() if value},
            skills=tuple(SkillRef.from_payload(skill) for skill in skills or [] if isinstance(skill, Mapping)),
            automation=AutomationDecision.from_payload(automation),
            policy=PolicySpec(approval_types=frozenset(require_approval or set()), max_cost_usd=max_cost_usd),
            ci_block_on_approval=ci_block_on_approval,
            depth=depth,
            topology=topology,
        )

    def task_context(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "gate": self.gate,
            "skills": [skill.to_payload() for skill in self.skills],
            "role_providers": dict(self.role_providers),
            "ci_block_on_approval": self.ci_block_on_approval,
            "depth": self.depth,
            "topology": self.topology,
            "automation": self.automation.to_payload(),
            "safety_policy": self.policy.to_payload(),
        }

    def runtime_kwargs(self) -> dict[str, Any]:
        return {
            "provider": self.default_provider,
            "workflow_name": self.workflow,
            "require_approval": set(self.policy.approval_types),
            "max_cost_usd": self.policy.max_cost_usd,
            "role_providers": dict(self.role_providers),
            "run_id": self.run_id,
            "profile": self.profile,
            "gate": self.gate,
            "skills": [skill.to_payload() for skill in self.skills],
            "ci_block_on_approval": self.ci_block_on_approval,
            "depth": self.depth,
            "topology": self.topology,
            "automation": self.automation.to_payload(),
        }
