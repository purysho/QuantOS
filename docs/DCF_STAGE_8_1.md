# Stage 8.1 — FCFF DCF and Reverse DCF

Stage 8 begins valuation only after the v0.8.0 Fundamental Engine boundary.

The DCF engine refuses an invalid model run and fingerprints the exact model
run used.

## FCFF

For each projected period:

```text
NOPAT = Operating Income × (1 - unlevered cash tax rate)

FCFF = NOPAT
     + Depreciation
     - CapEx
     - Change in Net Working Capital
```

The unlevered cash-tax rate is an explicit valuation assumption per projection.
The engine does not reuse a levered tax schedule implicitly.

## Discount timing

Each projection requires an explicit integer number of end-of-period discount
years.

No default timing is inferred.

A later stage may add an explicit mid-year convention; it should not be
silently substituted here.

## Terminal value

Stage 8.1 supports a perpetual-growth terminal value:

```text
TV = FCFF_n × (1 + g) / (WACC - g)
```

The engine rejects:
- WACC <= 0;
- WACC >= 100%;
- terminal growth <= -100%;
- terminal growth >= WACC.

It reports the present-value terminal contribution as a share of enterprise
value so terminal-value dependence remains visible.

## Equity bridge

Enterprise value becomes equity value through an explicit, evidence-backed
bridge:

```text
Equity Value
  = Enterprise Value
  + Cash
  + Non-operating Investments
  - Debt
  - Preferred Equity
  - Noncontrolling Interest
```

Per-share value uses an explicit diluted-share input.

The bridge has its own as-of timestamp and evidence references; it is not
silently taken from a future forecast year.

## Reverse DCF

The first reverse-DCF operation holds the explicit forecast and WACC fixed and
solves the perpetual-growth rate implied by a supplied market price.

It returns one of:
- `SOLVED`;
- `MARKET_EV_BELOW_EXPLICIT_PV`;
- `OUTSIDE_PERPETUITY_DOMAIN`.

If the market enterprise value is already below the present value of explicit
FCFF, the engine does not invent a terminal growth rate.

## Identity / provenance

A DCF valuation ID is content addressed over:
- exact model-run ID;
- exact WACC assumption;
- exact terminal-growth assumption;
- exact per-period unlevered tax assumptions;
- exact discount timing;
- exact equity bridge;
- resulting enterprise / equity / per-share values.

A reverse-DCF identity additionally includes the exact market-price evidence.

## What Stage 8.1 does not claim

- DCF output is not an investment recommendation;
- WACC is an assumption, not an observed fact;
- terminal growth is not a forecast certainty;
- accounting consistency does not guarantee forecast accuracy;
- a solved reverse DCF does not prove the market is right or wrong.

## Next slices

Recommended next valuation work:
1. WACC / terminal-growth sensitivity grids;
2. explicit-growth reverse DCF;
3. comparable-company framework with reproducible peer selection;
4. SOTP;
5. LBO;
6. valuation triangulation.
