"""Legacy Run HTTP adapter plus the versioned Conversation Web surface."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager, contextmanager, suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..application import TaskService
from ..providers import detect_providers
from ..runtime import ConversationService, RunEngine
from ..runtime.collaboration_service import CollaborationService
from ..services.evidence_verify import verify_evidence_report
from ..services.router import ProviderRouter
from ..services.skills import scan_skills
from ..storage import ControlStore
from .auth import WebAuthMiddleware, router as auth_router
from .conversations import router as conversations_router
from .collaboration import router as collaboration_router
from .conversation_experience import router as conversation_experience_router
from .projects import router as projects_router
from .skills import router as skills_router
from ..workbench import WorkbenchRegistry


router = APIRouter()


class RunRequest(BaseModel):
    task: str = Field(min_length=1)
    workflow: str = "change"
    profile: str = "standard"
    provider: str | None = "mock"
    max_cost_usd: float = Field(default=0.5, gt=0)


class InteractionResponse(BaseModel):
    status: str
    response: str | None = None


class ResumeRequest(BaseModel):
    action: str = Field(default="auto", pattern="^(auto|fix-output|retry|switch-provider)$")


def _workspace(request: Request) -> Path:
    return request.app.state.workspace


@contextmanager
def _tasks(workspace: Path):
    engine = RunEngine(workspace)
    try:
        yield TaskService(engine, engine.store)
    finally:
        engine.store.close()


def _run_or_404(store: ControlStore, run_id: str) -> dict[str, Any]:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, f"run not found: {run_id}")
    return run


def _report_path(run: dict[str, Any], workspace: Path) -> Path:
    metadata = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
    return Path(str(metadata.get("run_dir") or workspace / ".muxdev" / "runs" / run["run_id"])) / "evidence-report.json"


def _conversation_app_response() -> FileResponse:
    path = Path(__file__).with_name("static") / "app" / "index.html"
    if not path.is_file():
        raise HTTPException(
            503,
            "Conversation SPA assets are not built; run npm run build:web",
        )
    return FileResponse(
        path,
        media_type="text/html",
        headers={
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; font-src 'self' data:; connect-src 'self' ws: wss:; "
                "frame-src http://127.0.0.1:* http://localhost:*; object-src 'none'; "
                "base-uri 'self'; frame-ancestors 'self'"
            )
        },
    )


@router.get("/")
def dashboard():
    return _conversation_app_response()


@router.get("/app")
def conversation_app() -> FileResponse:
    return _conversation_app_response()


@router.get("/projects/{project_id}")
def project_app(project_id: str, request: Request) -> FileResponse:
    if not request.app.state.workbench.store.get_project(project_id):
        raise HTTPException(404, f"project not found: {project_id}")
    request.app.state.workbench.store.update_project(project_id, touch=True)
    return _conversation_app_response()


@router.get("/health")
def health(request: Request) -> dict[str, object]:
    with ControlStore(_workspace(request)) as store:
        return {"status": "ok", "database": str(store.path), "tables": len(store.table_names())}


@router.post("/runs", status_code=201)
def create_run(body: RunRequest, request: Request) -> dict[str, object]:
    with _tasks(_workspace(request)) as tasks:
        result = tasks.create(
            body.task,
            provider=body.provider,
            workflow_name=body.workflow,
            profile=body.profile,
            max_cost_usd=body.max_cost_usd,
        )
    return {"run_id": result.run_id, "status": str(result.status), "evidence": str(result.report_path)}


@router.get("/runs")
def list_runs(request: Request, status: str | None = None, limit: int = Query(100, ge=1, le=1000)) -> list[dict[str, Any]]:
    with _tasks(_workspace(request)) as tasks:
        return tasks.list(status=status, limit=limit)


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request) -> dict[str, object]:
    with _tasks(_workspace(request)) as tasks:
        try:
            return tasks.get(run_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, f"run not found: {run_id}") from exc


@router.post("/runs/{run_id}/resume")
def resume_run(
    run_id: str,
    request: Request,
    background: BackgroundTasks,
    body: ResumeRequest | None = None,
) -> dict[str, str]:
    with ControlStore(_workspace(request)) as store:
        _run_or_404(store, run_id)
    action = body.action if body else "auto"
    background.add_task(_resume_background, _workspace(request), run_id, action)
    return {"run_id": run_id, "status": "queued", "action": action}


def _resume_background(workspace: Path, run_id: str, action: str) -> None:
    with _tasks(workspace) as tasks:
        tasks.resume(run_id, action=action)


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, request: Request) -> dict[str, str]:
    with _tasks(_workspace(request)) as tasks:
        tasks.cancel(run_id)
    return {"run_id": run_id, "status": "aborted"}


@router.get("/runs/{run_id}/events")
def get_events(run_id: str, request: Request, after: int = Query(0, ge=0)) -> list[dict[str, Any]]:
    with ControlStore(_workspace(request)) as store:
        _run_or_404(store, run_id)
        return store.events(run_id, after=after)


@router.get("/runs/{run_id}/artifacts")
def get_artifacts(run_id: str, request: Request) -> list[dict[str, Any]]:
    with ControlStore(_workspace(request)) as store:
        _run_or_404(store, run_id)
        return store.artifacts(run_id)


@router.get("/artifacts/{artifact_id}")
def download_artifact(artifact_id: str, request: Request) -> FileResponse:
    with ControlStore(_workspace(request)) as store:
        artifact = store.artifact(artifact_id)
    if not artifact or not Path(str(artifact["path"])).is_file():
        raise HTTPException(404, "artifact not found")
    return FileResponse(str(artifact["path"]), media_type=str(artifact["media_type"]), filename=str(artifact["name"]))


@router.get("/runs/{run_id}/evidence")
def get_evidence(run_id: str, request: Request) -> dict[str, Any]:
    with ControlStore(_workspace(request)) as store:
        run = _run_or_404(store, run_id)
    path = _report_path(run, _workspace(request))
    if not path.is_file():
        raise HTTPException(404, "evidence report not found")
    return json.loads(path.read_text(encoding="utf-8"))


@router.post("/runs/{run_id}/evidence/verify")
def verify_evidence(run_id: str, request: Request) -> dict[str, object]:
    with ControlStore(_workspace(request)) as store:
        run = _run_or_404(store, run_id)
        return verify_evidence_report(_report_path(run, _workspace(request)), store=store)


@router.get("/interactions")
def list_interactions(request: Request, run_id: str, pending_only: bool = False) -> list[dict[str, Any]]:
    with ControlStore(_workspace(request)) as store:
        _run_or_404(store, run_id)
        return store.interactions(run_id, pending_only=pending_only)


@router.post("/interactions/{interaction_id}/respond")
def respond_interaction(interaction_id: str, body: InteractionResponse, request: Request) -> dict[str, Any]:
    with _tasks(_workspace(request)) as tasks:
        try:
            return tasks.respond(interaction_id, status=body.status, response=body.response)
        except KeyError as exc:
            raise HTTPException(404, "interaction not found") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc


@router.get("/providers")
def list_providers() -> list[dict[str, object]]:
    return [item.to_dict() for item in detect_providers()]


@router.get("/skills")
def list_skills(request: Request) -> list[dict[str, object]]:
    return [item.to_dict() for item in scan_skills(_workspace(request), include_disabled=True)]


@router.get("/routing/{run_id}")
def explain_route(run_id: str, request: Request) -> dict[str, Any]:
    with ControlStore(_workspace(request)) as store:
        try:
            return ProviderRouter(store).explain(run_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc


@router.post("/routing/{run_id}/replay")
def replay_route(run_id: str, request: Request) -> dict[str, Any]:
    with ControlStore(_workspace(request)) as store:
        try:
            return ProviderRouter(store).replay(run_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc


def create_app(
    workspace: Path | None = None,
    *,
    workbench: WorkbenchRegistry | None = None,
    instance_id: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    require_auth: bool = False,
    pairing_code: str | None = None,
    trusted_origins: tuple[str, ...] = (),
) -> FastAPI:
    resolved_workspace = (workspace or Path.cwd()).resolve()
    registry = workbench or WorkbenchRegistry.local(resolved_workspace)
    initial_project = registry.register(resolved_workspace)

    async def schedule_capacity_queue() -> None:
        cursor = 0
        while True:
            projects = registry.store.list_projects()
            if projects:
                offset = cursor % len(projects)
                projects = projects[offset:] + projects[:offset]
                cursor += 1
            for project in projects:
                path = Path(str(project["path"]))
                if not path.is_dir():
                    continue
                engine = RunEngine(path)
                try:
                    service = CollaborationService(
                        ConversationService(engine, engine.store),
                        engine.store,
                    )
                    service.schedule_queued()
                    service.dispatch_pending_messages()
                finally:
                    engine.store.close()
            await asyncio.sleep(0.5)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        registry.reconcile()
        scheduler = asyncio.create_task(schedule_capacity_queue())
        try:
            yield
        finally:
            scheduler.cancel()
            with suppress(asyncio.CancelledError):
                await scheduler

    application = FastAPI(title="muxdev", version="4", lifespan=lifespan)
    application.state.workspace = resolved_workspace
    application.state.workbench = registry
    application.state.initial_project_id = str(initial_project["project_id"])
    application.state.instance_id = instance_id or f"daemon_{uuid4().hex}"
    application.state.host = host
    application.state.port = port
    application.state.require_auth = require_auth
    application.state.pairing_code = pairing_code
    application.state.trusted_origins = trusted_origins
    application.add_middleware(WebAuthMiddleware)
    application.mount(
        "/assets",
        StaticFiles(directory=Path(__file__).with_name("static")),
        name="assets",
    )
    application.include_router(router)
    application.include_router(auth_router)
    application.include_router(projects_router)
    application.include_router(conversations_router)
    application.include_router(collaboration_router)
    application.include_router(conversation_experience_router)
    application.include_router(skills_router)

    return application


def write_dashboard(workspace: Path, output: Path) -> Path:
    del workspace
    source = Path(__file__).with_name("static") / "app" / "index.html"
    output.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return output


def _dashboard_html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>muxdev 可信交付</title>
<style>
:root{color-scheme:light;--ink:#17202a;--muted:#667085;--line:#e4e7ec;--panel:#f8fafc;--pass:#067647;--pass-bg:#ecfdf3;--block:#b42318;--block-bg:#fef3f2;--wait:#b54708;--wait-bg:#fffaeb;--blue:#175cd3;--blue-bg:#eff8ff}
*{box-sizing:border-box}body{font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;max-width:1120px;margin:0 auto;padding:36px 28px;color:var(--ink);background:#fff}
h1{font-size:34px;line-height:1.2;margin:0 0 10px}header p{margin:0;color:var(--muted)}button{font:inherit}.notice{margin:22px 0;padding:12px 14px;border-radius:9px;background:var(--panel);color:var(--muted)}.notice.error{color:var(--block);background:var(--block-bg)}
.runs{display:grid;gap:16px;margin-top:24px}.run-card{border:1px solid var(--line);border-radius:14px;overflow:hidden;background:#fff;box-shadow:0 1px 2px rgba(16,24,40,.04)}.run-card.blocked{border-left:4px solid var(--block)}.run-card.completed{border-left:4px solid var(--pass)}
.card-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;padding:18px 20px;background:var(--panel);border-bottom:1px solid var(--line)}.run-id{font:600 13px ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere}.meta{margin-top:5px;color:var(--muted);font-size:13px}.badges{display:flex;gap:7px;flex-wrap:wrap;justify-content:flex-end}.badge{display:inline-block;padding:3px 9px;border-radius:999px;font-size:12px;font-weight:700}.PASS,.completed,.recovered{color:var(--pass);background:var(--pass-bg)}.BLOCKED,.blocked,.failed,.exhausted{color:var(--block);background:var(--block-bg)}.WAITING_HUMAN,.awaiting_approval,.running,.recovering{color:var(--wait);background:var(--wait-bg)}.needs_action{color:var(--blue);background:var(--blue-bg)}
.card-body{padding:20px}.eyebrow{margin:0 0 5px;text-transform:uppercase;letter-spacing:.06em;font-size:11px;font-weight:800;color:var(--muted)}.headline{font-size:18px;font-weight:750;margin:0}.details{margin:8px 0 0;padding-left:20px;color:#475467}.details li+li{margin-top:4px}.safe{margin:14px 0 0;padding:10px 12px;border-radius:8px;background:var(--panel);color:#475467}
.attempts{margin-top:18px;padding-top:16px;border-top:1px solid var(--line)}.attempts ol{margin:8px 0 0;padding-left:22px}.attempts li+li{margin-top:7px}.attempt-status{font-weight:700}.consequences{margin-top:14px;color:var(--muted);font-size:13px}.actions{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin-top:18px}.action{border:0;border-radius:8px;padding:9px 13px;background:var(--blue);color:#fff;font-weight:700;cursor:pointer}.action.secondary{border:1px solid var(--line);background:#fff;color:#344054}.action:disabled{opacity:.55;cursor:wait}.commands{display:grid;gap:7px;margin-top:10px}.command{padding:9px 11px;border-radius:8px;background:#101828;color:#f2f4f7;font:12px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere}.evidence-link{display:inline-block;margin-top:16px;color:var(--blue)}
.empty{padding:32px;border:1px dashed var(--line);border-radius:12px;color:var(--muted);text-align:center}@media(max-width:700px){body{padding:24px 16px}h1{font-size:28px}.card-head{display:block}.badges{justify-content:flex-start;margin-top:12px}}
</style>
</head>
<body>
<header><h1>muxdev 可信交付</h1><p>先自动修复；无法修复时，告诉你真正原因和唯一的下一步。</p></header>
<div id="notice" class="notice" role="status">正在加载 Run…</div>
<main id="runs" class="runs" aria-live="polite"></main>
<script>
const statusLabels={completed:"已完成",blocked:"需要处理",running:"执行中",awaiting_approval:"等待确认",aborted:"已取消",recovered:"已自动修复",needs_action:"需要你处理",exhausted:"自动修复已用尽",not_needed:"无需修复",succeeded:"成功",failed:"失败",started:"执行中"};
function node(tag,text,className){const item=document.createElement(tag);if(className)item.className=className;if(text!==undefined)item.textContent=text;return item}
function badge(value){return node("span",statusLabels[value]||value||"—",`badge ${value||""}`)}
function actionButton(run,action){const supported=["auto","fix-output","retry","switch-provider"].includes(action.action);const button=node("button",action.label,`action${supported?"":" secondary"}`);button.type="button";button.title=action.description||"";button.addEventListener("click",()=>supported?resumeRun(run.run_id,action.action,button):copyCommand(action.command,button));return button}
function renderCard(run,evidence,error){const recovery=evidence?.recovery,decision=evidence?.decision,diagnosis=recovery?.primary_failure;const card=node("article",undefined,`run-card ${run.status}`);const head=node("div",undefined,"card-head"),identity=node("div"),id=node("div",run.run_id,"run-id"),meta=node("div",`${run.workflow} · ${run.profile}`,"meta"),badges=node("div",undefined,"badges");identity.append(id,meta);badges.append(badge(run.status),badge(decision?.status),badge(recovery?.status));head.append(identity,badges);card.appendChild(head);const body=node("div",undefined,"card-body");
  if(error){body.append(node("p","无法读取这个 Run 的 Evidence："+error,"headline"))}else if(run.status==="completed"){body.append(node("p","交付已通过全部门禁。","headline"))}else{body.append(node("p","当前发生了什么","eyebrow"),node("p",diagnosis?.summary||"Run 尚未通过交付门禁。","headline"));if(diagnosis?.details?.length){const list=node("ul",undefined,"details");for(const text of diagnosis.details)list.appendChild(node("li",text));body.appendChild(list)}if(diagnosis?.consequences?.length)body.append(node("p","以下项目因上游失败而未完成："+diagnosis.consequences.join("、"),"consequences"))}
  if(recovery?.attempts?.length){const attempts=node("section",undefined,"attempts");attempts.appendChild(node("p",`系统已经尝试（${recovery.actions_used}/${recovery.max_actions}）`,"eyebrow"));const list=node("ol");for(const attempt of recovery.attempts){const item=node("li"),strong=node("span",statusLabels[attempt.status]||attempt.status,"attempt-status");item.append(strong,document.createTextNode(` · ${attempt.action} · ${attempt.provider||"当前 Provider"}`));if(attempt.feedback_summary?.length)item.appendChild(node("div",attempt.feedback_summary.join("；"),"meta"));list.appendChild(item)}attempts.appendChild(list);body.appendChild(attempts)}
  if(recovery?.workspace_message)body.appendChild(node("p",recovery.workspace_message,"safe"));if(recovery?.next_actions?.length){const actions=node("div",undefined,"actions"),commands=node("div",undefined,"commands");for(const action of recovery.next_actions){actions.appendChild(actionButton(run,action));commands.appendChild(node("div",action.command,"command"))}body.append(actions,commands)}const link=node("a","查看完整 Evidence Report","evidence-link");link.href=`/runs/${encodeURIComponent(run.run_id)}/evidence`;link.target="_blank";link.rel="noopener";body.appendChild(link);card.appendChild(body);return card}
async function resumeRun(runId,action,button){button.disabled=true;const original=button.textContent;button.textContent="正在提交…";try{const response=await fetch(`/runs/${encodeURIComponent(runId)}/resume`,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({action})});if(!response.ok)throw new Error(`HTTP ${response.status}`);button.textContent="已进入恢复队列";setTimeout(loadDashboard,900)}catch(error){button.disabled=false;button.textContent=original;showNotice("恢复请求失败："+(error instanceof Error?error.message:String(error)),true)}}
async function copyCommand(command,button){try{await navigator.clipboard.writeText(command);button.textContent="命令已复制"}catch{showNotice("无法访问剪贴板，请手动复制页面中的命令。",true)}}
function showNotice(message,error=false){const notice=document.querySelector("#notice");notice.hidden=false;notice.className=`notice${error?" error":""}`;notice.textContent=message}
async function loadDashboard(){const notice=document.querySelector("#notice"),body=document.querySelector("#runs");try{const response=await fetch("/runs");if(!response.ok)throw new Error(`Run 列表加载失败（HTTP ${response.status}）`);const runs=await response.json();body.replaceChildren();if(!runs.length){notice.hidden=true;body.appendChild(node("div","当前 workspace 还没有 Run。","empty"));return}notice.hidden=true;for(const run of runs){let evidence=null,error=null;try{const result=await fetch(`/runs/${encodeURIComponent(run.run_id)}/evidence`);if(!result.ok)throw new Error(`HTTP ${result.status}`);evidence=await result.json()}catch(cause){error=cause instanceof Error?cause.message:String(cause)}body.appendChild(renderCard(run,evidence,error))}}catch(error){showNotice(error instanceof Error?error.message:String(error),true)}}
loadDashboard();
</script>
</body>
</html>"""
