"""Static provider capability probing.

The registry answers "what provider CLIs appear usable on this machine?" without
making model calls or mutating repositories. Probes are intentionally limited to
version/help/doctor-style commands so `muxdev provider detect` is safe to run in
fresh workspaces, CI, and unauthenticated environments.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Callable, Iterable

from ..config.loader import load_config
from ..core.platforms import hidden_subprocess_kwargs, script_invocation

PROBE_TIMEOUT_SECONDS = 8


class CapabilityState(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"
    NOT_INSTALLED = "not_installed"


class ProviderStatus(StrEnum):
    READY = "ready"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    error: str | None = None

    @property
    def text(self) -> str:
        return f"{self.stdout}\n{self.stderr}".strip()


@dataclass(frozen=True)
class ProviderProbe:
    provider: str
    mode: str
    command: str
    installed: bool
    version: str | None
    headless: CapabilityState
    pty: CapabilityState
    json: CapabilityState
    approval: CapabilityState
    skill: CapabilityState
    attach: CapabilityState
    status: ProviderStatus
    notes: str

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        for key in ("headless", "pty", "json", "approval", "skill", "attach", "status"):
            data[key] = str(data[key])
        return data


@dataclass(frozen=True)
class ProviderDefinition:
    provider: str
    mode: str
    commands: tuple[str, ...]
    status_hint: str


PROVIDERS: tuple[ProviderDefinition, ...] = tuple()


Runner = Callable[[str, Iterable[str]], CommandResult]
Which = Callable[[str], str | None]


def default_runner(command: str, args: Iterable[str]) -> CommandResult:
    """Run one bounded, read-only provider probe command."""
    command_args = tuple(args)
    invocation = _build_invocation(command, command_args)
    try:
        completed = subprocess.run(
            invocation,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
            **hidden_subprocess_kwargs(),
        )
        return CommandResult(
            args=tuple(invocation),
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            args=tuple(invocation),
            returncode=124,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "",
            timed_out=True,
            error="probe timed out",
        )
    except OSError as exc:
        return CommandResult(
            args=tuple(invocation),
            returncode=127,
            stdout="",
            stderr="",
            error=str(exc),
        )


def _build_invocation(command: str, args: tuple[str, ...]) -> list[str]:
    return script_invocation(command, args)


def detect_providers(
    *,
    which: Which = shutil.which,
    runner: Runner = default_runner,
) -> list[ProviderProbe]:
    """Probe every configured provider and preserve config order."""
    return [probe_provider(definition.provider, which=which, runner=runner) for definition in provider_definitions()]


def probe_provider(
    provider: str,
    *,
    which: Which = shutil.which,
    runner: Runner = default_runner,
) -> ProviderProbe:
    """Probe a single provider by combining config metadata and CLI help text."""
    definition = get_provider_definition(provider)
    if definition.provider == "mock":
        return _mock_probe()

    command = _resolve_command(definition.commands, which)
    if command is None:
        return _unavailable_probe(definition)

    probe_kind = _provider_config(definition.provider).get("probe", "generic")
    if probe_kind == "codex":
        return _probe_codex(definition, command, runner)

    version = _extract_version(runner(command, ("--version",)).text)
    help_text = runner(command, ("--help",)).text
    lower_help = help_text.lower()
    return ProviderProbe(
        provider=definition.provider,
        mode=definition.mode,
        command=command,
        installed=True,
        version=version,
        headless=_supported_if(any(term in lower_help for term in ("--print", "-p", "headless", "exec", "non-interactive"))),
        pty=_supported_if(any(term in lower_help for term in ("interactive", "tty", "terminal"))),
        json=_supported_if("--json" in lower_help or "stream-json" in lower_help),
        approval=_supported_if("approval" in lower_help or "permission" in lower_help),
        skill=_supported_if("skill" in lower_help or "plugin" in lower_help),
        attach=_supported_if("resume" in lower_help or "attach" in lower_help),
        status=ProviderStatus.PARTIAL,
        notes=f"installed; generic static probe ({definition.status_hint})",
    )


def get_provider_definition(provider: str) -> ProviderDefinition:
    normalized = provider.lower()
    for definition in provider_definitions():
        if definition.provider == normalized:
            return definition
    known = ", ".join(definition.provider for definition in provider_definitions())
    raise ValueError(f"unknown provider '{provider}'. Known providers: {known}")


def provider_definitions() -> tuple[ProviderDefinition, ...]:
    """Build provider definitions dynamically from merged configuration."""
    providers = load_config().get("providers", {})
    definitions: list[ProviderDefinition] = []
    for name, data in providers.items():
        definitions.append(
            ProviderDefinition(
                str(name),
                str(data.get("mode", "local CLI")),
                tuple(str(command) for command in data.get("commands", [name])),
                str(data.get("status_hint", "")),
            )
        )
    return tuple(definitions)


def _mock_probe() -> ProviderProbe:
    return ProviderProbe(
        provider="mock",
        mode="builtin",
        command="mock",
        installed=True,
        version="builtin",
        headless=CapabilityState.SUPPORTED,
        pty=CapabilityState.SUPPORTED,
        json=CapabilityState.SUPPORTED,
        approval=CapabilityState.SUPPORTED,
        skill=CapabilityState.SUPPORTED,
        attach=CapabilityState.SUPPORTED,
        status=ProviderStatus.READY,
        notes=str(_provider_config("mock").get("notes", "built-in deterministic provider for workflow and matrix tests")),
    )


def _unavailable_probe(definition: ProviderDefinition) -> ProviderProbe:
    return ProviderProbe(
        provider=definition.provider,
        mode=definition.mode,
        command=definition.commands[0],
        installed=False,
        version=None,
        headless=CapabilityState.NOT_INSTALLED,
        pty=CapabilityState.NOT_INSTALLED,
        json=CapabilityState.NOT_INSTALLED,
        approval=CapabilityState.NOT_INSTALLED,
        skill=CapabilityState.NOT_INSTALLED,
        attach=CapabilityState.NOT_INSTALLED,
        status=ProviderStatus.UNAVAILABLE,
        notes=f"command not found ({definition.status_hint})",
    )


def _probe_codex(
    definition: ProviderDefinition,
    command: str,
    runner: Runner,
) -> ProviderProbe:
    """Use Codex-specific help text to infer richer capability hints.

    Codex has well-known subcommands and flags, so static help inspection can
    distinguish headless execution, JSON output, approval control, plugin
    support, and resume/attach affordances more accurately than the generic
    provider probe.
    """
    version_result = runner(command, ("--version",))
    root_help = runner(command, ("--help",)).text
    exec_help = runner(command, ("exec", "--help")).text
    doctor_help = runner(command, ("doctor", "--help")).text
    plugin_help = runner(command, ("plugin", "--help")).text
    combined = "\n".join([root_help, exec_help, doctor_help, plugin_help])

    headless = _supported_if("exec" in root_help and "Run Codex non-interactively" in exec_help)
    pty = _supported_if("interactive CLI" in root_help or "--no-alt-screen" in root_help)
    json_capability = _supported_if("--json" in exec_help or "--json" in doctor_help)
    approval = _supported_if("--ask-for-approval" in combined)
    skill = _supported_if("plugin" in root_help and "Manage Codex plugins" in plugin_help)
    attach = _supported_if("resume" in root_help or "resume" in exec_help)
    status = ProviderStatus.READY if headless == CapabilityState.SUPPORTED else ProviderStatus.PARTIAL

    notes = ["static probe only"]
    if version_result.returncode != 0:
        notes.append("version command returned non-zero")
    if any(result.timed_out for result in (version_result,)):
        notes.append("one or more probes timed out")

    return ProviderProbe(
        provider=definition.provider,
        mode=definition.mode,
        command=command,
        installed=True,
        version=_extract_version(version_result.text),
        headless=headless,
        pty=pty,
        json=json_capability,
        approval=approval,
        skill=skill,
        attach=attach,
        status=status,
        notes="; ".join(notes),
    )


def _resolve_command(commands: tuple[str, ...], which: Which) -> str | None:
    for command in commands:
        resolved = which(command)
        if resolved:
            return resolved
    return None


def _supported_if(condition: bool) -> CapabilityState:
    return CapabilityState.SUPPORTED if condition else CapabilityState.UNKNOWN


def _extract_version(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _provider_config(provider: str) -> dict[str, object]:
    data = load_config().get("providers", {}).get(provider, {})
    return data if isinstance(data, dict) else {}


PROVIDERS = provider_definitions()
