"""Personal-device pairing and remote Web request protection."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from ..storage import ControlStore


COOKIE_NAME = "muxdev_session"
PUBLIC_PATHS = {"/", "/health", "/api/v1/auth/pair"}
router = APIRouter(prefix="/api/v1")


class PairRequest(BaseModel):
    pairing_code: str = Field(min_length=6, max_length=128)
    device_label: str = Field(default="浏览器设备", min_length=1, max_length=80)


class WebAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not bool(getattr(request.app.state, "require_auth", False)):
            return await call_next(request)
        if request.url.path in PUBLIC_PATHS or request.url.path.startswith("/assets/"):
            return await call_next(request)
        token = request.cookies.get(COOKIE_NAME)
        if not token or not _session_valid(request.app.state.workspace, token):
            return Response("authentication required", status_code=401)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not _origin_valid(request):
            return Response("origin is not trusted", status_code=403)
        return await call_next(request)


@router.post("/auth/pair")
def pair_device(body: PairRequest, request: Request, response: Response) -> dict[str, object]:
    expected = str(getattr(request.app.state, "pairing_code", ""))
    if not expected or not hmac.compare_digest(body.pairing_code, expected):
        raise HTTPException(401, "invalid or expired pairing code")
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(days=30)
    with ControlStore(request.app.state.workspace) as store:
        device = store.create_device(label=body.device_label)
        store.create_web_session(
            device_id=str(device["device_id"]),
            token_hash=_token_hash(token),
            expires_at=expires.isoformat(),
            metadata={"paired_from": request.client.host if request.client else "unknown"},
        )
    request.app.state.pairing_code = secrets.token_urlsafe(32)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=30 * 24 * 60 * 60,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return {"device": device, "expires_at": expires.isoformat()}


@router.post("/auth/logout")
def logout(request: Request, response: Response) -> dict[str, str]:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        with ControlStore(request.app.state.workspace) as store:
            store.revoke_web_session(_token_hash(token))
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"status": "logged_out"}


@router.get("/devices")
def devices(request: Request) -> list[dict[str, object]]:
    with ControlStore(request.app.state.workspace) as store:
        return store.list_devices()


@router.delete("/devices/{device_id}")
def revoke_device(device_id: str, request: Request) -> dict[str, str]:
    with ControlStore(request.app.state.workspace) as store:
        if not store.get_device(device_id):
            raise HTTPException(404, "device not found")
        store.revoke_device(device_id)
    return {"device_id": device_id, "status": "revoked"}


def _token_hash(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode()).hexdigest()


def session_for_token(workspace: Path, token: str) -> dict[str, object] | None:
    token_hash = _token_hash(token)
    with ControlStore(workspace) as store:
        session = store.web_session(token_hash)
        if not session:
            return None
        if session.get("revoked_at") or session.get("device_status") != "active":
            return None
        try:
            if datetime.fromisoformat(str(session["expires_at"])) <= datetime.now(UTC):
                return None
        except (KeyError, ValueError):
            return None
        store.touch_web_session(token_hash)
    return session


def _session_valid(workspace: Path, token: str) -> bool:
    return session_for_token(workspace, token) is not None


def _origin_valid(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return False
    configured = tuple(getattr(request.app.state, "trusted_origins", ()))
    return origin_is_trusted(origin, request.headers.get("host", ""), configured)


def origin_is_trusted(origin: str, host: str, configured: tuple[str, ...] = ()) -> bool:
    if configured:
        return origin.rstrip("/") in {item.rstrip("/") for item in configured}
    parsed = urlsplit(origin)
    return parsed.netloc == host and parsed.scheme in {"http", "https"}


__all__ = [
    "COOKIE_NAME", "WebAuthMiddleware", "origin_is_trusted", "router", "session_for_token",
]
