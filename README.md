# First Current Quant OS — Prototype v0.5.0

A high-assurance quantitative research operating-system prototype built around one rule:

> **Research may be aggressive; trust and capital authority must remain conservative and explicit.**

The repository is research-only. It can ingest live information, discover external research, and measure prospective signals, but **live order authorization is disabled by construction**.

## What exists now

### Point-in-time intelligence
- separate `event_time` and `knowledge_time`
- durable DuckDB event ledger and Parquet export
- point-in-time expectation history
- as-of reconstruction without revision leakage
- idempotent ingestion and conflict detection

### Live-source boundaries
- SEC submissions normalization
- targeted SEC primary-document capture
- FRED/ALFRED vintage normalization
- provider-neutral market-event contract

### Evidence and reasoning controls
- immutable SHA-256 source artifacts
- event → artifact lineage
- atomic Claim Cards
- explicit support / limitation / contradiction retrieval
- sourced point-in-time entity graph
- epistemic states: OBSERVED, DERIVED, ESTIMATED, INFERRED, SPECULATIVE, UNKNOWN

### Research Radar
- live arXiv metadata discovery
- raw Atom-feed provenance
- revision-preserving discovery identities
- deterministic attention triage
- review quarantine
- explicit reviewer assignment and disposition
- reviewed discoveries enter the research catalog as **QUARANTINED**
- exact source bytes require a separate source-identity verification gate
- source verification creates **zero claims**

### Edge-discovery plumbing
- expectation surprise generation
- benchmark-adjusted market reactions
- reaction windows anchored either to public event time or, by default, **system knowledge time**
- prospective shadow ledger
- minimum-sample gate
- edge-decay diagnostics

### Capital boundary
`CapitalFirewall.authorize_live_order()` remains unconditionally disabled.

Passing any current research gate means **research/shadow only**.

## Trust chain

```text
external source / research feed
          ↓
immutable artifacts + point-in-time metadata
          ↓
DISCOVERED
          ↓
attention triage
          ↓
review QUEUE
          ↓
UNDER_REVIEW
          ↓
CATALOG_CANDIDATE
          ↓
QUARANTINED catalog record
          ↓
exact-source verification
          ↓
VERIFIED source identity
          ↓
claim-level review          ← next stage
          ↓
trusted Claim Cards
          ↓
hypotheses / shadow research

          ╳
    no direct capital path
          ╳
```

## Install and test

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
quantos demo
quantos edge-demo
```

CI runs the complete suite on Python 3.11, 3.12 and 3.13. A separate live smoke workflow makes one small arXiv metadata request and stores only disposable CI artifacts.

## Research Radar

```bash
# Discover and triage current quantitative-finance research
quantos-radar scan-arxiv --max-results 20

# Inspect review quarantine
quantos-radar review-list --status QUEUED

# Start and complete review
quantos-radar review-start --queue-id review:... --reviewer analyst-1
quantos-radar review-candidate \
  --queue-id review:... \
  --reviewer analyst-1 \
  --notes "Relevant enough for source verification; no claims accepted."

# Admit metadata only; remains QUARANTINED
quantos-radar catalog-admit --queue-id review:...

# Separately attach exact source bytes after identity checking
quantos-radar catalog-verify-file \
  --source-id ARXIV:2609.01234v2 \
  --path /secure/intake/paper.pdf \
  --source-uri https://arxiv.org/abs/2609.01234v2 \
  --verifier analyst-2 \
  --notes "Revision, title, authors and canonical source identity checked."
```

## Other useful commands

```bash
export SEC_USER_AGENT="First Current Quant OS your-contact@example.com"
quantos sec --cik 320193 --db data/events.duckdb

quantos sec-document \
  --event-id sec:0000320193:ACCESSION \
  --db data/events.duckdb

export FRED_API_KEY="..."
quantos fred --series CPIAUCSL --vintage 2020-04-15

quantos asof --entity FRED:CPIAUCSL --as-of 2020-04-15T23:59:59Z
```

## Interpretation rules

- `MEASURED` does **not** mean profitable.
- a graph path does **not** prove economic impact.
- an event reaction does **not** prove causality.
- a published paper does **not** become trusted evidence automatically.
- `VERIFIED` research source means source identity/provenance checked, **not** conclusions proven.
- radar attention is not a truth, quality, or alpha score.
- public event time and our system's knowledge time are distinct.
- large edge deterioration is a diagnostic alert, not an autonomous portfolio instruction.
- `NO_TRADE`, `UNKNOWN`, `QUARANTINED`, and `INSUFFICIENT_EVIDENCE` are valid outcomes.

See `docs/STAGE_4_COMPLETE.md` for the v0.5.0 research-intake boundary.
