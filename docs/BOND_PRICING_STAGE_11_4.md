# Stage 11.4 — Fixed-Rate Bond Pricing

Stage 11.4 prices a deliberately narrow fixed-rate bond contract from the frozen Stage 11.3 discount curve and requires QuantLib to agree with an independent QuantOS cash-flow reference.

## Supported contract

The first adapter supports only fixed-rate bonds with ACT_365_FIXED accrual, NULL_CALENDAR, UNADJUSTED payment dates, FORWARD date generation, end-of-month disabled, 100 percent redemption and a regular no-stub coupon schedule.

Unsupported conventions fail closed. This avoids pretending that schedule generation, market calendars and accrual conventions are interchangeable.

## Exact curve binding

The pricing request, instrument, market snapshot, pricing-model specification and DiscountCurveArtifact must all agree. The curve must be content-authentic, QuantLib-verified, in the bond currency and built from the exact pricing snapshot.

A bond whose maturity exceeds a non-extrapolating curve is rejected.

## Independent reference pricer

QuantOS generates the regular coupon schedule itself, calculates ACT/365F coupon cash flows and redemption, discounts post-settlement cash flows with the frozen curve and independently calculates:

- NPV at the curve valuation date;
- dirty price per 100 at settlement;
- accrued amount per 100;
- clean price per 100;
- DV01.

QuantLib uses FixedRateBond plus DiscountingBondEngine from the same frozen curve. Every value is compared against the independent reference under a content-addressed BondPricingValidationPolicy.

A differential breach fails closed instead of returning a result with a warning.

## DV01

Stage 11.4 defines DV01 as the central change in currency NPV for a parallel plus/minus one-basis-point shift to the continuously compounded zero curve:

`DV01 = (NPV at -1 bp - NPV at +1 bp) / 2`

The exact one-basis-point bump is frozen in the model specification. Both QuantLib and the independent reference use the same economic definition but separate calculations.

## QuantLib global-state boundary

All QuantLib adapters now share one process-wide lock. The bond adapter sets the global evaluation date only inside that lock and restores the prior value in a finally block.

## Result lineage

BondPricingResult records the exact curve ID, schedule dates and fingerprint, reference cash flows, requested QuantLib measures, reference values, every absolute differential, QuantLib version and diagnostics.

Results keep order_authority = NONE and capital_authority = NONE.

## Next slice

Stage 11.5 should add a fixed/floating swap adapter. It should separate discount and forwarding curves even when a single-curve fixture is used initially, preserve fixing assumptions, and differential-test NPV and fixed-leg DV01 against a QuantOS cash-flow reference before scenario aggregation.
