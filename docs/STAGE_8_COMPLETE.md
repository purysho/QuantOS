# Stage 8 Complete — Valuation Engine v0.9.0

Stage 8 adds professional valuation capability on top of the validated v0.8.0 Fundamental Engine.

The phase closes at a strict boundary: the OS can produce auditable valuation evidence through multiple frameworks and show where those frameworks agree or disagree, but it **does not collapse them into a recommendation or capital action**.

## 8.1 — FCFF DCF and reverse DCF

The DCF engine is pinned to an exact validated fundamental-model run.

It derives:

```text
NOPAT = Operating Income × (1 - unlevered cash-tax rate)

FCFF = NOPAT
     + Depreciation
     - CapEx
     - Change in Net Working Capital
```

Controls include:
- explicit per-period unlevered tax assumptions;
- explicit discount timing;
- evidence-backed WACC and terminal growth;
- terminal growth strictly below WACC;
- explicit enterprise-to-equity bridge;
- diluted share count;
- terminal-value contribution visibility;
- reverse DCF for market-implied terminal growth.

## 8.2 — Valuation methodology gate

The OS now distinguishes model correctness from model appropriateness.

Methods are assessed as:
- `APPROPRIATE`;
- `CONDITIONAL`;
- `INAPPROPRIATE`.

A content-addressed MethodPermit is required by downstream valuation engines.

Conditional methods require evidence for each named condition. Stale permits fail when the methodology-assessment fingerprint changes.

## 8.3 — DCF sensitivity

Sensitivity grids preserve the exact:
- base valuation;
- fundamental model run;
- FCFF method permit;
- WACC axis;
- terminal-growth axis;
- diagnostic policy.

Cells with terminal growth greater than or equal to WACC remain visible as invalid rather than being omitted or coerced.

Diagnostic thresholds are explicit policy inputs, not hidden constants.

## 8.4 — Comparable companies

Peer selection and valuation are separate steps.

Point-in-time peer selection records:
- exact candidate snapshots;
- inclusion/exclusion decision for every candidate;
- industry/geography/business-model requirements;
- size bounds;
- distress exclusion;
- minimum peer count.

Future-known data and duplicate entities are excluded.

The valuation engine preserves peer-level multiples and percentile distributions rather than selecting one undocumented multiple.

## 8.5 — Reviewed normalization and sector contracts

Raw peer facts remain immutable.

Normalization adjustments are separate, evidenced records with:
- signed amount;
- metric;
- adjustment kind;
- knowledge time;
- rationale;
- evidence;
- preparer.

The reviewer must differ from the preparer.

SectorMetricContract restricts the multiple families that may be used for an industry.

## 8.6 — SOTP

SOTP requires:
- a company-level SOTP permit;
- a separate methodology assessment and permit for every segment;
- one common as-of date;
- explicit segment ownership;
- explicit EV-to-equity bridge where needed;
- explicit corporate costs and intercompany eliminations.

Segment plus corporate cash/debt allocations must reconcile to reported group totals.

## 8.7 — LBO

The LBO engine consumes a validated operating model run but defines sponsor financing separately.

It includes:
- sources & uses;
- sponsor equity residual;
- fixed/floating debt tranches;
- amortization and maturities;
- sponsor interest;
- cash-tax/NOL handling;
- minimum cash;
- debt sweep;
- exit multiple;
- MOIC and IRR.

Mandatory debt service that breaches minimum cash fails closed.

Operating downside cases are separate model runs. The engine does not hide operating haircuts inside one LBO case.

## 8.8 — Non-averaging triangulation

Comparable per-share valuation evidence is grouped by method family:
- DCF;
- trading comps;
- SOTP.

Multiple trading-comps metrics remain one method family.

The triangulation engine reports:
- common overlap;
- partial overlap;
- disjoint method envelopes;
- union range;
- cross-method central-value dispersion;
- explicit flags.

It intentionally has **no weighted fair-value field**.

LBO is retained as a non-comparable sponsor-return cross-check and is not averaged into per-share value.

## Valuation trust chain

```text
validated point-in-time financial model
            ↓
business / valuation profile
            ↓
methodology assessment
            ↓
method-specific permit
            ↓
valuation engine
            ↓
method-specific diagnostics
            ↓
non-averaging triangulation
            ↓
professional review / research use only

            ╳
      no capital authority
            ╳
```

## What v0.9.0 does not claim

- a permitted method is not necessarily accurate;
- a DCF is not intrinsic truth;
- a peer median is not fair value;
- SOTP does not eliminate allocation judgment;
- LBO returns depend on transaction financing and exit assumptions;
- cross-method overlap does not prove correctness;
- cross-method disagreement does not prove mispricing;
- no valuation output authorizes a live order.

## Next phase

Recommended Stage 9: **Quant Research Lab**.

Priority sequence:
1. point-in-time investable universe construction;
2. factor/signal specification and versioning;
3. walk-forward and purged validation;
4. explicit transaction-cost / turnover / borrow assumptions;
5. multiple-testing controls;
6. Probability of Backtest Overfitting;
7. Deflated Sharpe Ratio;
8. immutable experiment ledger / model registry integration;
9. prospective shadow promotion gates.

The existing capital firewall remains unchanged.
