# Architecture

The v0 implementation is a modular Python service and a React client. It executes one item and one sealed-bid round per run. The simulator has no dependency on an LLM provider.

```text
React researcher UI
    │ REST commands and reads / WebSocket events
    ▼
FastAPI application
    │ advance one scripted actor, or execute until settled
    ▼
Receiver and synchronizer ← Scripted auctioneer / scripted bidders
    │ validate sender, session, round, order and action
    ▼
Deterministic first-price auction rules
    │ snapshot + ordered events in one transaction
    ▼
SQLite run and event store → replay reducer → researcher view
```

## Responsibilities

The **API** exposes run creation, progression, observation, replay and export. The API does not accept arbitrary bidder or auctioneer messages from the browser. This keeps the operator controls distinct from the agent protocol.

The **scripted auctioneer** opens a round, closes it after all bidders have responded and requests settlement. Each **scripted bidder** receives an observation limited to its own valuation and budget, the auction rules and the current phase. It submits `valuation * 4 // 5`, or abstains if it cannot afford that amount. It cannot read another bidder's valuation, budget or sealed bid through this interface.

The **receiver** is the only path for agent actions. It checks a message against the actor bound by the orchestrator, the run and the current phase. It rejects unknown senders, stale rounds, duplicate/conflicting messages and illegal actions. Invalid messages are recorded without advancing the auction state. Retries of the same message are idempotent.

The **rule engine** computes deterministic outcomes. It selects the highest bid at or above the reserve price, breaks ties by configured bidder order and charges the winner its own bid. It does not depend on HTTP, WebSockets or an LLM. A run can end without a winner.

The **store** saves the authoritative snapshot and event history atomically. A SQLite `BEGIN IMMEDIATE` transaction serializes advancement, including simultaneous requests from two browser tabs. No WebSocket connection owns the run: disconnecting the UI does not discard committed progress. The prototype uses one SQLite database on a local disk.

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

There are two different views. The local researcher configures all valuations and budgets and can inspect completed experiment exports. Each bidder receives only its own private fields and permitted public auction information. The public event stream hides live bid amounts until settlement. Full exports are available only for settled runs.

This is a trusted local research application with no authentication. Binding the service to loopback limits access to the local machine; it is not a substitute for authentication in a future hosted service.

## Boundaries for later work

New strategies should implement the same observation-to-action interface. A future LLM adapter can construct a prompt from an observation and return a validated action. Such an adapter will need timeout, retry and cost controls before it is enabled. No model output should mutate state directly.

The built-in v0 agents finish immediately, so the runner calls them inside the SQLite transaction. A future network-based agent must make its request outside that transaction, then revalidate the expected phase and actor when its response returns. Holding the database lock during an LLM request would block other runs; this runner must be extended before adding remote providers.

Additional auction mechanisms require their own action rules, information disclosure policies, settlement tests and, for dynamic auctions, round/turn transitions. The current first-price state machine should not be presented as a complete generic implementation of English, Dutch or Japanese auctions. Integer credits avoid floating-point payment errors in v0; future currency support should preserve fixed-point arithmetic.
