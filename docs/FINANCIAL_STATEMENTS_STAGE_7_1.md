# Stage 7.1 — Standardized Financial Statements

Stage 7 begins the Fundamental Engine below valuation.

The first slice establishes an immutable, point-in-time financial-statement
domain model. No DCF, comps, SOTP or LBO model may be trusted until these
accounting controls pass.

## Domain model

Each statement records:

- entity identity;
- statement type: balance sheet / income statement / cash flow;
- currency;
- fiscal period and exact period dates;
- filing accession and form;
- filing date;
- SEC/provider acceptance timestamp;
- **system knowledge timestamp**;
- immutable source-artifact SHA-256 IDs;
- standardized line items;
- exact line-item source locators;
- optional trusted ClaimCard IDs;
- deterministic content-addressed statement ID.

Balance sheets use instant periods. Income and cash-flow statements use duration
periods.

## Point-in-time rule

`knowledge_time` must not precede the source's acceptance time.

The append-only statement store exposes an `as_of` query that returns only
statements known to the system by the requested timestamp.

## Accounting invariants

### Balance sheet

```text
total_assets = total_liabilities + total_equity
```

### Cash flow

```text
net_change_in_cash
  = cash_from_operating_activities
  + cash_from_investing_activities
  + cash_from_financing_activities

ending_cash = beginning_cash + net_change_in_cash
```

### Income statement

When the relevant standardized components are present:

```text
gross_profit = revenue - cost_of_revenue
operating_income = gross_profit - operating_expenses
net_income = pretax_income - income_tax_expense
```

### Cross-statement

A complete three-statement set must use the same:
- entity;
- currency;
- period end;
- filing accession.

It must also satisfy:

```text
balance_sheet.cash_and_cash_equivalents = cash_flow.ending_cash
```

## Fail-closed behavior

Validation reports contain typed issues. Any accounting/provenance error makes
the report invalid, and `require_valid()` raises
`AccountingValidationError`.

The valuation layer should consume only validated statement sets.

## Next slice

Stage 7.2 should add explicit schedules and roll-forwards:

1. revenue;
2. working capital;
3. PP&E / depreciation;
4. debt / interest;
5. tax;
6. retained earnings / equity;
7. cash sweep.

Those schedules should reconcile into the standardized statements rather than
being allowed to bypass them.
