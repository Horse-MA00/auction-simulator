"""Authentication, role authorization and bidder-level confidentiality checks."""

from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.auction_sim.main import create_app

PASSWORD = "Correct-Horse-42!"


def sign_in(client, username, *, register=False):
    endpoint = "register" if register else "login"
    response = client.post(
        f"/api/auth/{endpoint}", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code in (200, 201), response.text
    body = response.json()
    client.headers["X-CSRF-Token"] = body["csrf_token"]
    return body["user"]


@pytest.fixture
def access(tmp_path):
    app = create_app(db_path=tmp_path / "access.sqlite3")
    with ExitStack() as stack:
        clients = {
            name: stack.enter_context(TestClient(app))
            for name in ("admin", "alice", "bob", "outsider", "anonymous")
        }
        admin_user = app.state.auth.create_admin("administrator", PASSWORD)
        sign_in(clients["admin"], "administrator")
        users = {"admin": admin_user}
        for name in ("alice", "bob", "outsider"):
            users[name] = sign_in(clients[name], name, register=True)
        yield {"clients": clients, "users": users, "app": app}


def make_assigned_run(access):
    response = access["clients"]["admin"].post(
        "/api/runs",
        json={
            "item_name": "Assigned research auction",
            "reserve_price": 10,
            "bidders": [
                {
                    "id": "bidder-alice",
                    "name": "Alice bidder",
                    "valuation": 1010,
                    "budget": 1100,
                    "owner_user_id": access["users"]["alice"]["id"],
                },
                {
                    "id": "bidder-bob",
                    "name": "Bob bidder",
                    "valuation": 2075,
                    "budget": 2500,
                    "owner_user_id": access["users"]["bob"]["id"],
                },
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def complete(access, run_id):
    response = access["clients"]["admin"].post(f"/api/runs/{run_id}/execute")
    assert response.status_code == 200, response.text
    return response.json()


def assert_alice_view(view):
    bidders = {bidder["id"]: bidder for bidder in view["bidders"]}
    assert bidders["bidder-alice"]["valuation"] == 1010
    assert bidders["bidder-alice"]["budget"] == 1100
    assert bidders["bidder-bob"]["valuation"] is None
    assert bidders["bidder-bob"]["budget"] is None
    assert set(view["bids"] or {}) <= {"bidder-alice"}
    if view["results"] is not None:
        assert view["results"]["winner_id"] == "bidder-bob"
        assert view["results"]["winning_bid"] is None
        assert view["results"]["revenue"] is None
        assert {row["bidder_id"] for row in view["results"]["outcomes"]} == {"bidder-alice"}
        assert view["results"]["outcomes"][0]["bid"] == 808


def assert_alice_events(events):
    for event in events:
        payload = event["payload"]
        if event["type"] == "auction_created":
            bidders = {bidder["id"]: bidder for bidder in payload["config"]["bidders"]}
            assert bidders["bidder-alice"]["valuation"] == 1010
            assert bidders["bidder-bob"]["valuation"] is None
            assert bidders["bidder-bob"]["budget"] is None
        elif event["type"] == "auction_settled":
            assert payload["results"]["winning_bid"] is None
            assert payload["results"]["revenue"] is None
            own = payload["results"]["outcomes"]
            assert {row["bidder_id"] for row in own} == {"bidder-alice"}
        elif event["visibility"] == "private" and event["actor"] != "bidder-alice":
            assert payload.get("redacted") is True
            assert "amount" not in payload
            assert "message" not in payload


def test_registration_me_login_and_logout(access):
    alice = access["clients"]["alice"]
    me = alice.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["role"] == "user"
    assert me.json()["csrf_token"] == alice.headers["X-CSRF-Token"]
    assert "password" not in me.json()["user"]
    assert "password_hash" not in me.json()["user"]
    response = alice.post("/api/auth/logout")
    assert response.status_code in (200, 204)
    assert alice.get("/api/auth/me").status_code == 401
    sign_in(alice, "alice")
    assert alice.get("/api/auth/me").json()["user"]["id"] == access["users"]["alice"]["id"]


def test_registration_cannot_choose_or_escalate_role(access):
    anonymous = access["clients"]["anonymous"]
    response = anonymous.post(
        "/api/auth/register",
        json={"username": "attacker", "password": PASSWORD, "role": "admin"},
    )
    assert response.status_code == 422
    user = sign_in(anonymous, "attacker", register=True)
    assert user["role"] == "user"
    assert anonymous.get("/api/admin/users").status_code == 403


def test_anonymous_cannot_read_or_mutate_runs(access):
    run = make_assigned_run(access)
    anonymous = access["clients"]["anonymous"]
    assert anonymous.get("/api/health").status_code == 200
    for route in ("/api/runs", "/api/admin/users", "/api/auth/me"):
        assert anonymous.get(route).status_code == 401
    for suffix in ("", "/events", "/replay?through=1", "/export"):
        assert anonymous.get(f"/api/runs/{run['id']}{suffix}").status_code == 401
    for action in ("advance", "execute"):
        assert anonymous.post(f"/api/runs/{run['id']}/{action}").status_code == 401


def test_unassigned_user_cannot_access_another_auction_by_id(access):
    run = make_assigned_run(access)
    complete(access, run["id"])
    outsider = access["clients"]["outsider"]
    assert outsider.get("/api/runs").json()["runs"] == []
    for suffix in ("", "/events", "/replay?through=1", "/export"):
        response = outsider.get(f"/api/runs/{run['id']}{suffix}")
        assert response.status_code == 404, (suffix, response.text)


def test_users_cannot_control_auctions_or_list_accounts(access):
    run = make_assigned_run(access)
    alice = access["clients"]["alice"]
    config = {
        "item_name": "Unauthorized auction",
        "bidders": [
            {"id": "a", "name": "A", "valuation": 100, "budget": 100},
            {"id": "b", "name": "B", "valuation": 100, "budget": 100},
        ],
    }
    assert alice.post("/api/runs", json=config).status_code == 403
    assert alice.get("/api/admin/users").status_code == 403
    for action in ("advance", "execute"):
        assert alice.post(f"/api/runs/{run['id']}/{action}").status_code == 403
    assert access["clients"]["admin"].get(f"/api/runs/{run['id']}").json()["status"] == "created"


def test_user_run_list_and_completed_snapshot_hide_other_bidder_secrets(access):
    run = make_assigned_run(access)
    complete(access, run["id"])
    alice = access["clients"]["alice"]
    assert_alice_view(alice.get(f"/api/runs/{run['id']}").json())
    listed = alice.get("/api/runs").json()["runs"]
    assert len(listed) == 1
    assert_alice_view(listed[0])
    bob_view = access["clients"]["bob"].get(f"/api/runs/{run['id']}").json()
    assert bob_view["bids"] == {"bidder-bob": 1660}
    assert bob_view["results"]["winning_bid"] == 1660
    assert bob_view["results"]["revenue"] == 1660
    assert len(bob_view["results"]["outcomes"]) == 1
    admin_view = access["clients"]["admin"].get(f"/api/runs/{run['id']}").json()
    assert admin_view["bids"] == {"bidder-alice": 808, "bidder-bob": 1660}
    assert len(admin_view["results"]["outcomes"]) == 2


def test_event_and_export_views_apply_same_bidder_ownership(access):
    run = make_assigned_run(access)
    complete(access, run["id"])
    alice = access["clients"]["alice"]
    events_response = alice.get(f"/api/runs/{run['id']}/events")
    assert events_response.status_code == 200
    assert_alice_events(events_response.json()["events"])
    export = alice.get(f"/api/runs/{run['id']}/export")
    assert export.status_code == 200
    assert_alice_view(export.json()["run"])
    assert_alice_events(export.json()["events"])


def test_replay_prefix_hides_future_and_other_users_bids(access):
    run = make_assigned_run(access)
    completed = complete(access, run["id"])
    alice = access["clients"]["alice"]
    for seq in range(1, completed["event_count"] + 1):
        response = alice.get(f"/api/runs/{run['id']}/replay?through={seq}")
        assert response.status_code == 200
        snapshot = response.json()
        assert_alice_view(snapshot)
        if seq < completed["event_count"]:
            assert snapshot["results"] is None
        if seq < 3:
            assert not snapshot["bids"]


def test_private_rejected_message_is_not_exposed_to_another_owner(access):
    run = make_assigned_run(access)
    rejected = access["app"].state.receiver.receive(
        run["id"],
        {
            "message_id": "bad-bob-message-secret-bid-987654321",
            "session_id": run["id"],
            "sender": "bidder-bob",
            "round": 1,
            "type": "bid",
            "payload": {"amount": 987654321},
        },
        actor="bidder-bob",
    )
    assert rejected["accepted"] is False
    complete(access, run["id"])
    alice = access["clients"]["alice"]
    for suffix in ("/events", "/export"):
        data = alice.get(f"/api/runs/{run['id']}{suffix}").json()
        assert_alice_events(data["events"])
        assert "987654321" not in str(data)


def test_authenticated_mutation_requires_csrf(access):
    run = make_assigned_run(access)
    admin = access["clients"]["admin"]
    for token in ("", "not-the-session-token"):
        response = admin.post(
            f"/api/runs/{run['id']}/advance", headers={"X-CSRF-Token": token}
        )
        assert response.status_code == 403
    assert admin.get(f"/api/runs/{run['id']}").json()["status"] == "created"


def test_non_ascii_csrf_header_returns_403_not_server_error(access):
    run = make_assigned_run(access)
    response = access["clients"]["admin"].post(
        f"/api/runs/{run['id']}/advance", headers={b"X-CSRF-Token": b"\xff\xfe"}
    )
    assert response.status_code == 403


def test_cross_origin_authentication_and_mutation_are_rejected(access):
    anonymous = access["clients"]["anonymous"]
    for endpoint in ("register", "login"):
        response = anonymous.post(
            f"/api/auth/{endpoint}",
            json={"username": "alice", "password": PASSWORD},
            headers={"Origin": "https://untrusted.example"},
        )
        assert response.status_code == 403
    run = make_assigned_run(access)
    response = access["clients"]["admin"].post(
        f"/api/runs/{run['id']}/advance", headers={"Origin": "https://untrusted.example"}
    )
    assert response.status_code == 403


def test_websocket_requires_session_ownership_and_allowed_origin(access):
    run = make_assigned_run(access)
    for name in ("anonymous", "outsider"):
        with pytest.raises(WebSocketDisconnect):
            with access["clients"][name].websocket_connect(f"/api/runs/{run['id']}/ws"):
                pass
    with pytest.raises(WebSocketDisconnect):
        with access["clients"]["alice"].websocket_connect(
            f"/api/runs/{run['id']}/ws", headers={"Origin": "https://untrusted.example"}
        ):
            pass


def test_websocket_uses_filtered_events_and_revokes_on_logout(access):
    run = make_assigned_run(access)
    complete(access, run["id"])
    alice = access["clients"]["alice"]
    with alice.websocket_connect(f"/api/runs/{run['id']}/ws") as stream:
        batch = stream.receive_json()
        assert batch["type"] == "events"
        assert_alice_events(batch["events"])
        assert alice.post("/api/auth/logout").status_code in (200, 204)
        with pytest.raises(WebSocketDisconnect):
            stream.receive_json()


def test_invalid_credentials_do_not_create_authenticated_session(access):
    anonymous = access["clients"]["anonymous"]
    response = anonymous.post(
        "/api/auth/login", json={"username": "alice", "password": "Wrong-password-123"}
    )
    assert response.status_code == 401
    assert anonymous.get("/api/auth/me").status_code == 401
