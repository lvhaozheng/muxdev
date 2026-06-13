"""Provider-specific skill injection adapters."""

from __future__ import annotations

from .model import SkillInfo


def injection_mode(provider: str) -> str:
    provider_lc = provider.lower()
    if provider_lc in {"codex", "claude", "claude-code"}:
        return "native_or_passthrough"
    if provider_lc in {"qwen", "kimi"}:
        return "prompt"
    if provider_lc == "mock":
        return "context"
    return "prompt"


def activated_skill_wrapper(
    skill: SkillInfo,
    *,
    role: str | None,
    stage: str | None,
    provider: str,
    reason: str,
    content: str,
    tree_hash: str | None = None,
) -> str:
    attrs = {
        "name": skill.name,
        "version": skill.version or "",
        "hash": tree_hash or "",
        "role": role or "",
        "stage": stage or "",
        "provider": provider,
        "trust": skill.trust,
        "reason": reason,
    }
    attr_text = " ".join(f'{key}="{_escape(value)}"' for key, value in attrs.items() if value)
    return f"<activated_skill {attr_text}>\n{content}\n</activated_skill>"


def _escape(value: object) -> str:
    return str(value).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
