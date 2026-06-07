from __future__ import annotations

import shutil
import sys
import types
import uuid
import importlib
from io import StringIO
from pathlib import Path

from rich.console import Console

from muxdev.ui.repl import handle_repl_command
from muxdev.ui.tui import (
    daemon_chat_view,
    daemon_help_text,
    daemon_provider_actions_text,
    daemon_task_detail_text,
    daemon_tasks_text,
    _handle_tui_command,
    _normalize_command,
    _render_tui,
)


def test_repl_help_is_non_interactive() -> None:
    workspace = _workspace_temp()
    try:
        running, message = handle_repl_command("/help", workspace)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert running is True
    assert "/status" in message


def test_repl_exit_stops_loop() -> None:
    workspace = _workspace_temp()
    try:
        running, message = handle_repl_command("/exit", workspace)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert running is False
    assert message == "bye"


def test_tui_slash_commands_normalize() -> None:
    assert _normalize_command("/refresh") == "r"
    assert _normalize_command("/quit") == "q"
    assert _normalize_command("/run add tests") == "run add tests"
    assert _normalize_command("/dev add tests") == "run add tests"
    assert _normalize_command("/design plan it") == "workflow design plan it"
    assert _normalize_command("/approve appr_1") == "approve appr_1"
    assert _normalize_command("/action handled pact_1") == "action handled pact_1"


def test_tui_help_lists_slash_commands() -> None:
    workspace = _workspace_temp()
    try:
        message = _handle_tui_command("help", workspace, "latest")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert "/run" in message
    assert "/approve" in message


def test_tui_render_only_clears_when_requested() -> None:
    workspace = _workspace_temp()
    console = _FakeConsole()
    try:
        _render_tui(console, workspace, "latest")
        assert console.clears == 0

        _render_tui(console, workspace, "latest", clear_screen=True)
        assert console.clears == 1
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_daemon_tui_only_clears_on_initial_render(monkeypatch) -> None:
    cli_app_module = importlib.import_module("muxdev.cli.tui")
    clear_flags: list[bool] = []
    prompts: list[str] = []

    class FakePromptSession:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.lines = iter(["", "/help", "/quit"])

        def prompt(self, *args: object, **kwargs: object) -> str:
            prompts.append(str(args[0]))
            return next(self.lines)

    def fake_render(*args: object, **kwargs: object) -> None:
        clear_flags.append(bool(kwargs.get("clear_screen")))

    fake_prompt_toolkit = types.ModuleType("prompt_toolkit")
    fake_prompt_toolkit.PromptSession = FakePromptSession
    fake_completion = types.ModuleType("prompt_toolkit.completion")
    fake_completion.WordCompleter = lambda *args, **kwargs: object()
    monkeypatch.setitem(sys.modules, "prompt_toolkit", fake_prompt_toolkit)
    monkeypatch.setitem(sys.modules, "prompt_toolkit.completion", fake_completion)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli_app_module, "_render_daemon_tui_frame", fake_render)

    cli_app_module._start_daemon_tui()

    assert clear_flags == [True, False, False]
    assert prompts == ["muxdev › ", "muxdev › ", "muxdev › "]


