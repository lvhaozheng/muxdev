"""Product-facing Skill catalog, source audit, and immutable snapshots."""

from __future__ import annotations

import hashlib
import shutil
import stat
from pathlib import Path
from typing import Any, Iterable, Mapping

from .discovery import scan_all_skills, skill_from_file
from .model import SkillInfo


MAX_SOURCE_FILES = 500
MAX_SOURCE_BYTES = 20 * 1024 * 1024
MAX_SKILL_FILE_BYTES = 256 * 1024


class SkillCatalogError(ValueError):
    pass


def _safe_identifier(value: str) -> str:
    normalized = "".join(
        char.lower() if char.isalnum() else "-"
        for char in value.strip()
    ).strip("-")
    return normalized[:60] or "source"


def _snapshot_identifier(value: str) -> str:
    prefix = _safe_identifier(value)[:45]
    suffix = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{suffix}"


def _contained(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise SkillCatalogError(f"Skill path escapes its source: {candidate}") from exc
    return resolved


def audit_skill_source(root: Path) -> dict[str, Any]:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise SkillCatalogError(f"Skill source directory does not exist: {root}")
    files: list[dict[str, object]] = []
    total = 0
    digest = hashlib.sha256()
    for candidate in sorted(root.rglob("*")):
        if candidate.is_dir():
            continue
        resolved = _contained(root, candidate)
        if not resolved.is_file():
            continue
        size = resolved.stat().st_size
        total += size
        if len(files) >= MAX_SOURCE_FILES:
            raise SkillCatalogError("Skill source exceeds the 500 file limit")
        if total > MAX_SOURCE_BYTES:
            raise SkillCatalogError("Skill source exceeds the 20 MiB limit")
        relative = candidate.relative_to(root).as_posix()
        content_hash = hashlib.sha256()
        with resolved.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                content_hash.update(chunk)
        file_digest = content_hash.hexdigest()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_digest.encode("ascii"))
        digest.update(b"\n")
        files.append(
            {
                "path": relative,
                "size_bytes": size,
                "digest": f"sha256:{file_digest}",
                "symlink": candidate.is_symlink(),
            }
        )
    skill_count = sum(1 for item in files if str(item["path"]).endswith("SKILL.md"))
    if not skill_count:
        raise SkillCatalogError("Skill source does not contain a SKILL.md")
    return {
        "root": str(root),
        "revision": f"sha256:{digest.hexdigest()}",
        "files": files,
        "file_count": len(files),
        "size_bytes": total,
        "skill_count": skill_count,
    }


def import_skill_source(source: Path, destination_root: Path, source_id: str) -> Path:
    source = source.expanduser().resolve()
    audit = audit_skill_source(source)
    destination = destination_root.resolve() / "skill-sources" / _safe_identifier(source_id)
    temporary = destination.with_name(destination.name + ".tmp")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True, exist_ok=False)
    try:
        for item in audit["files"]:
            relative = Path(str(item["path"]))
            origin = _contained(source, source / relative)
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, target)
        if destination.exists():
            backup = destination.with_name(destination.name + ".previous")
            if backup.exists():
                shutil.rmtree(backup)
            destination.replace(backup)
            temporary.replace(destination)
            shutil.rmtree(backup)
        else:
            temporary.replace(destination)
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return destination


def _source_id_for_skill(workspace: Path, skill: SkillInfo) -> str:
    if skill.source == "builtin":
        return "builtin"
    path = Path(skill.path).resolve()
    try:
        relative = path.relative_to(workspace.resolve())
        first = relative.parts[0] if relative.parts else "project"
        if first in {".codex", ".claude", ".deepcode", ".agents"}:
            return f"project-{first[1:]}"
        return "project"
    except ValueError:
        pass
    lowered = {part.lower() for part in path.parts}
    if ".codex" in lowered:
        return "codex"
    if ".claude" in lowered:
        return "claude"
    if ".deepcode" in lowered:
        return "deepcode"
    if ".agents" in lowered:
        return "agents"
    return _safe_identifier(skill.source or path.parent.name)


