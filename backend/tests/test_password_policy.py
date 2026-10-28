"""Creation length boundaries and compatibility with already stored passwords."""

import pytest
from fastapi.testclient import TestClient

from backend.auction_sim.auth import PASSWORDS, AuthError
from backend.auction_sim.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "passwords.sqlite3")) as active:
        yield active


@pytest.mark.parametrize("length,accepted", [(4, False), (5, True), (20, True), (21, False)])
def test_registration_length_boundaries_and_login(client, length, accepted):
    credentials = {"username": "participant", "password": "a" * length}
    response = client.post("/api/auth/register", json=credentials)
    assert response.status_code == (201 if accepted else 422)
    if accepted:
        assert client.post("/api/auth/login", json=credentials).status_code == 200
    else:
        assert client.app.state.auth.list_users() == []


@pytest.mark.parametrize("length,accepted", [(4, False), (5, True), (20, True), (21, False)])
def test_admin_creation_length_boundaries_and_login(client, length, accepted):
    auth = client.app.state.auth
    password = "b" * length
    if accepted:
        auth.create_admin("administrator", password)
        response = client.post(
            "/api/auth/login", json={"username": "administrator", "password": password}
        )
        assert response.status_code == 200
        assert response.json()["user"]["role"] == "admin"
    else:
        with pytest.raises(AuthError, match="5–20"):
            auth.create_admin("administrator", password)
        assert auth.list_users() == []


def test_existing_long_password_can_still_log_in(client):
    auth = client.app.state.auth
    account = auth.create_admin("existing-admin", "initial-password")
    legacy_password = "previously-valid-password-over-20"
    with auth.store.transaction() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (PASSWORDS.hash(legacy_password), account["id"]),
        )
    response = client.post(
        "/api/auth/login", json={"username": "existing-admin", "password": legacy_password}
    )
    assert response.status_code == 200
    assert response.json()["user"]["id"] == account["id"]
