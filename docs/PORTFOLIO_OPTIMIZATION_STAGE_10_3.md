# Stage 10.3 — Constrained Minimum-Variance Optimization

Stage 10.3 introduces the first optimizer that is allowed to choose portfolio weights.

## Frozen covariance only

The MeanRisk optimizer does not estimate its own covariance. A private skfolio BaseCovariance adapter returns the exact Stage 10.2 CovarianceArtifact matrix.

After fitting, QuantOS reads the covariance inside skfolio's fitted prior and compares it element-by-element with the frozen artifact. A mismatch fails closed.

## Objective

The initial objective is intentionally narrow:

- objective: MINIMIZE_RISK;
- risk measure: VARIANCE;
- fully invested;
- long only.

The optimization policy records solver, L2 regularization, rationale, and evidence. The initial default solver is CLARABEL.

## Mandatory baselines

An optimized solution is not accepted unless the exact same PortfolioDataset and PortfolioConstraintPolicy have both:

- EqualWeight baseline;
- InverseVolatility baseline.

The two baseline solution IDs are embedded in the optimized solution identity.

## Constraints

Minimum and maximum position weights are passed into MeanRisk and then independently revalidated after solving.

Gross exposure and full-investment constraints are independently revalidated after solving.

QuantOS's maximum_one_way_turnover is a **portfolio-level** turnover definition. skfolio's built-in max_turnover applies a limit to each asset's individual weight change, so Stage 10.3 does not substitute one for the other.

When a portfolio-level turnover limit is present, Stage 10.3 adds an explicit convex constraint:

`0.5 × (sum(abs(target - previous)) + abs(target_cash - previous_cash)) <= limit`

The same turnover is recomputed independently after solving. A violation fails closed.

## Solver behavior

No fallback optimizer is configured. Optimization exceptions propagate as a failed research artifact.

The output records solver name, solver status, solver objective value, skfolio version, covariance artifact, both baseline IDs, policy IDs, weights, exposures, turnover, independently calculated portfolio variance, and `capital_authority = NONE`.

## Boundary

A successful minimum-variance result is a research allocation artifact, not an investment recommendation or live-trading authorization.

## Next slice

Stage 10.4 should add a portfolio comparison/selection dossier that evaluates EqualWeight, InverseVolatility, and MinimumVariance on common out-of-sample folds. It should prevent choosing the optimizer merely because it has the best in-sample variance.