def _skill_tree(skill: SkillInfo) -> tuple[str, list[dict[str, object]]]:
    root = Path(skill.path).resolve()
    audit = audit_skill_source(root)
    return str(audit["revision"]), list(audit["files"])


def _registered_skills(
    source: Mapping[str, object],
) -> Iterable[tuple[SkillInfo, Mapping[str, object]]]:
    if str(source.get("status")) != "connected":
        return ()
    root = Path(str(source["path"])).expanduser().resolve()
    audit_skill_source(root)
    result: list[tuple[SkillInfo, Mapping[str, object]]] = []
    for skill_file in sorted(root.rglob("SKILL.md")):
        _contained(root, skill_file)
        info = skill_from_file(
            skill_file,
            source=f"registered:{source['source_id']}",
            priority=900,
            config={
                "skill": {
                    skill_file.parent.name: {
                        "trust": str(source.get("trust_state") or "untrusted"),
                        "disabled": not bool(source.get("enabled")),
                    }
                }
            },
        )
        result.append((info, source))
    return result


def build_skill_catalog(
    workspace: Path,
    *,
    registered_sources: Iterable[Mapping[str, object]] = (),
) -> list[dict[str, Any]]:
    workspace = workspace.resolve()
    candidates: list[tuple[SkillInfo, Mapping[str, object] | None]] = [
        (item, None)
        for item in scan_all_skills(workspace, include_disabled=True)
    ]
    for source in registered_sources:
        candidates.extend(_registered_skills(source))
    names: dict[str, int] = {}
    for skill, _source in candidates:
        names[skill.name] = names.get(skill.name, 0) + 1
    used: set[str] = set()
    result: list[dict[str, Any]] = []
    for skill, source_record in candidates:
        source_id = (
            str(source_record["source_id"])
            if source_record is not None
            else _source_id_for_skill(workspace, skill)
        )
        qualified = skill.name if names[skill.name] == 1 else f"{skill.name}@{source_id}"
        if qualified in used:
            path_suffix = hashlib.sha256(skill.path.encode("utf-8")).hexdigest()[:8]
            qualified = f"{qualified}-{path_suffix}"
        used.add(qualified)
        try:
            revision, files = _skill_tree(skill)
        except (OSError, SkillCatalogError) as exc:
            revision, files = "", []
            skill = SkillInfo(
                **{
                    **skill.__dict__,
                    "disabled": True,
                    "validation_errors": [*skill.validation_errors, str(exc)],
                }
            )
        external = source_id not in {
            "builtin",
            "project",
            "project-codex",
            "project-claude",
            "project-deepcode",
            "project-agents",
        }
        trust = (
            str(source_record.get("trust_state") or "untrusted")
            if source_record is not None
            else (
                "builtin_trusted"
                if source_id == "builtin"
                else "project_trusted"
                if source_id.startswith("project")
                else skill.trust
            )
        )
        enabled = (
            bool(source_record.get("enabled"))
            if source_record is not None
            else not skill.disabled and (not external or trust not in {"untrusted", "needs_review", "quarantined"})
        )
        native_consumers: list[str] = []
        path_parts = {part.lower() for part in Path(skill.path).parts}
        if ".codex" in path_parts:
            native_consumers.append("codex")
        if ".claude" in path_parts:
            native_consumers.append("claude-code")
        if ".deepcode" in path_parts or ".agents" in path_parts:
            native_consumers.append("deepcode")
        result.append(
            {
                **skill.to_dict(),
                "qualified_name": qualified,
                "source_id": source_id,
                "revision": revision,
                "files": files,
                "script_count": sum(
                    1
                    for item in files
                    if Path(str(item["path"])).parts
                    and Path(str(item["path"])).parts[0] == "scripts"
                ),
                "trust": trust,
                "enabled": enabled,
                "native_consumers": native_consumers,
                "consumer_compatibility": {
                    "audited_loader": True,
                    "codex": "native" if "codex" in native_consumers else "loader",
                    "claude-code": "native" if "claude-code" in native_consumers else "loader",
                    "deepcode": "native" if "deepcode" in native_consumers else "loader",
                    "other": "loader",
                },
            }
        )
    return sorted(result, key=lambda item: (str(item["name"]), str(item["source_id"])))


