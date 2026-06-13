"""Data contracts for muxdev skill governance."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


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


@dataclass(frozen=True)
class ActivatedSkill:
    skill: SkillInfo
    role: str | None
    reason: str
    injection: str
    provider: str = "mock"
    stage: str | None = None
    score: int = 0
    reasons: tuple[str, ...] = ()
    approved: bool = True
    resources: dict[str, list[dict[str, object]]] = field(default_factory=dict)
    wrapper: str | None = None

    def to_dict(self, *, include_content: bool = False) -> dict[str, object]:
        data = self.skill.to_dict(include_content=include_content)
        data.update(
            {
                "role": self.role,
                "stage": self.stage,
                "provider": self.provider,
                "reason": self.reason,
                "reasons": list(self.reasons or (self.reason,)),
                "score": self.score,
                "injection": self.injection,
                "approved": self.approved,
            }
        )
        if self.resources:
            data["resources"] = self.resources
        if self.wrapper is not None:
            data["provider_payload"] = self.wrapper
        return data


@dataclass(frozen=True)
class SkillSelection:
    selected: list[ActivatedSkill]
    rejected: list[dict[str, object]]
    warnings: list[str] = field(default_factory=list)
    catalog_budget: dict[str, object] = field(default_factory=dict)

    def to_dict(self, *, include_content: bool = False) -> dict[str, object]:
        return {
            "selected": [item.to_dict(include_content=include_content) for item in self.selected],
            "rejected": self.rejected,
            "warnings": self.warnings,
            "catalog_budget": self.catalog_budget,
        }


def public_skill_payload(skill: SkillInfo) -> dict[str, object]:
    """Return catalog-safe metadata with no SKILL.md instructions."""
    return {
        "name": skill.name,
        "description": skill.description,
        "path": skill.path,
        "skill_file": skill.skill_file,
        "source": skill.source,
        "priority": skill.priority,
        "trust": skill.trust,
        "risk_level": skill.risk_level,
        "version": skill.version,
        "roles": list(skill.roles),
        "compatible_roles": list(skill.roles),
        "stages": list(skill.stages),
        "file_patterns": list(skill.file_patterns),
        "keywords": list(skill.keywords),
        "disabled": skill.disabled,
        "auto": skill.auto,
        "permissions": skill.permissions.to_dict(),
        "validation_errors": list(skill.validation_errors),
        "validation_warnings": list(skill.validation_warnings),
    }
