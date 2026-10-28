"""Render's runtime URL must work without trusting arbitrary request hosts."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.auction_sim.main import create_app

ORIGIN = "https://auction-simulator-6p52.onrender.com"


@pytest.mark.parametrize("configured", [None, "", "https://old-service.onrender.com"])
def test_render_origin_works_with_missing_empty_or_stale_configuration(
    tmp_path, monkeypatch, configured
):
    monkeypatch.setenv("RENDER_EXTERNAL_URL", ORIGIN)
    if configured is None:
        monkeypatch.delenv("AUCTION_ALLOWED_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("AUCTION_ALLOWED_ORIGINS", configured)
    app = create_app(tmp_path / "render.sqlite3")
    with TestClient(app) as client:
        credentials = {"username": "participant", "password": "short"}
        registered = client.post("/api/auth/register", json=credentials, headers={"Origin": ORIGIN})
        assert registered.status_code == 201, registered.text
        logged_in = client.post("/api/auth/login", json=credentials, headers={"Origin": ORIGIN})
        assert logged_in.status_code == 200
        # Runtime origin detection does not bypass CSRF protection.
        assert client.post("/api/auth/logout", headers={"Origin": ORIGIN}).status_code == 403
        headers = {"Origin": ORIGIN, "X-CSRF-Token": logged_in.json()["csrf_token"]}
        assert client.post("/api/auth/logout", headers=headers).status_code == 200


def test_only_exact_platform_or_explicit_custom_origin_is_allowed(tmp_path, monkeypatch):
    monkeypatch.setenv("RENDER_EXTERNAL_URL", ORIGIN + "/")
    monkeypatch.setenv("AUCTION_ALLOWED_ORIGINS", "https://auctions.example/")
    with TestClient(create_app(tmp_path / "origins.sqlite3")) as client:
        for allowed in (ORIGIN, "https://auctions.example"):
            # Empty credentials reach schema validation without authenticating a user.
            assert (
                client.post("/api/auth/login", json={}, headers={"Origin": allowed}).status_code
                == 422
            )
        for rejected in (
            "https://another-app.onrender.com",
            ORIGIN + ".attacker.example",
            ORIGIN.replace("https:", "http:"),
            "http://localhost:8000",
            "null",
        ):
            response = client.post(
                "/api/auth/login",
                json={},
                headers={"Origin": rejected, "X-Forwarded-Host": rejected.removeprefix("https://")},
            )
            assert response.status_code == 403


def test_websocket_uses_platform_origin_and_retains_authentication(tmp_path, monkeypatch):
    monkeypatch.setenv("RENDER_EXTERNAL_URL", ORIGIN)
    monkeypatch.setenv("AUCTION_ALLOWED_ORIGINS", "")
    with TestClient(create_app(tmp_path / "websocket.sqlite3")) as client:
        # Allowed origin proceeds to authentication; an arbitrary Render tenant does not.
        for origin, code in ((ORIGIN, 4401), ("https://another-app.onrender.com", 4403)):
            with pytest.raises(WebSocketDisconnect) as closed:
                with client.websocket_connect("/api/runs/not-a-run/ws", headers={"Origin": origin}):
                    pass
            assert closed.value.code == code
