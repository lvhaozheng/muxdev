"""Live dashboard workbench renderer."""

from __future__ import annotations

import html
import json


_DASHBOARD_LANGS = {"zh-CN", "en"}


def normalize_dashboard_lang(lang: str | None) -> str:
    """Return the supported dashboard language, defaulting to Chinese."""
    normalized = (lang or "").strip()
    if normalized in _DASHBOARD_LANGS:
        return normalized
    if normalized.lower() in {"zh", "zh-cn", "cn", "chinese"}:
        return "zh-CN"
    if normalized.lower() in {"en", "en-us", "english"}:
        return "en"
    return "zh-CN"


def render_live_dashboard_html(task_id: str | None = None, lang: str | None = None) -> str:
    """Render the daemon-backed live dashboard."""
    selected_lang = normalize_dashboard_lang(lang)
    text = _I18N[selected_lang]
    body_attr = f'data-task-id="{html.escape(task_id, quote=True)}"' if task_id else ""
    return (
        _LIVE_DASHBOARD_WORKBENCH_TEMPLATE
        .replace("__HTML_LANG__", selected_lang)
        .replace("__BODY_ATTR__", body_attr)
        .replace("__DASHBOARD_LANG__", selected_lang)
        .replace("__TEXT__", json.dumps(text, ensure_ascii=False, separators=(",", ":")))
        .replace("__TITLE__", html.escape(text["title"], quote=False))
        .replace("__APP_NAME__", html.escape(text["app_name"], quote=False))
        .replace("__SUBTITLE__", html.escape(text["subtitle"], quote=False))
        .replace("__LANGUAGE_TOGGLE__", html.escape(text["language_toggle"], quote=False))
        .replace("__DAEMON_CONNECTING__", html.escape(text["daemon_connecting"], quote=False))
        .replace("__PROJECT_TITLE__", html.escape(text["project_select_title"], quote=True))
        .replace("__NEW_TASK_PLACEHOLDER__", html.escape(text["new_task_placeholder"], quote=True))
        .replace("__PROFILE_AUTO__", html.escape(text["profile_auto"], quote=False))
        .replace("__PROFILE_SQUAD__", html.escape(text["profile_squad"], quote=False))
        .replace("__PROFILE_SOLO__", html.escape(text["profile_solo"], quote=False))
        .replace("__PROFILE_CI__", html.escape(text["profile_ci"], quote=False))
        .replace("__GATE_AUTO__", html.escape(text["gate_auto"], quote=False))
        .replace("__GATE_SAFE__", html.escape(text["gate_safe"], quote=False))
        .replace("__GATE_STRICT__", html.escape(text["gate_strict"], quote=False))
        .replace("__GATE_CI__", html.escape(text["gate_ci"], quote=False))
        .replace("__BUDGET_TITLE__", html.escape(text["budget_title"], quote=True))
        .replace("__COMMAND_PALETTE__", html.escape(text["command_palette"], quote=False))
        .replace("__CREATE_TASK__", html.escape(text["create_task"], quote=False))
    )


