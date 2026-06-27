"""Minimal daemon-backed dashboard renderer."""

from __future__ import annotations

import html
import json


_DASHBOARD_LANGS = {"zh-CN", "en"}


def normalize_dashboard_lang(lang: str | None) -> str:
    """Return the supported dashboard language, defaulting to Chinese."""
    normalized = (lang or "").strip()
    if normalized in _DASHBOARD_LANGS:
        return normalized
    lowered = normalized.lower()
    if lowered in {"zh", "zh-cn", "cn", "chinese"}:
        return "zh-CN"
    if lowered in {"en", "en-us", "english"}:
        return "en"
    return "zh-CN"


def render_minimal_dashboard_html(task_id: str | None = None, lang: str | None = None) -> str:
    """Render the simplified live dashboard."""
    selected_lang = normalize_dashboard_lang(lang)
    text = _I18N[selected_lang]
    body_attr = f'data-task-id="{html.escape(task_id, quote=True)}"' if task_id else ""
    return (
        _TEMPLATE.replace("__HTML_LANG__", selected_lang)
        .replace("__BODY_ATTR__", body_attr)
        .replace("__TEXT__", json.dumps(text, ensure_ascii=False, separators=(",", ":")))
        .replace("__TITLE__", html.escape(str(text["title"]), quote=False))
        .replace("__APP_NAME__", html.escape(str(text["app_name"]), quote=False))
        .replace("__SUBTITLE__", html.escape(str(text["subtitle"]), quote=False))
    )


_I18N: dict[str, dict[str, object]] = {
    "zh-CN": {
        "title": "muxdev 控制台",
        "app_name": "muxdev 控制台",
        "subtitle": "最小运行面板：总览、项目、配置，以及所有需要人工处理的事项。",
        "overview": "总览",
        "projects": "项目",
        "config": "配置",
        "refresh": "刷新",
        "command_palette": "命令面板",
        "loading": "正在连接 muxdev daemon...",
        "daemon_offline": "daemon 暂不可用",
        "ready_cli": "就绪 CLI 工具",
        "supported_workflows": "支持的工作流",
        "running_tasks": "工作中的任务",
        "needs_my_action": "需要我处理",
        "projects_with_runs": "包含执行的项目数",
        "manual_breakdown": "人工处理分类",
        "clarification": "需求澄清",
        "approval": "人工审批",
        "plan_approval": "计划审批",
        "plan_feedback": "计划反馈修改",
        "optional_feedback": "补充反馈",
        "provider_permission": "CLI/provider 权限确认",
        "failure_gate": "失败门禁确认",
        "ready": "就绪",
        "partial": "部分可用",
        "unavailable": "不可用",
        "none": "暂无",
        "all_projects": "全部项目",
        "status_running": "执行中",
        "status_action": "需要我处理",
        "status_blocked": "阻塞",
        "status_done": "已完成",
        "status_stopped": "已停止/失败",
        "status_pending": "待启动",
        "workflow": "工作流",
        "stage": "阶段",
        "provider": "CLI/provider",
        "role": "角色",
        "stop": "停止",
        "view_cli": "查看模型执行",
        "copy_attach": "复制 attach/transcript",
        "approve": "通过",
        "deny": "拒绝",
        "feedback": "提交反馈",
        "feedback_placeholder": "输入对方案的反馈，提交后会进入 revise/review 循环",
        "optional_feedback_placeholder": "输入补充偏好或约束，不会打断正在执行的 agent",
        "allow_continue": "允许并继续",
        "submit_continue": "提交并继续",
        "dismiss": "拒绝/忽略",
        "retry": "重试/继续",
        "rollback": "回滚",
        "copied": "已复制",
        "workflow_templates": "流程模板",
        "role_cli_mapping": "角色与 CLI/provider",
        "human_gates": "人审点",
        "phases": "阶段",
        "profiles": "档案",
        "provider_ready": "可用状态",
        "terminal_link": "本地浏览器链接",
        "empty_actions": "暂无需要人工处理的任务。",
        "empty_tasks": "暂无任务。",
        "empty_config": "暂无配置数据。",
        "show_full": "展开全文",
        "show_short": "收起全文",
        "last_refresh": "最近刷新",
        "delivery_gates": "交付门禁",
        "model_roles": "模型角色",
        "best_for": "适用场景",
        "workflow_stage_flow": "阶段流转",
        "configured_provider": "配置的提供方",
        "fallback_provider": "备用提供方",
        "effective_provider": "实际使用方",
        "setup_hint": "配置建议",
        "doctor_hint": "诊断建议",
        "readiness": "就绪状态",
        "show_more": "展开剩余 {count} 项",
        "show_less": "收起",
        "processing": "处理中...",
        "submitted": "已提交，正在刷新",
        "action_failed": "操作失败",
        "config_loading": "正在加载配置...",
        "response_placeholder": "输入回复内容，点击提交并继续后会交给工作流",
        "response_required": "请输入回复内容",
        "feedback_required": "请输入反馈内容",
        "refresh_deferred": "检测到更新，正在输入，已暂缓刷新",
    },
    "en": {
        "title": "muxdev Dashboard",
        "app_name": "muxdev Dashboard",
        "subtitle": "Minimal operations surface: overview, projects, config, and every item that needs human handling.",
        "overview": "Overview",
        "projects": "Projects",
        "config": "Config",
        "refresh": "Refresh",
        "command_palette": "Command Palette",
        "loading": "Connecting to muxdev daemon...",
        "daemon_offline": "Daemon unavailable",
        "ready_cli": "Ready CLI Tools",
        "supported_workflows": "Supported Workflows",
        "running_tasks": "Running Tasks",
        "needs_my_action": "Needs My Action",
        "projects_with_runs": "Projects With Runs",
        "manual_breakdown": "Human Handling",
        "clarification": "Clarification",
        "approval": "Approval",
        "plan_approval": "Plan Approval",
        "plan_feedback": "Plan Feedback",
        "optional_feedback": "Add feedback",
        "provider_permission": "CLI/provider Permission",
        "failure_gate": "Failure Gate",
        "ready": "Ready",
        "partial": "Partial",
        "unavailable": "Unavailable",
        "none": "None",
        "all_projects": "All projects",
        "status_running": "Running",
        "status_action": "Needs my action",
        "status_blocked": "Blocked",
        "status_done": "Completed",
        "status_stopped": "Stopped/failed",
        "status_pending": "Pending",
        "workflow": "Workflow",
        "stage": "Stage",
        "provider": "CLI/provider",
        "role": "Role",
        "stop": "Stop",
        "view_cli": "View model run",
        "copy_attach": "Copy attach/transcript",
        "approve": "Approve",
        "deny": "Deny",
        "feedback": "Send feedback",
        "feedback_placeholder": "Enter plan feedback; muxdev will revise and review again",
        "optional_feedback_placeholder": "Add preferences or constraints without interrupting the running agent",
        "allow_continue": "Allow and continue",
        "submit_continue": "Submit and continue",
        "dismiss": "Deny/dismiss",
        "retry": "Retry/continue",
        "rollback": "Rollback",
        "copied": "Copied",
        "workflow_templates": "Workflow Templates",
        "role_cli_mapping": "Model Role / CLI Routing",
        "human_gates": "Human Gates",
        "delivery_gates": "Delivery Gates",
        "model_roles": "Model Roles",
        "best_for": "Best For",
        "workflow_stage_flow": "Stage Flow",
        "configured_provider": "Configured Provider",
        "fallback_provider": "Fallback Provider",
        "effective_provider": "Effective Provider",
        "setup_hint": "Setup Hint",
        "doctor_hint": "Doctor Hint",
        "readiness": "Readiness",
        "show_more": "Show remaining {count}",
        "show_less": "Collapse",
        "show_full": "Show full text",
        "show_short": "Collapse text",
        "phases": "Phases",
        "profiles": "Profiles",
        "provider_ready": "Provider readiness",
        "terminal_link": "Local browser link",
        "empty_actions": "No human handling is needed.",
        "empty_tasks": "No tasks yet.",
        "empty_config": "No config data.",
        "last_refresh": "Last refresh",
        "processing": "Working...",
        "submitted": "Submitted, refreshing",
        "action_failed": "Action failed",
        "config_loading": "Loading config...",
        "response_placeholder": "Enter a response; submit and continue will pass it to the workflow",
        "response_required": "Enter a response first",
        "feedback_required": "Enter feedback first",
        "refresh_deferred": "Update detected; refresh deferred while you are typing",
    },
}


