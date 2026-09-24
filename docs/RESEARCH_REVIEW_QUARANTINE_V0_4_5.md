# Research Review Quarantine — v0.4.5

The Research Radar now hands sufficiently high-attention discoveries into a
separate review queue.

This is **not** trust promotion.

## Automatic handoff

By default a radar item with attention score >= 0.50 is enqueued for review.

The queue stores:
- exact discovery/revision identity;
- source URL and feed artifact provenance;
- triage profile hash;
- the initial attention score/band;
- review status and human/research review metadata.

## State machine

```text
DISCOVERED / TRIAGED
        ↓
      QUEUED
        ↓
   UNDER_REVIEW
      ↙      ↘
 DISMISSED   CATALOG_CANDIDATE
```

`CATALOG_CANDIDATE` still means **unverified**. It is only permission to do
the next work: resolve the primary source, verify bibliographic identity,
extract scoped claims, find limiting/contrary evidence, and attempt replication
where appropriate.

The queue cannot create Claim Cards or mark a research source VERIFIED.
