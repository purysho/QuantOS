# Case Dossiers — Stage 6.3

Stage 6.3 creates the exact review target for whole-case professional reasoning.

A Case Dossier binds:

```text
immutable Research Case
        +
exact Scenario Set
        +
current Evidence Dossier for every case claim
        ↓
case-dossier SHA-256 fingerprint
```

## Why this matters

A Research Case contains immutable claim IDs, but the evidence around a trusted
claim can evolve later:

- a new limitation can be linked;
- a contradiction can appear;
- an independent replication can succeed or fail.

The ClaimCard identity should not change when external evidence changes.

Therefore case-level professional reviews must bind not only to claim IDs, but
to the current evidence-dossier fingerprint for each cited claim.

If any one of the following changes, the case-dossier fingerprint changes:
- the Research Case;
- the Scenario Set;
- support/limitation/contradiction links around a cited claim;
- replication records around a cited claim;
- dossier flags/posture derived from that evidence.

Prior reviews remain auditable, but they are not silently reused for the new
case-dossier fingerprint.