_I18N: dict[str, dict[str, object]] = {
    "zh-CN": {
        "title": "muxdev 控制台",
        "app_name": "muxdev 控制台",
        "subtitle": "本地 AI 软件交付工作台：项目、运行、验证、证据和系统治理",
        "daemon_connecting": "连接中",
        "language_toggle": "切换英文",
        "new_task_placeholder": "描述你希望 muxdev 完成的任务",
        "project_select_title": "项目",
        "profile_auto": "档案：自动",
        "profile_squad": "团队",
        "profile_solo": "单人",
        "profile_ci": "CI",
        "gate_auto": "门禁：自动",
        "gate_safe": "安全",
        "gate_strict": "严格",
        "gate_ci": "CI",
        "budget_title": "预算上限",
        "command_palette": "命令面板",
        "create_task": "新建任务",
        "hero_headline": "正在读取交付现场",
        "hero_subtitle": "第一屏处理待办，第二屏确认可信度，随后进入项目、运行、验证和系统治理。",
        "product_experience": "产品体验",
        "activity_overview": "总览",
        "activity_projects": "项目",
        "activity_runs": "运行",
        "activity_validation": "验证",
        "activity_artifacts": "产物",
        "activity_system": "系统",
        "explorer_title": "资源管理器",
        "explorer_projects": "项目树",
        "explorer_workflows": "工作流",
        "explorer_filters": "筛选",
        "explorer_selected": "当前运行",
        "no_visible_projects": "没有可见项目。",
        "no_project_selected": "未选择项目。",
        "no_task_selected": "未选择任务。",
        "hide_project": "隐藏",
        "hide_task": "隐藏",
        "overview_title": "总览",
        "action_center": "行动中心",
        "action_center_note": "优先处理提供方操作、muxdev 审批、失败恢复和规划反馈。",
        "current_status": "当前状态",
        "active_runs": "运行看板",
        "delivery_confidence": "交付可信度",
        "health_strip": "健康条",
        "learning_governance": "学习与治理",
        "runs_title": "运行列表",
        "runs_note": "按项目、工作流、状态和风险定位每个运行。",
        "project_overview": "项目总览",
        "project_tasks": "任务看板",
        "project_config": "项目配置",
        "validation_title": "验证实验",
        "validation_note": "对比单智能体与多智能体运行的证据、可靠性、成本和可观测性。",
        "artifacts_title": "证据与产物",
        "artifacts_note": "集中查看报告、差异、测试、审查、回滚点和语义合并结果。",
        "system_title": "系统治理",
        "system_note": "提供方、预算、Git 安全、MCP、技能、记忆和工作流模板统一在这里治理。",
        "bottom_problems": "问题",
        "bottom_output": "输出",
        "bottom_evidence": "证据",
        "bottom_actions": "操作",
        "attention_strip": "决策条",
        "decision_attention": "待处理",
        "decision_delivery": "交付可信度",
        "decision_validation": "验证就绪度",
        "decision_system": "系统健康",
        "attention_strip_note": "需要人处理的事项会优先显示在这里。",
        "latest_output": "最新输出",
        "executable_actions": "可执行操作",
        "inspector_title": "详情检查器",
        "inspector_empty": "选择一个项目、运行、证据或系统条目查看详情。",
        "selected_detail": "选中详情",
        "view_details": "查看详情",
        "compact_workspace": "支撑信号",
        "focus_workspace": "交付焦点",
        "focus_task": "焦点任务",
        "no_focus_task": "暂无焦点任务",
        "next_action": "下一步",
        "priority_queue": "优先队列",
        "run_stream": "运行流",
        "recent_evidence": "最近证据",
        "standard_gaps": "未达标项",
        "trusted_ready": "可信可交付",
        "needs_attention": "需要处理",
        "select_to_inspect": "选择后在右侧查看详情",
        "inspector_tab_overview": "概览",
        "inspector_tab_gaps": "未达标",
        "inspector_tab_evidence": "证据",
        "inspector_tab_output": "输出",
        "inspector_tab_actions": "操作",
        "system_catalog": "系统目录",
        "project_details": "项目详情",
        "run_details": "运行详情",
        "standards": "标准",
        "trusted_delivery_standards": "可信交付标准",
        "validation_standards": "验证标准",
        "governance_standards": "治理标准",
        "configuration_standards": "配置标准",
        "standards_met": "达标",
        "current_value": "当前值",
        "target_value": "目标值",
        "evidence": "证据",
        "recommended_action": "建议操作",
        "problems_empty": "暂无待处理问题。",
        "output_empty": "暂无事件输出。",
        "evidence_empty": "暂无证据。",
        "actions_empty": "暂无可执行操作。",
        "task_empty": "任务描述为空",
        "submitted": "已提交",
        "tasks": "任务",
        "running": "运行中",
        "waiting": "等待中",
        "blocked": "阻塞",
        "projects": "项目",
        "completed": "完成",
        "attention": "待处理",
        "stage": "阶段",
        "workflow": "工作流",
        "cost": "成本",
        "tokens": "令牌",
        "approvals": "审批",
        "actions": "操作",
        "provider": "提供方",
        "tools": "工具",
        "resources": "资源",
        "prompts": "提示词",
        "branch": "分支",
        "risk": "风险",
        "tests": "测试",
        "review": "审查",
        "rollback": "回滚",
        "missing": "缺少",
        "none": "暂无",
        "ready": "就绪",
        "available": "可用",
        "copy": "复制",
        "copy_review_links": "复制审查链接",
        "hide_completed_low_risk": "隐藏已完成低风险",
        "low_risk_actions": "低风险批量操作",
        "low_risk_count": "当前筛选中的低风险任务",
        "reset": "重置",
        "all": "全部",
        "report": "报告",
        "diff": "差异",
        "share_review": "分享审查",
        "recover": "恢复",
        "approve": "批准",
        "deny": "拒绝",
        "submit_response": "提交响应",
        "response_placeholder": "请输入响应",
        "copy_attach": "复制接管命令",
        "handled_continue": "已处理并继续",
        "confirm_hide_project": "从控制台隐藏这个项目？不会删除工作区、运行记录、证据或文件。",
        "confirm_hide_task": "隐藏这个任务提醒？不会删除运行记录、证据或文件。",
        "confirm_hide_completed": "隐藏已完成低风险任务？证据和运行记录仍保留在磁盘。",
        "copied": "已复制",
        "no_completed_low_risk": "没有可隐藏的已完成低风险任务",
        "not_run": "未运行验证",
        "command_search": "搜索命令、项目、运行和审查链接",
        "command_run": "执行",
        "command_empty": "没有匹配命令。",
        "open_overview": "打开总览",
        "open_projects": "打开项目",
        "open_runs": "打开运行列表",
        "open_validation": "打开验证",
        "open_artifacts": "打开产物",
        "open_system": "打开系统治理",
        "copy_provider_doctor": "复制提供方诊断命令",
        "dashboard_home": "控制台首页",
        "projects_hint": "项目、工作流和任务看板",
        "runs_hint": "跨项目运行列表",
        "validation_hint": "实验与对比报告",
        "artifacts_hint": "报告、差异和证据",
        "system_hint": "提供方、策略、技能和记忆",
        "provider_doctor_hint": "muxdev provider doctor",
        "project_prefix": "项目",
        "action_prefix": "行动",
        "run_prefix": "运行",
        "project_health": "项目健康",
        "active_tasks": "活跃任务",
        "run_profile": "运行档",
        "shared_state": "共享状态",
        "memory_board": "共享状态 / 记忆板",
        "context_ready": "项目上下文已建立",
        "context_missing": "尚未建立项目上下文",
        "no_facts": "暂无运行事实。",
        "workflow_board": "工作流看板",
        "role_lanes": "角色泳道",
        "filters": "筛选",
        "provider_learning_trend": "提供方学习趋势",
        "parallel_conflict_map": "并行冲突图",
        "multi_repo_map": "多仓编排图",
        "workflow_template_preview": "工作流模板预览",
        "providers": "提供方",
        "policy_budget_git": "策略 / 预算 / Git 安全",
        "role_sessions": "角色会话",
        "workflow_templates": "工作流模板",
        "skills": "技能",
        "memory_context": "记忆上下文",
        "advanced_control": "高级控制",
        "standard_labels": {
            "confidence": "可信度阈值",
            "tests": "测试结果",
            "review": "审查阻塞",
            "rollback": "回滚能力",
            "artifacts": "报告 / 差异 / 证据",
            "budget": "预算",
            "human_attention": "人工待处理",
            "experiment_exists": "验证实验",
            "baseline_coverage": "Baseline 覆盖",
            "score": "综合评分",
            "test_pass_rate": "测试通过率",
            "evidence_confidence": "证据可信度",
            "safety": "安全性",
            "rollback_efficiency": "回滚 / 成本效率",
            "provider_ready": "Provider 可用性",
            "git_safety": "Git 安全",
            "budget_guardrail": "预算门禁",
            "mcp_guardrails": "MCP 防护",
            "skills_lock": "技能锁定",
            "memory_conflicts": "记忆冲突",
            "profile_gate": "Profile / Gate",
            "workflow_template": "工作流模板",
            "role_template": "角色提供方",
            "project_context": "项目上下文",
            "project_budget": "项目预算",
        },
        "standard_actions": {
            "none": "无需操作",
            "publish_evidence": "补齐报告、差异和证据",
            "resolve_risk": "处理高风险项",
            "add_tests": "补充或重跑测试",
            "complete_review": "完成审查并清理阻塞",
            "enable_rollback": "生成可回滚快照",
            "review_budget": "检查预算和成本",
            "resolve_attention": "处理审批或 provider 操作",
            "run_validation": "运行验证套件",
            "review_validation": "查看验证报告",
            "fix_provider": "修复 provider 配置",
            "fix_git": "检查 Git 安全状态",
            "setup_mcp": "检查 MCP 防护",
            "resolve_memory": "处理记忆冲突",
            "configure_project": "完善项目配置",
        },
        "status": {
            "completed": "已完成",
            "running": "运行中",
            "awaiting_approval": "待审批",
            "awaiting_provider_action": "待处理",
            "paused_budget": "预算暂停",
            "blocked": "已阻塞",
            "aborted": "已终止",
            "failed": "失败",
            "created": "已创建",
            "queued": "排队中",
            "pending": "等待中",
            "reviewable": "可审查",
            "trusted": "可信",
            "collecting": "收集中",
            "risky": "有风险",
            "watch": "关注",
            "ready": "就绪",
            "ok": "达标",
            "degraded": "需补充",
            "not_git": "非 Git 项目",
            "dirty": "有未提交变更",
            "clean": "干净",
        },
        "action_labels": {
            "provider_action": "提供方等待操作",
            "clarification": "需要补充需求",
            "approval": "需要审批",
            "recovery": "任务需要恢复",
            "plan_feedback": "计划可反馈",
        },
        "columns": {
            "experiment_id": "实验",
            "task_id": "任务",
            "suite": "套件",
            "winner": "胜出策略",
            "strategies": "策略",
            "report": "报告",
            "report_endpoint": "报告入口",
            "diff_endpoint": "差异入口",
            "cost_usd": "成本",
            "updated_at": "更新时间",
            "name": "名称",
            "kind": "类型",
            "stage_id": "阶段",
            "path": "路径",
            "created_at": "创建时间",
            "passed": "通过",
            "command": "命令",
            "summary": "摘要",
            "type": "类型",
            "severity": "级别",
            "file": "文件",
            "line": "行",
            "suggestion": "建议",
            "run_id": "运行",
            "label": "标签",
            "confidence": "可信度",
            "current": "当前值",
            "target": "目标值",
            "evidence": "证据",
            "action": "建议操作",
            "review_id": "审查",
            "decision": "决策",
            "patch_hash": "补丁哈希",
            "findings": "发现",
            "role": "角色",
            "value": "值",
            "source": "来源",
            "trust": "信任",
            "workflow": "工作流",
            "roles": "角色",
            "providers": "提供方",
            "status": "状态",
            "risk_level": "风险等级",
            "description": "描述",
            "phases": "阶段",
            "supported_providers": "支持提供方",
            "tool": "工具",
            "reason": "原因",
        },
    },
    "en": {
        "title": "muxdev Dashboard",
        "app_name": "muxdev Dashboard",
        "subtitle": "Local AI software delivery workbench: projects, runs, validation, evidence, and system governance",
        "daemon_connecting": "Connecting",
        "language_toggle": "中文",
        "new_task_placeholder": "Describe what you want muxdev to do",
        "project_select_title": "Project",
        "profile_auto": "profile:auto",
        "profile_squad": "squad",
        "profile_solo": "solo",
        "profile_ci": "ci",
        "gate_auto": "gate:auto",
        "gate_safe": "safe",
        "gate_strict": "strict",
        "gate_ci": "ci",
        "budget_title": "Budget cap",
        "command_palette": "Command Palette",
        "create_task": "Create Task",
        "hero_headline": "Reading the delivery workspace",
        "hero_subtitle": "Handle attention items first, verify confidence second, then move into projects, runs, validation, and governance.",
        "product_experience": "Product Experience",
        "activity_overview": "Overview",
        "activity_projects": "Projects",
        "activity_runs": "Runs",
        "activity_validation": "Validation",
        "activity_artifacts": "Artifacts",
        "activity_system": "System",
        "explorer_title": "Explorer",
        "explorer_projects": "Project Tree",
        "explorer_workflows": "Workflows",
        "explorer_filters": "Filters",
        "explorer_selected": "Selected Run",
        "no_visible_projects": "No visible projects.",
        "no_project_selected": "No project selected.",
        "no_task_selected": "No run selected.",
        "hide_project": "Hide",
        "hide_task": "Hide",
        "overview_title": "Overview",
        "action_center": "Action Center",
        "action_center_note": "Prioritize provider prompts, muxdev approvals, recovery, and planning feedback.",
        "current_status": "Current Status",
        "active_runs": "Run Board",
        "delivery_confidence": "Delivery Confidence",
        "health_strip": "Health Strip",
        "learning_governance": "Learning & Governance",
        "runs_title": "Run List",
        "runs_note": "Locate every run by project, workflow, status, and risk.",
        "project_overview": "Project Overview",
        "project_tasks": "Task Board",
        "project_config": "Project Config",
        "validation_title": "Validation Experiments",
        "validation_note": "Compare single-agent and multi-agent runs across evidence, reliability, cost, and observability.",
        "artifacts_title": "Evidence & Artifacts",
        "artifacts_note": "Review reports, diffs, tests, review blockers, rollback points, and semantic merge results.",
        "system_title": "System Governance",
        "system_note": "Govern providers, budget, Git safety, MCP, skills, memory, and workflow templates in one place.",
        "bottom_problems": "Problems",
        "bottom_output": "Output",
        "bottom_evidence": "Evidence",
        "bottom_actions": "Actions",
        "attention_strip": "Decision Rail",
        "decision_attention": "Attention",
        "decision_delivery": "Delivery Trust",
        "decision_validation": "Validation Readiness",
        "decision_system": "System Health",
        "attention_strip_note": "Human-in-the-loop items are surfaced here first.",
        "latest_output": "Latest Output",
        "executable_actions": "Executable Actions",
        "inspector_title": "Detail Inspector",
        "inspector_empty": "Select a project, run, evidence item, or system entry to inspect details.",
        "selected_detail": "Selected Detail",
        "view_details": "View Details",
        "compact_workspace": "Support Signals",
        "focus_workspace": "Delivery Focus",
        "focus_task": "Focus Run",
        "no_focus_task": "No focus run",
        "next_action": "Next Action",
        "priority_queue": "Priority Queue",
        "run_stream": "Run Stream",
        "recent_evidence": "Recent Evidence",
        "standard_gaps": "Standard Gaps",
        "trusted_ready": "Trusted to Ship",
        "needs_attention": "Needs Attention",
        "select_to_inspect": "Select to inspect on the right",
        "inspector_tab_overview": "Overview",
        "inspector_tab_gaps": "Gaps",
        "inspector_tab_evidence": "Evidence",
        "inspector_tab_output": "Output",
        "inspector_tab_actions": "Actions",
        "system_catalog": "System Catalog",
        "project_details": "Project Details",
        "run_details": "Run Details",
        "standards": "Standards",
        "trusted_delivery_standards": "Trusted Delivery Standards",
        "validation_standards": "Validation Standards",
        "governance_standards": "Governance Standards",
        "configuration_standards": "Configuration Standards",
        "standards_met": "met",
        "current_value": "Current",
        "target_value": "Target",
        "evidence": "Evidence",
        "recommended_action": "Recommended Action",
        "problems_empty": "No problems need attention.",
        "output_empty": "No event output yet.",
        "evidence_empty": "No evidence yet.",
        "actions_empty": "No actions available.",
        "task_empty": "Task description is empty",
        "submitted": "Submitted",
        "tasks": "Tasks",
        "running": "Running",
        "waiting": "Waiting",
        "blocked": "Blocked",
        "projects": "Projects",
        "completed": "Completed",
        "attention": "Attention",
        "stage": "Stage",
        "workflow": "Workflow",
        "cost": "Cost",
        "tokens": "Tokens",
        "approvals": "Approvals",
        "actions": "Actions",
        "provider": "Provider",
        "tools": "Tools",
        "resources": "Resources",
        "prompts": "Prompts",
        "branch": "Branch",
        "risk": "Risk",
        "tests": "Tests",
        "review": "Review",
        "rollback": "Rollback",
        "missing": "Missing",
        "none": "None",
        "ready": "Ready",
        "available": "Available",
        "copy": "Copy",
        "copy_review_links": "Copy Review Links",
        "hide_completed_low_risk": "Hide Completed Low Risk",
        "low_risk_actions": "Batch Low-Risk Actions",
        "low_risk_count": "low-risk tasks in current filter",
        "reset": "Reset",
        "all": "All",
        "report": "Report",
        "diff": "Diff",
        "share_review": "Share Review",
        "recover": "Recover",
        "approve": "Approve",
        "deny": "Deny",
        "submit_response": "Submit Response",
        "response_placeholder": "Enter a response",
        "copy_attach": "Copy Attach Command",
        "handled_continue": "Handled and Continue",
        "confirm_hide_project": "Hide this project from the dashboard? Workspace, runs, evidence, and files stay on disk.",
        "confirm_hide_task": "Hide this run reminder? Run records, evidence, and files stay on disk.",
        "confirm_hide_completed": "Hide completed low-risk tasks? Evidence and run records stay on disk.",
        "copied": "Copied",
        "no_completed_low_risk": "No completed low-risk task to hide",
        "not_run": "Not run",
        "command_search": "Search commands, projects, runs, and review links",
        "command_run": "Run",
        "command_empty": "No command found.",
        "open_overview": "Open Overview",
        "open_projects": "Open Projects",
        "open_runs": "Open Runs",
        "open_validation": "Open Validation",
        "open_artifacts": "Open Artifacts",
        "open_system": "Open System Governance",
        "copy_provider_doctor": "Copy Provider Doctor",
        "dashboard_home": "Dashboard home",
        "projects_hint": "Projects, workflows, and task board",
        "runs_hint": "Cross-project run list",
        "validation_hint": "Experiments and comparison reports",
        "artifacts_hint": "Reports, diffs, and evidence",
        "system_hint": "Providers, policy, skills, and memory",
        "provider_doctor_hint": "muxdev provider doctor",
        "project_prefix": "Project",
        "action_prefix": "Action",
        "run_prefix": "Run",
        "project_health": "Project Health",
        "active_tasks": "Active Tasks",
        "run_profile": "Run Profile",
        "shared_state": "Shared State",
        "memory_board": "Shared State / Memory Board",
        "context_ready": "Project context is ready",
        "context_missing": "Project context is missing",
        "no_facts": "No run facts yet.",
        "workflow_board": "Workflow Board",
        "role_lanes": "Role Lanes",
        "filters": "Filters",
        "provider_learning_trend": "Provider Learning Trend",
        "parallel_conflict_map": "Parallel Conflict Map",
        "multi_repo_map": "Multi-Repo Map",
        "workflow_template_preview": "Workflow Template Preview",
        "providers": "Providers",
        "policy_budget_git": "Policy / Budget / Git Safety",
        "role_sessions": "Role Sessions",
        "workflow_templates": "Workflow Templates",
        "skills": "Skills",
        "memory_context": "Memory Context",
        "advanced_control": "Advanced Control",
        "standard_labels": {
            "confidence": "Confidence Threshold",
            "tests": "Tests",
            "review": "Review Blockers",
            "rollback": "Rollback",
            "artifacts": "Report / Diff / Evidence",
            "budget": "Budget",
            "human_attention": "Human Attention",
            "experiment_exists": "Validation Experiment",
            "baseline_coverage": "Baseline Coverage",
            "score": "Aggregate Score",
            "test_pass_rate": "Test Pass Rate",
            "evidence_confidence": "Evidence Confidence",
            "safety": "Safety",
            "rollback_efficiency": "Rollback / Cost Efficiency",
            "provider_ready": "Provider Availability",
            "git_safety": "Git Safety",
            "budget_guardrail": "Budget Guardrail",
            "mcp_guardrails": "MCP Guardrails",
            "skills_lock": "Skills Lock",
            "memory_conflicts": "Memory Conflicts",
            "profile_gate": "Profile / Gate",
            "workflow_template": "Workflow Template",
            "role_template": "Role Providers",
            "project_context": "Project Context",
            "project_budget": "Project Budget",
        },
        "standard_actions": {
            "none": "No action",
            "publish_evidence": "Complete reports, diffs, and evidence",
            "resolve_risk": "Resolve high-risk items",
            "add_tests": "Add or rerun tests",
            "complete_review": "Complete review and clear blockers",
            "enable_rollback": "Create rollback snapshot",
            "review_budget": "Review budget and cost",
            "resolve_attention": "Handle approvals or provider actions",
            "run_validation": "Run validation suite",
            "review_validation": "Review validation report",
            "fix_provider": "Fix provider configuration",
            "fix_git": "Check Git safety",
            "setup_mcp": "Check MCP guardrails",
            "resolve_memory": "Resolve memory conflicts",
            "configure_project": "Complete project configuration",
        },
        "status": {
            "completed": "Completed",
            "running": "Running",
            "awaiting_approval": "Awaiting Approval",
            "awaiting_provider_action": "Awaiting Provider",
            "paused_budget": "Budget Paused",
            "blocked": "Blocked",
            "aborted": "Aborted",
            "failed": "Failed",
            "created": "Created",
            "queued": "Queued",
            "pending": "Pending",
            "reviewable": "Reviewable",
            "trusted": "Trusted",
            "collecting": "Collecting",
            "risky": "Risky",
            "watch": "Watch",
            "ready": "Ready",
            "ok": "Met",
            "degraded": "Needs Work",
            "not_git": "Not Git",
            "dirty": "Dirty",
            "clean": "Clean",
        },
        "action_labels": {
            "provider_action": "Provider Action",
            "clarification": "Question",
            "approval": "Approval Required",
            "recovery": "Recovery Needed",
            "plan_feedback": "Planning Feedback",
        },
        "columns": {
            "experiment_id": "Experiment",
            "task_id": "Task",
            "suite": "Suite",
            "winner": "Winner",
            "strategies": "Strategies",
            "report": "Report",
            "report_endpoint": "Report Link",
            "diff_endpoint": "Diff Link",
            "cost_usd": "Cost",
            "updated_at": "Updated",
            "name": "Name",
            "kind": "Kind",
            "stage_id": "Stage",
            "path": "Path",
            "created_at": "Created",
            "passed": "Passed",
            "command": "Command",
            "summary": "Summary",
            "type": "Type",
            "severity": "Severity",
            "file": "File",
            "line": "Line",
            "suggestion": "Suggestion",
            "run_id": "Run",
            "label": "Label",
            "confidence": "Confidence",
            "current": "Current",
            "target": "Target",
            "evidence": "Evidence",
            "action": "Action",
            "review_id": "Review",
            "decision": "Decision",
            "patch_hash": "Patch Hash",
            "findings": "Findings",
            "role": "Role",
            "value": "Value",
            "source": "Source",
            "trust": "Trust",
            "workflow": "Workflow",
            "roles": "Roles",
            "providers": "Providers",
            "status": "Status",
            "risk_level": "Risk Level",
            "description": "Description",
            "phases": "Phases",
            "supported_providers": "Supported Providers",
            "tool": "Tool",
            "reason": "Reason",
        },
    },
}


