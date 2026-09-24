# First Current Quant OS — Prototype v0.7.0

A high-assurance quantitative research operating-system prototype built around one rule:

> **Research may be aggressive; evidence, trust and capital authority must remain conservative and explicit.**

The repository is research-only. It can ingest live information, discover research, construct reviewed evidence, build whole-case reasoning, and run prospective shadow evaluation controls, but **live order authorization is disabled by construction**.

## What v0.7.0 proves

### Point-in-time foundation
- separate event time and knowledge time
- durable DuckDB event ledger and Parquet export
- as-of reconstruction without revision leakage
- deterministic identities, idempotence and conflict detection
- SEC submissions and primary-document capture
- FRED/ALFRED vintage ingestion

### Research intake
- live arXiv metadata radar
- immutable raw-feed provenance
- deterministic attention triage
- review quarantine
- quarantined research catalog
- exact-source SHA-256 verification
- publication/attention never imply trust

### Claim trust
- verified source required before drafting
- exact locator, scope, assumptions and limitations
- drafter/reviewer separation
- mandatory counter-evidence notes
- approval separate from promotion
- promotion rechecks exact source artifact

### Evidence structure
- SUPPORTS / LIMITS / CONTRADICTS / EXTENDS links
- direct / conceptual / reanalysis replication records
- successful / partial / failed / inconclusive outcomes
- fail-visible evidence dossiers
- no opaque truth score

### Professional reasoning
Six durable professional roles:
1. Fundamental Analyst
2. Quant Researcher
3. Portfolio Manager
4. Risk Officer
5. Execution Trader
6. Red Team

A blocking objection cannot be outvoted. Resolutions require the original reviewer plus evidence references, while the original objection remains immutable.

### Whole-case reasoning
- immutable point-in-time Research Cases
- explicit thesis, mechanism, alternatives and falsifiers
- trusted supporting / limiting / contradicting claims
- probability-band scenario sets
- explicit outcome ranges and probability rationale
- Case Dossier fingerprint over:
  - exact Research Case
  - exact Scenario Set
  - current Evidence Dossiers for every cited claim
- case-level six-role reviews bound to that fingerprint
- old reviews become stale automatically when evidence/scenarios change

### Research readiness
The only positive readiness state is:

`READY_FOR_PROSPECTIVE_SHADOW`

A resulting permit is:
- bound to the exact Case Dossier fingerprint;
- bound to the exact Scenario Set;
- explicitly `PROSPECTIVE_SHADOW_ONLY`;
- invalidated by a changed Case Dossier.

The live-capital firewall remains unchanged.

### Forecast calibration
- scenario probabilities frozen prospectively
- outcome classification only after the horizon
- outcome adjudicator must differ from forecaster
- outcome evidence references required
- multiclass Brier score
- log loss
- calibration remains `INSUFFICIENT_EVIDENCE` below the configured sample threshold

## Reasoning chain

```text
external source
    ↓
immutable artifact
    ↓
verified source identity
    ↓
independently reviewed Claim Card
    ↓
typed evidence / replication graph
    ↓
Evidence Dossier
    ↓
immutable Research Case
    ↓
Scenario Set
    ↓
Case Dossier fingerprint
    ↓
six professional case reviews
    ↓
research readiness gate
    ↓
PROSPECTIVE_SHADOW_ONLY permit
    ↓
prospective forecast / shadow measurement / calibration

    ╳
no automatic live-capital path
    ╳
```

## Install and test

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
quantos demo
quantos edge-demo
```

CI validates the complete suite on Python 3.11, 3.12 and 3.13.

## Research Radar

```bash
quantos-radar scan-arxiv --max-results 20
quantos-radar review-list --status QUEUED
```

A separate live smoke workflow makes one small real arXiv metadata request and stores only disposable CI artifacts.

## Safety semantics

- `VERIFIED` source = source identity checked, not conclusions proven.
- `APPROVED` claim = scoped claim passed review, not universal truth.
- `REPLICATION_SUPPORTED` = evidence structure, not a probability.
- `REVIEW_SET_COMPLETE` = workflow completeness, not an investment recommendation.
- `READY_FOR_PROSPECTIVE_SHADOW` = permission to measure prospectively, not permission to trade.
- `MEASURED` = sample exists, not profitable.
- `NO_TRADE`, `UNKNOWN`, `QUARANTINED`, `INCOMPLETE`, and `INSUFFICIENT_EVIDENCE` are valid outcomes.

See `docs/STAGE_6_COMPLETE.md` for the full v0.7.0 boundary.