def test_daemon_tui_command_results_use_chat_format(monkeypatch) -> None:
    cli_app_module = importlib.import_module("muxdev.cli.tui")

    class FakeClient:
        submitted: list[dict[str, object]] = []

        def submit_task(self, payload: dict[str, object]) -> dict[str, object]:
            self.submitted.append(payload)
            return {"task_id": "run_submit", "run_id": "run_submit", "status": "created"}

        def tasks(self) -> list[dict[str, object]]:
            return [{"task_id": "run_1", "status": "running", "current_stage": "code", "pending_approvals": 0, "tokens": 100, "task": "chat tui"}]

        def task(self, task_id: str) -> dict[str, object]:
            return _daemon_payload("running")

        def report(self, task_id: str) -> dict[str, object]:
            return {"task_id": "run_1", "content": "\n".join(f"line {index}" for index in range(30))}

        def diff(self, task_id: str) -> dict[str, object]:
            return {"task_id": "run_1", "diff": "diff --git a/a b/a\n+hello"}

        def provider_actions(self, *, status: str | None = None) -> list[dict[str, object]]:
            return [_provider_action()]

        def provider_action_handled(self, action_id: str) -> dict[str, object]:
            return {"action_id": action_id, "run_id": "run_1", "status": "handled"}

        def provider_action_dismiss(self, action_id: str) -> dict[str, object]:
            return {"action_id": action_id, "run_id": "run_1", "status": "dismissed"}

    monkeypatch.setattr(cli_app_module, "_daemon_client", lambda host, port: FakeClient())

    tasks, _ = cli_app_module._handle_daemon_tui_command("/tasks", "latest", host="127.0.0.1", port=8788, commands={})
    dev, dev_selected = cli_app_module._handle_daemon_tui_command("/dev --provider mock --simple chat tui", "latest", host="127.0.0.1", port=8788, commands={})
    design, design_selected = cli_app_module._handle_daemon_tui_command("/design --provider mock design tui", "latest", host="127.0.0.1", port=8788, commands={})
    status, selected = cli_app_module._handle_daemon_tui_command("/status run_1", "latest", host="127.0.0.1", port=8788, commands={})
    report, report_selected = cli_app_module._handle_daemon_tui_command("/report run_1", "latest", host="127.0.0.1", port=8788, commands={})
    actions, _ = cli_app_module._handle_daemon_tui_command("/actions", "latest", host="127.0.0.1", port=8788, commands={})
    handled, handled_selected = cli_app_module._handle_daemon_tui_command("/action handled pact_1", "latest", host="127.0.0.1", port=8788, commands={})

    assert "Recent tasks" in tasks
    assert "run_1" in tasks
    assert "workflow=dev" in dev
    assert "depth=simple" in dev
    assert dev_selected == "run_submit"
    assert "workflow=design" in design
    assert design_selected == "run_submit"
    assert "Recent events" in status
    assert selected == "run_running"
    assert "Full report: muxdev report run_1" in report
    assert "line 29" not in report
    assert report_selected == "run_1"
    assert "Pending provider actions" in actions
    assert "Apply this change" in actions
    assert handled == "pact_1: handled"
    assert handled_selected == "run_1"


def test_daemon_tui_tolerates_old_daemon_without_provider_actions(monkeypatch) -> None:
    cli_app_module = importlib.import_module("muxdev.cli.tui")
    from muxdev.clients.daemon import DaemonConnectionError

    class OldDaemonClient:
        def health(self) -> dict[str, object]:
            return {"status": "ok", "tasks": 1, "running_tasks": 0, "queue_length": 0}

        def tasks(self) -> list[dict[str, object]]:
            return [{"task_id": "run_1", "status": "running", "current_stage": "code", "pending_approvals": 0, "tokens": 10, "task": "old daemon"}]

        def approvals(self, *, status: str | None = None) -> list[dict[str, object]]:
            return []

        def task(self, task_id: str) -> dict[str, object]:
            return _daemon_payload("running")

        def provider_actions(self, *, status: str | None = None) -> list[dict[str, object]]:
            raise DaemonConnectionError("404", status_code=404, path="/api/provider-actions?status=pending")

    monkeypatch.setattr(cli_app_module, "_daemon_client", lambda host, port: OldDaemonClient())

    snapshot = cli_app_module._daemon_tui_snapshot("latest", host="127.0.0.1", port=8788)
    message, _ = cli_app_module._handle_daemon_tui_command("/actions", "latest", host="127.0.0.1", port=8788, commands={})

    assert snapshot["provider_actions"] == []
    assert "Provider Actions API is not available" in snapshot["daemon"]["warnings"][0]
    assert "muxdev serve --restart" in message


