"""Runtime provider adapters.

Provider detection only reports capabilities; adapters are the execution bridge
used by the supervisor when a workflow stage needs an agent. The runtime path is
configuration-driven so new headless CLIs can be added with YAML before writing
provider-specific Python code.
"""

from __future__ import annotations

import shutil
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config.loader import load_config, path_config
from ..core.redaction import redact
from ..clients.sessions import HeadlessSubprocessBackend
from .contracts import ProviderCapabilities, ProviderDescriptor, ProviderRuntimeKind
from .mock import MockProvider


EVIDENCE_PROMPT_BLOCK = """# muxdev Evidence v2 Contract
Return structured evidence whenever this stage makes a delivery claim. Do not only say "done", "tests passed", or "looks good".
Each conclusion should be expressible as an event with a layer, kind, status, strength, and artifact reference. If you did not run tests, list that in missing_evidence. Model-only judgments must use strength "D".

Use this JSON shape when possible:
{
  "summary": "...",
  "claims": [{"id": "claim-1", "text": "...", "supports_acceptance": ["AC-1"]}],
  "evidence": [{"claim_id": "claim-1", "layer": "core", "kind": "change", "status": "observed", "strength": "B", "files": ["path"], "summary": "..."}],
  "tests": [{"command": "pytest -q", "exit_code": 0, "relevance": "targeted", "summary": "..."}],
  "missing_evidence": ["..."],
  "risks": [{"severity": "medium", "reason": "..."}]
}
"""


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
    descriptor = ProviderDescriptor(id="provider")

    def run_stage(
        self,
        *,
        stage_id: str,
        task: str,
        worktree: Path,
        skills: list[dict[str, object]] | None = None,
        session_dir: Path | None = None,
    ) -> ProviderStageOutput:
        raise NotImplementedError


