# Stage 11.7 — Portfolio Risk Cube

Stage 11.7 aggregates deterministic Stage 11.6 instrument revaluations into an explicit positions-by-scenarios risk cube.

## Positions

PortfolioRiskPosition binds one exact instrument ID to a signed finite non-zero quantity, book ID and optional label. Negative quantity represents a short position.

Instrument pricing contracts already contain economic notional or multiplier where applicable; position quantity scales the priced instrument result.

## Coverage matrix

Every position/scenario pair has a RiskCoverageCell.

A complete cube requires exactly one unambiguous ScenarioRevaluationResult for every instrument/scenario cell. Missing cells are persisted as explicit uncovered cells.

When any cell for a scenario is missing, that scenario's portfolio P&L is `None`. First Current does not sum the covered subset and present it as a portfolio result.

Unexpected instruments, unexpected scenarios, duplicate revaluation identities, ambiguous instrument/scenario cells, mixed base snapshots and mixed reporting currencies all fail closed.

## Base values and concentration

Position base value is signed quantity × instrument base NPV.

Net base value is the signed sum. Gross base value is the sum of absolute signed base NPVs.

Base-value concentration is each position's absolute base NPV divided by gross base NPV. The cube explicitly labels this as NPV concentration: it is not notional, delta, economic exposure or regulatory exposure.

## Scenario summaries

For complete scenarios the cube records signed portfolio scenario P&L, P&L divided by gross base NPV, largest losing position and largest gaining position.

Short positions naturally reverse the sign of instrument scenario P&L through signed quantity.

## Finite-difference scenario sensitivity

For a complete scenario containing exactly one market shock, the cube records portfolio P&L divided by the shock magnitude.

This is a finite-difference scenario ratio, not an analytic Greek. Multi-shock scenarios are deliberately not attributed to individual factors because that would invent an allocation of joint P&L.

## Authority boundary

The cube records var_authority = NONE, order_authority = NONE and capital_authority = NONE.

Stage 11.7 does not compute VaR or expected shortfall and does not assign probabilities to deterministic stresses.

## Next slice

Stage 11.8 should introduce a controlled distributional-risk layer only after defining scenario-return history, observation windows, weighting, missing-data semantics and backtesting requirements. ORE integration should remain behind a compatibility boundary and be differential-tested against First Current deterministic stress aggregation before its VaR or XVA outputs are trusted.
