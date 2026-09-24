# Forecast Calibration — Stage 6.6

Stage 6.6 freezes case scenario probabilities prospectively and scores them only
after the stated horizon has ended.

## Forecast preregistration

A forecast requires:
- a valid `PROSPECTIVE_SHADOW_ONLY` permit for the exact Case Dossier;
- forecaster identity;
- forecast timestamp;
- future horizon end;
- the exact immutable Scenario Set.

The stored probabilities are the scenario set's central probabilities.

They cannot be edited in place. A changed scenario view requires a new
Scenario Set and a new Case Dossier / review / permit chain.

## Outcome adjudication

After the horizon ends, one predefined scenario may be classified as the
closest realized outcome.

Controls:
- adjudicator must differ from the original forecaster;
- classification notes are mandatory;
- evidence references are mandatory;
- outcome must be one of the frozen scenarios;
- only one outcome classification may exist per forecast.

This is intentionally strict. If no scenario can be responsibly selected, the
forecast should remain unresolved rather than forcing hindsight into the data.

## Scores

For resolved multiclass forecasts:
- multiclass Brier score is stored/calculated explicitly;
- log loss is calculated from the probability assigned to the realized scenario.

These are calibration diagnostics, not expected-return measures.

## Sample discipline

Forecaster calibration summaries are grouped by scenario count.

Below the configured minimum sample (default 20), the state is:

`INSUFFICIENT_EVIDENCE`

Only after the threshold is reached are mean Brier and mean log-loss values
reported with state:

`MEASURED`

Neither state authorizes capital or establishes market skill.
