# Stage 8.2 — Valuation Methodology Gate

Stage 8.1 added a technically explicit FCFF DCF and reverse DCF. Stage 8.2
closes a different failure mode: **correct arithmetic applied to the wrong
valuation framework**.

## Business profile first

A valuation profile records:
- business archetype;
- cash-flow visibility;
- FCFF / FCFE sign;
- dividend relevance;
- regulatory-capital importance;
- segment disclosure and economic divergence;
- peer-set availability;
- distress / going-concern uncertainty;
- leverage capacity;
- evidence references;
- as-of timestamp.

## Method assessments

The engine assesses:
- FCFF DCF;
- FCFE DCF;
- dividend discount;
- residual income;
- trading comps;
- transaction comps;
- SOTP;
- probability-weighted DCF;
- NAV;
- liquidation value;
- replacement value;
- LBO.

Every method is classified as:
- `APPROPRIATE`;
- `CONDITIONAL`;
- `INAPPROPRIATE`.

There is no numeric "best method" score.

## Professional model-choice examples

- Banks / insurers: enterprise FCFF is blocked when regulatory capital and
  funding are operating constraints; FCFE/residual-income logic remains
  available.
- Pre-revenue biotech: plain FCFF is conditional on explicit binary-outcome
  modeling; probability-weighted DCF is structurally compatible.
- Conglomerates: SOTP is appropriate only when segment economics are
  meaningfully divergent and disclosed.
- Distress: liquidation value becomes explicit; going-concern DCF requires
  evidence.

## Method permit

A downstream valuation requires a content-addressed `MethodPermit`.

The permit is bound to the exact methodology-assessment fingerprint, not merely
to a company name. Conditional methods require evidence for every named
condition.

If the profile or methodology assessment changes, an old permit becomes stale.

## DCF integration

Both forward FCFF DCF and reverse DCF now require:
- the current methodology assessment;
- an exact `FCFF_DCF` permit.

The valuation identity includes the permit ID.

This means a valid financial model and valid DCF assumptions are still
insufficient if FCFF DCF is not methodologically authorized for the current
business profile.

## Next slice

Stage 8.3 should add WACC / terminal-growth sensitivity grids and explicit
terminal-value dependence flags, then proceed to reproducible comparable-company
analysis.
