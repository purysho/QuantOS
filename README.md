# First Current Quant OS — Prototype v0.4.0

A high-assurance quantitative research operating-system prototype built around one rule:

> **Research may be aggressive; capital authority must remain conservative and deterministic.**

The repository is research-only. It can ingest live information and measure prospective signals, but **live order authorization is disabled by construction**.

## What exists now

### Point-in-time intelligence
- separate `event_time` and `knowledge_time`
- durable DuckDB event ledger
- Parquet export
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
- research references enter **QUARANTINED**, not trusted
- only independently verified source artifacts may promote research claims
- epistemic states: OBSERVED, DERIVED, ESTIMATED, INFERRED, SPECULATIVE, UNKNOWN

### Edge-discovery plumbing
- sourced point-in-time entity graph
- expectation surprise generation
- benchmark-adjusted market reactions
- reaction windows anchored either to public event time or, by default, **system knowledge time**
- prospective shadow ledger
- minimum-sample gate
- edge-decay diagnostics

### Capital boundary
`CapitalFirewall.authorize_live_order()` remains unconditionally disabled.

Passing a research gate means **shadow research only**.

## Intelligence chain

```text
external sources
      ↓
immutable artifacts + point-in-time events
      ↓
verified claims / quarantined references
      ↓
sourced entity graph
      ↓
expectations → surprises → hypotheses
      ↓
implementability-aware reaction windows
      ↓
prospective shadow observations
      ↓
edge health + decay diagnostics

      ╳
 no direct capital path
      ╳

portfolio / execution
```

## Install and test

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
quantos demo
quantos edge-demo
```

CI runs the complete suite on Python 3.11, 3.12 and 3.13.

## Useful commands

```bash
# Current SEC submissions
export SEC_USER_AGENT="First Current Quant OS your-contact@example.com"
quantos sec --cik 320193 --db data/events.duckdb

# Archive one primary filing document after the filing event exists
quantos sec-document \
  --event-id sec:0000320193:ACCESSION \
  --db data/events.duckdb

# Point-in-time macro vintage
export FRED_API_KEY="..."
quantos fred --series CPIAUCSL --vintage 2020-04-15

# Reconstruct what was known
quantos asof --entity FRED:CPIAUCSL --as-of 2020-04-15T23:59:59Z

# Import bibliographic research metadata into quarantine
quantos research-import \
  --registry research/source_registry.json \
  --catalog-db data/research-catalog.duckdb
```

## Interpretation rules

- `MEASURED` does **not** mean profitable.
- a graph path does **not** prove economic impact.
- an event reaction does **not** prove causality.
- a published paper does **not** become trusted evidence automatically.
- public event time and our system's knowledge time are distinct.
- large edge deterioration is a diagnostic alert, not an autonomous portfolio instruction.
- `NO_TRADE`, `UNKNOWN`, and `INSUFFICIENT_EVIDENCE` are valid outcomes.

See `docs/STAGE_3_COMPLETE.md` for the v0.4.0 control surface and next-stage gates.
