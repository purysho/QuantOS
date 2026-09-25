# Stage 7.3 — Multi-Period Fundamental Model

Stage 7.3 extends the linked one-period schedules into a reproducible
multi-period model.

## Roll-forward lineage

The first projected period is anchored to the exact validated historical
statement IDs.

Every later period is anchored to:
- those same historical base statement IDs;
- the exact prior `projection_id`;
- the new period's assumption-set fingerprint.

The prior projection therefore cannot be silently replaced.

## Projection plan

A plan is an ordered sequence of:
- exact projection period;
- exact assumption set.

Periods must be contiguous and fiscal years must increase.

Each period receives a new content-addressed projection identity. If a middle
year assumption changes:
- earlier projection IDs remain stable;
- that year changes;
- all later years change because their opening state changed.

This is deliberate lineage, not incidental recomputation.

## Model-run identity

A `MultiPeriodModelRun` is fingerprinted from:
- exact historical base statement IDs;
- ordered projection IDs.

The run is valid only when every period passes all Stage 7.2 reconciliation
checks.

## Persistence

`ModelRunStore` stores immutable manifests containing:
- base statement IDs;
- ordered projection IDs;
- parent-projection lineage;
- period metadata;
- assumption-set fingerprints;
- projected income statement;
- projected balance sheet;
- projected cash flow;
- schedule values.

Repeated insertion of the same run is an idempotent no-op.

## Comparison

Two runs on the same historical base and horizon can be compared with explicit
per-period deltas for:
- revenue;
- operating income;
- net income;
- cash;
- debt;
- equity.

No comparison result is treated as a valuation or investment recommendation.

## Next slice

Stage 7.4 should deepen the schedules before valuation:
- debt tranches and maturity schedules;
- floating/fixed-rate interest;
- tax-loss carryforwards and deferred tax handling;
- minimum-cash / cash-sweep logic;
- share-count and dilution schedule;
- explicit equity issuance / repurchase flows.

Only after those mechanisms are stable should Stage 8 introduce DCF, comps,
SOTP and LBO valuation.
