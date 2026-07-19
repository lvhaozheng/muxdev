"""No-build, task-first dashboard for the local muxdev daemon."""

from __future__ import annotations

import json
import secrets


def normalize_dashboard_lang(lang: str | None) -> str:
    return "en" if str(lang or "").lower().startswith("en") else "zh-CN"


def render_minimal_dashboard_html(task_id: str | None = None, lang: str | None = None, *, nonce: str | None = None) -> str:
    language = normalize_dashboard_lang(lang)
    text = {
        "zh-CN": {
            "title": "muxdev 可信任务控制台", "subtitle": "从任务路由到签名交付，一条可解释、可恢复、可验签的主路径。",
            "tasks": "任务", "needs_action": "需要处理", "recent_delivery": "最近交付", "health": "系统健康",
            "next": "下一步", "route": "为什么选择这个 Agent", "timeline": "执行与恢复时间线", "review": "异构评审",
            "trust": "认证、隔离与交付信任", "deliverables": "测试与交付物", "advanced": "高级诊断",
            "no_tasks": "还没有任务。先运行 muxdev demo --mode replay，或提交一个真实任务。", "loading": "正在读取可信状态…",
            "replay": "播放可信 Replay", "live": "启动 managed Live Demo", "simulation": "模拟 / 回放",
            "provider": "主 Agent", "reviewer": "Reviewer", "status": "状态", "risk": "风险", "evidence": "Evidence",
            "attestation": "交付证明", "warnings": "风险提示", "refresh": "刷新", "feedback": "保留中的反馈草稿",
        },
        "en": {
            "title": "muxdev Trusted Task Control", "subtitle": "One explainable, recoverable and verifiable path from routing to signed delivery.",
            "tasks": "Tasks", "needs_action": "Needs action", "recent_delivery": "Recent delivery", "health": "System health",
            "next": "Next action", "route": "Why this agent", "timeline": "Execution and recovery timeline", "review": "Heterogeneous review",
            "trust": "Certification, isolation and delivery trust", "deliverables": "Tests and deliverables", "advanced": "Advanced diagnostics",
            "no_tasks": "No tasks yet. Run muxdev demo --mode replay or submit a real task.", "loading": "Loading trusted state…",
            "replay": "Play trusted replay", "live": "Start managed live demo", "simulation": "SIMULATION / REPLAY",
            "provider": "Main agent", "reviewer": "Reviewer", "status": "Status", "risk": "Risk", "evidence": "Evidence",
            "attestation": "Attestation", "warnings": "Warnings", "refresh": "Refresh", "feedback": "Preserved feedback draft",
        },
    }[language]
    csp_nonce = nonce or secrets.token_urlsafe(18)
    return _HTML.replace("__LANG__", language).replace("__TASK_ID__", _json(task_id or "")).replace("__TEXT__", _json(text)).replace("__NONCE__", csp_nonce)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


