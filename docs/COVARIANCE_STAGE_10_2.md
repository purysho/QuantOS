# Stage 10.2 — Covariance Estimation Artifacts

Stage 10.2 introduces versioned covariance artifacts between point-in-time return datasets and constrained portfolio optimization.

## Estimator policy

CovarianceEstimationPolicy freezes the estimator family, minimum observations, nearest-covariance behavior, Higham mode, iteration cap, empirical ddof, PSD requirement, optional maximum condition number, rationale, and evidence references.

The initial supported estimators are:

- EMPIRICAL — skfolio EmpiricalCovariance;
- LEDOIT_WOLF — skfolio LedoitWolf shrinkage.

skfolio covariance estimators operate in the periodicity of the supplied return matrix. Stage 10.2 deliberately preserves that convention and does not annualize the covariance artifact.

## Artifact lineage

Every CovarianceArtifact binds:

- Stage 9 model and manifest IDs;
- exact Stage 10 PortfolioDataset ID;
- decision time and security ordering;
- estimator kind;
- installed skfolio version;
- covariance policy ID;
- full covariance matrix.

Changing the data, estimator, policy, security order, or library version changes the artifact identity.

## Independent diagnostics

After skfolio fits the estimator, First Current independently validates:

- matrix shape;
- finite values;
- symmetry;
- eigenvalues;
- positive-semidefinite requirement;
- condition number and optional ceiling.

Ledoit-Wolf artifacts also record the fitted shrinkage coefficient and require it to lie in [0, 1].

## Persistence

CovarianceArtifactStore provides immutable, idempotent DuckDB persistence.

## Boundary

Covariance is an input artifact, not an optimizer decision. No portfolio weights are chosen in Stage 10.2.

## Next slice

Stage 10.3 should introduce constrained MeanRisk minimum-variance optimization using a selected CovarianceArtifact, while keeping EqualWeight and InverseVolatility as mandatory comparison baselines. The optimizer must record objective, bounds, turnover constraint, covariance artifact, solver status, and post-solution independent constraint validation.
