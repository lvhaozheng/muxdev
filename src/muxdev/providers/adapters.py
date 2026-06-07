"""Runtime provider adapters.

Provider detection only reports capabilities; adapters are the execution bridge
used by the supervisor when a workflow stage needs an agent. The runtime path is
configuration-driven so new headless CLIs can be added with YAML before writing
provider-specific Python code.
"""

from __future__ import annotations

import shutil
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config.loader import load_config, path_config
from ..core.redaction import redact
from ..clients.sessions import HeadlessSubprocessBackend
from .mock import MockProvider


@dataclass(frozen=True)
class ProviderStageOutput:
    """Normalized stage result returned by every provider adapter."""

    artifact_name: str
    content: str
    summary: str
    tokens: int = 100
    cost_usd: float = 0.01
    returncode: int = 0
    provider_actions: list[dict[str, Any]] = field(default_factory=list)


class ProviderAdapter:
    """Minimal interface implemented by mock and headless CLI providers."""

    id = "provider"

    def run_stage(
        self,
        *,
        stage_id: str,
        task: str,
        worktree: Path,
        skills: list[dict[str, object]] | None = None,
    ) -> ProviderStageOutput:
        raise NotImplementedError


class MockProviderAdapter(ProviderAdapter):
    id = "mock"

    def __init__(self) -> None:
        self._mock = MockProvider()

    def run_stage(
        self,
        *,
        stage_id: str,
        task: str,
        worktree: Path,
        skills: list[dict[str, object]] | None = None,
    ) -> ProviderStageOutput:
        output = self._mock.run_stage(stage_id=stage_id, task=task, worktree=worktree)
        skill_lines = _skill_context_lines(skills or [], include_content=False)
        content = output.content
        if skill_lines:
            content += "\n\n# Active Skills\n" + "\n".join(skill_lines) + "\n"
        return ProviderStageOutput(
            artifact_name=output.artifact_name,
            content=content,
            summary=output.summary + (f"; skills={len(skills or [])}" if skills else ""),
            tokens=output.tokens,
            cost_usd=output.cost_usd,
        )


DEFAULT_PROMPT_TEMPLATE = (
    "You are running muxdev stage '{stage_id}'. "
    "Keep output concise. If you modify files, stay inside the current workspace. "
    "Task: {task}"
)


class HeadlessCliProviderAdapter(ProviderAdapter):
    def __init__(self, provider_id: str, command: list[str], *, timeout: float = 300, prompt_template: str = DEFAULT_PROMPT_TEMPLATE) -> None:
        self.id = provider_id
        self.command = command
        self.timeout = timeout
        self.prompt_template = prompt_template
        self.backend = HeadlessSubprocessBackend()

    def run_stage(
        self,
        *,
        stage_id: str,
        task: str,
        worktree: Path,
        skills: list[dict[str, object]] | None = None,
    ) -> ProviderStageOutput:
        """Execute one workflow stage in a worktree and archive transcripts."""
        prompt = self._prompt(stage_id, task, skills=skills or [])
        command = [*self.command, prompt]
        session_dir = path_config(worktree, "runtime_root") / "provider_sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = session_dir / f"{self.id}_{stage_id}.transcript.log"
        chunks_path = session_dir / f"{self.id}_{stage_id}.chunks.jsonl"
        result = self.backend.run(
            command,
            cwd=worktree,
            timeout=self.timeout,
            transcript_path=transcript_path,
            chunks_path=chunks_path,
        )
        content = redact((result.stdout or "") + (("\n" + result.stderr) if result.stderr else ""))
        event_lines = "\n".join(f"{event.type}: {event.text}" for event in result.events)
        if event_lines:
            content = content + "\n\n# Stream Events\n" + redact(event_lines) + "\n"
        content += f"\n# Session Archives\ntranscript: {transcript_path}\nchunks: {chunks_path}\n"
        provider_actions = [
            {
                "kind": action.kind,
                "prompt_text": action.prompt_text,
                "options": action.options,
                "transcript_path": str(transcript_path),
                "chunks_path": str(chunks_path),
            }
            for action in self.backend.adapter.provider_actions(result.events)
        ]
        return ProviderStageOutput(
            artifact_name=f"session/{self.id}_{stage_id}.log",
            content=content,
            summary=f"{self.id} {stage_id} exited with {result.returncode}",
            tokens=0,
            cost_usd=0,
            returncode=result.returncode,
            provider_actions=provider_actions,
        )

    def _prompt(self, stage_id: str, task: str, *, skills: list[dict[str, object]] | None = None) -> str:
        prompt = self.prompt_template.format(stage_id=stage_id, task=task)
        skills = skills or []
        if not skills:
            return prompt
        return prompt + "\n\n" + _skill_prompt_block(skills)


def get_runtime_provider(provider: str) -> ProviderAdapter:
    """Construct a runtime adapter from merged provider configuration."""
    config = load_config()
    provider_config = config.get("providers", {}).get(provider)
    if not isinstance(provider_config, dict):
        raise ValueError(f"unknown runtime provider: {provider}")
    runtime = provider_config.get("runtime", {})
    if not isinstance(runtime, dict):
        runtime = {}
    kind = runtime.get("kind", "headless_cli")
    if kind == "mock":
        return MockProviderAdapter()
    if kind != "headless_cli":
        raise ValueError(f"unsupported runtime kind for {provider}: {kind}")
    template_command = [str(item) for item in runtime.get("command", [])]
    if not template_command:
        template_command = [str(item) for item in provider_config.get("commands", [provider])]
    executable = _resolve_runtime_executable(template_command[0], [str(item) for item in provider_config.get("commands", [])])
    if not executable:
        raise ValueError(f"{provider} command not found; run muxdev provider install {provider} or muxdev provider doctor {provider}")
    return HeadlessCliProviderAdapter(
        provider,
        [executable, *template_command[1:]],
        timeout=float(runtime.get("timeout", 300)),
        prompt_template=str(runtime.get("prompt_template", DEFAULT_PROMPT_TEMPLATE)),
    )


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Extract the first JSON object embedded in provider text output."""
    for candidate in _json_candidates(text):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _json_candidates(text: str) -> list[str]:
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    candidates = [item.strip() for item in fenced]
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    return candidates


def _which_any(*commands: str) -> str | None:
    for command in commands:
        resolved = shutil.which(command)
        if resolved:
            return resolved
    return None


def _resolve_runtime_executable(primary: str, candidates: list[str]) -> str | None:
    resolved = shutil.which(primary)
    if resolved:
        return resolved
    return _which_any(*candidates)


def _skill_prompt_block(skills: list[dict[str, object]]) -> str:
    lines = ["# muxdev Skill Context", "Use the following skills when they are relevant to this stage."]
    for skill in skills:
        lines.extend(_skill_context_lines([skill], include_content=True))
    return "\n".join(lines)


def _skill_context_lines(skills: list[dict[str, object]], *, include_content: bool) -> list[str]:
    lines: list[str] = []
    for skill in skills:
        name = skill.get("name", "skill")
        role = skill.get("role") or "any"
        injection = skill.get("injection") or "prompt"
        path = skill.get("path") or skill.get("skill_file") or ""
        reason = skill.get("reason") or ""
        lines.append(f"- {name} role={role} injection={injection} reason={reason} path={path}")
        if include_content and skill.get("content"):
            lines.append("```markdown")
            lines.append(str(skill["content"]))
            lines.append("```")
    return lines