_HTML = r'''<!doctype html>
<html lang="__LANG__">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>muxdev Dashboard</title>
  <style nonce="__NONCE__">
    :root{color-scheme:light;--bg:#f4f6f8;--panel:#fff;--ink:#14212b;--muted:#62717c;--line:#dbe2e7;--accent:#126b61;--accent-soft:#e8f5f2;--warn:#9a5a00;--bad:#a53030;--good:#187443;--shadow:0 10px 28px rgba(25,40,50,.07)}
    *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}button,input{font:inherit}button{cursor:pointer}
    header{position:sticky;top:0;z-index:3;display:flex;justify-content:space-between;gap:20px;align-items:center;padding:18px 28px;background:rgba(255,255,255,.95);border-bottom:1px solid var(--line);backdrop-filter:blur(10px)}
    h1,h2,h3,p{margin:0}h1{font-size:20px}h2{font-size:16px}h3{font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}.subtitle{color:var(--muted);margin-top:3px}.actions{display:flex;gap:8px;flex-wrap:wrap}
    button{border:1px solid var(--line);border-radius:8px;background:#fff;color:var(--ink);padding:8px 11px}button.primary{background:var(--accent);color:#fff;border-color:var(--accent)}button:focus-visible,input:focus-visible{outline:3px solid #8fd1c8;outline-offset:2px}
    main{display:grid;grid-template-columns:minmax(260px,340px) minmax(0,1fr);gap:18px;max-width:1500px;margin:auto;padding:20px}.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow)}
    .sidebar{min-height:calc(100vh - 105px);overflow:hidden}.panel-head{display:flex;justify-content:space-between;align-items:center;padding:14px 16px;border-bottom:1px solid var(--line)}#task-list{display:grid;max-height:calc(100vh - 180px);overflow:auto}
    .task{border:0;border-bottom:1px solid var(--line);border-radius:0;text-align:left;padding:13px 16px;background:#fff}.task:hover,.task.active{background:var(--accent-soft)}.task strong{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.meta{color:var(--muted);font-size:12px;margin-top:4px}.chips{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}.chip{font-size:11px;padding:2px 7px;border-radius:999px;background:#edf1f3;color:var(--muted)}.chip.good{background:#e7f5ec;color:var(--good)}.chip.warn{background:#fff2da;color:var(--warn)}
    .story{display:grid;gap:14px}.hero{padding:20px}.hero-top{display:flex;justify-content:space-between;gap:16px}.hero h2{font-size:22px}.phase-rail{display:grid;grid-template-columns:repeat(6,1fr);gap:5px;margin-top:18px}.phase{height:7px;border-radius:99px;background:#e4e9ec}.phase.done,.phase.current{background:var(--accent)}.phase-labels{display:grid;grid-template-columns:repeat(6,1fr);gap:5px;color:var(--muted);font-size:10px;margin-top:5px}
    .next{margin-top:15px;padding:12px;border-radius:9px;background:var(--accent-soft);display:flex;justify-content:space-between;align-items:center;gap:12px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.section{padding:17px}.section h3{margin-bottom:11px}.kv{display:grid;grid-template-columns:minmax(100px,.7fr) 1.3fr;gap:7px 12px}.kv dt{color:var(--muted)}.kv dd{margin:0;overflow-wrap:anywhere}.timeline{display:grid;gap:8px;max-height:390px;overflow:auto}.event{border-left:3px solid var(--line);padding:6px 10px}.event.delivery{border-color:var(--good)}.event.review{border-color:#7565b7}.event.execution{border-color:#3f75aa}.warning{color:var(--warn);background:#fff7e8;border:1px solid #f2d7a5;padding:9px;border-radius:8px;margin-top:7px}.empty{padding:30px;color:var(--muted);text-align:center}
    details{border-top:1px solid var(--line);padding-top:10px}textarea{width:100%;min-height:72px;border:1px solid var(--line);border-radius:8px;padding:9px;resize:vertical}.watermark{position:fixed;right:12px;bottom:10px;padding:5px 9px;background:#7f1d1d;color:#fff;border-radius:6px;font-weight:700;display:none}.watermark.show{display:block}
    @media(max-width:900px){header{align-items:flex-start;padding:14px;flex-direction:column}main{grid-template-columns:1fr;padding:12px}.sidebar{min-height:auto}#task-list{max-height:300px}.grid{grid-template-columns:1fr}.phase-labels{display:none}}
  </style>
</head>
<body data-task-id=__TASK_ID__>
<header><div><h1 id="title"></h1><p class="subtitle" id="subtitle"></p></div><div class="actions"><button id="replay"></button><button id="live" class="primary"></button><button id="refresh"></button></div></header>
<main>
  <aside class="panel sidebar"><div class="panel-head"><h2 id="tasks-title"></h2><span id="task-count" class="chip">0</span></div><div id="task-list" aria-live="polite"></div></aside>
  <section id="story" class="story" aria-live="polite"><div class="panel empty" id="loading"></div></section>
</main>
<div id="watermark" class="watermark"></div>
<script nonce="__NONCE__">
const T=__TEXT__,PHASES=['submitted','routed','executing','reviewing','verifying','attested'];
const state={taskId:document.body.dataset.taskId||'',tasks:[],story:null,draft:'',wsRetry:1000,poll:null};
const $=id=>document.getElementById(id),esc=value=>String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
async function api(path,options={}){const response=await fetch('/api'+path,{credentials:'same-origin',headers:{'Content-Type':'application/json',...(options.headers||{})},...options});if(!response.ok)throw new Error(`${response.status} ${await response.text()}`);return response.headers.get('content-type')?.includes('json')?response.json():response.text()}
function initText(){$('title').textContent=T.title;$('subtitle').textContent=T.subtitle;$('tasks-title').textContent=T.tasks;$('replay').textContent=T.replay;$('live').textContent=T.live;$('refresh').textContent=T.refresh;$('loading').textContent=T.loading;$('watermark').textContent=T.simulation}
async function loadTasks(){const payload=await api('/dashboard/tasks?limit=100');state.tasks=payload.items||[];$('task-count').textContent=payload.total||0;if(!state.taskId&&state.tasks.length)state.taskId=state.tasks[0].run_id;renderTasks();if(state.taskId)await loadStory(state.taskId);else $('story').innerHTML=`<div class="panel empty">${esc(T.no_tasks)}</div>`}
function renderTasks(){$('task-list').innerHTML=state.tasks.map(row=>`<button class="task ${row.run_id===state.taskId?'active':''}" data-id="${esc(row.run_id)}"><strong>${esc(row.title||row.run_id)}</strong><div class="meta">${esc(row.provider||'-')} · ${esc(row.current_stage||'-')}</div><div class="chips"><span class="chip">${esc(row.status||'unknown')}</span><span class="chip ${row.evidence?.label==='trusted'?'good':''}">${esc(row.evidence?.label||'collecting')}</span></div></button>`).join('');document.querySelectorAll('.task').forEach(button=>button.addEventListener('click',()=>{state.taskId=button.dataset.id;renderTasks();loadStory(state.taskId)}))}
async function loadStory(id){state.draft=$('feedback-draft')?.value||state.draft;const story=await api(`/tasks/${encodeURIComponent(id)}/story`);state.story=story;renderStory(story)}
function renderStory(s){const p=s.progress||{},route=s.route||{},review=s.review||{},trust=s.trust||{},att=trust.attestation||{},events=s.timeline?.items||[],next=p.next_action||{};const phaseIndex=Math.max(0,PHASES.indexOf(p.current_phase));$('story').innerHTML=`
<section class="panel hero"><div class="hero-top"><div><h2>${esc(s.run?.title||s.run?.run_id)}</h2><div class="chips"><span class="chip">${esc(s.run?.status)}</span><span class="chip">${esc(T.risk)} ${esc(s.run?.risk_level)}</span><span class="chip">${esc(s.run?.delivery_mode)}</span></div></div><strong>${esc(p.percent||0)}%</strong></div><div class="phase-rail">${PHASES.map((x,i)=>`<span class="phase ${i<phaseIndex?'done':i===phaseIndex?'current':''}"></span>`).join('')}</div><div class="phase-labels">${PHASES.map(x=>`<span>${esc(x)}</span>`).join('')}</div><div class="next"><div><h3>${esc(T.next)}</h3><strong>${esc(next.label||'-')}</strong></div><span class="chip">${esc(next.kind||'observe')}</span></div></section>
<div class="grid"><section class="panel section"><h3>${esc(T.route)}</h3>${route?`<dl class="kv"><dt>${esc(T.provider)}</dt><dd>${esc(route.selected_main_provider||'-')}</dd><dt>${esc(T.reviewer)}</dt><dd>${esc(route.selected_reviewer_provider||'-')}</dd><dt>Reasons</dt><dd>${esc((route.reason_codes||[]).join(', ')||'-')}</dd><dt>Excluded</dt><dd>${esc((route.candidate_exclusions||[]).map(x=>x.provider+': '+(x.exclusion_codes||[]).join('/')).join('; ')||'-')}</dd></dl>`:'<div class="meta">pending</div>'}</section>
<section class="panel section"><h3>${esc(T.review)}</h3><dl class="kv"><dt>${esc(T.status)}</dt><dd>${esc(review.status||'not required')}</dd><dt>${esc(T.reviewer)}</dt><dd>${esc(review.reviewer_provider||'-')}</dd><dt>Verdict</dt><dd>${esc(review.verdict||'-')}</dd><dt>Waiver</dt><dd>${esc(review.waiver_approval_id||'none')}</dd></dl></section></div>
<div class="grid"><section class="panel section"><h3>${esc(T.timeline)}</h3><div class="timeline">${events.map(e=>`<article class="event ${esc(e.category)}"><strong>${esc(e.type)}</strong><div>${esc(e.summary)}</div><div class="meta">${esc(e.created_at||'')}</div></article>`).join('')||'<div class="meta">no events</div>'}</div></section>
<section class="panel section"><h3>${esc(T.trust)}</h3><dl class="kv"><dt>Certification</dt><dd>${esc(trust.provider_attempt?.certification_id||'-')}</dd><dt>Isolation</dt><dd>${esc(trust.provider_attempt?.isolation_mode||'-')}</dd><dt>${esc(T.evidence)}</dt><dd>${esc(trust.evidence?.label||'-')} ${esc(trust.evidence?.confidence??'')}</dd><dt>${esc(T.attestation)}</dt><dd>${esc(att.status||'-')} · ${esc(att.identity_status||'-')}</dd></dl>${(s.warnings||[]).map(w=>`<div class="warning">${esc(w)}</div>`).join('')}</section></div>
<section class="panel section"><h3>${esc(T.deliverables)}</h3><dl class="kv"><dt>Tests</dt><dd>${esc(s.deliverables?.tests?.passed||0)} / ${esc(s.deliverables?.tests?.total||0)}</dd><dt>Artifacts</dt><dd>${esc(s.deliverables?.artifact_count||0)}</dd><dt>Report</dt><dd><a href="${esc(s.deliverables?.links?.report||'#')}">API</a></dd><dt>Offline verify</dt><dd><a href="${esc(s.deliverables?.links?.attestation||'#')}">DeliveryAttestation</a></dd></dl><details data-fold="advanced"><summary>${esc(T.advanced)}</summary><label for="feedback-draft">${esc(T.feedback)}</label><textarea id="feedback-draft" data-draft-key="feedback">${esc(state.draft)}</textarea><pre>${esc(JSON.stringify({execution:s.execution,links:s.links},null,2))}</pre></details></section>`}
async function replay(){const scenario=await api('/demo/scenarios/trusted-delivery-v1');$('watermark').classList.add('show');const steps=scenario.steps||[];$('story').innerHTML=`<section class="panel hero"><h2>${esc(scenario.title)}</h2><div class="chips"><span class="chip warn">${esc(scenario.label)}</span><span class="chip">${esc(scenario.fixture_hash)}</span></div></section><section class="panel section"><h3>${esc(T.timeline)}</h3><div class="timeline">${steps.map(step=>`<article class="event ${esc(step.phase)}"><strong>${esc(step.title)}</strong><div>${esc(step.summary)}</div><div class="meta">${esc(step.seconds)}s</div></article>`).join('')}</div></section>`}
async function live(){const payload=await api('/demo/scenarios/trusted-delivery-v1/run',{method:'POST',body:'{}'});$('watermark').classList.add('show');state.taskId=payload.run?.task_id||payload.run?.run_id||'';await loadTasks()}
function connect(){const scheme=location.protocol==='https:'?'wss':'ws',socket=new WebSocket(`${scheme}://${location.host}/api/events`);socket.onopen=()=>state.wsRetry=1000;socket.onmessage=event=>{try{const msg=JSON.parse(event.data);if(msg.type==='task_story_invalidated'&&msg.run_id===state.taskId)loadStory(state.taskId);if(['task_submitted','benchmark_event'].includes(msg.type))loadTasks()}catch{}};socket.onclose=()=>setTimeout(connect,state.wsRetry=Math.min(state.wsRetry*2,15000))}
$('refresh').addEventListener('click',loadTasks);$('replay').addEventListener('click',()=>replay().catch(showError));$('live').addEventListener('click',()=>live().catch(showError));function showError(error){$('story').innerHTML=`<div class="panel warning">${esc(error.message||error)}</div>`}
initText();loadTasks().catch(showError);connect();state.poll=setInterval(()=>loadTasks().catch(()=>{}),15000);
</script>
</body></html>'''
