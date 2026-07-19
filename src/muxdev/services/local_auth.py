"""Private local API authentication and browser bootstrap support."""

from __future__ import annotations

import base64
import getpass
import hashlib
import hmac
import json
import os
import secrets
import stat
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping


TOKEN_BYTES = 32
SESSION_TTL_SECONDS = 8 * 60 * 60
BOOTSTRAP_TTL_SECONDS = 60
COOKIE_NAME = "muxdev_session"


@dataclass
class LocalApiAuth:
    """Own a daemon-private bearer token without exposing it through HTTP."""

    data_dir: Path
    now: Callable[[], float] = time.time
    token_path: Path = field(init=False)
    _token: str = field(init=False, repr=False)
    _bootstraps: dict[str, float] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        auth_dir = Path(self.data_dir).expanduser().resolve() / "auth"
        auth_dir.mkdir(parents=True, exist_ok=True)
        _secure_private_directory(auth_dir)
        self.token_path = auth_dir / "api-token"
        self._token = self._load_or_create_token()

    @property
    def cursor_secret(self) -> bytes:
        return hashlib.sha256(("muxdev-cursor:" + self._token).encode("utf-8")).digest()

    def status(self) -> dict[str, object]:
        permission = self.permission_status()
        return {
            "enabled": True,
            "token_present": bool(self._token),
            "permission_status": permission,
            "production_ready": permission == "secure",
            "token_fingerprint": hashlib.sha256(self._token.encode("utf-8")).hexdigest(),
        }

    def permission_status(self) -> str:
        try:
            mode = stat.S_IMODE(self.token_path.stat().st_mode)
        except OSError:
            return "permission_degraded"
        if os.name != "nt":
            return "secure" if mode & 0o077 == 0 else "permission_degraded"
        return "secure" if _windows_acl_is_private(self.token_path) else "permission_degraded"

    def verify_bearer(self, header: str | None) -> bool:
        prefix = "Bearer "
        if not header or not header.startswith(prefix):
            return False
        return hmac.compare_digest(header[len(prefix) :].strip(), self._token)

    def authorization_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def issue_bootstrap(self) -> str:
        nonce = secrets.token_urlsafe(32)
        expires = float(self.now()) + BOOTSTRAP_TTL_SECONDS
        bootstrap_dir = self.token_path.parent / "bootstrap"
        bootstrap_dir.mkdir(parents=True, exist_ok=True)
        _secure_private_directory(bootstrap_dir)
        _atomic_private_write(bootstrap_dir / hashlib.sha256(nonce.encode()).hexdigest(), str(expires))
        return nonce

    def exchange_bootstrap(self, nonce: str) -> str | None:
        nonce_path = self.token_path.parent / "bootstrap" / hashlib.sha256(str(nonce).encode()).hexdigest()
        try:
            expires = float(nonce_path.read_text(encoding="utf-8"))
            nonce_path.unlink()
        except (OSError, ValueError):
            return None
        if expires < float(self.now()):
            return None
        return self._session_cookie(expires=float(self.now()) + SESSION_TTL_SECONDS)

    def verify_cookie(self, cookie: str | None) -> bool:
        if not cookie:
            return False
        try:
            encoded, signature = cookie.rsplit(".", 1)
            raw = _b64decode(encoded)
            payload = json.loads(raw.decode("utf-8"))
            expires = float(payload["expires"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            return False
        expected = hmac.new(self.cursor_secret, encoded.encode("ascii"), hashlib.sha256).hexdigest()
        return expires >= float(self.now()) and hmac.compare_digest(signature, expected)

    def rotate(self) -> dict[str, object]:
        self._token = secrets.token_urlsafe(TOKEN_BYTES)
        _atomic_private_write(self.token_path, self._token + "\n")
        self._bootstraps.clear()
        return self.status()

    def _load_or_create_token(self) -> str:
        if self.token_path.exists():
            token = self.token_path.read_text(encoding="utf-8").strip()
            if len(token) < 32:
                raise RuntimeError("muxdev API token file is invalid")
            return token
        token = secrets.token_urlsafe(TOKEN_BYTES)
        _atomic_private_write(self.token_path, token + "\n")
        return token

    def _session_cookie(self, *, expires: float) -> str:
        payload = json.dumps({"expires": int(expires), "nonce": secrets.token_hex(8)}, sort_keys=True, separators=(",", ":"))
        encoded = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
        signature = hmac.new(self.cursor_secret, encoded.encode("ascii"), hashlib.sha256).hexdigest()
        return f"{encoded}.{signature}"

    def _prune_bootstraps(self) -> None:
        current = float(self.now())
        self._bootstraps = {key: value for key, value in self._bootstraps.items() if value >= current}


def request_is_authenticated(
    auth: LocalApiAuth,
    *,
    headers: Mapping[str, str],
    cookies: Mapping[str, str],
) -> bool:
    return auth.verify_bearer(headers.get("authorization")) or auth.verify_cookie(cookies.get(COOKIE_NAME))


def _atomic_private_write(path: Path, content: str) -> None:
    # Secure the parent before creating the temporary file. On Windows the
    # replacement inherits this private DACL, so invoking ``icacls`` for the
    # temporary file, parent and final file would only repeat the same policy
    # three times (and makes daemon-heavy test suites needlessly slow).
    _secure_private_directory(path.parent)
    temp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    temp.write_text(content, encoding="utf-8")
    if os.name != "nt":
        os.chmod(temp, 0o600)
    os.replace(temp, path)
    if os.name != "nt":
        os.chmod(path, 0o600)


def _secure_private_directory(path: Path) -> None:
    if os.name == "nt":
        if not _windows_acl_is_private(path):
            _secure_windows_path(path)
    else:
        os.chmod(path, 0o700)


def _secure_windows_path(path: Path) -> bool:
    """Remove inherited ACLs and grant the current Windows identity full access."""
    principal = _windows_principal()
    if not principal:
        return False
    try:
        completed = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{principal}:(F)"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _windows_acl_is_private(path: Path) -> bool:
    principal = _windows_principal().lower()
    if not principal:
        return False
    try:
        completed = subprocess.run(
            ["icacls", str(path)], capture_output=True, text=True,
            check=False, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    output = (completed.stdout or "").lower()
    forbidden = ("everyone:", "users:", "authenticated users:", "everyone:(", "users:(")
    return completed.returncode == 0 and principal in output and not any(value in output for value in forbidden)


def _windows_principal() -> str:
    user = getpass.getuser().strip()
    domain = str(os.environ.get("USERDOMAIN") or "").strip()
    return f"{domain}\\{user}" if domain and "\\" not in user else user


def _b64decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(value + padding)
