# Stage 9.2 — Versioned Factor & Signal Contracts

Stage 9.2 creates the contract between a point-in-time investable universe and a factor score.

## Exact universe binding

Every FactorSpecification contains the exact InvestableUniverse ID it was designed to score. A factor cannot silently run on another universe.

## Components

Each component defines a feature name, exact lookback-period count, minimum availability lag, maximum staleness, signed weight, and cross-sectional transform.

The sum of absolute component weights must equal one. Components may use RAW values or percentile ranks.

## Feature observations

A FeatureObservation records stable security ID, feature name, feature end time, system knowledge time, lookback-period count, value, source fact IDs, and evidence references.

Knowledge time cannot precede feature end time.

## No-look-ahead gate

For a decision timestamp, a component's cutoff is decision time minus its minimum lag.

A feature is eligible only when both its feature end time and knowledge time are at or before that cutoff, its lookback-period count matches exactly, and it is not stale under the component policy.

If no eligible feature exists, that component is MISSING for that security. The engine never substitutes a later observation.

## Cross-sectional scoring

Percentile ranks use deterministic average ranks for ties. A one-security cross section receives 0.5 rather than an arbitrary extreme rank.

Every score retains raw feature value, transformed value, weight, contribution, and exact FeatureObservation ID.

## Exclusions

A security missing any required component is excluded from that factor run with named MISSING_FEATURE reasons. Other securities may still be scored; missing data are not imputed silently.

## Identity

Factor specification IDs include name, version, exact universe ID, component windows, lag/staleness rules, transforms, weights, rationale, and evidence references.

Factor-run IDs include exact factor ID, universe ID, decision time, scored component observation IDs, scores, and exclusions.

## Next slice

Stage 9.3 should add immutable experiment specifications and walk-forward/purged validation splits. The experiment must pin factor run/spec IDs, universe IDs, train/validation/test windows, benchmarks, cost assumptions, and every tested variant.