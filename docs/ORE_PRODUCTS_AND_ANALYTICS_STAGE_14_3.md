# Stages 14.3–14.6 — Broader ORE Coverage: Bonds, Options, Sensitivity and Stress

Stage 14 compared ORE only on vanilla swap NPV and on Stage 11.6 scenarios repriced with rebuilt curves. These stages add two more products, and they use **ORE's own risk analytics**, not only its pricer. Every comparison:

- runs through a content-addressed `OREInputBundle`;
- runs ORE only in the isolated `quantos.ore_worker` process;
- keeps mismatches as `MISMATCH`, with no trust authority.

The worker now returns any requested ORE report (NPV, sensitivity, stress) as strings, so no precision is lost. The new bundle fields (trade ID, base currency, analytics list, sensitivity and stress XML) are left out of the identity when they have default values, so Stage 14.1 swap bundle IDs are unchanged.

## 14.3 Fixed-rate bond

`OREBondDifferential` maps a Stage 11.4 bond and its frozen curve to an ORE `Bond` trade. The trade has no credit curve, no security spread, the reference curve set to the QuantOS curve, NullCalendar and an Unadjusted Forward schedule. ORE is priced with `DiscountingRiskyBondEngine`.

ORE's NPV is compared with **both** the independent Stage 11.4 cash-flow reference and QuantLib.

Refused:

- an issue day above 28, because month-end rolling could differ between schedule generators;
- a cash flow between valuation and settlement date, which the reference excludes but ORE's valuation-date NPV includes;
- extrapolating curves;
- maturity beyond the last pillar;
- unverified or tampered Stage 11.4 results.

## 14.4 European equity option

`OREEuropeanOptionDifferential` takes a Stage 11.2 BSM `PricingResult` and its exact request, snapshot and model, and maps them to:

- an ORE `EquityOption`;
- a flat continuous risk-free curve (two identical pillars, so it is exactly flat);
- a flat dividend-yield equity curve;
- a single constant ATM vol quote at the expiry date.

The underlying's name is an opaque hash, so identifiers never break ORE's quote keys.

ORE is compared with **both** a new QuantOS closed-form Black-Scholes-Merton reference (`black_scholes_merton_npv`, calls and puts, × multiplier) and QuantLib.

## 14.5 ORE SENSITIVITY versus single-pillar revaluations

`ORESwapRiskAnalytics.sensitivity` runs ORE's own bucketed zero-rate sensitivity analytic, with 1bp absolute shifts on the discount and index curves.

ORE's simulation-market grid is set to **exactly** the QuantOS pillar tenors. The curves must have identical, tenor-aligned pillars, and otherwise the run is refused. With that grid, ORE's zero shift at a grid point is economically identical to QuantOS shocking the pillar quote and rebuilding the curve.

The caller supplies one Stage 11.6 revaluation per pillar, each a single absolute +1bp shock, covering every pillar of both curves exactly once. Each revaluation is re-verified, down to its rebuilt shocked curves. Each ORE `Delta` is then compared with the matching QuantOS scenario P&L.

## 14.6 ORE STRESS versus a Stage 11.6 scenario

`ORESwapRiskAnalytics.stress` converts a Stage 11.6 scenario's absolute zero-rate shocks into an ORE stress test:

- per-tenor shifts, with zero for unshocked pillars, on the discount and index curves;
- the same pillar-aligned simulation grid.

It runs ORE's STRESS analytic and compares the scenario P&L with the Stage 11.6 revaluation. Relative shocks, and shocks on other quotes, are refused.

## Results

With the `ore` extra on Python 3.11–3.13:

- bond NPV, call NPV and put NPV match inside a 1e-6 tolerance. Option differences against the closed form are below 1e-8.
- All six bucket deltas and the multi-tenor stress P&L match. Exploratory runs agreed to about 1e-10.

## Still not in scope

- Credit-risky bonds (credit curves, security spreads, recovery).
- American and Bermudan options.
- FX products.
- ORE VaR/ES, which is compared only where its semantics truly match the Stage 11.8 estimator.
- XVA.
