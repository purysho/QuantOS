# Claim Evidence Graph & Replication — Stage 5.2

Stage 5.2 adds explicit relationships between already trusted claims.

It intentionally avoids a single "truth score."

## Claim relationships

A trusted claim may be linked to another trusted claim as:

- `SUPPORTS`
- `LIMITS`
- `CONTRADICTS`
- `EXTENDS`

The relationship is explicit, directional, reviewed and persisted. The system
does not infer causality merely because two claims are semantically similar.

## Replication records

Replication is stored separately from generic support/contradiction.

Each record includes:
- original trusted claim;
- replication trusted claim;
- direct / conceptual / reanalysis kind;
- outcome: replicates / partial / fails / inconclusive;
- whether independent data were used;
- preregistration status when known;
- review notes and reviewer;
- timestamp.

The two claims must reference distinct source-artifact sets. Restating the same
paper cannot count as replication.

## Reasoning behavior

For a focal claim, the evidence context returns separate buckets for:
- supporting claims;
- limiting claims;
- contradicting claims;
- extensions;
- replication records.

A failed replication remains visible as a failed replication. It is not
automatically averaged into an opaque confidence score.

Future scoring, if added, should consume these typed records while preserving
their identities and caveats.
