# Stage 7.2 — Linked Fundamental Schedules

Stage 7.2 adds the first deterministic one-period three-statement projection.

A projection is **not** a filed financial statement. Every projected line item
is explicitly marked `ESTIMATED` and carries derivation references to:

- the exact historical statement IDs;
- the exact assumption-set fingerprint;
- the schedule/driver names used.

## Historical gate

The engine accepts only a complete historical balance sheet, income statement
and cash-flow statement that pass the Stage 7.1 accounting validator.

It additionally requires the mapped operating components to reconcile to the
reported historical totals. This prevents a projection from silently dropping
unmapped assets, liabilities or equity.

## Assumption contract

Every assumption requires:

- a name;
- finite Decimal value;
- rationale;
- at least one evidence/reference identifier;
- explicit epistemic state: ESTIMATED / INFERRED / SPECULATIVE.

The current required assumptions are:

- revenue growth;
- gross margin;
- operating expense ratio;
- capex / revenue;
- depreciation / beginning PP&E;
- AR days;
- inventory days;
- AP days;
- new borrowing;
- debt repayment;
- interest rate;
- tax rate;
- dividend payout ratio.

Unknown assumptions fail closed rather than being ignored.

## Linked schedules

The engine explicitly links:

```text
Revenue
  ↓
Cost / gross profit / opex
  ↓
Operating income
  ↓
Debt schedule → interest
  ↓
Pretax income → tax → net income
  ↓
Retained earnings

Revenue + cost
  ↓
AR / inventory / AP
  ↓
Δ working capital
  ↓
CFO

Revenue
  ↓
Capex / depreciation
  ↓
PP&E
  ↓
CFI

Borrowing / repayment / dividends
  ↓
Debt + CFF

CFO + CFI + CFF
  ↓
Ending cash
  ↓
Projected balance sheet
```

## Projection invariants

Before a projection is considered valid it rechecks:

- gross-profit identity;
- operating-income identity;
- net-income identity;
- balance-sheet equation;
- cash-flow component sum;
- cash roll-forward;
- cash tie between balance sheet and cash-flow statement.

The projection receives a deterministic content-addressed ID derived from the
historical statement IDs, assumption-set fingerprint, period and schedule
outputs.

## Next slice

Stage 7.3 should extend the one-period engine into a multi-period model with:

- explicit year-to-year roll-forward provenance;
- assumption curves rather than one scalar per driver;
- debt maturities / interest by tranche;
- tax carryforwards;
- retained-earnings and equity bridge;
- model-run persistence and comparison;
- sensitivity-ready outputs for later valuation.
