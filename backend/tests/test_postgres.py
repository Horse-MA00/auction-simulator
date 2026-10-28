"""Real PostgreSQL integration tests, isolated from existing tables by schema.

Set AUCTION_TEST_DATABASE_URL to a PostgreSQL test connection with permission to
create schemas. Every test creates and removes only its own randomly named schema.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

from backend.auction_sim.main import create_app
from backend.auction_sim.models import RunConfig
from backend.auction_sim.receiver import Receiver
from backend.auction_sim.storage import Store

PASSWORD = "Postgres-Test-Password-42!"


@pytest.fixture
def postgres_url():
    configured = os.environ.get("AUCTION_TEST_DATABASE_URL")
    if not configured:
        pytest.skip("Set AUCTION_TEST_DATABASE_URL to run PostgreSQL integration tests")
    base = make_url(configured)
    if base.get_backend_name() not in ("postgresql", "postgres"):
        pytest.fail("AUCTION_TEST_DATABASE_URL must point to PostgreSQL")
    base = base.set(drivername="postgresql+psycopg")
    schema = f"han2601_test_{uuid4().hex}"
    engine = create_engine(base, pool_pre_ping=True)
    with engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    options = str(base.query.get("options", ""))
    scoped = base.update_query_dict({"options": f"{options} -csearch_path={schema}".strip()})
    try:
        yield scoped.render_as_string(hide_password=False)
    finally:
        # SQLAlchemy quotes this exact, internally generated schema identifier.
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()


def authenticate(client, username, *, register=False):
    endpoint = "register" if register else "login"
    response = client.post(
        f"/api/auth/{endpoint}", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code in (200, 201), response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response.json()["user"]


def config():
    return RunConfig(
        item_name="PostgreSQL integration item",
        reserve_price=0,
        bidders=[
            {"id": "alpha", "name": "Alpha", "valuation": 100, "budget": 100},
            {"id": "bravo", "name": "Bravo", "valuation": 120, "budget": 120},
            {"id": "charlie", "name": "Charlie", "valuation": 90, "budget": 90},
        ],
    )


def test_postgres_authentication_and_revocation_survive_restart(postgres_url):
    username = f"user-{uuid4().hex[:12]}"
    with TestClient(create_app(db_path=postgres_url)) as first:
        user = authenticate(first, username, register=True)
        original_cookies = dict(first.cookies)
        csrf = first.headers["X-CSRF-Token"]
        assert user["role"] == "user"
    with TestClient(create_app(db_path=postgres_url)) as second:
        second.cookies.update(original_cookies)
        second.headers["X-CSRF-Token"] = csrf
        response = second.get("/api/auth/me")
        assert response.status_code == 200
        assert response.json()["user"] == user
        assert second.post("/api/auth/logout").status_code in (200, 204)
    with TestClient(create_app(db_path=postgres_url)) as third:
        third.cookies.update(original_cookies)
        assert third.get("/api/auth/me").status_code == 401
        assert authenticate(third, username) == user


def test_postgres_ownership_projection_for_views_events_and_export(postgres_url):
    app = create_app(db_path=postgres_url)
    with TestClient(app) as admin, TestClient(app) as alice, TestClient(app) as bob:
        app.state.auth.create_admin("postgres-admin", PASSWORD)
        authenticate(admin, "postgres-admin")
        alice_user = authenticate(alice, "postgres-alice", register=True)
        bob_user = authenticate(bob, "postgres-bob", register=True)
        spec = config().model_dump()
        spec["bidders"][0]["owner_user_id"] = alice_user["id"]
        spec["bidders"][1]["owner_user_id"] = bob_user["id"]
        response = admin.post("/api/runs", json=spec)
        assert response.status_code == 201, response.text
        run_id = response.json()["id"]
        assert admin.post(f"/api/runs/{run_id}/execute").status_code == 200
        view = alice.get(f"/api/runs/{run_id}").json()
        assert view["bids"] == {"alpha": 80}
        assert view["bidders"][1]["valuation"] is None
        assert view["bidders"][1]["budget"] is None
        assert view["results"]["winning_bid"] is None
        assert view["results"]["revenue"] is None
        assert len(view["results"]["outcomes"]) == 1
        assert view["results"]["outcomes"][0]["bidder_id"] == "alpha"
        events = alice.get(f"/api/runs/{run_id}/events").json()["events"]
        export = alice.get(f"/api/runs/{run_id}/export").json()
        assert export["run"] == view
        assert export["events"] == events
        for event in events:
            if event["type"] == "bid_submitted" and event["actor"] != "alpha":
                assert event["payload"]["redacted"] is True
                assert "amount" not in event["payload"]
        admin_view = admin.get(f"/api/runs/{run_id}").json()
        assert admin_view["bids"] == {"alpha": 80, "bravo": 96, "charlie": 72}


def test_postgres_concurrent_workers_serialize_each_transition_once(postgres_url):
    first_store = Store(postgres_url)
    second_store = Store(postgres_url)
    try:
        first_store.initialize()
        receivers = (Receiver(first_store), Receiver(second_store))
        run = receivers[0].create(config())

        def step(index):
            return receivers[index % 2].advance(run["id"])

        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(step, range(18)))
        assert results
        completed = receivers[0].get(run["id"])
        assert completed["status"] == "settled"
        assert completed["results"]["winner_id"] == "bravo"
        assert completed["results"]["revenue"] == 96
        events = receivers[0].events(run["id"])
        assert [event["seq"] for event in events] == list(range(1, 8))
        assert sum(event["type"] == "auction_settled" for event in events) == 1
        assert sum(event["type"] == "bid_submitted" for event in events) == 3
        assert receivers[1].replay(run["id"])["results"] == completed["results"]
    finally:
        first_store.close()
        second_store.close()


def test_postgres_partial_v0_run_persists_and_replays_exactly(postgres_url):
    first_store = Store(postgres_url)
    first_store.initialize()
    first = Receiver(first_store)
    run = first.create(config())
    first.advance(run["id"])
    partial = first.advance(run["id"])
    first_store.close()
    second_store = Store(postgres_url)
    try:
        second_store.initialize()
        second = Receiver(second_store)
        assert second.get(run["id"]) == partial
        completed = second.execute(run["id"])
        assert second.replay(run["id"], through=partial["event_count"]) == partial
        assert second.replay(run["id"]) == completed
        assert completed["results"]["revenue"] == 96
    finally:
        second_store.close()
