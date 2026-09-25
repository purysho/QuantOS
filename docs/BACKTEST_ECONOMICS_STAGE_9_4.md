# Stage 9.4 — Cost-Aware Backtest Economics

Stage 9.4 separates a factor's paper gross return from the return that remains after explicit implementation economics.

## Portfolio construction

PortfolioConstructionPolicy records long/short mode, selected counts, gross exposures, maximum absolute position size, boundary-tie behavior, rationale, and evidence.

Rank selection is deterministic. When a selection boundary cuts through equal scores, the default is to fail closed rather than let security-ID ordering become an undocumented economic decision.

## Costs

CostModel separately records commission, half-spread, slippage, market impact, and annual short-borrow cost in basis points.

Transaction cost is charged on gross traded security notional. Borrow cost is charged on short gross exposure over the exact holding-period day count.

## Drift and turnover

The simulator carries post-return drifted security weights into the next rebalance. The next period's traded notional therefore reflects actual drift rather than comparing each target to the prior target as if prices had not moved.

One-way turnover is reported separately from gross traded notional.

## Return lineage

Every held security requires an exact SecurityPeriodReturn covering the holding interval. Missing returns fail closed.

A delisted security may be represented only when its return observation also carries corporate-action fact IDs. A disappeared security can therefore not silently vanish from the backtest.

## Mandatory baselines

Every holding period must contain exact returns for four baseline families: cash, market-cap benchmark, equal-weight, and inverse-volatility.

Candidate gross and net wealth are tracked separately from each baseline wealth path.

## Output identity

The backtest fingerprints exact factor ID, walk-forward validation-plan ID, construction policy, cost model, factor runs, target positions, held-security return IDs, benchmark return IDs, and per-period economics.

## Boundary

Stage 9.4 does not call a positive historical return profitable evidence. It is a simulation result under explicit assumptions.

## Next slice

Stage 9.5 should add performance and robustness diagnostics over frozen validation/backtest results: annualized return/volatility, Sharpe, drawdown, CVaR, information coefficient, turnover/cost attribution, benchmark-relative results, and then multiple-testing-aware Deflated Sharpe and Probability of Backtest Overfitting.
