# First Current Quant OS — Prototype v0.2

A high-assurance prototype for a live quantitative intelligence loop.

The prototype is **research-only**. Live data may enter the research plane, but no component can submit live orders.

## What v0.2 proves

1. events preserve separate `event_time` and `knowledge_time`;
2. state can be reconstructed as-of a historical knowledge timestamp;
3. events persist across process restarts in DuckDB and can export to Parquet;
4. expectations are themselves point-in-time events, so revised consensus cannot leak backward;
5. hypotheses are explicitly labeled as inference and recorded in a research ledger;
6. SEC and FRED/ALFRED data normalize into the same event contract;
7. market-feed records have a provider-neutral normalization boundary;
8. repeated identical provider events are idempotent, while conflicting reuse of an event ID fails closed;
9. the capital firewall still blocks every live order.

## Architecture

```text
SEC / FRED vintages / market adapters
                 ↓
        Idempotent ingestion
                 ↓
       DuckDB event ledger
        ↙               ↘
Parquet snapshots    as-of replay
                 ↓
       Expectation book
                 ↓
        Surprise engine
                 ↓
      Hypothesis generator
                 ↓
       Research ledger
                 ↓
        Research gate
                 ↓
          SHADOW ONLY

                 ╳
          no direct path
                 ╳

           Live capital
```

## Install and test

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
quantos demo
```

CI runs the suite on Python 3.11, 3.12 and 3.13.

## Live research ingestion

### SEC

Set an identifying SEC User-Agent with a contact address, then ingest a CIK:

```bash
export SEC_USER_AGENT="First Current Quant OS your-contact@example.com"
quantos sec --cik 320193 --db data/events.duckdb
```

SEC accession numbers become deterministic event IDs, so repeated polling of the same filing is a safe no-op.

### FRED / ALFRED vintages

Set a FRED API key and request a historical vintage:

```bash
export FRED_API_KEY="..."
quantos fred --series CPIAUCSL --vintage 2020-04-15 --db data/events.duckdb
```

When FRED provides only a vintage **date**, not an exact release timestamp, Quant OS conservatively marks knowledge availability at end-of-day UTC rather than pretending the value was knowable earlier.

### Reconstruct what the OS knew

```bash
quantos asof \
  --entity FRED:CPIAUCSL \
  --as-of 2020-04-15T23:59:59Z \
  --db data/events.duckdb
```

### Export the ledger

```bash
quantos export --db data/events.duckdb --parquet data/events.parquet
```

## Core modules

- `models.py` — typed events, claims, hypotheses, decisions and order proposals.
- `persistent.py` — durable DuckDB point-in-time event ledger + Parquet export.
- `expectations.py` — versioned point-in-time expectation book.
- `ingestion.py` — idempotent provider-ingestion boundary with conflict detection.
- `ledger.py` — persistent hypothesis and research-decision ledger.
- `intelligence.py` — surprise calculation and deterministic hypothesis generation.
- `adapters/sec.py` — SEC submissions normalization.
- `adapters/fred.py` — FRED/ALFRED vintage normalization.
- `adapters/market.py` — provider-neutral market-record contract.
- `evidence.py` — epistemic/provenance validation.
- `gates.py` — research gate and capital firewall.
- `service.py` — orchestration.

## Safety boundary

`CapitalFirewall.authorize_live_order()` always raises `LiveTradingDisabled` in v0.2.

No SEC/FRED/market adapter has broker credentials or a reference to the execution layer. The next live-capital steps remain gated behind an independent risk kernel, compliance policy, broker-state reconciliation, credential isolation and a sustained paper environment.

## Next stage

The next useful work is no longer basic ingestion plumbing. It is the beginning of the **edge-discovery loop**:

1. source-artifact hashing and data lineage;
2. claim-card retrieval and contradictory-evidence retrieval;
3. expectations from multiple independent models/sources;
4. event/entity graph;
5. market-reaction residuals;
6. shadow hypothesis scoring through time;
7. signal-decay / edge-health metrics;
8. Perspective terminal over events, hypotheses and shadow performance.
