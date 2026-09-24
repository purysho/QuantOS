# First Current Quant OS — Prototype v0.1

A high-assurance prototype for a live quantitative intelligence loop.

This build is **research-only**. It cannot submit live orders. The prototype proves four properties before real market connectivity is added:

1. events preserve `event_time` and `knowledge_time`;
2. current state can be reconstructed as-of a historical knowledge timestamp;
3. hypotheses are explicitly labeled as inference, never facts;
4. the capital boundary fails closed: no hypothesis can create a live order.

## Architecture

```text
Event adapters
    ↓
Point-in-time Event Store
    ↓
Expectation + Surprise Engine
    ↓
Hypothesis Generator
    ↓
Evidence / Epistemic Labels
    ↓
Research Gate
    ↓
SHADOW_ONLY

              ╳
        no direct path
              ╳

         Live Capital
```

## Quick start

```bash
python -m unittest discover -s tests -v
PYTHONPATH=src python -m quantos.cli demo
```

The demo ingests a synthetic earnings event with a prior expectation, generates a hypothesis from the surprise, evaluates it through the research gate, and proves that the execution boundary rejects the order proposal.

## Current prototype modules

- `models.py` — typed events, claims, hypotheses, decisions and order proposals.
- `store.py` — append-only point-in-time event store.
- `intelligence.py` — expectation, surprise and deterministic hypothesis generation.
- `evidence.py` — claim registry and epistemic validation.
- `gates.py` — research promotion gate and capital firewall.
- `service.py` — end-to-end orchestration.
- `cli.py` — runnable demo.

## Safety boundary

`CapitalFirewall.authorize_live_order()` always raises `LiveTradingDisabled` in v0.1. This is intentional. Live execution will only be introduced after the independent risk kernel, compliance policy, reconciliation, credentials isolation and paper environment exist.

## Next prototype increments

1. Real SEC event adapter with accession IDs and timestamps.
2. FRED/ALFRED macro-vintage adapter.
3. Market-data adapter interface (Databento-compatible schema).
4. DuckDB/Parquet event persistence.
5. Claim-card RAG index and contradictory-evidence retrieval.
6. Perspective terminal over the same event/hypothesis stream.
7. Shadow strategy ledger and prospective scoring.
