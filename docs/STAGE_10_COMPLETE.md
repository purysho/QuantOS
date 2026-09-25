# Stage 10 Complete — Portfolio Construction & Shadow PAPER Controls

Stage 10 builds the portfolio-construction layer on top of the Stage 9 Research Lab while preserving First Current's fail-closed authority model.

## 10.1 — Baseline construction

- content-addressed synchronous point-in-time return datasets;
- EqualWeight and InverseVolatility baseline allocators;
- explicit long-only / full-investment / weight / gross / turnover policies;
- independent post-allocation constraint validation;
- immutable portfolio-solution persistence;
- `capital_authority = NONE`.

## 10.2 — Covariance

- frozen covariance artifacts bound to exact Research Run, dataset and security order;
- empirical and Ledoit-Wolf estimator families;
- finite/symmetry/PSD/condition diagnostics;
- explicit repair policy and evidence references;
- deterministic covariance identity.

## 10.3 — Minimum variance

- skfolio MeanRisk minimum-variance candidate;
- exact frozen covariance consumption verified after fit;
- solver diagnostics preserved;
- no optimizer fallback;
- mandatory EqualWeight and InverseVolatility comparison lineage;
- First Current portfolio-level one-way turnover enforced independently.

## 10.4 — Common out-of-sample comparison

- EqualWeight, InverseVolatility and MinimumVariance evaluated on identical frozen OOS folds;
- common implementation-cost policy;
- realized net return, volatility, drawdown, expected shortfall, turnover, concentration and benchmark-relative wealth;
- no `selected_method` field;
- `selection_authority = NONE` and `capital_authority = NONE`.

## 10.5 — Hierarchical candidates

- HRP and HERC added as research candidates;
- explicit Pearson distance and Ward linkage;
- frozen covariance reused rather than silently re-estimated;
- cluster labels plus distance/linkage fingerprints preserved;
- optional expansion of the same OOS dossier from three methods to five.

## 10.6 — Robustness

- adjacent-fold weight instability;
- label-invariant HRP/HERC cluster stability;
- covariance-estimator sensitivity;
- covariance condition-number checks;
- upper-weight and turnover headroom;
- inaccurate-solver detection;
- states: `INSUFFICIENT_EVIDENCE`, `WITHIN_POLICY`, `REVIEW_REQUIRED`.

## 10.7 — Human portfolio decision

- explicit two-person reviewer / independent-challenger gate;
- one assessment for every evaluated method;
- explicit trade-offs, objections and evidence;
- no numeric auto-ranking;
- one method may be recommended only for a separate PAPER review;
- `paper_authority = NONE` and `capital_authority = NONE`.

## 10.8 — Shadow-only PAPER authorization

- exact Research Run Manifest and current PAPER Model Registry state required;
- original Research Case shadow permit must match;
- one exact content-addressed portfolio solution is authorized, not merely a method name;
- frozen commission/spread/slippage/impact/borrow assumptions;
- finite authorization window;
- complete mandatory kill-condition set;
- `paper_authority = SHADOW_ONLY`, `order_authority = NONE`, `capital_authority = NONE`.

## 10.9 — Runtime PAPER enforcement

- authorization state machine: ACTIVE → SUSPENDED / TERMINATED / EXPIRED / CLOSED;
- no resume transition for the same authorization;
- manifest, registry, solution, execution-policy and monitoring-policy drift fail closed;
- missing or duplicate source lineage terminates the authorization;
- drawdown, turnover, implementation-cost, constraint and solution-drift kills suspend it;
- expected-vs-realized implementation-cost variance recorded;
- accepted observations remain shadow-only.

## 10.10 — PAPER review & mandatory postmortem

- exact authorization, OOS dossier, shadow observations, benchmark observations and enforcement-event chain;
- shadow-vs-OOS return calibration on comparable geometric mean period returns;
- realized volatility, drawdown, turnover and benchmark-relative wealth;
- cost-model error and solution drift;
- kill-event history preserved explicitly;
- states: `OPEN`, `INSUFFICIENT_EVIDENCE`, `WITHIN_POLICY`, `REVIEW_REQUIRED`;
- two-person postmortem;
- postmortem may recommend another `RESEARCH` iteration or close the research line only;
- no path to `APPROVED`, `LIVE`, broker orders or real capital.

## Integrity hardening

After 10.10, nested content identity was tightened so:

- OOS dossier identity binds nested fold outcomes;
- robustness identity binds covariance-sensitivity content;
- PAPER review validates nested OOS lineage;
- regression tests explicitly tamper nested comparison and robustness content and require fail-closed behavior.

## Stage 10 safety invariant

> A portfolio can be researched, compared, challenged, selected by humans, authorized for finite shadow observation, monitored, killed and postmortemed — but Stage 10 cannot create a live order or expose capital.

## Current gate

At Stage 10 completion, the full CI matrix is green on Python 3.11, 3.12 and 3.13.