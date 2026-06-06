"""Provider installer plans and optional execution.

Install commands are dry-run by default. When execution is requested, this
module resolves the package manager executable first so missing tools produce a
stable muxdev error instead of leaking platform-specific OSError details.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Callable, Iterable

from ..core.platforms import hidden_subprocess_kwargs, script_invocation
from .accounts import AccountInfo, get_account_info
from .loader import load_config
from ..providers.registry import CommandResult, get_provider_definition


INSTALL_TIMEOUT_SECONDS = 180


class InstallStatus(StrEnum):
    PLANNED = "planned"
    INSTALLED = "installed"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class InstallPlan:
    """Configured install command plus verification and account guidance."""

    provider: str
    supported: bool
    manager: str
    command: tuple[str, ...]
    verify_command: tuple[str, ...]
    docs_url: str
    account: AccountInfo
    notes: str

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["command"] = list(self.command)
        data["verify_command"] = list(self.verify_command)
        data["account"] = self.account.to_dict()
        return data


@dataclass(frozen=True)
class InstallResult:
    """Result object shared by human output and `--json` automation."""

    provider: str
    executed: bool
    status: InstallStatus
    plan: InstallPlan
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["status"] = str(self.status)
        data["plan"] = self.plan.to_dict()
        return data


Runner = Callable[[str, Iterable[str]], CommandResult]
Which = Callable[[str], str | None]


INSTALL_PLANS: dict[str, InstallPlan] = {}


def get_install_plan(provider: str, manager: str = "auto") -> InstallPlan:
    """Build an install plan for a provider from merged configuration."""
    definition = get_provider_definition(provider)
    data = load_config().get("installers", {}).get(definition.provider, {})
    if not isinstance(data, dict):
        data = {}
    plan = InstallPlan(
        provider=definition.provider,
        supported=bool(data.get("supported", False)),
        manager=str(data.get("manager", "manual")),
        command=tuple(str(item) for item in data.get("command", [])),
        verify_command=tuple(str(item) for item in data.get("verify_command", [])),
        docs_url=str(data.get("docs_url", "")),
        account=get_account_info(definition.provider),
        notes=str(data.get("notes", "")),
    )
    if manager not in {"auto", plan.manager}:
        raise ValueError(f"{definition.provider} only supports manager '{plan.manager}' in M0")
    return plan


def install_provider(
    provider: str,
    *,
    execute: bool = False,
    manager: str = "auto",
    which: Which = shutil.which,
    runner: Runner | None = None,
) -> InstallResult:
    """Return a dry-run plan or execute the configured installer command."""
    plan = get_install_plan(provider, manager)
    if not plan.supported:
        return InstallResult(
            provider=plan.provider,
            executed=False,
            status=InstallStatus.UNSUPPORTED,
            plan=plan,
            error=plan.notes,
        )

    if not execute:
        return InstallResult(
            provider=plan.provider,
            executed=False,
            status=InstallStatus.PLANNED,
            plan=plan,
        )

    executable = plan.command[0]
    resolved_executable = which(executable)
    if resolved_executable is None:
        return InstallResult(
            provider=plan.provider,
            executed=True,
            status=InstallStatus.FAILED,
            plan=plan,
            error=f"installer command not found: {executable}",
        )

    command_runner = runner or default_install_runner
    result = command_runner(resolved_executable, plan.command[1:])
    status = InstallStatus.INSTALLED if result.returncode == 0 else InstallStatus.FAILED
    return InstallResult(
        provider=plan.provider,
        executed=True,
        status=status,
        plan=plan,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        error=result.error,
    )


def default_install_runner(command: str, args: Iterable[str]) -> CommandResult:
    """Run one bounded installer subprocess."""
    invocation = _build_install_invocation(command, tuple(args))
    try:
        completed = subprocess.run(
            invocation,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=INSTALL_TIMEOUT_SECONDS,
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
            error="install command timed out",
        )
    except OSError as exc:
        return CommandResult(
            args=tuple(invocation),
            returncode=127,
            stdout="",
            stderr="",
            error=str(exc),
        )


def _build_install_invocation(command: str, args: tuple[str, ...]) -> list[str]:
    return script_invocation(command, args)
