"""Design pack writer for the first-class ``muxdev design`` flow."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..models import utc_now


USER_DESIGN_DIR = Path("docs") / "design"
USER_DESIGN_FILENAME = "design.md"

DESIGN_PACK_FILES = [
    "00_problem_statement.md",
    "01_requirements.md",
    "02_architecture_options.md",
    "03_decision_record.md",
    "04_system_design.md",
    "05_api_and_data_model.md",
    "06_risk_and_threat_model.md",
    "07_test_strategy.md",
    "08_implementation_roadmap.md",
    "09_open_questions.md",
    "10_final_design_review.md",
]

DESIGN_WORKFLOWS = {"design", "design-lite"}

DESIGN_DOCUMENT_QUALITY_GROUPS = {
    "goal and scope": ("目标", "范围", "problem", "scope", "goal", "requirements", "需求"),
    "users and platform": ("目标用户", "用户", "平台", "audience", "platform", "device", "target user"),
    "play or interaction": ("玩法", "交互", "控制", "core_loop", "core loop", "controls", "interaction"),
    "ui and states": ("界面", "状态", "屏幕", "ui", "state", "screen"),
    "rules and data": ("规则", "数据", "计分", "rules", "data", "scoring", "entities", "api"),
    "acceptance and tests": ("验收", "测试", "验证", "acceptance", "test", "verify"),
    "roadmap": ("实施", "路线", "步骤", "implementation", "roadmap", "sequence"),
    "open questions": ("待确认", "未确认", "open question", "assumption", "假设"),
}

NON_DELIVERY_ACCEPTANCE_TERMS = (
    "no implementation",
    "not implemented",
    "no files",
    "without implementation",
    "read_only",
    "read only",
    "未实现",
    "没有实现",
    "未修改",
    "未运行验证",
    "只产生",
)

DESIGN_STAGE_TITLES = {
    "Problem Statement": "问题陈述",
    "Requirements": "需求与约束",
    "Architecture Options": "架构选项",
    "Decision Record": "方案决策",
    "System Design": "系统设计",
    "Api And Data Model": "API 与数据模型",
    "Risk And Threat Model": "风险与威胁模型",
    "Test Strategy": "测试策略",
    "Implementation Roadmap": "实施路线图",
    "Open Questions": "待确认问题",
    "Final Design Review": "最终设计评审",
    "Design Brief": "设计简报",
    "Software Design": "软件设计",
    "Design": "设计方案",
}

DESIGN_FIELD_TITLES = {
    "name": "方案名称",
    "summary": "摘要",
    "problem_statement": "目标与范围",
    "goal": "目标",
    "goals": "目标",
    "scope": "目标与范围",
    "assumptions": "假设",
    "assumed_defaults": "默认假设",
    "acceptance_criteria": "验收标准",
    "requirements": "需求与约束",
    "constraints": "约束",
    "non_goals": "非目标",
    "user_preferences": "用户偏好",
    "design_preferences": "用户偏好",
    "style_preferences": "用户偏好",
    "audience": "目标用户",
    "users": "目标用户",
    "target_users": "目标用户",
    "platform": "目标平台",
    "target_platform": "目标平台",
    "style": "视觉风格",
    "visual_direction": "视觉方向",
    "proposed_design": "设计方案",
    "architecture": "设计方案",
    "architecture_options": "架构选项",
    "decision_record": "方案决策",
    "system_design": "系统设计",
    "core_loop": "核心流程",
    "controls": "控制方式",
    "desktop": "桌面端",
    "keyboard": "键盘",
    "mobile": "移动端",
    "touch": "触控",
    "input_rules": "输入规则",
    "interactions": "交互",
    "feedback": "反馈",
    "ui": "界面",
    "ui_states": "界面与状态",
    "screens": "页面",
    "states": "状态",
    "rules": "规则",
    "rules_and_data": "规则与数据",
    "scoring": "计分",
    "entities": "实体",
    "data_model": "数据模型",
    "game": "游戏状态",
    "snake": "蛇",
    "snake_segment": "蛇身格",
    "food": "食物",
    "board": "棋盘",
    "score": "分数",
    "modules": "模块",
    "technical_approach": "技术方案",
    "technology": "技术方案",
    "state_model": "状态模型",
    "data_flow": "数据流",
    "api_and_data_model": "API 与数据模型",
    "test_strategy": "测试策略",
    "tests": "测试策略",
    "implementation_sequence": "实施步骤",
    "implementation_roadmap": "实施步骤",
    "roadmap": "实施路线",
    "important_alternatives": "备选方案",
    "risks_and_mitigations": "风险与缓解",
    "risks": "风险与缓解",
    "open_questions": "待确认问题",
    "unconfirmed_items": "未确认项",
    "final_design_review": "最终设计评审",
}

PREFERENCE_KEYS = {"user_preferences", "design_preferences", "style_preferences"}
INTERNAL_USER_DOC_KEYS = {
    "artifacts",
    "blockers",
    "claims",
    "contract_version",
    "created_at",
    "delivery_decision",
    "depth",
    "evidence",
    "feedback_request",
    "has_blockers",
    "intent",
    "memory_proposals",
    "missing_evidence",
    "ordered_steps",
    "provider",
    "roles",
    "run_id",
    "source_evidence",
    "status",
    "verification_approach",
    "workflow",
}
NOISE_PREFIXES = (
    "Reading prompt from stdin",
    "output:",
    "transcript:",
    "chunks:",
    "cli_exited:",
    "Wall time:",
    "Exit code:",
)
NOISE_CONTAINS = (
    "RequestsDependencyWarning",
    "warnings.warn(",
    "codex_core::tools::router",
)


def write_user_design_document(
    *,
    workspace: Path,
    run_id: str,
    task: str,
    workflow: str,
    sections: list[tuple[str, str]],
) -> Path:
    """Write the design deliverable where users expect project files."""
    design_dir = workspace / USER_DESIGN_DIR
    design_dir.mkdir(parents=True, exist_ok=True)
    path = design_dir / USER_DESIGN_FILENAME
    body = _user_design_markdown(run_id=run_id, task=task, workflow=workflow, sections=sections)
    issues = design_document_quality_issues(markdown=body) if workflow in DESIGN_WORKFLOWS else []
    if issues:
        raise ValueError("design document incomplete: " + "; ".join(issues))
    path.write_text(body, encoding="utf-8")
    if not path.exists() or path.stat().st_size == 0:
        raise OSError(f"design document missing after write: {path}")
    return path


def write_design_pack(
    *,
    run_dir: Path,
    run_id: str,
    task: str,
    workflow: str,
    automation: dict[str, Any] | None = None,
    sections: list[tuple[str, str]] | None = None,
) -> dict[str, object]:
    """Create the design pack files and return an artifact manifest."""
    design_dir = run_dir / "design"
    design_dir.mkdir(parents=True, exist_ok=True)
    automation = automation or {}
    section_bodies = _design_pack_markdown_sections(
        run_id=run_id,
        task=task,
        workflow=workflow,
        sections=sections or [],
        automation=automation,
    )
    for filename in DESIGN_PACK_FILES:
        path = design_dir / filename
        path.write_text(section_bodies.get(filename) or _markdown_for(filename, task, run_id, automation), encoding="utf-8")

    contract = {
        "contract_version": "muxdev.design_contract.v1",
        "run_id": run_id,
        "workflow": workflow,
        "task": task,
        "created_at": utc_now(),
        "intent": automation.get("intent", "design"),
        "depth": automation.get("depth", "deep"),
        "roles": automation.get("roles", []),
        "artifacts": DESIGN_PACK_FILES,
        "memory_proposals": "memory_proposals.json",
    }
    contract_path = design_dir / "design_contract.json"
    if not contract_path.exists():
        contract_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    proposals = [
        {
            "kind": "architecture_decision",
            "claim": f"Design run {run_id} produced an implementation contract for: {task}",
            "source_evidence": str(contract_path),
            "status": "proposed",
            "confidence": 0.7,
        }
    ]
    proposals_path = design_dir / "memory_proposals.json"
    if not proposals_path.exists():
        proposals_path.write_text(json.dumps(proposals, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return {
        "design_dir": str(design_dir),
        "contract": str(contract_path),
        "memory_proposals": str(proposals_path),
        "files": [str(design_dir / filename) for filename in DESIGN_PACK_FILES],
    }


def latest_design_contract(workspace: Path) -> Path | None:
    """Find the newest local design contract under the workspace runtime root."""
    candidates = sorted((workspace / ".muxdev" / "runs").glob("*/design/design_contract.json"))
    return candidates[-1] if candidates else None


def design_document_quality_issues(
    *,
    markdown: str | None = None,
    sections: list[tuple[str, str]] | None = None,
) -> list[str]:
    """Return deterministic completeness issues for the user-facing design doc."""
    if markdown is None:
        if not sections:
            return ["design document has no source content"]
        try:
            markdown = _user_design_markdown(
                run_id="quality_check",
                task="quality check",
                workflow="design-lite",
                sections=sections,
            )
        except Exception as exc:
            return [f"design document could not be rendered: {exc}"]
    return _design_markdown_quality_issues(markdown)


def _design_markdown_quality_issues(markdown: str) -> list[str]:
    text = _clean_stage_content(str(markdown)).strip()
    lowered = text.lower()
    compact = re.sub(r"\s+", "", text)
    headings = [line for line in text.splitlines() if line.lstrip().startswith("#")]
    issues: list[str] = []

    if len(compact) < 800:
        issues.append("design document is too short for a complete single-file handoff")
    if len(headings) < 8:
        issues.append("design document has too few sections")

    missing_groups = [
        group
        for group, tokens in DESIGN_DOCUMENT_QUALITY_GROUPS.items()
        if not any(token.lower() in lowered for token in tokens)
    ]
    if missing_groups:
        issues.append("design document missing coverage for: " + ", ".join(missing_groups))

    if _acceptance_section_mentions_non_delivery(text):
        issues.append("acceptance criteria cannot be satisfied by stating that no implementation or verification exists")
    return issues


def _acceptance_section_mentions_non_delivery(text: str) -> bool:
    in_acceptance = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        if line.startswith("#"):
            in_acceptance = any(token in lowered for token in ("acceptance", "验收"))
            continue
        if in_acceptance and any(term in lowered for term in NON_DELIVERY_ACCEPTANCE_TERMS):
            return True
    return False


def _markdown_for(filename: str, task: str, run_id: str, automation: dict[str, Any]) -> str:
    title = filename[3:-3].replace("_", " ").title()
    return "\n".join(
        [
            f"# {title}",
            "",
            f"- 任务: {task}",
            "",
            "本节尚未在设计输出中明确提供；请在实现前复核并补充。",
            "",
        ]
    )


def _user_design_markdown(*, run_id: str, task: str, workflow: str, sections: list[tuple[str, str]]) -> str:
    structured = _extract_structured_design_payload(sections, purpose="user_doc")
    if structured:
        return _structured_design_markdown(run_id=run_id, task=task, workflow=workflow, payload=structured)
    return _fallback_design_markdown(run_id=run_id, task=task, workflow=workflow, sections=sections)


def _fallback_design_markdown(*, run_id: str, task: str, workflow: str, sections: list[tuple[str, str]]) -> str:
    lines = [
        "# 设计文档",
        "",
    ]
    if workflow in DESIGN_WORKFLOWS:
        lines.extend(
            [
                "## 任务概览",
                "",
                f"- 任务: {task}",
                "- 说明: 以下内容来自设计阶段的最终输出，已过滤会话日志和调试事件。",
                "",
                "## 未确认项",
                "",
                "- 当前设计未明确记录目标用户、视觉风格或参考产品偏好；后续实现前需要确认。",
                "",
            ]
        )
    for title, content in sections:
        clean_title = _localized_title(str(title).strip() or "Design")
        clean_content = _clean_user_visible_content(str(content)).strip()
        if not clean_content:
            continue
        lines.extend([f"## {clean_title}", "", clean_content, ""])
    if len(lines) <= 6:
        raise ValueError("design document has no section content")
    return "\n".join(lines).rstrip() + "\n"


def _structured_design_markdown(*, run_id: str, task: str, workflow: str, payload: dict[str, Any]) -> str:
    if isinstance(payload.get("design_pack"), dict):
        return _structured_design_pack_markdown(run_id=run_id, task=task, workflow=workflow, payload=payload)

    if isinstance(payload.get("design_doc"), dict):
        design_doc = payload.get("design_doc")
    elif isinstance(payload.get("design_document"), dict):
        design_doc = payload.get("design_document")
    else:
        design_doc = payload
    if not isinstance(design_doc, dict):
        raise ValueError("structured design payload is not a mapping")

    lines = [
        "# 设计文档",
        "",
        "## 任务概览",
        "",
    ]
    summary = _scalar_text(payload.get("summary")) or _scalar_text(design_doc.get("summary")) or _scalar_text(design_doc.get("problem_statement"))
    lines.append(f"- 摘要: {summary}" if summary else f"- 任务: {task}")
    lines.append("")

    preference_value = _first_present(design_doc, PREFERENCE_KEYS)
    lines.extend(["## 用户偏好", ""])
    if preference_value is None:
        lines.append("- 当前设计未明确记录目标用户、视觉风格或参考产品偏好；后续实现前需要确认。")
    else:
        _append_value(lines, preference_value)
    lines.append("")

    emitted: set[str] = set()
    grouped_fields = [
        ("目标与范围", ["problem_statement", "scope", "requirements", "assumptions"]),
        ("用户与平台", ["audience", "platform", "target_users", "target_platform", "style"]),
        ("玩法与交互", ["core_loop", "controls", "interactions", "feedback"]),
        ("界面与状态", ["ui", "screens", "state_model"]),
        ("规则与数据", ["rules", "scoring", "entities", "data_model", "api_and_data_model", "data_flow"]),
        ("验收标准", ["acceptance_criteria"]),
        ("测试策略", ["test_strategy", "tests"]),
        ("设计方案", ["proposed_design", "architecture"]),
        ("实施步骤", ["implementation_sequence", "implementation_roadmap"]),
        ("备选方案", ["important_alternatives"]),
        ("风险与缓解", ["risks_and_mitigations"]),
    ]
    for title, keys in grouped_fields:
        values = [(key, design_doc.get(key)) for key in keys if _has_content(design_doc.get(key))]
        if not values:
            continue
        lines.extend([f"## {title}", ""])
        for key, value in values:
            emitted.add(key)
            if len(values) > 1 and not _is_scalar(value):
                lines.extend([f"### {_field_title(key)}", ""])
            elif len(values) > 1 and _is_scalar(value):
                lines.append(f"- {_field_title(key)}: {_scalar_text(value)}")
                continue
            _append_value(lines, value)
            lines.append("")

    risk_values = []
    if _has_content(payload.get("risks")):
        risk_values.append(payload.get("risks"))
    if _has_content(payload.get("risks_and_mitigations")):
        risk_values.append(payload.get("risks_and_mitigations"))
    if risk_values:
        lines.extend(["## 风险与缓解", ""])
        for value in risk_values:
            _append_value(lines, value)
        lines.append("")

    open_items: list[Any] = []
    for key in ("open_questions", "unconfirmed_items", "questions"):
        if _has_content(design_doc.get(key)):
            emitted.add(key)
            open_items.append(design_doc.get(key))
    if preference_value is None:
        open_items.append(["目标用户、视觉风格、参考产品和平台优先级尚未在最终输出中明确确认。"])
    if open_items:
        lines.extend(["## 待确认问题 / 未确认项", ""])
        for value in open_items:
            _append_value(lines, value)
        lines.append("")

    for key, value in design_doc.items():
        if key in emitted or key in PREFERENCE_KEYS or key in {"summary", "tests"} or _is_internal_user_key(key):
            continue
        if not _has_content(value):
            continue
        lines.extend([f"## {_field_title(key)}", ""])
        _append_value(lines, value)
        lines.append("")

    body = "\n".join(lines).rstrip() + "\n"
    if len(body.strip().splitlines()) <= 6:
        raise ValueError("design document has no section content")
    return body


def _extract_structured_design_payload(
    sections: list[tuple[str, str]],
    *,
    purpose: str = "user_doc",
) -> dict[str, Any] | None:
    candidates: list[tuple[int, int, int, dict[str, Any]]] = []
    order = 0
    for _title, content in sections:
        for payload in _payloads_from_text(str(content)):
            for kind, normalized in _structured_payload_candidates(payload):
                priority = _payload_priority(kind, purpose=purpose)
                if priority <= 0:
                    continue
                candidates.append((priority, _payload_content_score(normalized), order, normalized))
            order += 1
    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        return candidates[0][3]
    return None


def _structured_payload_candidates(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    candidates: list[tuple[str, dict[str, Any]]] = []
    summary = payload.get("summary")
    for key in ("design_doc", "design_document"):
        value = payload.get(key)
        if isinstance(value, dict):
            normalized: dict[str, Any] = {"design_doc": value}
            if _has_content(summary):
                normalized["summary"] = summary
            for optional_key in ("risks", "risks_and_mitigations"):
                if _has_content(payload.get(optional_key)):
                    normalized[optional_key] = payload[optional_key]
            candidates.append(("explicit_design_doc", normalized))
    if isinstance(payload.get("design_pack"), dict):
        candidates.append(("explicit_design_pack", payload))
    if _looks_like_design_doc(payload):
        candidates.append(("loose_design_doc", {"design_doc": payload}))
    if _looks_like_design_pack(payload):
        candidates.append(("loose_design_pack", {"design_pack": payload}))
    return candidates


def _payload_priority(kind: str, *, purpose: str) -> int:
    if purpose == "design_pack":
        return {
            "explicit_design_pack": 40,
            "explicit_design_doc": 30,
            "loose_design_pack": 20,
            "loose_design_doc": 10,
        }.get(kind, 0)
    return {
        "explicit_design_doc": 40,
        "explicit_design_pack": 30,
        "loose_design_doc": 20,
        "loose_design_pack": 10,
    }.get(kind, 0)


def _payload_content_score(payload: dict[str, Any]) -> int:
    body = payload.get("design_doc") if isinstance(payload.get("design_doc"), dict) else payload.get("design_pack")
    if not isinstance(body, dict):
        body = payload
    score_keys = {
        "problem_statement",
        "summary",
        "scope",
        "requirements",
        "goals",
        "constraints",
        "non_goals",
        "audience",
        "platform",
        "target_users",
        "target_platform",
        "core_loop",
        "controls",
        "interactions",
        "ui",
        "screens",
        "states",
        "state_model",
        "rules",
        "scoring",
        "entities",
        "data_model",
        "api_and_data_model",
        "acceptance_criteria",
        "test_strategy",
        "tests",
        "implementation_sequence",
        "implementation_roadmap",
        "roadmap",
        "open_questions",
        "unconfirmed_items",
        "risks",
        "risks_and_mitigations",
    }
    score = sum(10 for key in score_keys if _has_content(body.get(key)))
    try:
        score += min(len(json.dumps(body, ensure_ascii=False)), 5000) // 250
    except (TypeError, ValueError):
        score += 0
    return score


def _payloads_from_text(text: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for obj in _json_objects_in_text(text):
        payloads.extend(_payloads_from_object(obj))
    return payloads


def _payloads_from_object(obj: Any) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    if isinstance(obj, dict):
        payloads.append(obj)
        item = obj.get("item")
        if isinstance(item, dict):
            for key in ("text", "content", "message"):
                if isinstance(item.get(key), str):
                    payloads.extend(_payloads_from_text(str(item[key])))
        for key in ("text", "content", "message", "output"):
            if isinstance(obj.get(key), str):
                payloads.extend(_payloads_from_text(str(obj[key])))
    return payloads


def _json_objects_in_text(text: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    decoder = json.JSONDecoder()
    candidates = [match.group(1) for match in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)]
    candidates.append(text)
    for candidate in candidates:
        index = 0
        while index < len(candidate):
            start = candidate.find("{", index)
            if start == -1:
                break
            try:
                parsed, end = decoder.raw_decode(candidate[start:])
            except json.JSONDecodeError:
                index = start + 1
                continue
            if isinstance(parsed, dict):
                objects.append(parsed)
            index = start + max(end, 1)
    return objects


def _looks_like_design_doc(payload: dict[str, Any]) -> bool:
    design_keys = {
        "problem_statement",
        "acceptance_criteria",
        "proposed_design",
        "state_model",
        "data_flow",
        "implementation_sequence",
        "risks_and_mitigations",
        "user_preferences",
        "audience",
        "platform",
        "core_loop",
        "controls",
        "ui",
        "rules",
        "test_strategy",
        "tests",
        "constraints",
        "non_goals",
        "open_questions",
        "implementation_roadmap",
        "roadmap",
    }
    return bool(design_keys.intersection(payload))


def _looks_like_design_pack(payload: dict[str, Any]) -> bool:
    design_pack_keys = {
        "platform",
        "audience",
        "style",
        "core_loop",
        "controls",
        "ui",
        "rules",
        "states",
        "acceptance_criteria",
    }
    return bool(design_pack_keys.intersection(payload))


def _structured_design_pack_markdown(*, run_id: str, task: str, workflow: str, payload: dict[str, Any]) -> str:
    pack = payload.get("design_pack") if isinstance(payload.get("design_pack"), dict) else payload
    if not isinstance(pack, dict):
        raise ValueError("structured design pack payload is not a mapping")
    lines = [
        "# 设计文档",
        "",
        "## 任务概览",
        "",
    ]
    summary = _scalar_text(payload.get("summary")) or _scalar_text(pack.get("summary")) or _scalar_text(pack.get("name"))
    lines.append(f"- 摘要: {summary}" if summary else f"- 任务: {task}")
    for key, label in (("name", "方案名称"), ("platform", "目标平台"), ("audience", "目标用户"), ("style", "视觉风格")):
        if _has_content(pack.get(key)):
            lines.append(f"- {label}: {_scalar_text(pack.get(key))}")
    lines.append("")

    groups = [
        ("目标与范围", ["goals", "scope", "requirements", "constraints", "non_goals"]),
        ("玩法与交互", ["core_loop", "controls", "interactions", "feedback"]),
        ("界面与状态", ["ui", "states", "screens", "state_model"]),
        ("规则与数据", ["rules", "scoring", "entities", "data_model", "api_and_data_model"]),
        ("验收标准", ["acceptance_criteria", "tests", "test_strategy"]),
        ("实施路线", ["implementation_sequence", "implementation_roadmap", "roadmap"]),
        ("风险与缓解", ["risks", "risks_and_mitigations"]),
        ("待确认问题 / 未确认项", ["open_questions", "unconfirmed_items"]),
    ]
    emitted = {"summary", "name", "platform", "audience", "style"}
    for title, keys in groups:
        values = [(key, pack.get(key)) for key in keys if _has_content(pack.get(key))]
        if not values:
            continue
        lines.extend([f"## {title}", ""])
        for key, value in values:
            emitted.add(key)
            if len(values) > 1:
                lines.extend([f"### {_field_title(key)}", ""])
            _append_value(lines, value)
            lines.append("")

    for key, value in pack.items():
        if key in emitted or _is_internal_user_key(key) or not _has_content(value):
            continue
        lines.extend([f"## {_field_title(key)}", ""])
        _append_value(lines, value)
        lines.append("")

    body = "\n".join(lines).rstrip() + "\n"
    if len(body.strip().splitlines()) <= 6:
        raise ValueError("design pack document has no section content")
    return body


def _design_pack_markdown_sections(
    *,
    run_id: str,
    task: str,
    workflow: str,
    sections: list[tuple[str, str]],
    automation: dict[str, Any],
) -> dict[str, str]:
    payload = _extract_structured_design_payload(sections, purpose="design_pack")
    pack: dict[str, Any] = {}
    if payload:
        if isinstance(payload.get("design_pack"), dict):
            pack = dict(payload["design_pack"])
        elif isinstance(payload.get("design_doc"), dict):
            pack = dict(payload["design_doc"])
    fallback = _combined_stage_text(sections)
    common = [f"- 任务: {task}", ""]

    mapping = {
        "00_problem_statement.md": ("问题陈述", ["problem_statement", "summary", "name", "goals", "scope"]),
        "01_requirements.md": ("需求与约束", ["requirements", "constraints", "audience", "platform", "style", "acceptance_criteria"]),
        "02_architecture_options.md": ("架构选项", ["architecture_options", "important_alternatives", "platform", "ui", "controls"]),
        "03_decision_record.md": ("方案决策", ["decision_record", "proposed_design", "core_loop", "rules"]),
        "04_system_design.md": ("系统设计", ["system_design", "architecture", "states", "state_model", "entities"]),
        "05_api_and_data_model.md": ("API 与数据模型", ["api_and_data_model", "data_model", "data_flow", "scoring"]),
        "06_risk_and_threat_model.md": ("风险与威胁模型", ["risks", "risks_and_mitigations"]),
        "07_test_strategy.md": ("测试策略", ["test_strategy", "tests", "acceptance_criteria"]),
        "08_implementation_roadmap.md": ("实施路线图", ["implementation_sequence", "implementation_roadmap", "roadmap"]),
        "09_open_questions.md": ("待确认问题", ["open_questions", "unconfirmed_items"]),
        "10_final_design_review.md": ("最终设计评审", ["final_design_review", "acceptance_criteria"]),
    }
    rendered: dict[str, str] = {}
    for filename, (title, keys) in mapping.items():
        lines = [f"# {title}", "", *common]
        used = False
        for key in keys:
            if not _has_content(pack.get(key)):
                continue
            used = True
            lines.extend([f"## {_field_title(key)}", ""])
            _append_value(lines, pack.get(key))
            lines.append("")
        if not used and fallback and not pack:
            lines.extend(["## 设计输出摘录", "", fallback, ""])
        elif not used:
            lines.extend(["## 待补充", "", "- 本节尚未在设计输出中明确给出，请在实现前复核。", ""])
        rendered[filename] = "\n".join(lines).rstrip() + "\n"
    return rendered


def _combined_stage_text(sections: list[tuple[str, str]]) -> str:
    chunks: list[str] = []
    for title, content in sections:
        clean = _clean_user_visible_content(str(content)).strip()
        if not clean:
            continue
        clean_title = _localized_title(str(title).strip() or "Design")
        chunks.append(f"### {clean_title}\n\n{clean}")
    text = "\n\n".join(chunks).strip()
    if len(text) > 4000:
        return text[:4000].rstrip() + "\n\n..."
    return text


def _clean_user_visible_content(content: str) -> str:
    clean = _clean_stage_content(content)
    visible: list[str] = []
    skip_section = False
    internal_terms = (
        "claims",
        "evidence",
        "missing evidence",
        "missing_evidence",
        "delivery_decision",
        "verification_approach",
        "run:",
        "workflow:",
        "roles:",
        "provider",
        "muxdev",
    )
    for line in clean.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if stripped.startswith("#"):
            skip_section = any(term in lowered for term in internal_terms)
            if skip_section:
                continue
        elif skip_section:
            continue
        if any(lowered.startswith(prefix) for prefix in ("- run:", "- workflow:", "- roles:", "run:", "workflow:", "roles:")):
            continue
        if re.match(r'[-*]?\s*"?(?:claims|evidence|missing_evidence|delivery_decision|verification_approach)"?\s*[:：]', lowered):
            continue
        visible.append(line)
    return "\n".join(visible).strip()


def _clean_stage_content(content: str) -> str:
    text = re.split(r"\n# Stream Events\n|\n# Session Archives\n", content, maxsplit=1)[0]
    cleaned: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            cleaned.append("")
            continue
        if _is_noise_line(stripped):
            continue
        cleaned.append(line)
    clean = "\n".join(cleaned).strip()
    clean = re.sub(r"\A#\s+[^\n]+\n+", "", clean, count=1).strip()
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    return clean


def _is_noise_line(line: str) -> bool:
    if line.startswith(NOISE_PREFIXES) or any(token in line for token in NOISE_CONTAINS):
        return True
    candidate = line[7:].strip() if line.startswith("output:") else line
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, dict) and "type" in parsed and (
        "item" in parsed or str(parsed.get("type") or "").startswith(("thread.", "turn.", "error", "item."))
    )


def _localized_title(title: str) -> str:
    return DESIGN_STAGE_TITLES.get(title, DESIGN_STAGE_TITLES.get(title.replace("_", " ").title(), title))


def _field_title(key: str) -> str:
    return DESIGN_FIELD_TITLES.get(key, key.replace("_", " ").title())


def _is_internal_user_key(key: str) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return normalized in INTERNAL_USER_DOC_KEYS


def _first_present(data: dict[str, Any], keys: set[str]) -> Any:
    for key in keys:
        if _has_content(data.get(key)):
            return data[key]
    return None


def _append_value(lines: list[str], value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if _is_internal_user_key(str(key)):
                continue
            if not _has_content(item):
                continue
            title = _field_title(str(key))
            if _is_scalar(item):
                lines.append(f"- {title}: {_scalar_text(item)}")
            else:
                lines.extend([f"### {title}", ""])
                _append_value(lines, item)
                lines.append("")
        return
    if isinstance(value, list):
        for item in value:
            if not _has_content(item):
                continue
            if _is_scalar(item):
                lines.append(f"- {_scalar_text(item)}")
            elif isinstance(item, dict):
                summary = _dict_inline_summary(item)
                if summary:
                    lines.append(f"- {summary}")
                else:
                    _append_value(lines, item)
            else:
                lines.append(f"- {item}")
        return
    if _has_content(value):
        lines.append(str(value).strip())


def _dict_inline_summary(value: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, item in value.items():
        if _is_internal_user_key(str(key)):
            continue
        if _is_scalar(item) and _has_content(item):
            parts.append(f"{_field_title(str(key))}: {_scalar_text(item)}")
    return "; ".join(parts)


def _has_content(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _scalar_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value).strip()
