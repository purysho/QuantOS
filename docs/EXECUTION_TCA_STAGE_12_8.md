# Stage 12.8 — Deterministic Transaction-Cost Analysis

`TransactionCostAnalyzer` turns a reference execution into a content-addressed `TransactionCostReport`. Its inputs are:

- the replay dataset;
- the simulation policy;
- the instrument;
- the order intent;
- the `ReferenceExecutionResult`;
- the persisted `SimulatedFill` records, whose identities and order are verified against the result.

## Benchmark

The arrival benchmark is the mid of the latest book available at submission, after market-data latency. If no book is available, analysis fails closed. There is no substitute benchmark.

## Exact implementation-shortfall decomposition

Let s = +1 for a buy and −1 for a sell, and m be the contract multiplier. Positive numbers are costs.

| Component | Definition |
| --- | --- |
| timing (delay, including latency) | Σ q·s·m·(fill-quote mid − arrival mid) |
| half spread | Σ q·s·m·(fill-quote touch − fill-quote mid) |
| slippage / impact | Σ q·s·m·(fill price − fill-quote touch) |
| fees | Σ fee |
| opportunity | unfilled·s·m·(terminal mid − arrival mid) |

The terminal time follows the Stage 12.2 semantics shared with Stage 12.5:

- the last fill, if filled;
- activation, for IOC/FOK;
- the replay horizon, for DAY/GTC expiry;
- submission, if rejected.

Before returning, the report asserts in `Decimal` that the components sum exactly to Σ q·s·m·(price − arrival mid) + fees + opportunity. Shortfall in bps is measured against the paper notional, q·arrival mid·m.

## Other measures

- Fill ratio.
- Average participation: filled quantity divided by displayed contra size on the fill quotes.
- Activation delay, and time to first and last fill.
- Arrival spread in bps.
- Market VWAP and slippage versus market VWAP, computed from trade prints known inside the order window. When no prints exist, both are `None` and a diagnostic says so. Nothing is estimated in their place.

`aggregate` sums the components across distinct reports, for example the orders of a Stage 12.5 schedule, and gives schedule-level shortfall in bps and the value-weighted fill ratio.

Reports carry no simulation or capital authority. A historical TCA number is evidence about the frozen replay, not a forecast of live costs.
