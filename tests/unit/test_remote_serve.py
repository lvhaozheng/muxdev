from __future__ import annotations

import pytest
import typer

from muxdev.cli.main import serve


def test_non_loopback_serve_requires_explicit_remote_mode(workspace) -> None:
    with pytest.raises(typer.BadParameter, match="--allow-remote"):
        serve(workspace, host="0.0.0.0", port=8765, allow_remote=False, trusted_origin=None)


def test_remote_serve_enables_auth_and_trusted_origins(workspace, monkeypatch) -> None:
    captured = {}

    def fake_run(app, *, host, port):
        captured.update(app=app, host=host, port=port)

    monkeypatch.setattr("uvicorn.run", fake_run)
    serve(
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

