from __future__ import annotations

import json
import os
import shutil
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from typer.testing import CliRunner

from muxdev.cli import app
from muxdev.runtime import SupervisorRuntime


runner = CliRunner()


class FakeDaemonClient:
    submitted: list[dict[str, object]] = []

    def submit_task(self, payload: dict[str, object]) -> dict[str, object]:
        self.submitted.append(payload)
        return {"task_id": "run_fake", "status": "created", "dashboard": "http://127.0.0.1:8787/tasks/run_fake"}

    def continue_task(self, task_id: str, **_: object) -> dict[str, object]:
        return {"task_id": task_id, "status": "running"}

    def tasks(self) -> list[dict[str, object]]:
        return [{"task_id": "run_fake", "task": "fake task", "status": "running", "current_stage": "implement"}]

    def task(self, task_id: str) -> dict[str, object]:
        return {
            "run": {"run_id": task_id, "status": "running", "task": "fake task"},
            "agents": [],
            "approvals": [],
            "provider_actions": [],
            "events": [],
        }

    def approvals(self, status: str = "pending") -> list[dict[str, object]]:
        return []

    def provider_actions(self, *, status: str | None = None, task_id: str | None = None) -> list[dict[str, object]]:
        return [{"action_id": "pact_fake", "run_id": task_id or "run_fake", "status": status or "pending", "prompt_text": "Apply? [y/N]"}]

    def provider_action_handled(self, action_id: str) -> dict[str, object]:
        return {"action_id": action_id, "status": "handled", "run_id": "run_fake"}

    def provider_action_dismiss(self, action_id: str) -> dict[str, object]:
        return {"action_id": action_id, "status": "dismissed", "run_id": "run_fake"}

    def approve(self, approval_id: str) -> dict[str, object]:
        return {"approval_id": approval_id, "status": "approved", "run_id": "run_fake"}

    def deny(self, approval_id: str) -> dict[str, object]:
        return {"approval_id": approval_id, "status": "denied", "run_id": "run_fake"}

    def attach_command(self, task_id: str, *, agent: str) -> dict[str, object]:
        return {"task_id": task_id, "agent": agent, "status": "attached"}


