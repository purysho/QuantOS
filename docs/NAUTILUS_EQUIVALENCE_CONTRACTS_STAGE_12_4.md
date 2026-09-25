# Stage 12.4 — Frozen Nautilus Equivalence Contracts

Stage 12.4 expands the Stage 12.3 NautilusTrader differential one behavior at a time. Each behavior gets its own frozen, content-addressed equivalence contract. No contract makes the Stage 12.3 claim broader. A fixture that needs two new behaviors at once falls outside every contract and fails closed before Nautilus runs.

## Contract object

`NautilusEquivalenceContract` records:

- the single behavior it exercises;
- its stage number;
- its scope preconditions;
- how QuantOS fields map to Nautilus;
- the fields the differential compares;
- the reference/Nautilus divergences that were observed and are refused rather than tolerated.

`contract_id` is a SHA-256 over that payload. The adapter accepts only the six contracts in `NAUTILUS_EQUIVALENCE_CONTRACTS`. A contract that has been edited, for example with a widened scope, is refused. Every Nautilus result and every differential records the `contract_id` and behavior it was produced under.

A contract must actually exercise the behavior it names. The fee contract refuses a zero commission, the latency contracts refuse zero latency, and the IOC/FOK contract refuses an order that fills completely.

## Stronger comparison for every contract

The differential now takes the reference engine's persisted `SimulatedFill` records alongside the reference result. It checks each fill's identity, confirms they match `reference_result.fill_ids` in order, and compares:

- final state;
- fill count;
- filled quantity;
- VWAP;
- total fees;
- the per-fill sequence of fill time, quantity, price and fee.

All comparisons are exact. One fill of 100 cannot match two fills of 40 + 60 even though the VWAP is the same. Mismatched fills are listed in the differential diagnostics (for example `fill[0] fee reference=1.50 nautilus=1.51`), and the result stays `MISMATCH` with `trust_authority = NONE`.

The Stage 12.3 zero-friction fixtures pass under the stronger comparison with no change to their scope.

## Shared baseline scope

Every contract requires:

- a whole-share equity with a unit multiplier;
- zero slippage and zero market impact;
- maximum participation of exactly 1;
- unique mapped quote arrival times, because ties would make the processing order ambiguous;
- exactly one quote arriving at the order's submission time;
- no behavior other than the one the contract names.

Quotes whose mapped arrival time falls after the replay horizon are not loaded into Nautilus. The reference engine drops the same quotes.

## 12.4.1 DETERMINISTIC_FEES

- `commission_bps / 10000` is set as both `maker_fee` and `taker_fee` on the Nautilus equity, so the liquidity side cannot change the fee.
- `MakerTakerFeeModel` commissions are compared per fill against the reference fee.
- **Observed divergence:** Nautilus rounds commissions half-even to the currency's minor unit (0.005 → 0.00, 0.015 → 0.02, 0.025 → 0.02), while the reference keeps the exact decimal. The contract therefore accepts only fixtures where the exact commission is already representable at currency precision. `CURRENCY_PRECISION` freezes that table, and the adapter cross-checks it against the installed Nautilus currency at run time.

## 12.4.2 ORDER_LATENCY

- `order_latency_ms` maps to `StaticLatencyModel` insert/update/cancel latency with zero base latency.
- Both engines must fill on the book that arrives exactly at `submitted_at + order_latency_ms`, and fill times must agree. Quotes may arrive between submission and activation.
- **Observed divergence:** Nautilus only processes an in-flight order when the next data point arrives, and it matches against that post-activation book. The reference matches immediately against the latest book known at activation. For example, with quotes at +0, +5 and +10 ms, a 7 ms latency fills on the +10 ms quote in Nautilus but on the +5 ms quote in the reference. If no later data arrives, Nautilus never processes the order. The contract therefore requires exactly one quote at the activation instant and refuses activations between quotes.

## 12.4.3 MARKET_DATA_LATENCY

- `knowledge_time + market_latency_ms` maps to `QuoteTick.ts_init`, and `event_time` stays `ts_event`.
- The order must be submitted when a quote arrives after the delay. Submitting at the undelayed knowledge time is refused because no book has arrived yet.

## 12.4.4 IMMEDIATE_TIME_IN_FORCE

Covers IOC and FOK orders that leave an unfilled remainder on the submission book:

- IOC with a displayed-liquidity shortfall fills the displayed quantity at the touch and expires the rest. It requires `allow_partial_fills = True`.
- FOK with a shortfall expires without any fill, whether or not partial fills are enabled.
- Non-marketable IOC/FOK limit orders expire without any fill.

Nautilus reports the venue cancel of an IOC/FOK remainder as `OrderCanceled`. The adapter maps it explicitly to QuantOS `EXPIRED` and keeps the raw Nautilus outcome in `raw_terminal_state = CANCELED`.

**Observed divergence:** with partial fills disabled, the reference IOC takes nothing from a short book, while Nautilus IOC takes the displayed quantity. That fixture is refused.

## 12.4.5 LIMIT_TRANSITION

Covers DAY/GTC limit orders that are not marketable on submission:

- If a later quote reaches the limit exactly, with enough displayed liquidity for the whole order, both engines fill once at the limit price and at the time that quote arrives.
- If no later quote makes the order marketable, the order is still working when the replay data ends. The adapter maps this to `EXPIRED` using the QuantOS horizon rule, and records `raw_terminal_state = OPEN_AT_HORIZON`.
- DAY fixtures may not cross a UTC date boundary.

**Observed divergences, both refused:**

- Nautilus fills a resting limit at its own limit price as MAKER even when the later book crosses through it. The reference fills at the contra touch.
- Nautilus liquidity consumption does not refresh on a repeated unchanged quote, so multi-quote partial accumulation differs from the reference.

## Also found and deliberately out of scope

- On an L1 book, a Nautilus MARKET DAY/GTC order larger than the displayed size fills the remainder immediately one tick worse. The reference waits for later books. Partial-liquidity market orders stay refused.
- The divergences above are the working list for Stage 12.5. Resolve each one by extending the reference semantics or by a new contract, never by tolerance.

## Authority

Nothing changes. Every result keeps `network_authority`, `external_order_authority` and `capital_authority` at `NONE`. A match grants only `REFERENCE_MATCH_ONLY`, and only for the named contract.

## Python boundary

Python 3.11 still runs every contract-registry and synthetic differential-gate test without NautilusTrader. CI sets `QUANTOS_REQUIRE_NAUTILUS=1` on Python 3.12 and 3.13, so a failed NautilusTrader install fails the build instead of quietly skipping the runtime differential tests.

## Next slice

Stage 12.5 should take on multi-event, multi-order replay and the multi-quote partial-fill divergences recorded above, each through a new frozen contract.
