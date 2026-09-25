# Stage 9.3 — Immutable Walk-Forward Validation

Stage 9.3 freezes the chronology of quantitative validation before performance statistics are calculated.

## Validation samples

Each ValidationSample binds an exact factor specification, factor run, investable-universe snapshot, stable security ID, decision time, label interval, time the outcome became knowable, realized return, source fact IDs, and evidence references.

The label may not begin before the decision and an outcome may not become known before the label interval ends.

## Experiment specification

ResearchExperimentSpecification records name/version, factor ID, benchmark, hypothesis reference, number of variants tested, primary metric, rationale, and evidence.

Recording variants_tested now is deliberate: later multiple-testing diagnostics must know how much searching occurred rather than infer it from surviving results.

## Walk-forward policy

The policy explicitly records minimum training decision dates, test dates per fold, step size, purge interval, embargo interval, minimum train/test sample counts, expanding versus rolling training window, rationale, and evidence.

Test folds are not allowed to overlap.

## Leakage gates

For each test start, a candidate training sample is excluded when any of the following is true:

- its decision time falls inside the embargo interval;
- its label ends after the purge cutoff;
- its outcome was not yet knowable by the test start.

These excluded sample IDs remain in the fold manifest under separate embargoed, purged, and not-yet-known lists.

## Exact fold identity

Every fold fingerprints the experiment, validation policy, exact sample set, train/test boundaries, included train/test samples, and every excluded sample.

The full WalkForwardValidationPlan fingerprints the exact fold IDs.

## Persistence

Validation plans are stored idempotently in DuckDB so later metrics can reference one immutable validation design.

## Boundary

Stage 9.3 does not calculate Sharpe, information coefficient, alpha, PBO, DSR, or profitability. It establishes which observations are legally allowed into training and testing.

## Next slice

Stage 9.4 should add explicit portfolio/backtest economics: position construction from factor scores, benchmark baselines, turnover, commissions, spread/slippage, borrow costs, delisting/corporate-action handling, and gross-versus-net return attribution.
