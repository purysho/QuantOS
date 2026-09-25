# Stage 10.5 — HRP/HERC Hierarchical Candidates

Stage 10.5 adds Hierarchical Risk Parity (HRP) and Hierarchical Equal Risk Contribution (HERC) as research allocation candidates.

## Explicit model choices

The first hierarchical policy is deliberately narrow and explicit:

- risk measure: variance;
- distance: Pearson angular distance;
- Pearson absolute transform: explicit;
- Pearson power: explicit;
- linkage: Ward;
- optional maximum cluster count;
- HERC solver: explicit.

These choices are recorded in a content-addressed HierarchicalAllocationPolicy.

## Frozen covariance

HRP and HERC receive the exact Stage 10.2 covariance through the same FrozenCovarianceEstimator used by Stage 10.3.

After fitting, QuantOS independently reads the fitted prior covariance and verifies that it equals the frozen artifact. A mismatch fails closed.

## Cluster lineage

Every HierarchicalPortfolioSolution records:

- model, manifest, dataset and covariance artifact IDs;
- algorithm: HRP or HERC;
- skfolio version;
- hierarchical policy and portfolio constraint policy IDs;
- weights and prior weights;
- net/gross exposure and one-way turnover;
- independently calculated variance;
- cluster count and asset cluster labels;
- Pearson distance-matrix fingerprint;
- linkage-matrix fingerprint;
- capital_authority = NONE.

## Constraint behavior

Stage 10.5 remains fully invested and long only.

Weight, exposure, and optional QuantOS total one-way-turnover constraints are independently checked after allocation. HRP/HERC do not silently substitute a library-specific turnover concept for the operating-system policy.

## Common OOS evaluation

Stage 10.4's PortfolioComparisonFold now accepts an optional hierarchical candidate set.

When present, it must contain exactly one authentic HRP solution and one authentic HERC solution sharing the same model, manifest, training dataset, constraint policy, decision time, and security set as EqualWeight, InverseVolatility, and MinimumVariance.

The existing OOS engine then evaluates all five candidates with the same realized returns, benchmark, implementation-cost policy, concentration metrics, drawdown, expected shortfall, and market-relative wealth calculation.

There is still no automatic selected_method, no selection authority, and no capital authority.

## Next slice

Stage 10.6 should add robustness/stability diagnostics across portfolio construction methods: weight instability across adjacent folds, covariance-estimator sensitivity, clustering instability, concentration drift, and constraint fragility. A method should not progress merely because its mean OOS statistic is attractive.