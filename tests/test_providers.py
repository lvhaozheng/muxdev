from __future__ import annotations

from muxdev.providers import CapabilityState, CommandResult, ProviderStatus, detect_providers, probe_provider


CODEX_ROOT_HELP = """
Codex CLI

If no subcommand is specified, options will be forwarded to the interactive CLI.

Commands:
  exec            Run Codex non-interactively
  plugin          Manage Codex plugins
  resume          Resume a previous interactive session

Options:
      --ask-for-approval <APPROVAL_POLICY>
      --no-alt-screen
"""


CODEX_EXEC_HELP = """
Run Codex non-interactively

Options:
      --json
      --ask-for-approval <APPROVAL_POLICY>
"""


CODEX_DOCTOR_HELP = """
Diagnose local Codex installation

Options:
      --json
"""


CODEX_PLUGIN_HELP = """
Manage Codex plugins
"""


def fake_which(command: str) -> str | None:
    return {"codex": "C:/bin/codex.cmd"}.get(command)


def fake_runner(command: str, args) -> CommandResult:
    args = tuple(args)
    if args == ("--version",):
        return CommandResult((command, *args), 0, "codex-cli 0.135.0\n", "")
    if args == ("--help",):
        return CommandResult((command, *args), 0, CODEX_ROOT_HELP, "")
    if args == ("exec", "--help"):
        return CommandResult((command, *args), 0, CODEX_EXEC_HELP, "")
    if args == ("doctor", "--help"):
        return CommandResult((command, *args), 0, CODEX_DOCTOR_HELP, "")
    if args == ("plugin", "--help"):
        return CommandResult((command, *args), 0, CODEX_PLUGIN_HELP, "")
    return CommandResult((command, *args), 1, "", "unexpected command")


def test_missing_providers_are_unavailable() -> None:
    probes = detect_providers(which=lambda command: None, runner=fake_runner)
    by_name = {probe.provider: probe for probe in probes}

    assert by_name["mock"].status == ProviderStatus.READY
    assert by_name["codex"].status == ProviderStatus.UNAVAILABLE
    assert by_name["codex"].headless == CapabilityState.NOT_INSTALLED
    assert by_name["qwen"].status == ProviderStatus.UNAVAILABLE


def test_codex_help_maps_to_static_capabilities() -> None:
    probe = probe_provider("codex", which=fake_which, runner=fake_runner)

    assert probe.installed is True
    assert probe.version == "codex-cli 0.135.0"
    assert probe.headless == CapabilityState.SUPPORTED
    assert probe.pty == CapabilityState.SUPPORTED
    assert probe.json == CapabilityState.SUPPORTED
    assert probe.approval == CapabilityState.SUPPORTED
    assert probe.skill == CapabilityState.SUPPORTED
    assert probe.attach == CapabilityState.SUPPORTED
    assert probe.status == ProviderStatus.READY


def test_detect_includes_all_m0_providers() -> None:
    probes = detect_providers(which=fake_which, runner=fake_runner)
    names = [probe.provider for probe in probes]

    assert names == ["mock", "codex", "claude-code", "qwen", "kimi", "trae", "antigravity"]
