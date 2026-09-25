# Stage 7.5 — Integrated Advanced Fundamental Model

Stage 7.5 connects the advanced financing, tax, cash-sweep and dilution
schedules directly into the multi-period three-statement model.

The simple Stage 7.2/7.3 path remains intact for auditability. This advanced
path is separate and stricter.

## Mandatory roll-forwards

### Debt

First-period tranche opening balances must reconcile exactly to historical
`total_debt`.

For later periods:
- every outstanding prior tranche must be carried forward;
- its opening balance must equal the prior post-sweep ending balance;
- a new tranche must begin at zero and enter through explicit `new_borrowing`.

### Tax

Later-period opening NOL must equal the prior period's ending NOL.

### Shares

Later-period opening basic shares must equal the prior period's ending basic
shares.

These rules prevent modelers from silently resetting financing state between
forecast years.

## Integrated calculation order

```text
operating assumptions
        ↓
revenue / margins / working capital / PP&E
        ↓
debt-tranche schedule
        ↓
interest expense
        ↓
pretax income
        ↓
NOL / current-tax schedule
        ↓
net income / retained earnings
        ↓
share issuance / repurchase / dilution schedule
        ↓
CFO + CFI + pre-sweep CFF
        ↓
minimum-cash / debt-sweep schedule
        ↓
final debt + final cash
        ↓
three statements
        ↓
accounting reconciliation
```

## Timing convention

Current-period interest is calculated on the debt schedule before the
end-of-period cash sweep. The sweep is treated as an end-of-period financing
action.

This convention is explicit and should later become configurable if
intra-period timing precision is required.

## Equity treatment

Issuance and repurchase cash flows change both:
- financing cash flow; and
- modeled `other_equity`.

Net income less dividends rolls through retained earnings separately.

Diluted share count is retained as a projected balance-sheet/model metric for
later per-share valuation.

## Provenance

Every projected line item cites:
- exact historical statement IDs;
- parent projection ID for roll-forward periods;
- operating assumption-set ID;
- debt schedule ID;
- tax schedule ID;
- cash-sweep schedule ID;
- share schedule ID.

The advanced projection and model-run IDs are content addressed over those
inputs and outputs.

## Fail-closed gates

The advanced model rejects:
- historical debt not reconciled to opening tranches;
- missing outstanding debt in the next period;
- reset or mismatched tranche balances;
- reset NOL balances;
- reset basic share counts;
- non-contiguous periods;
- accounting statements that no longer balance.

## Next step

Once Stage 7.5 is green, Stage 7 can be frozen as the **Fundamental Engine
pre-valuation baseline**. Stage 8 can then add valuation models that consume
only validated, provenance-complete historical and projected financials.
