# Stage 11.1 — Pricing & Risk Contracts

Stage 11 begins the Pricing & Risk Engine with contracts rather than library bindings.

The rule is deliberate: QuantLib and ORE will be adapters behind First Current domain objects. Their native object graphs are not allowed to become the operating system's source of truth.

## Point-in-time market snapshots

MarketQuote separates event_time from knowledge_time and carries source fact IDs. MarketDataSnapshotBuilder rejects future-known quotes and duplicate semantic market keys.

Snapshots are content-addressed from canonical quote identities plus a separate lineage fingerprint derived from every nested source fact.

Nested quote tampering invalidates snapshot identity.

## Typed instruments

Stage 11.1 defines typed contracts for:

- equity;
- fixed-rate bond;
- European vanilla option;
- fixed/floating interest-rate swap.

Dates, notionals, strikes, coupon frequencies, day-count conventions, floating-index IDs and pay/receive direction are explicit rather than passed as unstructured dictionaries.

Each instrument is content-addressed.

## Pricing model specification and requests

PricingModelSpecification records model family, model version, canonical parameters, rationale and evidence references.

PricingRequest binds one exact instrument, market snapshot and model specification to an explicit set of requested measures and reporting currency.

Requests carry order_authority = NONE and capital_authority = NONE.

Stage 11.1 defines common measures including NPV, clean/dirty price, yield, Delta, Gamma, Vega, Theta and DV01. Support is not assumed: later engine adapters must fail closed when a requested measure is unsupported.

## Risk scenarios

RiskScenario is an explicit set of absolute or relative market shocks. A single market target cannot be shocked twice in one scenario.

Scenario identity is independent of input ordering.

## Persistence

PricingContractStore persists market snapshots, typed instruments, pricing requests and risk scenarios idempotently in DuckDB. Snapshot and request identities are independently recomputed before persistence.

## Authority boundary

Stage 11.1 contains no pricing engine, no trade recommendation, no order route and no capital authority.

## Next slice

Stage 11.2 should add a QuantLib adapter for a narrow instrument set with explicit market-data mapping, engine/version provenance, deterministic pricing results and fail-closed unsupported-measure behavior. An independent reference implementation should then be used for differential testing before QuantLib results are accepted as trusted research artifacts.
