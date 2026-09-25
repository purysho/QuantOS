# Stage 12.9 — Two-Person Execution Review

Stage 12.9 closes the historical execution program with a professional review, in the same pattern as the Stage 11.10 risk review.

## Bound evidence

Every input is checked by identity:

- the historical-replay `ExecutionSimulationRunManifest`;
- the Stage 12.5 `ExecutionSchedule` and its `ExecutionScheduleResult`, from the same run;
- the Stage 12.7 `ReplayQualityReport` for the run's dataset, which must have checked every schedule order;
- one Stage 12.8 `TransactionCostReport` for **exactly** the schedule's orders, each referencing a result that belongs to the schedule;
- optionally, a Stage 12.6 `NautilusScheduleDifferentialResult` for the same dataset and simulation policy.

Upstream evidence carrying any external-order or capital authority is rejected.

`ExecutionScheduleEngine.run` now returns the per-order reference results and fills along with the schedule result, so TCA can be produced from the same execution.

## People

The reviewer (Execution Trader) and the independent challenger (Red Team) are both required, and must differ. The review requires explicit limitations and evidence references. Every unresolved objection must appear among the challenger's objections, and any unresolved objection keeps the review at `REVIEW_REQUIRED`. It cannot be outvoted.

## Policy and states

`ExecutionReviewPolicy` freezes:

- maximum schedule implementation shortfall in bps;
- minimum value-weighted fill ratio;
- whether a DEGRADED replay requires review;
- whether an independent engine match is required;
- rationale and evidence.

| Condition | State |
| --- | --- |
| Replay UNUSABLE, or a required engine differential is absent | INSUFFICIENT_EVIDENCE |
| Replay DEGRADED under a clean-replay policy, engine MISMATCH, schedule INCOMPLETE or CONSTRAINT_BREACH, shortfall above threshold, fill ratio below minimum, or unresolved objection | REVIEW_REQUIRED |
| None of the above | WITHIN_POLICY |

## Recommendation and authority

- `WITHIN_POLICY` → `ELIGIBLE_FOR_SHADOW_EXECUTION_DESIGN_REVIEW`. This allows only the *design* of a separate shadow-execution authority plane to be reviewed.
- Every other state → `RESEARCH_ITERATION`.

Every dossier keeps approval, network, order, live and capital authority at `NONE`, and carries an explicit caveat. No historical execution artifact can be reused as a network or order permit.

## What remains before any shadow execution

A shadow or paper execution adapter still needs its own authority plane, as HANDOFF.md requires:

- finite authorization;
- kill conditions independent of strategy code;
- broker/sandbox reconciliation;
- credential isolation;
- no reuse of historical permits.

None of that is built.
