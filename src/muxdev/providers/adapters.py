"""Runtime provider adapters.

Provider detection only reports capabilities; adapters are the execution bridge
used by the supervisor when a workflow stage needs an agent. The runtime path is
configuration-driven so new headless CLIs can be added with YAML before writing
provider-specific Python code.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from ..clients.sessions import HeadlessSubprocessBackend
from ..config.loader import load_config, path_config
from ..core.redaction import redact
from ..core.platforms import hidden_subprocess_kwargs
from ..core.text_cleaning import clean_provider_text
from ..core.private_paths import muxdev_private_data_dir
from .certification import make_certification_report, require_live_acknowledgement, sha256_file, sha256_text
from .contracts import ProviderCapabilities, ProviderDescriptor, ProviderRuntimeKind
from .harness import (
    ADAPTER_CONTRACT_VERSION,
    AdapterProbe,
    AttemptHandle,
    CancelResult,
    CapabilityVerificationState,
    CertificationReport,
    HarnessEvent,
    HarnessEventSource,
    TrustTier,
    Unsupported,
    capability_map,
)
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
    harness_events: tuple[HarnessEvent, ...] = ()


class ProviderAdapter:
    """Versioned Agent Harness contract with a legacy ``run_stage`` facade."""

    id = "provider"
    adapter_version = f"generic/{ADAPTER_CONTRACT_VERSION}"
    trust_tier = TrustTier.OPAQUE
    descriptor = ProviderDescriptor(id="provider")

    def probe(self) -> AdapterProbe:
        return AdapterProbe(
            provider=self.id,
            available=False,
            executable=None,
            provider_version=None,
            executable_fingerprint=None,
            help_fingerprint=None,
            adapter_version=self.adapter_version,
            platform=platform.platform(),
            diagnostics=("adapter does not implement probe",),
        )

    def certify(
        self,
        *,
        live: bool = False,
        acknowledged: bool = False,
        max_cost_usd: float | None = None,
    ) -> CertificationReport:
        require_live_acknowledgement(live=live, acknowledged=acknowledged, max_cost_usd=max_cost_usd)
        probe = self.probe()
        return make_certification_report(
            probe=probe,
            capabilities=capability_map(),
            trust_tier=self.trust_tier,
            live=live,
            evidence={"checks": ["generic adapter remains experimental and uncertified"]},
            failure="generic CLI adapter is not on the v0.2 certified path",
        )

    def start(self, **kwargs: object) -> AttemptHandle:
        return AttemptHandle(
            attempt_id=f"attempt_{uuid4().hex}",
            provider=self.id,
            stage_id=str(kwargs.get("stage_id") or "stage"),
            worktree=Path(str(kwargs.get("worktree") or Path.cwd())).resolve(),
            metadata=dict(kwargs),
        )

    def events(self, handle: AttemptHandle) -> Iterator[HarnessEvent]:
        raise NotImplementedError

    def cancel(self, handle: AttemptHandle) -> CancelResult:
        token = getattr(self, "cancellation_token", None)
        if token is not None and hasattr(token, "request_cancel"):
            token.request_cancel("adapter cancellation requested")
            return CancelResult(status="cancel_requested", cooperative=True)
        return CancelResult(status="unsupported", cooperative=False, detail="adapter has no cancellation token")

    def resume(self, handle: AttemptHandle) -> AttemptHandle | Unsupported:
        return Unsupported("crash-safe provider resume is not verified")

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
    adapter_version = f"mock/{ADAPTER_CONTRACT_VERSION}"
    trust_tier = TrustTier.MANAGED
    descriptor = ProviderDescriptor(
        id="mock",
        runtime_kind=ProviderRuntimeKind.MOCK,
        roles=frozenset({"design", "code", "test", "review"}),
        capabilities=ProviderCapabilities(),
        trust_tier="managed",
        metadata={"builtin": True},
    )

    def __init__(self) -> None:
        self._mock = MockProvider()

    def probe(self) -> AdapterProbe:
        return AdapterProbe(
            provider=self.id,
            available=True,
            executable=None,
            provider_version="builtin",
            executable_fingerprint="builtin-mock",
            help_fingerprint="builtin-mock",
            adapter_version=self.adapter_version,
            platform=platform.platform(),
            advertised_capabilities=tuple(capability_map().keys()),
        )

    def certify(
        self,
        *,
        live: bool = False,
        acknowledged: bool = False,
        max_cost_usd: float | None = None,
    ) -> CertificationReport:
        require_live_acknowledgement(live=live, acknowledged=acknowledged, max_cost_usd=max_cost_usd)
        capabilities = capability_map(
            structured_events="verified",
            tool_events="verified",
            usage="verified",
            session_resume="verified",
            cooperative_cancel="verified",
            approval_bridge="verified",
            read_only="verified",
            patch_output="verified",
            provider_sandbox="verified",
        )
        return make_certification_report(
            probe=self.probe(),
            capabilities=capabilities,
            trust_tier=self.trust_tier,
            live=live,
            evidence={"checks": ["deterministic lifecycle", "cancellation", "sandbox sentinel"]},
        )

    def events(self, handle: AttemptHandle) -> Iterator[HarnessEvent]:
        run_id = str(handle.metadata.get("run_id") or "unbound")
        attempt = int(handle.metadata.get("attempt") or 1)
        task = str(handle.metadata.get("task") or "")
        skills = handle.metadata.get("skills")
        output = self._legacy_run_stage(
            stage_id=handle.stage_id,
            task=task,
            worktree=handle.worktree,
            skills=skills if isinstance(skills, list) else None,
        )
        events = _build_harness_events(
            run_id=run_id,
            stage_id=handle.stage_id,
            provider=self.id,
            attempt=attempt,
            decoded=[
                ("harness.attempt_started", HarnessEventSource.HARNESS, {"managed": True}),
                ("provider.tool.completed", HarnessEventSource.PROVIDER, {"tool": "mock", "status": "ok"}),
                ("provider.usage", HarnessEventSource.PROVIDER, {"tokens": output.tokens, "cost_usd": output.cost_usd}),
                ("harness.attempt_completed", HarnessEventSource.HARNESS, {"returncode": output.returncode}),
            ],
        )
        output = ProviderStageOutput(**{**output.__dict__, "harness_events": tuple(events)})
        handle.metadata["output"] = output
        yield from events

    def resume(self, handle: AttemptHandle) -> AttemptHandle | Unsupported:
        resumed = AttemptHandle(
            attempt_id=f"attempt_{uuid4().hex}",
            provider=self.id,
            stage_id=handle.stage_id,
            worktree=handle.worktree,
            session_id=handle.session_id,
            metadata={**handle.metadata, "resumed_from": handle.attempt_id},
        )
        return resumed

    def run_stage(
        self,
        *,
        stage_id: str,
        task: str,
        worktree: Path,
        skills: list[dict[str, object]] | None = None,
        session_dir: Path | None = None,
        run_id: str | None = None,
        attempt: int = 1,
    ) -> ProviderStageOutput:
        handle = self.start(stage_id=stage_id, task=task, worktree=worktree, skills=skills or [], run_id=run_id, attempt=attempt)
        tuple(self.events(handle))
        return handle.metadata["output"]  # type: ignore[return-value]

    def _legacy_run_stage(
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
        self.adapter_version = f"generic-cli/{ADAPTER_CONTRACT_VERSION}"
        self.trust_tier = TrustTier.OPAQUE
        self.command = command
        self.timeout = timeout
        self.prompt_template = prompt_template
        self.prompt_transport = prompt_transport
        self.backend = HeadlessSubprocessBackend()
        self.cancellation_token: object | None = None
        self.cancel_grace_seconds = 10.0
        self.descriptor = ProviderDescriptor(
            id=provider_id,
            commands=tuple(command),
            runtime_kind=ProviderRuntimeKind.HEADLESS_CLI,
            trust_tier="opaque",
            metadata={"timeout": timeout, "prompt_transport": prompt_transport},
        )

    def probe(self) -> AdapterProbe:
        executable = Path(self.command[0])
        resolved = executable if executable.is_file() else Path(shutil.which(self.command[0]) or "")
        if not resolved or not resolved.is_file():
            return AdapterProbe(
                provider=self.id,
                available=False,
                executable=None,
                provider_version=None,
                executable_fingerprint=None,
                help_fingerprint=None,
                adapter_version=self.adapter_version,
                platform=platform.platform(),
                diagnostics=("executable not found",),
            )
        version = _safe_static_command([str(resolved), "--version"])
        help_result = _safe_static_command([str(resolved), "--help"])
        advertised = self._advertised_capabilities(help_result)
        return AdapterProbe(
            provider=self.id,
            available=True,
            executable=str(resolved),
            provider_version=_first_line(version),
            executable_fingerprint=sha256_file(resolved),
            help_fingerprint=sha256_text([help_result]),
            adapter_version=self.adapter_version,
            platform=platform.platform(),
            advertised_capabilities=tuple(sorted(advertised)),
        )

    def certify(
        self,
        *,
        live: bool = False,
        acknowledged: bool = False,
        max_cost_usd: float | None = None,
    ) -> CertificationReport:
        require_live_acknowledgement(live=live, acknowledged=acknowledged, max_cost_usd=max_cost_usd)
        probe = self.probe()
        if type(self) is HeadlessCliProviderAdapter:
            return make_certification_report(
                probe=probe,
                capabilities={name: CapabilityVerificationState.ADVERTISED for name in probe.advertised_capabilities},
                trust_tier=self.trust_tier,
                live=live,
                evidence={"checks": ["static executable and help probe"]},
                failure="generic CLI adapter remains uncertified and experimental",
            )
        capabilities, evidence, failure = self._certification_checks(probe, live=live, max_cost_usd=max_cost_usd)
        return make_certification_report(
            probe=probe,
            capabilities=capabilities,
            trust_tier=self.trust_tier,
            live=live,
            evidence=evidence,
            failure=failure,
        )

    def start(self, **kwargs: object) -> AttemptHandle:
        handle = super().start(**kwargs)
        handle.session_id = str(kwargs.get("session_id") or f"session_{uuid4().hex}")
        return handle

    def events(self, handle: AttemptHandle) -> Iterator[HarnessEvent]:
        output, decoded = self._execute_handle(handle)
        events = _build_harness_events(
            run_id=str(handle.metadata.get("run_id") or "unbound"),
            stage_id=handle.stage_id,
            provider=self.id,
            attempt=int(handle.metadata.get("attempt") or 1),
            decoded=decoded,
        )
        output = ProviderStageOutput(**{**output.__dict__, "harness_events": tuple(events)})
        handle.metadata["output"] = output
        yield from events

    def set_cancellation_token(self, token: object, *, grace_seconds: float = 10.0) -> None:
        self.cancellation_token = token
        self.cancel_grace_seconds = max(0.0, float(grace_seconds))

    def run_stage(
        self,
        *,
        stage_id: str,
        task: str,
        worktree: Path,
        skills: list[dict[str, object]] | None = None,
        session_dir: Path | None = None,
        run_id: str | None = None,
        attempt: int = 1,
    ) -> ProviderStageOutput:
        """Compatibility facade implemented through the lifecycle contract."""
        handle = self.start(
            stage_id=stage_id,
            task=task,
            worktree=worktree,
            skills=skills or [],
            session_dir=session_dir,
            run_id=run_id,
            attempt=attempt,
        )
        tuple(self.events(handle))
        return handle.metadata["output"]  # type: ignore[return-value]

    def _execute_handle(self, handle: AttemptHandle) -> tuple[ProviderStageOutput, list[tuple[str, HarnessEventSource, dict[str, object]]]]:
        stage_id = handle.stage_id
        task = str(handle.metadata.get("task") or "")
        skills_value = handle.metadata.get("skills")
        skills = skills_value if isinstance(skills_value, list) else []
        prompt = self._prompt(stage_id, task, skills=skills)
        command, input_text = _command_for_prompt(self.command, prompt, transport=self.prompt_transport)
        worktree = handle.worktree
        requested_session_dir = handle.metadata.get("session_dir")
        session_dir = Path(str(requested_session_dir)) if requested_session_dir else path_config(worktree, "runtime_root") / "provider_sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = session_dir / f"{self.id}_{stage_id}.transcript.log"
        chunks_path = session_dir / f"{self.id}_{stage_id}.chunks.jsonl"
        cancellation_kwargs: dict[str, object] = {}
        if self.cancellation_token is not None:
            binder = getattr(self.cancellation_token, "bind_provider_attempt", None)
            if callable(binder):
                binder(
                    run_id=str(handle.metadata.get("run_id") or "unbound"),
                    stage_id=stage_id,
                    provider=self.id,
                    attempt=int(handle.metadata.get("attempt") or 1),
                )
            cancellation_kwargs = {
                "cancellation_token": self.cancellation_token,
                "cancel_grace_seconds": self.cancel_grace_seconds,
            }
        result = self.backend.run(
            command,
            cwd=worktree,
            timeout=self.timeout,
            transcript_path=transcript_path,
            chunks_path=chunks_path,
            input_text=input_text,
            env=_provider_runtime_env(self.id, worktree),
            **cancellation_kwargs,
        )
        raw_content = (result.stdout or "") + (("\n" + result.stderr) if result.stderr else "")
        content = redact(clean_provider_text(raw_content, fallback=raw_content))
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
        decoded = [("harness.attempt_started", HarnessEventSource.HARNESS, {"session_id": handle.session_id})]
        decoded.extend(self._decode_provider_events(result.stdout or "", returncode=result.returncode))
        decoded.append(("harness.attempt_completed", HarnessEventSource.HARNESS, {"returncode": result.returncode}))
        return ProviderStageOutput(
            artifact_name=f"session/{self.id}_{stage_id}.log",
            content=content,
            summary=summary,
            tokens=0,
            cost_usd=0,
            returncode=result.returncode,
            provider_actions=provider_actions,
        ), decoded

    def _decode_provider_events(self, output: str, *, returncode: int) -> list[tuple[str, HarnessEventSource, dict[str, object]]]:
        decoded: list[tuple[str, HarnessEventSource, dict[str, object]]] = []
        for line_number, line in enumerate(output.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                decoded.append(("provider.unknown", HarnessEventSource.PROVIDER, {"line": line_number, "reason": "invalid_json"}))
                continue
            decoded.append(("provider.unknown", HarnessEventSource.PROVIDER, {"line": line_number, "event": payload}))
        if not decoded:
            decoded.append(("provider.terminal", HarnessEventSource.DERIVED, {"returncode": returncode}))
        return decoded

    def _advertised_capabilities(self, help_text: str) -> set[str]:
        lowered = help_text.lower()
        advertised: set[str] = set()
        if "json" in lowered:
            advertised.add("structured_events")
        if "resume" in lowered or "continue" in lowered:
            advertised.add("session_resume")
        if "sandbox" in lowered:
            advertised.add("provider_sandbox")
        if "read-only" in lowered:
            advertised.add("read_only")
        return advertised

    def _certification_checks(
        self,
        probe: AdapterProbe,
        *,
        live: bool,
        max_cost_usd: float | None,
    ) -> tuple[dict[str, CapabilityVerificationState], dict[str, object], str | None]:
        return capability_map(), {"checks": ["no provider-specific certification suite"]}, "uncertified adapter"

    def _prompt(self, stage_id: str, task: str, *, skills: list[dict[str, object]] | None = None) -> str:
        prompt = self.prompt_template.format(stage_id=stage_id, task=task) + "\n\n" + EVIDENCE_PROMPT_BLOCK
        skills = skills or []
        if not skills:
            return prompt
        return prompt + "\n\n" + _skill_prompt_block(skills)


def get_runtime_provider(provider: str) -> ProviderAdapter:
    """Construct a runtime adapter from merged provider configuration."""
    if provider == "replay":
        from .certified import ReplayAdapter

        return ReplayAdapter(())
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
    adapter_type: type[HeadlessCliProviderAdapter] = HeadlessCliProviderAdapter
    if provider == "codex":
        from .certified import CodexHarnessAdapter

        adapter_type = CodexHarnessAdapter
    elif provider == "qwen":
        from .certified import QwenHarnessAdapter

        adapter_type = QwenHarnessAdapter
    command = [executable, *template_command[1:]]
    common = {
        "timeout": float(runtime.get("timeout", 300)),
        "prompt_template": str(runtime.get("prompt_template", DEFAULT_PROMPT_TEMPLATE)),
        "prompt_transport": str(prompt_transport or "argument"),
    }
    if adapter_type is HeadlessCliProviderAdapter:
        return adapter_type(provider, command, **common)
    return adapter_type(command, **common)


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
    if provider_id == "codex":
        codex_home = _prepare_codex_home(worktree)
        return {"CODEX_HOME": str(codex_home)}
    if provider_id == "qwen":
        runtime_dir = _provider_state_dir("qwen", worktree)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        _restrict_permissions(runtime_dir, directory=True)
        return {"QWEN_RUNTIME_DIR": str(runtime_dir)}
    return {}


def _prepare_codex_home(worktree: Path) -> Path:
    target = _provider_state_dir("codex", worktree)
    target.mkdir(parents=True, exist_ok=True)
    _restrict_permissions(target, directory=True)
    source = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        if source.exists() and source.resolve() != target.resolve():
            _seed_codex_home(source, target)
    except OSError:
        pass
    return target


def _provider_state_dir(provider_id: str, worktree: Path) -> Path:
    del worktree
    # Provider credentials and signing keys share the daemon-private data root
    # but remain in physically separate subtrees. Never fall back into a user
    # project or worktree.
    return muxdev_private_data_dir() / "provider_state" / provider_id


def _seed_codex_home(source: Path, target: Path) -> None:
    # The credential is copied into daemon-private state. User configuration,
    # extensions, MCP servers, and arbitrary global settings are never copied.
    for name in ("auth.json",):
        src = source / name
        dst = target / name
        if not src.is_file():
            continue
        try:
            if not dst.exists() or src.stat().st_mtime > dst.stat().st_mtime:
                shutil.copy2(src, dst)
                _restrict_permissions(dst, directory=False)
        except OSError:
            continue


def _restrict_permissions(path: Path, *, directory: bool) -> None:
    try:
        path.chmod(0o700 if directory else 0o600)
    except OSError:
        pass


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


def _safe_static_command(command: list[str], *, timeout: float = 8.0) -> str:
    """Run a bounded version/help probe without credentials, prompts, or model calls."""
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=_static_probe_env(),
            **hidden_subprocess_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (completed.stdout or "") + (completed.stderr or "")


def _static_probe_env() -> dict[str, str]:
    allowed = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "TMP",
        "TEMP",
        "LANG",
        "LC_ALL",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
    }
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


def _first_line(text: str) -> str | None:
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:200]
    return None


def _build_harness_events(
    *,
    run_id: str,
    stage_id: str,
    provider: str,
    attempt: int,
    decoded: list[tuple[str, HarnessEventSource, dict[str, object]]],
) -> list[HarnessEvent]:
    events: list[HarnessEvent] = []
    prev_hash: str | None = None
    for sequence, (event_type, source, payload) in enumerate(decoded, start=1):
        event = HarnessEvent.create(
            run_id=run_id,
            stage_id=stage_id,
            provider=provider,
            attempt=attempt,
            sequence=sequence,
            event_type=event_type,
            source=source,
            idempotency_key=f"{run_id}:{stage_id}:{provider}:{attempt}:{sequence}:{event_type}",
            payload=payload,
            prev_hash=prev_hash,
        )
        events.append(event)
        prev_hash = event.event_hash
    return events
