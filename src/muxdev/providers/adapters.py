"""Provider adapters with one method: execute(StageExecutionInput)."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

from ..config.loader import load_config
from ..core.platforms import hidden_subprocess_kwargs
from ..domain import StageExecutionInput, StageExecutionResult
from .contracts import ProviderAdapter
from .mock import MockProvider


DEFAULT_PROMPT = "Execute muxdev stage '{stage_id}' for this task: {task}"


class MockProviderAdapter:
    def execute(self, input: StageExecutionInput) -> StageExecutionResult:
        return MockProvider().execute(input)


class HeadlessCliProviderAdapter:
    def __init__(
        self,
        provider: str,
        command: list[str],
        *,
        timeout: float,
        prompt_template: str,
        prompt_transport: str,
    ) -> None:
        self.provider = provider
        self.command = command
        self.timeout = timeout
        self.prompt_template = prompt_template
        self.prompt_transport = prompt_transport

    def execute(self, input: StageExecutionInput) -> StageExecutionResult:
        prompt = self._prompt(input)
        command = [item.replace("{prompt}", prompt) for item in self.command]
        stdin = prompt if self.prompt_transport == "stdin" or not any("{prompt}" in item for item in self.command) else None
        try:
            completed = subprocess.run(
                command,
                cwd=input.worktree,
                input=stdin,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=min(self.timeout, float(input.policy.get("timeout_seconds") or self.timeout)),
                check=False,
                env={**os.environ, "MUXDEV_RUN_ID": input.run_id, "MUXDEV_STAGE_ID": input.stage_id},
                **hidden_subprocess_kwargs(),
            )
            stdout, stderr, returncode = completed.stdout or "", completed.stderr or "", completed.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = (exc.stderr if isinstance(exc.stderr, str) else "") + "\nprovider timed out"
            returncode = 124
        content = _provider_content(stdout, stderr)
        return StageExecutionResult(
            artifact_name=f"{input.stage_id}.log",
            content=content,
            summary=f"{self.provider} stage exited with {returncode}",
            stage_id=input.stage_id,
            provider=self.provider,
            status="completed" if returncode == 0 else "failed",
            returncode=returncode,
            stdout_hash=_hash(stdout),
            stderr_hash=_hash(stderr),
            stdout_bytes=len(stdout.encode()),
            stderr_bytes=len(stderr.encode()),
            output_refs=tuple(_output_refs(content)),
            cost_usd=0.0,
            tokens=0,
        )

    def _prompt(self, input: StageExecutionInput) -> str:
        prompt = self.prompt_template.format(stage_id=input.stage_id, task=input.task)
        schema = input.policy.get("output_schema")
        if schema:
            prompt += f"\nReturn a JSON object matching {schema}; do not declare a gate decision, confidence, or evidence score."
        repo_map = str(input.context.get("repo_map") or "").strip()
        if repo_map:
            prompt += f"\n\n# Deterministic repository map\n{repo_map}"
        for skill in input.skills:
            content = str(skill.get("content") or "").strip()
            if content:
                prompt += f"\n\n# Skill: {skill.get('name')}\n{content}"
        return prompt


def get_runtime_provider(provider: str, *, workspace: Path | None = None) -> ProviderAdapter:
    providers = load_config(workspace).get("providers", {})
    config = providers.get(provider) if isinstance(providers, dict) else None
    if not isinstance(config, dict):
        raise ValueError(f"unknown runtime provider: {provider}")
    runtime = config.get("runtime") if isinstance(config.get("runtime"), dict) else {}
    if runtime.get("kind") == "mock":
        return MockProviderAdapter()
    template = [str(item) for item in runtime.get("command", [])]
    if not template:
        template = [str(item) for item in config.get("commands", [provider])]
    executable = _resolve_executable(template[0], [str(item) for item in config.get("commands", [])])
    if not executable:
        raise ValueError(f"{provider} command is not installed")
    return HeadlessCliProviderAdapter(
        provider,
        [executable, *template[1:]],
        timeout=float(runtime.get("timeout", 300)),
        prompt_template=str(runtime.get("prompt_template", DEFAULT_PROMPT)),
        prompt_transport=str(runtime.get("prompt_transport") or ("stdin" if provider == "codex" else "argument")),
    )


def _resolve_executable(primary: str, candidates: list[str]) -> str | None:
    for command in (primary, *candidates):
        if resolved := shutil.which(command):
            return resolved
    return None


def _provider_content(stdout: str, stderr: str) -> str:
    # Codex and similar JSONL CLIs include the assistant's final text as an
    # event. Prefer that text while retaining stderr for objective diagnostics.
    messages = re.findall(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"', stdout)
    body = bytes(messages[-1], "utf-8").decode("unicode_escape") if messages else stdout
    return body + (f"\n\n# stderr\n{stderr}" if stderr else "")


def _hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _output_refs(content: str) -> list[str]:
    return sorted(set(re.findall(r"(?:^|\s)([\w./\\-]+\.[A-Za-z0-9]{1,8})(?:\s|$)", content)))[:100]
