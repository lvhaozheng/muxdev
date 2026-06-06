"""Skill registry and provider-native skill export helpers."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from ..config.loader import path_config


@dataclass(frozen=True)
class SkillRecord:
    """Persisted skill metadata shown by `muxdev skill list`."""

    name: str
    path: str
    native: bool = False
    native_provider: str | None = None
    native_path: str | None = None
    description: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class SkillRegistry:
    """Manage local skill files under the configured runtime skills path."""

    def __init__(self, workspace: Path):
        self.root = path_config(workspace, "skills")
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "skills.json"

    def list(self) -> list[SkillRecord]:
        """Return installed skills from the JSON index."""
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
        """Install a skill from a source path or create a minimal local skill."""
        target = self.root / name
        target.mkdir(parents=True, exist_ok=True)
        if source:
            resolved = source.resolve()
            if not resolved.exists():
                raise FileNotFoundError(f"skill source not found: {source}")
            if resolved.is_dir():
                _copy_tree_contents(resolved, target)
            else:
                shutil.copy2(resolved, target / resolved.name)
        skill_md = target / "SKILL.md"
        if not skill_md.exists():
            skill_md.write_text(f"# {name}\n\nLocal muxdev skill.\n", encoding="utf-8")
        native_path = None
        if native:
            native_path = str(self._export_native_skill(name, skill_md, provider or "generic"))
        record = SkillRecord(
            name=name,
            path=str(target),
            native=native,
            native_provider=provider if native else None,
            native_path=native_path,
            description=_first_heading(skill_md),
        )
        self._upsert(record)
        return record

    def inject(self, name: str) -> str:
        """Return the SKILL.md content for prompt injection."""
        for record in self.list():
            if record.name == name:
                skill_md = Path(record.path) / "SKILL.md"
                if not skill_md.exists():
                    raise FileNotFoundError(f"skill has no SKILL.md: {name}")
                return skill_md.read_text(encoding="utf-8")
        raise ValueError(f"skill not found: {name}")

    def _upsert(self, record: SkillRecord) -> None:
        rows = [item for item in self.list() if item.name != record.name]
        rows.append(record)
        self.index_path.write_text(
            json.dumps([row.to_dict() for row in sorted(rows, key=lambda item: item.name)], indent=2),
            encoding="utf-8",
        )

    def _export_native_skill(self, name: str, skill_md: Path, provider: str) -> Path:
        """Write provider-specific skill files while preserving source content."""
        exports_root = self.root / ".native" / provider
        exports_root.mkdir(parents=True, exist_ok=True)
        target = exports_root / _safe_name(name)
        target.mkdir(parents=True, exist_ok=True)
        content = skill_md.read_text(encoding="utf-8")
        if provider == "codex":
            (target / "SKILL.md").write_text(content, encoding="utf-8")
            (target / "codex-plugin.json").write_text(
                json.dumps({"name": name, "type": "skill", "entry": "SKILL.md"}, indent=2),
                encoding="utf-8",
            )
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


def _copy_tree_contents(source: Path, target: Path) -> None:
    for child in source.iterdir():
        destination = target / child.name
        if child.is_dir():
            shutil.copytree(child, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(child, destination)


def _first_heading(path: Path) -> str:
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return path.parent.name


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value).strip("-") or "skill"
