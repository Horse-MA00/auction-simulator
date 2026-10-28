"""Acceptance tests for the scripted v0 exposed through its public HTTP API."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from backend.auction_sim.main import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(db_path=tmp_path / "auctions.sqlite3")) as active:
        yield active


def configuration():
    return {
        "item_name": "Prototype auction item",
        "reserve_price": 0,
        "bidders": [
            {"id": "alpha", "name": "Alpha", "valuation": 100, "budget": 100},
            {"id": "bravo", "name": "Bravo", "valuation": 120, "budget": 120},
            {"id": "charlie", "name": "Charlie", "valuation": 90, "budget": 90},
        ],
    }


def create_run(client, config=None):
    response = client.post("/api/runs", json=config or configuration())
    assert response.status_code in (200, 201), response.text
    return response.json()


def execute(client, run_id):
    response = client.post(f"/api/runs/{run_id}/execute")
    assert response.status_code == 200, response.text
    return response.json()


def outcomes(run):
    return {row["bidder_id"]: row for row in run["results"]["outcomes"]}


def test_first_price_settlement_uses_fixed_bids_and_correct_surplus(client):
    run = create_run(client)
    completed = execute(client, run["id"])
    assert completed["mechanism"] == "first_price_sealed_bid"
    assert completed["status"] == "settled"
    assert completed["bids"] == {"alpha": 80, "bravo": 96, "charlie": 72}
    assert completed["results"]["winner_id"] == "bravo"
    assert completed["results"]["winning_bid"] == 96
    assert completed["results"]["revenue"] == 96
    rows = outcomes(completed)
    assert rows["bravo"]["payment"] == 96
    assert rows["bravo"]["utility"] == 24
    assert rows["bravo"]["won"] is True
    for bidder_id in ("alpha", "charlie"):
        assert rows[bidder_id]["payment"] == 0
        assert rows[bidder_id]["utility"] == 0
        assert rows[bidder_id]["won"] is False


def test_indivisible_credit_rounding_is_floor_not_float_rounding(client):
    config = configuration()
    config["bidders"] = [
        {"id": "alpha", "name": "Alpha", "valuation": 101, "budget": 101},
        {"id": "bravo", "name": "Bravo", "valuation": 99, "budget": 99},
    ]
    completed = execute(client, create_run(client, config)["id"])
    assert completed["bids"] == {"alpha": 80, "bravo": 79}
    assert outcomes(completed)["alpha"]["utility"] == 21


def test_budget_shortfall_abstains_instead_of_changing_strategy(client):
    config = configuration()
    config["bidders"][1]["budget"] = 95
    completed = execute(client, create_run(client, config)["id"])
    assert completed["results"]["winner_id"] == "alpha"
    assert outcomes(completed)["bravo"]["bid"] is None
    assert outcomes(completed)["bravo"]["payment"] == 0
    assert outcomes(completed)["bravo"]["utility"] == 0
    event_list = client.get(f"/api/runs/{completed['id']}/events").json()["events"]
    assert any(event["type"] == "bidder_abstained" for event in event_list)


def test_reserve_can_produce_no_sale_and_zero_payments(client):
    config = configuration()
    config["reserve_price"] = 97
    completed = execute(client, create_run(client, config)["id"])
    assert completed["results"]["winner_id"] is None
    assert completed["results"]["winning_bid"] is None
    assert completed["results"]["revenue"] == 0
    assert all(row["payment"] == row["utility"] == 0 for row in outcomes(completed).values())


def test_bid_equal_to_reserve_is_eligible(client):
    config = configuration()
    config["reserve_price"] = 96
    completed = execute(client, create_run(client, config)["id"])
    assert completed["results"]["winner_id"] == "bravo"


def test_all_abstaining_is_a_completed_no_sale(client):
    config = configuration()
    for bidder in config["bidders"]:
        bidder["budget"] = 0
    completed = execute(client, create_run(client, config)["id"])
    assert completed["status"] == "settled"
    assert completed["results"]["winner_id"] is None
    assert all(row["bid"] is None for row in outcomes(completed).values())


def test_tie_uses_configured_order_not_lexical_order(client):
    config = configuration()
    config["bidders"] = [
        {"id": "zulu", "name": "First", "valuation": 100, "budget": 100},
        {"id": "alpha", "name": "Second", "valuation": 100, "budget": 100},
    ]
    completed = execute(client, create_run(client, config)["id"])
    assert completed["results"]["winner_id"] == "zulu"
    assert completed["results"]["revenue"] == 80


def test_zero_bid_can_win_with_zero_reserve_under_documented_floor_policy(client):
    config = configuration()
    config["bidders"] = [
        {"id": "alpha", "name": "Alpha", "valuation": 1, "budget": 0},
        {"id": "bravo", "name": "Bravo", "valuation": 1, "budget": 0},
    ]
    completed = execute(client, create_run(client, config)["id"])
    assert completed["results"]["winner_id"] == "alpha"
    assert completed["results"]["revenue"] == 0
    assert outcomes(completed)["alpha"]["utility"] == 1


def test_steps_wait_for_all_bidders_and_hide_sealed_bids(client):
    run = create_run(client)
    states = [run["status"]]
    for expected_responses in (0, 1, 2, 3, 3):
        response = client.post(f"/api/runs/{run['id']}/advance")
        assert response.status_code == 200, response.text
        view = response.json()
        states.append(view["status"])
        assert len(view["responded"]) == expected_responses
        assert view["bids"] is None
        assert view["results"] is None
    assert states == ["created", "collecting", "collecting", "collecting", "collecting", "closed"]
    final = client.post(f"/api/runs/{run['id']}/advance").json()
    assert final["status"] == "settled"
    events = client.get(f"/api/runs/{run['id']}/events").json()["events"]
    bid_events = [event for event in events if event["type"] == "bid_submitted"]
    assert len(bid_events) == 3
    for event in bid_events:
        assert event["visibility"] == "private"
        assert event["payload"].get("redacted") is True
        assert "bid" not in event["payload"]
        assert "amount" not in event["payload"]


def test_event_cursor_and_replay_never_show_future_results(client):
    completed = execute(client, create_run(client)["id"])
    events = client.get(f"/api/runs/{completed['id']}/events").json()["events"]
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    for event in events[:-1]:
        replay = client.get(f"/api/runs/{completed['id']}/replay", params={"through": event["seq"]})
        assert replay.status_code == 200, replay.text
        view = replay.json()
        assert view["event_count"] == event["seq"]
        assert view["bids"] is None
        assert view["results"] is None
    replay_final = client.get(
        f"/api/runs/{completed['id']}/replay", params={"through": events[-1]["seq"]}
    ).json()
    assert replay_final["status"] == completed["status"]
    assert replay_final["bids"] == completed["bids"]
    assert replay_final["results"] == completed["results"]
    tail = client.get(f"/api/runs/{completed['id']}/events", params={"after": 3}).json()["events"]
    assert tail == events[3:]
    assert client.get(f"/api/runs/{completed['id']}/replay?through=0").status_code == 422
    assert client.get(f"/api/runs/{completed['id']}/replay?through=999999").status_code == 422


def test_completed_run_is_idempotent(client):
    completed = execute(client, create_run(client)["id"])
    events_before = client.get(f"/api/runs/{completed['id']}/events").json()
    for action in ("advance", "execute", "advance"):
        response = client.post(f"/api/runs/{completed['id']}/{action}")
        assert response.status_code == 200, response.text
        assert response.json() == completed
    assert client.get(f"/api/runs/{completed['id']}/events").json() == events_before


def test_runs_and_events_survive_application_restart(tmp_path):
    database = tmp_path / "persist.sqlite3"
    with TestClient(create_app(db_path=database)) as first:
        completed = execute(first, create_run(first)["id"])
        events = first.get(f"/api/runs/{completed['id']}/events").json()
    with TestClient(create_app(db_path=database)) as second:
        assert second.get(f"/api/runs/{completed['id']}").json() == completed
        assert second.get(f"/api/runs/{completed['id']}/events").json() == events
        listed = second.get("/api/runs").json()["runs"]
        assert any(run["id"] == completed["id"] for run in listed)


def test_partially_collected_run_resumes_after_restart(tmp_path):
    database = tmp_path / "partial.sqlite3"
    with TestClient(create_app(db_path=database)) as first:
        run = create_run(first)
        first.post(f"/api/runs/{run['id']}/advance")
        partial = first.post(f"/api/runs/{run['id']}/advance").json()
        assert partial["status"] == "collecting"
        assert partial["responded"] == ["alpha"]
    with TestClient(create_app(db_path=database)) as second:
        completed = execute(second, run["id"])
        assert completed["results"]["winner_id"] == "bravo"
        events = second.get(f"/api/runs/{run['id']}/events").json()["events"]
        assert sum(event["type"] == "bid_submitted" for event in events) == 3


def test_full_audit_export_is_available_only_after_settlement(client):
    run = create_run(client)
    assert client.get(f"/api/runs/{run['id']}/export").status_code == 422
    completed = execute(client, run["id"])
    response = client.get(f"/api/runs/{run['id']}/export")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    exported = response.json()
    assert exported["schema_version"] == 1
    assert exported["run"] == completed
    sealed = [event for event in exported["events"] if event["type"] == "bid_submitted"]
    assert [event["payload"]["amount"] for event in sealed] == [80, 96, 72]


def test_websocket_recovers_events_from_cursor_and_delivers_new_steps(client):
    run = create_run(client)
    with client.websocket_connect(f"/api/runs/{run['id']}/ws?after=0") as stream:
        first = stream.receive_json()
        assert first["type"] == "events"
        assert [event["seq"] for event in first["events"]] == [1]
        client.post(f"/api/runs/{run['id']}/advance")
        next_batch = stream.receive_json()
        assert next_batch["type"] == "events"
        assert [event["type"] for event in next_batch["events"]] == ["auction_opened"]
    execute(client, run["id"])
    with client.websocket_connect(f"/api/runs/{run['id']}/ws?after=2") as reconnected:
        recovered = reconnected.receive_json()
        assert recovered["type"] == "events"
        assert recovered["events"][0]["seq"] == 3
        assert recovered["events"][-1]["type"] == "auction_settled"
        for event in recovered["events"]:
            if event["type"] == "bid_submitted":
                assert event["payload"]["redacted"] is True
                assert "amount" not in event["payload"]


@pytest.mark.parametrize(
    "change",
    [
        lambda config: config.update(reserve_price=-1),
        lambda config: config.update(reserve_price=1.5),
        lambda config: config.update(item_name=""),
        lambda config: config.update(bidders=[]),
        lambda config: config["bidders"][0].update(valuation=-1),
        lambda config: config["bidders"][0].update(budget=-1),
        lambda config: config["bidders"][0].update(valuation=True),
        lambda config: config["bidders"][1].update(id="alpha"),
    ],
)
def test_invalid_configuration_rejected_without_creating_run(client, change):
    config = deepcopy(configuration())
    change(config)
    response = client.post("/api/runs", json=config)
    assert response.status_code == 422, response.text
    assert client.get("/api/runs").json()["runs"] == []


@pytest.mark.parametrize("suffix", ["", "/events", "/replay?through=1", "/export"])
def test_missing_run_reads_return_404(client, suffix):
    assert client.get(f"/api/runs/missing-run{suffix}").status_code == 404


def test_concurrent_advances_never_duplicate_bid_or_settlement(client):
    run = create_run(client)
    with ThreadPoolExecutor(max_workers=8) as executor:
        responses = list(
            executor.map(lambda _: client.post(f"/api/runs/{run['id']}/advance"), range(16))
        )
    assert all(response.status_code == 200 for response in responses)
    completed = client.get(f"/api/runs/{run['id']}").json()
    assert completed["status"] == "settled"
    assert completed["results"]["revenue"] == 96
    events = client.get(f"/api/runs/{run['id']}/events").json()["events"]
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert sum(event["type"] == "auction_settled" for event in events) == 1
    assert sum(event["type"] == "bid_submitted" for event in events) == 3
    assert sum(event["type"] == "auction_opened" for event in events) == 1
    assert sum(event["type"] == "auction_closed" for event in events) == 1
