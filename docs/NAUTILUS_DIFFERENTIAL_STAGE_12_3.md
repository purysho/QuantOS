# Stage 12.3 — NautilusTrader Historical Differential Adapter

Stage 12.3 introduces NautilusTrader only after First Current owns independent execution contracts and a deterministic reference fill oracle.

## Version and Python boundary

The adapter pins NautilusTrader 2.0.0rc5 on Python 3.12+ only. Python 3.11 remains a supported First Current runtime but does not install or execute the Nautilus v2 adapter.

NautilusTrader 2.x currently requires Python 3.12+ and is still distributed as a release candidate. Stage 12.3 therefore treats it as an experimental historical engine, not as a production live-trading dependency.

Every Nautilus result records the exact installed package version plus a First Current adapter version and a content-addressed backtest configuration fingerprint.

## Historical-only boundary

The adapter imports BacktestEngine, not LiveNode. It creates only an in-memory simulated venue and loads First Current historical QuoteTick mappings.

Every run retains network_authority = NONE, external_order_authority = NONE and capital_authority = NONE.

## Exact differential scope

Stage 12.3 deliberately refuses broad equivalence claims. A fixture is eligible only when:

- the execution instrument is a whole-share equity with unit multiplier;
- market latency is zero;
- order latency is zero;
- commission, slippage and market impact are zero;
- maximum participation is exactly 1;
- partial fills are disabled;
- exactly one top-of-book quote exists at the order submission knowledge time;
- displayed contra liquidity can fill the complete order;
- limit orders are already marketable.

Anything outside this overlap fails closed before Nautilus is run.

## Mapping

First Current event_time maps to Nautilus ts_event and knowledge_time maps to ts_init. Top-of-book quotes map to L1_MBP QuoteTick data.

The simulated venue uses NETTING, a MARGIN account, L1_MBP, trade_execution disabled, liquidity consumption enabled, queue position disabled and a zero Maker/Taker fee model.

Trade-based execution is disabled so any unrelated TradePrint data cannot become hidden execution liquidity.

## Differential oracle

The same economic order is run once through FIRST_CURRENT_REFERENCE and once through NAUTILUS_TRADER using separate content-addressed run and intent identities.

NautilusDifferentialEngine requires both runs to share the exact Research Run Manifest, portfolio solution, replay dataset and simulation policy, and requires the economic intent fields to be identical.

It then compares final order state, fill count, filled quantity and VWAP exactly. No tolerance is needed in the zero-friction overlap.

A match records trust_authority = REFERENCE_MATCH_ONLY. A mismatch remains an explicit MISMATCH with trust_authority = NONE. Neither state creates external-order or capital authority.

## No live inference

A historical differential match means Nautilus reproduced the deliberately narrow First Current fixture. It does not validate live adapters, venue connectivity, queue models, latency models, partial fills, fees, slippage, impact or broker reconciliation.

## Next slice

Stage 12.4 should expand differential coverage carefully: explicit latency models, deterministic fees and controlled partial-liquidity cases only where Nautilus semantics can be mapped without ambiguity. Each new behavior should require a separate frozen equivalence contract rather than broadening Stage 12.3 implicitly.

## Superseded comparison (Stage 12.4)

Stage 12.4 keeps this scope unchanged as the `ZERO_FRICTION` frozen equivalence contract, and strengthens the comparison to include total fees and the exact per-fill sequence. See docs/NAUTILUS_EQUIVALENCE_CONTRACTS_STAGE_12_4.md.
