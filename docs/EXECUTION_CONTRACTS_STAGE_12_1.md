# Stage 12.1 — Simulation & Execution Contracts

Stage 12 begins the Simulation & Execution Engine by freezing First Current's execution semantics before NautilusTrader is introduced.

## Engine boundary

NautilusTrader will be an adapter behind these contracts. Native Nautilus order objects, event models and account state will not become First Current's source of truth.

## Execution instruments

ExecutionInstrument separates canonical instrument identity from venue and symbol identity and freezes asset class, quote currency, price increment, quantity increment, minimum quantity and contract multiplier.

Order quantities and limit prices must align exactly to configured increments.

## Point-in-time market events

Stage 12.1 defines top-of-book quotes and trade prints with separate event_time and knowledge_time, per-instrument sequence and source fact IDs.

Market data cannot be known before it occurs. Locked/crossed top-of-book data fails closed.

HistoricalReplayDataset canonicalizes events by knowledge chronology and preserves both event identities and a nested source-lineage fingerprint. Tampering with a nested event invalidates dataset identity.

## Simulation policy

ExecutionSimulationPolicy freezes market latency, order latency, commission, slippage, market impact, maximum participation and partial-fill behavior with evidence references.

No randomness is hidden in the Stage 12.1 contract.

## Run manifest

ExecutionSimulationRunManifest binds one Research Run Manifest, one portfolio solution, one exact historical replay dataset, one simulation policy, engine name/version and code revision.

Stage 12.1 supports HISTORICAL_REPLAY only. Every run records network_authority = NONE, external_order_authority = NONE and capital_authority = NONE.

## Simulated order intent

SimulationOrderIntent supports MARKET and LIMIT orders with BUY/SELL direction and DAY/GTC/IOC/FOK time-in-force.

These are simulation artifacts, not broker instructions. They carry simulation_authority = HISTORICAL_REPLAY_ONLY and explicitly carry no external-order or capital authority.

## Fill chronology

A SimulatedFill must bind the exact run, order intent and market event. It cannot predate order submission or use a market event before that event's knowledge_time.

Cumulative filled quantity cannot exceed original order quantity.

## Order state machine

The immutable ledger enforces:

- CREATED -> ACCEPTED or REJECTED;
- ACCEPTED -> PARTIALLY_FILLED / FILLED / CANCELED / EXPIRED;
- PARTIALLY_FILLED -> PARTIALLY_FILLED / FILLED / CANCELED / EXPIRED;
- FILLED / CANCELED / REJECTED / EXPIRED are terminal.

Fill transitions are atomic with fill persistence.

## Authority boundary

Stage 12.1 contains no broker connector, no network permission, no live-order type and no real-capital authority.

## Next slice

Stage 12.2 should add an independent deterministic First Current reference fill engine for top-of-book historical replay. Market and limit orders must respect order latency, market-data knowledge time, top-of-book liquidity, participation caps, tick/lot increments and the frozen fee/slippage/impact policy. That reference engine should become the differential oracle for the later NautilusTrader adapter.