class MockProviderAdapter(ProviderAdapter):
    id = "mock"
    descriptor = ProviderDescriptor(
        id="mock",
        runtime_kind=ProviderRuntimeKind.MOCK,
        roles=frozenset({"design", "code", "test", "review"}),
        capabilities=ProviderCapabilities(),
        metadata={"builtin": True},
    )

    def __init__(self) -> None:
        self._mock = MockProvider()

    def run_stage(
        self,
        *,
        stage_id: str,
        task: str,
        worktree: Path,
        skills: list[dict[str, object]] | None = None,
        session_dir: Path | None = None,
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
    def __init__(
        self,
        provider_id: str,
        command: list[str],
        *,
        timeout: float = 300,
        prompt_template: str = DEFAULT_PROMPT_TEMPLATE,
        prompt_transport: str = "argument",
    ) -> None:
        self.id = provider_id
        self.command = command
        self.timeout = timeout
        self.prompt_template = prompt_template
        self.prompt_transport = prompt_transport
        self.backend = HeadlessSubprocessBackend()
        self.descriptor = ProviderDescriptor(
            id=provider_id,
            commands=tuple(command),
            runtime_kind=ProviderRuntimeKind.HEADLESS_CLI,
            metadata={"timeout": timeout, "prompt_transport": prompt_transport},
        )

    def run_stage(
        self,
        *,
        stage_id: str,
        task: str,
        worktree: Path,
        skills: list[dict[str, object]] | None = None,
        session_dir: Path | None = None,
    ) -> ProviderStageOutput:
        """Execute one workflow stage in a worktree and archive transcripts."""
        prompt = self._prompt(stage_id, task, skills=skills or [])
        command, input_text = _command_for_prompt(self.command, prompt, transport=self.prompt_transport)
        session_dir = session_dir or path_config(worktree, "runtime_root") / "provider_sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = session_dir / f"{self.id}_{stage_id}.transcript.log"
        chunks_path = session_dir / f"{self.id}_{stage_id}.chunks.jsonl"
        result = self.backend.run(
            command,
            cwd=worktree,
            timeout=self.timeout,
            transcript_path=transcript_path,
            chunks_path=chunks_path,
            input_text=input_text,
            env=_provider_runtime_env(self.id, worktree),
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
                "input_kind": "confirmation" if action.kind == "cli_confirmation" else ("external" if not action.options else "choice"),
                "choices": action.options,
                "default_choice": _default_choice(action.options),
                "auto_policy": "manual",
                "transcript_path": str(transcript_path),
                "chunks_path": str(chunks_path),
            }
            for action in self.backend.adapter.provider_actions(result.events)
        ]
        summary = _provider_stage_summary(self.id, stage_id, result.returncode, content)
        return ProviderStageOutput(
            artifact_name=f"session/{self.id}_{stage_id}.log",
            content=content,
            summary=summary,
            tokens=0,
            cost_usd=0,
            returncode=result.returncode,
            provider_actions=provider_actions,
        )

    def _prompt(self, stage_id: str, task: str, *, skills: list[dict[str, object]] | None = None) -> str:
        prompt = self.prompt_template.format(stage_id=stage_id, task=task) + "\n\n" + EVIDENCE_PROMPT_BLOCK
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
    prompt_transport = runtime.get("prompt_transport")
    if prompt_transport is None and provider == "codex":
        prompt_transport = "stdin"
    return HeadlessCliProviderAdapter(
        provider,
        [executable, *template_command[1:]],
        timeout=float(runtime.get("timeout", 300)),
        prompt_template=str(runtime.get("prompt_template", DEFAULT_PROMPT_TEMPLATE)),
        prompt_transport=str(prompt_transport or "argument"),
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


def _provider_runtime_env(provider_id: str, worktree: Path) -> dict[str, str]:
    if provider_id != "codex":
        return {}
    codex_home = _prepare_codex_home(worktree)
    return {"CODEX_HOME": str(codex_home)}


def _prepare_codex_home(worktree: Path) -> Path:
    target = _provider_state_dir("codex", worktree)
    target.mkdir(parents=True, exist_ok=True)
    source = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        if source.exists() and source.resolve() != target.resolve():
            _seed_codex_home(source, target)
    except OSError:
        pass
    return target


def _provider_state_dir(provider_id: str, worktree: Path) -> Path:
    muxdev_home = os.environ.get("MUXDEV_HOME")
    if muxdev_home:
        return Path(muxdev_home) / "data" / "provider_state" / provider_id
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "muxdev" / "data" / "provider_state" / provider_id
    return path_config(worktree, "runtime_root") / "provider_state" / provider_id


def _seed_codex_home(source: Path, target: Path) -> None:
    for name in ("auth.json", "config.toml"):
        src = source / name
        dst = target / name
        if not src.is_file():
            continue
        try:
            if not dst.exists() or src.stat().st_mtime > dst.stat().st_mtime:
                shutil.copy2(src, dst)
        except OSError:
            continue


def _command_for_prompt(command: list[str], prompt: str, *, transport: str) -> tuple[list[str], str | None]:
    if any("{prompt}" in item for item in command):
        return [item.replace("{prompt}", prompt) for item in command], None
    if transport == "stdin":
        return list(command), prompt
    if transport == "dash-stdin":
        return [*command, "-"], prompt
    return [*command, prompt], None


def _provider_stage_summary(provider_id: str, stage_id: str, returncode: int, output: str) -> str:
    base = f"{provider_id} {stage_id} exited with {returncode}"
    if returncode == 0:
        return base
    excerpt = _failure_excerpt(output)
    return f"{base}: {excerpt}" if excerpt else base


def _failure_excerpt(output: str, *, max_chars: int = 320) -> str:
    clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", output)
    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    ignored_prefixes = ("# Stream Events", "# Session Archives", "transcript:", "chunks:", "cli_exited:")
    useful = [
        line
        for line in lines
        if not any(line.startswith(prefix) for prefix in ignored_prefixes)
    ]
    if not useful:
        return ""
    excerpt = "\n".join(useful[-4:])
    if len(excerpt) <= max_chars:
        return excerpt
    return excerpt[-max_chars:].lstrip()


def _default_choice(options: list[dict[str, object]]) -> str | None:
    for option in options:
        if option.get("default") and option.get("value") is not None:
            return str(option["value"])
    return None


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
