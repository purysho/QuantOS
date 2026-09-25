# Stage 7 Complete — Fundamental Engine v0.8.0

Stage 7 establishes the accounting and forecasting substrate that valuation
must consume.

The phase is complete at a deliberately strict boundary: **the system can build
and reconcile point-in-time historical and projected financial statements, but
it does not yet call those statements fair value.**

## 7.1 — Reported financial statements

The system now has an immutable financial-statement domain model for:
- balance sheets;
- income statements;
- cash-flow statements.

Each reported statement records:
- exact entity;
- currency;
- fiscal period;
- filing accession and form;
- filed date;
- provider acceptance timestamp;
- system knowledge timestamp;
- SHA-256 source artifacts;
- exact line-item locators;
- deterministic statement identity.

The point-in-time store prevents future-known statements from leaking into an
earlier as-of reconstruction.

### Accounting gates

Reported statements fail closed if:
- assets do not equal liabilities plus equity;
- cash-flow components do not reconcile;
- cash does not roll forward;
- available income-statement subtotals do not tie;
- a three-statement set mixes entities, currencies, filing accessions or period
  ends;
- balance-sheet cash and cash-flow ending cash disagree.

## 7.2 — Linked one-period projection

Projected lines are separate from reported facts and explicitly marked
`ESTIMATED`.

Every assumption records:
- exact value;
- rationale;
- evidence references;
- epistemic state.

The linked model covers:
- revenue;
- gross margin;
- operating expenses;
- working capital;
- PP&E / depreciation;
- debt / interest;
- tax;
- retained earnings;
- cash flow.

Projected statements must rebalance before the projection is valid.

## 7.3 — Multi-period lineage

Multi-period forecasts:
- require contiguous periods;
- roll opening financial state from the prior projection;
- preserve exact parent-projection lineage;
- fingerprint each period;
- fingerprint the complete model run;
- persist immutable run manifests;
- expose deterministic run-to-run metric deltas.

Changing an intermediate assumption changes that period and all descendants,
while leaving prior-period identities stable.

## 7.4 — Advanced schedules

The model now has explicit primitives for:

### Debt
- individual tranches;
- fixed / floating rates;
- spread and base-rate mechanics;
- maturities;
- scheduled repayments;
- new borrowing;
- interest expense.

### Tax
- opening NOL;
- NOL generation;
- utilization limits;
- taxable income;
- current cash tax;
- ending NOL.

### Cash sweep
- minimum cash;
- sweep percentage;
- tranche repayment priority;
- exact post-sweep balances.

### Equity / dilution
- basic shares;
- issuance;
- repurchases;
- issuance / repurchase cash;
- options and strikes;
- treasury-stock-method incremental shares;
- restricted units;
- diluted shares.

Every advanced schedule has a deterministic content-addressed identity.

## 7.5 — Integrated advanced model

The advanced path connects the schedules back into the three statements.

```text
operating schedules
      ↓
debt tranches → interest
      ↓
pretax income
      ↓
NOL / current tax
      ↓
net income / retained earnings
      ↓
share schedule / equity financing
      ↓
CFO + CFI + pre-sweep CFF
      ↓
minimum-cash debt sweep
      ↓
final debt + cash
      ↓
balanced statements
```

### Mandatory state continuity

The model rejects:
- opening debt tranches that do not reconcile to historical debt;
- outstanding debt omitted from a later year;
- later tranche balances reset away from prior post-sweep balances;
- new tranches with non-zero unexplained opening balances;
- NOL resets;
- basic-share resets;
- non-contiguous forecast periods.

An unfunded debt maturity that would breach minimum cash is rejected rather
than silently financed.

### Timing convention

Interest is calculated on the debt schedule before the end-of-period cash
sweep. The sweep is treated as an end-of-period financing action.

This convention is explicit and may later become configurable.

## What v0.8.0 does not claim

- accounting consistency does not prove forecast accuracy;
- model assumptions are not observed facts;
- scenario outputs are not probabilities unless separately specified and
  reviewed;
- a model-run comparison is not a valuation;
- projected diluted shares are not a price target;
- minimum-cash feasibility is not a liquidity guarantee;
- no Fundamental Engine output has live-capital authority.

## Stage 8 — Valuation

The next phase may now consume only validated, provenance-complete financials.

Recommended order:
1. FCFF / DCF with explicit WACC and terminal-value assumptions;
2. reverse DCF;
3. comparable-company framework with reproducible peer selection;
4. SOTP with method-by-segment;
5. LBO with sources & uses, tranche debt, cash sweep, MOIC and IRR;
6. valuation triangulation and assumption sensitivity.

Valuation outputs should preserve:
- exact financial-model run ID;
- exact assumptions;
- exact method;
- exact scenario;
- exact evidence references;
- explicit sensitivity rather than false precision.

The live-capital firewall remains unchanged.
