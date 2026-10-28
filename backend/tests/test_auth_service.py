"""Persistence/security checks independent of HTTP and browser presentation."""

import sqlite3
import time

import pytest

from backend.auction_sim.auth import (
    AuthError,
    AuthService,
    InvalidCredentials,
    RateLimited,
    UsernameTaken,
    token_hash,
)
from backend.auction_sim.storage import Store

PASSWORD = "test-password-42"


@pytest.fixture
def auth(tmp_path):
    store = Store(tmp_path / "authentication.sqlite3")
    store.initialize()
    yield AuthService(store)
    store.close()


def test_password_and_session_secrets_are_not_stored_plaintext(auth):
    user = auth.register("Alice", PASSWORD)
    session = auth.login("ALICE", PASSWORD)
    assert user == {"id": user["id"], "username": "alice", "role": "user"}
    assert auth.authenticate(session["token"])["user"] == user
    with auth.store.reader() as conn:
        stored_user = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        stored_session = conn.execute("SELECT * FROM sessions").fetchone()
    assert stored_user["password_hash"].startswith("$argon2id$")
    assert PASSWORD not in stored_user["password_hash"]
    assert stored_session["token_hash"] == token_hash(session["token"])
    assert stored_session["token_hash"] != session["token"]
    assert session["expires_at"] > int(time.time())
    assert session["token"] != session["csrf_token"]


def test_logout_revokes_persisted_session(auth):
    auth.register("alice", PASSWORD)
    session = auth.login("alice", PASSWORD)
    reopened = AuthService(Store(auth.store.path))
    assert reopened.authenticate(session["token"]) is not None
    reopened.logout(session["token"])
    assert auth.authenticate(session["token"]) is None
    reopened.store.close()


def test_expired_session_rejected(auth):
    auth.register("alice", PASSWORD)
    session = auth.login("alice", PASSWORD)
    with auth.store.transaction() as conn:
        conn.execute("UPDATE sessions SET expires_at = ?", (int(time.time()) - 1,))
    assert auth.authenticate(session["token"]) is None
    assert auth.authenticate("x" * 43) is None
    assert auth.authenticate("not-a-token") is None
    assert auth.authenticate(None) is None


def test_username_uniqueness_case_insensitive_and_no_first_user_admin(auth):
    assert auth.register("Alice", PASSWORD)["role"] == "user"
    with pytest.raises(UsernameTaken):
        auth.register("ALICE", PASSWORD)
    assert len(auth.list_users()) == 1


def test_password_spaces_are_preserved(auth):
    auth.register("alice", " " + PASSWORD + " ")
    with pytest.raises(InvalidCredentials):
        auth.login("alice", PASSWORD)
    assert auth.login("alice", " " + PASSWORD + " ")["user"]["username"] == "alice"


def test_incorrect_and_unknown_credentials(auth):
    auth.register("alice", PASSWORD)
    for username, password in [("alice", "wrong-password"), ("nobody", PASSWORD)]:
        with pytest.raises(InvalidCredentials, match="Invalid username or password"):
            auth.login(username, password)


def test_admin_provisioning_and_promotion_revoke_previous_session(auth):
    admin = auth.create_admin("admin", PASSWORD)
    assert admin["role"] == "admin"
    alice = auth.register("alice", PASSWORD)
    session = auth.login("alice", PASSWORD)
    promoted = auth.promote("alice")
    assert promoted == {**alice, "role": "admin"}
    assert auth.authenticate(session["token"]) is None
    assert auth.login("alice", PASSWORD)["user"]["role"] == "admin"
    assert all(set(user) == {"id", "username", "role"} for user in auth.list_users())


def test_role_is_read_from_database_for_every_session_check(auth):
    admin = auth.create_admin("admin", PASSWORD)
    session = auth.login("admin", PASSWORD)
    with auth.store.transaction() as conn:
        conn.execute("UPDATE users SET role = 'user' WHERE id = ?", (admin["id"],))
    assert auth.authenticate(session["token"])["user"]["role"] == "user"


def test_rate_limit_survives_service_restart(auth):
    for _ in range(10):
        with pytest.raises(InvalidCredentials):
            auth.login("nobody", PASSWORD, remote_key="test-client")
    reopened = AuthService(Store(auth.store.path))
    with pytest.raises(RateLimited) as limited:
        reopened.login("nobody", PASSWORD, remote_key="test-client")
    assert 1 <= limited.value.retry_after <= 300
    reopened.store.close()


@pytest.mark.parametrize(
    "username,password",
    [("ab", PASSWORD), ("a/b", PASSWORD), ("alice", "tiny"), ("alice", "a" * 21)],
)
def test_registration_rejects_invalid_input(auth, username, password):
    with pytest.raises(AuthError):
        auth.register(username, password)
    assert auth.list_users() == []


def test_existing_sqlite_runs_survive_new_schema_initialization(tmp_path):
    path = tmp_path / "existing.sqlite3"
    with sqlite3.connect(path) as original:
        original.execute(
            "CREATE TABLE runs (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, state TEXT NOT NULL)"
        )
        original.execute("INSERT INTO runs VALUES ('legacy', '2026-10-01', '{}')")
    store = Store(path)
    store.initialize()
    with store.reader() as conn:
        assert conn.execute("SELECT id FROM runs").fetchone()["id"] == "legacy"
    assert AuthService(store).register("alice", PASSWORD)["role"] == "user"
    store.close()
