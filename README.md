# First Current Quant OS — Prototype v0.3.1

A high-assurance prototype for live quantitative intelligence and provenance-first edge discovery.

The prototype is **research-only**. Live data may enter the research plane, but no component can submit live orders.

## What the prototype now proves

1. event time and knowledge time remain separate;
2. historical state can be reconstructed without leaking later revisions backward;
3. events persist in DuckDB and export to Parquet;
4. expectations are versioned point-in-time events;
5. SEC and FRED/ALFRED data normalize into a shared event contract;
6. raw research/source bytes can be stored immutably by SHA-256;
7. material Claim Cards require source provenance and deterministic IDs;
8. evidence retrieval separates supporting, limiting and contradicting evidence;
9. entity relationships are time-bounded and require sourced claims;
10. event market reactions are measured relative to a benchmark;
11. hypotheses can be scored prospectively in a shadow ledger;
12. small samples remain `INSUFFICIENT_EVIDENCE`, not “profitable”;
13. the capital firewall still rejects every live order.

## Intelligence chain

```text
LIVE / HISTORICAL SOURCES
          ↓
 immutable source artifacts
          ↓
 point-in-time events + Claim Cards
          ↓
 support / limits / contradictions
          ↓
 sourced entity graph
          ↓
 expectations → surprises
          ↓
 hypotheses
          ↓
 market-reaction residuals
          ↓
 prospective shadow observations
          ↓
 descriptive edge health

          ╳
    no capital path
          ╳

      live execution
```

## Install and test

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
quantos demo
quantos edge-demo
```

CI runs the suite and both demos on Python 3.11, 3.12 and 3.13.

## Live research ingestion

### SEC

```bash
export SEC_USER_AGENT="First Current Quant OS your-contact@example.com"
quantos sec --cik 320193 --db data/events.duckdb
```

SEC accession numbers become deterministic event IDs, so repeated polling of the same filing is a safe no-op.

### FRED / ALFRED vintages

```bash
export FRED_API_KEY="..."
quantos fred --series CPIAUCSL --vintage 2020-04-15 --db data/events.duckdb
```

When the provider supplies only a vintage date, Quant OS conservatively uses end-of-day UTC rather than pretending the observation was knowable earlier.

### Reconstruct prior knowledge

```bash
quantos asof \
  --entity FRED:CPIAUCSL \
  --as-of 2020-04-15T23:59:59Z \
  --db data/events.duckdb
```

## Safety semantics

- Passing a research gate means **shadow only**.
- A market reaction is descriptive and does not establish causality.
- An entity-graph path is a sourced relationship path, not proof of economic impact.
- `MEASURED` means the configured minimum sample exists; it does not mean profitable or approved.
- `CapitalFirewall.authorize_live_order()` remains unconditionally disabled.

## Stage 3 modules

- `artifacts.py` — content-addressed immutable evidence.
- `claims.py` — atomic claims and counter-evidence retrieval.
- `entity_graph.py` — sourced point-in-time relationships and bounded paths.
- `reactions.py` — benchmark-adjusted event reaction measurement.
- `shadow.py` — prospective observations and conservative edge health.
- `docs/EDGE_DISCOVERY_V0_3.md` — design doctrine.

## Next build slice

1. ingest the curated finance research library into an untrusted/reference catalog, then promote verified claims only after source checking;
2. capture SEC filing bodies as source artifacts and attach exact filing/section provenance;
3. derive reaction windows directly from market events rather than manual pre/post prices;
4. add rolling edge-decay, regime and crowding diagnostics;
5. add a Perspective terminal over events, claims, graph paths, hypotheses and shadow results;
6. add a Research Radar for new NBER/SSRN/arXiv/financial-journal work, with quarantine before promotion.
