# Stage 10.9 — Enforced Shadow PAPER Observation

Stage 10.9 turns the Stage 10.8 authorization from a static packet into an enforced runtime boundary for prospective shadow portfolios.

## Authorization state

Each authorization begins ACTIVE. It can move only to SUSPENDED, TERMINATED, or EXPIRED. There is deliberately no resume transition: any non-ACTIVE state blocks later observations and requires a newly issued authorization.

## Fail-closed binding checks

Before an observation can be recorded, the ledger verifies the authorization identity, exact PAPER Research Run Manifest, current PAPER Model Registry state, exact selected solution identity, execution-assumption ID and monitoring-policy ID.

A model, manifest, solution, execution-policy or monitoring-policy change terminates the authorization under MODEL_OR_MANIFEST_CHANGE.

Missing, blank or duplicate source-fact lineage terminates the authorization under DATA_LINEAGE_BREAK.

An expired authorization is persisted as EXPIRED and refuses the observation.

## Automatic quantitative kill checks

For each accepted prospective interval the ledger calculates net and gross exposure, maximum absolute weight, half-L1 drift from the frozen authorized solution, cumulative net wealth, running maximum drawdown and running average implementation cost.

The mandatory Stage 10.8 conditions are evaluated automatically. Constraint, drawdown, turnover, implementation-cost or solution-drift breaches record the triggering observation and atomically move the authorization to SUSPENDED.

Any later observation under that authorization fails closed.

## Implementation-cost assumption variance

The ledger calculates an expected implementation-cost rate from frozen commission, half-spread, slippage, market-impact and annual borrow assumptions. Trading assumptions are applied to two-way traded notional derived from one-way turnover; borrow is prorated by the observed interval and short exposure.

Every observation stores realized minus expected implementation-cost rate as implementation_cost_assumption_variance. The running realized implementation-cost rate is still the mandatory kill metric frozen in the authorization.

## Chronology and provenance

Observation intervals must be chronological and non-overlapping. Every observation is content-addressed and persisted with source fact IDs, the exact authorization, manifest and selected solution, and explicit shadow-only authority fields.

## Authority boundary

Recorded observations retain paper_authority = SHADOW_ONLY, order_authority = NONE and capital_authority = NONE. This ledger contains no broker or live-execution path.

## Next slice

Stage 10.10 should add a portfolio PAPER review dossier over this enforcement ledger: minimum observation horizon, calibration versus the original OOS dossier, cost-model error, drift persistence, kill-event history, benchmark-relative behavior and a mandatory postmortem. It should decide only whether evidence supports another research iteration; it must not promote directly to APPROVED or LIVE.
