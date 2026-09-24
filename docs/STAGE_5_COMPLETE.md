# Stage 5 Complete — Professional Reasoning Controls v0.6.0

Stage 5 builds the first durable professional-reasoning substrate.

## 5.1 Claim Workbench

```text
VERIFIED source
    ↓
DRAFT
    ↓
IN_REVIEW
   ↙   ↘
REJECTED APPROVED
             ↓
          PROMOTED
             ↓
         ClaimStore
```

Controls:
- exact verified artifact pinned into the draft;
- exact source locator;
- explicit scope and limitations for empirical/inference claims;
- independent reviewer;
- mandatory counter-evidence notes;
- approval and promotion are separate;
- promotion rechecks source verification and artifact identity.

## 5.2 Typed evidence and replication

Claim relationships remain distinct:
- SUPPORTS
- LIMITS
- CONTRADICTS
- EXTENDS

Replication remains distinct from generic support:
- DIRECT
- CONCEPTUAL
- REANALYSIS
- REPLICATES
- PARTIAL
- FAILS_TO_REPLICATE
- INCONCLUSIVE

No single confidence number hides those records.

## 5.3 Evidence Dossier

The dossier describes evidence structure with postures such as:
- SPARSE_EVIDENCE
- SUPPORTED_WITH_LIMITATIONS
- DISPUTED
- REPLICATION_SUPPORTED
- FAILED_REPLICATION_PRESENT
- REPLICATION_CONFLICT
- INCONCLUSIVE

Negative/conflicting evidence is deliberately hard to hide.

## 5.4 Professional Role Reviews

Six roles have separate required checks:
- Fundamental Analyst
- Quant Researcher
- Portfolio Manager
- Risk Officer
- Execution Trader
- Red Team

Reviews bind to the exact evidence-dossier fingerprint.

Any material evidence change therefore invalidates silent reuse of old reviews.

The panel is not a vote:
- any block → BLOCKING_OBJECTION_PRESENT
- missing role → INCOMPLETE
- open conditions → CONDITIONS_OPEN
- otherwise → REVIEW_SET_COMPLETE

## 5.5 Objection Resolution

Blocking and conditional reviews remain immutable.

A resolution:
- may be filed only by the reviewer who raised the issue;
- requires notes;
- requires evidence references;
- is stored separately;
- never deletes the original finding.

## What this proves

First Current can now preserve a chain from raw source bytes through independently reviewed claims and structured professional disagreement without collapsing uncertainty into a magic score.

## Still not built

- whole investment/research cases;
- explicit scenario trees;
- valuation-model selection;
- forecast calibration at case level;
- portfolio construction/risk kernel;
- trading simulation;
- Investment Committee case lifecycle;
- terminal UI;
- live capital authority.

## Next stage

Stage 6 should move from **single-claim reasoning** to **whole-case reasoning**.

A Research Case should minimally require:
- subject/universe;
- thesis;
- mechanism;
- decision horizon;
- trusted supporting claims;
- limiting and contradicting claims where known;
- alternative explanations;
- explicit falsifiers;
- monitoring conditions;
- as-of timestamp;
- deterministic case fingerprint.

Professional reviews should then attach to the whole case, while retaining the underlying claim-level dossiers.
