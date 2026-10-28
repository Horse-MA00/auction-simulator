# API and message protocol

## Operator API

All endpoints are under `/api`. The API is for the trusted local researcher. It is not an authenticated remote-agent gateway.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Service health |
| GET | `/runs` | List persisted runs |
| POST | `/runs` | Validate configuration and create a run |
| GET | `/runs/{id}` | Read the current researcher view |
| POST | `/runs/{id}/advance` | Apply one scripted actor action |
| POST | `/runs/{id}/execute` | Advance until settlement |
| GET | `/runs/{id}/events?after=0` | Ordered public events after a cursor |
| GET | `/runs/{id}/replay?through=N` | Reconstruct the run through event `N` |
| GET | `/runs/{id}/export` | Download the completed experiment as JSON |
| WebSocket | `/runs/{id}/ws?after=0` | Event batches from a persisted cursor |

The OpenAPI schema at `/docs` is the authoritative source for exact request constraints and response fields.

Create a run with integer credits:

```json
{
  "item_name": "Demo lot",
  "reserve_price": 50,
  "bidders": [
    {"id": "alice", "name": "Alice", "valuation": 100, "budget": 100},
    {"id": "bob", "name": "Bob", "valuation": 90, "budget": 100}
  ]
}
```

A run progresses through `created`, `collecting`, `closed` and `settled`. `bids` and `results` are hidden until settlement. The `responded` field records which bidders have responded without revealing their bid amounts.

Each run requires 2–20 bidders with unique IDs. Valuations must be positive integers; budgets and the reserve price must be nonnegative integers. All credit fields are capped at 1,000,000,000. `auctioneer`, `receiver` and `system` are reserved IDs. Request validation returns HTTP 422, and an unknown run returns HTTP 404.

Every successful step is committed before its API response is returned. `/advance` and `/execute` return the existing result when the run is already settled. They do not reset the experiment. Create another run to repeat a scenario.

## Internal agent messages

The orchestrator binds the sender identity independently of the message. The receiver then validates the envelope and action. This internal interface is where a future programmed or LLM adapter can connect.

| Field | Meaning |
| --- | --- |
| `message_id` | Unique action identifier used for idempotency |
| `session_id` | Run identifier |
| `sender` | Auctioneer or bidder identifier |
| `recipient` | Intended receiver |
| `round` | Auction round; v0 has round 1 |
| `type` | Typed auctioneer control or bidder action |
| `payload` | Structured action-specific data |

An envelope does not grant authority by claiming an auctioneer or bidder identity. A mismatch with the bound actor is rejected. The receiver also checks phase, expected bidder, amount, budget and duplicate response rules. A rejected message leaves the auction state unchanged and produces an audit event.

Bidder observations contain the bidder's own private valuation and budget. Opponent private data and other sealed bids are omitted. The fixed strategy is deterministic, so rerunning the same configuration yields the same economic outcome, apart from identifiers and timestamps.

## Events, live updates and replay

Events have a run-local increasing `seq`, `type`, `actor`, `round`, `visibility`, `payload` and `created_at`. Clients deduplicate by `seq` and request events after the last cursor when reconnecting. A WebSocket sends batches like:

```json
{"type": "events", "events": []}
```

When there is no new event, the server may send `{"type":"heartbeat"}`. The stream is derived from persisted events; it does not execute auction logic. REST reads remain available if a WebSocket disconnects.

The event endpoint keeps private message payloads redacted even after settlement. Completed bid amounts are available in the settled run results and full audit export. The first stored event has sequence 1; replay accepts a `through` value from 1 to the run's latest event count, or omits it to replay all events.

Replay reads the persisted event sequence through the selected event and applies the same projection rules as the live view. It does not advance a run or call an agent. The final replay should match the live settled run, including winner, payments and utility. Export is a researcher record that contains the completed configuration and experiment history; do not treat it as a bidder observation.
