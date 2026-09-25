# Stage 12.6 — Multi-Instrument Nautilus Schedule Differential

Stage 12.6 adds a seventh frozen equivalence contract, `MULTI_INSTRUMENT_SCHEDULE`. It tests that NautilusTrader keeps several instruments' books and orders independent when all of them run in **one** backtest.

## Contract scope

- At least two orders in one `BacktestEngine` run.
- Exactly one order per instrument. Two working orders on one instrument would share displayed liquidity, and the reference has no shared-liquidity semantics, so such schedules are refused.
- All instruments share one venue and one quote currency.
- Every order individually satisfies the Stage 12.3 zero-friction scope: no fees, latency, partial fills, slippage or impact.

## Mapping

- There is one Nautilus `Equity` per execution instrument, all on one simulated venue.
- Quotes from every instrument are interleaved by `ts_init`.
- A single strategy submits each order on its own instrument's exact point-in-time submission quote.
- Fills and terminal events are attributed back to orders by `client_order_id`.

The single-order `simulate()` now runs through the same backtest path with one order. The Stage 12.3/12.4 results are unchanged.

## Differential

Each order is compared with the reference by the usual `NautilusDifferentialEngine.compare`, which checks per-fill sequence, fees, state, quantity and VWAP exactly.

`compare_schedule` then aggregates the orders. It requires that:

- every order differential is bound to the schedule contract;
- all orders come from one Nautilus run, dataset, policy and backtest configuration;
- no order or instrument repeats.

The schedule is `MATCH` only if every order matches. One mismatch keeps the whole schedule at `MISMATCH`, with no trust authority. A match grants only `REFERENCE_MATCH_ONLY`, and there is still no network, external-order or capital authority.

## Relation to Stage 12.5

The Stage 12.5 `ExecutionScheduleEngine` can run the same orders through the reference engine and reconcile cash and inventory. Stage 12.6 proves Nautilus reproduces each order's fills in the one-order-per-instrument overlap, so both engines share the fill inputs to that reconciliation.