_TEMPLATE = """<!doctype html>
<html lang="__HTML_LANG__">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__TITLE__</title>
  <style>
    :root{color-scheme:light;--bg:#f5f6f8;--panel:#fff;--ink:#1f2937;--muted:#667085;--line:#d9dee8;--soft:#eef2f6;--accent:#0f766e;--warn:#a15c07;--bad:#b42318;--good:#16703c;--info:#3151a3}
    *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
    header{position:sticky;top:0;z-index:10;background:rgba(255,255,255,.96);border-bottom:1px solid var(--line);padding:14px 22px 10px}
    .topline,.toolbar,.title-row,.actions{display:flex;gap:12px;align-items:flex-start}.topline,.toolbar,.title-row{justify-content:space-between}.actions{flex-wrap:wrap;align-items:center}
    h1{margin:0;font-size:20px;letter-spacing:0}h2{margin:0 0 10px;font-size:16px;letter-spacing:0}h3{margin:0 0 8px;font-size:13px;letter-spacing:0;color:var(--muted)}
    .subtitle,.meta{color:var(--muted);overflow-wrap:anywhere}.meta{font-size:12px}
    nav{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}button,.button{border:1px solid var(--line);background:#fff;color:var(--ink);min-height:32px;padding:6px 10px;border-radius:6px;font:inherit;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;justify-content:center;gap:6px}
    button:hover,.button:hover{border-color:#aab4c3}button.primary,.button.primary,nav button.active{background:var(--accent);color:#fff;border-color:var(--accent)}button.danger{color:var(--bad);border-color:#f2b8b5;background:#fff7f7}button.ghost{background:transparent}
    main{padding:18px 22px 34px}.view{display:none}.view.active{display:grid;gap:14px}.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px;min-width:0}
    .grid{display:grid;gap:12px}.metrics{grid-template-columns:repeat(5,minmax(0,1fr))}.columns-2{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}
    .metric,.task-card,.action-card{border:1px solid var(--line);border-radius:8px;background:#fff;padding:12px;min-width:0}.metric{min-height:82px}.metric .label{color:var(--muted);font-size:12px}.metric strong{display:block;margin-top:4px;font-size:24px;line-height:1.1}
    .chips{display:flex;gap:6px;flex-wrap:wrap}.chip{display:inline-flex;align-items:center;gap:4px;border:1px solid var(--line);border-radius:999px;background:#fff;padding:2px 8px;min-height:24px;color:var(--muted);font-size:12px;max-width:100%}
    .chip.good{color:var(--good);border-color:#b7dfc6;background:#f2fbf5}.chip.warn{color:var(--warn);border-color:#f4cf9a;background:#fff8ec}.chip.bad{color:var(--bad);border-color:#f2b8b5;background:#fff7f7}.chip.info{color:var(--info);border-color:#c6d3f8;background:#f5f7ff}
    .task-card,.action-card{display:grid;gap:8px}.task-card+.task-card,.action-card+.action-card{margin-top:10px}.title-row strong{overflow-wrap:anywhere}.optional-feedback{border:1px dashed var(--line);border-radius:8px;background:#fbfcff;padding:10px;display:grid;gap:8px}.optional-feedback textarea{min-height:58px}
    .task-meta{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;color:var(--muted);font-size:12px}.task-meta div{overflow-wrap:anywhere}
    textarea,input,select{width:100%;border:1px solid var(--line);border-radius:6px;padding:8px;font:inherit;min-height:34px;background:#fff;color:var(--ink)}textarea{min-height:74px;resize:vertical}
    table{width:100%;border-collapse:collapse;table-layout:fixed}th,td{border-bottom:1px solid #edf0f5;padding:8px 6px;text-align:left;vertical-align:top;overflow-wrap:anywhere}th{color:var(--muted);font-size:12px;font-weight:700}tr:last-child td{border-bottom:0}
    code{background:var(--soft);border:1px solid var(--line);border-radius:5px;padding:1px 5px;overflow-wrap:anywhere}.project-list{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}.project-list button.active{background:#213547;border-color:#213547;color:#fff}
    .status-section h3{display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line);padding-bottom:6px}.empty{border:1px dashed var(--line);border-radius:8px;padding:18px;color:var(--muted);background:#fff}
    .stack{display:grid;gap:10px}.fold-toggle{margin-top:10px}.workflow-card,.route-card{border:1px solid var(--line);border-radius:8px;background:#fff;padding:12px;display:grid;gap:8px}.stage-rail{display:flex;gap:6px;flex-wrap:wrap}.stage-pill{border:1px solid var(--line);border-radius:999px;padding:2px 8px;font-size:12px;background:var(--soft);color:var(--muted)}.stage-pill.model{color:var(--info);border-color:#c6d3f8;background:#f5f7ff}.stage-pill.human{color:var(--warn);border-color:#f4cf9a;background:#fff8ec}.stage-pill.delivery{color:var(--good);border-color:#b7dfc6;background:#f2fbf5}.ability{font-size:12px;color:var(--muted)}.clamp{white-space:pre-wrap;overflow-wrap:anywhere}.clamp.is-clamped{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;max-height:4.35em}.clamp-toggle{justify-self:start;margin-top:-2px;padding:2px 6px;min-height:24px;font-size:12px}
    @media(max-width:1100px){.metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.columns-2{grid-template-columns:1fr}.task-meta{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:700px){header,main{padding-left:12px;padding-right:12px}.metrics{grid-template-columns:1fr}.task-meta{grid-template-columns:1fr}.topline{display:grid}}
  </style>
</head>
<body __BODY_ATTR__>
  <header>
    <div class="topline"><div><h1>__APP_NAME__</h1><div class="subtitle">__SUBTITLE__</div></div><div class="actions"><span class="meta" id="refresh-state">__TITLE__</span><button type="button" class="ghost" id="command-palette-button"></button><button type="button" class="ghost" id="refresh-button"></button></div></div>
    <nav aria-label="dashboard views"><button type="button" class="active" data-view-button="overview"></button><button type="button" data-view-button="projects"></button><button type="button" data-view-button="config"></button></nav>
  </header>
  <main>
    <section id="view-overview" class="view active">
      <div class="grid metrics" id="metrics"></div>
      <div class="grid columns-2"><section class="panel"><div class="toolbar"><h2 id="needs-title"></h2><div class="chips" id="manual-breakdown"></div></div><div id="action-list"></div></section><section class="panel"><h2 id="running-title"></h2><div id="running-list"></div></section></div>
      <div class="grid columns-2"><section class="panel"><h2 id="cli-title"></h2><div class="chips" id="cli-list"></div></section><section class="panel"><h2 id="workflow-title"></h2><div class="chips" id="workflow-list"></div></section></div>
    </section>
    <section id="view-projects" class="view"><section class="panel"><div class="toolbar"><h2 id="projects-title"></h2><div class="project-list" id="project-list"></div></div><div id="project-board"></div></section></section>
    <section id="view-config" class="view"><div class="grid columns-2"><section class="panel"><h2 id="workflow-templates-title"></h2><div id="workflow-templates"></div></section><section class="panel"><h2 id="role-cli-title"></h2><div id="role-cli"></div></section></div></section>
  </main>
  <script>
const TEXT=__TEXT__;const state={overview:null,selectedProjectId:null,focusTaskId:document.body.dataset.taskId||'',view:'overview',expanded:{},drafts:{},lastInputAt:0,pendingRefresh:false,globalConfig:null,globalConfigLoaded:false,globalConfigLoading:false};
function t(k){return TEXT[k]||k}function esc(v){return String(v??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]))}
function isZh(){return document.documentElement.lang==='zh-CN'}
const ZH={roles:{requirements:'需求分析',plan:'计划',architect:'架构',code:'编码',implementer:'实现',test_strategy:'测试策略',test:'测试',tester:'测试',review:'评审',reviewer:'评审',secure:'安全',docs:'文档',memory_curator:'记忆整理'},abilities:{requirements:'澄清范围、约束和验收标准。',plan:'把需求转化为实施步骤和风险控制。',architect:'设计架构、接口和系统取舍。',code:'实现变更并修复阻塞问题。',implementer:'实现变更并修复阻塞问题。',test_strategy:'选择验证策略和覆盖重点。',test:'运行聚焦检查并报告证据。',tester:'运行聚焦检查并报告证据。',review:'发现回归、阻塞问题和缺失测试。',reviewer:'发现回归、阻塞问题和缺失测试。',secure:'审查安全、认证、密钥和策略敏感变更。',docs:'更新文档、发布说明和交接摘要。',memory_curator:'从已评审证据中整理长期项目记忆。'},workflows:{void:'直接会话',muxdev:'muxdev 标准流程','spec-lite':'轻量规格流程','review-only':'仅评审流程',design:'设计流程','dev-lite':'轻量开发流程'},workflowDescriptions:{void:'直接 provider 会话，不注入规格驱动命令。',muxdev:'默认的 muxdev 设计、审批、实现、测试、评审和修复流程。','spec-lite':'适合先产出规划资料再实施的小型规格驱动流程。','review-only':'供并行评审智能体使用的只读评审流程。'},phases:{running:'执行',design:'设计',implement:'实现',implementation:'实现',test:'测试',testing:'测试',review:'评审',fix:'修复',research:'调研',planning:'规划',plan:'计划',approve_plan:'计划审批',requirements:'需求',architecture_options:'架构选项',decision_record:'决策记录',system_design:'系统设计',api_and_data_model:'接口与数据模型',risk_and_threat_model:'风险与威胁模型'},readiness:{ready:'就绪',partial:'部分可用',unavailable:'不可用',unknown:'未知',auto:'自动',true:'是',false:'否'},bestFor:{'Direct provider sessions where the human already knows the exact next step.':'人已经明确下一步操作时的直接 provider 会话。','Low ceremony exploration that still keeps muxdev safety and evidence boundaries.':'保留 muxdev 安全和证据边界的低流程探索。','Standard implementation work that benefits from plan, approval, test, review, and fix loops.':'需要计划、审批、测试、评审和修复循环的标准实现工作。','Risk-aware delivery where evidence and blocker handling should be explicit.':'需要明确证据和阻塞处理的风险感知交付。','Small to medium tasks where a concise spec should exist before code changes.':'代码变更前需要简明规格的小中型任务。','Repository changes that need research notes, acceptance criteria, and a final review trail.':'需要调研记录、验收标准和最终评审轨迹的仓库变更。','Independent read-only review of an existing change or plan.':'对已有变更或计划做独立只读评审。','Parallel reviewer fan-out where no agent should write to the workspace.':'并行评审扩展场景，所有智能体都不应写入工作区。'},actorKinds:{human_gate:'人工门禁',delivery_gate:'交付门禁',model_role:'模型角色'}};
function zhLookup(map,value){const key=String(value??'');return isZh()?(map[key]||key):key}
function roleLabel(value){return zhLookup(ZH.roles,value)}
function abilityLabel(role,fallback){return isZh()?(ZH.abilities[String(role||'')]||fallback||''):String(fallback||'')}
function workflowLabel(value){return zhLookup(ZH.workflows,value)}
function workflowDescription(row){const name=String(row?.name||row?.id||'');return isZh()?(ZH.workflowDescriptions[name]||row?.description||row?.notes||''):String(row?.description||row?.notes||'')}
const EXTRA_ZH={phases:{problem_statement:'问题陈述',design_brief:'设计简报',approve_plan:'计划审批',approval:'审批',recovery:'恢复处理',provider:'提供方',provider_action:'提供方操作',provider_action_reconcile:'提供方操作确认',approval_reconcile:'审批确认',plan_feedback:'计划反馈',clarification_required:'需要澄清',read_only_write_violation:'只读写入违规',code:'编码',review:'评审'},statuses:{awaiting_approval:'等待审批',awaiting_provider_action:'等待 CLI/provider 处理',paused_budget:'预算暂停',running:'执行中',completed:'已完成',aborted:'已停止',blocked:'阻塞',failed:'失败',created:'已创建',queued:'排队中',pending:'待启动',action:'需要处理',stopped:'已停止',done:'已完成'},choices:{Yes:'是',No:'否',yes:'是',no:'否',true:'是',false:'否'}};
function phaseLabel(value){const key=String(value??'');if(!isZh())return key;return EXTRA_ZH.phases[key]||ZH.phases[key]||key}
function statusLabel(value){const key=String(value??'');if(!isZh())return key;return EXTRA_ZH.statuses[key]||phaseLabel(key)}
function choiceLabel(value){const key=String(value??'');if(!isZh())return key;return EXTRA_ZH.choices[key]||key}
function cjkScore(text){let score=0,bad=0;for(const ch of String(text||'')){const code=ch.charCodeAt(0);if(code>=0x4e00&&code<=0x9fff)score+=3;if(/[ÃÂäåæçèé�]/.test(ch)||(code>=0xe000&&code<=0xf8ff))bad+=4}return score-bad}
function repairMojibakeText(text){let best=String(text||''),bestScore=cjkScore(best);for(let pass=0;pass<2;pass++){let changed=false;if(/[ÃÂäåæçèé]/.test(best)){try{const bytes=new Uint8Array(Array.from(best,ch=>ch.charCodeAt(0)));const candidate=new TextDecoder('utf-8',{fatal:true}).decode(bytes),score=cjkScore(candidate);if(score>bestScore+2){best=candidate;bestScore=score;changed=true}}catch(_e){}}if(!changed)break}return best}
function localIssueText(value){
  const raw=String(value??'').trim();
  if(!raw||!isZh())return raw;
  let text=raw
    .replace(/\\\\r\\\\n/g,'\\n')
    .replace(/\\r\\n/g,'\\n')
    .replace(/\\\\n/g,'\\n')
    .replace(/\\n/g,'\\n')
    .replace(/\\"/g,'"');
  text=text.replace(/^.*RequestsDependencyWarning:.*(?:\\n\\s*warnings\\.warn\\(.*)?$/gmi,'');
  text=text.replace(/\\{[^\\n{}]*"exit_code"\\s*:\\s*0[^\\n{}]*"status"\\s*:\\s*"completed"[^\\n{}]*\\}/gi,'');
  text=repairMojibakeText(text);
  const lower=text.toLowerCase();
  if((lower.includes('provider_action_responses')||lower.includes('context_packet_hash')||lower.includes('"worktree"')||lower.includes('"task_id"'))&&!/^\\s*waiting_external_confirmation\\s*[:：]/im.test(text)){
    return '提供方返回了运行上下文或工具输出，已自动忽略这段非交互内容。';
  }
  text=text.replace(/^\\s*waiting_external_confirmation\\s*[:：]\\s*/im,'');
  text=text.replace(/The task stopped before normal delivery\\./g,'任务在正常交付前停止。');
  text=text.replace(/\\b([a-z][a-z0-9_/-]*) read_only_write_violation:/gi,(_m,stage)=>`${phaseLabel(stage)} 只读写入违规：`);
  text=text.replace(/read-only stage ([a-z][a-z0-9_/-]*) modified the worktree/gi,(_m,stage)=>`只读阶段「${phaseLabel(stage)}」修改了工作区`);
  text=text.replace(/\\bread_only_write_violation\\b/g,'只读写入违规');
  return text.trim();
}
function readinessLabel(value){return zhLookup(ZH.readiness,value)}
function providerValue(value){const raw=String(value??'');if(!isZh())return raw||'-';if(!raw||raw==='-')return'无';if(raw==='auto')return'自动';return raw}
function localText(value){const raw=String(value??'');return isZh()?(ZH.bestFor[raw]||raw):raw}
function setupHint(row){return isZh()?`在项目配置中为「${roleLabel(row.role||row.label)}」选择可用的模型提供方。`:String(row.setup_hint||'muxdev setup')}
function doctorHint(row){return isZh()?'如果不可用，请运行提供方诊断并按提示修复。':String(row.doctor_hint||'muxdev provider doctor')}
function api(p){return fetch('/api'+p,{headers:{accept:'application/json'}}).then(checkJson)}async function post(p,b,options={}){const e=p.startsWith('/api')?p:'/api'+p;const o={method:'POST',headers:{accept:'application/json'}};if(b!==undefined){o.headers['content-type']='application/json';o.body=JSON.stringify(b)}const r=await fetch(e,o);if(!r.ok)throw new Error(await responseMessage(r));const payload=await readJson(r);if(options.refresh!==false)await refresh({force:true});return payload}async function checkJson(r){if(!r.ok)throw new Error(await responseMessage(r));return readJson(r)}async function readJson(r){const text=await r.text();return text?JSON.parse(text):{}}async function responseMessage(r){const text=await r.text();try{const data=JSON.parse(text);return data.detail?String(data.detail):JSON.stringify(data)}catch(_e){return text||`${r.status} ${r.statusText}`}}
function notify(message){setText('refresh-state',message)}function beginButton(button){if(!button||button.tagName!=='BUTTON')return;button.dataset.originalText=button.textContent||'';button.textContent=t('processing');button.disabled=true}function endButton(button){if(!button||button.tagName!=='BUTTON')return;button.disabled=false;if(button.dataset.originalText)button.textContent=button.dataset.originalText;delete button.dataset.originalText}
function setText(id,v){const e=document.getElementById(id);if(e)e.textContent=v}function chip(label,tone=''){return `<span class="chip ${esc(tone)}">${esc(label||t('none'))}</span>`}function metric(label,value,tone){return `<div class="metric"><div class="label">${esc(label)}</div><strong>${esc(value)}</strong>${tone?chip(tone,tone):''}</div>`}
function folded(id,rows,render,emptyHtml){const list=Array.isArray(rows)?rows:[];if(!list.length)return emptyHtml||`<div class="empty">${esc(t('empty_tasks'))}</div>`;const open=!!state.expanded[id],visible=open?list:list.slice(0,3),rest=Math.max(0,list.length-visible.length);const body=visible.map(render).join('');const label=open?t('show_less'):String(t('show_more')).replace('{count}',rest);const toggle=rest||open?`<button type="button" class="fold-toggle" data-fold="${esc(id)}">${esc(label)}</button>`:'';return body+toggle}
function slug(v){return String(v??'').replace(/[^a-zA-Z0-9_-]+/g,'-').replace(/^-+|-+$/g,'').slice(0,80)||'item'}
function clampedText(id,value,className='meta'){const text=String(value??'').trim();if(!text)return'';const key=`text-${id}`,open=!!state.expanded[key],long=text.length>220||text.split(/\\r?\\n/).length>3;const cls=`${className} clamp${long&&!open?' is-clamped':''}`;const toggle=long?`<button type="button" class="clamp-toggle" data-clamp="${esc(key)}">${esc(open?t('show_short'):t('show_full'))}</button>`:'';return `<div class="${esc(cls)}">${esc(text)}</div>${toggle}`}
function captureDrafts(){document.querySelectorAll('input[data-draft-key],textarea[data-draft-key],select[data-draft-key]').forEach(el=>{state.drafts[el.dataset.draftKey]=el.value||''})}
function draftValue(key,fallback=''){return Object.prototype.hasOwnProperty.call(state.drafts,key)?state.drafts[key]:fallback}
function clearDraft(key){delete state.drafts[key]}
function activeDraftKey(){const el=document.activeElement;return el&&el.matches&&el.matches('input[data-draft-key],textarea[data-draft-key],select[data-draft-key]')?el.dataset.draftKey:''}
function shouldDeferRefresh(){return !!activeDraftKey()||Date.now()-state.lastInputAt<2500}
function optionSelected(key,value,fallback=''){return String(draftValue(key,fallback))===String(value)?' selected':''}
function taskId(task){return String(task.task_id||task.run_id||'')}function taskTitle(task){return localIssueText(String(task.title||task.task_title||task.task||taskId(task)||'muxdev task'))}
function projectTasks(project){const seen=new Set(),rows=[];for(const wf of project?.workflows||[])for(const group of wf.role_groups||[])for(const task of group.tasks||[]){const id=taskId(task);if(id&&!seen.has(id)){seen.add(id);rows.push({...task,project_id:project.id,project_name:project.name,project_path:project.path})}}return rows}
function allTasks(overview=state.overview){const seen=new Set(),rows=[];for(const project of overview?.projects||[])for(const task of projectTasks(project)){const id=taskId(task);if(id&&!seen.has(id)){seen.add(id);rows.push(task)}}for(const board of overview?.task_board||[])for(const task of board.tasks||[]){const id=taskId(task);if(id&&!seen.has(id)){seen.add(id);rows.push(task)}}return rows}
function statusBucket(task){const status=String(task.status||'');const pending=Number(task.pending_approvals||0)+Number(task.pending_provider_actions||0);if(pending||['awaiting_approval','awaiting_provider_action','paused_budget'].includes(status)||Number(task.errors||0)||['blocked','failed'].includes(status))return'action';if(status==='running')return'running';if(status==='completed')return'done';if(status==='aborted')return'stopped';if(['created','queued','pending'].includes(status)||!status)return'pending';return'blocked'}
function manualItems(overview=state.overview){const rows=[],seen=new Set();for(const row of overview?.pending_provider_actions||[]){const id=String(row.action_id||'');if(!id)continue;seen.add('provider:'+id);rows.push({...row,manual_kind:row.kind==='clarification_required'?'clarification':'provider_permission'})}for(const row of overview?.pending_approvals||[]){const id=String(row.approval_id||'');if(!id)continue;seen.add('approval:'+id);const type=String(row.type||row.approval_type||'');rows.push({...row,manual_kind:type==='plan'||type==='design'?'plan_approval':'approval'})}for(const row of overview?.action_center||[]){const approvalId=String(row.approval_id||''),actionId=String(row.action_id||'');if(approvalId&&seen.has('approval:'+approvalId))continue;if(actionId&&seen.has('provider:'+actionId))continue;const kind=String(row.kind||'');rows.push({...row,manual_kind:kind.includes('recovery')?'failure_gate':kind.includes('plan')?'plan_feedback':kind.includes('provider')?'provider_permission':kind.includes('approval')?'plan_approval':'failure_gate'})}return rows}
function itemRunId(row){return String(row.run_id||row.task_id||row.task?.task_id||'')}
function taskActionItems(task){const id=taskId(task);if(!id)return[];return manualItems(state.overview||{}).filter(row=>itemRunId(row)===id)}
function optionalTaskActions(task){const id=taskId(task);if(!id)return[];return (state.overview?.optional_actions||[]).filter(row=>itemRunId(row)===id)}
function recoveryRowFromTask(task){const id=taskId(task);return {run_id:id,task_id:id,stage_id:task.current_stage||task.error_summary?.stage_id||task.stage_id||'',why:task.error_summary?.message||task.failure_reason||task.current_activity||taskTitle(task),task_title:taskTitle(task)}}
function renderProjectTaskCard(task){const items=taskActionItems(task);if(items.length)return `<div class="stack project-action-stack">${items.map(renderActionCard).join('')}</div>`;const bucket=statusBucket(task);if(['action','blocked','stopped'].includes(bucket)&&taskId(task))return renderRecoveryCard(recoveryRowFromTask(task));return renderTaskCard(task)}
function canonicalWorkflowName(value){const name=String(value||'');if(name==='design'+'-v2')return'design';if(name==='dev'+'-light')return'dev-lite';return name}
function workflowRows(overview=state.overview){const config=overview?.global_config||state.globalConfig||{},defs=Array.isArray(config.workflow_templates?.definitions)?config.workflow_templates.definitions:[],templates=Array.isArray(config.workflow_templates?.templates)?config.workflow_templates.templates:[],raw=defs.concat(templates),rows=[],seen=new Set();for(const row of raw){const name=canonicalWorkflowName(row.name||row.id);if(!name||name==='design'+'-memory'||seen.has(name))continue;seen.add(name);rows.push({...row,id:name,name})}return rows}
function renderMetrics(overview){const config=overview?.global_config||state.globalConfig||{},tasks=allTasks(overview),running=tasks.filter(task=>statusBucket(task)==='running').length,projectsWithRuns=(overview.projects||[]).filter(project=>projectTasks(project).length>0).length,readyCli=config.providers?.ready||[],workflows=workflowRows(overview);document.getElementById('metrics').innerHTML=[metric(t('ready_cli'),readyCli.length,readyCli.length?'good':'warn'),metric(t('supported_workflows'),workflows.length,workflows.length?'good':'warn'),metric(t('running_tasks'),running,running?'info':''),metric(t('needs_my_action'),manualItems(overview).length,manualItems(overview).length?'warn':'good'),metric(t('projects_with_runs'),projectsWithRuns,projectsWithRuns?'info':'')].join('')}
function renderOverview(overview){renderMetrics(overview);setText('needs-title',t('needs_my_action'));setText('running-title',t('running_tasks'));setText('cli-title',t('ready_cli'));setText('workflow-title',t('supported_workflows'));const items=manualItems(overview);const counts={clarification:items.filter(r=>r.manual_kind==='clarification').length,plan_approval:items.filter(r=>r.manual_kind==='plan_approval').length,plan_feedback:items.filter(r=>r.manual_kind==='plan_feedback').length,provider_permission:items.filter(r=>r.manual_kind==='provider_permission').length,failure_gate:items.filter(r=>r.manual_kind==='failure_gate').length};document.getElementById('manual-breakdown').innerHTML=Object.entries(counts).map(([k,v])=>chip(`${t(k)} ${v}`,v?'warn':'')).join('');document.getElementById('action-list').innerHTML=folded('needs-action',items,renderActionCard,`<div class="empty">${esc(t('empty_actions'))}</div>`);const runningTasks=allTasks(overview).filter(task=>statusBucket(task)==='running');document.getElementById('running-list').innerHTML=folded('running-tasks',runningTasks,task=>renderTaskCard(task,{compact:true}),`<div class="empty">${esc(t('empty_tasks'))}</div>`);renderCliList(overview);renderWorkflowList(overview)}
function providerName(row){return typeof row==='object'?String(row.provider||row.name||''):String(row||'')}
function renderCliList(overview){const p=(overview?.global_config||state.globalConfig||{}).providers||{};const rows=[...(p.ready||[]).map(n=>({name:providerName(n),tone:'good',label:t('ready')})),...(p.partial||[]).map(n=>({name:providerName(n),tone:'warn',label:t('partial')})),...(p.unavailable||[]).map(n=>({name:providerName(n),tone:'bad',label:t('unavailable')}))].filter(row=>row.name);document.getElementById('cli-list').innerHTML=folded('cli-providers',rows,row=>chip(`${row.name} ${row.label}`,row.tone),chip(t('none')))}
function renderWorkflowList(overview){const rows=workflowRows(overview);document.getElementById('workflow-list').innerHTML=folded('workflow-overview',rows,row=>chip(row.name||row.id,'info'),chip(t('none')))}
function renderProjects(overview){const projects=overview.projects||[];setText('projects-title',t('projects'));if(!state.selectedProjectId||(state.selectedProjectId!=='__all'&&!projects.some(p=>p.id===state.selectedProjectId)))state.selectedProjectId=overview.selected_project_id||projects[0]?.id||null;const buttons=[`<button type="button" data-project="__all" class="${state.selectedProjectId==='__all'?'active':''}">${esc(t('all_projects'))}</button>`].concat(projects.map(p=>`<button type="button" data-project="${esc(p.id)}" class="${p.id===state.selectedProjectId?'active':''}">${esc(p.name||p.id)} <span class="meta">${esc(p.summary?.tasks||0)}</span></button>`));document.getElementById('project-list').innerHTML=buttons.join('');const selected=state.selectedProjectId==='__all'?projects:projects.filter(p=>p.id===state.selectedProjectId);const tasks=selected.flatMap(projectTasks);const buckets=[['running',t('status_running')],['action',t('status_action')],['blocked',t('status_blocked')],['done',t('status_done')],['stopped',t('status_stopped')],['pending',t('status_pending')]];document.getElementById('project-board').innerHTML=buckets.map(([id,label])=>{const rows=tasks.filter(task=>statusBucket(task)===id),foldId=`project-${state.selectedProjectId||'all'}-${id}`;return `<section class="status-section"><h3>${esc(label)} <span class="chip">${rows.length}</span></h3>${folded(foldId,rows,task=>renderProjectTaskCard(task),`<div class="empty">${esc(t('empty_tasks'))}</div>`)}</section>`}).join('')}
function renderTaskCard(task){const id=taskId(task),role=String(task.model_role||task.role||'code'),actor=String(task.stage_actor_label||role||'-'),terminalPath=`/tasks/${encodeURIComponent(id)}/terminal?agent=${encodeURIComponent(role||'code')}`,localUrl=`${location.origin}${terminalPath}`,bucket=statusBucket(task),canStop=!['done','stopped'].includes(bucket),activity=localIssueText(task.current_activity),optional=optionalTaskActions(task).map(renderOptionalAction).join('');return `<article class="task-card" data-task-card="${esc(id)}"><div class="title-row"><strong>${esc(taskTitle(task))}</strong>${chip(statusLabel(task.status||bucket),bucket==='action'?'warn':bucket==='running'?'info':bucket==='done'?'good':bucket==='stopped'?'bad':'')}</div><div class="task-meta"><div>${esc(t('workflow'))}: ${esc(workflowLabel(task.workflow||'-'))}</div><div>${esc(t('stage'))}: ${esc(phaseLabel(task.current_stage||'-'))}</div><div>${esc(t('provider'))}: ${esc(task.provider||'-')}</div><div>${esc(t('role'))}: ${esc(roleLabel(actor))}</div></div>${activity?clampedText(`task-${slug(id)}-activity`,activity):''}${optional}<div class="actions"><a class="button primary" href="${esc(terminalPath)}" target="_blank" rel="noreferrer">${esc(t('view_cli'))}</a><button type="button" data-copy="${esc(localUrl)}">${esc(t('terminal_link'))}</button><button type="button" data-copy-attach="${esc(id)}" data-agent="${esc(role)}">${esc(t('copy_attach'))}</button>${canStop?`<button type="button" class="danger" data-task-stop="${esc(id)}">${esc(t('stop'))}</button>`:''}</div></article>`}
function renderActionCard(row){if(row.approval_id)return renderApprovalCard(row);if(row.action_id)return renderProviderActionCard(row);if(row.manual_kind==='plan_feedback')return renderFeedbackCard(row);return renderRecoveryCard(row)}
function renderOptionalAction(row){if(String(row.kind||'')==='plan_feedback')return renderOptionalFeedback(row);return''}
function renderOptionalFeedback(row){const runId=String(row.run_id||row.task_id||''),body=localIssueText(row.why||row.headline||runId),draftKey=`optional-feedback-${runId||slug(row.stage_id||row.kind||'plan')}`;return `<div class="optional-feedback">${clampedText(`optional-feedback-${slug(runId)}`,body)}<textarea data-optional-feedback="${esc(runId)}" data-draft-key="${esc(draftKey)}" placeholder="${esc(t('optional_feedback_placeholder'))}">${esc(draftValue(draftKey,''))}</textarea><div class="actions"><button type="button" data-submit-feedback="${esc(runId)}" data-feedback-draft="${esc(draftKey)}" data-feedback-optional="true">${esc(t('optional_feedback'))}</button></div></div>`}
function renderApprovalCard(row){const id=String(row.approval_id||''),runId=String(row.run_id||row.task_id||''),body=localIssueText(row.reason||row.why||row.subject_summary||runId),draftKey=`approval-feedback-${id||runId}`;return `<article class="action-card"><div class="title-row"><strong>${esc(t(row.manual_kind||'plan_approval'))}</strong>${chip(phaseLabel(row.type||row.stage_id||'approval'),'warn')}</div>${clampedText(`approval-${slug(id||runId)}`,body)}<textarea data-feedback-text="${esc(id)}" data-draft-key="${esc(draftKey)}" placeholder="${esc(t('feedback_placeholder'))}">${esc(draftValue(draftKey,''))}</textarea><div class="actions"><button type="button" class="primary" data-approve="${esc(id)}">${esc(t('approve'))}</button><button type="button" class="danger" data-deny="${esc(id)}">${esc(t('deny'))}</button><button type="button" data-feedback="${esc(id)}">${esc(t('feedback'))}</button></div></article>`}
function renderProviderActionCard(row){const id=String(row.action_id||''),runId=String(row.run_id||row.task_id||''),choices=Array.isArray(row.choices)&&row.choices.length?row.choices:(Array.isArray(row.options)?row.options:[]),inputKind=String(row.input_kind||''),prompt=localIssueText(row.prompt_text||row.why||row.stage_id||runId),draftKey=`provider-${id||runId}`,needsText=!choices.length&&inputKind!=='external';const fallback=String(row.default_choice||choices[0]?.value||choices[0]?.label||'');const input=choices.length?`<select data-action-input="${esc(id)}" data-draft-key="${esc(draftKey)}">${choices.map(c=>{const value=String(c.value??c.label??'');return `<option value="${esc(value)}"${optionSelected(draftKey,value,fallback)}>${esc(choiceLabel(c.label??c.value??''))}</option>`}).join('')}</select>`:(needsText?`<textarea data-action-input="${esc(id)}" data-draft-key="${esc(draftKey)}" placeholder="${esc(t('response_placeholder'))}">${esc(draftValue(draftKey,''))}</textarea>`:'');const endpoint=input?`/tasks/${encodeURIComponent(runId||'latest')}/actions/${encodeURIComponent(id)}/respond-and-continue`:(row.endpoint||`/tasks/${encodeURIComponent(runId||'latest')}/actions/${encodeURIComponent(id)}/handled-and-continue`),label=row.provider||phaseLabel(row.kind||'provider');return `<article class="action-card"><div class="title-row"><strong>${esc(t(row.manual_kind||'provider_permission'))}</strong>${chip(label,row.kind==='clarification_required'?'info':'warn')}</div>${clampedText(`provider-action-${slug(id||runId)}`,prompt)}${input}<div class="actions"><button type="button" class="primary" data-provider-respond="${esc(id)}" data-provider-endpoint="${esc(endpoint)}" data-provider-draft="${esc(draftKey)}">${esc(input?t('submit_continue'):t('allow_continue'))}</button><button type="button" class="danger" data-provider-deny="${esc(id)}">${esc(t('dismiss'))}</button>${row.attach_command?`<button type="button" data-copy="${esc(row.attach_command)}">${esc(t('copy_attach'))}</button>`:''}</div></article>`}
function renderFeedbackCard(row){const runId=String(row.run_id||row.task_id||''),body=localIssueText(row.why||row.headline||row.task_title||runId),draftKey=`generic-feedback-${runId||slug(row.stage_id||row.kind||'plan')}`;return `<article class="action-card"><div class="title-row"><strong>${esc(t('plan_feedback'))}</strong>${chip(phaseLabel(row.stage_id||row.kind||'plan'),'info')}</div>${clampedText(`feedback-${slug(runId)}`,body)}<textarea data-generic-feedback="${esc(runId)}" data-draft-key="${esc(draftKey)}" placeholder="${esc(t('feedback_placeholder'))}">${esc(draftValue(draftKey,''))}</textarea><div class="actions"><button type="button" class="primary" data-submit-feedback="${esc(runId)}" data-feedback-draft="${esc(draftKey)}">${esc(t('submit_continue'))}</button>${runId?`<button type="button" data-task-stop="${esc(runId)}">${esc(t('stop'))}</button>`:''}</div></article>`}
function renderRecoveryCard(row){const runId=String(row.run_id||row.task_id||''),body=localIssueText(row.why||row.headline||row.task_title||runId);return `<article class="action-card"><div class="title-row"><strong>${esc(t('failure_gate'))}</strong>${chip(phaseLabel(row.stage_id||row.kind||'recovery'),'bad')}</div>${clampedText(`recovery-${slug(runId)}`,body)}<div class="actions">${runId?`<button type="button" class="primary" data-task-continue="${esc(runId)}">${esc(t('retry'))}</button><button type="button" class="danger" data-task-stop="${esc(runId)}">${esc(t('stop'))}</button><button type="button" data-task-rollback="${esc(runId)}">${esc(t('rollback'))}</button>`:''}</div></article>`}
function renderConfig(overview){
  setText('workflow-templates-title',t('workflow_templates'));
  setText('role-cli-title',t('role_cli_mapping'));
  const config=overview?.global_config||state.globalConfig||{},workflows=workflowRows(overview),routes=config.role_routes||[],empty=state.globalConfigLoading?`<div class="empty">${esc(t('config_loading'))}</div>`:`<div class="empty">${esc(t('empty_config'))}</div>`;
  document.getElementById('workflow-templates').innerHTML=`<div class="stack">${folded('config-workflows',workflows,renderWorkflowTemplateCard,empty)}</div>`;
  document.getElementById('role-cli').innerHTML=`<div class="stack">${folded('config-role-routes',routes,renderRoleRouteCard,empty)}</div>`;
}
function stageTone(stage){const kind=String(stage.actor_kind||stage.type||'');if(kind==='human_gate')return'human';if(kind==='delivery_gate')return'delivery';if(kind==='model_role'||stage.model_role||stage.role)return'model';return''}
function renderWorkflowTemplateCard(row){const stages=row.stages||row.phases||[],roles=row.model_roles||row.roles||[],human=row.human_gates||[],delivery=row.delivery_gates||[],best=row.best_for||[],key=slug(row.name||row.id),title=workflowLabel(row.name||row.id);const stageHtml=(stages||[]).map(s=>typeof s==='string'?`<span class="stage-pill">${esc(phaseLabel(s))}</span>`:`<span class="stage-pill ${stageTone(s)}" title="${esc(zhLookup(ZH.actorKinds,s.actor_label||s.type||''))}">${esc(phaseLabel(s.id||s.stage||'-'))}</span>`).join('')||chip(t('none'));const bestText=best.map(localText).join('\\n');return `<article class="workflow-card"><div class="title-row"><strong>${esc(title)}</strong>${chip(`${esc(row.stage_count||stages.length||0)} ${t('stage')}`,'info')}</div>${clampedText(`workflow-${key}-description`,workflowDescription(row),'ability')}${best.length?`<div><h3>${esc(t('best_for'))}</h3>${clampedText(`workflow-${key}-best`,bestText,'ability')}</div>`:''}<div><h3>${esc(t('workflow_stage_flow'))}</h3><div class="stage-rail">${stageHtml}</div></div><div><h3>${esc(t('model_roles'))}</h3><div class="chips">${roles.length?roles.map(role=>chip(roleLabel(role),'info')).join(''):chip(t('none'))}</div></div><div class="chips">${human.length?human.map(g=>chip(`${t('human_gates')}: ${phaseLabel(g.stage||g.type)}`,'warn')).join(''):''}${delivery.length?delivery.map(g=>chip(`${t('delivery_gates')}: ${phaseLabel(g.stage)}`,'good')).join(''):''}</div></article>`}
function renderRoleRouteCard(row){const tone=row.readiness==='ready'?'good':row.readiness==='partial'?'warn':row.readiness==='unavailable'?'bad':'info',key=slug(row.role||row.label),role=row.role||row.label;return `<article class="route-card"><div class="title-row"><strong>${esc(roleLabel(role))}</strong>${chip(readinessLabel(row.readiness||t('none')),tone)}</div>${clampedText(`route-${key}-ability`,abilityLabel(role,row.ability),'ability')}<div class="task-meta"><div>${esc(t('configured_provider'))}: ${esc(providerValue(row.configured_provider||'auto'))}</div><div>${esc(t('fallback_provider'))}: ${esc(providerValue(row.fallback_provider||'-'))}</div><div>${esc(t('effective_provider'))}: ${esc(providerValue(row.provider||'-'))}</div><div>${esc(t('readiness'))}: ${esc(readinessLabel(row.readiness||'-'))}</div></div>${clampedText(`route-${key}-setup`,`${t('setup_hint')}: ${setupHint(row)}`)}${clampedText(`route-${key}-doctor`,`${t('doctor_hint')}: ${doctorHint(row)}`)}</article>`}
function setView(view){state.view=view;document.querySelectorAll('[data-view-button]').forEach(b=>b.classList.toggle('active',b.dataset.viewButton===view));document.querySelectorAll('.view').forEach(s=>s.classList.toggle('active',s.id===`view-${view}`));if(view==='config'&&!state.globalConfigLoaded)loadGlobalConfig()}
async function copyAttach(taskId,agent){const payload=await api(`/tasks/${encodeURIComponent(taskId)}/attach-command?agent=${encodeURIComponent(agent||'implementer')}`),handoff=payload.handoff||{},command=Array.isArray(handoff.command)?handoff.command.join(' '):handoff.command;await copyText(command||handoff.path||'')}async function copyText(text){if(!text)return;await navigator.clipboard.writeText(String(text));setText('refresh-state',t('copied'))}
function hasGlobalConfig(config){return !!config&&typeof config==='object'&&Object.keys(config).length>0}
function withCachedConfig(overview){if(!overview)overview={};const incoming=overview.global_config;if(state.globalConfig&&(!hasGlobalConfig(incoming)||overview.global_config_deferred))overview.global_config=state.globalConfig;if(hasGlobalConfig(overview.global_config)&&!overview.global_config_deferred){state.globalConfig=overview.global_config;state.globalConfigLoaded=true}return overview}
async function loadGlobalConfig(){if(state.globalConfigLoaded||state.globalConfigLoading)return;state.globalConfigLoading=true;renderConfig(state.overview||{});try{const overview=await api('/dashboard/overview?include_global_config=true');state.globalConfig=overview.global_config||{};state.globalConfigLoaded=hasGlobalConfig(state.globalConfig);if(state.overview){state.overview.global_config=state.globalConfig;state.overview.global_config_deferred=false;renderOverview(state.overview);renderProjects(state.overview);renderConfig(state.overview)}}catch(error){notify(`${t('action_failed')}: ${error.message||error}`)}finally{state.globalConfigLoading=false;renderConfig(state.overview||{})}}
async function refresh(options={}){if(!options.force&&shouldDeferRefresh()){state.pendingRefresh=true;notify(t('refresh_deferred'));return}captureDrafts();try{setText('refresh-state',t('loading'));const overview=withCachedConfig(await api('/dashboard/overview?include_global_config=false'));state.overview=overview;state.pendingRefresh=false;if(state.focusTaskId){const task=allTasks(overview).find(item=>taskId(item)===state.focusTaskId);if(task?.project_id)state.selectedProjectId=task.project_id}renderOverview(overview);renderProjects(overview);renderConfig(overview);setText('refresh-state',`${t('last_refresh')}: ${new Date().toLocaleTimeString()}`);if(state.view==='config'&&!state.globalConfigLoaded&&!state.globalConfigLoading)loadGlobalConfig()}catch(error){setText('refresh-state',t('daemon_offline'));document.getElementById('action-list').innerHTML=`<div class="empty">${esc(error.message||error)}</div>`}}
function rerenderAll(){captureDrafts();renderOverview(state.overview||{});renderProjects(state.overview||{});renderConfig(state.overview||{})}
function cssEscape(value){return window.CSS&&CSS.escape?CSS.escape(String(value)):String(value).replace(/["\\\\]/g,'\\\\$&')}
function draftText(key){captureDrafts();return String(state.drafts[key]??'').trim()}
function providerResponseBody(target){const actionId=target.dataset.providerRespond,input=document.querySelector(`[data-action-input="${cssEscape(actionId)}"]`);if(!input)return undefined;const value=input.value||'';if(input.tagName==='SELECT')return {response:{choice:value}};if(!value.trim())throw new Error(t('response_required'));return {response:{text:value.trim()}}}
async function submitProviderResponse(target){const key=target.dataset.providerDraft||'',body=providerResponseBody(target);await post(target.dataset.providerEndpoint,body,{refresh:false});if(key)clearDraft(key);await refresh({force:true})}
async function submitPlanFeedback(target){const runId=target.dataset.submitFeedback,key=target.dataset.feedbackDraft||`generic-feedback-${runId}`,text=draftText(key),optional=target.dataset.feedbackOptional==='true';if(!text)throw new Error(t('feedback_required'));await post('/feedback',{kind:'manual_feedback',source:'dashboard',content:text,run_id:runId,auto_submit:!optional},{refresh:false});if(!optional&&runId)await post(`/tasks/${encodeURIComponent(runId)}/continue`,undefined,{refresh:false});clearDraft(key);await refresh({force:true})}
async function runButtonAction(button,action){beginButton(button);try{await action();notify(t('submitted'))}finally{endButton(button)}}
document.addEventListener('input',event=>{const target=event.target;if(target?.matches?.('input[data-draft-key],textarea[data-draft-key],select[data-draft-key]')){state.drafts[target.dataset.draftKey]=target.value||'';state.lastInputAt=Date.now()}})
document.addEventListener('change',event=>{const target=event.target;if(target?.matches?.('input[data-draft-key],textarea[data-draft-key],select[data-draft-key]')){state.drafts[target.dataset.draftKey]=target.value||'';state.lastInputAt=Date.now()}})
document.addEventListener('click',async event=>{const target=event.target.closest('button,a');if(!target)return;if(target.tagName==='BUTTON')event.preventDefault();try{if(target.dataset.viewButton){setView(target.dataset.viewButton);notify(`${t('last_refresh')}: ${new Date().toLocaleTimeString()}`);return}if(target.dataset.fold){state.expanded[target.dataset.fold]=!state.expanded[target.dataset.fold];rerenderAll();return}if(target.dataset.clamp){state.expanded[target.dataset.clamp]=!state.expanded[target.dataset.clamp];rerenderAll();return}if(target.dataset.project){state.selectedProjectId=target.dataset.project;renderProjects(state.overview||{});return}if(target.id==='refresh-button'){beginButton(target);try{await refresh({force:true})}finally{endButton(target)}return}if(target.dataset.copy){await copyText(target.dataset.copy);return}if(target.dataset.copyAttach){beginButton(target);try{await copyAttach(target.dataset.copyAttach,target.dataset.agent)}finally{endButton(target)}return}if(target.dataset.approve){await runButtonAction(target,async()=>post(`/approvals/${encodeURIComponent(target.dataset.approve)}/approve`));return}if(target.dataset.deny){await runButtonAction(target,async()=>post(`/approvals/${encodeURIComponent(target.dataset.deny)}/deny`));return}if(target.dataset.feedback){await runButtonAction(target,async()=>{const key=`approval-feedback-${target.dataset.feedback}`,field=document.querySelector(`[data-feedback-text="${cssEscape(target.dataset.feedback)}"]`),text=(field?.value||state.drafts[key]||'').trim();if(!text)throw new Error(t('feedback_required'));await post(`/approvals/${encodeURIComponent(target.dataset.feedback)}/feedback-and-continue`,{feedback:text},{refresh:false});clearDraft(key);await refresh({force:true})});return}if(target.dataset.submitFeedback){await runButtonAction(target,async()=>submitPlanFeedback(target));return}if(target.dataset.providerRespond){await runButtonAction(target,async()=>submitProviderResponse(target));return}if(target.dataset.providerDeny){await runButtonAction(target,async()=>post(`/provider-actions/${encodeURIComponent(target.dataset.providerDeny)}/dismiss`));return}if(target.dataset.taskStop){await runButtonAction(target,async()=>post(`/tasks/${encodeURIComponent(target.dataset.taskStop)}/stop`));return}if(target.dataset.taskContinue){await runButtonAction(target,async()=>post(`/tasks/${encodeURIComponent(target.dataset.taskContinue)}/continue`));return}if(target.dataset.taskRollback){await runButtonAction(target,async()=>post(`/tasks/${encodeURIComponent(target.dataset.taskRollback)}/rollback`));return}}catch(error){notify(`${t('action_failed')}: ${error.message||String(error)}`);endButton(target)}})
function initLabels(){document.querySelector('[data-view-button="overview"]').textContent=t('overview');document.querySelector('[data-view-button="projects"]').textContent=t('projects');document.querySelector('[data-view-button="config"]').textContent=t('config');setText('command-palette-button',t('command_palette'));setText('refresh-button',t('refresh'))}
initLabels();refresh({force:true});setInterval(()=>refresh(),30000);try{const socket=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/events`);socket.onmessage=()=>refresh()}catch(error){}
  </script>
</body>
</html>
"""
