"""Fixed 18-endpoint HTTP adapter for the compact control plane."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from ..application import TaskService
from ..providers import detect_providers
from ..runtime import RunEngine
from ..services.evidence_verify import verify_evidence_report
from ..services.router import ProviderRouter
from ..services.skills import scan_skills
from ..storage import ControlStore


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


@router.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return _dashboard_html()


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
def resume_run(run_id: str, request: Request, background: BackgroundTasks) -> dict[str, str]:
    with ControlStore(_workspace(request)) as store:
        _run_or_404(store, run_id)
    background.add_task(_resume_background, _workspace(request), run_id)
    return {"run_id": run_id, "status": "queued"}


def _resume_background(workspace: Path, run_id: str) -> None:
    with _tasks(workspace) as tasks:
        tasks.resume(run_id)


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


def create_app(workspace: Path | None = None) -> FastAPI:
    application = FastAPI(title="muxdev", version="3")
    application.state.workspace = (workspace or Path.cwd()).resolve()
    application.include_router(router)
    return application


def write_dashboard(workspace: Path, output: Path) -> Path:
    del workspace
    output.write_text(_dashboard_html(), encoding="utf-8")
    return output


def _dashboard_html() -> str:
    return """<!doctype html><html><head><meta charset='utf-8'><title>muxdev</title>
<style>body{font:15px system-ui;max-width:1100px;margin:40px auto;color:#17202a}table{width:100%;border-collapse:collapse}th,td{padding:10px;border-bottom:1px solid #ddd;text-align:left}.PASS,.completed{color:#16794b}.BLOCKED,.blocked{color:#b42318}code{font-size:12px}</style></head>
<body><h1>muxdev 可信交付</h1><p>任务、人工动作、门禁和四维证据质量。</p><table><thead><tr><th>Run</th><th>Workflow</th><th>Profile</th><th>Status</th><th>Gate</th><th>Score</th></tr></thead><tbody id='runs'></tbody></table>
<script>fetch('/runs').then(r=>r.json()).then(async runs=>{for(const x of runs){let gate='—',score='—';try{const e=await fetch(`/runs/${x.run_id}/evidence`).then(r=>r.json());gate=e.decision.status;const s=e.decision.scorecard;score=`C ${s.completeness.numerator}/${s.completeness.denominator} · R ${s.reproducibility.numerator}/${s.reproducibility.denominator} · I ${s.integrity.numerator}/${s.integrity.denominator} · D ${s.independence.numerator}/${s.independence.denominator}`;}catch{}document.querySelector('#runs').insertAdjacentHTML('beforeend',`<tr><td><code>${x.run_id}</code></td><td>${x.workflow}</td><td>${x.profile}</td><td class='${x.status}'>${x.status}</td><td class='${gate}'>${gate}</td><td>${score}</td></tr>`);}})</script></body></html>"""
