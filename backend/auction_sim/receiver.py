"""One receiver serializes both auctioneer control messages and bidder responses."""

import json
from uuid import uuid4

from pydantic import ValidationError

from .access import account_event, account_view, can_access, require_access, require_admin
from .agents import FixedFractionBidder, ScriptedAuctioneer, observe
from .engine import (
    InvalidAction,
    next_actor,
    public_view,
    reduce_event,
    validate_action,
)
from .models import Message, RunConfig
from .storage import Store, timestamp


class Receiver:
    def __init__(self, store: Store):
        self.store = store
        self.bidder = FixedFractionBidder()
        self.auctioneer = ScriptedAuctioneer()

    def create(self, config: RunConfig, viewer: dict | None = None) -> dict:
        require_admin(viewer)
        run_id = str(uuid4())
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO runs(id, created_at, state) VALUES (?, ?, ?)",
                (run_id, timestamp(), "{}"),
            )
            state = self.store.append(
                conn,
                run_id,
                None,
                "auction_created",
                "system",
                {
                    "config": config.model_dump(),
                    "session_id": run_id,
                },
            )
            return account_view(state, viewer)

    def get(self, run_id: str, viewer: dict | None = None) -> dict:
        with self.store.reader() as conn:
            return account_view(self.store.state(conn, run_id), viewer)

    def list_runs(self, viewer: dict | None = None) -> list[dict]:
        with self.store.reader() as conn:
            rows = conn.execute("SELECT state FROM runs ORDER BY created_at DESC")
            visible = []
            for row in rows:
                state = json.loads(row["state"])
                if can_access(state, viewer):
                    visible.append(account_view(state, viewer))
                    if len(visible) == 100:
                        break
            return visible

    def _receive(self, conn, run_id: str, raw: dict, actor: str) -> dict:
        state = self.store.state(conn, run_id)
        # The actor is bound by our runner, never taken on trust from the message sender field.
        canonical = json.dumps(raw, sort_keys=True)
        message_id = raw.get("message_id")
        valid_id = isinstance(message_id, str) and 0 < len(message_id) <= 128
        old = (
            conn.execute(
                "SELECT * FROM messages WHERE run_id = ? AND message_id = ?",
                (run_id, message_id),
            ).fetchone()
            if valid_id
            else None
        )
        if old and old["body"] == canonical and old["actor"] == actor:
            return {
                "accepted": bool(old["accepted"]),
                "duplicate": True,
                "error": old["error"],
                "state": public_view(state),
            }
        try:
            if old:
                raise InvalidAction("Message ID was already used for a different action")
            message = Message.model_validate(raw)
            if message.sender != actor:
                raise InvalidAction("Message sender does not match the bound actor")
            kind, payload, visibility = validate_action(state, message)
            state = self.store.append(
                conn, run_id, state, kind, actor, payload, visibility, message.message_id
            )
            accepted, error = True, None
        except (ValidationError, InvalidAction) as exc:
            error = (
                str(exc) if isinstance(exc, InvalidAction) else "Invalid message or payload schema"
            )
            state = self.store.append(
                conn,
                run_id,
                state,
                "message_rejected",
                actor,
                {"reason": error, "message": raw},
                "private",
                message_id if valid_id else None,
            )
            accepted = False
        if valid_id and not old:
            conn.execute(
                "INSERT INTO messages(run_id, message_id, actor, body, accepted, error) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, message_id, actor, canonical, int(accepted), error),
            )
        return {
            "accepted": accepted,
            "duplicate": False,
            "error": error,
            "state": public_view(state),
        }

    def receive(self, run_id: str, raw: dict, *, actor: str) -> dict:
        """Internal participant boundary; not exposed as an unauthenticated HTTP endpoint."""
        with self.store.transaction() as conn:
            return self._receive(conn, run_id, raw, actor)

    def advance(self, run_id: str, viewer: dict | None = None) -> dict:
        require_admin(viewer)
        # Choosing the actor, processing its message and updating the log are atomic.
        with self.store.transaction() as conn:
            state = self.store.state(conn, run_id)
            actor = next_actor(state)
            if actor is None:
                return account_view(state, viewer)
            agent = self.auctioneer if actor == "auctioneer" else self.bidder
            message = agent.act(observe(state, actor))
            receipt = self._receive(conn, run_id, message.model_dump(), actor)
            if not receipt["accepted"]:
                raise RuntimeError(f"Built-in agent returned an invalid action: {receipt['error']}")
            return account_view(self.store.state(conn, run_id), viewer)

    def execute(self, run_id: str, viewer: dict | None = None) -> dict:
        # Max bidders 20 + open, close, settle. Each step is durably committed for WS/restart.
        for _ in range(24):
            state = self.advance(run_id, viewer)
            if state["status"] == "settled":
                return state
        raise RuntimeError("Run exceeded the v0 step bound")

    def events(self, run_id: str, after: int = 0, viewer: dict | None = None) -> list[dict]:
        with self.store.reader() as conn:
            state = self.store.state(conn, run_id)
            require_access(state, viewer)
            return [account_event(e, state, viewer) for e in self.store.events(conn, run_id, after)]

    def replay(self, run_id: str, through: int | None = None, viewer: dict | None = None) -> dict:
        with self.store.reader() as conn:
            current = self.store.state(conn, run_id)
            require_access(current, viewer)
            if through is not None and not 1 <= through <= current["event_count"]:
                raise ValueError("Replay position must be between 1 and the final event sequence")
            events = self.store.events(conn, run_id, through=through)
        state = None
        for event in events:
            state = reduce_event(state, event)
        return account_view(state, viewer)

    def export(self, run_id: str, viewer: dict | None = None) -> dict:
        with self.store.reader() as conn:
            state = self.store.state(conn, run_id)
            require_access(state, viewer)
            if state["status"] != "settled":
                raise ValueError("Full audit export is available after settlement")
            return {
                "schema_version": 2,
                "run": account_view(state, viewer),
                "events": [
                    account_event(e, state, viewer) if viewer is not None else e
                    for e in self.store.events(conn, run_id)
                ],
            }
