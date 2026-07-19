"""Run contracts shared by entrypoints, daemon services, and runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import subprocess
from typing import Any, Mapping

from .ids import new_run_id


@dataclass(frozen=True)
class SkillRef:
    """A normalized skill reference bound to a run or workflow stage."""

    name: str
    role: str | None = None
    stage: str | None = None
    path: str | None = None
    injection: str = "prompt"
    reason: str | None = None
    content: str | None = None

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "SkillRef":
        return cls(
            name=str(payload.get("name") or payload.get("id") or "skill"),
            role=str(payload["role"]) if payload.get("role") else None,
            stage=str(payload["stage"]) if payload.get("stage") else None,
            path=str(payload.get("path") or payload.get("skill_file") or "") or None,
            injection=str(payload.get("injection") or "prompt"),
            reason=str(payload["reason"]) if payload.get("reason") else None,
            content=str(payload["content"]) if payload.get("content") else None,
        )

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {"name": self.name, "injection": self.injection}
        if self.role:
            payload["role"] = self.role
        if self.stage:
            payload["stage"] = self.stage
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
    strict_approval: bool = False

    @classmethod
    def from_payload(cls, payload: Mapping[str, object] | None, *, max_cost_usd: float = 0.5) -> "PolicySpec":
        policy = payload or {}
        approvals = policy.get("approval_types", [])
        if not isinstance(approvals, list):
            approvals = []
        return cls(
            approval_types=frozenset(str(item) for item in approvals),
            max_cost_usd=float(policy.get("max_cost_usd", max_cost_usd)),
            strict_approval=bool(policy.get("strict_approval")),
        )

    def to_payload(self) -> dict[str, object]:
        return {"approval_types": sorted(self.approval_types), "max_cost_usd": self.max_cost_usd, "strict_approval": self.strict_approval}


HIGH_RISK_TAGS = frozenset({"auth", "payment", "permission", "secret", "migration", "security"})


@dataclass(frozen=True)
class HarnessPolicySpec:
    """Durable Adapter certification and isolation requirements."""

    risk_level: str = "normal"
    risk_tags: frozenset[str] = frozenset()
    required_capabilities: frozenset[str] = frozenset({"structured_events", "provider_sandbox"})
    isolation_requirement: str = "provider_sandbox"
    minimum_certification: str = "offline_verified"

    @classmethod
    def derive(
        cls,
        *,
        task: str,
        gate: str | None,
        automation: Mapping[str, object] | None,
    ) -> "HarnessPolicySpec":
        tags = _risk_tags(task, automation)
        high_risk = str(gate or "").lower() == "strict" or bool(tags & HIGH_RISK_TAGS)
        if high_risk:
            return cls(
                risk_level="high",
                risk_tags=frozenset(tags),
                required_capabilities=frozenset({"structured_events", "provider_sandbox", "cooperative_cancel"}),
                isolation_requirement="strong",
                minimum_certification="live_verified",
            )
        return cls(risk_tags=frozenset(tags))

    @classmethod
    def from_payload(cls, payload: Mapping[str, object] | None) -> "HarnessPolicySpec":
        value = payload or {}
        risk_level = str(value.get("risk_level") or "normal")
        required = value.get("required_capabilities", ["structured_events", "provider_sandbox"])
        tags = value.get("risk_tags", [])
        return cls(
            risk_level=risk_level if risk_level in {"normal", "high"} else "high",
            risk_tags=frozenset(str(item) for item in tags if str(item)) if isinstance(tags, list) else frozenset(),
            required_capabilities=frozenset(str(item) for item in required if str(item)) if isinstance(required, list) else frozenset({"structured_events", "provider_sandbox"}),
            isolation_requirement=str(value.get("isolation_requirement") or ("strong" if risk_level == "high" else "provider_sandbox")),
            minimum_certification=str(value.get("minimum_certification") or ("live_verified" if risk_level == "high" else "offline_verified")),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "risk_level": self.risk_level,
            "risk_tags": sorted(self.risk_tags),
            "required_capabilities": sorted(self.required_capabilities),
            "isolation_requirement": self.isolation_requirement,
            "minimum_certification": self.minimum_certification,
        }


@dataclass(frozen=True)
class RoutingPolicySpec:
    """Durable task-level Provider routing policy."""

    mode: str = "fixed"
    delivery_mode: str = "simulation"
    fixed_provider: str | None = "mock"
    allowed_providers: frozenset[str] = frozenset()
    reviewer_policy: str = "auto"
    minimum_samples: int = 5
    benchmark_snapshot_id: str | None = None
    max_cost_usd: float = 0.5

    @classmethod
    def derive(
        cls,
        *,
        provider: str,
        max_cost_usd: float,
        payload: Mapping[str, object] | None = None,
    ) -> "RoutingPolicySpec":
        if payload:
            return cls.from_payload(payload, fallback_provider=provider, max_cost_usd=max_cost_usd)
        automatic = provider == "auto"
        fixed = None if automatic else provider
        delivery_mode = "simulation" if provider in {"mock", "replay"} else "production"
        return cls(
            mode="auto" if automatic else "fixed",
            delivery_mode=delivery_mode,
            fixed_provider=fixed,
            max_cost_usd=max_cost_usd,
        )

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, object] | None,
        *,
        fallback_provider: str = "mock",
        max_cost_usd: float = 0.5,
    ) -> "RoutingPolicySpec":
        value = payload or {}
        mode = str(value.get("mode") or "fixed")
        if mode not in {"auto", "fixed", "replay"}:
            mode = "fixed"
        delivery_mode = str(value.get("delivery_mode") or ("simulation" if fallback_provider in {"mock", "replay"} else "production"))
        if delivery_mode not in {"production", "simulation"}:
            delivery_mode = "production"
        reviewer_policy = str(value.get("reviewer_policy") or "auto")
        if reviewer_policy not in {"auto", "required", "disabled"}:
            reviewer_policy = "auto"
        allowed = value.get("allowed_providers", [])
        fixed_value = value.get("fixed_provider")
        fixed_provider = str(fixed_value) if fixed_value else (None if mode == "auto" else fallback_provider)
        return cls(
            mode=mode,
            delivery_mode=delivery_mode,
            fixed_provider=fixed_provider,
            allowed_providers=frozenset(str(item) for item in allowed if str(item)) if isinstance(allowed, list) else frozenset(),
            reviewer_policy=reviewer_policy,
            minimum_samples=max(1, int(value.get("minimum_samples") or 5)),
            benchmark_snapshot_id=str(value["benchmark_snapshot_id"]) if value.get("benchmark_snapshot_id") else None,
            max_cost_usd=max(0.0, float(value.get("max_cost_usd", max_cost_usd))),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "delivery_mode": self.delivery_mode,
            "fixed_provider": self.fixed_provider,
            "allowed_providers": sorted(self.allowed_providers),
            "reviewer_policy": self.reviewer_policy,
            "minimum_samples": self.minimum_samples,
            "benchmark_snapshot_id": self.benchmark_snapshot_id,
            "max_cost_usd": self.max_cost_usd,
        }


@dataclass(frozen=True)
class RepositoryBaseline:
    """Privacy-minimal repository state captured when a Run is submitted."""

    vcs: str = "none"
    initial_commit: str | None = None
    dirty_patch_hash: str | None = None
    untracked_manifest_hash: str | None = None
    captured_at: str | None = None
    history_complete: bool = False

    @classmethod
    def capture(cls, workspace: Path) -> "RepositoryBaseline":
        from ..models import utc_now

        root = Path(workspace).expanduser().resolve()
        captured_at = utc_now()
        if not (root / ".git").exists():
            return cls(captured_at=captured_at, history_complete=True)
        commit = _git_output(root, "rev-parse", "HEAD")
        patch = _git_bytes(root, "diff", "--binary", "HEAD")
        untracked = _git_output(root, "ls-files", "--others", "--exclude-standard")
        manifest: list[str] = []
        for relative in sorted(line.strip() for line in untracked.splitlines() if line.strip()):
            path = (root / relative).resolve()
            try:
                if not path.is_relative_to(root) or not path.is_file() or path.is_symlink():
                    continue
                manifest.append(f"{relative.replace('\\\\', '/')}:{_file_digest(path)}")
            except OSError:
                continue
        return cls(
            vcs="git",
            initial_commit=commit or None,
            dirty_patch_hash=_sha256(patch),
            untracked_manifest_hash=_sha256("\n".join(manifest).encode("utf-8")),
            captured_at=captured_at,
            history_complete=bool(commit),
        )

    @classmethod
    def from_payload(cls, payload: Mapping[str, object] | None, *, legacy: bool = False) -> "RepositoryBaseline":
        value = payload or {}
        return cls(
            vcs=str(value.get("vcs") or "none"),
            initial_commit=str(value["initial_commit"]) if value.get("initial_commit") else None,
            dirty_patch_hash=str(value["dirty_patch_hash"]) if value.get("dirty_patch_hash") else None,
            untracked_manifest_hash=str(value["untracked_manifest_hash"]) if value.get("untracked_manifest_hash") else None,
            captured_at=str(value["captured_at"]) if value.get("captured_at") else None,
            history_complete=False if legacy else bool(value.get("history_complete")),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "vcs": self.vcs,
            "initial_commit": self.initial_commit,
            "dirty_patch_hash": self.dirty_patch_hash,
            "untracked_manifest_hash": self.untracked_manifest_hash,
            "captured_at": self.captured_at,
            "history_complete": self.history_complete,
        }


@dataclass(frozen=True)
class RunSpec:
    """The typed run request passed from gateway/application into runtime."""

    run_id: str
    task: str
    workspace: Path
    workflow: str = "software-dev"
    default_provider: str = "mock"
    gate: str | None = None
    role_providers: Mapping[str, str] = field(default_factory=dict)
    skills: tuple[SkillRef, ...] = ()
    automation: AutomationDecision = field(default_factory=AutomationDecision)
    policy: PolicySpec = field(default_factory=PolicySpec)
    harness_policy: HarnessPolicySpec = field(default_factory=HarnessPolicySpec)
    routing_policy: RoutingPolicySpec = field(default_factory=RoutingPolicySpec)
    repository_baseline: RepositoryBaseline = field(default_factory=RepositoryBaseline)
    ci_block_on_approval: bool = False
    depth: str | None = None

    @classmethod
    def from_submit_payload(
        cls,
        *,
        task: str,
        workspace: Path,
        provider: str = "mock",
        workflow: str = "software-dev",
        run_id: str | None = None,
        gate: str | None = None,
        require_approval: set[str] | None = None,
        max_cost_usd: float = 0.5,
        role_providers: Mapping[str, str] | None = None,
        skills: list[Mapping[str, object]] | None = None,
        ci_block_on_approval: bool = False,
        depth: str | None = None,
        automation: Mapping[str, object] | None = None,
        routing_policy: Mapping[str, object] | None = None,
    ) -> "RunSpec":
        return cls(
            run_id=run_id or new_run_id(),
            task=task,
            workspace=workspace,
            workflow=workflow,
            default_provider=provider,
            gate=gate,
            role_providers={str(key): str(value) for key, value in (role_providers or {}).items() if value},
            skills=tuple(SkillRef.from_payload(skill) for skill in skills or [] if isinstance(skill, Mapping)),
            automation=AutomationDecision.from_payload(automation),
            policy=PolicySpec(
                approval_types=frozenset(require_approval or set()),
                max_cost_usd=max_cost_usd,
                strict_approval=bool(require_approval) and gate not in {"auto", "safe"},
            ),
            harness_policy=HarnessPolicySpec.derive(task=task, gate=gate, automation=automation),
            routing_policy=RoutingPolicySpec.derive(
                provider=provider,
                max_cost_usd=max_cost_usd,
                payload=routing_policy,
            ),
            repository_baseline=RepositoryBaseline.capture(workspace),
            ci_block_on_approval=ci_block_on_approval,
            depth=depth,
        )

    def task_context(self) -> dict[str, object]:
        return {
            "gate": self.gate,
            "skills": [skill.to_payload() for skill in self.skills],
            "role_providers": dict(self.role_providers),
            "ci_block_on_approval": self.ci_block_on_approval,
            "depth": self.depth,
            "automation": self.automation.to_payload(),
            "safety_policy": self.policy.to_payload(),
            "harness_policy": self.harness_policy.to_payload(),
            "routing_policy": self.routing_policy.to_payload(),
        }

    def to_payload(self) -> dict[str, object]:
        """Return the versioned, credential-free durable execution request."""
        return {
            "schema_version": 4,
            "run_id": self.run_id,
            "task": self.task,
            "workspace": str(self.workspace.expanduser().resolve()),
            "workflow": self.workflow,
            "default_provider": self.default_provider,
            "gate": self.gate,
            "role_providers": dict(self.role_providers),
            "skills": [skill.to_payload() for skill in self.skills],
            "automation": self.automation.to_payload(),
            "policy": self.policy.to_payload(),
            "harness_policy": self.harness_policy.to_payload(),
            "routing_policy": self.routing_policy.to_payload(),
            "repository_baseline": self.repository_baseline.to_payload(),
            "ci_block_on_approval": self.ci_block_on_approval,
            "depth": self.depth,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "RunSpec":
        """Rebuild a persisted RunSpec without reading process environment."""
        version = int(payload.get("schema_version") or 0)
        if version not in {1, 2, 3, 4}:
            raise ValueError(f"unsupported RunSpec schema: {version}")
        skills = payload.get("skills", [])
        roles = payload.get("role_providers", {})
        return cls(
            run_id=str(payload.get("run_id") or ""),
            task=str(payload.get("task") or ""),
            workspace=Path(str(payload.get("workspace") or "")).expanduser().resolve(),
            workflow=str(payload.get("workflow") or "software-dev"),
            default_provider=str(payload.get("default_provider") or "mock"),
            gate=str(payload["gate"]) if payload.get("gate") else None,
            role_providers={str(key): str(value) for key, value in (roles.items() if isinstance(roles, Mapping) else []) if value},
            skills=tuple(SkillRef.from_payload(item) for item in skills if isinstance(item, Mapping)) if isinstance(skills, list) else (),
            automation=AutomationDecision.from_payload(payload.get("automation") if isinstance(payload.get("automation"), Mapping) else {}),
            policy=PolicySpec.from_payload(payload.get("policy") if isinstance(payload.get("policy"), Mapping) else {}),
            harness_policy=HarnessPolicySpec.from_payload(
                payload.get("harness_policy") if version >= 2 and isinstance(payload.get("harness_policy"), Mapping) else {}
            ),
            routing_policy=RoutingPolicySpec.from_payload(
                payload.get("routing_policy") if version >= 3 and isinstance(payload.get("routing_policy"), Mapping) else {},
                fallback_provider=str(payload.get("default_provider") or "mock"),
                max_cost_usd=float((payload.get("policy") or {}).get("max_cost_usd", 0.5)) if isinstance(payload.get("policy"), Mapping) else 0.5,
            ),
            repository_baseline=RepositoryBaseline.from_payload(
                payload.get("repository_baseline") if version >= 4 and isinstance(payload.get("repository_baseline"), Mapping) else {},
                legacy=version < 4,
            ),
            ci_block_on_approval=bool(payload.get("ci_block_on_approval")),
            depth=str(payload["depth"]) if payload.get("depth") else None,
        )

    def runtime_kwargs(self) -> dict[str, Any]:
        return {
            "provider": self.default_provider,
            "workflow_name": self.workflow,
            "require_approval": set(self.policy.approval_types),
            "max_cost_usd": self.policy.max_cost_usd,
            "role_providers": dict(self.role_providers),
            "run_id": self.run_id,
            "gate": self.gate,
            "skills": [skill.to_payload() for skill in self.skills],
            "ci_block_on_approval": self.ci_block_on_approval,
            "depth": self.depth,
            "automation": self.automation.to_payload(),
            "harness_policy": self.harness_policy.to_payload(),
            "routing_policy": self.routing_policy.to_payload(),
        }


def _git_bytes(root: Path, *args: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return b""
    return completed.stdout if completed.returncode == 0 else b""


def _git_output(root: Path, *args: str) -> str:
    return _git_bytes(root, *args).decode("utf-8", errors="replace").strip()


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _risk_tags(task: str, automation: Mapping[str, object] | None) -> set[str]:
    tags: set[str] = set()
    payload = automation or {}
    intake = payload.get("intake")
    if isinstance(intake, Mapping):
        flags = intake.get("risk_flags")
        if isinstance(flags, list):
            tags.update(str(item).strip().lower() for item in flags if str(item).strip())
        elif isinstance(flags, Mapping):
            tags.update(str(key).strip().lower() for key, enabled in flags.items() if enabled)
    direct = payload.get("risk_tags")
    if isinstance(direct, list):
        tags.update(str(item).strip().lower() for item in direct if str(item).strip())
    lowered = task.lower()
    aliases = {
        "auth": ("auth", "authentication", "登录", "认证"),
        "payment": ("payment", "billing", "支付", "计费"),
        "permission": ("permission", "authorization", "权限", "授权"),
        "secret": ("secret", "credential", "token", "密钥", "凭证"),
        "migration": ("migration", "migrate", "迁移"),
        "security": ("security", "vulnerability", "安全", "漏洞"),
    }
    for tag, terms in aliases.items():
        if any(term in lowered for term in terms):
            tags.add(tag)
    return tags
