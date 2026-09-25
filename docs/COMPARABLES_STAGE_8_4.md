# Stage 8.4 — Reproducible Comparable-Company Valuation

Stage 8.4 adds trading comparables without allowing the peer set or selected multiple to become an undocumented judgment call.

## Two-step workflow

Candidate universe → deterministic peer-selection policy → explicit inclusion/exclusion decisions → selection fingerprint → TRADING_COMPS methodology permit → multiple distributions → implied valuation ranges.

Peer selection is intentionally separate from valuation. A conditional methodology assessment can therefore cite the exact reviewed peer-selection fingerprint before valuation is authorized.

## Point-in-time peer snapshots

Every peer snapshot records entity identity, knowledge time, market-data as-of time, industry, geography, business-model tags, distress state, revenue, EBITDA, EBIT, net income, book equity, free cash flow, enterprise value, equity value, and evidence references.

A peer is excluded when its knowledge time or market data lies after the target valuation as-of time.

## Reproducible peer-selection policy

The policy supports explicit same-industry requirements, allowed geographies, required business tags, revenue-size bounds, distress exclusion, minimum peer count, rationale, and evidence references.

Every candidate receives either inclusion or named exclusion reasons. There is no hidden similarity score.

A selection may still be stored when it is insufficient for valuation, but it is marked structurally insufficient and the valuation engine fails closed if its minimum included-peer requirement is not met.

## Multiples

The engine currently computes EV/Revenue, EV/EBITDA, EV/EBIT, P/E, P/Book, and FCF yield.

Metrics with non-positive peer denominators are omitted for that peer rather than coerced. Each multiple is usable only when it satisfies the explicit minimum peer count.

## Distribution, not a cherry-picked point

The valuation policy must specify at least two sorted percentiles. A typical policy uses the 25th, 50th, and 75th percentiles.

For every usable metric the system preserves every peer-level value, minimum, configured percentile values, maximum, and peer count. Implied target valuation is returned for every configured percentile rather than one selected fair multiple.

## Enterprise-to-equity bridge

Enterprise multiples use the same explicit bridge discipline as DCF: enterprise value plus cash and non-operating investments, less debt, preferred equity, and noncontrolling interest. Per-share value uses explicit diluted shares.

## Fail-closed behavior

The engine rejects or marks unavailable a stale or wrong-method permit, a selection for a different target, mismatched as-of timestamps, future-known peers, insufficient peer counts, non-positive target denominators, and non-positive FCF-yield percentiles for reciprocal valuation.

## Next slice

Stage 8.5 should add explicit peer-normalization adjustments and sector-specific metric contracts, then SOTP, LBO, and valuation triangulation.

Comparable-company outputs remain valuation evidence, not investment recommendations and not capital authority.