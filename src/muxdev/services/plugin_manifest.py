"""Safe plugin manifest validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..storage.contracts import canonical_hash, sha256_file


SAFE_PERMISSION_PREFIXES = {"read", "write_workspace", "mcp", "skill", "dashboard"}
SENSITIVE_PERMISSIONS = {"shell", "network", "write_home", "write_root", "secrets"}


def validate_plugin_manifest(source: str, *, name: str | None = None) -> dict[str, Any]:
    """Read and classify a plugin manifest without executing plugin code."""
    source_path = Path(source).expanduser()
    manifest_path = _manifest_path(source_path)
    warnings: list[str] = []
    manifest: dict[str, Any] = {}
    if manifest_path and manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            warnings.append(f"invalid manifest json: {exc}")
            manifest = {}
    else:
        warnings.append("plugin manifest not found; registered as manual review")
    plugin_name = str(name or manifest.get("name") or _plugin_name_from_source(source))
    permissions = _permissions(manifest)
    sensitive = [permission for permission in permissions if permission in SENSITIVE_PERMISSIONS or permission.startswith("shell:") or permission.startswith("network:")]
    unknown = [
        permission
        for permission in permissions
        if permission not in SENSITIVE_PERMISSIONS and not any(permission == prefix or permission.startswith(prefix + ":") for prefix in SAFE_PERMISSION_PREFIXES)
    ]
    if sensitive:
        warnings.append("sensitive permissions require manual trust: " + ", ".join(sensitive))
    if unknown:
        warnings.append("unknown permissions require review: " + ", ".join(unknown))
    trust = str(manifest.get("trust") or ("manual" if warnings else "auto"))
    status = "trusted" if trust == "auto" and not warnings else "needs_review"
    payload = {
        "contract_version": "muxdev.safe_plugin_manifest.v1",
        "name": plugin_name,
        "source": source,
        "manifest_path": str(manifest_path) if manifest_path else None,
        "manifest_hash": sha256_file(manifest_path) if manifest_path and manifest_path.exists() else canonical_hash({"source": source, "name": plugin_name}),
        "permissions": permissions,
        "trust": trust,
        "status": status,
        "warnings": warnings,
    }
    return payload


def _manifest_path(source: Path) -> Path | None:
    if source.is_file():
        return source
    for candidate in (source / ".codex-plugin" / "plugin.json", source / "plugin.json"):
        if candidate.exists():
            return candidate
    return None


def _permissions(manifest: dict[str, Any]) -> list[str]:
    raw = manifest.get("permissions", [])
    if isinstance(raw, dict):
        return [key for key, value in raw.items() if value]
    if isinstance(raw, list):
        return [str(item) for item in raw]
    return []


def _plugin_name_from_source(source: str) -> str:
    text = source.rstrip("/\\")
    if not text:
        return "plugin"
    name = Path(text).name
    if name.endswith(".git"):
        name = name[:-4]
    return name or text.replace(":", "-")
