"""P0 automation primitives for intent, flow depth, and role topology.

The service is intentionally deterministic. It does not execute model calls;
it compiles a user command plus lightweight repository signals into the local
runtime shape that the existing daemon and supervisor can execute.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


KNOWN_DEPTHS = {"auto", "simple", "safe", "deep", "parallel", "ci"}
KNOWN_PROFILES = {"auto", "solo", "pair", "squad", "ci"}

INTENT_WORKFLOWS = {
    "design": "design",
    "dev": "dev",
    "fix": "fix",
    "refactor": "dev",
    "review": "review",
    "test": "test",
    "ci": "dev",
}

TOPOLOGY_ROLES = {
    "solo": ["code"],
    "pair": ["code", "review"],
    "squad": ["plan", "code", "test", "review"],
    "parallel-squad": ["plan", "code", "test", "review"],
    "ci": ["plan", "code", "test", "review"],
}

DESIGN_ROLES = ["requirements", "architect", "test_strategy", "review", "docs", "memory_curator"]
DESIGN_TOPOLOGY_ROLES = {
    "solo": ["architect"],
    "pair": ["requirements", "architect"],
    "squad": DESIGN_ROLES,
}

SENSITIVE_TERMS = {
    "auth",
    "oauth",
    "login",
    "payment",
    "billing",
    "permission",
    "secret",
    "token",
    "migration",
    "migrations",
    "security",
    "secure",
    "认证",
    "登录",
    "支付",
    "账单",
    "权限",
    "密钥",
    "迁移",
    "安全",
}

DESIGN_TERMS = {
    "design",
    "architecture",
    "architect",
    "schema",
    "proposal",
    "roadmap",
    "设计",
    "架构",
    "方案",
    "规划",
}

SMALL_FIX_TERMS = {"typo", "lint", "small", "minor", "bug", "fix", "小修复", "修复", "报错"}
SIMPLE_TASK_TERMS = {
    "simple",
    "small",
    "tiny",
    "minimal",
    "basic",
    "demo",
    "prototype",
    "toy",
    "snake",
    "tetris",
    "todo",
    "counter",
    "landing page",
    "简单",
    "小游戏",
    "贪吃蛇",
    "原型",
    "演示",
}


@dataclass(frozen=True)
class RepoSignals:
    project_markers: list[str] = field(default_factory=list)
    test_markers: list[str] = field(default_factory=list)
    sensitive_hits: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "project_markers": self.project_markers,
            "test_markers": self.test_markers,
            "sensitive_hits": self.sensitive_hits,
        }


@dataclass(frozen=True)
class AutomationDecision:
    intent: str
    depth: str
    topology: str
    profile: str
    workflow: str
    roles: list[str]
    reasons: list[str]
    repo: RepoSignals
    memory_context: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "intent": self.intent,
            "depth": self.depth,
            "topology": self.topology,
            "profile": self.profile,
            "workflow": self.workflow,
            "roles": self.roles,
            "reasons": self.reasons,
            "repo": self.repo.to_dict(),
            "memory_context": self.memory_context,
            "memory_refs": [item.get("id") for item in self.memory_context if item.get("id")],
        }


def resolve_automation(
    *,
    workspace: Path,
    command_workflow: str,
    task: str,
    config: dict[str, Any],
    profile: str | None = None,
    workflow: str | None = None,
    depth: str | None = None,
) -> AutomationDecision:
    """Compile command intent, flow depth, and role topology for one task."""
    automation = config.get("automation", {}) if isinstance(config.get("automation"), dict) else {}
    intent = _resolve_intent(command_workflow, task)
    repo = analyze_repo(workspace, task)
    selected_depth = _select_depth(intent, task, repo, requested=depth, configured=str(automation.get("depth", "auto")))
    selected_profile = _select_profile(
        intent,
        selected_depth,
        requested=profile,
        configured=str(automation.get("profile", "auto")),
    )
    topology = _compile_topology(intent, selected_depth, selected_profile)
    roles = DESIGN_TOPOLOGY_ROLES.get(topology, DESIGN_ROLES) if intent == "design" else TOPOLOGY_ROLES[topology]
    selected_workflow = _select_workflow(intent, command_workflow, selected_depth, requested=workflow)
    memory_context = _load_memory_context(workspace, task, roles, limit=int(config.get("memory", {}).get("max_items_per_role", 8)) if isinstance(config.get("memory"), dict) else 8)
    reasons = _reasons(intent, selected_depth, topology, repo, bool(memory_context), requested_depth=depth, requested_profile=profile)
    return AutomationDecision(
        intent=intent,
        depth=selected_depth,
        topology=topology,
        profile=selected_profile,
        workflow=selected_workflow,
        roles=roles,
        reasons=reasons,
        repo=repo,
        memory_context=memory_context,
    )


def analyze_repo(workspace: Path, task: str = "") -> RepoSignals:
    """Collect cheap repository signals used by the auto selector."""
    markers: list[str] = []
    for name in ("pyproject.toml", "package.json", "pnpm-lock.yaml", "package-lock.json", "go.mod", "Cargo.toml"):
        if (workspace / name).exists():
            markers.append(name)

    tests: list[str] = []
    for name in ("tests", "test", "__tests__", "pytest.ini", "tox.ini"):
        if (workspace / name).exists():
            tests.append(name)

    sensitive: list[str] = []
    lowered = task.lower()
    for term in sorted(SENSITIVE_TERMS):
        if term in lowered:
            sensitive.append(f"task:{term}")
    for pattern in ("auth", "payment", "billing", "migrations"):
        if (workspace / pattern).exists():
            sensitive.append(f"path:{pattern}")

    return RepoSignals(project_markers=markers, test_markers=tests, sensitive_hits=sensitive)


def render_why(decision: dict[str, object]) -> str:
    """Render an automation decision for human CLI output."""
    automation = decision.get("automation", decision)
    if not isinstance(automation, dict):
        return "No automation decision recorded."
    lines = [
        f"intent: {automation.get('intent', '-')}",
        f"depth: {automation.get('depth', '-')}",
        f"topology: {automation.get('topology', '-')}",
        f"profile: {automation.get('profile', '-')}",
        f"workflow: {automation.get('workflow', '-')}",
        f"roles: {', '.join(str(item) for item in automation.get('roles', []) or []) or '-'}",
    ]
    memory_refs = automation.get("memory_refs", [])
    if memory_refs:
        lines.append(f"memory_refs: {', '.join(str(item) for item in memory_refs)}")
    reasons = automation.get("reasons", [])
    if reasons:
        lines.append("")
        lines.append("reasons:")
        lines.extend(f"- {reason}" for reason in reasons)
    repo = automation.get("repo", {})
    if isinstance(repo, dict):
        lines.append("")
        lines.append("repo signals:")
        for key in ("project_markers", "test_markers", "sensitive_hits"):
            values = repo.get(key, [])
            lines.append(f"- {key}: {', '.join(str(item) for item in values) or '-'}")
    return "\n".join(lines)


def _resolve_intent(command_workflow: str, task: str) -> str:
    command = command_workflow.strip().lower().replace("-", "_")
    if command in {"design", "dev", "fix", "refactor", "review", "test", "ci"}:
        return command
    lowered = task.lower()
    if any(term in lowered for term in DESIGN_TERMS):
        return "design"
    return "dev"


def _select_depth(intent: str, task: str, repo: RepoSignals, *, requested: str | None, configured: str) -> str:
    if requested and requested in KNOWN_DEPTHS and requested != "auto":
        return requested
    if configured in KNOWN_DEPTHS and configured != "auto":
        return configured
    lowered = task.lower()
    if intent == "ci":
        return "ci"
    if repo.sensitive_hits:
        return "deep"
    if _is_simple_task(lowered):
        return "simple"
    if intent in {"design", "refactor"}:
        return "deep"
    if "parallel" in lowered or "并行" in lowered:
        return "parallel"
    if intent in {"review", "test"}:
        return "safe"
    if intent == "fix" and any(term in lowered for term in SMALL_FIX_TERMS):
        return "simple"
    return "safe"


def _select_profile(intent: str, depth: str, *, requested: str | None, configured: str) -> str:
    if requested and requested in KNOWN_PROFILES and requested != "auto":
        return requested
    if configured in KNOWN_PROFILES and configured != "auto":
        return configured
    if depth == "ci":
        return "ci"
    if depth == "simple":
        return "solo"
    if intent in {"review", "test", "fix"} and depth == "safe":
        return "pair"
    return "squad"


def _compile_topology(intent: str, depth: str, profile: str) -> str:
    if intent == "design":
        if depth == "simple":
            return "solo"
        if depth == "safe":
            return "pair"
        return "squad"
    if depth == "parallel":
        return "parallel-squad"
    if profile == "ci" or depth == "ci":
        return "ci"
    return profile if profile in {"solo", "pair", "squad"} else "squad"


def _select_workflow(intent: str, command_workflow: str, depth: str, *, requested: str | None) -> str:
    if requested:
        return requested
    if intent == "design" and depth == "simple":
        return "design-lite"
    if command_workflow == "dev" and depth == "simple":
        return "dev"
    return INTENT_WORKFLOWS.get(intent, INTENT_WORKFLOWS.get(command_workflow, command_workflow))


def _is_simple_task(lowered_task: str) -> bool:
    return any(term in lowered_task for term in SIMPLE_TASK_TERMS)


def _load_memory_context(workspace: Path, task: str, roles: list[str], *, limit: int) -> list[dict[str, object]]:
    try:
        from ..storage.memory import MemoryStore

        with MemoryStore(workspace) as store:
            return store.query(task, roles=roles, status="active", limit=limit)
    except Exception:
        return []


def _reasons(
    intent: str,
    depth: str,
    topology: str,
    repo: RepoSignals,
    has_memory: bool,
    *,
    requested_depth: str | None,
    requested_profile: str | None,
) -> list[str]:
    reasons = [f"intent resolved to {intent} from command/task"]
    if requested_depth and requested_depth != "auto":
        reasons.append(f"depth forced by CLI override: {requested_depth}")
    elif repo.sensitive_hits:
        reasons.append("deep flow selected because sensitive task/path signals were detected")
    else:
        reasons.append(f"{depth} flow selected from intent and repository signals")
    if requested_profile and requested_profile != "auto":
        reasons.append(f"profile forced by CLI override: {requested_profile}")
    else:
        reasons.append(f"role topology compiled as {topology}")
    if repo.test_markers:
        reasons.append("test markers found, so test-capable roles remain available")
    if has_memory:
        reasons.append("active evidence-grounded memory matched the task and was bound to context")
    return reasons
