from __future__ import annotations

from fastapi.testclient import TestClient

from muxdev.api import create_app, write_dashboard


def test_legacy_dashboard_is_removed_with_unscoped_v2_calls(workspace) -> None:
    response = TestClient(create_app(workspace)).get("/legacy")

    assert response.status_code == 404


def test_conversation_spa_is_default_and_uses_external_assets(workspace) -> None:
    response = TestClient(create_app(workspace)).get("/")

    assert response.status_code == 200
    assert '<div id="root"></div>' in response.text
    assert "/assets/app/assets/" in response.text
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert "<script type=\"module\"" in response.text
    assert "<script>" not in response.text


def test_static_dashboard_export_uses_the_same_built_spa(workspace) -> None:
    output = workspace / "dashboard.html"

    write_dashboard(workspace, output)

    rendered = output.read_text(encoding="utf-8")
    assert '<div id="root"></div>' in rendered
    assert "/assets/app/assets/" in rendered
    assert "/api/v2/conversations" not in rendered
