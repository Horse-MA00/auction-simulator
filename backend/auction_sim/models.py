"""Strict message/configuration schemas. All monetary quantities are integer credits."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Credits = Annotated[int, Field(strict=True, ge=0, le=1_000_000_000)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BidderConfig(StrictModel):
    id: str = Field(min_length=1, max_length=32, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=60)
    valuation: Annotated[int, Field(strict=True, ge=1, le=1_000_000_000)]
    budget: Credits


class RunConfig(StrictModel):
    item_name: str = Field(min_length=1, max_length=120)
    reserve_price: Credits = 0
    mechanism: Literal["first_price_sealed_bid"] = "first_price_sealed_bid"
    bidders: list[BidderConfig] = Field(min_length=2, max_length=20)

    @model_validator(mode="after")
    def unique_bidders(self):
        ids = [bidder.id for bidder in self.bidders]
        if len(ids) != len(set(ids)):
            raise ValueError("Bidder IDs must be unique")
        if set(ids) & {"auctioneer", "receiver", "system"}:
            raise ValueError("Bidder ID is reserved by the simulator")
        return self


class Message(StrictModel):
    message_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=64)
    sender: str = Field(min_length=1, max_length=32)
    recipient: Literal["receiver"] = "receiver"
    round: Annotated[int, Field(strict=True, ge=1)] = 1
    type: Literal["open", "bid", "abstain", "close", "settle"]
    payload: dict = Field(default_factory=dict)


class BidPayload(StrictModel):
    amount: Credits


class AbstainPayload(StrictModel):
    reason: str = Field(min_length=1, max_length=160)


class EmptyPayload(StrictModel):
    pass


class Outcome(StrictModel):
    bidder_id: str
    valuation: Credits
    bid: Credits | None
    payment: Credits
    utility: int
    won: bool


class Settlement(StrictModel):
    winner_id: str | None
    winning_bid: Credits | None
    revenue: Credits
    outcomes: list[Outcome]


class RunView(RunConfig):
    id: str
    status: Literal["created", "collecting", "closed", "settled"]
    round: int
    responded: list[str]
    bids: dict[str, int] | None
    results: Settlement | None
    created_at: str
    updated_at: str
    event_count: int
    next_actor: str | None


class RunsResponse(StrictModel):
    runs: list[RunView]


class Event(StrictModel):
    seq: int
    type: str
    actor: str
    round: int
    visibility: Literal["public", "private"]
    payload: dict
    message_id: str | None
    created_at: str


class EventsResponse(StrictModel):
    events: list[Event]
