# Evidence Dossier — Stage 5.3

Stage 5.3 adds a deterministic evidence dossier over trusted ClaimCards.

It does **not** produce a probability of truth or an investment score.

## Evidence postures

A focal claim can currently be summarized as:

- `SPARSE_EVIDENCE`
- `SUPPORTING_EVIDENCE_PRESENT`
- `SUPPORTED_WITH_LIMITATIONS`
- `LIMITED_EVIDENCE`
- `DISPUTED`
- `REPLICATION_SUPPORTED`
- `FAILED_REPLICATION_PRESENT`
- `REPLICATION_CONFLICT`
- `INCONCLUSIVE`

These are structural descriptions of the recorded evidence graph.

## Precedence

The dossier deliberately makes negative/conflicting evidence hard to hide:

1. successful + failed replication → `REPLICATION_CONFLICT`
2. any failed replication → `FAILED_REPLICATION_PRESENT`
3. any reviewed contradiction → `DISPUTED`
4. successful replication → `REPLICATION_SUPPORTED`
5. support + limitations → `SUPPORTED_WITH_LIMITATIONS`
6. support only → `SUPPORTING_EVIDENCE_PRESENT`
7. limitations only → `LIMITED_EVIDENCE`
8. partial/inconclusive replication or extension only → `INCONCLUSIVE`
9. otherwise → `SPARSE_EVIDENCE`

This is not intended as an ordering from bad to good. It is a fail-visible
classification used to decide what a professional reviewer needs to inspect.

## Dossier flags

The dossier also emits explicit flags such as:
- no external context;
- contradictory claims present;
- limitations present;
- failed replication present;
- conflicting replication results;
- partial/inconclusive replication;
- no independent-data replication;
- no replication record.

No flag or posture has capital authority.
