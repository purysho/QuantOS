# Stage 10.8 — Shadow-Only Portfolio PAPER Authorization

Stage 10.8 is a separate authorization gate after the Stage 10.7 human research decision. It does not authorize live execution.

## Exact lineage gate

A PortfolioPaperAuthorization is issued only when the exact Research Run Manifest is PAPER-eligible, the current Model Registry state is PAPER for that same manifest, the original ProspectiveShadowPermit matches the Research Case and case-dossier fingerprint, the OOS comparison and robustness dossiers are authentic and match the same run, and the Stage 10.7 decision explicitly recommended one method for PAPER review with no unresolved objections.

The authorization also binds one exact content-addressed portfolio solution. The selected method name alone is insufficient.

## Solution authenticity

EqualWeight and InverseVolatility use the baseline PortfolioSolution identity contract; MinimumVariance uses the optimized solution identity contract; HRP/HERC use the hierarchical solution identity contract. The method implied by the exact solution must equal the method chosen by the human research decision.

## Execution assumptions

PortfolioPaperExecutionAssumptions freeze commission, half-spread, slippage, market-impact and borrow assumptions with rationale and evidence. These are assumptions for prospective shadow accounting, not broker instructions.

## Monitoring and kill policy

The policy freezes minimum observations, maximum drawdown, one-way turnover, implementation-cost rate, absolute position weight, gross exposure and solution-drift turnover.

The complete mandatory kill set is structural and cannot be silently omitted: data-lineage break, model/manifest change, portfolio-constraint breach, drawdown breach, turnover breach, implementation-cost breach and solution-drift breach.

The selected solution must already fit the frozen PAPER position, gross-exposure and turnover ceilings before authorization can be created.

## Finite authorization

Every authorization has an explicit authorized_at and expires_at. It cannot predate the research decision, shadow permit, registry state or selected solution.

Two distinct reviewers are required: a portfolio reviewer and an independent risk reviewer.

## Authority boundary

Every authorization records lifecycle_stage = PAPER, paper_authority = SHADOW_ONLY, order_authority = NONE, capital_authority = NONE and purpose = PROSPECTIVE_PORTFOLIO_SHADOW_ONLY.

Stage 10.8 therefore cannot create broker orders, live positions or real-capital exposure.

## Next slice

Stage 10.9 should enforce this packet during prospective portfolio observation: authorization expiry, kill-condition evaluation, suspension/termination ledger, target-solution drift, implementation-cost variance and lineage breaks should immediately block further shadow observations until a new authorization is issued.
