# Edge Discovery v0.3

This stage adds a provenance-first shadow research loop. It deliberately does
not add autonomous trading or an LLM decision-maker.

## Chain of custody

```text
raw source bytes
      ↓
SHA-256 content-addressed artifact
      ↓
source fetch observation
      ↓
atomic Claim Card + exact locator
      ↓
support / limits / contradiction buckets
      ↓
hypothesis
      ↓
benchmark-adjusted market reaction
      ↓
prospective shadow observation
      ↓
edge-health measurement
```

## Design rules

- Raw evidence is immutable and hash-verified when read.
- A non-UNKNOWN Claim Card requires a source artifact.
- Claim IDs are derived from claim text + sources + locator.
- Retrieval always has separate limiting and contradicting buckets.
- A market reaction is descriptive, not proof of causality.
- Shadow outcomes are grouped by explicit `signal_id`; one hypothesis is not an edge.
- Fewer than 20 shadow observations returns `INSUFFICIENT_EVIDENCE`.
- Passing the sample threshold returns `MEASURED`, not `PROFITABLE` or `APPROVED`.
- Nothing in this stage has execution authority.

## What comes next

1. ingest the curated research library into Claim Cards;
2. source SEC filings as stored artifacts, not only normalized events;
3. add an as-of entity graph for suppliers/customers/ownership/exposure;
4. derive reaction windows directly from the market event ledger;
5. add rolling/decay diagnostics and regime labels;
6. expose events, evidence, hypotheses and shadow measurements in Perspective.
