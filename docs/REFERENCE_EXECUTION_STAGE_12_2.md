# Stage 12.2 — Deterministic Reference Fill Engine

Stage 12.2 adds a First Current-owned execution oracle before NautilusTrader is allowed into the stack.

## Why a reference engine comes first

The external execution engine must be testable against independently specified semantics. First Current therefore owns the simple top-of-book model and will later use it as a differential oracle for overlapping NautilusTrader fixtures.

## Order activation and market-data availability

Order activation time is submitted_at plus the frozen order-latency policy.

A market quote becomes available at knowledge_time plus the frozen market-data latency.

If a quote is already available when an order becomes active, the order may interact with the latest known book immediately. Otherwise it must wait for a later available quote. Future market data is never used early.

## Supported execution model

Stage 12.2 fills only against visible top-of-book liquidity.

Market orders always cross the contra touch. Limit orders are considered only when the contra touch is marketable, and the final adverse-cost-adjusted execution price must still respect the limit.

Passive queue position and passive-fill probability are deliberately not invented because Stage 12.1 does not contain order-book depth or queue data.

## Liquidity and participation

Fill capacity equals displayed contra quantity times maximum_participation_rate, rounded down to the configured quantity increment.

When partial fills are enabled, DAY/GTC orders may accumulate fills across later book updates. When partial fills are disabled, a book update is skipped unless it can satisfy the full remaining quantity.

IOC evaluates one current eligible book, may partially fill when policy permits, and expires any remainder. FOK fills only when the first eligible book can satisfy the whole remaining order; otherwise it expires with no partial fill.

Because historical replay is finite, unfilled DAY and GTC orders expire at the dataset boundary.

## Deterministic costs

Adverse execution-price adjustment is:

`slippage_bps + market_impact_bps × (fill_quantity / displayed_quantity)`

BUY prices are rounded upward to the next price tick and SELL prices downward. Commission is calculated from filled notional and the frozen commission-bps policy.

The result records VWAP, filled/remaining quantity, notional, fees, average adverse slippage, exact market-event IDs, fill IDs and transition IDs.

## State-machine enforcement

The reference engine persists every order through the Stage 12.1 immutable state ledger. Fill persistence and fill-state transitions are atomic.

## Authority boundary

The engine operates only on HISTORICAL_REPLAY runs. It has no network authority, no external-order authority and no capital authority.

## Next slice

Stage 12.3 should integrate NautilusTrader in historical-backtest mode only. The adapter must map First Current instruments, market events and simulated intents explicitly, record the exact NautilusTrader version/configuration, disable live adapters, and run differential fixtures against this reference engine for supported market/limit cases before Nautilus results are accepted.