_LIVE_DASHBOARD_WORKBENCH_TEMPLATE = """<!doctype html>
<html lang="__HTML_LANG__">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__TITLE__</title>
  <style>
    :root {
      --bg:#f4f6f8; --panel:#ffffff; --panel-soft:#f8fafc; --ink:#17202a; --muted:#627082;
      --line:#d9e1ea; --line-strong:#c4ceda; --accent:#0f766e; --accent-soft:#e8fbf7;
      --warn:#a16207; --warn-soft:#fffbeb; --bad:#b91c1c; --bad-soft:#fff1f2; --good:#15803d; --good-soft:#ecfdf5;
      --sidebar:#eef2f7; --activity:#18212f; --activity-muted:#a8b3c5;
    }
    * { box-sizing:border-box; }
    html,body { min-height:100%; }
    body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.45 ui-sans-serif,system-ui,"Segoe UI",sans-serif; }
    button,input,select,textarea { font:inherit; }
    button { border:1px solid var(--line); background:#fff; color:var(--ink); border-radius:6px; padding:6px 10px; cursor:pointer; white-space:nowrap; }
    button:hover { border-color:var(--line-strong); }
    button.active,button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
    button.ghost { background:transparent; }
    input,select,textarea { border:1px solid var(--line); background:#fff; color:var(--ink); border-radius:6px; padding:7px 9px; min-width:0; }
    textarea { width:100%; resize:vertical; }
    code { background:#eef2f7; border:1px solid #d8dee8; border-radius:4px; padding:1px 5px; }
    h1,h2,h3 { margin:0; letter-spacing:0; }
    h1 { font-size:20px; }
    h2 { font-size:16px; }
    h3 { font-size:12px; color:var(--muted); text-transform:uppercase; }
    table { width:100%; border-collapse:collapse; table-layout:fixed; }
    th,td { border-bottom:1px solid #edf1f7; padding:7px 6px; text-align:left; vertical-align:top; overflow-wrap:anywhere; }
    th { color:var(--muted); font-size:12px; }
    pre { margin:0; background:#101827; color:#e2e8f0; border-radius:8px; padding:12px; overflow:auto; max-height:330px; }
    .topbar { min-height:64px; padding:12px 18px; border-bottom:1px solid var(--line); background:var(--panel); display:grid; grid-template-columns:minmax(220px,1fr) auto; gap:16px; align-items:center; }
    .brand { display:grid; gap:2px; }
    .meta { color:var(--muted); font-size:12px; overflow-wrap:anywhere; }
    .top-actions { display:flex; gap:8px; align-items:center; flex-wrap:wrap; justify-content:flex-end; }
    .status-dot { width:8px; height:8px; border-radius:999px; display:inline-block; background:var(--muted); }
    .daemon { display:inline-flex; align-items:center; gap:7px; }
    .command-bar { padding:10px 14px; border-bottom:1px solid var(--line); background:var(--panel); display:grid; grid-template-columns:minmax(170px,260px) minmax(240px,1fr) 110px 110px 110px 100px auto auto; gap:8px; align-items:center; }
    .workbench { min-height:calc(100vh - 116px); display:grid; grid-template-columns:52px minmax(240px,320px) minmax(0,1fr); }
    .activity-bar { background:var(--activity); padding:8px 6px; display:grid; align-content:start; gap:7px; }
    .activity-button { width:40px; height:40px; border:0; border-radius:6px; background:transparent; color:var(--activity-muted); display:grid; place-items:center; padding:0; }
    .activity-button.active { background:#243246; color:#fff; }
    .activity-button span { font-size:18px; line-height:1; }
    .side-panel { background:var(--sidebar); border-right:1px solid var(--line); padding:12px; overflow:auto; display:grid; align-content:start; gap:12px; }
    .side-section { display:grid; gap:8px; }
    .work-area { min-width:0; min-height:0; display:grid; grid-template-rows:auto minmax(0,1fr); overflow:hidden; }
    .attention-strip,.decision-rail { background:#fff; border-bottom:1px solid var(--line); padding:10px 12px; display:grid; grid-template-columns:minmax(0,1fr); gap:8px; align-items:center; }
    .attention-title { display:none; }
    .attention-items { display:grid; grid-template-columns:repeat(4,minmax(150px,1fr)); gap:8px; min-width:0; }
    .attention-card { min-width:0; min-height:70px; text-align:left; display:grid; grid-template-columns:minmax(0,1fr) auto; gap:8px; align-items:start; background:var(--panel-soft); border-color:transparent; }
    .attention-card.active { border-color:var(--accent); box-shadow:inset 3px 0 0 var(--accent); background:#fff; }
    .attention-card strong { font-size:18px; line-height:1; }
    .attention-card b { display:block; margin-bottom:3px; }
    .editor { min-width:0; min-height:0; padding:16px; overflow:hidden; display:grid; grid-template-columns:minmax(0,1fr) minmax(360px,420px); gap:16px; align-items:stretch; }
    .workspace-canvas { min-width:0; overflow:auto; display:grid; align-content:start; gap:12px; }
    .workspace-header { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:12px; align-items:end; padding:2px 2px 8px; border-bottom:1px solid var(--line); }
    .workspace-header h2 { font-size:18px; }
    .inspector { min-width:0; background:var(--panel); border:1px solid var(--line-strong); border-radius:8px; display:grid; grid-template-rows:auto auto minmax(0,1fr); overflow:hidden; box-shadow:0 8px 24px rgba(15,23,42,.06); }
    .inspector-head { padding:12px; border-bottom:1px solid var(--line); display:grid; gap:3px; }
    .inspector-tabs { padding:8px; border-bottom:1px solid var(--line); display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
    .inspector-content { padding:10px; overflow:auto; min-height:0; }
    .panel { min-width:0; }
    .card,.surface,.focus-panel { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; min-width:0; }
    .panel-head,.surface-head { display:flex; justify-content:space-between; gap:12px; align-items:flex-start; margin-bottom:10px; }
    .view { display:none; }
    .view.active { display:grid; gap:14px; }
    .hero { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:12px; align-items:end; }
    .hero h2 { font-size:20px; }
    .grid-2 { display:grid; grid-template-columns:minmax(0,1.2fr) minmax(320px,.8fr); gap:14px; align-items:start; }
    .stack { display:grid; gap:14px; }
    .summary-grid,.metrics,.health-grid,.confidence-grid,.governance-grid,.system-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:10px; }
    .summary-card { width:100%; min-width:0; text-align:left; white-space:normal; display:grid; gap:4px; border-radius:8px; padding:12px; background:#fff; }
    .summary-card.active { border-color:var(--accent); box-shadow:inset 3px 0 0 var(--accent); }
    .focus-panel { display:grid; gap:12px; border-color:var(--line-strong); box-shadow:0 10px 28px rgba(15,23,42,.06); }
    .focus-head { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:12px; align-items:start; }
    .focus-title { font-size:18px; font-weight:800; overflow-wrap:anywhere; }
    .focus-meta { color:var(--muted); font-size:12px; display:flex; gap:8px; flex-wrap:wrap; }
    .focus-body { display:grid; grid-template-columns:minmax(0,1.2fr) minmax(240px,.8fr); gap:12px; align-items:start; }
    .stage-flow { display:flex; gap:6px; flex-wrap:wrap; }
    .stage-flow span { border:1px solid var(--line); border-radius:999px; padding:4px 8px; background:var(--panel-soft); font-size:12px; }
    .stage-flow span.active { border-color:var(--accent); background:var(--accent-soft); color:var(--accent); font-weight:800; }
    .kpi-stack { display:grid; gap:8px; }
    .kpi-row { border:1px solid var(--line); border-radius:8px; padding:9px 10px; display:grid; grid-template-columns:minmax(0,1fr) auto; gap:8px; background:var(--panel-soft); }
    .kpi-row strong { font-size:14px; }
    .overview-lanes { display:grid; grid-template-columns:minmax(0,1fr) minmax(300px,.8fr); gap:12px; align-items:start; }
    .support-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; align-items:start; }
    .surface { display:grid; align-content:start; gap:8px; }
    .surface.compact { padding:12px; }
    .gap-list { display:grid; gap:7px; }
    .gap-item { border:1px solid var(--line); border-radius:8px; padding:8px; background:var(--panel-soft); }
    .standard-list { display:grid; gap:8px; }
    .standard-card { border:1px solid var(--line); border-radius:8px; padding:10px; background:#fff; display:grid; gap:6px; }
    .standard-card.ready { border-color:#bbf7d0; background:var(--good-soft); }
    .standard-card.watch { border-color:#fde68a; background:var(--warn-soft); }
    .standard-card.blocked { border-color:#fecaca; background:var(--bad-soft); }
    .standard-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:6px; }
    .standard-grid span,.standard-grid b { min-width:0; overflow-wrap:anywhere; }
    .metric strong { display:block; font-size:24px; }
    .lane-board { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:10px; }
    .lane,.column { min-width:0; background:var(--panel-soft); border:1px solid var(--line); border-radius:8px; padding:10px; }
    .lane h3,.column h3 { display:flex; justify-content:space-between; gap:8px; }
    .compact-list { display:grid; gap:8px; }
    .compact-row { width:100%; min-width:0; text-align:left; white-space:normal; display:grid; grid-template-columns:minmax(0,1fr) auto; gap:10px; align-items:start; border:1px solid var(--line); border-radius:8px; padding:10px; background:#fff; }
    .compact-row.active { border-color:var(--accent); box-shadow:inset 3px 0 0 var(--accent); }
    .task-card,.action-card,.timeline-step { border:1px solid var(--line); border-radius:8px; padding:10px; margin-top:8px; background:#fff; }
    .task-card.hidden { opacity:.7; }
    .task-card strong { overflow-wrap:anywhere; }
    .status { display:inline-flex; border:1px solid var(--line); border-radius:999px; padding:1px 7px; font-weight:700; font-size:12px; }
    .completed,.ready,.trusted { color:var(--good); background:var(--good-soft); border-color:#bbf7d0; }
    .running,.collecting { color:var(--accent); background:var(--accent-soft); border-color:#99f6e4; }
    .awaiting_approval,.awaiting_provider_action,.paused_budget,.watch,.reviewable { color:var(--warn); background:var(--warn-soft); border-color:#fde68a; }
    .blocked,.aborted,.failed,.risky { color:var(--bad); background:var(--bad-soft); border-color:#fecaca; }
    .project-item { width:100%; display:grid; grid-template-columns:minmax(0,1fr) auto; gap:8px; align-items:center; border:1px solid var(--line); border-radius:8px; padding:8px; background:#fff; }
    .project-item.active { border-color:var(--accent); box-shadow:inset 3px 0 0 var(--accent); }
    .project-select { border:0; padding:0; min-width:0; text-align:left; display:grid; gap:3px; background:transparent; white-space:normal; }
    .project-name { font-weight:700; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .chips,.actions,.filters { display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
    .chips span { border:1px solid var(--line); border-radius:999px; padding:1px 7px; background:#fff; font-size:12px; }
    .stage-rail { display:flex; gap:8px; flex-wrap:wrap; margin:8px 0 12px; }
    .stage-pill { border:1px solid var(--line); border-radius:999px; padding:3px 8px; background:#fff; font-size:12px; }
    .action-summary { display:grid; grid-template-columns:auto minmax(0,1fr) auto; gap:12px; align-items:center; }
    .action-count { min-width:34px; height:34px; display:grid; place-items:center; border-radius:8px; background:#fff7ed; color:#9a3412; border:1px solid #fed7aa; font-weight:800; }
    .action-count.calm { background:var(--good-soft); color:var(--good); border-color:#bbf7d0; }
    .action-list { margin-top:10px; display:grid; gap:8px; max-height:320px; overflow:auto; }
    .action-row { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:10px; align-items:start; border:1px solid var(--line); border-radius:8px; padding:10px; background:#fff; }
    .palette-backdrop { position:fixed; inset:0; display:none; background:rgba(15,23,42,.24); z-index:10; padding:8vh 16px; }
    .palette-backdrop.open { display:block; }
    .palette { max-width:720px; margin:0 auto; background:#fff; border:1px solid var(--line); border-radius:8px; box-shadow:0 16px 50px rgba(15,23,42,.18); padding:12px; }
    .palette input { width:100%; margin:10px 0; }
    .palette-command { width:100%; display:grid; grid-template-columns:minmax(0,1fr) auto; gap:8px; text-align:left; margin-top:6px; }
    .sparkline { display:grid; gap:6px; }
    .spark-row { display:grid; grid-template-columns:minmax(80px,130px) minmax(0,1fr) auto; gap:8px; align-items:center; }
    .spark-bar { height:8px; border-radius:999px; background:#e2e8f0; overflow:hidden; }
    .spark-bar span { display:block; height:100%; background:var(--accent); }
    @media (max-width:1160px){ .command-bar{grid-template-columns:1fr 1fr 1fr;} .grid-2,.focus-body,.overview-lanes,.support-grid{grid-template-columns:1fr;} .editor{grid-template-columns:1fr;} .inspector{min-height:360px;} }
    @media (max-width:860px){ .topbar{grid-template-columns:1fr;} .top-actions{justify-content:flex-start;} .workbench{grid-template-columns:44px 1fr;} .side-panel{display:none;} .command-bar{grid-template-columns:1fr 1fr;} .hero,.workspace-header,.focus-head{grid-template-columns:1fr;} .attention-strip{grid-template-columns:1fr;} .attention-items{grid-template-columns:1fr 1fr;} }
    @media (max-width:560px){ .editor{padding:10px;} .command-bar{grid-template-columns:minmax(0,1fr) auto;} #task-project,#new-task{grid-column:1 / -1;} #new-provider,#new-profile,#new-gate,#new-cost{display:none;} .metrics{grid-template-columns:1fr;} .attention-items{display:flex; overflow-x:auto; padding-bottom:2px;} .attention-card{min-width:210px;} }
  </style>
</head>
<body data-lang="__DASHBOARD_LANG__" __BODY_ATTR__>
  <header class="topbar">
    <div class="brand">
      <h1>__APP_NAME__</h1>
      <div class="meta">__SUBTITLE__</div>
    </div>
    <div class="top-actions">
      <span class="daemon" id="daemon-status"><span class="status-dot"></span>__DAEMON_CONNECTING__</span>
      <button type="button" id="language-toggle" onclick="switchLanguage()">__LANGUAGE_TOGGLE__</button>
    </div>
  </header>
  <form class="command-bar" onsubmit="createTask(event)">
    <select id="task-project" title="__PROJECT_TITLE__"></select>
    <input id="new-task" placeholder="__NEW_TASK_PLACEHOLDER__" autocomplete="off">
    <select id="new-provider"><option value="mock">mock</option><option value="auto">auto</option><option value="codex">codex</option><option value="claude">claude</option><option value="gemini">gemini</option></select>
    <select id="new-profile"><option value="">__PROFILE_AUTO__</option><option value="squad">__PROFILE_SQUAD__</option><option value="solo">__PROFILE_SOLO__</option><option value="ci">__PROFILE_CI__</option></select>
    <select id="new-gate"><option value="">__GATE_AUTO__</option><option value="safe">__GATE_SAFE__</option><option value="strict">__GATE_STRICT__</option><option value="ci">__GATE_CI__</option></select>
    <input id="new-cost" type="number" step="0.05" min="0" value="0.50" title="__BUDGET_TITLE__">
    <button type="button" onclick="openCommandPalette()">__COMMAND_PALETTE__</button>
    <button class="primary" type="submit">__CREATE_TASK__</button>
  </form>
  <section id="command-palette" class="palette-backdrop" onclick="closeCommandPalette(event)">
    <div class="palette" onclick="event.stopPropagation()">
      <h2 id="palette-title"></h2>
      <input id="command-search" oninput="renderCommandPalette()">
      <div id="command-results"></div>
    </div>
  </section>
  <div class="workbench">
    <nav class="activity-bar" id="activity-bar"></nav>
    <aside class="side-panel">
      <section class="side-section"><h2 id="explorer-title"></h2><div class="meta" id="explorer-note"></div></section>
      <section class="side-section"><h3 id="explorer-projects-title"></h3><div id="project-list"></div></section>
      <section class="side-section"><h3 id="explorer-workflows-title"></h3><div id="workflow-list" class="chips"></div></section>
      <section class="side-section"><h3 id="explorer-filters-title"></h3><div id="sidebar-filters" class="filters"></div></section>
      <section class="side-section"><h3 id="explorer-selected-title"></h3><div id="selected-run-summary" class="card"></div></section>
    </aside>
    <section class="work-area">
      <section class="decision-rail" aria-label="attention-strip">
        <div class="attention-title" id="attention-strip-title"></div>
        <div class="attention-items" id="attention-strip"></div>
      </section>
      <main class="editor">
        <section class="workspace-canvas">
          <section class="workspace-header">
            <div><h2 id="hero-headline"></h2><div class="meta" id="hero-subtitle"></div></div>
            <div class="meta"><span id="product-experience-label"></span> <code>/product/experience</code></div>
          </section>
          <section id="view-overview" class="view active">
            <section id="focus-board" class="focus-panel"></section>
            <section class="overview-lanes">
              <section class="surface"><div class="surface-head"><div><h2 id="action-center-title"></h2><div class="meta" id="action-center-note"></div></div></div><div id="action-center"></div></section>
              <section class="surface"><div class="surface-head"><h2 id="active-runs-title"></h2></div><div id="active-runs" class="compact-list"></div></section>
            </section>
            <section class="support-grid">
              <section class="surface compact"><h2 id="current-status-title"></h2><div id="current-status" class="metrics"></div></section>
              <section class="surface compact"><h2 id="compact-workspace-title"></h2><div id="overview-summaries" class="summary-grid"></div></section>
              <section class="surface compact"><h2 id="recent-evidence-title"></h2><div id="recent-evidence" class="compact-list"></div></section>
            </section>
          </section>
          <section id="view-projects" class="view">
            <section class="panel"><div class="panel-head"><div><h2 id="project-title"></h2><div id="project-path" class="meta"></div></div><button type="button" onclick="openInspector('evidence',{detail:'project'})" id="project-detail-button"></button></div><div id="project-overview"></div></section>
            <section class="panel"><h2 id="project-tasks-title"></h2><div id="project-tasks"></div></section>
          </section>
          <section id="view-runs" class="view">
            <section class="panel"><div class="panel-head"><div><h2 id="runs-title"></h2><div class="meta" id="runs-note"></div></div></div><div id="runs-list"></div></section>
          </section>
          <section id="view-validation" class="view">
            <section class="panel"><div class="panel-head"><div><h2 id="validation-standards-title"></h2><div class="meta" id="validation-standards-note"></div></div></div><div id="validation-standards" class="standard-list"></div></section>
            <section class="panel"><div class="panel-head"><div><h2 id="validation-title"></h2><div class="meta" id="validation-note"></div></div></div><div id="validation-summary" class="summary-grid"></div><div id="validation-experiments"></div></section>
          </section>
          <section id="view-artifacts" class="view">
            <section class="panel"><div class="panel-head"><div><h2 id="artifacts-title"></h2><div class="meta" id="artifacts-note"></div></div></div><div id="artifacts-center"></div></section>
          </section>
          <section id="view-system" class="view">
            <section class="panel"><div class="panel-head"><div><h2 id="system-title"></h2><div class="meta" id="system-note"></div></div></div><div id="system-catalog" class="compact-list"></div><div hidden><div id="system-providers"></div><div id="system-policy"></div><div id="mcp-summary"></div><div id="role-templates"></div><div id="workflow-templates"></div><div id="skills-catalog"></div><div id="memory-governance"></div><div id="advanced-control"></div></div></section>
          </section>
        </section>
        <aside class="inspector" aria-label="detail-inspector">
          <div class="inspector-head">
            <h2 id="inspector-title"></h2>
            <div class="meta" id="inspector-note"></div>
          </div>
          <div class="inspector-tabs" id="inspector-tabs"></div>
          <div class="inspector-content" id="inspector-content"></div>
        </aside>
      </main>
    </section>
  </div>
<script>
const TEXT=__TEXT__;
const LANG=document.body.dataset.lang||'zh-CN';
const VALID_LANGS=['zh-CN','en'];
const state={taskId:document.body.dataset.taskId||null,selectedProjectId:null,activity:'overview',inspectorTab:'problems',activeSummary:'attention',selectedSystemSection:'providers',events:[],outputText:'',globalConfigLoaded:false,currentProjectTasks:[],projectFilters:{provider:'',workflow:'',status:'',risk:'',cost:''},paletteOpen:false,commands:[],visibleCommands:[],actionRows:[],providerRows:[],approvalRows:[],artifactCenter:{},standards:{},selectedTaskDetail:null,selectedTaskHtml:'',selectedEvidenceHtml:'',projectDetailHtml:'',allRunRows:[]};
const esc=v=>String(v??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const fmt=v=>Array.isArray(v)||(v&&typeof v==='object')?JSON.stringify(v):v;
const t=key=>TEXT[key]||key;
const tStatus=v=>(TEXT.status&&TEXT.status[v])||v||'-';
const tAction=v=>(TEXT.action_labels&&TEXT.action_labels[v])||v||'-';
const colLabel=v=>(TEXT.columns&&TEXT.columns[v])||v;
const statusClass=v=>`status ${v||''}`;
function syncStoredLanguage(){const query=new URLSearchParams(location.search);if(query.has('lang')){localStorage.setItem('muxdev.dashboard.lang',LANG);return;}const stored=localStorage.getItem('muxdev.dashboard.lang');if(VALID_LANGS.includes(stored)&&stored!==LANG){query.set('lang',stored);location.search=query.toString();}}
function switchLanguage(){const next=LANG==='en'?'zh-CN':'en';localStorage.setItem('muxdev.dashboard.lang',next);const query=new URLSearchParams(location.search);query.set('lang',next);location.search=query.toString();}
async function api(path,options){const r=await fetch('/api'+path,options);if(!r.ok)throw new Error(await r.text());return r.json();}
async function optionalApi(path,fallback){try{return await api(path)}catch(_e){return fallback}}
function setText(id,value){const el=document.getElementById(id);if(el)el.textContent=value;}
function initChrome(){setText('palette-title',t('command_palette'));document.getElementById('command-search').placeholder=t('command_search');setText('hero-headline',t('hero_headline'));setText('hero-subtitle',t('hero_subtitle'));setText('product-experience-label',t('product_experience'));setText('explorer-title',t('explorer_title'));setText('explorer-note',t('projects_hint'));setText('explorer-projects-title',t('explorer_projects'));setText('explorer-workflows-title',t('explorer_workflows'));setText('explorer-filters-title',t('explorer_filters'));setText('explorer-selected-title',t('explorer_selected'));setText('attention-strip-title',t('attention_strip'));setText('action-center-title',t('priority_queue'));setText('action-center-note',t('action_center_note'));setText('active-runs-title',t('run_stream'));setText('current-status-title',t('current_status'));setText('compact-workspace-title',t('compact_workspace'));setText('recent-evidence-title',t('recent_evidence'));setText('project-tasks-title',t('project_tasks'));setText('project-detail-button',t('view_details'));setText('runs-title',t('runs_title'));setText('runs-note',t('runs_note'));setText('validation-standards-title',t('validation_standards'));setText('validation-standards-note',t('validation_note'));setText('validation-title',t('validation_title'));setText('validation-note',t('validation_note'));setText('artifacts-title',t('artifacts_title'));setText('artifacts-note',t('artifacts_note'));setText('system-title',t('system_title'));setText('system-note',t('system_note'));renderActivityBar();renderInspectorTabs();renderInspector();}
function renderActivityBar(){const rows=[['overview','⌂',t('activity_overview')],['projects','▣',t('activity_projects')],['runs','▶',t('activity_runs')],['validation','✓',t('activity_validation')],['artifacts','◫',t('activity_artifacts')],['system','⚙',t('activity_system')]];document.getElementById('activity-bar').innerHTML=rows.map(([id,icon,label])=>`<button type="button" class="activity-button ${state.activity===id?'active':''}" title="${esc(label)}" aria-label="${esc(label)}" onclick="setActivity('${id}')"><span>${icon}</span></button>`).join('');}
function setActivity(activity){state.activity=activity;for(const id of ['overview','projects','runs','validation','artifacts','system'])document.getElementById('view-'+id).classList.toggle('active',activity===id);renderActivityBar();if(activity==='system'&&!state.globalConfigLoaded)loadGlobalConfig().catch(showError);if(activity==='runs'&&state.taskId)loadSelectedTask().catch(showError);}
function openInspector(tab,options={}){state.inspectorTab=tab;if(options.detail)state.activeSummary=options.detail;if(options.taskId)state.taskId=options.taskId;if(options.system){sectionSystem(options.system);setActivity('system');}renderInspectorTabs();renderInspector();}
function sectionSystem(id){state.selectedSystemSection=id;state.activeSummary='system';state.activity='system';}
function showError(err){document.getElementById('daemon-status').innerHTML=`<span class="status-dot"></span>${esc(err.message||err)}`;}
async function refresh(){const overview=await api('/dashboard/overview?include_global_config=false');state.artifactCenter=overview.artifact_center||{};state.providerRows=overview.pending_provider_actions||[];state.approvalRows=overview.pending_approvals||[];state.actionRows=overview.action_center||[];state.standards=overview.standards||{};const ids=(overview.projects||[]).map(p=>p.id);if(!state.selectedProjectId||!ids.includes(state.selectedProjectId)){state.selectedProjectId=overview.selected_project_id||ids[0]||null;state.taskId=state.taskId||null;}const selected=(overview.projects||[]).find(p=>p.id===state.selectedProjectId)||(overview.projects||[])[0];if(selected&&!state.taskId){const first=firstProjectTask(selected);if(first)state.taskId=first.task_id;}setDaemonStatus(overview);renderProjectSelect(overview.projects||[],selected);renderExplorer(overview.projects||[],selected);renderAttentionStrip(overview);renderFocusBoard(overview,overview.projects||[]);renderCurrentStatus(overview.current_status||{},overview.counts||{});renderActionCenter(state.actionRows);renderActiveRuns(overview.task_board||[]);renderOverviewSummaries(overview.delivery_confidence||{},overview.health_strip||[],overview.governance_summary||{},state.standards);renderRecentEvidence(state.artifactCenter);renderProjects(selected);renderRuns(overview.projects||[]);renderValidation(overview.validation||{});renderArtifacts(state.artifactCenter);buildCommandPalette(overview.projects||[],state.actionRows);if(state.taskId){await loadSelectedTask(state.artifactCenter);}else{renderSelectedTask(null,state.artifactCenter);}setActivity(state.activity);renderInspectorTabs();renderInspector();}
function setDaemonStatus(overview){const counts=overview.counts||{};document.getElementById('daemon-status').innerHTML=`<span class="status-dot"></span>${t('tasks')}=${esc(counts.tasks??0)} ${t('running')}=${esc(counts.active??0)} ${t('attention')}=${esc(counts.needs_attention??0)}`;if(overview.headline&&LANG==='en')setText('hero-headline',overview.headline);}
function attentionRows(){return [...state.actionRows,...state.providerRows.map(row=>({...row,kind:row.kind==='clarification_required'?'clarification':'provider_action'})),...state.approvalRows.map(row=>({...row,kind:'approval'}))];}
function renderAttentionStrip(overview={}){const problems=attentionRows().length,trusted=state.standards.trusted_delivery||{},validation=state.standards.validation||{},governance=state.standards.governance||{};const rows=[{id:'problems',label:t('decision_attention'),value:problems,note:problems?t('attention_strip_note'):t('problems_empty'),active:state.inspectorTab==='problems',run:"openInspector('problems')"},{id:'confidence',label:t('decision_delivery'),value:standardSummary(trusted)||'-',note:standardGapNote(trusted)||t('trusted_ready'),active:state.activeSummary==='confidence',run:"openInspector('evidence',{detail:'confidence'})"},{id:'validation_standards',label:t('decision_validation'),value:standardSummary(validation)||'-',note:standardGapNote(validation)||t('trusted_ready'),active:state.activeSummary==='validation_standards',run:"openInspector('evidence',{detail:'validation_standards'})"},{id:'governance_standards',label:t('decision_system'),value:standardSummary(governance)||'-',note:standardGapNote(governance)||t('ready'),active:state.selectedSystemSection==='governance_standards',run:"openInspector('evidence',{system:'governance_standards'})"}];document.getElementById('attention-strip').innerHTML=rows.map(row=>`<button type="button" class="attention-card ${row.active?'active':''}" onclick="${row.run}"><span><b>${esc(row.label)}</b><br><span class="meta">${esc(row.note)}</span></span><strong>${esc(row.value)}</strong></button>`).join('');}
function standardGap(section){return (section?.items||[]).find(item=>item.status!=='ready');}
function standardGapNote(section){const gap=standardGap(section);return gap?`${standardLabel(gap)} · ${standardAction(gap)}`:'';}
function renderInspectorTabs(){let tabs=[['problems',t('bottom_problems')],['evidence',t('inspector_tab_evidence')],['output',t('inspector_tab_output')],['actions',t('inspector_tab_actions')]];if(state.inspectorTab!=='problems'&&(String(state.activeSummary||'').includes('standards')||state.selectedSystemSection?.includes('standards')))tabs=[['evidence',t('inspector_tab_gaps')],['actions',t('inspector_tab_actions')]];else if(state.inspectorTab!=='problems'&&(state.activeSummary==='run'||state.taskId))tabs=[['evidence',t('inspector_tab_overview')],['output',t('inspector_tab_output')],['actions',t('inspector_tab_actions')]];document.getElementById('inspector-tabs').innerHTML=tabs.map(([id,label])=>`<button type="button" class="${state.inspectorTab===id?'active':''}" onclick="openInspector('${id}')">${esc(label)}</button>`).join('');}
function renderInspector(){const titleMap={problems:t('bottom_problems'),output:t('bottom_output'),evidence:t('selected_detail'),actions:t('bottom_actions')};setText('inspector-title',titleMap[state.inspectorTab]||t('inspector_title'));setText('inspector-note',state.inspectorTab==='problems'?t('attention_strip_note'):state.inspectorTab==='actions'?t('executable_actions'):state.inspectorTab==='output'?t('latest_output'):t('inspector_title'));let html='';if(state.inspectorTab==='problems'){const rows=attentionRows();html=rows.length?rows.map(problemCard).join(''):`<div class="meta">${esc(t('problems_empty'))}</div>`;}else if(state.inspectorTab==='actions'){const section=currentStandardSection();html=section?renderStandardActions(section):(renderProviderActions(state.providerRows)+renderApprovals(state.approvalRows)||`<div class="meta">${esc(t('actions_empty'))}</div>`);}else if(state.inspectorTab==='output'){html=`<pre id="events">${esc(state.outputText||state.events.join(String.fromCharCode(10))||t('output_empty'))}</pre>`;}else{html=renderEvidenceInspector();}document.getElementById('inspector-content').innerHTML=html;renderAttentionStrip();}
function renderEvidenceInspector(){if(state.activeSummary==='confidence')return `<h2>${esc(t('delivery_confidence'))}</h2>${state.confidenceHtml||''}`;if(state.activeSummary==='health')return `<h2>${esc(t('health_strip'))}</h2>${state.healthHtml||''}`;if(state.activeSummary==='governance')return `<h2>${esc(t('learning_governance'))}</h2>${state.governanceHtml||''}`;if(state.activeSummary==='validation_standards')return standardSection(state.standards.validation,{title:true});if(state.activeSummary==='config_standards')return standardSection(state.standards.configuration,{title:true});if(state.activeSummary==='system')return renderSystemInspector();if(state.activeSummary==='project')return state.projectDetailHtml||`<div class="meta">${esc(t('inspector_empty'))}</div>`;if(state.selectedTaskHtml||state.selectedEvidenceHtml)return state.selectedTaskHtml+state.selectedEvidenceHtml;const recent=state.artifactCenter?.recent||[];return recent.length?tableBlock(t('artifacts_title'),recent,['task_id','task','report_endpoint','diff_endpoint','tokens','cost_usd']):`<div class="meta">${esc(t('inspector_empty'))}</div>`;}
function renderSystemInspector(){const standards={trusted_standards:state.standards.trusted_delivery,validation_standards:state.standards.validation,governance_standards:state.standards.governance,config_standards:state.standards.configuration};if(standards[state.selectedSystemSection])return standardSection(standards[state.selectedSystemSection],{title:true});const map={providers:t('providers'),policy:t('policy_budget_git'),mcp:'MCP',roles:t('role_sessions'),workflows:t('workflow_templates'),skills:t('skills'),memory:t('memory_context'),advanced:t('advanced_control')};const ids={providers:'system-providers',policy:'system-policy',mcp:'mcp-summary',roles:'role-templates',workflows:'workflow-templates',skills:'skills-catalog',memory:'memory-governance',advanced:'advanced-control'};const id=ids[state.selectedSystemSection]||ids.providers;return `<h2>${esc(map[state.selectedSystemSection]||t('system_catalog'))}</h2>${document.getElementById(id)?.innerHTML||`<div class="meta">${esc(t('inspector_empty'))}</div>`}`;}
function renderOverviewSummaries(confidence,health,governance,standards={}){const confidenceRows=confidence.items||[],healthRows=health||[],governanceRows=governance.items||[],trusted=standards.trusted_delivery||{},governanceStandard=standards.governance||{};document.getElementById('overview-summaries').innerHTML=[summaryButton('confidence',t('delivery_confidence'),confidenceRows.length,standardSummary(trusted)||confidenceRows[0]?.task_title||confidenceRows[0]?.task_id||t('evidence_empty')),summaryButton('health',t('health_strip'),healthRows.length,healthRows[0]?.summary||t('none')),summaryButton('governance',t('learning_governance'),governanceRows.length,standardSummary(governanceStandard)||governanceRows[0]?.summary||t('none'))].join('');state.confidenceHtml=(confidenceRows.length?confidenceRows.map(deliveryConfidenceCard).join(''):`<div class="meta">${esc(t('evidence_empty'))}</div>`)+standardSection(trusted,{title:true});state.healthHtml=healthRows.length?healthRows.map(row=>`<div class="card ${esc(row.status||'')}"><strong>${esc(row.label||row.id)}</strong><div class="meta">${esc(row.summary||'-')}</div></div>`).join(''):`<div class="meta">${esc(t('none'))}</div>`;state.governanceHtml=(governanceRows.length?governanceRows.map(row=>`<div class="card ${esc(row.status||'')}"><strong>${esc(row.label||row.id)}</strong><div class="meta">${esc(row.summary||'-')}</div></div>`).join(''):`<div class="meta">${esc(t('none'))}</div>`)+standardSection(governanceStandard,{title:true});}
function summaryButton(id,label,count,note){return `<button type="button" class="summary-card ${state.activeSummary===id?'active':''}" onclick="state.activeSummary='${id}';openInspector('evidence')"><strong>${esc(label)}</strong><span class="meta">${esc(count)} · ${esc(note)}</span><span>${esc(t('view_details'))}</span></button>`;}
function deliveryConfidenceCard(row){return `<div class="card ${esc(row.label||'collecting')}"><strong>${esc(tStatus(row.label)||row.label||'collecting')} ${esc(row.score??0)}%</strong><div>${esc(row.task_title||row.task_id||'-')}</div><div class="meta">${esc(row.project_name||'-')} · ${esc(row.current_stage||row.status||'-')}</div><div class="chips"><span>${esc(t('tests'))} ${esc(row.tests?.status||t('missing'))}</span><span>${esc(t('review'))} ${esc(row.review?.status||t('none'))}</span><span>${esc(t('rollback'))} ${row.rollback?.available?esc(t('available')):esc(t('missing'))}</span></div></div>`;}
function renderFocusBoard(overview,projects){const task=focusTask(overview,projects),el=document.getElementById('focus-board');if(!el)return;if(!task){el.innerHTML=`<div class="focus-head"><div><div class="meta">${esc(t('focus_workspace'))}</div><div class="focus-title">${esc(t('no_focus_task'))}</div></div><span class="status ready">${esc(t('ready'))}</span></div><div class="meta">${esc(t('select_to_inspect'))}</div>`;return;}const action=focusAction(task),trusted=state.standards.trusted_delivery||{},validation=state.standards.validation||{},governance=state.standards.governance||{},gaps=[standardGap(trusted),standardGap(validation),standardGap(governance)].filter(Boolean).slice(0,3),confidence=task.delivery_confidence||{},evidence=task.evidence_summary||{};const stages=focusStages(task);const next=action?`${tAction(action.kind)} · ${action.why||action.reason||action.task_title||''}`:(gaps.length?`${standardLabel(gaps[0])} · ${standardAction(gaps[0])}`:t('trusted_ready'));el.innerHTML=`<div class="focus-head"><div><div class="meta">${esc(t('focus_workspace'))}</div><div class="focus-title">${esc(task.title||task.task||task.task_title||task.task_id)}</div><div class="focus-meta"><span>${esc(task.project_name||task.project_path||'-')}</span><span>${esc(task.provider||'-')}</span><span>${esc(t('stage'))}: ${esc(task.current_stage||'-')}</span><span>${esc(t('risk'))}: ${esc(task.risk||'-')}</span></div></div><span class="${statusClass(task.status)}">${esc(tStatus(task.status))}</span></div><div class="focus-body"><div class="kpi-stack"><div><h3>${esc(t('next_action'))}</h3><div class="gap-item">${esc(next)}</div></div><div><h3>${esc(t('stage'))}</h3><div class="stage-flow">${stages.map(row=>`<span class="${row.active?'active':''}">${esc(row.label)}</span>`).join('')}</div></div><div class="actions"><button class="primary" onclick="selectTask('${esc(task.task_id)}')">${esc(t('view_details'))}</button><button onclick="openInspector('problems')">${esc(t('decision_attention'))}</button><button onclick="openInspector('evidence',{detail:'validation_standards'})">${esc(t('validation_standards'))}</button></div></div><div class="kpi-stack"><div class="kpi-row"><span><strong>${esc(t('decision_delivery'))}</strong><span class="meta">${esc(evidence.label||confidence.label||t('missing'))}</span></span><b>${esc(standardSummary(trusted)||'-')}</b></div><div class="kpi-row"><span><strong>${esc(t('decision_validation'))}</strong><span class="meta">${esc(standardGapNote(validation)||t('trusted_ready'))}</span></span><b>${esc(standardSummary(validation)||'-')}</b></div><div class="kpi-row"><span><strong>${esc(t('decision_system'))}</strong><span class="meta">${esc(standardGapNote(governance)||t('ready'))}</span></span><b>${esc(standardSummary(governance)||'-')}</b></div><div><h3>${esc(t('standard_gaps'))}</h3><div class="gap-list">${gaps.length?gaps.map(gap=>`<div class="gap-item"><strong>${esc(standardLabel(gap))}</strong><div class="meta">${esc(standardAction(gap))}</div></div>`).join(''):`<div class="meta">${esc(t('trusted_ready'))}</div>`}</div></div></div></div>`;}
function focusTask(overview,projects){const rows=allTasksFromProjects(projects);if(!rows.length)return null;const attention=attentionRows().find(row=>row.run_id||row.task_id);const attentionId=attention&&(attention.run_id||attention.task_id);const attentionTask=attentionId&&rows.find(row=>String(row.task_id)===String(attentionId));if(attentionTask)return attentionTask;const selected=rows.find(row=>String(row.task_id)===String(state.taskId));if(selected)return selected;return rows.find(row=>['awaiting_approval','awaiting_provider_action','paused_budget','blocked','failed','aborted'].includes(String(row.status)))||rows.find(row=>!['completed','aborted'].includes(String(row.status)))||rows[0];}
function focusAction(task){const id=String(task?.task_id||'');return attentionRows().find(row=>String(row.run_id||row.task_id||'')===id);}
function focusStages(task){const timeline=Array.isArray(task.stage_timeline)?task.stage_timeline:[];const current=String(task.current_stage||'');const rows=timeline.length?timeline.map(row=>({label:row.stage_id||row.id||row.status||'-',active:String(row.stage_id||row.id||'')===current||String(row.status||'')==='running'})):[{label:current||task.status||'-',active:true}];return rows.slice(0,6);}
function standardSection(section,options={}){const rows=section?.items||[];if(!rows.length)return `<div class="meta">${esc(t('evidence_empty'))}</div>`;const title=options.title===false?'':`<h3>${esc(standardSectionTitle(section))}</h3>`;return `${title}<div class="card ${esc(section.status||'watch')}"><strong>${esc(standardSummary(section))}</strong><div class="meta">${esc(tStatus(section.status)||section.status||'watch')}</div></div><div class="standard-list">${rows.map(standardCard).join('')}</div>`;}
function standardCard(row){const tags=[row.severity,row.risk_level,row.evidence_level].filter(Boolean).join(' / ');return `<div class="standard-card ${esc(row.status||'watch')}"><strong>${esc(standardLabel(row))}</strong><span class="${statusClass(row.status)}">${esc(tStatus(row.status)||row.status||'watch')}</span>${tags?`<div class="chips"><span>${esc(tags)}</span></div>`:''}<div class="standard-grid"><span>${esc(t('current_value'))}</span><b>${esc(standardValue(row.current))}</b><span>${esc(t('target_value'))}</span><b>${esc(standardValue(row.target))}</b><span>${esc(t('evidence'))}</span><b>${esc(standardValue(row.evidence))}</b><span>${esc(t('recommended_action'))}</span><b>${esc(standardAction(row))}</b></div></div>`;}
function currentStandardSection(){if(state.activeSummary==='validation_standards')return state.standards.validation;if(state.activeSummary==='config_standards')return state.standards.configuration;if(state.selectedSystemSection==='trusted_standards')return state.standards.trusted_delivery;if(state.selectedSystemSection==='validation_standards')return state.standards.validation;if(state.selectedSystemSection==='governance_standards')return state.standards.governance;if(state.selectedSystemSection==='config_standards')return state.standards.configuration;return null;}
function renderStandardActions(section){const rows=(section?.items||[]).filter(row=>row.status!=='ready');return rows.length?`<div class="standard-list">${rows.map(row=>`<div class="standard-card ${esc(row.status||'watch')}"><strong>${esc(standardLabel(row))}</strong><div class="meta">${esc(standardAction(row))}</div><div class="actions"><button onclick="copyText('${esc(standardAction(row))}')">${esc(t('copy'))}</button></div></div>`).join('')}</div>`:`<div class="card ready"><strong>${esc(t('trusted_ready'))}</strong><div class="meta">${esc(t('actions_empty'))}</div></div>`;}
function standardSectionTitle(section){const map={trusted_delivery:t('trusted_delivery_standards'),validation:t('validation_standards'),governance:t('governance_standards'),configuration:t('configuration_standards')};return map[section?.id]||section?.label||t('standards');}
function standardSummary(section){const total=Number(section?.total||0),passed=Number(section?.passed||0);return total?`${passed}/${total} ${t('standards_met')}`:'';}
function standardLabel(row){return (TEXT.standard_labels&&TEXT.standard_labels[row?.id])||row?.label||row?.id||'-';}
function standardAction(row){return (TEXT.standard_actions&&TEXT.standard_actions[row?.action])||row?.action||t('none');}
function standardValue(value){if(value===undefined||value===null||value==='')return'-';if(Array.isArray(value))return value.length?value.map(standardValue).join(', '):'-';if(value&&typeof value==='object')return Object.entries(value).map(([key,val])=>`${key}: ${standardValue(val)}`).join(', ')||'-';return fmt(value);}
function renderProjectSelect(projects,selected){const el=document.getElementById('task-project');el.innerHTML=(projects||[]).map(p=>`<option value="${esc(p.path||'')}" ${p.id===(selected||{}).id?'selected':''}>${esc(p.name||p.path||t('project_prefix'))}</option>`).join('')||`<option value="">${esc(t('project_prefix'))}</option>`;}
async function createTask(event){event.preventDefault();const task=(document.getElementById('new-task')?.value||'').trim();if(!task){document.getElementById('daemon-status').innerHTML=`<span class="status-dot"></span>${esc(t('task_empty'))}`;return;}const payload={task,workspace:document.getElementById('task-project')?.value||null,provider:document.getElementById('new-provider')?.value||'mock',workflow:'software-dev',profile:document.getElementById('new-profile')?.value||null,gate:document.getElementById('new-gate')?.value||null,max_cost_usd:Number(document.getElementById('new-cost')?.value||0.5)};const created=await api('/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});state.taskId=created.task_id||created.run_id||state.taskId;document.getElementById('new-task').value='';document.getElementById('daemon-status').innerHTML=`<span class="status-dot"></span>${esc(t('submitted'))} ${esc(created.task_id||created.run_id||'')}`;await refresh();}
function renderExplorer(projects,selected){document.getElementById('project-list').innerHTML=projects.length?projects.map(p=>`<div class="project-item ${p.id===(selected||{}).id?'active':''}"><button class="project-select" title="${esc(p.path)}" onclick="state.selectedProjectId='${esc(p.id)}';state.taskId=null;setActivity('projects');refresh()"><span class="project-name">${esc(p.name||p.path)}</span><span class="meta">${esc(p.path||'')}</span><span class="chips"><span>${esc(p.summary?.tasks??0)} ${esc(t('tasks'))}</span><span>${esc(p.summary?.active??0)} ${esc(t('running'))}</span><span>${esc(p.health?.waiting??0)} ${esc(t('waiting'))}</span></span></button><button title="${esc(t('hide_project'))}" onclick="hideProject(event,'${esc(p.id)}')">${esc(t('hide_project'))}</button></div>`).join(''):`<div class="meta">${esc(t('no_visible_projects'))}</div>`;const workflows=(selected?.workflows||[]).map(w=>`<span>${esc(w.name||w.id||t('workflow'))}</span>`).join('');document.getElementById('workflow-list').innerHTML=workflows||`<span>${esc(t('none'))}</span>`;document.getElementById('sidebar-filters').innerHTML=['provider','workflow','status','risk','cost'].map(key=>filterSelect(key,state.currentProjectTasks||[])).join('');renderSelectedRunSummary();}
function renderSelectedRunSummary(){const task=(state.currentProjectTasks||state.allRunRows||[]).find(t=>String(t.task_id)===String(state.taskId));document.getElementById('selected-run-summary').innerHTML=task?`<strong>${esc(task.title||task.task||task.task_id)}</strong><div class="meta">${esc(tStatus(task.status))} · ${esc(task.provider||'-')} · ${esc(task.current_stage||'-')}</div><div class="actions"><button onclick="setActivity('runs')">${esc(t('activity_runs'))}</button><button onclick="openInspector('evidence',{detail:'run'})">${esc(t('bottom_evidence'))}</button></div>`:`<div class="meta">${esc(t('no_task_selected'))}</div>`;}
function firstProjectTask(project){for(const workflow of project.workflows||[])for(const group of workflow.role_groups||[])if((group.tasks||[]).length)return group.tasks[0];return null;}
function allTasksFromProject(project){const tasks=[];(project?.workflows||[]).forEach(w=>(w.role_groups||[]).forEach(g=>(g.tasks||[]).forEach(task=>tasks.push({...task,workflow:task.workflow||w.name||w.id,role:task.role||g.role,project_name:project.name,project_path:project.path}))));return tasks;}
function allTasksFromProjects(projects){return (projects||[]).flatMap(allTasksFromProject);}
function renderCurrentStatus(status,counts){const rows=[[t('running'),status.running??counts.active??0,t('tasks')],[t('waiting'),(status.waiting_provider_action??0)+(status.waiting_muxdev_approval??0),t('actions')],[t('blocked'),status.stuck??0,t('blocked')],[t('projects'),counts.projects??0,t('projects')],[t('completed'),(status.recent_completed||[]).length,t('completed')]];document.getElementById('current-status').innerHTML=rows.map(([a,b,c])=>`<div class="card metric"><h3>${esc(a)}</h3><strong>${esc(b)}</strong><span class="meta">${esc(c)}</span></div>`).join('');}
function renderActionCenter(rows){document.getElementById('action-center').innerHTML=`<div class="action-list">${rows.length?rows.slice(0,5).map(actionRow).join(''):`<div class="card"><strong>${esc(t('trusted_ready'))}</strong><div class="meta">${esc(t('problems_empty'))}</div></div>`}</div>`;}
function actionRow(row){return `<div class="action-row"><div><strong>${esc(tAction(row.kind))}</strong><div class="meta">${esc(row.why||row.reason||row.task_title||row.task_id||'')}</div><div class="chips"><span>${esc(row.project_name||row.run_id||row.task_id||'-')}</span><span>${esc(row.stage_id||'-')}</span></div></div><div class="actions">${row.endpoint?`<button class="primary" onclick="post('${esc(row.endpoint).replace('/api','')}')">${esc(t('handled_continue'))}</button>`:''}${row.secondary_endpoint?`<button onclick="loadText('${esc(row.secondary_endpoint).replace('/api','')}','events')">${esc(t('report'))}</button>`:''}${row.command?`<button onclick="copyText('${esc(row.command)}')">${esc(t('copy'))}</button>`:''}</div></div>`;}
function renderActiveRuns(board){const rows=(board||[]).flatMap(col=>(col.tasks||[]).map(task=>({...task,lane:col.id,label:col.label})));document.getElementById('active-runs').innerHTML=rows.length?rows.slice(0,8).map(task=>`<button type="button" class="compact-row ${String(task.task_id)===String(state.taskId)?'active':''}" onclick="selectTask('${esc(task.task_id)}')"><span><strong>${esc(task.task||task.title||task.task_id)}</strong><br><span class="meta">${esc(tStatus(task.lane)||task.label||task.status||'-')} · ${esc(task.provider||'-')} · ${esc(task.current_stage||'-')}</span></span><span class="${statusClass(task.status)}">${esc(tStatus(task.status))}</span></button>`).join(''):`<div class="meta">${esc(t('none'))}</div>`;}
function taskBoardCard(task){const evidence=task.evidence_summary||{};return `<div class="task-card" tabindex="0" onclick="selectTask('${esc(task.task_id)}')"><strong>${esc(task.task||task.title||task.task_id)}</strong> <span class="${statusClass(task.status)}">${esc(tStatus(task.status))}</span><div class="meta">${esc(task.provider||'-')} · ${esc(task.current_stage||'-')} · $${esc(task.cost_usd||0)}</div><div class="chips"><span>${esc(evidence.label||t('missing'))}</span><span>${esc(t('tests'))} ${esc(evidence.tests||t('missing'))}</span><span>${esc(t('rollback'))} ${esc(evidence.rollback||t('missing'))}</span></div></div>`;}
function selectTask(id){state.taskId=id;state.activity='runs';state.activeSummary='run';setActivity('runs');openInspector('evidence',{detail:'run'});loadSelectedTask().catch(showError);renderSelectedRunSummary();}
function renderProjects(project){const tasks=allTasksFromProject(project);state.currentProjectTasks=tasks;if(!project){setText('project-title',t('no_project_selected'));setText('project-path','');document.getElementById('project-overview').innerHTML=`<div class="meta">${esc(t('no_project_selected'))}</div>`;document.getElementById('project-tasks').innerHTML='';state.projectDetailHtml='';return;}setText('project-title',project.name||t('project_prefix'));setText('project-path',project.path||'');renderProjectOverview(project);renderProjectTasks(project);renderSelectedRunSummary();}
function renderProjectOverview(project){const s=project.summary||{},h=project.health||{},shared=project.shared_state||{},config=project.config||{};const facts=(shared.recent_facts||[]).map(f=>`<li><strong>${esc(f.stage||'-')}</strong> ${esc(f.summary||f.task_id||'-')}</li>`).join('')||`<li>${esc(t('no_facts'))}</li>`;const summary=`<div class="summary-grid"><button type="button" class="summary-card" onclick="openInspector('evidence',{detail:'project'})"><strong>${esc(t('project_health'))}</strong><span class="meta">${esc(tStatus(h.status)||h.status||'idle')} · ${esc(t('waiting'))}=${esc(h.waiting??0)} · ${esc(t('blocked'))}=${esc(h.failed??0)}</span><span>${esc(t('view_details'))}</span></button><button type="button" class="summary-card" onclick="openInspector('evidence',{detail:'project'})"><strong>${esc(t('active_tasks'))}</strong><span class="meta">${esc(s.active??0)} / ${esc(s.tasks??0)} ${esc(t('tasks'))} · ${esc(t('tokens'))} ${esc(s.tokens??0)}</span><span>${esc(t('view_details'))}</span></button><button type="button" class="summary-card" onclick="openInspector('evidence',{detail:'project'})"><strong>${esc(t('run_profile'))}</strong><span class="meta">${esc(config.profile||'-')} · gate=${esc(config.gate||'-')}</span><span>${esc(t('view_details'))}</span></button><button type="button" class="summary-card" onclick="openInspector('evidence',{detail:'project'})"><strong>${esc(t('shared_state'))}</strong><span class="meta">${shared.context_exists?esc(t('ready')):esc(t('missing'))}</span><span>${esc(t('view_details'))}</span></button></div>`;document.getElementById('project-overview').innerHTML=summary;state.projectDetailHtml=`<h2>${esc(t('project_details'))}</h2>${summary}<h2>${esc(t('memory_board'))}</h2><div class="card"><div class="meta">${shared.context_exists?esc(t('context_ready')):esc(t('context_missing'))} ${shared.context_path?`<code>${esc(shared.context_path)}</code>`:''}</div><ul>${facts}</ul></div><h2>${esc(t('workflow_board'))}</h2>${renderWorkflowCards(project)}${projectConfigHtml(config)}`;}
function renderWorkflowCards(project){return (project.workflows||[]).map(w=>`<section><h2>${esc(w.name||w.id||t('workflow'))}</h2><div class="meta">${esc(w.stage_count??0)} ${esc(t('stage'))} · ${esc(w.task_count??0)} ${esc(t('tasks'))}</div><div class="stage-rail">${(w.stages||[]).map(s=>`<span class="stage-pill">${esc(s.id||'-')} · ${esc(s.role||'-')}</span>`).join('')||'<span class="stage-pill">default</span>'}</div><h3>${esc(t('role_lanes'))}</h3><div class="lane-board">${(w.role_groups||[]).map(g=>`<div class="column"><h3>${esc(g.role||'-')} <span>${(g.tasks||[]).length}</span></h3>${(g.tasks||[]).map(taskCard).join('')||`<div class="meta">${esc(t('none'))}</div>`}</div>`).join('')}</div></section>`).join('')||`<div class="meta">${esc(t('none'))}</div>`;}
function renderProjectTasks(project){const tasks=allTasksFromProject(project);state.currentProjectTasks=tasks;const filtered=tasks.filter(matchesProjectFilters);const lowRisk=filtered.filter(row=>(row.risk||'low')==='low');document.getElementById('project-tasks').innerHTML=`<div class="filters"><strong>${esc(t('filters'))}</strong>${['provider','workflow','status','risk','cost'].map(key=>filterSelect(key,tasks)).join('')}<button onclick="resetProjectFilters()">${esc(t('reset'))}</button></div><div class="actions"><strong>${esc(t('low_risk_actions'))}</strong><span class="meta">${lowRisk.length} ${esc(t('low_risk_count'))}</span><button onclick="copyLowRiskReviewLinks()">${esc(t('copy_review_links'))}</button><button onclick="hideCompletedLowRisk()">${esc(t('hide_completed_low_risk'))}</button></div><div class="compact-list">${filtered.length?filtered.map(taskCompactRow).join(''):`<div class="meta">${esc(t('none'))}</div>`}</div>`;}
function taskCompactRow(task){const evidence=task.evidence_summary||{};return `<button type="button" class="compact-row ${String(task.task_id)===String(state.taskId)?'active':''}" onclick="selectTask('${esc(task.task_id)}')"><span><strong>${esc(task.title||task.task||task.task_id)}</strong><br><span class="meta">${esc(task.provider||'-')} · ${esc(t('stage'))}=${esc(task.current_stage||'-')} · ${esc(t('workflow'))}=${esc(task.workflow||'-')}</span><br><span class="meta">${esc(t('tests'))} ${esc(evidence.tests||t('missing'))} · ${esc(t('review'))} ${esc(evidence.review||t('none'))} · ${esc(t('rollback'))} ${esc(evidence.rollback||t('missing'))}</span></span><span class="${statusClass(task.status)}">${esc(tStatus(task.status))}</span></button>`;}
function taskCard(task){const err=task.error_summary||{},evidence=task.evidence_summary||{};return `<div class="task-card ${task.hidden?'hidden':''}" tabindex="0" onclick="selectTask('${esc(task.task_id)}')"><strong>${esc(task.title||task.task||task.task_id)}</strong> <span class="${statusClass(task.status)}">${esc(tStatus(task.status))}</span>${task.hidden?`<span class="status">${esc(t('hide_task'))}</span>`:''}<div class="meta">${esc(task.provider||'-')} · ${esc(t('stage'))}=${esc(task.current_stage||'-')} · ${esc(t('workflow'))}=${esc(task.workflow||'-')}</div><div class="chips"><span>${esc(evidence.label||t('missing'))}</span><span>${esc(t('tests'))} ${esc(evidence.tests||t('missing'))}</span><span>${esc(t('review'))} ${esc(evidence.review||t('none'))}</span><span>${esc(t('rollback'))} ${esc(evidence.rollback||t('missing'))}</span></div>${err.message?`<div class="meta">${esc(t('blocked'))}: ${esc(err.message)}</div>`:''}<div class="actions"><button onclick="loadText('${esc(task.report_endpoint||'').replace('/api','')}','events');event.stopPropagation()">${esc(t('report'))}</button><button onclick="loadText('${esc(task.diff_endpoint||'').replace('/api','')}','events');event.stopPropagation()">${esc(t('diff'))}</button><button onclick="copyText(location.origin+'/review/${esc(task.task_id)}');event.stopPropagation()">${esc(t('share_review'))}</button><button onclick="hideTask(event,'${esc(task.task_id)}')">${esc(t('hide_task'))}</button></div></div>`;}
function filterSelect(key,tasks){const values=[...new Set(tasks.map(row=>filterValue(row,key)).filter(Boolean))].sort();return `<select title="${esc(colLabel(key))}" onchange="state.projectFilters.${key}=this.value;renderProjectTasks({workflows:[{role_groups:[{tasks:state.currentProjectTasks}]}]})"><option value="">${esc(colLabel(key))}: ${esc(t('all'))}</option>${values.map(v=>`<option value="${esc(v)}" ${state.projectFilters[key]===v?'selected':''}>${esc(v)}</option>`).join('')}</select>`;}
function filterValue(row,key){if(key==='cost'){const cost=Number(row.cost_usd||0);return cost>0.5?'0.50+':cost>0?'0-0.50':'0';}return String(row[key]||'');}
function matchesProjectFilters(row){return Object.entries(state.projectFilters).every(([key,value])=>!value||filterValue(row,key)===value);}
function resetProjectFilters(){state.projectFilters={provider:'',workflow:'',status:'',risk:'',cost:''};renderProjectTasks({workflows:[{role_groups:[{tasks:state.currentProjectTasks}]}]});}
function projectTaskColumn(row){if(row.status==='completed')return'done';if(['blocked','aborted','failed'].includes(row.status)||Number(row.errors||0)>0)return'failed';if(['awaiting_approval','awaiting_provider_action','paused_budget'].includes(row.status)||Number(row.pending_approvals||0)||Number(row.pending_provider_actions||0))return'waiting';if(row.status==='needs_review'||row.current_stage==='review')return'needs_review';if(['created','queued','pending'].includes(row.status))return'todo';return'running';}
function projectConfigHtml(config){return `<h2>${esc(t('project_config'))}</h2><div class="metrics"><div class="card"><h3>${esc(t('run_profile'))}</h3><strong>${esc(config.profile||'-')}</strong><div class="meta">gate=${esc(config.gate||'-')}</div></div><div class="card"><h3>${esc(t('approvals'))}</h3><strong>${esc(config.approvals?.pending||0)}</strong></div></div>${standardSection(state.standards.configuration,{title:true})}${tableBlock(colLabel('role'),Object.entries(config.roles||{}).map(([role,value])=>({role,value})),['role','value'])}${tableBlock(t('skills'),config.skills||[],['name','source','trust'])}`;}
function renderRuns(projects){const rows=allTasksFromProjects(projects);state.allRunRows=rows;document.getElementById('runs-list').innerHTML=rows.length?`<table><thead><tr>${['run_id','project_prefix','status','provider','workflow','stage','risk','cost'].map(c=>`<th>${esc(c==='project_prefix'?t('project_prefix'):colLabel(c))}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr onclick="selectTask('${esc(row.task_id)}')"><td>${esc(row.task_id||'-')}</td><td>${esc(row.project_name||'-')}</td><td><span class="${statusClass(row.status)}">${esc(tStatus(row.status))}</span></td><td>${esc(row.provider||'-')}</td><td>${esc(row.workflow||'-')}</td><td>${esc(row.current_stage||'-')}</td><td>${esc(row.risk||'-')}</td><td>$${esc(row.cost_usd||0)}</td></tr>`).join('')}</tbody></table>`:`<div class="meta">${esc(t('none'))}</div>`;}
function renderValidation(validation){const rows=validation.experiments||[],summary=validation.summary||{},standard=state.standards.validation||{};document.getElementById('validation-standards').innerHTML=standardSection(standard,{title:false});document.getElementById('validation-summary').innerHTML=`<button type="button" class="summary-card" onclick="openInspector('evidence',{detail:'validation_standards'})"><strong>${esc(tStatus(summary.status)||summary.status||'watch')}</strong><span class="meta">${esc(standardSummary(standard)||summary.summary||t('none'))}</span><span>${esc(t('view_details'))}</span></button>`;document.getElementById('validation-experiments').innerHTML=rows.length?tableBlock(t('validation_title'),rows,['experiment_id','suite','winner','strategies','report','updated_at']):`<div class="meta">${esc(t('none'))} <code>muxdev validate run validation/suites/example.yaml</code></div>`;}
function renderRecentEvidence(center){const rows=(center?.recent||[]).slice(0,4);const el=document.getElementById('recent-evidence');if(!el)return;el.innerHTML=rows.length?rows.map(row=>`<button type="button" class="compact-row" onclick="state.taskId='${esc(row.task_id||'')}';openInspector('evidence',{detail:'run'});loadSelectedTask().catch(showError)"><span><strong>${esc(row.task||row.task_id||'-')}</strong><br><span class="meta">${esc(row.report_endpoint||row.diff_endpoint||'-')}</span></span><span>${esc(t('view_details'))}</span></button>`).join(''):`<div class="meta">${esc(t('evidence_empty'))}</div>`;}
function renderArtifacts(center){document.getElementById('artifacts-center').innerHTML=tableBlock(t('artifacts_title'),center?.recent||[],['task_id','task','report_endpoint','diff_endpoint','tokens','cost_usd']);}
async function loadSelectedTask(center={}){if(!state.taskId){renderSelectedTask(null,center);return;}const detail=await optionalApi('/tasks/'+encodeURIComponent(state.taskId),null);state.selectedTaskDetail=detail;renderSelectedTask(detail,center);}
function renderSelectedTask(payload,center){if(!payload){state.selectedTaskHtml=`<div class="meta">${esc(t('no_task_selected'))}</div>`;state.selectedEvidenceHtml=`<div class="meta">${esc(t('evidence_empty'))}</div>`;renderInspector();return;}const stages=payload.stages||[],run=payload.run||{},ux=payload.ux||{},evaluation=payload.evidence_evaluation||{};const summary=`<div class="card"><h2>${esc(run.task||ux.headline||run.run_id||state.taskId)}</h2><div class="meta">${esc(ux.why||'')}</div><div class="chips"><span>${esc(tStatus(run.status))}</span><span>${esc(t('stage'))} ${esc(ux.current_stage||run.current_stage||'-')}</span><span>${esc(t('risk'))} ${esc(ux.risk||'-')}</span><span>${esc(t('delivery_confidence'))} ${Math.round(Number(evaluation.confidence||0)*100)}%</span></div></div>`;const timeline=stages.length?stages.map(s=>`<div class="timeline-step"><strong>${esc(s.stage_id)}</strong> <span class="${statusClass(s.status)}">${esc(tStatus(s.status))}</span><div>${esc(s.summary||'')}</div><div class="meta">role=${esc(s.role||'-')}</div></div>`).join(''):`<div class="meta">${esc(t('none'))}</div>`;state.selectedTaskHtml=`<h2>${esc(t('run_details'))}</h2>${summary}${timeline}`;state.selectedEvidenceHtml=tableBlock(t('report'),payload.artifacts||[],['name','kind','stage_id','path','created_at'])+tableBlock(t('tests'),payload.test_results||[],['stage_id','passed','command','summary'])+tableBlock(t('review'),payload.review_blockers||[],['stage_id','type','severity','file','line','suggestion'])+tableBlock('Evidence v2',payload.evidence_evaluations||[],['run_id','label','confidence','path'])+tableBlock('Semantic Merge',payload.semantic_merge_reviews||[],['review_id','decision','patch_hash','findings','path']);renderInspector();}
function problemCard(row){return `<div class="action-card"><strong>${esc(tAction(row.kind))}</strong><div class="meta">${esc(row.why||row.reason||row.prompt_text||row.task_title||row.run_id||'')}</div><div class="actions">${row.approval_id?`<button class="primary" onclick="post('/approvals/${encodeURIComponent(row.approval_id)}/approve')">${esc(t('approve'))}</button><button onclick="post('/approvals/${encodeURIComponent(row.approval_id)}/deny')">${esc(t('deny'))}</button>`:''}${row.action_id?`<button onclick="copyText('${esc(row.attach_command||'')}')">${esc(t('copy_attach'))}</button><button class="primary" onclick="post('/tasks/${encodeURIComponent(row.run_id)}/actions/${encodeURIComponent(row.action_id)}/handled-and-continue')">${esc(t('handled_continue'))}</button>`:''}${row.endpoint?`<button class="primary" onclick="post('${esc(row.endpoint).replace('/api','')}')">${esc(t('handled_continue'))}</button>`:''}</div></div>`;}
function renderProviderActions(rows){return rows.length?rows.map(row=>{const choices=row.choices||row.options||[];const field='action-response-'+String(row.action_id||'').replace(/[^a-zA-Z0-9_-]/g,'-');const choiceButtons=choices.map(choice=>{const payload=encodeURIComponent(JSON.stringify({choice:String(choice.value??choice.label??'')}));return `<button onclick="respondProviderAction('${esc(row.run_id)}','${esc(row.action_id)}','${esc(payload)}')">${esc(choice.label??choice.value)}</button>`;}).join('');const textBox=(row.input_kind==='text'||!choices.length&&row.input_kind!=='external'&&row.input_kind!=='confirmation')?`<textarea id="${esc(field)}" rows="2" placeholder="${esc(t('response_placeholder'))}"></textarea><button onclick="respondProviderActionText('${esc(row.run_id)}','${esc(row.action_id)}','${esc(field)}')">${esc(t('submit_response'))}</button>`:'';const label=row.kind==='clarification_required'?'clarification':'provider_action';return `<div class="action-card"><h3>${esc(tAction(label))}</h3><strong>${esc(row.provider||'provider')} / ${esc(row.input_kind||row.kind||'action')}</strong><p>${esc(row.prompt_text||'')}</p><div class="actions">${choiceButtons}${textBox}<button onclick="copyText('${esc(row.attach_command||'')}')">${esc(t('copy_attach'))}</button><button class="primary" onclick="post('/tasks/${encodeURIComponent(row.run_id)}/actions/${encodeURIComponent(row.action_id)}/handled-and-continue')">${esc(t('handled_continue'))}</button></div></div>`;}).join(''):'';}
function renderApprovals(rows){return rows.length?rows.map(row=>`<div class="action-card"><h3>${esc(tAction('approval'))}</h3><strong>${esc(row.type||'policy gate')}</strong><div>${esc(row.reason||'')}</div><div class="meta">subject=${esc(row.subject_hash||'-')}</div><div class="actions"><button class="primary" onclick="post('/approvals/${encodeURIComponent(row.approval_id)}/approve')">${esc(t('approve'))}</button><button onclick="post('/approvals/${encodeURIComponent(row.approval_id)}/deny')">${esc(t('deny'))}</button></div></div>`).join(''):'';}
async function loadGlobalConfig(){const overview=await api('/dashboard/overview?include_global_config=true');state.standards=overview.standards||state.standards||{};renderGlobalConfig(overview.global_config||{},overview.governance_summary||{});state.globalConfigLoaded=true;}
function renderGlobalConfig(config,governance={}){renderSystemProviders(config.providers||{});renderSystemPolicy(config.budget||{},config.safety||{});renderMcpSummary(config.mcp||{});renderMemoryGovernance(governance||{});renderAdvancedControl(governance||{});document.getElementById('role-templates').innerHTML=tableBlock(t('role_sessions'),config.role_templates||[],['name','workflow','roles','providers']);renderWorkflowTemplates(config.workflow_templates||{});const skills=config.skills_catalog||{};document.getElementById('skills-catalog').innerHTML=tableBlock(t('skills'),(skills.catalog||{}).skills||[],['name','trust','risk_level','source','description'])+tableBlock('Skill Lock',(skills.lock||{}).skills||[],['name','status']);renderSystemCatalog();if(state.activeSummary==='system')renderInspector();}
function renderSystemCatalog(){const rows=[['trusted_standards',t('trusted_delivery_standards'),'standards'],['validation_standards',t('validation_standards'),'standards'],['governance_standards',t('governance_standards'),'standards'],['config_standards',t('configuration_standards'),'standards'],['providers',t('providers'),'system-providers'],['policy',t('policy_budget_git'),'system-policy'],['mcp','MCP','mcp-summary'],['roles',t('role_sessions'),'role-templates'],['workflows',t('workflow_templates'),'workflow-templates'],['skills',t('skills'),'skills-catalog'],['memory',t('memory_context'),'memory-governance'],['advanced',t('advanced_control'),'advanced-control']];document.getElementById('system-catalog').innerHTML=rows.map(([id,label,source])=>{const standard={trusted_standards:state.standards.trusted_delivery,validation_standards:state.standards.validation,governance_standards:state.standards.governance,config_standards:state.standards.configuration}[id];const hasContent=source==='standards'?standardSummary(standard):(document.getElementById(source)?.textContent||'').trim();return `<button type="button" class="compact-row ${state.selectedSystemSection===id?'active':''}" onclick="openInspector('evidence',{system:'${id}'})"><span><strong>${esc(label)}</strong><br><span class="meta">${hasContent?esc(hasContent):esc(t('inspector_empty'))}</span></span><span>${esc(t('selected_detail'))}</span></button>`;}).join('');}
function renderSystemProviders(providers){const rows=[['ready',(providers.ready||[]).join(', ')||'-'],['partial',(providers.partial||[]).join(', ')||'-'],['unavailable',(providers.unavailable||[]).join(', ')||'-'],['known',providers.total??0]];document.getElementById('system-providers').innerHTML=`<div class="metrics">${rows.map(([a,b])=>`<div class="card metric"><h3>${esc(a)}</h3><strong>${esc(b)}</strong></div>`).join('')}</div>${(providers.recommendations||[]).length?`<ul>${providers.recommendations.map(r=>`<li>${esc(r)}</li>`).join('')}</ul>`:''}`;}
function renderSystemPolicy(budget,safety){const git=safety.git||{};const rows=[['profile',safety.profile||'-'],['gate',safety.gate||'-'],[t('cost'),`$${Number(budget.total_cost_usd||0).toFixed(4)}`],['git',git.status||'-'],['branch',git.branch||'-']];document.getElementById('system-policy').innerHTML=`<div class="metrics">${rows.map(([a,b])=>`<div class="card metric"><h3>${esc(a)}</h3><strong>${esc(b)}</strong></div>`).join('')}</div><div class="actions"><button onclick="copyText('muxdev config')">${esc(t('copy'))} config</button><button onclick="copyText('muxdev provider doctor')">${esc(t('copy_provider_doctor'))}</button></div>`;}
function renderMcpSummary(mcp){const rows=[['status',mcp.status||'enabled'],['mode',mcp.mode||'local stdio'],[t('tools'),mcp.tools_count??0],['resources',mcp.resources_count??0],['prompts',mcp.prompts_count??0],['write_policy',mcp.write_policy||'guarded']];document.getElementById('mcp-summary').innerHTML=`<div class="metrics">${rows.map(([a,b])=>`<div class="card metric"><h3>${esc(a)}</h3><strong>${esc(b)}</strong></div>`).join('')}</div>${tableBlock('MCP Guardrails',mcp.recent_guardrails||[],['tool','decision','reason','created_at'])}`;}
function renderWorkflowTemplates(templates){const rows=templates.templates||[];const cards=rows.length?`<h3>${esc(t('workflow_template_preview'))}</h3><div class="governance-grid">${rows.map(row=>{const phases=Array.isArray(row.phases)?row.phases:String(row.phases||'').split(',').filter(Boolean);return `<div class="card"><strong>${esc(row.name||'-')}</strong><div class="meta">${esc(row.description||'')}</div><div class="stage-rail">${phases.slice(0,6).map(phase=>`<span class="stage-pill">${esc(phase)}</span>`).join('')||'<span class="stage-pill">default</span>'}</div></div>`;}).join('')}</div>`:'';document.getElementById('workflow-templates').innerHTML=cards+tableBlock(t('workflow_templates'),rows,['name','description','phases','supported_providers']);}
function renderMemoryGovernance(governance){const memory=governance.memory||{};document.getElementById('memory-governance').innerHTML=`<div class="card ${esc(memory.status||'watch')}"><strong>${esc(memory.status||'watch')}</strong><div class="meta">${esc(memory.summary||t('none'))}</div></div>${tableBlock(t('memory_context'),Object.entries(memory.counts||{}).map(([name,value])=>({name,value})),['name','value'])}`;}
function renderAdvancedControl(governance){const trend=(governance.provider_learning?.trend)||[];const parallel=(governance.parallel_control?.items)||[];const repos=(governance.multi_repo?.items)||[];document.getElementById('advanced-control').innerHTML=`<h3>${esc(t('provider_learning_trend'))}</h3>${trend.length?`<div class="sparkline">${trend.map(row=>{const pct=Math.max(0,Math.min(100,Math.round(Number(row.score||0)*100)));return `<div class="spark-row"><span>${esc(row.provider||'-')}/${esc(row.role||'any')}</span><div class="spark-bar"><span style="width:${pct}%"></span></div><strong>${pct}%</strong></div>`;}).join('')}</div>`:`<div class="meta">${esc(t('none'))}</div>`}<h3>${esc(t('parallel_conflict_map'))}</h3>${tableBlock(t('parallel_conflict_map'),parallel,['id','kind','severity','summary'])}<h3>${esc(t('multi_repo_map'))}</h3>${tableBlock(t('multi_repo_map'),repos,['id','mode','status','summary'])}`;}
function tableBlock(title,rows,cols){rows=rows||[];if(!rows.length)return `<h3>${esc(title)}</h3><div class="meta">${esc(t('none'))}</div>`;return `<h3>${esc(title)}</h3><table><thead><tr>${cols.map(c=>`<th>${esc(colLabel(c))}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${cols.map(c=>tableCell(row,c)).join('')}</tr>`).join('')}</tbody></table>`;}
function tableCell(row,c){const value=c==='path'&&row.path_display?row.path_display:row[c];const title=c==='path'&&row.path_title?` title="${esc(row.path_title)}"`:'';return `<td${title}>${esc(fmt(value))}</td>`;}
async function copyLowRiskReviewLinks(){const links=(state.currentProjectTasks||[]).filter(matchesProjectFilters).filter(row=>(row.risk||'low')==='low').map(row=>`${location.origin}/review/${encodeURIComponent(row.task_id)}`);await copyText(links.join(String.fromCharCode(10)));document.getElementById('daemon-status').innerHTML=`<span class="status-dot"></span>${esc(t('copied'))} ${links.length}`;}
async function hideCompletedLowRisk(){const rows=(state.currentProjectTasks||[]).filter(matchesProjectFilters).filter(row=>(row.risk||'low')==='low'&&row.status==='completed');if(!rows.length){document.getElementById('daemon-status').innerHTML=`<span class="status-dot"></span>${esc(t('no_completed_low_risk'))}`;return;}if(!confirm(t('confirm_hide_completed')))return;for(const row of rows)await api('/dashboard/tasks/'+encodeURIComponent(row.task_id),{method:'DELETE'});await refresh();}
async function respondProviderAction(runId,actionId,encoded){const response=JSON.parse(decodeURIComponent(encoded));await api('/tasks/'+encodeURIComponent(runId)+'/actions/'+encodeURIComponent(actionId)+'/respond-and-continue',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({response})});await refresh();}
async function respondProviderActionText(runId,actionId,fieldId){const text=(document.getElementById(fieldId)?.value||'').trim();if(!text){showError(t('task_empty'));return;}await api('/tasks/'+encodeURIComponent(runId)+'/actions/'+encodeURIComponent(actionId)+'/respond-and-continue',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({response:{text}})});await refresh();}
async function post(path){await api(path,{method:'POST'});await refresh();}
async function hideProject(event,id){event.stopPropagation();if(!confirm(t('confirm_hide_project')))return;await api('/dashboard/projects/'+encodeURIComponent(id),{method:'DELETE'});if(state.selectedProjectId===id){state.selectedProjectId=null;state.taskId=null;}await refresh();}
async function hideTask(event,id){event.stopPropagation();if(!confirm(t('confirm_hide_task')))return;await api('/dashboard/tasks/'+encodeURIComponent(id),{method:'DELETE'});if(state.taskId===id)state.taskId=null;await refresh();}
async function loadText(path,target){if(!path)return;const payload=await api(path);state.outputText=payload[target]||payload.content||payload.diff||JSON.stringify(payload,null,2);openInspector('output');}
async function copyText(value){if(navigator.clipboard&&value)await navigator.clipboard.writeText(value);}
function buildCommandPalette(projects,actions){const commands=[{label:t('open_overview'),hint:t('dashboard_home'),run:()=>setActivity('overview')},{label:t('open_projects'),hint:t('projects_hint'),run:()=>setActivity('projects')},{label:t('open_runs'),hint:t('runs_hint'),run:()=>setActivity('runs')},{label:t('open_validation'),hint:t('validation_hint'),run:()=>setActivity('validation')},{label:t('open_artifacts'),hint:t('artifacts_hint'),run:()=>setActivity('artifacts')},{label:t('open_system'),hint:t('system_hint'),run:()=>setActivity('system')},{label:t('copy_provider_doctor'),hint:t('provider_doctor_hint'),run:()=>copyText('muxdev provider doctor')}];(projects||[]).forEach(project=>commands.push({label:`${t('project_prefix')}: ${project.name||project.path}`,hint:project.path||'',run:()=>{state.selectedProjectId=project.id;state.activity='projects';closeCommandPalette();refresh();}}));state.allRunRows.forEach(row=>commands.push({label:`${t('run_prefix')}: ${row.task||row.task_id}`,hint:row.project_name||row.status||'',run:()=>{state.taskId=row.task_id;state.activity='runs';state.inspectorTab='evidence';state.activeSummary='run';closeCommandPalette();refresh();}}));(actions||[]).forEach(action=>commands.push({label:`${t('action_prefix')}: ${tAction(action.kind)}`,hint:action.task_title||action.run_id||'',run:()=>{state.taskId=action.run_id||action.task_id||state.taskId;state.activity='runs';state.inspectorTab='problems';closeCommandPalette();refresh();}}));state.commands=commands;renderCommandPalette();}
function openCommandPalette(){state.paletteOpen=true;document.getElementById('command-palette').classList.add('open');document.getElementById('command-search')?.focus();renderCommandPalette();}
function closeCommandPalette(event){if(event&&event.target&&event.target.id!=='command-palette')return;state.paletteOpen=false;document.getElementById('command-palette').classList.remove('open');}
function renderCommandPalette(){const q=(document.getElementById('command-search')?.value||'').toLowerCase();const rows=(state.commands||[]).filter(row=>(row.label+' '+row.hint).toLowerCase().includes(q)).slice(0,10);state.visibleCommands=rows;document.getElementById('command-results').innerHTML=rows.length?rows.map((row,index)=>`<button type="button" class="palette-command" onclick="runPaletteCommand(${index})"><span><strong>${esc(row.label)}</strong><br><span class="meta">${esc(row.hint||'')}</span></span><span>${esc(t('command_run'))}</span></button>`).join(''):`<div class="meta">${esc(t('command_empty'))}</div>`;}
function runPaletteCommand(index){const row=(state.visibleCommands||[])[index];if(row&&row.run)row.run();closeCommandPalette();}
function connectEvents(){try{const socket=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/events`);socket.onmessage=event=>{state.events.push(event.data);state.events=state.events.slice(-40);state.outputText=state.events.join(String.fromCharCode(10));if(state.inspectorTab==='output')renderInspector();refresh().catch(()=>{});};socket.onclose=()=>setTimeout(connectEvents,2000);}catch(_e){}}
document.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'){event.preventDefault();openCommandPalette();}else if(event.key==='Escape'&&state.paletteOpen){event.preventDefault();closeCommandPalette();}});
syncStoredLanguage();initChrome();refresh().catch(showError);connectEvents();setInterval(()=>refresh().catch(()=>{}),5000);
</script>
</body>
</html>"""
