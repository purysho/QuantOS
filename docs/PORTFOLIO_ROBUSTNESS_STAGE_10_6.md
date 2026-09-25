# Stage 10.6 — Portfolio Robustness & Stability Diagnostics

Stage 10.6 adds failure-oriented diagnostics after common out-of-sample comparison.

The purpose is not to produce another optimizer ranking. It asks whether portfolio construction behavior is stable enough to deserve continued research.

## Adjacent-fold weight stability

For every active method, QuantOS compares consecutive frozen portfolio solutions using half-L1 weight distance.

Large adjacent changes are surfaced as instability even if the method's average OOS return looks attractive.

## Hierarchical clustering stability

For HRP and HERC, cluster labels are compared with a label-permutation-invariant pairwise agreement metric.

For every common pair of securities, the engine asks whether both fits agree on whether the pair belongs to the same cluster. This avoids treating arbitrary cluster label numbers as meaningful.

## Covariance-estimator sensitivity

A CovarianceSensitivityObservation compares two minimum-variance solutions that are identical in model, manifest, dataset, decision time, constraints and optimization policy but use distinct covariance estimator families.

It records the resulting weight-turnover distance and the worse covariance condition number.

The engine therefore detects portfolios whose allocation changes materially just because Empirical covariance is replaced by Ledoit-Wolf shrinkage.

## Constraint and solver fragility

The dossier records:

- minimum headroom to the maximum position-weight constraint;
- minimum headroom to the QuantOS total turnover constraint when one exists;
- count of minimum-variance fits reported as optimal_inaccurate.

Near-boundary or inaccurate solutions can require review even when their headline OOS statistics are acceptable.

## States

The output is one of:

- INSUFFICIENT_EVIDENCE;
- WITHIN_POLICY;
- REVIEW_REQUIRED.

The policy freezes all thresholds and evidence references. Missing required covariance sensitivity is not silently treated as robustness.

## Authority boundary

PortfolioRobustnessDossier retains:

- selection_authority = NONE;
- capital_authority = NONE.

A robustness pass is evidence for further research, not permission to deploy.

## Next slice

Stage 10.7 should create a Portfolio Research Decision packet that combines the common-OOS dossier and robustness dossier with explicit reviewer rationale, competing-method trade-offs and rejection reasons. It should still stop before PAPER portfolio authorization.