"""Protocol, atomicity and streaming checks at the receiver boundary."""

import sqlite3
from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

from backend.auction_sim.agents import observe
from backend.auction_sim.main import create_app
from backend.auction_sim.models import RunConfig


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "receiver.sqlite3")) as client:
        yield client


@pytest.fixture
def receiver(client):
    return client.app.state.receiver


@pytest.fixture
def run(receiver):
    return receiver.create(
        RunConfig(
            item_name="Test item",
            reserve_price=10,
            bidders=[
                {"id": "alice", "name": "Alice", "valuation": 100, "budget": 100},
                {"id": "bob", "name": "Bob", "valuation": 90, "budget": 50},
            ],
        )
    )


def message(run, kind="open", sender="auctioneer", **changes):
    return {
        "message_id": "test-message",
        "session_id": run["id"],
        "sender": sender,
        "recipient": "receiver",
        "round": 1,
        "type": kind,
        "payload": {},
        **changes,
    }


@pytest.mark.parametrize(
    "changes,actor",
    [
        ({"session_id": "another-session"}, "auctioneer"),
        ({"round": 2}, "auctioneer"),
        ({"recipient": "alice"}, "auctioneer"),
        ({"payload": {"unexpected": True}}, "auctioneer"),
        ({"type": "unknown"}, "auctioneer"),
        ({}, "alice"),  # Auctioneer impersonation.
        ({"sender": "ghost"}, "ghost"),
    ],
)
def test_invalid_messages_are_logged_without_transition(receiver, run, changes, actor):
    result = receiver.receive(run["id"], message(run, **changes), actor=actor)
    assert not result["accepted"]
    assert result["state"]["status"] == "created"
    assert result["state"]["event_count"] == 2
    assert result["state"]["responded"] == []
    events = receiver.events(run["id"])
    assert events[-1]["type"] == "message_rejected"
    assert events[-1]["payload"]["redacted"] is True
    assert receiver.execute(run["id"])["status"] == "settled"
    assert receiver.export(run["id"])["events"][1]["payload"]["message"] == message(run, **changes)


def test_duplicate_message_is_idempotent_but_changed_reuse_is_rejected(receiver, run):
    raw = message(run)
    first = receiver.receive(run["id"], raw, actor="auctioneer")
    again = receiver.receive(run["id"], raw, actor="auctioneer")
    assert first["accepted"] and again["accepted"] and again["duplicate"]
    assert again["state"] == first["state"]
    changed = receiver.receive(run["id"], {**raw, "type": "close"}, actor="auctioneer")
    assert not changed["accepted"]
    assert changed["state"]["status"] == "collecting"
    assert changed["state"]["event_count"] == 3


def test_invalid_bid_does_not_consume_turn(receiver, run):
    receiver.advance(run["id"])
    for number, amount in enumerate([-1, True, 1.5, "80", 101]):
        rejected = receiver.receive(
            run["id"],
            message(
                run,
                "bid",
                "alice",
                message_id=f"invalid-{number}",
                payload={"amount": amount},
            ),
            actor="alice",
        )
        assert not rejected["accepted"]
        assert rejected["state"]["responded"] == []
        assert rejected["state"]["next_actor"] == "alice"
    assert receiver.advance(run["id"])["responded"] == ["alice"]


def test_out_of_order_bid_and_early_close_rejected(receiver, run):
    receiver.advance(run["id"])
    bid = receiver.receive(
        run["id"],
        message(
            run,
            "bid",
            "bob",
            payload={"amount": 40},
        ),
        actor="bob",
    )
    close = receiver.receive(
        run["id"],
        message(
            run,
            "close",
            message_id="early-close",
        ),
        actor="auctioneer",
    )
    assert not bid["accepted"] and not close["accepted"]
    assert receiver.get(run["id"])["status"] == "collecting"


def test_agent_observation_contains_only_own_private_information(receiver, run):
    receiver.advance(run["id"])
    receiver.advance(run["id"])
    with receiver.store.reader() as conn:
        state = receiver.store.state(conn, run["id"])
    own = asdict(observe(state, "bob"))
    assert own["valuation"] == 90 and own["budget"] == 50
    assert "bids" not in own and "bidders" not in own and "responded" not in own
    assert "alice" not in str(own)


def test_failed_event_write_rolls_back_snapshot(receiver, run, monkeypatch):
    original_append = receiver.store.append

    def fail_after_write(*args, **kwargs):
        original_append(*args, **kwargs)
        raise sqlite3.OperationalError("simulated disk failure")

    monkeypatch.setattr(receiver.store, "append", fail_after_write)
    with pytest.raises(sqlite3.OperationalError):
        receiver.advance(run["id"])
    assert receiver.get(run["id"]) == run
    assert len(receiver.events(run["id"])) == 1


def test_websocket_reconnect_cursor_and_secrecy(client, receiver, run):
    receiver.advance(run["id"])
    receiver.advance(run["id"])
    with client.websocket_connect(f"/api/runs/{run['id']}/ws?after=2") as ws:
        packet = ws.receive_json()
        assert packet["type"] == "events"
        assert [event["seq"] for event in packet["events"]] == [3]
        assert packet["events"][0]["payload"] == {"bidder_id": "alice", "redacted": True}
        receiver.advance(run["id"])
        packet = ws.receive_json()
        assert packet["events"][0]["seq"] == 4


def test_export_requires_settlement_and_contains_audit(client, receiver, run):
    assert client.get(f"/api/runs/{run['id']}/export").status_code == 422
    receiver.execute(run["id"])
    response = client.get(f"/api/runs/{run['id']}/export")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    audit = response.json()
    bid_event = next(e for e in audit["events"] if e["type"] == "bid_submitted")
    assert bid_event["payload"]["amount"] == 80


def test_sessions_are_isolated(receiver, run):
    other = receiver.create(
        RunConfig(
            item_name="Other",
            bidders=[
                {"id": "alice", "name": "Alice", "valuation": 20, "budget": 20},
                {"id": "bob", "name": "Bob", "valuation": 10, "budget": 10},
            ],
        )
    )
    receiver.execute(run["id"])
    assert receiver.get(other["id"]) == other


def test_oversized_event_cursor_returns_validation_error(client, run):
    response = client.get(f"/api/runs/{run['id']}/events?after={10**100}")
    assert response.status_code == 422
