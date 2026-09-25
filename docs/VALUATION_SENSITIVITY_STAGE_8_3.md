# Stage 8.3 — DCF Sensitivity & Terminal-Dependence Diagnostics

Stage 8.3 makes DCF fragility visible without creating a new confidence score.

## Explicit sensitivity grid

The grid re-runs the exact permitted FCFF DCF over explicit:
- WACC values;
- terminal-growth values.

Every valid cell retains its own valuation identity.

Cells where:

```text
terminal growth >= WACC
```

are not silently dropped and are not coerced. They remain visible as
`INVALID_PERPETUITY_DOMAIN`.

## Policy-bound diagnostics

Thresholds are not hidden constants.

A `SensitivityPolicy` explicitly records:
- terminal-value watch threshold;
- terminal-value critical threshold;
- minimum acceptable WACC-growth spread;
- per-share sensitivity-span threshold;
- rationale;
- evidence references.

The policy itself is content-addressed.

## Diagnostic flags

The current layer can surface:
- `TERMINAL_VALUE_WATCH`;
- `TERMINAL_VALUE_CRITICAL`;
- `INVALID_PERPETUITY_CELLS`;
- `NARROW_WACC_GROWTH_SPREAD`;
- `WIDE_PER_SHARE_SENSITIVITY`;
- `BASE_PER_SHARE_ZERO`.

These are review flags, not risk scores and not recommendations.

## Lineage

A sensitivity grid is bound to:
- exact base DCF valuation ID;
- exact fundamental model-run ID;
- exact FCFF method-permit ID;
- exact sensitivity policy;
- exact WACC axis;
- exact terminal-growth axis.

A stale methodology permit therefore cannot be used to generate a new grid.

## Next slice

Stage 8.4 should add comparable-company analysis with:
- reproducible candidate universe;
- explicit inclusion/exclusion rules;
- normalization policy;
- point-in-time market and fundamental inputs;
- peer-level provenance;
- multiple distributions rather than one cherry-picked number;
- enterprise-to-equity bridge and per-share output;
- no opaque peer-selection score.
