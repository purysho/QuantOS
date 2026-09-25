# Stage 8.8 — Non-Averaging Valuation Triangulation

Stage 8.8 compares valuation evidence without converting disagreement into a synthetic consensus.

## Comparable method families

The common comparison basis is equity value per diluted share.

Current families:
- DCF;
- trading comps;
- SOTP.

Trading-comps submetrics such as EV/Revenue and EV/EBITDA remain separate observations but are summarized as **one family**. They do not receive multiple votes merely because more multiples were calculated.

## Family envelopes

Each family is summarized by:
- low per-share value;
- central per-share value;
- high per-share value;
- exact underlying observation IDs.

The trading-comps family central value is the median of its metric-level central observations.

## Structural states

The engine reports:
- `INSUFFICIENT_METHOD_FAMILIES`;
- `COMMON_OVERLAP`;
- `PARTIAL_OVERLAP`;
- `DISJOINT`.

It also preserves the union of all family envelopes and, when defined, the exact common-overlap interval.

## Dispersion

Cross-method central-value dispersion is:

```text
(max family central - min family central)
-----------------------------------------
      |median family central|
```

The threshold for a wide-dispersion flag is an explicit, evidenced TriangulationPolicy input.

A zero median does not trigger a hidden epsilon; the ratio is left undefined and a dedicated flag is emitted.

## LBO treatment

LBO sponsor returns do not share the same economic unit as per-share intrinsic/relative valuation.

Therefore LBO is represented as a separate cross-check carrying:
- exact LBO valuation ID;
- exact operating model run;
- LBO method permit;
- status;
- MOIC;
- IRR.

It is never averaged into the per-share families.

## Adapters

Adapters verify exact upstream identities:
- DCF observation must match its DCF sensitivity grid, model-run ID and method permit;
- comps observations require explicit 50th-percentile points;
- SOTP becomes a point observation;
- LBO becomes a sponsor-return cross-check.

## Boundary

The result has no weighted fair value, target price, expected return, recommendation or capital action.

The purpose is to make agreement and disagreement inspectable, not to make the disagreement disappear.
