# Stage 10.1 — Portfolio Construction Contracts & skfolio Baselines

Stage 10 begins the Portfolio Construction layer on top of the completed Stage 9 Research Lab.

## Point-in-time portfolio dataset

PortfolioReturnObservation records security, period start/end, knowledge time, total return, and exact source fact IDs.

PortfolioDatasetBuilder accepts only BACKTESTED- or PAPER-eligible Research Run Manifests. It rejects any return whose period end or knowledge time is later than the portfolio decision time.

Every security must have the exact same synchronous set of historical periods. Missing/asynchronous histories fail closed rather than being forward-filled, dropped, or silently aligned.

The resulting PortfolioDataset fingerprints the exact Stage 9 model/manifest, decision time, security order, periods, returns, and observation IDs.

## Constraint policy

PortfolioConstraintPolicy records full-investment requirement, long-only status, minimum/maximum weight, maximum gross exposure, optional maximum one-way turnover, rationale, and evidence references.

Constraint checks are independent of the external optimization library. An allocator result is validated after it returns. The system never silently clips or renormalizes an output to make it appear compliant.

## skfolio adapter

SkfolioBaselineAllocator currently exposes two explicit benchmark families:

- EqualWeighted;
- InverseVolatility.

The exact installed skfolio version is recorded in every PortfolioSolution together with allocator kind, constraint policy, Stage 9 manifest, portfolio dataset, previous weights, exposures, and turnover.

Initial allocation is treated as a move from cash. Turnover-constrained allocation requires explicit prior weights.

Every solution carries `capital_authority = NONE`.

## Immutable persistence

PortfolioSolutionStore persists allocation outputs idempotently in DuckDB using the content-addressed solution ID.

## Boundary

Stage 10.1 establishes portfolio-construction inputs, baselines, lineage, and post-allocation policy gates. It does not yet introduce an optimizer that is allowed to choose weights under constraints.

## Next slice

Stage 10.2 should add covariance-estimation contracts and a skfolio covariance adapter, beginning with empirical covariance and Ledoit-Wolf shrinkage. Covariance artifacts must bind to the exact PortfolioDataset and estimator/version before MeanRisk is introduced.
