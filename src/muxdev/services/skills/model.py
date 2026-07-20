"""Data contracts for muxdev skill governance."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal


TrustState = Literal[
    "builtin_trusted",
    "user_trusted",
    "project_trusted",
    "org_trusted",
    "untrusted",
    "needs_review",
    "quarantined",
]


@dataclass(frozen=True)
class SkillPermissions:
    read_workspace: bool = True
    write_workspace: bool = False
    shell: bool = False
    network: bool = False
    secrets: bool = False
    mcp: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "read_workspace": self.read_workspace,
            "write_workspace": self.write_workspace,
            "shell": self.shell,
            "network": self.network,
            "secrets": self.secrets,
            "mcp": list(self.mcp),
        }


@dataclass(frozen=True)
class SkillInfo:
    name: str
    path: str
    skill_file: str
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    source: str = ""
    priority: int = 0
    disabled: bool = False
    trust: TrustState = "untrusted"
    version: str | None = None
    roles: list[str] = field(default_factory=list)
    stages: list[str] = field(default_factory=list)
    file_patterns: list[str] = field(default_factory=list)
    risk_level: str = "medium"
    permissions: SkillPermissions = field(default_factory=SkillPermissions)
    auto: bool = True
    source_path: str | None = None
    validation_errors: list[str] = field(default_factory=list)
    validation_warnings: list[str] = field(default_factory=list)

    @property
    def compatible_roles(self) -> list[str]:
        return list(self.roles)

    def to_dict(self, *, include_content: bool = False) -> dict[str, object]:
        data = asdict(self)
        data["permissions"] = self.permissions.to_dict()
        data["compatible_roles"] = list(self.roles)
        if include_content:
            data["content"] = Path(self.skill_file).read_text(encoding="utf-8", errors="replace")
        return data
