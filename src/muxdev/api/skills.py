"""Product-managed Skill sources, bindings, activation, and usage APIs."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ..services.skills.catalog import (
    SkillCatalogError,
    audit_skill_source,
    find_catalog_skill,
    import_skill_source,
)
from ..services.skills.product import (
    activate_product_skill,
    bind_product_skill,
    product_skill_catalog,
)
from ..services.skills.remote_catalog import (
    RemoteSkillError,
    RemoteSkillRateLimitError,
    download_remote_skill,
    search_remote_skills,
)
from ..storage import ControlStore
from ..workbench import utc_now


router = APIRouter(prefix="/api/v2")


class SkillSourceCreateV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=2000)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    mode: Literal["connect", "copy"] = "connect"


class SkillSourceUpdateV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trust_state: Literal[
        "user_trusted",
        "org_trusted",
        "untrusted",
        "needs_review",
        "quarantined",
    ] | None = None
    enabled: bool | None = None
    auto_enable: bool | None = None


class SkillBindingV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["project", "conversation", "assignment"] = "project"
    conversation_id: str | None = Field(default=None, max_length=100)
    assignment_id: str | None = Field(default=None, max_length=100)
    required: bool = False
    enabled: bool = True


class RemoteSkillCatalogItemV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_id: str
    provider: Literal["openai", "anthropic"]
    provider_label: str
    publisher: str
    repository: str
    name: str
    description: str
    path: str
    ref: str
    commit_sha: str
    source_url: str
    license: str | None = None
    file_count: int
    script_count: int
    trust: Literal["publisher_verified"]


class RemoteSkillSearchV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["muxdev.remote-skills.v1"]
    query: str
    provider: Literal["all", "openai", "anthropic"]
    items: list[RemoteSkillCatalogItemV1]
    next_cursor: str | None = None
    total: int


class RemoteSkillImportV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)
    ref: str | None = Field(default=None, min_length=1, max_length=200)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)


def _workspace(request: Request, project_id: str) -> Path:
    try:
        return request.app.state.workbench.workspace(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


def _source_response(item: dict[str, Any]) -> dict[str, Any]:
    return {
        **item,
        "enabled": bool(item.get("enabled")),
        "auto_enable": bool(item.get("auto_enable")),
    }


@router.get("/skill-sources")
def list_skill_sources(request: Request) -> list[dict[str, Any]]:
    return [
        _source_response(item)
        for item in request.app.state.workbench.store.list_skill_sources(
            include_disconnected=True
        )
    ]


@router.get("/skills/remote/search", response_model=RemoteSkillSearchV1)
def search_remote_skill_catalog(
    request: Request,
    q: str = Query(default="", max_length=120),
    provider: Literal["all", "openai", "anthropic"] = "all",
    cursor: str | None = Query(default=None, max_length=20),
    limit: int = Query(default=20, ge=1, le=50),
) -> dict[str, Any]:
    try:
        return search_remote_skills(
            q,
            provider=provider,
            cursor=cursor,
            page_size=limit,
        )
    except RemoteSkillRateLimitError as exc:
        raise HTTPException(
            429,
            {
                "code": "github_rate_limited",
                "message": str(exc),
                "retry_after_ms": exc.retry_after_ms,
            },
            headers={"Retry-After": str(max(1, exc.retry_after_ms // 1000))},
        ) from exc
    except (RemoteSkillError, FileNotFoundError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/skill-sources/import-remote", status_code=201)
def import_remote_skill_source(
    body: RemoteSkillImportV1,
    request: Request,
) -> dict[str, Any]:
    store = request.app.state.workbench.store
    data_root = store.path.parent.resolve()
    data_root.mkdir(parents=True, exist_ok=True)
    staging_root = (
        data_root / "remote-skill-staging" / f"download-{uuid4().hex}"
    )
    try:
        downloaded = download_remote_skill(
            body.url,
            staging_root / "source",
            ref=body.ref,
        )
        location = dict(downloaded["location"])
        identity = hashlib.sha256(
            (
                f"{location['repository']}\0{location['path']}\0"
                f"{location['commit_sha']}"
            ).encode("utf-8")
        ).hexdigest()[:20]
        source_id = f"skillsrc-remote-{identity}"
        existing = store.get_skill_source(source_id)
        if existing:
            return _source_response(existing)
        path = import_skill_source(
            Path(str(downloaded["audit"]["root"])),
            data_root,
            source_id,
        )
        audit = audit_skill_source(path)
    except RemoteSkillRateLimitError as exc:
        raise HTTPException(
            429,
            {
                "code": "github_rate_limited",
                "message": str(exc),
                "retry_after_ms": exc.retry_after_ms,
            },
            headers={"Retry-After": str(max(1, exc.retry_after_ms // 1000))},
        ) from exc
    except (RemoteSkillError, SkillCatalogError, FileNotFoundError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
        try:
            staging_root.parent.rmdir()
        except OSError:
            pass

    display_name = (
        body.display_name
        or Path(str(location.get("path") or "")).name
        or str(location["repository"])
    )
    stored = store.put_skill_source(
        {
            "source_id": source_id,
            "kind": "managed",
            "display_name": display_name,
            "path": str(path),
            "mode": "copy",
            "trust_state": "needs_review",
            "enabled": False,
            "auto_enable": False,
            "revision": audit["revision"],
            "status": "connected",
            "last_scanned_at": utc_now(),
            "metadata": {
                "origin_url": location["source_url"],
                "repository": location["repository"],
                "repository_path": location["path"],
                "requested_ref": location["requested_ref"],
                "commit_sha": location["commit_sha"],
                "license": downloaded.get("license"),
                "file_count": audit["file_count"],
                "size_bytes": audit["size_bytes"],
                "skill_count": audit["skill_count"],
                "files": audit["files"],
                "remote": True,
            },
        }
    )
    return _source_response(stored)


@router.post("/skill-sources", status_code=201)
def create_skill_source(
    body: SkillSourceCreateV1,
    request: Request,
) -> dict[str, Any]:
    origin = Path(body.path).expanduser().resolve()
    try:
        audit = audit_skill_source(origin)
    except SkillCatalogError as exc:
        raise HTTPException(422, str(exc)) from exc
    identity = hashlib.sha256(
        f"{body.mode}\0{origin}".encode("utf-8")
    ).hexdigest()[:20]
    source_id = f"skillsrc-{identity}"
    path = origin
    if body.mode == "copy":
        try:
            path = import_skill_source(
                origin,
                request.app.state.workbench.store.path.parent,
                source_id,
            )
            audit = audit_skill_source(path)
        except (OSError, SkillCatalogError) as exc:
            raise HTTPException(422, str(exc)) from exc
    stored = request.app.state.workbench.store.put_skill_source(
        {
            "source_id": source_id,
            "kind": "managed" if body.mode == "copy" else "custom",
            "display_name": body.display_name or origin.name,
            "path": str(path),
            "mode": body.mode,
            "trust_state": "needs_review",
            "enabled": False,
            "auto_enable": False,
            "revision": audit["revision"],
            "status": "connected",
            "last_scanned_at": utc_now(),
            "metadata": {
                "origin_path": str(origin) if body.mode == "copy" else None,
                "file_count": audit["file_count"],
                "size_bytes": audit["size_bytes"],
                "skill_count": audit["skill_count"],
                "files": audit["files"],
            },
        }
    )
    return _source_response(stored)


@router.patch("/skill-sources/{source_id}")
def update_skill_source(
    source_id: str,
    body: SkillSourceUpdateV1,
    request: Request,
) -> dict[str, Any]:
    current = request.app.state.workbench.store.get_skill_source(source_id)
    if not current:
        raise HTTPException(404, f"Skill source not found: {source_id}")
    trust = body.trust_state or str(current["trust_state"])
    enabled = body.enabled if body.enabled is not None else bool(current["enabled"])
    if enabled and trust in {"untrusted", "needs_review", "quarantined"}:
        raise HTTPException(409, "Review and trust the Skill source before enabling it")
    stored = request.app.state.workbench.store.update_skill_source(
        source_id,
        trust_state=body.trust_state,
        enabled=body.enabled,
        auto_enable=body.auto_enable,
    )
    return _source_response(stored)


@router.post("/skill-sources/{source_id}/rescan")
def rescan_skill_source(source_id: str, request: Request) -> dict[str, Any]:
    current = request.app.state.workbench.store.get_skill_source(source_id)
    if not current:
        raise HTTPException(404, f"Skill source not found: {source_id}")
    try:
        audit = audit_skill_source(Path(str(current["path"])))
    except SkillCatalogError as exc:
        raise HTTPException(422, str(exc)) from exc
    metadata = dict(current.get("metadata") or {})
    metadata.update(
        {
            "file_count": audit["file_count"],
            "size_bytes": audit["size_bytes"],
            "skill_count": audit["skill_count"],
            "files": audit["files"],
            "drifted": str(current["revision"]) != str(audit["revision"]),
        }
    )
    stored = request.app.state.workbench.store.update_skill_source(
        source_id,
        revision=str(audit["revision"]),
        metadata=metadata,
    )
    return _source_response(stored)


@router.delete("/skill-sources/{source_id}")
def disconnect_skill_source(source_id: str, request: Request) -> dict[str, Any]:
    try:
        stored = request.app.state.workbench.store.update_skill_source(
            source_id,
            enabled=False,
            status="disconnected",
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return _source_response(stored)


@router.get("/projects/{project_id}/skills")
def project_skills(project_id: str, request: Request) -> dict[str, Any]:
    workspace = _workspace(request, project_id)
    catalog = product_skill_catalog(
        workspace,
        workbench=request.app.state.workbench.store,
    )
    with ControlStore(workspace) as control:
        bindings = control.list_skill_bindings()
        usage = control.list_skill_usage()
    usage_count: dict[str, int] = {}
    for item in usage:
        name = str(item["qualified_name"])
        usage_count[name] = usage_count.get(name, 0) + 1
    return {
        "schema_version": "muxdev.skills-catalog.v1",
        "project_id": project_id,
        "catalog": [
            {**item, "usage_count": usage_count.get(str(item["qualified_name"]), 0)}
            for item in catalog
        ],
        "sources": [
            _source_response(item)
            for item in request.app.state.workbench.store.list_skill_sources(
                include_disconnected=True
            )
        ],
        "bindings": bindings,
        "usage": usage,
    }


@router.get("/projects/{project_id}/skills/{qualified_name}")
def project_skill_detail(
    project_id: str,
    qualified_name: str,
    request: Request,
) -> dict[str, Any]:
    workspace = _workspace(request, project_id)
    try:
        return find_catalog_skill(
            product_skill_catalog(
                workspace,
                workbench=request.app.state.workbench.store,
            ),
            qualified_name,
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except SkillCatalogError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.put("/projects/{project_id}/skills/{qualified_name}/binding")
def bind_project_skill(
    project_id: str,
    qualified_name: str,
    body: SkillBindingV1,
    request: Request,
) -> dict[str, Any]:
    workspace = _workspace(request, project_id)
    if body.scope != "project" and not body.conversation_id:
        raise HTTPException(422, "conversation_id is required for non-project Skill bindings")
    if body.scope == "assignment" and not body.assignment_id:
        raise HTTPException(422, "assignment_id is required for assignment Skill bindings")
    with ControlStore(workspace) as control:
        try:
            return bind_product_skill(
                workspace,
                control,
                qualified_name,
                workbench=request.app.state.workbench.store,
                scope=body.scope,
                conversation_id=body.conversation_id,
                assignment_id=body.assignment_id,
                required=body.required,
                enabled=body.enabled,
            )
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(409, str(exc)) from exc
        except SkillCatalogError as exc:
            raise HTTPException(422, str(exc)) from exc


@router.post(
    "/projects/{project_id}/conversations/{conversation_id}/skills/{qualified_name}/activate"
)
def activate_conversation_skill(
    project_id: str,
    conversation_id: str,
    qualified_name: str,
    request: Request,
) -> dict[str, Any]:
    workspace = _workspace(request, project_id)
    with ControlStore(workspace) as control:
        try:
            result = activate_product_skill(
                workspace,
                control,
                workbench=request.app.state.workbench.store,
                conversation_id=conversation_id,
                qualified_name=qualified_name,
            )
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(409, str(exc)) from exc
        except (RuntimeError, SkillCatalogError) as exc:
            raise HTTPException(422, str(exc)) from exc
    from ..runtime.agent_sessions import agent_session_manager

    manager = agent_session_manager(workspace)
    session_id = str(result["usage"].get("session_id") or "")
    manager.send_runtime(
        session_id,
        (
            f"[MuxDev verified Skill {result['qualified_name']} "
            f"{result['revision']}]\n{result['content']}"
        ),
    )
    return {key: value for key, value in result.items() if key != "content"}


@router.get("/projects/{project_id}/skill-usage")
def project_skill_usage(project_id: str, request: Request) -> list[dict[str, Any]]:
    workspace = _workspace(request, project_id)
    with ControlStore(workspace) as control:
        return control.list_skill_usage()


__all__ = [
    "RemoteSkillCatalogItemV1",
    "RemoteSkillImportV1",
    "RemoteSkillSearchV1",
    "SkillBindingV1",
    "SkillSourceCreateV1",
    "SkillSourceUpdateV1",
    "router",
]
