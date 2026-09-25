# Stage 9.6 — Multiple-Testing & Backtest-Overfitting Diagnostics

Stage 9.6 adds two distinct defenses against research selection bias: Deflated Sharpe Ratio (DSR) and Probability of Backtest Overfitting (PBO).

Methodology follows Bailey & López de Prado, The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality (Journal of Portfolio Management, 2014), and Bailey, Borwein, López de Prado & Zhu, The Probability of Backtest Overfitting (Journal of Computational Finance).

## Complete variant registry

Every VariantReturnSeries belongs to one immutable ResearchExperimentSpecification and carries one full synchronous series of **net** period returns from an economic backtest.

The number of registered variants must exactly equal experiment.variants_tested. A researcher cannot compute the diagnostics on only the surviving or interesting variants.

## Deflated Sharpe Ratio

The engine calculates unannualized Sharpe for every registered variant, the cross-sectional variance of those Sharpe estimates, and the expected maximum Sharpe under the null across the complete registered trial count using the extreme-value approximation from the DSR framework.

The selected variant's Probabilistic Sharpe statistic is then evaluated against that expected maximum rather than against zero. The sampling-error term includes the selected return series' skewness and kurtosis.

DSR output preserves observed Sharpe, trial count, Sharpe variance, expected maximum null Sharpe, skewness, kurtosis, z-score, and probability.

## PBO via CSCV

PBO uses the complete synchronous T × N matrix of net variant returns.

The time axis is divided into an even number S of equal contiguous slices. Every combination of S/2 slices becomes in-sample and the complement becomes out-of-sample.

For every symmetric split, the engine:

1. computes the same unannualized Sharpe metric for every variant in-sample and out-of-sample;
2. selects the in-sample winner;
3. ranks that same variant out-of-sample using average ranks for ties;
4. calculates omega = rank / (N + 1);
5. calculates lambda = log(omega / (1 - omega)).

PBO is the fraction of combinations with lambda < 0: cases where the selected in-sample winner finishes below the out-of-sample median.

The period count must divide exactly into CSCV slices. The engine does not silently truncate observations.

## Tie policy

By default, an in-sample winner tie fails closed. Deterministic ID ordering is not allowed to become an undocumented model-selection rule. A policy may explicitly permit that tie-break when required.

## Boundary

DSR and PBO answer different questions and neither proves an investment strategy is safe, causal, scalable, or profitable in the future. They are research diagnostics over the exact search that was recorded.

## Next slice

Stage 9.7 should bind the universe, factor definition, walk-forward plan, economic backtest, conventional analytics, and multiple-testing audit into one immutable Research Run / Model Registry artifact with lifecycle gates for RESEARCH → VALIDATED → BACKTESTED → PAPER.
