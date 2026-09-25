# Stage 8.7 — Sponsor-Return LBO Valuation

Stage 8.7 adds an LBO framework that consumes a validated operating model but keeps sponsor financing separate from the company's pre-deal capital structure.

## Methodology gate

The LBO engine requires a current LBO MethodPermit. The methodology layer therefore has to establish stable leverage capacity, sufficient cash-flow visibility, and evidence-backed transaction terms before sponsor returns can be calculated.

## Operating model vs acquisition financing

The exact fundamental model run supplies EBIT, depreciation, CapEx, and change in working capital. Sponsor debt is defined separately through explicit LBO debt terms.

This prevents the OS from treating the target's historical or forecast capital structure as if it were the acquisition financing package.

## Sources & uses

The engine calculates equity purchase price from entry enterprise value, target debt, target cash, and non-operating investments. Uses then include debt refinancing, transaction fees, and financing fees.

Sources consist of sponsor debt, target cash explicitly permitted for use at close, and sponsor equity as the balancing residual. Cash used at close cannot breach the minimum-cash requirement. Sources must equal uses exactly.

## Sponsor debt

Each tranche records initial draw, fixed or floating rate, amortization rate, maturity period, and evidence references.

Interest uses an explicit pre-sweep timing convention: current-period interest is calculated on average beginning and post-mandatory/pre-sweep debt. End-of-period cash sweeps do not reduce current-period interest.

## Cash tax and NOL

Each projection period has explicit cash-tax rate, NOL utilization limit, floating base rate, and cash-sweep percentage. Sponsor interest affects taxable income. Losses create NOLs and later profitable periods may consume them.

## Liquidity

Cash available for debt service is derived from the operating model after sponsor interest, cash tax, CapEx, and working-capital investment.

Mandatory amortization or maturity repayment that would breach minimum cash fails closed. After mandatory repayment, excess cash may be swept by explicit tranche priority and sweep percentage.

## Exit and sponsor returns

Exit enterprise value equals final-period EBITDA times an evidence-backed exit multiple. Sponsor exit equity equals exit enterprise value less ending debt plus ending cash and explicit non-operating investments.

When exit equity is positive, the engine reports MOIC and annualized IRR. A non-positive exit equity state is preserved explicitly as NEGATIVE_EXIT_EQUITY rather than forcing a meaningless IRR.

## Downside cases

Operating downside is represented by a separate validated fundamental model run. The LBO engine does not hide operating haircuts inside the sponsor-return calculation.

LBOCaseComparison preserves named cases and their exact valuation/model-run identities without averaging them into one expected return.

LBO output is transaction/sponsor-return evidence only. It is not an investment recommendation and has no capital authority.

## Next slice

Stage 8.8 should triangulate valuation evidence while preserving method disagreement. Comparable per-share methods may be checked for overlap and dispersion; LBO should remain a non-comparable sponsor-return cross-check rather than being averaged into a synthetic fair value.