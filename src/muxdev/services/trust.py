"""Per-project Ed25519 identity and private-key governance."""

from __future__ import annotations

import base64
import json
import os
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from ..core.canonical import canonical_json_bytes, sha256_bytes
from ..core.private_paths import muxdev_private_home
from ..domain.attestation import SIGNATURE_CONTRACT
from ..models import utc_now


class TrustError(RuntimeError):
    """Base class for project signing identity failures."""


class KeyMaterialMissingError(TrustError):
    """A public project identity exists but its private key is unavailable."""


class KeyPermissionError(TrustError):
    """Private-key permissions cannot be shown to meet the signing policy."""


@dataclass(frozen=True)
class ProjectKey:
    project_id: str
    key_id: str
    fingerprint: str
    public_key_spki: bytes
    private_key: Ed25519PrivateKey
    permission_status: str


class ProjectSigningKeyStore:
    """Keep public project identity in-project and private keys daemon-private."""

    def __init__(self, project_root: Path, *, muxdev_home: Path | None = None) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.trust_dir = self.project_root / ".muxdev" / "trust"
        self.project_id_path = self.trust_dir / "project-id"
        self.identity_path = self.trust_dir / "identity.json"
        home = Path(muxdev_home).expanduser().resolve() if muxdev_home else muxdev_private_home().resolve()
        self.private_projects_dir = home / "data" / "signing" / "projects"

    def status(self) -> dict[str, Any]:
        project_id = self._read_project_id()
        identity = self._read_identity()
        key_id = str(identity.get("current_key_id") or "") or None
        private_path = self._private_key_path(project_id, key_id) if project_id and key_id else None
        permission = self._permission_status(private_path) if private_path and private_path.exists() else "unavailable"
        return {
            "contract_version": "muxdev.project-trust-status.v1",
            "project_id": project_id,
            "initialized": bool(project_id and key_id),
            "key_id": key_id,
            "public_key_fingerprint": identity.get("current_fingerprint"),
            "private_key_available": bool(private_path and private_path.is_file()),
            "permission_status": permission,
            "identity_path": str(self.identity_path),
        }

    def initialize(self) -> ProjectKey:
        self._ensure_project_directory()
        project_id = self._read_project_id()
        if project_id is None:
            project_id = str(uuid4())
            self._atomic_write(self.project_id_path, (project_id + "\n").encode("ascii"), mode=0o644)
        identity = self._read_identity()
        current_key_id = str(identity.get("current_key_id") or "") or None
        if current_key_id:
            private_path = self._private_key_path(project_id, current_key_id)
            if not private_path.is_file():
                raise KeyMaterialMissingError(
                    "project public identity exists but its private key is missing; use explicit trust recovery or rotation"
                )
            return self.load(require_secure=False)
        return self._generate(project_id, prior_identity=identity)

    def load(self, *, require_secure: bool = False) -> ProjectKey:
        project_id = self._read_project_id()
        identity = self._read_identity()
        key_id = str(identity.get("current_key_id") or "")
        if not project_id or not key_id:
            raise KeyMaterialMissingError("project signing identity has not been initialized")
        private_path = self._private_key_path(project_id, key_id)
        if not private_path.is_file():
            raise KeyMaterialMissingError("project private signing key is missing")
        permission = self._permission_status(private_path)
        if require_secure and permission != "secure":
            raise KeyPermissionError(f"private-key permission status is {permission}")
        raw = private_path.read_bytes()
        private_key = serialization.load_pem_private_key(raw, password=None)
        if not isinstance(private_key, Ed25519PrivateKey):
            raise TrustError("project private key is not Ed25519")
        public_spki = _public_spki(private_key.public_key())
        fingerprint = sha256_bytes(public_spki)
        expected = str(identity.get("current_fingerprint") or "")
        if expected and fingerprint != expected:
            raise TrustError("private key does not match the project public identity")
        return ProjectKey(project_id, key_id, fingerprint, public_spki, private_key, permission)

    def get_or_create(self, *, require_secure: bool = False) -> ProjectKey:
        try:
            key = self.initialize()
        except KeyMaterialMissingError:
            raise
        if require_secure and key.permission_status != "secure":
            raise KeyPermissionError(f"private-key permission status is {key.permission_status}")
        return key

    def sign(self, payload: bytes, *, require_secure: bool = False) -> dict[str, Any]:
        key = self.get_or_create(require_secure=require_secure)
        signature = key.private_key.sign(payload)
        return {
            "contract_version": SIGNATURE_CONTRACT,
            "algorithm": "Ed25519",
            "key_id": key.key_id,
            "project_id": key.project_id,
            "public_key_format": "SubjectPublicKeyInfo/DER",
            "public_key": base64.b64encode(key.public_key_spki).decode("ascii"),
            "public_key_fingerprint": key.fingerprint,
            "payload_sha256": sha256_bytes(payload),
            "signature": base64.b64encode(signature).decode("ascii"),
            "created_at": utc_now(),
        }

    def public_key_pem(self) -> bytes:
        key = self.load(require_secure=False)
        return key.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def rotate(self, *, reason: str, confirmed: bool) -> dict[str, Any]:
        if not confirmed:
            raise TrustError("key rotation requires explicit confirmation")
        reason = reason.strip()
        if not reason:
            raise TrustError("key rotation requires a reason")
        old = self.load(require_secure=False)
        identity = self._read_identity()
        new_private = Ed25519PrivateKey.generate()
        new_spki = _public_spki(new_private.public_key())
        new_fingerprint = sha256_bytes(new_spki)
        new_key_id = _key_id(new_fingerprint)
        record = {
            "contract_version": "muxdev.project-key-rotation.v1",
            "project_id": old.project_id,
            "old_key_id": old.key_id,
            "old_fingerprint": old.fingerprint,
            "new_key_id": new_key_id,
            "new_fingerprint": new_fingerprint,
            "reason": reason[:500],
            "rotated_at": utc_now(),
        }
        record_bytes = canonical_json_bytes(record)
        rotation = {
            **record,
            "old_signature": base64.b64encode(old.private_key.sign(record_bytes)).decode("ascii"),
            "new_signature": base64.b64encode(new_private.sign(record_bytes)).decode("ascii"),
        }
        project_dir = self._project_private_dir(old.project_id)
        project_dir.mkdir(parents=True, exist_ok=True)
        self._secure_directory(project_dir)
        new_path = self._private_key_path(old.project_id, new_key_id)
        self._write_private_key(new_path, new_private)
        public_keys = dict(identity.get("public_keys") or {})
        public_keys[old.key_id] = {"fingerprint": old.fingerprint, "spki": base64.b64encode(old.public_key_spki).decode("ascii")}
        public_keys[new_key_id] = {"fingerprint": new_fingerprint, "spki": base64.b64encode(new_spki).decode("ascii")}
        rotations = list(identity.get("rotations") or [])
        rotations.append(rotation)
        updated = {
            "contract_version": "muxdev.project-signing-identity.v1",
            "project_id": old.project_id,
            "current_key_id": new_key_id,
            "current_fingerprint": new_fingerprint,
            "public_keys": public_keys,
            "rotations": rotations,
            "updated_at": utc_now(),
        }
        self._atomic_write(self.identity_path, _pretty_json(updated), mode=0o644)
        old_path = self._private_key_path(old.project_id, old.key_id)
        if old_path.exists():
            old_path.unlink()
        return rotation

    def _generate(self, project_id: str, *, prior_identity: dict[str, Any]) -> ProjectKey:
        private_key = Ed25519PrivateKey.generate()
        public_spki = _public_spki(private_key.public_key())
        fingerprint = sha256_bytes(public_spki)
        key_id = _key_id(fingerprint)
        project_dir = self._project_private_dir(project_id)
        project_dir.mkdir(parents=True, exist_ok=True)
        self._secure_directory(project_dir)
        private_path = self._private_key_path(project_id, key_id)
        self._write_private_key(private_path, private_key)
        identity = {
            "contract_version": "muxdev.project-signing-identity.v1",
            "project_id": project_id,
            "current_key_id": key_id,
            "current_fingerprint": fingerprint,
            "public_keys": {
                **dict(prior_identity.get("public_keys") or {}),
                key_id: {"fingerprint": fingerprint, "spki": base64.b64encode(public_spki).decode("ascii")},
            },
            "rotations": list(prior_identity.get("rotations") or []),
            "updated_at": utc_now(),
        }
        self._atomic_write(self.identity_path, _pretty_json(identity), mode=0o644)
        permission = self._permission_status(private_path)
        return ProjectKey(project_id, key_id, fingerprint, public_spki, private_key, permission)

    def _write_private_key(self, path: Path, private_key: Ed25519PrivateKey) -> None:
        raw = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        self._atomic_write(path, raw, mode=0o600)
        self._secure_file(path)

    def _ensure_project_directory(self) -> None:
        if not self.project_root.is_dir():
            raise FileNotFoundError(f"project root not found: {self.project_root}")
        self.trust_dir.mkdir(parents=True, exist_ok=True)

    def _read_project_id(self) -> str | None:
        try:
            value = self.project_id_path.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            return None
        try:
            return str(__import__("uuid").UUID(value))
        except ValueError as exc:
            raise TrustError("invalid .muxdev/trust/project-id") from exc

    def _read_identity(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.identity_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError as exc:
            raise TrustError("invalid project signing identity") from exc
        return payload if isinstance(payload, dict) else {}

    def _project_private_dir(self, project_id: str) -> Path:
        return self.private_projects_dir / project_id

    def _private_key_path(self, project_id: str | None, key_id: str | None) -> Path:
        return self._project_private_dir(str(project_id)) / f"{key_id}.key.pem"

    def _permission_status(self, path: Path) -> str:
        try:
            if os.name == "nt":
                return "secure" if self._windows_acl_secure(path) else "permission_degraded"
            file_mode = stat.S_IMODE(path.stat().st_mode)
            dir_mode = stat.S_IMODE(path.parent.stat().st_mode)
            return "secure" if file_mode & 0o077 == 0 and dir_mode & 0o077 == 0 else "permission_degraded"
        except OSError:
            return "permission_degraded"

    def _secure_directory(self, path: Path) -> None:
        try:
            os.chmod(path, 0o700)
            if os.name == "nt":
                self._apply_windows_acl(path)
        except OSError:
            return

    def _secure_file(self, path: Path) -> None:
        try:
            os.chmod(path, 0o600)
            if os.name == "nt":
                self._apply_windows_acl(path)
        except OSError:
            return

    @staticmethod
    def _apply_windows_acl(path: Path) -> bool:
        username = os.environ.get("USERNAME")
        if not username:
            return False
        completed = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{username}:(F)"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        return completed.returncode == 0

    @staticmethod
    def _windows_acl_secure(path: Path) -> bool:
        username = os.environ.get("USERNAME")
        if not username:
            return False
        completed = subprocess.run(
            ["icacls", str(path)], capture_output=True, text=True, check=False, timeout=10
        )
        if completed.returncode != 0:
            return False
        lines = [line.strip().lower() for line in completed.stdout.splitlines() if line.strip()]
        allowed = {username.lower(), "nt authority\\system", "builtin\\administrators"}
        principals: set[str] = set()
        for line in lines:
            if ":(" not in line:
                continue
            principal = line.split(":(", 1)[0].strip()
            if principal:
                principals.add(principal)
        return bool(principals) and all(any(principal.endswith(item) for item in allowed) for principal in principals)

    @staticmethod
    def _atomic_write(path: Path, data: bytes, *, mode: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        tmp = Path(raw_tmp)
        try:
            os.chmod(tmp, mode)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
            os.chmod(path, mode)
        finally:
            if tmp.exists():
                tmp.unlink()


def verify_signature(payload: bytes, envelope: dict[str, Any]) -> tuple[bool, str | None]:
    try:
        if envelope.get("contract_version") != SIGNATURE_CONTRACT:
            return False, "unsupported signature contract"
        public_spki = base64.b64decode(str(envelope["public_key"]), validate=True)
        signature = base64.b64decode(str(envelope["signature"]), validate=True)
        if envelope.get("payload_sha256") != sha256_bytes(payload):
            return False, "payload hash mismatch"
        fingerprint = sha256_bytes(public_spki)
        if envelope.get("public_key_fingerprint") != fingerprint:
            return False, "public key fingerprint mismatch"
        public_key = serialization.load_der_public_key(public_spki)
        if not isinstance(public_key, Ed25519PublicKey):
            return False, "public key is not Ed25519"
        public_key.verify(signature, payload)
        return True, None
    except (InvalidSignature, ValueError, TypeError, KeyError) as exc:
        return False, f"invalid signature: {type(exc).__name__}"


def _public_spki(public_key: Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _key_id(fingerprint: str) -> str:
    return "key_" + fingerprint.removeprefix("sha256:")[:20]


def _pretty_json(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
