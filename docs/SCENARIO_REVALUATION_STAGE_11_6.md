# Stage 11.6 — Deterministic Scenario Revaluation

Stage 11.6 applies explicit RiskScenario shocks to an exact frozen point-in-time market snapshot and reprices supported instruments on a fully derived shocked state.

## Shock application

Every shock must match exactly one quote by quote type, market key, tenor and optional currency. Missing or ambiguous targets fail closed.

ABSOLUTE shocks add the shock value. RELATIVE shocks multiply the base value by one plus the shock value.

The derived quote retains the original event and knowledge times and carries both original source lineage and a deterministic scenario-derivation lineage marker. The standard MarketQuote validation is rerun, so an economically invalid shocked quote cannot bypass contract checks.

## Derived market state

ScenarioMarketState binds the scenario ID, exact base snapshot, exact shocked snapshot and each base-to-shocked quote transformation. Its identity is independently recomputable.

## Repricing

Stage 11.6 supports scenario revaluation for:

- equity spot mark-to-market;
- European vanilla option;
- fixed-rate bond;
- future-starting fixed/float swap.

Each shocked pricing request is rebuilt from the original instrument, model, measure set and reporting currency but bound to the derived shocked snapshot.

NPV must be present in the base request. Scenario P&L is shocked NPV minus base NPV.

## Curve rebuilding

For bonds, the affected discount curve is rebuilt from the shocked snapshot under the exact frozen CurveConstructionPolicy.

For swaps, discount and forwarding curves are rebuilt independently under their exact frozen policies. Even when only one curve is shocked, both roles remain explicitly bound to their derived-snapshot artifacts.

Base and shocked bond/swap prices must each pass their existing independent differential validation gates.

## Lineage

ScenarioRevaluationResult preserves scenario market-state identity, base and shocked request IDs, base and shocked pricing-result IDs, base and shocked curve IDs, exact quote transformations, base NPV, shocked NPV and scenario P&L.

Results retain order_authority = NONE and capital_authority = NONE.

## What Stage 11.6 is not

This is deterministic scenario revaluation, not VaR, expected shortfall, probability assignment or stress-scenario likelihood. No scenario is described as predictive merely because it can be repriced.

## Next slice

Stage 11.7 should aggregate content-addressed instrument revaluations into a portfolio risk cube with explicit positions, currencies, scenario coverage, base market value, scenario P&L, concentration and sensitivity summaries. Missing instrument coverage must remain fail-visible before introducing ORE or distributional VaR.
