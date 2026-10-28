# Architecture

The implementation is a modular Python service and a React client. It executes one item and one sealed-bid round per run. Registered accounts are authorized as administrators or ordinary users. The simulator has no dependency on an LLM provider.

```text
React administrator / participant UI
    │ Session cookie + REST commands and reads / WebSocket events
    ▼
FastAPI authentication and account authorization
    │ advance one scripted actor, or execute until settled
    ▼
Receiver and synchronizer ← Scripted auctioneer / scripted bidders
    │ validate sender, session, round, order and action
    ▼
Deterministic first-price auction rules
    │ snapshot + ordered events in one transaction
    ▼
SQLAlchemy store (SQLite or PostgreSQL)
    │ replay reducer + account-specific projection
    ▼
Authorized run, event, replay and export views
```

## Responsibilities

The **API** authenticates account sessions and exposes run creation, progression, observation, replay and export. Administrators can create and advance runs; ordinary users can read runs only when a bidder is assigned to their account. The API does not accept arbitrary bidder or auctioneer messages from the browser. Account ownership governs visibility while scripted agents continue to bid automatically.

The **authentication service** stores Argon2id password hashes and opaque sessions. The browser receives a 12-hour HttpOnly, SameSite=Strict cookie; the database stores only its token hash. Registration always creates an ordinary user. Administrator creation or promotion requires the local operator CLI. Passwords are never passed as command-line arguments. Logout and role promotion revoke sessions. Database-backed rate limits bound login and registration attempts across processes.

The **access projection** applies the same ownership policy to ordinary reads, lists, events, replay, export and WebSockets. It removes other bidders' private fields before serialization. The administrator view includes all bidder data, including live sealed bids, for experiment inspection. That operator privilege is never added to a scripted bidder's observation.

The **scripted auctioneer** opens a round, closes it after all bidders have responded and requests settlement. Each **scripted bidder** receives an observation limited to its own valuation and budget, the auction rules and the current phase. It submits `valuation * 4 // 5`, or abstains if it cannot afford that amount. It cannot read another bidder's valuation, budget or sealed bid through this interface.

The **receiver** is the only path for agent actions. It checks a message against the actor bound by the orchestrator, the run and the current phase. It rejects unknown senders, stale rounds, duplicate/conflicting messages and illegal actions. Invalid messages are recorded without advancing the auction state. Retries of the same message are idempotent.

The **rule engine** computes deterministic outcomes. It selects the highest bid at or above the reserve price, breaks ties by configured bidder order and charges the winner its own bid. It does not depend on HTTP, WebSockets or an LLM. A run can end without a winner.

The **store** uses SQLAlchemy with SQLite or PostgreSQL and saves the authoritative snapshot and event history atomically. SQLite uses `BEGIN IMMEDIATE`; PostgreSQL locks the run row during mutations so requests cannot settle one run twice. PostgreSQL read transactions use a consistent snapshot for state and event projection. No WebSocket connection owns the run: disconnecting the UI does not discard committed progress.

The **replay reducer** reconstructs a run at an event sequence number without rerunning agents. It must reproduce the final allocation and payment and keep future bids/results absent when replaying an earlier phase. Run metadata and the event order support inspection after a restart.

## State machine

| Current state | Accepted next action | Result |
| --- | --- | --- |
| `created` | Auctioneer opens | `collecting` |
| `collecting` | Next bidder submits or abstains | Remains `collecting`; response recorded |
| `collecting`, all bidders responded | Auctioneer closes | `closed` |
| `closed` | Auctioneer settles | `settled` |
| `settled` | No further auction actions | Existing result returned by progression API |

The orchestrator calls bidders sequentially to provide a simple synchronous baseline. This is still a sealed-bid mechanism: later bidders do not observe earlier bids. Sequential processing must not grant an information advantage. Response order and tie-breaking order are explicit and deterministic.

## Information access

There are three separate boundaries:

1. An administrator can inspect all persisted runs, configure bidder ownership, and advance the scripted experiment. Administrative inspection includes live bids.
2. An ordinary account can inspect only runs containing an assigned bidder. It sees its own bidders' valuations, budgets, submitted bids and result rows. Other bidders' valuations and budgets are `null`, their bids and outcomes are omitted, and another winner's payment and the equivalent first-price seller revenue are hidden. The winner identity and public phase information remain visible.
3. Each scripted bidder receives only its own private fields and permitted public information from the agent observation interface. It does not inherit its owner's or an administrator's view.

Event payloads are projected by an explicit allowlist. Rejected raw messages are redacted for ordinary users even if the actor is theirs, because their payloads may contain arbitrary data. Replay reconstructs an event prefix before projection so a later settlement does not leak into an earlier state. Exports require a settled run and retain the caller's access restrictions. WebSocket authentication is checked again during streaming so logout or expiry closes the connection.

Authenticated HTTP mutations require a session-specific `X-CSRF-Token`; registration and login require JSON and validate any supplied Origin. The configured Origin allowlist also applies to WebSockets. On Render, the service's platform-provided `RENDER_EXTERNAL_URL` is included at startup; custom domains require explicit configuration. Trust is never derived from request Host or forwarded headers. Frontend and API use a shared origin or the Vite development proxy, without permissive CORS. The default local deployment binds to loopback. HTTPS deployments should set `AUCTION_COOKIE_SECURE=true`.

## Boundaries for later work

New strategies should implement the same observation-to-action interface. A future LLM adapter can construct a prompt from an observation and return a validated action. Such an adapter will need timeout, retry and cost controls before it is enabled. No model output should mutate state directly.

The built-in v0 agents finish immediately, so the runner calls them inside the database transaction. A future network-based agent must make its request outside that transaction, then revalidate the expected phase and actor when its response returns. Holding the run/database lock during an LLM request would block progress; this runner must be extended before adding remote providers.

Additional auction mechanisms require their own action rules, information disclosure policies, settlement tests and, for dynamic auctions, round/turn transitions. The current first-price state machine should not be presented as a complete generic implementation of English, Dutch or Japanese auctions. Integer credits avoid floating-point payment errors in v0; future currency support should preserve fixed-point arithmetic.
