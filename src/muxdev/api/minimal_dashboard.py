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
    },
    "en": {
        "title": "muxdev Dashboard",
        "app_name": "muxdev Dashboard",
        "subtitle": "Minimal operations surface: overview, projects, config, and every item that needs human handling.",
        "overview": "Overview",
        "projects": "Projects",
        "config": "Config",
        "refresh": "Refresh",
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
    .task-card,.action-card{display:grid;gap:8px}.task-card+.task-card,.action-card+.action-card{margin-top:10px}.title-row strong{overflow-wrap:anywhere}
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
    <div class="topline"><div><h1>__APP_NAME__</h1><div class="subtitle">__SUBTITLE__</div></div><div class="actions"><span class="meta" id="refresh-state">__TITLE__</span><button type="button" class="ghost" id="refresh-button"></button></div></div>
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
const TEXT=__TEXT__;const state={overview:null,selectedProjectId:null,focusTaskId:document.body.dataset.taskId||'',view:'overview',expanded:{}};
function t(k){return TEXT[k]||k}function esc(v){return String(v??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]))}
function api(p){return fetch('/api'+p,{headers:{accept:'application/json'}}).then(checkJson)}async function post(p,b){const e=p.startsWith('/api')?p:'/api'+p;const o={method:'POST',headers:{accept:'application/json'}};if(b!==undefined){o.headers['content-type']='application/json';o.body=JSON.stringify(b)}const r=await fetch(e,o);if(!r.ok)throw new Error(await r.text());await refresh()}async function checkJson(r){if(!r.ok)throw new Error(await r.text());return r.json()}
function setText(id,v){const e=document.getElementById(id);if(e)e.textContent=v}function chip(label,tone=''){return `<span class="chip ${esc(tone)}">${esc(label||t('none'))}</span>`}function metric(label,value,tone){return `<div class="metric"><div class="label">${esc(label)}</div><strong>${esc(value)}</strong>${tone?chip(tone,tone):''}</div>`}
function folded(id,rows,render,emptyHtml){const list=Array.isArray(rows)?rows:[];if(!list.length)return emptyHtml||`<div class="empty">${esc(t('empty_tasks'))}</div>`;const open=!!state.expanded[id],visible=open?list:list.slice(0,3),rest=Math.max(0,list.length-visible.length);const body=visible.map(render).join('');const label=open?t('show_less'):String(t('show_more')).replace('{count}',rest);const toggle=rest||open?`<button type="button" class="fold-toggle" data-fold="${esc(id)}">${esc(label)}</button>`:'';return body+toggle}
function slug(v){return String(v??'').replace(/[^a-zA-Z0-9_-]+/g,'-').replace(/^-+|-+$/g,'').slice(0,80)||'item'}
function clampedText(id,value,className='meta'){const text=String(value??'').trim();if(!text)return'';const key=`text-${id}`,open=!!state.expanded[key],long=text.length>220||text.split(/\r?\n/).length>3;const cls=`${className} clamp${long&&!open?' is-clamped':''}`;const toggle=long?`<button type="button" class="clamp-toggle" data-clamp="${esc(key)}">${esc(open?t('show_short'):t('show_full'))}</button>`:'';return `<div class="${esc(cls)}">${esc(text)}</div>${toggle}`}
function taskId(task){return String(task.task_id||task.run_id||'')}function taskTitle(task){return String(task.title||task.task_title||task.task||taskId(task)||'muxdev task')}
function projectTasks(project){const seen=new Set(),rows=[];for(const wf of project?.workflows||[])for(const group of wf.role_groups||[])for(const task of group.tasks||[]){const id=taskId(task);if(id&&!seen.has(id)){seen.add(id);rows.push({...task,project_id:project.id,project_name:project.name,project_path:project.path})}}return rows}
function allTasks(overview=state.overview){const seen=new Set(),rows=[];for(const project of overview?.projects||[])for(const task of projectTasks(project)){const id=taskId(task);if(id&&!seen.has(id)){seen.add(id);rows.push(task)}}for(const board of overview?.task_board||[])for(const task of board.tasks||[]){const id=taskId(task);if(id&&!seen.has(id)){seen.add(id);rows.push(task)}}return rows}
function statusBucket(task){const status=String(task.status||'');const pending=Number(task.pending_approvals||0)+Number(task.pending_provider_actions||0);if(pending||['awaiting_approval','awaiting_provider_action','paused_budget'].includes(status)||Number(task.errors||0)||['blocked','failed'].includes(status))return'action';if(status==='running')return'running';if(status==='completed')return'done';if(status==='aborted')return'stopped';if(['created','queued','pending'].includes(status)||!status)return'pending';return'blocked'}
function manualItems(overview=state.overview){const rows=[],seen=new Set();for(const row of overview?.pending_provider_actions||[]){const id=String(row.action_id||'');if(!id)continue;seen.add('provider:'+id);rows.push({...row,manual_kind:row.kind==='clarification_required'?'clarification':'provider_permission'})}for(const row of overview?.pending_approvals||[]){const id=String(row.approval_id||'');if(!id)continue;seen.add('approval:'+id);const type=String(row.type||row.approval_type||'');rows.push({...row,manual_kind:type==='plan'||type==='design'?'plan_approval':'approval'})}for(const row of overview?.action_center||[]){const approvalId=String(row.approval_id||''),actionId=String(row.action_id||'');if(approvalId&&seen.has('approval:'+approvalId))continue;if(actionId&&seen.has('provider:'+actionId))continue;const kind=String(row.kind||'');rows.push({...row,manual_kind:kind.includes('recovery')?'failure_gate':kind.includes('plan')?'plan_feedback':kind.includes('provider')?'provider_permission':kind.includes('approval')?'plan_approval':'failure_gate'})}return rows}
function canonicalWorkflowName(value){const name=String(value||'');if(name==='design'+'-v2')return'design';if(name==='dev'+'-light')return'dev-lite';return name}
function workflowRows(overview=state.overview){const config=overview?.global_config||{},defs=Array.isArray(config.workflow_templates?.definitions)?config.workflow_templates.definitions:[],templates=Array.isArray(config.workflow_templates?.templates)?config.workflow_templates.templates:[],raw=defs.concat(templates),rows=[],seen=new Set();for(const row of raw){const name=canonicalWorkflowName(row.name||row.id);if(!name||name==='design'+'-memory'||seen.has(name))continue;seen.add(name);rows.push({...row,id:name,name})}return rows}
function renderMetrics(overview){const tasks=allTasks(overview),running=tasks.filter(task=>statusBucket(task)==='running').length,projectsWithRuns=(overview.projects||[]).filter(project=>projectTasks(project).length>0).length,readyCli=overview.global_config?.providers?.ready||[],workflows=workflowRows(overview);document.getElementById('metrics').innerHTML=[metric(t('ready_cli'),readyCli.length,readyCli.length?'good':'warn'),metric(t('supported_workflows'),workflows.length,workflows.length?'good':'warn'),metric(t('running_tasks'),running,running?'info':''),metric(t('needs_my_action'),manualItems(overview).length,manualItems(overview).length?'warn':'good'),metric(t('projects_with_runs'),projectsWithRuns,projectsWithRuns?'info':'')].join('')}
function renderOverview(overview){renderMetrics(overview);setText('needs-title',t('needs_my_action'));setText('running-title',t('running_tasks'));setText('cli-title',t('ready_cli'));setText('workflow-title',t('supported_workflows'));const items=manualItems(overview);const counts={clarification:items.filter(r=>r.manual_kind==='clarification').length,plan_approval:items.filter(r=>r.manual_kind==='plan_approval').length,plan_feedback:items.filter(r=>r.manual_kind==='plan_feedback').length,provider_permission:items.filter(r=>r.manual_kind==='provider_permission').length,failure_gate:items.filter(r=>r.manual_kind==='failure_gate').length};document.getElementById('manual-breakdown').innerHTML=Object.entries(counts).map(([k,v])=>chip(`${t(k)} ${v}`,v?'warn':'')).join('');document.getElementById('action-list').innerHTML=folded('needs-action',items,renderActionCard,`<div class="empty">${esc(t('empty_actions'))}</div>`);const runningTasks=allTasks(overview).filter(task=>statusBucket(task)==='running');document.getElementById('running-list').innerHTML=folded('running-tasks',runningTasks,task=>renderTaskCard(task,{compact:true}),`<div class="empty">${esc(t('empty_tasks'))}</div>`);renderCliList(overview);renderWorkflowList(overview)}
function providerName(row){return typeof row==='object'?String(row.provider||row.name||''):String(row||'')}
function renderCliList(overview){const p=overview.global_config?.providers||{};const rows=[...(p.ready||[]).map(n=>({name:providerName(n),tone:'good',label:t('ready')})),...(p.partial||[]).map(n=>({name:providerName(n),tone:'warn',label:t('partial')})),...(p.unavailable||[]).map(n=>({name:providerName(n),tone:'bad',label:t('unavailable')}))].filter(row=>row.name);document.getElementById('cli-list').innerHTML=folded('cli-providers',rows,row=>chip(`${row.name} ${row.label}`,row.tone),chip(t('none')))}
function renderWorkflowList(overview){const rows=workflowRows(overview);document.getElementById('workflow-list').innerHTML=folded('workflow-overview',rows,row=>chip(row.name||row.id,'info'),chip(t('none')))}
function renderProjects(overview){const projects=overview.projects||[];setText('projects-title',t('projects'));if(!state.selectedProjectId||!projects.some(p=>p.id===state.selectedProjectId))state.selectedProjectId=overview.selected_project_id||projects[0]?.id||null;const buttons=[`<button type="button" data-project="__all" class="${state.selectedProjectId==='__all'?'active':''}">${esc(t('all_projects'))}</button>`].concat(projects.map(p=>`<button type="button" data-project="${esc(p.id)}" class="${p.id===state.selectedProjectId?'active':''}">${esc(p.name||p.id)} <span class="meta">${esc(p.summary?.tasks||0)}</span></button>`));document.getElementById('project-list').innerHTML=buttons.join('');const selected=state.selectedProjectId==='__all'?projects:projects.filter(p=>p.id===state.selectedProjectId);const tasks=selected.flatMap(projectTasks);const buckets=[['running',t('status_running')],['action',t('status_action')],['blocked',t('status_blocked')],['done',t('status_done')],['stopped',t('status_stopped')],['pending',t('status_pending')]];document.getElementById('project-board').innerHTML=buckets.map(([id,label])=>{const rows=tasks.filter(task=>statusBucket(task)===id),foldId=`project-${state.selectedProjectId||'all'}-${id}`;return `<section class="status-section"><h3>${esc(label)} <span class="chip">${rows.length}</span></h3>${folded(foldId,rows,task=>renderTaskCard(task),`<div class="empty">${esc(t('empty_tasks'))}</div>`)}</section>`}).join('')}
function renderTaskCard(task){const id=taskId(task),role=String(task.model_role||task.role||'code'),actor=String(task.stage_actor_label||role||'-'),terminalPath=`/tasks/${encodeURIComponent(id)}/terminal?agent=${encodeURIComponent(role||'code')}`,localUrl=`${location.origin}${terminalPath}`,bucket=statusBucket(task),canStop=!['done','stopped'].includes(bucket);return `<article class="task-card" data-task-card="${esc(id)}"><div class="title-row"><strong>${esc(taskTitle(task))}</strong>${chip(task.status||bucket,bucket==='action'?'warn':bucket==='running'?'info':bucket==='done'?'good':bucket==='stopped'?'bad':'')}</div><div class="task-meta"><div>${esc(t('workflow'))}: ${esc(task.workflow||'-')}</div><div>${esc(t('stage'))}: ${esc(task.current_stage||'-')}</div><div>${esc(t('provider'))}: ${esc(task.provider||'-')}</div><div>${esc(t('role'))}: ${esc(actor)}</div></div>${task.current_activity?clampedText(`task-${slug(id)}-activity`,task.current_activity):''}<div class="actions"><a class="button primary" href="${esc(terminalPath)}" target="_blank" rel="noreferrer">${esc(t('view_cli'))}</a><button type="button" data-copy="${esc(localUrl)}">${esc(t('terminal_link'))}</button><button type="button" data-copy-attach="${esc(id)}" data-agent="${esc(role)}">${esc(t('copy_attach'))}</button>${canStop?`<button type="button" class="danger" data-task-stop="${esc(id)}">${esc(t('stop'))}</button>`:''}</div></article>`}
function renderActionCard(row){if(row.approval_id)return renderApprovalCard(row);if(row.action_id)return renderProviderActionCard(row);if(row.manual_kind==='plan_feedback')return renderFeedbackCard(row);return renderRecoveryCard(row)}
function renderApprovalCard(row){const id=String(row.approval_id||''),runId=String(row.run_id||row.task_id||'');return `<article class="action-card"><div class="title-row"><strong>${esc(t(row.manual_kind||'plan_approval'))}</strong>${chip(row.type||row.stage_id||'approval','warn')}</div>${clampedText(`approval-${slug(id||runId)}`,row.reason||row.why||row.subject_summary||runId)}<textarea data-feedback-text="${esc(id)}" placeholder="${esc(t('feedback_placeholder'))}"></textarea><div class="actions"><button type="button" class="primary" data-approve="${esc(id)}">${esc(t('approve'))}</button><button type="button" class="danger" data-deny="${esc(id)}">${esc(t('deny'))}</button><button type="button" data-feedback="${esc(id)}">${esc(t('feedback'))}</button></div></article>`}
function renderProviderActionCard(row){const id=String(row.action_id||''),runId=String(row.run_id||row.task_id||''),choices=Array.isArray(row.choices)?row.choices:[],inputKind=String(row.input_kind||'');const input=choices.length?`<select data-action-input="${esc(id)}">${choices.map(c=>`<option value="${esc(c.value||c.label||'')}">${esc(c.label||c.value||'')}</option>`).join('')}</select>`:(inputKind==='text'||row.kind==='clarification_required'?`<textarea data-action-input="${esc(id)}" placeholder="${esc(row.prompt_text||'')}"></textarea>`:'');return `<article class="action-card"><div class="title-row"><strong>${esc(t(row.manual_kind||'provider_permission'))}</strong>${chip(row.provider||row.kind||'provider',row.kind==='clarification_required'?'info':'warn')}</div>${clampedText(`provider-action-${slug(id||runId)}`,row.prompt_text||row.why||row.stage_id||runId)}${input}<div class="actions"><button type="button" class="primary" data-provider-respond="${esc(id)}" data-run="${esc(runId)}">${esc(input?t('submit_continue'):t('allow_continue'))}</button><button type="button" class="danger" data-provider-deny="${esc(id)}">${esc(t('dismiss'))}</button>${row.attach_command?`<button type="button" data-copy="${esc(row.attach_command)}">${esc(t('copy_attach'))}</button>`:''}</div></article>`}
function renderFeedbackCard(row){const runId=String(row.run_id||row.task_id||'');return `<article class="action-card"><div class="title-row"><strong>${esc(t('plan_feedback'))}</strong>${chip(row.stage_id||row.kind||'plan','info')}</div>${clampedText(`feedback-${slug(runId)}`,row.why||row.headline||row.task_title||runId)}<textarea data-generic-feedback="${esc(runId)}" placeholder="${esc(t('feedback_placeholder'))}"></textarea><div class="actions"><button type="button" class="primary" data-submit-feedback="${esc(runId)}">${esc(t('feedback'))}</button>${runId?`<button type="button" data-task-stop="${esc(runId)}">${esc(t('stop'))}</button>`:''}</div></article>`}
function renderRecoveryCard(row){const runId=String(row.run_id||row.task_id||'');return `<article class="action-card"><div class="title-row"><strong>${esc(t('failure_gate'))}</strong>${chip(row.stage_id||row.kind||'recovery','bad')}</div>${clampedText(`recovery-${slug(runId)}`,row.why||row.headline||row.task_title||runId)}<div class="actions">${runId?`<button type="button" class="primary" data-task-continue="${esc(runId)}">${esc(t('retry'))}</button><button type="button" class="danger" data-task-stop="${esc(runId)}">${esc(t('stop'))}</button><button type="button" data-task-rollback="${esc(runId)}">${esc(t('rollback'))}</button>`:''}</div></article>`}
function renderConfig(overview){
  setText('workflow-templates-title',t('workflow_templates'));
  setText('role-cli-title',t('role_cli_mapping'));
  const workflows=workflowRows(overview),routes=overview.global_config?.role_routes||[];
  document.getElementById('workflow-templates').innerHTML=`<div class="stack">${folded('config-workflows',workflows,renderWorkflowTemplateCard,`<div class="empty">${esc(t('empty_config'))}</div>`)}</div>`;
  document.getElementById('role-cli').innerHTML=`<div class="stack">${folded('config-role-routes',routes,renderRoleRouteCard,`<div class="empty">${esc(t('empty_config'))}</div>`)}</div>`;
}
function stageTone(stage){const kind=String(stage.actor_kind||stage.type||'');if(kind==='human_gate')return'human';if(kind==='delivery_gate')return'delivery';if(kind==='model_role'||stage.model_role||stage.role)return'model';return''}
function renderWorkflowTemplateCard(row){const stages=row.stages||row.phases||[],roles=row.model_roles||row.roles||[],human=row.human_gates||[],delivery=row.delivery_gates||[],best=row.best_for||[],key=slug(row.name||row.id);const stageHtml=(stages||[]).map(s=>typeof s==='string'?`<span class="stage-pill">${esc(s)}</span>`:`<span class="stage-pill ${stageTone(s)}" title="${esc(s.actor_label||s.type||'')}">${esc(s.id||s.stage||'-')}</span>`).join('')||chip(t('none'));return `<article class="workflow-card"><div class="title-row"><strong>${esc(row.name||row.id)}</strong>${chip(`${esc(row.stage_count||stages.length||0)} ${t('stage')}`,'info')}</div>${clampedText(`workflow-${key}-description`,row.description||row.notes||'','ability')}${best.length?`<div><h3>${esc(t('best_for'))}</h3>${clampedText(`workflow-${key}-best`,best.join('\\n'),'ability')}</div>`:''}<div><h3>${esc(t('workflow_stage_flow'))}</h3><div class="stage-rail">${stageHtml}</div></div><div class="chips">${roles.length?roles.map(role=>chip(role,'info')).join(''):chip(t('none'))}</div><div class="chips">${human.length?human.map(g=>chip(`${t('human_gates')}: ${g.stage||g.type}`,'warn')).join(''):''}${delivery.length?delivery.map(g=>chip(`${t('delivery_gates')}: ${g.stage}`,'good')).join(''):''}</div></article>`}
function renderRoleRouteCard(row){const tone=row.readiness==='ready'?'good':row.readiness==='partial'?'warn':row.readiness==='unavailable'?'bad':'info',key=slug(row.role||row.label);return `<article class="route-card"><div class="title-row"><strong>${esc(row.label||row.role)}</strong>${chip(row.readiness||t('none'),tone)}</div>${clampedText(`route-${key}-ability`,row.ability||'','ability')}<div class="task-meta"><div>${esc(t('configured_provider'))}: ${esc(row.configured_provider||'auto')}</div><div>${esc(t('fallback_provider'))}: ${esc(row.fallback_provider||'-')}</div><div>${esc(t('effective_provider'))}: ${esc(row.provider||'-')}</div><div>${esc(t('readiness'))}: ${esc(row.readiness||'-')}</div></div>${clampedText(`route-${key}-setup`,`${t('setup_hint')}: ${row.setup_hint||'muxdev setup'}`)}${clampedText(`route-${key}-doctor`,`${t('doctor_hint')}: ${row.doctor_hint||'muxdev provider doctor'}`)}</article>`}
function setView(view){state.view=view;document.querySelectorAll('[data-view-button]').forEach(b=>b.classList.toggle('active',b.dataset.viewButton===view));document.querySelectorAll('.view').forEach(s=>s.classList.toggle('active',s.id===`view-${view}`))}
async function copyAttach(taskId,agent){const payload=await api(`/tasks/${encodeURIComponent(taskId)}/attach-command?agent=${encodeURIComponent(agent||'implementer')}`),handoff=payload.handoff||{},command=Array.isArray(handoff.command)?handoff.command.join(' '):handoff.command;await copyText(command||handoff.path||'')}async function copyText(text){if(!text)return;await navigator.clipboard.writeText(String(text));setText('refresh-state',t('copied'))}
async function refresh(){try{setText('refresh-state',t('loading'));const overview=await api('/dashboard/overview?include_global_config=true');state.overview=overview;if(state.focusTaskId){const task=allTasks(overview).find(item=>taskId(item)===state.focusTaskId);if(task?.project_id)state.selectedProjectId=task.project_id}renderOverview(overview);renderProjects(overview);renderConfig(overview);setText('refresh-state',`${t('last_refresh')}: ${new Date().toLocaleTimeString()}`)}catch(error){setText('refresh-state',t('daemon_offline'));document.getElementById('action-list').innerHTML=`<div class="empty">${esc(error.message||error)}</div>`}}
document.addEventListener('click',async event=>{const target=event.target.closest('button,a');if(!target)return;try{if(target.dataset.viewButton)setView(target.dataset.viewButton);if(target.dataset.fold){state.expanded[target.dataset.fold]=!state.expanded[target.dataset.fold];renderOverview(state.overview||{});renderProjects(state.overview||{});renderConfig(state.overview||{});return}if(target.dataset.clamp){state.expanded[target.dataset.clamp]=!state.expanded[target.dataset.clamp];renderOverview(state.overview||{});renderProjects(state.overview||{});renderConfig(state.overview||{});return}if(target.dataset.project){state.selectedProjectId=target.dataset.project;renderProjects(state.overview||{})}if(target.id==='refresh-button')await refresh();if(target.dataset.copy)await copyText(target.dataset.copy);if(target.dataset.copyAttach)await copyAttach(target.dataset.copyAttach,target.dataset.agent);if(target.dataset.approve)await post(`/approvals/${encodeURIComponent(target.dataset.approve)}/approve`);if(target.dataset.deny)await post(`/approvals/${encodeURIComponent(target.dataset.deny)}/deny`);if(target.dataset.feedback){const text=document.querySelector(`[data-feedback-text="${CSS.escape(target.dataset.feedback)}"]`)?.value||'';await post(`/approvals/${encodeURIComponent(target.dataset.feedback)}/feedback-and-continue`,{feedback:text})}if(target.dataset.submitFeedback){const runId=target.dataset.submitFeedback,text=document.querySelector(`[data-generic-feedback="${CSS.escape(runId)}"]`)?.value||'';await post('/feedback',{kind:'manual_feedback',source:'dashboard',content:text,run_id:runId,auto_submit:true})}if(target.dataset.providerRespond){const actionId=target.dataset.providerRespond,runId=target.dataset.run||'latest',input=document.querySelector(`[data-action-input="${CSS.escape(actionId)}"]`),value=input?.value||'',body=input?(input.tagName==='SELECT'?{choice:value}:{text:value}):undefined;await post(`/tasks/${encodeURIComponent(runId)}/actions/${encodeURIComponent(actionId)}/${body?'respond-and-continue':'handled-and-continue'}`,body)}if(target.dataset.providerDeny)await post(`/provider-actions/${encodeURIComponent(target.dataset.providerDeny)}/dismiss`);if(target.dataset.taskStop)await post(`/tasks/${encodeURIComponent(target.dataset.taskStop)}/stop`);if(target.dataset.taskContinue)await post(`/tasks/${encodeURIComponent(target.dataset.taskContinue)}/continue`);if(target.dataset.taskRollback)await post(`/tasks/${encodeURIComponent(target.dataset.taskRollback)}/rollback`)}catch(error){setText('refresh-state',error.message||String(error))}})
function initLabels(){document.querySelector('[data-view-button="overview"]').textContent=t('overview');document.querySelector('[data-view-button="projects"]').textContent=t('projects');document.querySelector('[data-view-button="config"]').textContent=t('config');setText('refresh-button',t('refresh'))}
initLabels();refresh();setInterval(refresh,5000);try{const socket=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/events`);socket.onmessage=()=>refresh()}catch(error){}
  </script>
</body>
</html>
"""
