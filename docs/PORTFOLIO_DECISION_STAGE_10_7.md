# Stage 10.7 — Portfolio Research Decision Gate

Stage 10.7 creates the first explicit human method-selection gate in the portfolio research pipeline. It does not automate the choice of portfolio method.

## Upstream evidence

A decision packet requires a content-addressed common out-of-sample PortfolioComparisonDossier and a content-addressed PortfolioRobustnessDossier bound to that exact comparison. Both upstream artifacts must still carry selection_authority = NONE and capital_authority = NONE.

## Two-person review

Every decision requires a named reviewer, a different independent challenger, explicit cross-method trade-offs, evidence references, and one assessment for every method evaluated upstream. A method cannot disappear from the decision simply because it performed poorly or is inconvenient to discuss.

## Dispositions

The packet can be RECOMMEND_FOR_PAPER_REVIEW, DEFER, or REJECT.

RECOMMEND_FOR_PAPER_REVIEW requires robustness state WITHIN_POLICY, exactly one human-selected method, and zero unresolved challenger objections.

DEFER and REJECT cannot silently select a method. DEFER requires at least one explicit unresolved challenger objection.

## No auto-ranking

Method assessments contain rationale and evidence, not a numeric composite score or automatic rank. The chosen method is recorded with method_selection_origin = HUMAN_REVIEW. This preserves human accountability instead of disguising a weighted formula as professional judgment.

## Authority boundary

Even a successful recommendation records paper_authority = NONE and capital_authority = NONE. It is permission to enter a separate PAPER authorization review, not permission to create a PAPER portfolio, send orders, or expose capital.

## Identity hardening

Stage 10.7 also makes comparison and robustness dossier identities independently recomputable before they can enter the decision gate. Tampered immutable artifacts therefore fail closed.

## Next slice

Stage 10.8 should create the separate PAPER portfolio authorization packet. It should bind one selected method, exact Research Run Manifest, current Model Registry PAPER state, prospective shadow permit, implementation-cost assumptions, monitoring thresholds, stop/kill conditions and reviewer approvals. It must remain shadow-only.
