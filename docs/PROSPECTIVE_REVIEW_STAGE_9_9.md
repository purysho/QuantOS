# Stage 9.9 — Prospective Calibration, Comparison & Revision Seeds

Stage 9.9 closes the research learning loop without permitting hindsight edits to the original Research Case or PAPER manifest.

## Frozen behavior expectation

A ProspectiveBehaviorExpectation is frozen at exactly the same timestamp as the scenario ForecastRecord.

It binds the exact model, manifest, shadow permit, Research Case, case-dossier fingerprint, forecast, and horizon, plus explicit ranges/limits for cumulative net return, market-relative wealth return, average implementation cost, and average one-way turnover.

Because the expectation timestamp is inherited from the forecast, later PAPER outcomes cannot rewrite the pre-observation expectation.

## Prospective comparison

Comparison is allowed only after both the scenario outcome and PAPER postmortem exist.

The engine requires chronological PAPER observations for the exact manifest, rejects observations that predate the frozen expectation, and rejects observations extending beyond the frozen forecast horizon.

It then compares realized cumulative net return, market-relative wealth return, average implementation cost, average turnover, and Research Case monitoring-condition breaches against the frozen expectation.

The descriptive state is WITHIN_FROZEN_EXPECTATIONS or DEVIATION_PRESENT. This is not an investment verdict.

## Forecast calibration

The same comparison scores the frozen scenario probabilities against the independently adjudicated realized scenario.

It records realized-scenario probability, multiclass Brier score, and log loss. If the realized scenario was assigned exactly zero probability, the state becomes ZERO_PROBABILITY_REALIZED and log loss is left undefined rather than hidden behind an epsilon.

## Explicit lessons

Every prospective comparison requires reviewer-authored lessons and evidence references. The learning record is therefore explicit rather than inferred from whether the strategy made money.

## Research Revision Seed

Lessons can produce an immutable ResearchRevisionSeed containing the prior Research Case ID, exact model/manifest/comparison lineage, author, timestamp, and evidence.

The seed's instruction is structural: create a **new** ResearchCase whose supersedes_case_id equals the prior case. The validator rejects an in-place mutation, silent case-type change, silent subject change, or revision created before the seed.

The existing ResearchCaseStore remains the authority that constructs the new content-addressed case.

## Persistence

Expectations, comparisons, and revision seeds are stored as immutable, idempotent artifacts in DuckDB.

## Boundary

This stage creates a research learning loop, not self-modifying live trading. New evidence produces a new immutable research case and a new downstream manifest; the prior case, permit, PAPER observations, and postmortem remain auditable history.

## Next slice

Stage 9.10 should consolidate Stages 9.1–9.9 into one Research Lab service/API surface and CLI workflow, then add end-to-end golden tests from point-in-time universe reconstruction through PAPER postmortem and revision seed.
