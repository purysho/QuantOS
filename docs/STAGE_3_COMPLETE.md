# Stage 3 Complete — v0.4.0

## Purpose

Stage 3 converts the prototype from a timestamped event collector into a provenance-first edge-discovery system.

It still has **zero live-capital authority**.

## Proven properties

### Data
- later revisions cannot leak into earlier knowledge states;
- duplicate deterministic events are idempotent;
- conflicting contents under one identity fail closed;
- TIMESTAMPTZ semantics are preserved in persistent storage.

### Evidence
- raw evidence is content-addressed by SHA-256;
- reads verify artifact integrity;
- one event/lineage role cannot silently switch to different bytes;
- material Claim Cards require provenance;
- research source-card summaries are quarantined until tied to a verified artifact;
- limiting and contradictory evidence have explicit retrieval buckets.

### Relationships
- entity-graph edges require sourced claim IDs;
- edges have separate knowledge and economic effective times;
- graph traversal is bounded and cycle-safe;
- a graph path is never represented as causal proof.

### Edge measurement
- market reactions are benchmark-adjusted;
- prospective reaction windows default to the time the system actually knew the event;
- stale or incomplete reaction windows fail closed;
- shadow signals require a minimum sample before descriptive statistics appear;
- recent deterioration is measured against prior shadow behavior;
- decay diagnostics carry an explicit non-causal / serial-dependence caveat.

### Safety
- research gates only admit shadow evaluation;
- broker credentials do not exist in the research plane;
- live order authorization remains disabled.

## Failure modes intentionally still open

These are **not** solved by v0.4.0:

1. research papers have not yet been independently verified at scale;
2. no continuous Research Radar is ingesting newly published work;
3. no production market-data provider is connected;
4. no sector-specific accounting packs are complete;
5. no portfolio/risk kernel exists yet;
6. no execution simulator is integrated yet;
7. no Perspective terminal exists yet;
8. no compliance policy engine exists yet;
9. no real-money execution exists or should exist.

## Next stage — Research Radar

Stage 4 should discover new external research continuously while preserving the same trust boundary:

```text
external research feed
       ↓
raw feed artifact
       ↓
DISCOVERED metadata
       ↓
QUARANTINE
       ↓
triage / dedupe / topic scoring
       ↓
artifact verification
       ↓
claim extraction
       ↓
replication / counter-evidence search
       ↓
trusted research corpus
```

A new paper is a **discovery item**, not knowledge.

The first adapter should be arXiv because it provides structured programmatic metadata and allows a clean fixture-driven implementation. Later adapters can cover NBER, SSRN, journals, institutional research and GitHub research code.
