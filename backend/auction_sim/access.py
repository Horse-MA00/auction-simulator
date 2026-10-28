"""Account authorization and the shared HTTP/WebSocket/replay/export projection."""

from copy import deepcopy

from .engine import next_actor, public_event, public_view


def is_admin(viewer: dict) -> bool:
    return viewer["role"] == "admin"


def owned_bidders(state: dict, viewer: dict) -> set[str]:
    return {b["id"] for b in state["bidders"] if b.get("owner_user_id") == viewer["id"]}


def can_access(state: dict, viewer: dict | None) -> bool:
    # None is reserved for trusted in-process engine callers, never an anonymous HTTP user.
    return viewer is None or is_admin(viewer) or bool(owned_bidders(state, viewer))


def require_access(state: dict, viewer: dict | None):
    if not can_access(state, viewer):
        # Conceal the existence of another account's run.
        raise KeyError(state["id"])


def require_admin(viewer: dict | None):
    if viewer is not None and not is_admin(viewer):
        raise PermissionError("Administrator access required")


def bidder_views(bidders: list[dict], owned: set[str]) -> list[dict]:
    return [
        deepcopy(b)
        if b["id"] in owned
        else {
            "id": b["id"],
            "name": b["name"],
            "valuation": None,
            "budget": None,
            "owner_user_id": None,
        }
        for b in bidders
    ]


def settlement_view(results: dict | None, owned: set[str]) -> dict | None:
    if results is None:
        return None
    result = deepcopy(results)
    result["outcomes"] = [r for r in result["outcomes"] if r["bidder_id"] in owned]
    if result["winner_id"] is not None and result["winner_id"] not in owned:
        # First-price winning payment and seller revenue would disclose somebody else's bid.
        result["winning_bid"] = None
        result["revenue"] = None
    return result


def account_view(state: dict, viewer: dict | None) -> dict:
    require_access(state, viewer)
    if viewer is None:
        return public_view(state)
    view = deepcopy(state)
    view["next_actor"] = next_actor(state)
    if is_admin(viewer):
        return view
    owned = owned_bidders(state, viewer)
    view["bidders"] = bidder_views(state["bidders"], owned)
    view["bids"] = {key: value for key, value in state["bids"].items() if key in owned}
    view["results"] = settlement_view(state["results"], owned)
    return view


def account_event(event: dict, state: dict, viewer: dict | None) -> dict:
    require_access(state, viewer)
    if viewer is None:
        return public_event(event)
    view = deepcopy(event)
    if is_admin(viewer):
        return view
    owned = owned_bidders(state, viewer)
    kind, payload = event["type"], event["payload"]
    # Message IDs originate at the agent boundary and may contain arbitrary submitted text.
    view["message_id"] = None
    # Use an allowlist so a future event cannot accidentally expose arbitrary private fields.
    if kind == "auction_created":
        config = payload["config"]
        view["payload"] = {
            "session_id": payload["session_id"],
            "config": {
                "item_name": config["item_name"],
                "reserve_price": config["reserve_price"],
                "mechanism": config["mechanism"],
                "bidders": bidder_views(config["bidders"], owned),
            },
        }
    elif kind == "auction_settled":
        view["payload"] = {"results": settlement_view(payload["results"], owned)}
    elif kind == "auction_opened":
        view["payload"] = {"reserve_price": payload["reserve_price"]}
    elif kind == "auction_closed":
        view["payload"] = {}
    elif kind == "bid_submitted" and event["actor"] in owned:
        view["payload"] = {"bidder_id": event["actor"], "amount": payload["amount"]}
    elif kind == "bidder_abstained" and event["actor"] in owned:
        view["payload"] = {"bidder_id": event["actor"], "reason": payload["reason"]}
    else:
        # Rejected messages may contain arbitrary raw values, even when the sender is ours.
        view["payload"] = {"bidder_id": event["actor"], "redacted": True}
    return view
