"""Skill installation and legacy registry compatibility."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from ...config.loader import path_config
from .discovery import add_skill_directory, copy_tree_contents, scan_skills
from .validation import skill_show


@dataclass(frozen=True)
class SkillRecord:
    """Persisted skill metadata shown by legacy `muxdev skill install`."""

    name: str
    path: str
    native: bool = False
    native_provider: str | None = None
    native_path: str | None = None
    description: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class SkillRegistry:
    """Compatibility facade over filesystem discovery.

    `skills.json` is maintained as a cache for older surfaces, not as the source
    of truth. Discovery still comes from the filesystem.
    """

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.root = path_config(workspace, "skills")
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "skills.json"

    def list(self) -> list[SkillRecord]:
        discovered = [
            SkillRecord(name=skill.name, path=skill.path, description=skill.description)
            for skill in scan_skills(self.workspace, include_disabled=True)
            if _same_or_child(Path(skill.path), self.root)
        ]
        if discovered:
            return sorted(discovered, key=lambda item: item.name)
        if not self.index_path.exists():
            return []
        data = json.loads(self.index_path.read_text(encoding="utf-8"))
        return [SkillRecord(**item) for item in data]

    def install(
        self,
        name: str,
        *,
        source: Path | None = None,
        native: bool = False,
        provider: str | None = None,
    ) -> SkillRecord:
        target = self.root / _safe_name(name)
        target.mkdir(parents=True, exist_ok=True)
        if source:
            resolved = source.resolve()
            if not resolved.exists():
                raise FileNotFoundError(f"skill source not found: {source}")
            if resolved.is_dir():
                copy_tree_contents(resolved, target)
            else:
                shutil.copy2(resolved, target / resolved.name)
        skill_md = target / "SKILL.md"
        if not skill_md.exists():
            skill_md.write_text(f"---\nname: {name}\ndescription: Local muxdev skill.\n---\n# {name}\n\nLocal muxdev skill.\n", encoding="utf-8")
        native_path = None
        if native:
            native_path = str(self._export_native_skill(name, skill_md, provider or "generic"))
        record = SkillRecord(name=name, path=str(target), native=native, native_provider=provider if native else None, native_path=native_path, description=_first_heading(skill_md))
        self._upsert(record)
        return record

    def inject(self, name: str) -> str:
        try:
            return str(skill_show(self.workspace, name).get("content", ""))
        except ValueError:
            for record in self.list():
                if record.name == name:
                    skill_md = Path(record.path) / "SKILL.md"
                    if not skill_md.exists():
                        raise FileNotFoundError(f"skill has no SKILL.md: {name}")
                    return skill_md.read_text(encoding="utf-8")
        raise ValueError(f"skill not found: {name}")

    def _upsert(self, record: SkillRecord) -> None:
        rows = [item for item in self._cached_rows() if item.name != record.name]
        rows.append(record)
        self.index_path.write_text(
            json.dumps([row.to_dict() for row in sorted(rows, key=lambda item: item.name)], indent=2),
            encoding="utf-8",
        )

    def _cached_rows(self) -> list[SkillRecord]:
        if not self.index_path.exists():
            return []
        data = json.loads(self.index_path.read_text(encoding="utf-8"))
        return [SkillRecord(**item) for item in data]

    def _export_native_skill(self, name: str, skill_md: Path, provider: str) -> Path:
        target = self.root / ".native" / provider / _safe_name(name)
        target.mkdir(parents=True, exist_ok=True)
        content = skill_md.read_text(encoding="utf-8")
        if provider == "codex":
            (target / "SKILL.md").write_text(content, encoding="utf-8")
            (target / "codex-plugin.json").write_text(json.dumps({"name": name, "type": "skill", "entry": "SKILL.md"}, indent=2), encoding="utf-8")
        elif provider == "claude-code":
            (target / "CLAUDE.md").write_text(content, encoding="utf-8")
            (target / "skill.json").write_text(json.dumps({"name": name, "entry": "CLAUDE.md"}, indent=2), encoding="utf-8")
        elif provider == "qwen":
            (target / "QWEN.md").write_text(content, encoding="utf-8")
            (target / "skill.json").write_text(json.dumps({"name": name, "entry": "QWEN.md"}, indent=2), encoding="utf-8")
        else:
            (target / "SKILL.md").write_text(content, encoding="utf-8")
            (target / "skill.json").write_text(json.dumps({"name": name, "entry": "SKILL.md", "provider": provider}, indent=2), encoding="utf-8")
        return target


def add_skill(workspace: Path, source: str, *, name: str | None = None, global_scope: bool = False):
    return add_skill_directory(workspace, source, name=name, global_scope=global_scope)


def sync_skills(workspace: Path) -> dict[str, object]:
    rows = scan_skills(workspace, include_disabled=True)
    index = workspace / ".muxdev" / "skills-index.json"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(json.dumps([row.to_dict() for row in rows], ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": str(index), "skills": len(rows), "status": "synced"}


def export_skill(workspace: Path, name: str, output: Path | None = None) -> dict[str, object]:
    data = skill_show(workspace, name)
    source = Path(str(data["path"]))
    target = output or workspace / ".muxdev" / "exports" / f"{name}"
    target.mkdir(parents=True, exist_ok=True)
    copy_tree_contents(source, target)
    return {"name": name, "path": str(target), "status": "exported"}


def remove_skill(workspace: Path, name: str) -> dict[str, object]:
    for skill in scan_skills(workspace, include_disabled=True):
        if skill.name != name:
            continue
        path = Path(skill.path).resolve()
        workspace_resolved = workspace.resolve()
        if path == workspace_resolved or workspace_resolved in path.parents:
            shutil.rmtree(path, ignore_errors=True)
            return {"name": name, "path": str(path), "status": "removed"}
        return {"name": name, "path": str(path), "status": "not_removed", "reason": "outside workspace"}
    raise ValueError(f"skill not found: {name}")


def _first_heading(path: Path) -> str:
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return path.parent.name


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value).strip("-") or "skill"


def _same_or_child(path: Path, parent: Path) -> bool:
    try:
        resolved = path.resolve()
        root = parent.resolve()
    except OSError:
        return False
    return resolved == root or root in resolved.parents
