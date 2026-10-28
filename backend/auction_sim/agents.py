"""Programmed participants receive a filtered observation and return a message."""

from dataclasses import dataclass
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from .models import Message


@dataclass(frozen=True)
class Observation:
    session_id: str
    round: int
    phase: str
    item_name: str
    reserve_price: int
    actor: str
    valuation: int | None = None
    budget: int | None = None
    all_responded: bool = False


class Agent(Protocol):
    def act(self, observation: Observation) -> Message: ...


def observe(state: dict, actor: str) -> Observation:
    """Bidder input never contains opponent valuations, budgets, bids, or responses."""
    own = next((b for b in state["bidders"] if b["id"] == actor), None)
    return Observation(
        session_id=state["id"],
        round=state["round"],
        phase=state["status"],
        item_name=state["item_name"],
        reserve_price=state["reserve_price"],
        actor=actor,
        valuation=own["valuation"] if own else None,
        budget=own["budget"] if own else None,
        all_responded=actor == "auctioneer" and len(state["responded"]) == len(state["bidders"]),
    )


def make_message(observation: Observation, kind: str, payload: dict) -> Message:
    identity = f"{observation.session_id}:{observation.round}:{observation.actor}:{kind}"
    return Message(
        message_id=str(uuid5(NAMESPACE_URL, identity)),
        session_id=observation.session_id,
        sender=observation.actor,
        round=observation.round,
        type=kind,
        payload=payload,
    )


class FixedFractionBidder:
    def act(self, observation: Observation) -> Message:
        if observation.valuation is None or observation.budget is None:
            raise ValueError("Bidder requires its own valuation and budget")
        amount = observation.valuation * 4 // 5
        if amount > observation.budget:
            return make_message(observation, "abstain", {"reason": "insufficient_budget"})
        return make_message(observation, "bid", {"amount": amount})


class ScriptedAuctioneer:
    def act(self, observation: Observation) -> Message:
        if observation.phase == "created":
            return make_message(observation, "open", {})
        if observation.phase == "collecting" and observation.all_responded:
            return make_message(observation, "close", {})
        if observation.phase == "closed":
            return make_message(observation, "settle", {})
        raise ValueError("Auctioneer is not scheduled to act")
