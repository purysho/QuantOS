# Stage 11.10 — Exception Independence & Risk Review Dossier

Stage 11.10 closes QuantOS's independent Stage 11 risk layer before any OpenSourceRisk/Engine adapter is trusted.

## Exception independence

Stage 11.9 tests unconditional exception frequency. Stage 11.10 adds a Christoffersen first-order independence diagnostic for exception clustering.

Only exactly contiguous realized periods create transitions. Gaps split the sequence and do not become fabricated no-exception observations.

The diagnostic records n00, n01, n10 and n11 transition counts, LR_ind and its chi-square df=1 p-value.

Conditional-coverage LR_cc is the sum of the upstream Kupiec LR_uc and LR_ind. Its chi-square df=2 survival probability is also recorded.

The first structural floor is 19 contiguous transitions. A shorter or fragmented sequence remains INSUFFICIENT_EVIDENCE.

Statistical non-rejection is called WITHIN_TEST_TOLERANCE, never APPROVED.

## Two-person risk review

RiskReviewDossier requires a named reviewer and a different independent challenger.

It binds:

- one exact COMPLETE deterministic PortfolioRiskCube;
- the current exact historical-simulation VaR/ES estimate;
- its exact historical risk policy;
- prospective unconditional-coverage calibration;
- exception-independence / conditional-coverage diagnostic;
- frozen concentration and deterministic worst-scenario-loss review thresholds;
- explicit limitations;
- challenger objections and unresolved objections;
- evidence references.

## Review states

INSUFFICIENT_EVIDENCE applies when required prospective calibration or independence evidence has not reached its frozen minimum.

REVIEW_REQUIRED applies when statistical calibration is flagged, concentration exceeds policy, deterministic worst scenario loss exceeds policy, a required ratio is unavailable, or the independent challenger has an unresolved objection.

WITHIN_POLICY means only that the supplied research evidence did not breach those frozen rules.

## Stress and concentration semantics

Maximum concentration remains absolute base NPV share from Stage 11.7; it is not notional, delta or regulatory exposure.

Worst deterministic scenario loss is the largest non-negative loss across the supplied stress set. Dividing it by gross base NPV produces a review ratio, not a probability or VaR measure.

## Authority boundary

Every risk-review dossier records approval_authority = NONE, live_authority = NONE, order_authority = NONE and capital_authority = NONE.

## Next step

With this independent baseline in place, a later Stage 11.x adapter may integrate OpenSourceRisk/Engine as a separately versioned comparator. ORE outputs should be accepted only after exact input mapping, engine/version provenance and differential checks against QuantOS deterministic stress and supported distributional-risk fixtures.