def test_daemon_chat_view_renders_intro_and_empty_state(monkeypatch) -> None:
    import muxdev.ui.tui as tui_module

    workspace = _workspace_temp()
    monkeypatch.setattr(tui_module, "detect_providers", lambda: (_ for _ in ()).throw(AssertionError("daemon TUI render must not probe providers")))
    try:
        text = _render_to_text(
            daemon_chat_view(
                workspace=workspace,
                version="0.1.0",
                host="127.0.0.1",
                api_port=8788,
                ui_port=8787,
                daemon={"tasks": 0, "running_tasks": 0, "queue_length": 0},
                tasks=[],
                approvals=[],
            )
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    assert "muxdev" in text
    assert "local AI coding control plane" in text
    assert "task lifecycle | approvals | reports | diffs | skills" in text
    assert "http://127.0.0.1:8788" in text
    assert "http://127.0.0.1:8787" in text
    assert "Start with /dev <task> or /design <task>" in text
    assert "/help" in text


def test_daemon_task_summary_handles_core_statuses() -> None:
    for status in ["running", "awaiting_approval", "awaiting_provider_action", "completed"]:
        text = daemon_task_detail_text(_daemon_payload(status))
        assert f": {status}" in text
        assert "profile=squad" in text
        assert "gate=safe" in text
        assert "Recent events" in text
        if status == "awaiting_provider_action":
            assert "Provider actions" in text
            assert "muxdev attach" in text


def test_daemon_help_and_tasks_are_grouped_and_compact() -> None:
    help_text = daemon_help_text()
    tasks_text = daemon_tasks_text(
        [
            {
                "task_id": "run_1",
                "status": "running",
                "current_stage": "code",
                "pending_approvals": 1,
                "tokens": 1200,
                "task": "x" * 200,
            }
        ]
    )

    assert "Work:" in help_text
    assert "/dev <task>" in help_text
    assert "/design <task>" in help_text
    assert "/run <task>" in help_text
    assert "Review:" in help_text
    assert "/approve <id>" in help_text
    assert "Provider Actions:" in help_text
    assert "/action handled <id>" in help_text
    assert "Output:" in help_text
    assert "/dashboard" in help_text
    assert "run_1" in tasks_text
    assert len(tasks_text.splitlines()[1]) < 150


def test_daemon_provider_actions_text_shows_prompt_and_attach() -> None:
    text = daemon_provider_actions_text([_provider_action()])

    assert "pact_1" in text
    assert "Apply this change" in text
    assert "Yes, No" in text
    assert "muxdev attach run_1 --agent designer" in text


class _FakeConsole:
    def __init__(self) -> None:
        self.clears = 0
        self.prints = 0

    def clear(self) -> None:
        self.clears += 1

    def print(self, *args: object, **kwargs: object) -> None:
        self.prints += 1


def _workspace_temp() -> Path:
    path = Path(".test_workspaces") / f"repl_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _render_to_text(renderable: object) -> str:
    output = StringIO()
    console = Console(file=output, width=120, force_terminal=False, color_system=None)
    console.print(renderable)
    return output.getvalue()


def _daemon_payload(status: str) -> dict[str, object]:
    provider_actions = [_provider_action()] if status == "awaiting_provider_action" else []
    return {
        "run": {"run_id": f"run_{status}", "status": status, "task": "ship polished tui", "workflow": "dev", "provider": "mock"},
        "stages": [{"stage_id": "code", "role": "code", "status": "running" if status == "running" else "completed", "summary": "working"}],
        "summary": {
            "pending_approvals": 1 if status == "awaiting_approval" else 0,
            "pending_provider_actions": len(provider_actions),
            "tokens": 100,
            "cost_usd": 0.01,
        },
        "context": {"profile": "squad", "gate": "safe", "skills": [{"name": "demo"}]},
        "trace": [{"type": "stage_started", "stage": "code", "data": {"role": "code"}}],
        "provider_actions": provider_actions,
    }


def _provider_action() -> dict[str, object]:
    return {
        "action_id": "pact_1",
        "run_id": "run_1",
        "stage_id": "design",
        "provider": "codex",
        "kind": "cli_confirmation",
        "status": "pending",
        "prompt_text": "Apply this change? [y/N]",
        "options": [{"label": "Yes", "value": "y"}, {"label": "No", "value": "n"}],
        "attach_command": "muxdev attach run_1 --agent designer",
    }
