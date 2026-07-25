from __future__ import annotations

import pytest
import typer

from muxdev.cli import main as cli
from muxdev.workbench import WorkbenchRegistry


def test_non_loopback_serve_requires_explicit_remote_mode(workspace) -> None:
    with pytest.raises(typer.BadParameter, match="--allow-remote"):
        cli.serve(workspace, host="0.0.0.0", port=8765, allow_remote=False, trusted_origin=None)


def test_remote_serve_enables_auth_and_trusted_origins(workspace, monkeypatch) -> None:
    captured = {}
    monkeypatch.setenv("MUXDEV_HOME", str(workspace / ".workbench-user"))

    def fake_run(app, *, host, port):
        captured.update(app=app, host=host, port=port)

    monkeypatch.setattr("uvicorn.run", fake_run)
    cli.serve(
        workspace,
        host="0.0.0.0",
        port=9999,
        allow_remote=True,
        trusted_origin=["https://muxdev.example"],
    )

    assert captured["host"] == "0.0.0.0" and captured["port"] == 9999
    assert captured["app"].state.require_auth is True
    assert captured["app"].state.pairing_code
    assert captured["app"].state.trusted_origins == ("https://muxdev.example",)


def test_serve_registers_two_directories_against_one_running_daemon(
    workspace, monkeypatch, capsys
) -> None:
    project_a = workspace / "project-a"
    project_b = workspace / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    monkeypatch.setenv("MUXDEV_HOME", str(workspace / ".workbench-user"))
    state = {
        "instance_id": "daemon-existing",
        "pid": 1234,
        "host": "127.0.0.1",
        "port": 9988,
        "started_at": "2026-07-25T00:00:00+00:00",
    }
    monkeypatch.setattr(cli, "_daemon_state", lambda: state)
    monkeypatch.setattr(
        cli,
        "_daemon_health",
        lambda value: {
            "service": "muxdev-workbench",
            "instance_id": value.get("instance_id") or "daemon-existing",
        },
    )
    monkeypatch.setattr(
        "uvicorn.run",
        lambda *_args, **_kwargs: pytest.fail("running daemon must be reused"),
    )

    cli.serve(project_a, port=8765)
    cli.serve(project_b, port=7766)

    registry = WorkbenchRegistry.user()
    try:
        projects = registry.store.list_projects()
        assert {project["path"] for project in projects} == {
            str(project_a.resolve()),
            str(project_b.resolve()),
        }
    finally:
        registry.store.close()
    output = capsys.readouterr().out
    assert output.count("http://127.0.0.1:9988/projects/") == 2


def test_serve_rejects_a_non_muxdev_process_on_the_requested_port(
    workspace, monkeypatch
) -> None:
    monkeypatch.setenv("MUXDEV_HOME", str(workspace / ".workbench-user"))
    monkeypatch.setattr(cli, "_daemon_state", lambda: None)
    monkeypatch.setattr(cli, "_daemon_health", lambda _state: None)
    monkeypatch.setattr(cli, "_port_is_open", lambda _host, _port: True)

    with pytest.raises(typer.BadParameter, match="non-Muxdev process"):
        cli.serve(workspace, host="127.0.0.1", port=9876)


def test_serve_clears_stale_daemon_state_before_starting(
    workspace, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("MUXDEV_HOME", str(workspace / ".workbench-user"))
    stale = {
        "instance_id": "daemon-stale",
        "pid": 987654,
        "host": "127.0.0.1",
        "port": 8765,
    }
    cleared: list[str | None] = []
    started: dict[str, object] = {}
    monkeypatch.setattr(cli, "_daemon_state", lambda: stale)
    monkeypatch.setattr(cli, "_daemon_health", lambda _state: None)
    monkeypatch.setattr(cli, "_pid_is_alive", lambda _pid: False)
    monkeypatch.setattr(cli, "_port_is_open", lambda _host, _port: False)
    monkeypatch.setattr(cli, "_clear_daemon_state", cleared.append)
    monkeypatch.setattr(cli, "_write_daemon_state", lambda value: started.update(value))
    monkeypatch.setattr("uvicorn.run", lambda *_args, **_kwargs: None)

    cli.serve(workspace, host="127.0.0.1", port=7654)

    assert cleared[0] == "daemon-stale"
    assert started["port"] == 7654
    assert "http://127.0.0.1:7654/projects/" in capsys.readouterr().out
