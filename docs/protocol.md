# API and message protocol

## Authentication

All paths below are relative to `/api`. Except for health, registration and login, endpoints require a valid `auction_session` cookie. The cookie is HttpOnly, SameSite=Strict, and valid for 12 hours. Its Secure attribute is enabled with `AUCTION_COOKIE_SECURE=true` for HTTPS deployments.

| Method | Path | Access and result |
| --- | --- | --- |
| POST | `/auth/register` | JSON username/password; creates a `user` and signs it in |
| POST | `/auth/login` | JSON username/password; sets a new session cookie |
| GET | `/auth/me` | Current `user` and `csrf_token` |
| POST | `/auth/logout` | Session + CSRF token; revokes session and clears cookie |
| GET | `/admin/users` | Administrator only; account IDs, usernames and roles |

Registration and login return `{"user":{"id":"…","username":"…","role":"user"},"csrf_token":"…"}`. The role may be `admin` when an administrator signs in, but a registration request cannot select a role. Usernames contain 3–32 ASCII letters, digits, underscores or hyphens and are normalized to lowercase. New passwords must have 5–20 characters; their leading and trailing spaces are significant. Login still accepts existing passwords created under the previous policy.

After login, send `X-CSRF-Token` with every authenticated POST. Use `/auth/me` to retrieve the token again after a page reload. Browser requests use `credentials: "include"`; the frontend keeps the CSRF token in memory. Login/registration must have `Content-Type: application/json`. Supplied Origin headers must exactly match `AUCTION_ALLOWED_ORIGINS`; WebSocket upgrades use the same allowlist. There is no cross-origin CORS grant.

Example authenticated sequence, using the cookie jar provided by an HTTP client:

```text
POST /api/auth/login     JSON {username, password}
GET  /api/admin/users    Cookie: auction_session=…
POST /api/runs           Cookie + X-CSRF-Token + JSON configuration
POST /api/runs/ID/execute  Cookie + X-CSRF-Token
POST /api/auth/logout   Cookie + X-CSRF-Token
```

Passwords and cookies should not be included in experiment exports or Git. There is no HTTP endpoint for creating or promoting an administrator; use the local `backend.auction_sim.admin` CLI.

## Run API

| Method | Path | Access and purpose |
| --- | --- | --- |
| GET | `/health` | Public service health |
| GET | `/runs` | Administrator: all; user: assigned runs only |
| POST | `/runs` | Administrator + CSRF: create a run |
| GET | `/runs/{id}` | Authorized account view |
| POST | `/runs/{id}/advance` | Administrator + CSRF: apply one scripted actor action |
| POST | `/runs/{id}/execute` | Administrator + CSRF: advance until settlement |
| GET | `/runs/{id}/events?after=0` | Authorized projected events after a cursor |
| GET | `/runs/{id}/replay?through=N` | Authorized projected run through event `N` |
| GET | `/runs/{id}/export` | Completed experiment JSON, with the same account restrictions |
| WebSocket | `/runs/{id}/ws?after=0` | Authenticated event batches from a persisted cursor |

The OpenAPI schema at `/docs` is the authoritative source for exact request constraints and response fields.

An administrator creates a run with integer credits. `owner_user_id` is optional; when present, it must identify an existing account returned by `/admin/users`. Leaving it absent creates a bidder that only administrators can inspect:

```json
{
  "item_name": "Demo lot",
  "reserve_price": 50,
  "bidders": [
    {"id": "alice", "name": "Alice", "valuation": 100, "budget": 100, "owner_user_id": "ACCOUNT_UUID"},
    {"id": "bob", "name": "Bob", "valuation": 90, "budget": 100}
  ]
}
```

A run progresses through `created`, `collecting`, `closed` and `settled`. Results exist only after settlement. Administrators can see all bids during collection; ordinary users can see their own bids as soon as they are accepted. The `responded` field records which bidders have responded.

For an ordinary user, other bidders' `valuation`, `budget` and `owner_user_id` are `null`; the `bids` mapping and settlement `outcomes` contain only owned bidders. When another account's bidder wins, `winning_bid` and `revenue` are `null` so the first-price payment cannot disclose that bid. These restrictions apply during replay and export as well as live reads.

Each run requires 2–20 bidders with unique IDs. Valuations must be positive integers; budgets and the reserve price must be nonnegative integers. All credit fields are capped at 1,000,000,000. `auctioneer`, `receiver` and `system` are reserved IDs. Request validation returns HTTP 422. Unauthenticated requests return 401, missing administrator/CSRF authority returns 403, and an unknown or unassigned run returns 404. Duplicate usernames return 409. Login/registration rate limits return 429 with `Retry-After`.

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

Events have a run-local increasing `seq`, `type`, `actor`, `round`, `visibility`, `payload` and `created_at`. Visibility labels describe the stored event; actual account access is enforced by payload projection. Clients deduplicate by `seq` and request events after the last cursor when reconnecting. A WebSocket sends batches like:

```json
{"type": "events", "events": []}
```

When there is no new event, the server may send `{"type":"heartbeat"}`. The stream is derived from persisted events; it does not execute auction logic. REST reads remain available if a WebSocket disconnects.

Administrators receive full event payloads. Users receive their own bid/abstention payloads and filtered public events; other private messages and raw rejection payloads are redacted even after settlement. The first stored event has sequence 1; replay accepts a `through` value from 1 to the run's latest event count, or omits it to replay all events.

Replay reads the persisted event sequence through the selected event and applies the same account projection rules as the live view. It does not advance a run or call an agent. The final replay matches the live settled view for that account. Export is available only after settlement and includes no broader permissions than normal reads. WebSocket authentication is rechecked while streaming; expired or revoked sessions are disconnected.
