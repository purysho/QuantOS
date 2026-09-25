# Stage 11 Complete — Pricing, Scenario Risk & Prospective Calibration

Stage 11 establishes First Current's independent pricing and risk baseline before any OpenSourceRisk/Engine integration.

## 11.1 — Pricing & risk contracts

- point-in-time MarketQuote and MarketDataSnapshot contracts;
- event time and knowledge time preserved separately;
- future-known quotes rejected;
- nested quote lineage and deterministic snapshot identities;
- typed equity, fixed-rate bond, European option and fixed/float swap instruments;
- explicit PricingModelSpecification and PricingRequest;
- content-addressed RiskScenario shocks;
- no implicit order or capital authority.

## 11.2 — Narrow QuantLib adapter

- QuantLib remains behind First Current domain contracts;
- equity spot mark-to-market;
- analytic European Black-Scholes-Merton;
- explicit market-quote mapping and model parameters;
- no implicit FX conversion;
- unsupported instruments/measures fail closed;
- independent closed-form option differential tests;
- QuantLib version and exact quote IDs preserved;
- process-wide evaluation-date lock and restoration.

## 11.3 — Frozen interest-rate curves

- exact point-in-time zero-rate inputs;
- ACT/365F and NULL_CALENDAR baseline;
- continuous compounding;
- log-linear discount-factor interpolation;
- positive discount-factor semantics including negative-rate environments;
- forward-rate sanity diagnostics;
- input repricing checks;
- QuantLib pillar cross-check;
- optional explicit extrapolation only.

## 11.4 — Fixed-rate bond pricing

- regular no-stub fixed-rate bond contract;
- exact curve binding;
- independent First Current cash-flow reference;
- QuantLib FixedRateBond comparison;
- NPV, clean/dirty price, accrued amount and parallel-zero-curve DV01;
- content-addressed differential validation policy;
- pricing result rejected when reference tolerance is breached.

## 11.5 — Fixed/floating swap pricing

- explicit fixed and floating frequencies/day-counts;
- future-starting USD vanilla swaps only;
- separate discount and forwarding curve roles;
- projected-simple-forward floating coupons;
- no inferred historical fixings;
- independent fixed/floating cash-flow reference;
- QuantLib VanillaSwap differential NPV validation;
- signed fixed-leg DV01 with forwarding curve held fixed;
- schedule fingerprints and leg-level lineage.

## 11.6 — Deterministic scenario revaluation

- every shock must match exactly one base quote;
- absolute and relative shock semantics;
- shocked snapshots retain original source lineage plus derivation lineage;
- equity, European option, bond and swap revaluation;
- affected curves rebuilt under the same frozen construction policies;
- base and shocked instrument prices independently validated;
- P&L = shocked NPV minus base NPV;
- scenarios remain deterministic stresses, not assigned probabilities.

## 11.7 — Portfolio risk cube

- signed positions × scenarios coverage matrix;
- missing cells produce INCOMPLETE, never partial portfolio totals;
- one common base snapshot and reporting currency required;
- net and gross base NPV;
- explicit NPV-based concentration;
- signed scenario P&L and largest gain/loss contributors;
- finite-difference ratio only for single-shock scenarios;
- multi-shock factor attribution is not invented;
- var_authority remains NONE.

## Risk chronology hardening

- every scenario revaluation carries the exact timezone-aware valuation timestamp;
- the portfolio risk cube requires one common valuation timestamp;
- valuation time is included in immutable risk identities;
- distributional risk cannot be detached from its as-of time.

## 11.8 — Historical-simulation VaR / expected shortfall

- COMPLETE risk cube required;
- one explicit HistoricalScenarioObservation per scenario;
- unique point-in-time historical periods and derivation evidence;
- exact frozen horizon and historical window;
- structural minimum of 20 observations;
- equal-weight empirical historical simulation only;
- nearest-rank VaR;
- expected shortfall = mean of worst ceil((1-alpha) × N) observed losses;
- negative empirical VaR is preserved rather than floored;
- no scaling, weighting decay, parametric distribution or tail extrapolation;
- var_authority = RESEARCH_ONLY.

## 11.9 — Prospective VaR backtesting

- every realized P&L is bound to one exact frozen risk estimate and cube;
- realized period starts at forecast valuation time and matches forecast horizon;
- forecast-before-outcome chronology enforced;
- VaR exception = realized loss strictly greater than frozen VaR;
- exception magnitude and descriptive ES exceedance recorded;
- prospective exception history;
- Kupiec unconditional-coverage LR_uc and p-value;
- short histories remain INSUFFICIENT_EVIDENCE;
- statistical non-rejection is WITHIN_TEST_TOLERANCE, not approval;
- no regulatory traffic-light inference.

## 11.10 — Exception independence & two-person risk review

- only contiguous realized periods form exception transitions;
- gaps split sequences instead of fabricating observations;
- Christoffersen first-order exception-independence LR_ind;
- combined conditional-coverage LR_cc = LR_uc + LR_ind;
- explicit transition counts n00 / n01 / n10 / n11;
- two-person reviewer + independent challenger;
- deterministic stress, concentration, current VaR/ES and prospective calibration combined in one dossier;
- unresolved challenger objections force REVIEW_REQUIRED;
- review states are INSUFFICIENT_EVIDENCE, WITHIN_POLICY or REVIEW_REQUIRED;
- approval_authority, live_authority, order_authority and capital_authority all remain NONE.

## Stage 11 safety invariant

> The OS may price supported instruments, rebuild frozen curves, run deterministic stresses, aggregate portfolio scenario P&L, estimate historical empirical VaR/ES, and test prospective exception behavior — but none of those artifacts can authorize a live trade or real-capital exposure.

## Why ORE comes after this

First Current now has an independently specified baseline against which OpenSourceRisk/Engine can be compared. ORE should be integrated as a separately versioned adapter, not as the system's source of truth.

Before an ORE result is trusted, the adapter should preserve exact First Current input lineage, engine/build provenance and differential comparisons for supported deterministic stress and distributional-risk fixtures.

## Current gate

At Stage 11 completion, the full CI matrix is green on Python 3.11, 3.12 and 3.13.