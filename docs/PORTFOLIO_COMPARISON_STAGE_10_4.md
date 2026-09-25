# Stage 10.4 — Common Out-of-Sample Portfolio Comparison

Stage 10.4 prevents portfolio-method selection from being driven by in-sample optimizer fit.

## Common OOS folds

EqualWeight, InverseVolatility, and MinimumVariance must be evaluated on the exact same frozen out-of-sample intervals and security returns.

Each fold requires authentic content-addressed baseline and optimized solutions. The optimized solution must cite the exact two baseline IDs used in that fold.

Within a fold all three methods must share the same Stage 9 model/manifest, training PortfolioDataset, constraint policy, security set, and decision time. The decision must occur no later than the OOS period start.

OOS periods must be chronological and non-overlapping.

## Implementation economics

One common PortfolioComparisonPolicy supplies implementation cost in basis points per unit of security traded notional.

Security traded notional is recomputed directly from target and prior weights. It is not inferred from an optimizer objective or silently replaced with a library-specific turnover definition.

Every method reports gross return, implementation cost, and net return per fold.

## Realized OOS diagnostics

For each method the dossier reports:

- cumulative OOS net return;
- realized period-return volatility;
- maximum drawdown;
- historical expected-shortfall return at an explicit confidence;
- total implementation-cost rate;
- average one-way turnover;
- average effective number of assets (1 / sum(w²));
- maximum absolute weight;
- market-benchmark-relative terminal wealth return.

These are realized OOS diagnostics. The MinimumVariance solver's in-sample objective value is not used as an OOS performance metric.

## No automatic winner

PortfolioComparisonDossier has no `selected_method` field.

It explicitly records:

- `selection_authority = NONE`;
- `capital_authority = NONE`.

The system therefore cannot promote MinimumVariance merely because it achieved the lowest in-sample variance.

## Persistence

PortfolioComparisonDossierStore persists the complete comparison artifact idempotently.

## Next slice

Stage 10.5 should add hierarchical risk-based candidates (HRP and HERC) behind the same contracts, then require them to enter this identical common-OOS comparison process rather than introducing a separate evaluation path.
