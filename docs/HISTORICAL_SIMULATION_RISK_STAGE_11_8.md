# Stage 11.8 — Controlled Historical-Simulation Risk

Stage 11.8 adds the first distributional risk estimate, but only after Stage 11.7 has produced a COMPLETE deterministic portfolio risk cube and every scenario has explicit historical-observation metadata.

## Historical observation contract

HistoricalScenarioObservation binds exactly one risk-cube scenario to a timezone-aware historical period, source fact IDs and explicit derivation evidence explaining how the historical market move became that scenario.

Observation periods must be unique, lie wholly inside the frozen policy window, end before the risk-cube valuation timestamp and have exactly the frozen horizon length.

Stage 11.8 does not infer business-day or exchange-session semantics from calendar timestamps. The policy freezes the exact elapsed horizon in seconds. More sophisticated trading-calendar horizons belong in a later adapter.

## Structural sample floor

The policy-level minimum observation count cannot be set below 20. This is a structural guard against trivially tiny empirical distributions, not a claim that 20 observations are adequate for production VaR.

Professional deployments should generally demand materially more history based on horizon, confidence level, regime coverage, data quality and governance.

## Frozen methodology

The first supported method is equal-weight historical simulation with a nearest-rank empirical quantile and FAIL_CLOSED missing-data policy.

For N observations and confidence alpha:

- loss = negative portfolio scenario P&L;
- VaR loss = nearest-rank alpha quantile of observed losses;
- tail size = max(1, ceil((1-alpha) × N));
- expected shortfall = arithmetic mean of the worst tail-size observed losses.

No interpolation, volatility scaling, decay weighting, distribution fitting or parametric tail extrapolation is performed.

Negative VaR is deliberately preserved if the empirical alpha quantile is still a gain. The engine does not floor VaR at zero because doing so would silently alter the observed distribution.

## Exact coverage

The historical-observation set must exactly match the risk-cube scenario set one-for-one. Extra, missing or duplicate scenario observations fail closed.

The source risk cube itself must be content-authentic and COMPLETE.

## Output

HistoricalSimulationRiskEstimate stores:

- exact cube and policy IDs;
- base snapshot and valuation time;
- every historical observation ID;
- the ordered empirical loss sample;
- VaR loss;
- expected-shortfall loss;
- worst, best and mean loss;
- VaR and ES divided by gross base NPV when a positive gross base exists;
- explicit methodology diagnostics.

## Authority boundary

The estimate records var_authority = RESEARCH_ONLY, order_authority = NONE and capital_authority = NONE.

Historical frequencies are backward-looking evidence. They are not forecasts, guarantees, capital requirements or permission to trade.

## Next slice

Stage 11.9 should add prospective VaR calibration/backtesting. Each frozen risk estimate must be paired with later realized portfolio P&L over the same horizon, with strict forecast-before-outcome chronology, exception counting, expected exception rate and immutable calibration history. No regulatory traffic-light label should be inferred until a specific regulatory methodology is deliberately implemented.
