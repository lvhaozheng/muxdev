"""P0 automation primitives for intent, flow depth, and model role routing.

The service is intentionally deterministic. It does not execute model calls;
it compiles a user command plus lightweight repository signals into the local
runtime shape that the existing daemon and supervisor can execute.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


KNOWN_DEPTHS = {"auto", "simple", "light", "safe", "deep", "parallel", "ci"}
LIGHTWEIGHT_CONFIDENCE_THRESHOLD = 0.7

TASK_INTAKE_CLASSIFIER_PROMPT = """You are muxdev's task intake router.

Decide whether the user's request is clear enough to execute, whether it is a
lightweight development task, and which workflow should run.

A lightweight dev task is suitable for quick implementation when:
- scope is small or prototype-like;
- few files or modules are likely affected;
- no security, auth, payment, data migration, permissions, or production-risk change is implied;
- success can be checked with a smoke test or targeted check;
- the user likely wants speed over deep architecture.

A standard/complex task is required when:
- architecture, data model, migration, security, permissions, payments, reliability, or cross-module behavior matters;
- requirements are ambiguous enough to affect implementation choices;
- broad tests or independent review are needed;
- failure would be costly or hard to revert.

Return exactly one JSON object:
{
  "clarity": "clear|unclear",
  "reasonableness": "reasonable|needs_user_decision|unsafe",
  "intent": "dev|fix|refactor|test|design|review|docs",
  "complexity": "light|standard|complex",
  "recommended_flow": "dev_light|dev_standard|dev_new|fix|refactor|test|design|review|docs",
  "is_lightweight_dev": true,
  "is_zero_to_one": false,
  "risk_flags": ["..."],
  "questions": [
    {
      "id": "target_platform",
      "question": "...",
      "kind": "single_choice|multi_choice|text",
      "options": [{"label": "...", "value": "..."}]
    }
  ],
  "confidence": 0.0,
  "reason": "short explanation"
}

