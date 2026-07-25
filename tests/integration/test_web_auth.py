from __future__ import annotations

from fastapi.testclient import TestClient

from muxdev.api import create_app


def test_remote_web_requires_pairing_and_uses_device_session(workspace) -> None:
    app = create_app(
        workspace,
        require_auth=True,
        pairing_code="pair-once-123",
        trusted_origins=("https://muxdev.example",),
    )
    client = TestClient(app, base_url="https://muxdev.example")

    assert client.get("/").status_code == 200
    assert client.get("/api/v1/conversations").status_code == 401
    assert client.post("/api/v1/auth/pair", json={
        "pairing_code": "wrong-code",
        "device_label": "phone",
    }).status_code == 401

    paired = client.post("/api/v1/auth/pair", json={
        "pairing_code": "pair-once-123",
        "device_label": "phone",
    })
    assert paired.status_code == 200
    cookie = paired.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie
    assert client.get("/api/v1/conversations").status_code == 200

    rejected = client.post("/api/v1/conversations", json={
        "goal": "must have a trusted origin",
        "auto_start": False,
    })
    assert rejected.status_code == 403
    accepted = client.post(
        "/api/v1/conversations",
        headers={"origin": "https://muxdev.example"},
        json={"goal": "trusted remote request", "profile": "lite", "auto_start": False},
    )
    assert accepted.status_code == 201

    reused = TestClient(app, base_url="https://muxdev.example").post("/api/v1/auth/pair", json={
        "pairing_code": "pair-once-123",
        "device_label": "second device",
    })
    assert reused.status_code == 401

    devices = client.get("/api/v1/devices")
    assert devices.status_code == 200
    assert [item["label"] for item in devices.json()] == ["phone"]
    device_id = devices.json()[0]["device_id"]
    assert client.delete(f"/api/v1/devices/{device_id}").status_code == 403
    revoked = client.delete(
        f"/api/v1/devices/{device_id}",
        headers={"origin": "https://muxdev.example"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert client.get("/api/v1/conversations").status_code == 401
