# Stage 9.5 — Performance & Robustness Diagnostics

Stage 9.5 measures a frozen cost-aware backtest without promoting a historical result into a claim of future profitability.

## Net-first performance

Primary portfolio diagnostics use net returns after explicit transaction and borrow costs. Gross cumulative return is retained alongside net cumulative return so implementation drag remains visible.

## Risk metrics

The current layer reports annualized net return, annualized net volatility, cash-excess Sharpe ratio, maximum drawdown, and historical expected-shortfall return at an explicit confidence level.

Insufficient samples and zero variance are explicit metric states. The engine does not manufacture infinity, zero, or an epsilon-adjusted ratio.

## Cost attribution

Analysis preserves summed transaction-cost rates, summed borrow-cost rates, terminal gross-versus-net wealth drag, and average one-way turnover.

## Benchmark-relative analysis

Each mandatory baseline receives final wealth, relative wealth return, annualized tracking error, and information ratio against the candidate's net returns.

## Information coefficient

Cross-sectional Spearman information coefficient is computed separately from portfolio returns using the full factor-score cross section and corresponding holding-period security returns.

If any scored security lacks an outcome return, that period's IC is marked PARTIAL and omitted from the mean rather than imputed. Zero rank variance is also explicit.

## Identity

The analysis fingerprints the exact economic backtest, performance policy, and source factor-run IDs used for IC.

## Boundary

Sharpe, drawdown, CVaR/expected shortfall, information ratio, and IC are descriptive statistics for one frozen research design. They are not evidence against multiple testing or backtest selection bias.

## Next slice

Stage 9.6 should add explicit multiple-testing defenses: variant registry, Deflated Sharpe Ratio inputs/diagnostics, and Probability of Backtest Overfitting from combinatorial purged cross-validation. These must remain separate from conventional performance metrics.
