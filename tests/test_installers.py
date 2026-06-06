from __future__ import annotations

from muxdev.config.installers import InstallStatus, _build_install_invocation, get_install_plan, install_provider
from muxdev.providers import CommandResult


def test_get_install_plan_for_supported_provider() -> None:
    plan = get_install_plan("qwen")

    assert plan.supported is True
    assert plan.command == ("npm", "install", "-g", "@qwen-code/qwen-code@latest")
    assert plan.verify_command == ("qwen", "--version")
    assert plan.account.signup_url == "https://modelstudio.console.alibabacloud.com/"


def test_install_provider_is_dry_run_by_default() -> None:
    result = install_provider("kimi")

    assert result.status == InstallStatus.PLANNED
    assert result.executed is False
    assert result.plan.command == ("npm", "install", "-g", "@moonshot-ai/kimi-code@latest")
    assert result.plan.account.login_command == "kimi login"


def test_execute_install_uses_runner_when_installer_exists() -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_runner(command: str, args) -> CommandResult:
        args = tuple(args)
        calls.append((command, args))
        return CommandResult((command, *args), 0, "ok", "")

    result = install_provider(
        "claude-code",
        execute=True,
        which=lambda command: "C:/bin/npm.cmd" if command == "npm" else None,
        runner=fake_runner,
    )

    assert result.status == InstallStatus.INSTALLED
    assert calls == [("C:/bin/npm.cmd", ("install", "-g", "@anthropic-ai/claude-code"))]


def test_execute_install_fails_when_installer_missing() -> None:
    result = install_provider("codex", execute=True, which=lambda command: None)

    assert result.status == InstallStatus.FAILED
    assert result.error == "installer command not found: npm"


def test_windows_cmd_installer_uses_cmd_wrapper() -> None:
    invocation = _build_install_invocation("C:/Program Files/nodejs/npm.CMD", ("install", "-g", "pkg"))

    assert invocation == ["cmd", "/c", "C:\\Program Files\\nodejs\\npm.CMD", "install", "-g", "pkg"]
