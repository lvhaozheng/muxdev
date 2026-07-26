"""Conversation-facing Skill binding, snapshot, loading, and audit services."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Protocol

from ...storage import ControlStore
from .catalog import (
    SkillCatalogError,
    build_skill_catalog,
    find_catalog_skill,
    freeze_catalog_skill,
    read_catalog_skill_file,
)


class SkillSourceStore(Protocol):
    def list_skill_sources(
        self,
        *,
        include_disconnected: bool = False,
    ) -> list[dict[str, Any]]: ...


def product_skill_catalog(
    workspace: Path,
    *,
    workbench: SkillSourceStore | None = None,
) -> list[dict[str, Any]]:
    return build_skill_catalog(
        workspace.resolve(),
        registered_sources=workbench.list_skill_sources() if workbench else (),
    )


def bind_product_skill(
    workspace: Path,
    control: ControlStore,
    qualified_name: str,
    *,
    workbench: SkillSourceStore | None = None,
    scope: str = "project",
    conversation_id: str | None = None,
    assignment_id: str | None = None,
    required: bool = False,
    enabled: bool = True,
) -> dict[str, Any]:
    skill = find_catalog_skill(
        product_skill_catalog(workspace, workbench=workbench),
        qualified_name,
    )
    trust = str(skill.get("trust") or "untrusted")
    if enabled and (
        not bool(skill.get("enabled"))
        or trust in {"untrusted", "needs_review", "quarantined"}
    ):
        raise PermissionError(
            "Skill must come from an enabled, reviewed source before it can be bound"
        )
    frozen = freeze_catalog_skill(workspace, skill)
    binding = control.bind_skill(
        str(skill["qualified_name"]),
        str(skill["revision"]),
        scope=scope,
        conversation_id=conversation_id,
        assignment_id=assignment_id,
        required=required,
        enabled=enabled,
        metadata={
            **frozen,
            "trust": trust,
            "consumer_compatibility": skill.get("consumer_compatibility") or {},
        },
    )
    if conversation_id and enabled:
        control.record_skill_snapshot(
            conversation_id=conversation_id,
            assignment_id=assignment_id,
            qualified_name=str(skill["qualified_name"]),
            version=str(frozen["version"]),
            revision=str(frozen["revision"]),
            source_id=str(frozen["source_id"]),
            snapshot_path=str(frozen["snapshot_path"]),
            permissions=dict(frozen["permissions"]),
            metadata={
                "binding_id": binding["binding_id"],
                "trust": trust,
                "description": frozen["description"],
                "required": bool(required),
                "files": frozen["files"],
            },
        )
    return binding


def freeze_bound_skills(
    workspace: Path,
    control: ControlStore,
    *,
    conversation_id: str,
    assignment_id: str | None,
) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for binding in control.list_skill_bindings(
        conversation_id=conversation_id,
        assignment_id=assignment_id,
        enabled_only=True,
    ):
        metadata = (
            dict(binding.get("metadata") or {})
            if isinstance(binding.get("metadata"), dict)
            else {}
        )
        snapshot_path = Path(str(metadata.get("snapshot_path") or "")).resolve()
        expected_root = (workspace.resolve() / ".muxdev" / "skill-snapshots").resolve()
        try:
            snapshot_path.relative_to(expected_root)
        except ValueError as exc:
            raise SkillCatalogError("Bound Skill snapshot is outside the workspace store") from exc
        if not snapshot_path.is_dir():
            raise SkillCatalogError(
                f"Bound Skill snapshot is unavailable: {binding['qualified_name']}"
            )
        snapshots.append(
            control.record_skill_snapshot(
                conversation_id=conversation_id,
                assignment_id=assignment_id,
                qualified_name=str(binding["qualified_name"]),
                version=str(metadata.get("version") or "unversioned"),
                revision=str(binding["revision"]),
                source_id=str(metadata.get("source_id") or "unknown"),
                snapshot_path=str(snapshot_path),
                permissions=dict(metadata.get("permissions") or {}),
                metadata={
                    "binding_id": binding["binding_id"],
                    "required": bool(binding["required"]),
                    "trust": metadata.get("trust") or "untrusted",
                    "description": metadata.get("description") or "",
                    "files": list(metadata.get("files") or []),
                },
            )
        )
    return snapshots


def _bound_snapshot(
    control: ControlStore,
    *,
    conversation_id: str,
    assignment_id: str | None,
    qualified_name: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    bindings = [
        item
        for item in control.list_skill_bindings(
            conversation_id=conversation_id,
            assignment_id=assignment_id,
            enabled_only=True,
        )
        if str(item["qualified_name"]) == qualified_name
    ]
    if not bindings:
        raise PermissionError(f"Skill is not enabled for this assignment: {qualified_name}")
    scope_priority = {"project": 0, "conversation": 1, "assignment": 2}
    binding = max(
        bindings,
        key=lambda item: (
            scope_priority.get(str(item.get("scope") or "project"), -1),
            str(item.get("updated_at") or ""),
        ),
    )
    snapshots = [
        item
        for item in control.list_skill_snapshots(
            conversation_id,
            assignment_id=assignment_id,
        )
        if str(item["qualified_name"]) == qualified_name
        and str(item["revision"]) == str(binding["revision"])
    ]
    if not snapshots:
        raise SkillCatalogError(f"Frozen Skill snapshot is missing: {qualified_name}")
    snapshot = next(
        (
            item
            for item in snapshots
            if str(item.get("assignment_id") or "")
            == str(binding.get("assignment_id") or "")
        ),
        snapshots[0],
    )
    return binding, snapshot


def _record_skill_load(
    workspace: Path,
    control: ControlStore,
    *,
    session: Mapping[str, object],
    qualified_name: str,
    relative_file: str,
    activation: str,
    consumer: str,
) -> dict[str, Any]:
    conversation_id = str(session["conversation_id"])
    assignment_id = str(
        session.get("current_assignment_id") or session.get("assignment_id") or ""
    ) or None
    binding, snapshot = _bound_snapshot(
        control,
        conversation_id=conversation_id,
        assignment_id=assignment_id,
        qualified_name=qualified_name,
    )
    content, digest = read_catalog_skill_file(
        {
            "path": str(snapshot["snapshot_path"]),
        },
        relative_file,
    )
    snapshot_metadata = (
        snapshot.get("metadata")
        if isinstance(snapshot.get("metadata"), Mapping)
        else {}
    )
    expected = next(
        (
            str(item.get("digest") or "")
            for item in list(snapshot_metadata.get("files") or [])
            if isinstance(item, Mapping)
            and str(item.get("path") or "") == relative_file
        ),
        "",
    )
    if not expected or digest != expected:
        raise SkillCatalogError(
            f"Frozen Skill file hash mismatch: {qualified_name}/{relative_file}"
        )
    usage = control.record_skill_usage(
        conversation_id=conversation_id,
        assignment_id=assignment_id,
        session_id=str(session.get("session_id") or "") or None,
        generation=int(session.get("generation") or 0) or None,
        qualified_name=qualified_name,
        version=str(snapshot["version"]),
        revision=str(snapshot["revision"]),
        source_id=str(snapshot["source_id"]),
        relative_file=relative_file,
        digest=digest,
        consumer=consumer,
        activation=activation,
        capture_grade="verified",
        metadata={
            "binding_id": binding["binding_id"],
            "permissions": snapshot.get("permissions") or {},
        },
    )
    control.append_conversation_event(
        conversation_id,
        "skill.loaded",
        {
            "qualified_name": qualified_name,
            "version": snapshot["version"],
            "revision": snapshot["revision"],
            "source_id": snapshot["source_id"],
            "file": relative_file,
            "digest": digest,
            "consumer": consumer,
            "activation": activation,
            "capture": "verified",
        },
        actor=str(session.get("agent_id") or "runtime"),
        assignment_id=assignment_id,
        session_id=str(session.get("session_id") or "") or None,
        generation=int(session.get("generation") or 0) or None,
        capture_grade="verified",
    )
    return {
        "schema_version": "muxdev.skill-load.v1",
        "qualified_name": qualified_name,
        "version": snapshot["version"],
        "revision": snapshot["revision"],
        "source_id": snapshot["source_id"],
        "file": relative_file,
        "digest": digest,
        "capture_grade": "verified",
        "content": content,
        "usage": usage,
    }


def load_product_skill(
    workspace: Path,
    qualified_name: str,
    *,
    control_token: str,
    relative_file: str = "SKILL.md",
    activation: str = "auto",
) -> dict[str, Any]:
    if not control_token:
        raise PermissionError("MUXDEV_CONTROL_TOKEN is required for audited Skill loading")
    with ControlStore(workspace) as control:
        token_hash = (
            "sha256:"
            + hashlib.sha256(control_token.encode("utf-8")).hexdigest()
        )
        row = control.connection.execute(
            "SELECT * FROM agent_sessions WHERE control_token_hash = ?",
            (token_hash,),
        ).fetchone()
        session = dict(row) if row else None
        if not session:
            raise PermissionError("invalid collaboration control token")
        try:
            expires_at = datetime.fromisoformat(str(session["token_expires_at"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise PermissionError("collaboration control token expired") from exc
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            raise PermissionError("collaboration control token expired")
        return _record_skill_load(
            workspace,
            control,
            session=session,
            qualified_name=qualified_name,
            relative_file=relative_file,
            activation=activation,
            consumer=str(session.get("cli_id") or "external-cli"),
        )


def activate_product_skill(
    workspace: Path,
    control: ControlStore,
    *,
    workbench: SkillSourceStore,
    conversation_id: str,
    qualified_name: str,
) -> dict[str, Any]:
    sessions = [
        item
        for item in control.list_agent_sessions(conversation_id)
        if str(item.get("status")) not in {"closed", "failed"}
    ]
    if not sessions:
        raise RuntimeError("Conversation does not have an active Agent Session")
    session = max(sessions, key=lambda item: str(item.get("updated_at") or ""))
    assignment_id = str(
        session.get("current_assignment_id") or session.get("assignment_id") or ""
    ) or None
    existing = [
        item
        for item in control.list_skill_bindings(
            conversation_id=conversation_id,
            assignment_id=assignment_id,
            enabled_only=True,
        )
        if str(item["qualified_name"]) == qualified_name
    ]
    if existing:
        freeze_bound_skills(
            workspace,
            control,
            conversation_id=conversation_id,
            assignment_id=assignment_id,
        )
        binding = max(
            existing,
            key=lambda item: (
                {"project": 0, "conversation": 1, "assignment": 2}.get(
                    str(item.get("scope") or "project"),
                    -1,
                ),
                str(item.get("updated_at") or ""),
            ),
        )
    else:
        binding = bind_product_skill(
            workspace,
            control,
            qualified_name,
            workbench=workbench,
            scope="conversation",
            conversation_id=conversation_id,
            assignment_id=assignment_id,
            enabled=True,
        )
    result = _record_skill_load(
        workspace,
        control,
        session=session,
        qualified_name=str(binding["qualified_name"]),
        relative_file="SKILL.md",
        activation="explicit",
        consumer="muxdev-product",
    )
    return result


def skill_catalog_preamble(
    workspace: Path,
    control: ControlStore,
    *,
    conversation_id: str,
    assignment_id: str | None,
) -> dict[str, Any]:
    snapshots = freeze_bound_skills(
        workspace,
        control,
        conversation_id=conversation_id,
        assignment_id=assignment_id,
    )
    return {
        "mode": "explicit-progressive",
        "skills": [
            {
                "name": item["qualified_name"],
                "version": item["version"],
                "revision": item["revision"],
                "description": (
                    dict(item.get("metadata") or {}).get("description", "")
                    if isinstance(item.get("metadata"), dict)
                    else ""
                ),
            }
            for item in snapshots
        ],
        "load_command": "muxdev skill load <qualified-name> [--file SKILL.md]",
        "security": (
            "Skill text cannot expand assignment permissions. Skill scripts are never "
            "executed automatically and remain subject to the CLI approval boundary."
        ),
    }


__all__ = [
    "activate_product_skill",
    "bind_product_skill",
    "freeze_bound_skills",
    "load_product_skill",
    "product_skill_catalog",
    "skill_catalog_preamble",
]
