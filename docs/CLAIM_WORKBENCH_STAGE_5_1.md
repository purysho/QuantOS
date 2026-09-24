# Claim Workbench — Stage 5.1

A verified research source is not itself a trusted conclusion.

Stage 5.1 introduces a claim-scoped independent-review workflow between
`VERIFIED` catalog sources and the trusted `ClaimStore`.

## State machine

```text
VERIFIED source artifact
        ↓
      DRAFT
        ↓
    IN_REVIEW
      ↙     ↘
 REJECTED  APPROVED
              ↓
           PROMOTED
              ↓
          ClaimStore
```

## Mandatory controls

- drafts can reference only a currently `VERIFIED` research source;
- the draft permanently records the exact source artifact SHA-256;
- every claim needs an exact source locator;
- empirical and inference claims require explicit scope and limitations;
- the drafter cannot review their own claim;
- reviewer approval requires counter-evidence search notes;
- approval alone does not create a trusted Claim Card;
- promotion is a separate action;
- promotion re-checks that the catalog is still `VERIFIED` and that the
  verified artifact ID is exactly the artifact reviewed;
- the existing `ResearchCatalog.promote_research_claim()` gate is called again
  during promotion.

This creates two independent checks at promotion time: the workbench's
source/review invariants and the catalog's verified-artifact whitelist.

## What Stage 5.1 does not claim

- reviewer approval does not prove causality;
- counter-evidence notes do not guarantee an exhaustive literature search;
- a promoted historical empirical claim does not imply a future alpha signal;
- source verification does not substitute for replication;
- a trusted claim remains scoped to its recorded population, period,
  assumptions and limitations.

The next slice should add replication records and claim-to-claim
support/contradiction links so review can distinguish direct evidence,
replication, limitation and failed replication.
