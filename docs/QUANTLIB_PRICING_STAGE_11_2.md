# Stage 11.2 — QuantLib Pricing Adapter

Stage 11.2 introduces QuantLib behind the Stage 11.1 First Current contracts.

## Dependency

The adapter uses the official `QuantLib` Python distribution. First Current records the installed QuantLib package version in every PricingResult.

QuantLib objects are adapter implementation details; First Current MarketDataSnapshot, PricingInstrument, PricingModelSpecification and PricingRequest remain the source-of-truth contracts.

## Narrow instrument scope

The first adapter supports:

- equity spot mark-to-market;
- European vanilla options under analytic Black-Scholes-Merton.

Fixed-rate bonds and swaps remain unsupported until curve construction, calendar conventions and schedule semantics are specified independently. Unsupported instruments and measures fail closed.

## Black-Scholes-Merton contract

The BSM adapter requires explicit model parameters for risk-free quote key, dividend-yield quote key, volatility quote key, `ACT_365_FIXED` day count and `NULL_CALENDAR`.

It uses frozen point-in-time quotes from the exact bound MarketDataSnapshot. Missing or ambiguous quotes fail closed.

The initial curve contract is intentionally simple: flat continuously compounded risk-free and dividend curves plus constant Black volatility.

Supported option measures are NPV, Delta, Gamma, Vega and Theta.

## No implicit FX

The reporting currency must equal the instrument currency. Stage 11.2 refuses implicit FX conversion rather than silently assuming a spot or conversion convention.

## QuantLib global-state containment

QuantLib's evaluation date is global process state. Every option pricing call therefore holds a process-wide re-entrant lock, sets the exact evaluation calendar date, and restores the previous QuantLib evaluation date in a `finally` block.

This prevents one concurrent First Current request from intentionally sharing mutable QuantLib evaluation-date state with another.

## Independent differential test

The test suite calculates European call NPV, Delta, Gamma and Vega independently from the closed-form Black-Scholes equations and compares them with QuantLib to tight numerical tolerances.

This is not proof that every QuantLib model is correct; it is an initial differential-control pattern that later adapters must preserve.

## Result lineage

PricingResult binds request, instrument, snapshot, model specification, QuantLib version, evaluation date, exact quote IDs used, measure values and explicit diagnostics.

Pricing results remain `order_authority = NONE` and `capital_authority = NONE`.

## Next slice

Stage 11.3 should introduce explicit interest-rate curve construction and independent curve diagnostics before adding bond and swap pricing. Curves must preserve input quote lineage, interpolation/bootstrap choices, pillar dates, residual checks and monotonic/discount-factor sanity diagnostics.
