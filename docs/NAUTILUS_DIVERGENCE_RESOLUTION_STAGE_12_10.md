# Stage 12.10 — Resolving the Stage 12.4 Nautilus Divergences

Stage 12.4 recorded six places where NautilusTrader and the QuantOS reference engine disagree, and refused all of them. Stage 12.10 resolves five of them the only acceptable way. For each one:

1. the Nautilus behavior was pinned down by experiment;
2. an explicit, opt-in **reference semantics mode** was added to QuantOS;
3. a new frozen equivalence contract was written that requires that mode;
4. the two engines are compared exactly.

No tolerance was added, and no existing contract was widened.

## Reference semantics modes

These are new fields on `ExecutionSimulationPolicy`. Each defaults to the exact Stage 12.2 behavior. Defaults are left out of the policy identity, so every existing policy ID is unchanged. The reference engine version is now `12.10`.

| Mode | Default | Alternative |
| --- | --- | --- |
| `order_activation` | `IMMEDIATE_LATEST_BOOK` | `NEXT_QUOTE_ARRIVAL`: a delayed order matches the first book arriving at or after activation. With no such book before the horizon, the order is REJECTED. |
| `market_order_residual` | `WAIT_FOR_LIQUIDITY` | `ONE_TICK_THROUGH`: a DAY/GTC market order fills displayed size at the touch and the whole residual one tick through it, at the same instant (L1 synthetic depth). |
| `resting_limit_fill_price` | `CONTRA_TOUCH` | `LIMIT_PRICE`: fills of a *resting* limit, meaning any fill after its activation book, happen at the limit price and are marked MAKER. |
| `liquidity_refresh` | `EVERY_QUOTE` | `ON_LEVEL_SIZE_CHANGE`: quantity the order took at a price level stays consumed until that level is shown with a different size, even after the book moves away and comes back. |
| `commission_rounding` | `EXACT` | `HALF_EVEN_MINOR_UNIT`: each fill's commission is rounded to the currency's ISO minor unit. |
| `immediate_partial_fills` | `FOLLOW_POLICY` | `ALWAYS_ALLOW`: IOC takes the displayed size regardless of the partial-fill flag. |

The liquidity rule was **found by the randomized differential, not assumed**. The first version reset consumption on any change of the contra quote. 5 of 40 random books then mismatched, which showed that Nautilus keeps the memory per price level. The corrected rule matches all 120 random books in the test suite.

## New frozen contracts

| Contract | Stage | Requires |
| --- | --- | --- |
| `ROUNDED_FEES` | 12.10.1 | positive commission and `HALF_EVEN_MINOR_UNIT`. Exact half-minor-unit ties are refused, because Nautilus rounds a binary floating-point value. |
| `NEXT_ARRIVAL_LATENCY` | 12.10.2 | positive order latency, `NEXT_QUOTE_ARRIVAL`, a post-activation book inside the horizon, and full liquidity on it. |
| `RESTING_LIMIT_ACCUMULATION` | 12.10.3 | a DAY/GTC limit order, `LIMIT_PRICE` + `ON_LEVEL_SIZE_CHANGE`, partial fills enabled, and an order that does not fill completely on its activation book. |
| `MARKET_L1_SWEEP` | 12.10.4 | a DAY/GTC market order, `ONE_TICK_THROUGH`, and displayed size smaller than the order. |
| `IOC_ALWAYS_PARTIAL` | 12.10.5 | IOC with partial fills disabled, `ALWAYS_ALLOW`, and a marketable shortfall. |

- Every other contract now requires all reference modes to be at their defaults.
- `REQUIRED_REFERENCE_MODES` in `execution_nautilus.py` is the single table that says which modes a contract needs.
- There are now 12 frozen contracts.

## Still refused

- Concurrent orders sharing one book. That needs explicit shared-liquidity and queue semantics in the reference first.
- Slippage and market impact. Nautilus fill models are probabilistic or configuration-specific, and no deterministic overlap has been frozen.
- Participation below 1.

## Evidence

- `tests/test_execution_nautilus.py::Stage1210ContractTests` contains the documented cases for each contract, plus a seeded randomized differential over 120 resting-limit books.
- `tests/test_execution_reference_modes.py` covers each mode on the reference alone, so it runs without Nautilus.