def find_catalog_skill(
    catalog: Iterable[Mapping[str, object]],
    qualified_name: str,
) -> dict[str, Any]:
    matches = [
        dict(item)
        for item in catalog
        if item.get("qualified_name") == qualified_name
        or item.get("name") == qualified_name
    ]
    if not matches:
        raise FileNotFoundError(f"Skill not found: {qualified_name}")
    exact = [item for item in matches if item.get("qualified_name") == qualified_name]
    if exact:
        return exact[0]
    if len(matches) > 1:
        names = ", ".join(str(item["qualified_name"]) for item in matches)
        raise SkillCatalogError(f"Skill name is ambiguous; use one of: {names}")
    return matches[0]


def read_catalog_skill_file(skill: Mapping[str, object], relative_file: str = "SKILL.md") -> tuple[str, str]:
    root = Path(str(skill["path"])).resolve()
    candidate = _contained(root, root / relative_file)
    if not candidate.is_file():
        raise FileNotFoundError(f"Skill file not found: {relative_file}")
    size = candidate.stat().st_size
    if size > MAX_SKILL_FILE_BYTES:
        raise SkillCatalogError("Skill file exceeds the 256 KiB load limit")
    payload = candidate.read_bytes()
    if b"\x00" in payload[:8192]:
        raise SkillCatalogError("Binary Skill files cannot be loaded as instructions")
    content = payload.decode("utf-8", errors="strict")
    digest = hashlib.sha256(payload).hexdigest()
    return content, f"sha256:{digest}"


def freeze_catalog_skill(
    workspace: Path,
    skill: Mapping[str, object],
) -> dict[str, Any]:
    revision = str(skill.get("revision") or "")
    if not revision.startswith("sha256:"):
        raise SkillCatalogError("Skill revision is unavailable")
    root = Path(str(skill["path"])).resolve()
    target = (
        workspace.resolve()
        / ".muxdev"
        / "skill-snapshots"
        / revision.split(":", 1)[1]
        / _snapshot_identifier(str(skill["qualified_name"]))
    )
    if not target.exists():
        temporary = target.with_name(target.name + ".tmp")
        temporary.mkdir(parents=True, exist_ok=False)
        try:
            for item in list(skill.get("files") or []):
                relative = Path(str(item["path"]))
                source = _contained(root, root / relative)
                destination = temporary / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                try:
                    destination.chmod(stat.S_IREAD)
                except OSError:
                    pass
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary.replace(target)
        except BaseException:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise
    frozen_files = list(skill.get("files") or [])
    for item in frozen_files:
        relative = Path(str(item["path"]))
        candidate = _contained(target, target / relative)
        if not candidate.is_file():
            raise SkillCatalogError(f"Frozen Skill file is missing: {relative.as_posix()}")
        actual = "sha256:" + hashlib.sha256(candidate.read_bytes()).hexdigest()
        if actual != str(item.get("digest") or ""):
            raise SkillCatalogError(
                f"Frozen Skill file hash mismatch: {relative.as_posix()}"
            )
    return {
        "qualified_name": str(skill["qualified_name"]),
        "name": str(skill["name"]),
        "version": str(skill.get("version") or "unversioned"),
        "revision": revision,
        "source_id": str(skill["source_id"]),
        "snapshot_path": str(target),
        "permissions": dict(skill.get("permissions") or {}),
        "description": str(skill.get("description") or ""),
        "files": frozen_files,
    }


__all__ = [
    "MAX_SKILL_FILE_BYTES",
    "MAX_SOURCE_BYTES",
    "MAX_SOURCE_FILES",
    "SkillCatalogError",
    "audit_skill_source",
    "build_skill_catalog",
    "find_catalog_skill",
    "freeze_catalog_skill",
    "import_skill_source",
    "read_catalog_skill_file",
]
