# Stage 11.9 — Prospective VaR Calibration & Backtesting

Stage 11.9 evaluates whether frozen Stage 11.8 historical-simulation VaR estimates behave prospectively as forecasts of an empirical loss quantile.

## Forecast-before-outcome chronology

ProspectivePortfolioOutcome binds one exact historical-simulation estimate and one exact portfolio risk cube to a later realized portfolio P&L.

The realized period must start at the frozen estimate valuation timestamp and have exactly the same horizon as the historical-simulation policy. The outcome cannot be recorded before the realized period ends.

This prevents a realized result from being retroactively paired with a risk estimate produced after the outcome was known.

## Exception semantics

Realized loss is negative realized portfolio P&L.

A VaR exception occurs only when realized loss is strictly greater than the frozen VaR loss. Equality is not an exception.

The observation also records exception magnitude and whether realized loss exceeded the frozen historical expected-shortfall loss. Expected-shortfall exceedance is descriptive only; Stage 11.9 does not claim a complete ES backtest.

## Calibration history

VaRCalibrationEngine consumes immutable prospective observations with unique risk-estimate IDs and unique realized periods.

All observations in one report must use the same confidence level and forecast horizon.

The report records:

- observation count;
- exception count and rate;
- expected exception rate = 1 - confidence;
- expected exception count;
- exception-rate difference;
- mean and maximum exception magnitude;
- expected-shortfall exceedance count;
- Kupiec unconditional-coverage likelihood-ratio statistic and p-value.

## Kupiec diagnostic

The Kupiec LR_uc diagnostic tests whether the unconditional exception frequency is statistically consistent with the frozen expected exception probability.

The calibration policy freezes the significance level. If the p-value is below it, the report is REVIEW_REQUIRED. Otherwise it is WITHIN_TEST_TOLERANCE.

`WITHIN_TEST_TOLERANCE` deliberately does not mean APPROVED. Non-rejection can result from limited power, and LR_uc does not test exception independence, clustering, model misspecification, tail severity or structural regime change.

A structural floor of 20 prospective observations applies before any Kupiec state can be reported. Shorter histories remain INSUFFICIENT_EVIDENCE.

## No regulatory label

Stage 11.9 does not infer Basel traffic-light zones or any other regulatory classification. Such labels require deliberately implemented jurisdiction-specific rules and assumptions.

## Authority boundary

Every observation and report records approval_authority = NONE, order_authority = NONE and capital_authority = NONE.

## Next slice

Stage 11.10 should add exception-independence and clustering diagnostics plus a risk-review dossier that combines deterministic stress coverage, historical-simulation calibration, concentration and model limitations. Only after QuantOS's independent risk layer is stable should OpenSourceRisk/Engine be introduced as a separately versioned adapter and differential comparator.