If any missing information would materially change the implementation, set
clarity to unclear and provide 1-3 concise questions.
"""

INTENT_WORKFLOWS = {
    "design": "design",
    "dev": "dev",
    "fix": "fix",
    "refactor": "refactor",
    "review": "review",
    "test": "test",
    "docs": "docs",
    "ci": "dev",
}

MODEL_ROLES = {"requirements", "plan", "architect", "code", "test", "test_strategy", "review", "secure", "docs", "memory_curator"}
INTENT_ROLE_FALLBACKS = {
    "design": ["requirements", "architect", "review", "docs"],
    "dev": ["requirements", "plan", "code", "test", "review"],
    "fix": ["requirements", "plan", "code", "test", "review"],
    "refactor": ["requirements", "plan", "review", "code", "test"],
    "review": ["review", "docs"],
    "test": ["requirements", "plan", "test_strategy", "test", "review"],
    "docs": ["requirements", "plan", "docs", "review"],
    "ci": ["requirements", "plan", "code", "test", "review"],
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
    workflow: str
    roles: list[str]
    reasons: list[str]
    repo: RepoSignals
    memory_context: list[dict[str, object]] = field(default_factory=list)
    intake: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "intent": self.intent,
            "depth": self.depth,
            "workflow": self.workflow,
            "roles": self.roles,
            "reasons": self.reasons,
            "repo": self.repo.to_dict(),
            "memory_context": self.memory_context,
            "memory_refs": [item.get("id") for item in self.memory_context if item.get("id")],
            "intake": self.intake,
        }


@dataclass(frozen=True)
class TaskIntakeDecision:
    clarity: str = "clear"
    reasonableness: str = "reasonable"
    intent: str = "dev"
    complexity: str = "standard"
    recommended_flow: str = "dev_standard"
    is_lightweight_dev: bool = False
    is_zero_to_one: bool = False
    risk_flags: list[str] = field(default_factory=list)
    questions: list[dict[str, object]] = field(default_factory=list)
    confidence: float = 0.5
    reason: str = "deterministic fallback"

    def to_dict(self) -> dict[str, object]:
        return {
            "clarity": self.clarity,
            "reasonableness": self.reasonableness,
            "intent": self.intent,
            "complexity": self.complexity,
            "recommended_flow": self.recommended_flow,
            "is_lightweight_dev": self.is_lightweight_dev,
            "is_zero_to_one": self.is_zero_to_one,
            "risk_flags": self.risk_flags,
            "questions": self.questions,
            "answer_options": [question.get("options", []) for question in self.questions],
            "confidence": self.confidence,
            "reason": self.reason,
            "classifier_prompt": TASK_INTAKE_CLASSIFIER_PROMPT,
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
    """Compile command intent, flow depth, workflow template, and model roles."""
    automation = config.get("automation", {}) if isinstance(config.get("automation"), dict) else {}
    intent = _resolve_intent(command_workflow, task)
    repo = analyze_repo(workspace, task)
    intake = classify_task_intake(task=task, command_workflow=command_workflow, intent=intent, repo=repo)
    selected_depth = _select_depth(intent, task, repo, requested=depth, configured=str(automation.get("depth", "auto")), intake=intake)
    selected_workflow = _select_workflow(intent, command_workflow, selected_depth, requested=workflow, intake=intake)
    roles = _workflow_roles(workspace, selected_workflow, fallback=_fallback_roles(intent))
    memory_context = _load_memory_context(workspace, task, roles, limit=int(config.get("memory", {}).get("max_items_per_role", 8)) if isinstance(config.get("memory"), dict) else 8)
    reasons = _reasons(intent, selected_depth, selected_workflow, roles, repo, bool(memory_context), requested_depth=depth, legacy_profile=profile)
    return AutomationDecision(
        intent=intent,
        depth=selected_depth,
        workflow=selected_workflow,
        roles=roles,
        reasons=reasons,
        repo=repo,
        memory_context=memory_context,
        intake=intake.to_dict(),
    )


def classify_task_intake(*, task: str, command_workflow: str, intent: str, repo: RepoSignals) -> TaskIntakeDecision:
    """Cheap local fallback for the LLM intake classifier.

    The prompt is shipped in the decision payload so provider-backed routers can
    reuse the same contract. This deterministic implementation keeps submit
    fast and testable when no external model is available.
    """
    normalized = " ".join(task.strip().lower().split())
    if not normalized or normalized in {"?", "??", "clarify", "needs clarification", "unclear", "tbd"}:
        return TaskIntakeDecision(
            clarity="unclear",
            intent=intent,
            complexity="standard",
            recommended_flow=_recommended_flow(intent, "standard", zero_to_one=False),
            questions=[
                {
                    "id": "goal",
                    "question": "What should muxdev build or change, and how should success be checked?",
                    "kind": "text",
                    "options": [],
                }
            ],
            confidence=0.9,
            reason="task text is empty or explicitly unclear",
        )

    risk_flags = [hit.removeprefix("task:") for hit in repo.sensitive_hits]
    zero_to_one = _is_zero_to_one_task(normalized)
    lightweight = not repo.sensitive_hits and (_is_simple_task(normalized) or zero_to_one)
    if command_workflow != "dev":
        lightweight = False
    complexity = "light" if lightweight else ("complex" if repo.sensitive_hits or intent in {"design", "refactor"} else "standard")
    recommended_flow = _recommended_flow(intent, complexity, zero_to_one=zero_to_one)
    return TaskIntakeDecision(
        clarity="clear",
        intent=intent,
        complexity=complexity,
        recommended_flow=recommended_flow,
        is_lightweight_dev=bool(lightweight),
        is_zero_to_one=bool(zero_to_one),
        risk_flags=risk_flags,
        questions=[],
        confidence=0.82 if lightweight else 0.74,
        reason="deterministic fallback from task and repository signals",
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
    if command in {"design", "dev", "fix", "refactor", "review", "test", "docs", "ci"}:
        return command
    lowered = task.lower()
    if any(term in lowered for term in DESIGN_TERMS):
        return "design"
    return "dev"


def _select_depth(intent: str, task: str, repo: RepoSignals, *, requested: str | None, configured: str, intake: TaskIntakeDecision) -> str:
    if requested and requested in KNOWN_DEPTHS and requested != "auto":
        return "simple" if requested == "light" else requested
    if configured in KNOWN_DEPTHS and configured != "auto":
        return "simple" if configured == "light" else configured
    lowered = task.lower()
    if intent == "ci":
        return "ci"
    if repo.sensitive_hits:
        return "deep"
    if intent == "dev" and intake.is_lightweight_dev and intake.confidence >= LIGHTWEIGHT_CONFIDENCE_THRESHOLD:
        return "simple"
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


def _select_workflow(intent: str, command_workflow: str, depth: str, *, requested: str | None, intake: TaskIntakeDecision) -> str:
    if requested:
        return requested
    if command_workflow == "dev" and intake.is_zero_to_one:
        return "dev-new"
    if command_workflow == "dev" and depth == "simple":
        return "dev-lite"
    if intent == "design" and depth == "simple":
        return "design-lite"
    return INTENT_WORKFLOWS.get(intent, INTENT_WORKFLOWS.get(command_workflow, command_workflow))


def _workflow_roles(workspace: Path, workflow_name: str, *, fallback: list[str]) -> list[str]:
    try:
        from ..config.loader import load_config

        workflow = load_config(workspace).get("workflows", {}).get(workflow_name, {})
    except Exception:
        workflow = {}
    stages = workflow.get("stages", []) if isinstance(workflow, dict) else []
    stage_rows = stages if isinstance(stages, list) else []
    roles: list[str] = []
    for stage in stage_rows:
        if not isinstance(stage, dict):
            continue
        if str(stage.get("type") or "agent") != "agent":
            continue
        role = str(stage.get("role") or "").strip()
        if role and role in MODEL_ROLES and role not in roles:
            roles.append(role)
    return roles or fallback


def _fallback_roles(intent: str) -> list[str]:
    return list(INTENT_ROLE_FALLBACKS.get(intent, INTENT_ROLE_FALLBACKS["dev"]))


def _is_simple_task(lowered_task: str) -> bool:
    return any(term in lowered_task for term in SIMPLE_TASK_TERMS)


def _is_zero_to_one_task(lowered_task: str) -> bool:
    terms = {
        "from zero",
        "from scratch",
        "new project",
        "scaffold",
        "bootstrap",
        "0-1",
        "0 to 1",
        "create a project",
        "build a project",
    }
    return any(term in lowered_task for term in terms)


def _recommended_flow(intent: str, complexity: str, *, zero_to_one: bool) -> str:
    if intent == "dev" and zero_to_one:
        return "dev_new"
    if intent == "dev" and complexity == "light":
        return "dev_light"
    if intent == "dev":
        return "dev_standard"
    return intent


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
    workflow: str,
    roles: list[str],
    repo: RepoSignals,
    has_memory: bool,
    *,
    requested_depth: str | None,
    legacy_profile: str | None,
) -> list[str]:
    reasons = [f"intent resolved to {intent} from command/task"]
    if requested_depth and requested_depth != "auto":
        reasons.append(f"depth forced by CLI override: {requested_depth}")
    elif repo.sensitive_hits:
        reasons.append("deep flow selected because sensitive task/path signals were detected")
    else:
        reasons.append(f"{depth} flow selected from intent and repository signals")
    reasons.append(f"workflow template selected as {workflow}")
    reasons.append(f"model roles derived from workflow template: {', '.join(roles) or '-'}")
    if legacy_profile and legacy_profile != "auto":
        reasons.append("legacy profile input was accepted for compatibility but no longer controls runtime topology")
    if repo.test_markers:
        reasons.append("test markers found, so test-capable roles remain available")
    if has_memory:
        reasons.append("active evidence-grounded memory matched the task and was bound to context")
    return reasons
