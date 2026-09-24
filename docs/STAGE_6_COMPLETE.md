# Stage 6 Complete — Whole-Case Reasoning v0.7.0

Stage 6 converts the professional-reasoning substrate from isolated trusted claims into auditable whole-case research.

## 6.1 Research Cases

Each immutable case requires:
- subject/universe;
- thesis;
- mechanism;
- horizon;
- as-of timestamp;
- trusted supporting evidence;
- explicit alternatives;
- falsifiers;
- monitoring conditions.

Later claims cannot leak into an earlier case.

Case revisions create new content-addressed records and preserve lineage.

## 6.2 Scenario discipline

Each scenario has:
- probability lower / central / upper values;
- probability rationale;
- assumptions;
- observable conditions;
- outcome ranges.

Central probabilities sum to one. Probability bands and outcome ranges are validated.

Scenario Sets are immutable and bound to one exact Research Case.

## 6.3 Case Dossier

A Case Dossier fingerprints:

```text
Research Case
    +
Scenario Set
    +
current Evidence Dossier for every cited claim
```

If a new contradiction, limitation or replication changes the evidence around a cited claim, the Case Dossier fingerprint changes even though the ClaimCard remains immutable.

## 6.4 Case-level professional reviews

The same six professional roles review the whole Case Dossier.

Case-specific checks cover:
- economics / measurement / model fit;
- identification / overfitting / replication;
- scenario asymmetry / portfolio interaction / sizing assumptions;
- failure modes / tail / liquidity / scenario coverage;
- implementability / costs / capacity / market impact;
- strongest alternatives / falsifiers / missing evidence.

No majority vote can erase one blocking objection.

Old reviews do not transfer to a changed Case Dossier fingerprint.

## 6.5 Research readiness

Deterministic mapping:

```text
unresolved block → BLOCKED
missing role      → INCOMPLETE
open condition    → CONDITIONS_OPEN
complete panel    → READY_FOR_PROSPECTIVE_SHADOW
```

A prospective-shadow permit binds the exact Case Dossier and Scenario Set.

The permit has no live-execution authority.

## 6.6 Forecast calibration

Scenario probabilities are frozen before the horizon under a valid shadow permit.

Outcome classification:
- occurs only after the horizon;
- requires a separate adjudicator;
- requires evidence references;
- must select one of the frozen scenarios.

Scoring:
- multiclass Brier score;
- log loss.

Calibration summaries remain `INSUFFICIENT_EVIDENCE` until the configured minimum sample is reached.

## Capital boundary

The existing `CapitalFirewall` remains unchanged and blocks every live order.

## Next build phase

Stage 7 should begin the **Fundamental Engine**, starting below the valuation layer:

1. standardized financial-statement domain model;
2. accounting identities and statement invariants;
3. explicit statement-period and filing/knowledge timestamps;
4. line-item provenance to source artifacts/claims;
5. three-statement schedules and roll-forwards;
6. only then DCF / comps / SOTP / LBO.

The same doctrine applies: valuation must fail closed if accounting identities or provenance checks fail.
