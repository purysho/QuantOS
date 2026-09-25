# Stage 13.4 — Point-in-Time Adjusted and Total-Return Research Panels

`ResearchReturnPanelBuilder` connects the new data foundations to the research lab and portfolio engine. It takes:

- raw, unadjusted `SessionCloseObservation`s, each with a knowledge time and source facts;
- the Stage 13.3 session calendar;
- Stage 13.2 corporate actions;
- the Stage 13.1 Security Master;
- a decision time.

It produces a content-addressed `ResearchReturnPanel`.

## Point-in-time rules

- Only closes and actions known at or before the decision time are used. When a close has been revised, the latest version known at the decision time wins. Two different closes with the same knowledge time raise an error.
- A close cannot be known before its session closed.
- A close on a non-session day is rejected.
- The panel bounds must be sessions, and the last session must close before the decision time.
- Every security must exist in the Security Master at the decision time.

## Completeness (no forward fill)

Every session in a security's life, from its first close to its terminal ex date or the end of the panel, must have a close. A gap, an unresolved corporate action, or too few returns makes the security `INCOMPLETE`, and the exact reason is recorded. Unresolved actions include a missing spin-off child price, a missing acquirer price and missing delisting proceeds.

`portfolio_observations(securities)` returns observations only for complete securities whose histories are synchronous. A delisted security therefore has to be handled by choosing a window explicitly; it is never padded.

## Outputs

- **`PortfolioReturnObservation`s**, ready for `PortfolioDatasetBuilder`. Each covers one session close to the next in UTC, from the calendar. Its knowledge time is the latest of the session close, the close facts and the action knowledge times. Its source facts are the close fact IDs plus the corporate-action event IDs.
- **Total returns** that include splits, stock dividends, cash dividends, spin-off value, and terminal proceeds for cash and stock mergers. A security that stopped trading before its terminal ex date gets its terminal return from the proceeds.
- **Backward-adjusted close series**, total-return or split-only, each bound to its adjustment-factor series ID. These are for factor and signal research.
