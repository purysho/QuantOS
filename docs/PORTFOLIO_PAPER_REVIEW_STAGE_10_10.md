# Stage 10.10 — Portfolio PAPER Review & Mandatory Postmortem

Stage 10.10 closes the Stage 10 portfolio research loop. It evaluates completed or interrupted shadow evidence against the exact common-OOS reference and requires a human postmortem before the work can be treated as finished.

## Review evidence

The review binds the exact Stage 10.8 authorization, the exact common-OOS PortfolioComparisonDossier, every content-addressed Stage 10.9 shadow observation, one point-in-time benchmark observation for every shadow interval, and the complete immutable enforcement-event chain.

## Calibration against OOS

For the human-selected method, the dossier compares shadow and OOS cumulative return, geometric mean return per observed period, realized period volatility, maximum drawdown and average one-way turnover.

The primary return calibration statistic is the absolute difference between shadow and OOS geometric mean period returns. This avoids directly comparing cumulative returns across unequal numbers of folds.

## Cost, drift and benchmark diagnostics

The dossier records mean absolute implementation-cost assumption error, average authorized-solution drift, average realized implementation cost, cumulative benchmark return and benchmark-relative shadow wealth.

Kill events are never hidden inside aggregate performance statistics: their exact event IDs and unique kill conditions are carried into the dossier.

## Review states

OPEN means the authorization is still active and cannot receive a final postmortem. INSUFFICIENT_EVIDENCE means the run ended without the frozen minimum number of observations. WITHIN_POLICY means the terminal shadow run met every frozen review threshold and triggered no kill condition. REVIEW_REQUIRED means at least one kill condition or review threshold failed.

These states are research diagnostics, not deployment grades.

## Mandatory human postmortem

A final postmortem requires a reviewer and a different independent challenger, explicit lessons, explicit limitations, rationale and evidence references.

The only substantive outcomes are ITERATE_RESEARCH or CLOSE_RESEARCH_LINE. An insufficient-evidence dossier can only be closed as INSUFFICIENT_EVIDENCE.

If another iteration is recommended, next_stage_recommendation is RESEARCH and research_iteration_authority is RECOMMENDATION_ONLY.

## No promotion path

Every dossier and postmortem records promotion_authority = NONE, order_authority = NONE and capital_authority = NONE. Stage 10.10 has no transition to APPROVED or LIVE.

## Stage 10 result

Stage 10 now forms an auditable chain from baseline and optimized construction through common OOS comparison, robustness diagnostics, human method selection, finite shadow authorization, runtime kill enforcement and mandatory prospective postmortem.