def test_provider_detect_json_contains_all_providers() -> None:
    result = runner.invoke(app, ["provider", "detect", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert [item["provider"] for item in payload] == [
        "mock",
        "codex",
        "claude-code",
        "qwen",
        "kimi",
        "trae",
        "antigravity",
    ]


def test_provider_detect_human_table_has_m0_columns() -> None:
    result = runner.invoke(app, ["provider", "detect"])

    assert result.exit_code == 0
    for column in ("provider", "mode", "command", "installed", "headless", "approval", "skill", "status"):
        assert column in result.stdout


def test_provider_install_dry_run_outputs_plan() -> None:
    result = runner.invoke(app, ["provider", "install", "codex", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "planned"
    assert payload["executed"] is False
    assert payload["plan"]["command"] == ["npm", "install", "-g", "@openai/codex"]


def test_provider_install_rejects_unsupported_provider() -> None:
    result = runner.invoke(app, ["provider", "install", "mock", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "unsupported"
    assert payload["plan"]["supported"] is False


def test_provider_account_json_outputs_signup_and_login() -> None:
    result = runner.invoke(app, ["provider", "account", "codex", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["required"] is True
    assert payload["signup_url"] == "https://chatgpt.com/"
    assert payload["login_command"] == "codex login"


def test_removed_top_level_aliases_are_unknown_commands() -> None:
    for command in ("run", "resume", "web", "account", "install", "doctor"):
        result = runner.invoke(app, [command])
        assert result.exit_code != 0, command


def test_dev_submits_daemon_task_with_resolved_main_path_options(monkeypatch) -> None:
    fake = FakeDaemonClient()
    monkeypatch.setattr("muxdev.cli.main._daemon_client", lambda *_args, **_kwargs: fake)

    result = runner.invoke(
        app,
        [
            "dev",
            "ship daemon task",
            "--provider",
            "mock",
            "--profile",
            "solo",
            "--gate",
            "strict",
            "--role",
            "code=mock",
            "--skill",
            "pytest",
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["task_id"] == "run_fake"
    submitted = fake.submitted[0]
    assert submitted["task"] == "ship daemon task"
    assert submitted["workflow"] == "dev"
    assert submitted["profile"] == "solo"
    assert submitted["gate"] == "strict"
    assert submitted["role_providers"]["code"] == "mock"
    assert "skills" in submitted


def test_continue_dashboard_and_tui_json_use_daemon_client(monkeypatch) -> None:
    fake = FakeDaemonClient()
    monkeypatch.setattr("muxdev.cli.main._daemon_client", lambda *_args, **_kwargs: fake)

    continued = runner.invoke(app, ["continue", "run_fake", "--json"])
    dashboard = runner.invoke(app, ["dashboard", "--json"])
    tui = runner.invoke(app, ["tui", "run_fake", "--json"])
    actions = runner.invoke(app, ["actions", "--json"])
    handled = runner.invoke(app, ["action", "handled", "pact_fake", "--json"])

    assert continued.exit_code == 0
    assert json.loads(continued.stdout)["status"] == "running"
    assert dashboard.exit_code == 0
    assert json.loads(dashboard.stdout)["dashboard"] == "http://127.0.0.1:8787"
    assert tui.exit_code == 0
    assert json.loads(tui.stdout)["run"]["run_id"] == "run_fake"
    assert actions.exit_code == 0
    assert json.loads(actions.stdout)[0]["action_id"] == "pact_fake"
    assert handled.exit_code == 0
    assert json.loads(handled.stdout)["status"] == "handled"


def test_no_args_enters_daemon_tui(monkeypatch) -> None:
    monkeypatch.setattr("muxdev.cli.main._start_daemon_tui", lambda *_args, **_kwargs: print("entered daemon tui"))

    result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert "entered daemon tui" in result.stdout


def test_new_creates_muxdev_project_scaffold() -> None:
    temp_dir = _workspace_temp()
    target = temp_dir / "project"
    try:
        result = runner.invoke(app, ["new", str(target), "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["status"] == "created"
        assert (target / ".muxdev" / "workflows" / "software-dev.yaml").exists()
        assert "muxdev dev" in (target / "README.md").read_text(encoding="utf-8")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_trace_skip_merge_metrics_and_search_commands() -> None:
    temp_dir = _workspace_temp()
    try:
        with _chdir(temp_dir):
            Path("sample.py").write_text("print('muxdev search target')\n", encoding="utf-8")
            run_id = _create_mock_run("observability smoke")

            trace = runner.invoke(app, ["trace", "view", run_id, "--json"])
            skipped = runner.invoke(app, ["skip", run_id, "--stage", "review", "--json"])
            merged = runner.invoke(app, ["merge", run_id, "--gate-command", "python --version", "--json"])
            chrome = runner.invoke(app, ["trace", "chrome", run_id, "--json"])
            metrics = runner.invoke(app, ["metrics", run_id, "--json"])
            prometheus = runner.invoke(app, ["metrics", run_id, "--prometheus"])
            search = runner.invoke(app, ["search", "search target", "--json"])
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    assert trace.exit_code == 0
    assert json.loads(trace.stdout)["events"]
    assert skipped.exit_code == 0
    assert json.loads(skipped.stdout)["status"] == "skipped"
    assert merged.exit_code == 0
    assert json.loads(merged.stdout)["status"] == "dry_run"
    assert chrome.exit_code == 0
    assert json.loads(chrome.stdout)["events"] > 0
    assert metrics.exit_code == 0
    assert json.loads(metrics.stdout)["completed_stages"] >= 1
    assert prometheus.exit_code == 0
    assert "muxdev_run_tokens" in prometheus.stdout
    assert search.exit_code == 0
    assert json.loads(search.stdout)[0]["path"] == "sample.py"


def test_detach_updates_local_agent_session() -> None:
    temp_dir = _workspace_temp()
    try:
        with _chdir(temp_dir):
            run_id = _create_mock_run("detach smoke")
            detached = runner.invoke(app, ["detach", run_id, "--agent", "implementer", "--json"])
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    assert detached.exit_code == 0
    assert json.loads(detached.stdout)["status"] == "detached"


def test_skill_install_list_and_inject_json() -> None:
    temp_dir = _workspace_temp()
    try:
        with _chdir(temp_dir):
            install = runner.invoke(app, ["skill", "install", "demo", "--native", "--json"])
            listing = runner.invoke(app, ["skill", "list", "--json"])
            inject = runner.invoke(app, ["skill", "inject", "demo", "--json"])
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    assert install.exit_code == 0
    assert json.loads(install.stdout)["native"] is True
    assert listing.exit_code == 0
    assert json.loads(listing.stdout)[0]["name"] == "demo"
    assert inject.exit_code == 0
    assert "# demo" in json.loads(inject.stdout)["content"]


def test_session_start_list_stop_and_rag_query() -> None:
    temp_dir = _workspace_temp()
    try:
        with _chdir(temp_dir):
            Path("notes.md").write_text("alpha beta muxdev retrieval target\n", encoding="utf-8")
            start = runner.invoke(
                app,
                ["session", "start", "mock", "--command", "python -c \"print('session transcript')\"", "--json"],
            )
            assert start.exit_code == 0
            session_id = json.loads(start.stdout)["session_id"]
            listed = runner.invoke(app, ["session", "list", "--json"])
            stopped = runner.invoke(app, ["session", "stop", session_id, "--json"])
            indexed = runner.invoke(app, ["rag", "index", "--json"])
            queried = runner.invoke(app, ["rag", "query", "retrieval target", "--json"])
            index_payload = json.loads(Path(json.loads(indexed.stdout)["path"]).read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    assert listed.exit_code == 0
    assert json.loads(listed.stdout)[0]["session_id"] == session_id
    assert stopped.exit_code == 0
    assert json.loads(stopped.stdout)["status"] == "stopped"
    assert indexed.exit_code == 0
    assert index_payload["chunks"][0]["embedding"]
    assert queried.exit_code == 0
    assert json.loads(queried.stdout)[0]["path"] == "notes.md"


def test_graph_mcp_workflow_and_flow_smoke() -> None:
    graph = runner.invoke(app, ["graph", "export", "--json"])
    manifest = runner.invoke(app, ["mcp", "manifest", "--json"])
    plugin = runner.invoke(app, ["workflow", "plugin", "spec-lite", "--json"])
    command = runner.invoke(
        app,
        ["workflow", "render", "spec-lite", "--phase", "planning", "--provider", "codex", "--task", "ship it", "--json"],
    )
    temp_dir = _workspace_temp()
    try:
        with _chdir(temp_dir):
            flow = runner.invoke(
                app,
                ["flow", "add", "daily-review", "--task", "review open changes", "--schedule", "0 9 * * *", "--provider", "mock", "--json"],
            )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    assert graph.exit_code == 0
    assert json.loads(graph.stdout)["name"] == "software-dev"
    assert manifest.exit_code == 0
    assert "provider.detect" in {tool["name"] for tool in json.loads(manifest.stdout)["tools"]}
    assert plugin.exit_code == 0
    assert json.loads(plugin.stdout)["artifacts"]["planning"] == ".muxdev/spec/plan.md"
    assert command.exit_code == 0
    assert json.loads(command.stdout)["canonical"] == "/spec-lite:plan ship it"
    assert flow.exit_code == 0
    assert json.loads(flow.stdout)["schedule"] == "0 9 * * *"


def _create_mock_run(task: str) -> str:
    return SupervisorRuntime(Path.cwd()).run(task, provider="mock").run_id


def _workspace_temp() -> Path:
    path = Path(".test_workspaces") / f"cli_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextmanager
def _chdir(path: Path) -> Iterator[None]:
    cwd = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(cwd)
