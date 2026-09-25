# Stage 7.4 — Advanced Financing, Tax and Equity Schedules

Stage 7.4 adds explicit schedule primitives that must be available before
valuation and LBO work can be credible.

## Debt tranches

Each tranche records:
- opening balance;
- fixed or floating rate type;
- contractual maturity;
- scheduled repayment;
- new borrowing;
- fixed coupon or floating spread;
- evidence references.

The schedule computes:
- effective rate;
- mandatory repayment;
- ending debt before cash sweep;
- interest expense.

A tranche maturing inside the modeled period is fully repaid rather than being
silently carried past maturity.

## Tax / NOL schedule

The tax schedule separates:
- pretax income;
- opening NOL;
- NOL generated;
- NOL utilized;
- utilization limit;
- taxable income;
- current tax expense;
- ending NOL.

Loss years create NOLs rather than negative current cash tax.

This slice intentionally does not yet model deferred-tax assets/valuation
allowances.

## Minimum cash / cash sweep

A cash-sweep policy records:
- minimum cash;
- percentage of excess cash to sweep;
- repayment priority by tranche.

The engine:
- never sweeps cash below the minimum;
- cannot repay more than remaining debt;
- preserves the exact allocation by tranche.

## Share count and dilution

The share schedule records:
- opening basic shares;
- issuances and issue price;
- repurchases and repurchase price;
- options and strike;
- average market price;
- restricted units.

It computes:
- ending basic shares;
- treasury-stock-method incremental option shares;
- diluted shares;
- issuance cash inflow;
- repurchase cash outflow;
- net equity financing cash flow.

## Determinism

Every schedule result is content-addressed from its exact inputs and outputs.

## Next slice

Stage 7.5 should integrate these schedule primitives into the multi-period
three-statement model so that:
- tranche interest replaces aggregate debt-rate assumptions;
- mandatory maturities and cash sweeps change debt and financing cash flow;
- NOLs drive modeled current tax;
- share issuance/repurchases feed cash, equity and diluted share count.

After that integration is green, the Fundamental Engine can be frozen as the
pre-valuation baseline.
