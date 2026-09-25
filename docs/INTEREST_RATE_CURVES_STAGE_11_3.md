# Stage 11.3 — Interest-Rate Curve Construction

Stage 11.3 adds frozen interest-rate curve artifacts before any bond or swap pricing.

## Initial curve contract

The first curve builder accepts point-in-time continuous ZERO_RATE quotes for one explicit curve key and currency.

The policy freezes minimum pillars, zero-rate and implied-forward sanity bounds, repricing tolerance, extrapolation behavior, `ACT_365_FIXED`, `LOG_LINEAR_DISCOUNT`, continuous compounding and `NULL_CALENDAR`.

Unsupported conventions fail closed rather than being silently mapped.

## Negative-rate correctness

Discount factors are required to be strictly positive but are not capped at 1. A discount factor above 1 is valid when the relevant continuously compounded rate is negative.

Stage 11.3 therefore does not impose monotonic-decreasing discount factors as a universal invariant.

## Pillar lineage

Every pillar records tenor, exact pillar date, ACT/365F year fraction, source zero rate, derived discount factor, input quote ID and zero-rate repricing residual.

The curve artifact binds the exact MarketDataSnapshot, policy and ordered input quote IDs.

## Diagnostics

The builder checks:

- minimum pillar count;
- unique tenors and pillar dates;
- zero-rate sanity bounds;
- positive finite discount factors;
- finite adjacent continuously compounded forward rates;
- maximum absolute forward-rate policy;
- maximum zero-rate repricing residual.

## QuantLib verification

QuantOS constructs its own frozen pillar artifact first. It then constructs a QuantLib DiscountCurve from the same dates and discount factors and verifies every pillar to the frozen tolerance.

QuantLib is therefore a checked adapter, not the owner of curve lineage.

## Interpolation

Curve queries use log-linear interpolation on discount factors. Extrapolation is disabled by default and fails closed unless explicitly allowed by the frozen policy.

## Authority boundary

DiscountCurveArtifact carries order_authority = NONE and capital_authority = NONE.

## Next slice

Stage 11.4 should use these frozen curve artifacts for fixed-rate bond pricing, then compare QuantLib clean/dirty price and DV01 against an independent cash-flow reference pricer before adding swaps.
