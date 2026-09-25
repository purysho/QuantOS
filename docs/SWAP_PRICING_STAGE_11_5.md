# Stage 11.5 — Fixed/Floating Swap Pricing

Stage 11.5 adds the first fixed/floating interest-rate swap adapter on top of the frozen Stage 11.3 curves.

## Supported contract

The initial adapter supports a future-starting USD vanilla fixed/float swap with regular no-stub schedules, NULL_CALENDAR, UNADJUSTED dates, FORWARD date generation, end-of-month disabled and zero fixing days.

Fixed and floating accrual conventions are explicit and may be ACT_360 or ACT_365_FIXED.

Stage 11.5 deliberately does not infer historical or same-day fixings. A swap whose effective date is on or before the valuation date fails closed.

## Discount versus forwarding curves

The adapter requires two explicit curve roles: discount_curve and forwarding_curve. They are bound independently to the exact pricing snapshot and model specification.

The two roles may reference the same frozen curve in a single-curve fixture, but this must be explicit; the API never silently substitutes one curve for the other.

## Floating-rate semantics

The first floating index mode is PROJECTED_SIMPLE_FORWARD. Each floating coupon is projected from the forwarding curve over its exact accrual period:

`F = (P(start) / P(end) - 1) / accrual_fraction`

No benchmark-specific fixing, lookback, lockout, observation-shift or compounding convention is implied by this generic fixture.

## Independent reference pricer

QuantOS independently generates both schedules and cash flows.

The fixed leg is notional × fixed rate × accrual fraction. The floating leg is notional × (projected forward + spread) × accrual fraction. Each signed cash flow is discounted with the separately bound discount curve.

QuantLib VanillaSwap pricing must agree with this reference under a content-addressed SwapPricingValidationPolicy. Differential failure blocks the result.

## Fixed-leg DV01

Stage 11.5 defines the requested DV01 measure as signed fixed-leg DV01 only:

`DV01 = (signed fixed-leg NPV at -1 bp discount shift - signed fixed-leg NPV at +1 bp discount shift) / 2`

The forwarding curve is held fixed. This isolates discount sensitivity of the contractual fixed leg and avoids silently mixing forward and discount risk.

## Global-state containment

The adapter uses the shared process-wide QuantLib lock, temporarily sets the evaluation date and restores the prior global value in a finally block.

## Result lineage

SwapPricingResult preserves exact discount and forwarding curve IDs, schedule dates and fingerprints, independent fixed and floating reference cash flows, QuantLib leg NPVs, requested measures, differential errors, validation policy and engine version.

Results retain order_authority = NONE and capital_authority = NONE.

## Next slice

Stage 11.6 should move from single-instrument pricing into scenario revaluation: apply content-addressed RiskScenario shocks to frozen market snapshots, rebuild affected curves deterministically, reprice supported instruments, and persist scenario P&L with complete base-versus-shocked lineage before any portfolio VaR or ORE aggregation is introduced.
