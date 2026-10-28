"""Pure mechanism rules and event reducer; no HTTP, database or model-provider calls."""

from copy import deepcopy

from .models import AbstainPayload, BidPayload, EmptyPayload, Message


class InvalidAction(ValueError):
    """An action is structurally valid but illegal in the current state."""


def next_actor(state: dict) -> str | None:
    if state["status"] in {"created", "closed"}:
        return "auctioneer"
    if state["status"] == "collecting":
        for bidder in state["bidders"]:
            if bidder["id"] not in state["responded"]:
                return bidder["id"]
        return "auctioneer"
    return None


def settle(state: dict) -> dict:
    # Iteration order is the documented tie-break; message arrival time does not decide it.
    eligible = [
        b
        for b in state["bidders"]
        if b["id"] in state["bids"] and state["bids"][b["id"]] >= state["reserve_price"]
    ]
    winner = max(eligible, key=lambda b: state["bids"][b["id"]], default=None)
    winner_id = winner["id"] if winner else None
    winning_bid = state["bids"][winner_id] if winner else None
    outcomes = []
    for bidder in state["bidders"]:
        won = bidder["id"] == winner_id
        payment = winning_bid if won else 0
        outcomes.append(
            {
                "bidder_id": bidder["id"],
                "valuation": bidder["valuation"],
                "bid": state["bids"].get(bidder["id"]),
                "payment": payment,
                "utility": bidder["valuation"] - payment if won else 0,
                "won": won,
            }
        )
    return {
        "winner_id": winner_id,
        "winning_bid": winning_bid,
        "revenue": winning_bid if winner else 0,
        "outcomes": outcomes,
    }


def validate_action(state: dict, message: Message) -> tuple[str, dict, str]:
    if message.session_id != state["id"]:
        raise InvalidAction("Message belongs to another session")
    if message.round != state["round"]:
        raise InvalidAction("Stale or unexpected round")
    if state["status"] == "settled":
        raise InvalidAction("Auction is already settled")
    if message.sender != next_actor(state):
        raise InvalidAction("Sender is not the next scheduled actor")

    if message.type in {"open", "close", "settle"}:
        EmptyPayload.model_validate(message.payload)
        if message.sender != "auctioneer":
            raise InvalidAction("Only the auctioneer may control the auction")
        if message.type == "open" and state["status"] == "created":
            return "auction_opened", {"reserve_price": state["reserve_price"]}, "public"
        if message.type == "close" and state["status"] == "collecting":
            if len(state["responded"]) != len(state["bidders"]):
                raise InvalidAction("Cannot close before every bidder responds")
            return "auction_closed", {}, "public"
        if message.type == "settle" and state["status"] == "closed":
            return "auction_settled", {"results": settle(state)}, "public"
        raise InvalidAction("Control message is invalid in the current phase")

    if state["status"] != "collecting" or message.sender == "auctioneer":
        raise InvalidAction("Bidder responses are only accepted during collection")
    bidder = next(b for b in state["bidders"] if b["id"] == message.sender)
    if message.type == "bid":
        bid = BidPayload.model_validate(message.payload)
        if bid.amount > bidder["budget"]:
            raise InvalidAction("Bid exceeds the bidder's available budget")
        # The mechanism does not enforce bid <= valuation: later strategies may overbid.
        return "bid_submitted", {"bidder_id": message.sender, "amount": bid.amount}, "private"
    abstention = AbstainPayload.model_validate(message.payload)
    return (
        "bidder_abstained",
        {"bidder_id": message.sender, "reason": abstention.reason},
        "private",
    )


def reduce_event(state: dict | None, event: dict) -> dict:
    """Reconstruct state using only the event prefix, never a future database snapshot."""
    kind, payload = event["type"], event["payload"]
    if kind == "auction_created":
        state = {
            **deepcopy(payload["config"]),
            "id": payload["session_id"],
            "status": "created",
            "round": 1,
            "responded": [],
            "bids": {},
            "results": None,
            "created_at": event["created_at"],
            "updated_at": event["created_at"],
            "event_count": 0,
        }
    elif state is None:
        raise ValueError("Event stream must start with auction_created")
    else:
        state = deepcopy(state)
        if kind == "auction_opened":
            state["status"] = "collecting"
        elif kind in {"bid_submitted", "bidder_abstained"}:
            state["responded"].append(payload["bidder_id"])
            if kind == "bid_submitted":
                state["bids"][payload["bidder_id"]] = payload["amount"]
        elif kind == "auction_closed":
            state["status"] = "closed"
        elif kind == "auction_settled":
            state["status"] = "settled"
            state["results"] = deepcopy(payload["results"])
        elif kind != "message_rejected":
            raise ValueError(f"Unknown event type: {kind}")
    state["event_count"] = event["seq"]
    state["updated_at"] = event["created_at"]
    return state


def public_view(state: dict) -> dict:
    view = deepcopy(state)
    if view["status"] != "settled":
        view["bids"] = None
    view["next_actor"] = next_actor(state)
    return view


def public_event(event: dict) -> dict:
    view = deepcopy(event)
    if view["visibility"] == "private":
        view["payload"] = {"bidder_id": view["actor"], "redacted": True}
    return view
