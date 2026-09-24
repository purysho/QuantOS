# First Current Quant OS — Prototype v0.6.0

A high-assurance quantitative research operating-system prototype built around one rule:

> **Research may be aggressive; evidence, trust and capital authority must remain conservative and explicit.**

The repository is research-only. It can ingest live information, discover external research, construct reviewed evidence, and measure prospective signals, but **live order authorization is disabled by construction**.

## Current control stack

### Point-in-time intelligence
- separate event time and knowledge time
- durable DuckDB event ledger
- Parquet export
- point-in-time expectations
- as-of reconstruction without revision leakage
- idempotent ingestion and conflict detection

### Live-source boundaries
- SEC submissions normalization
- SEC primary-document artifact capture
- FRED/ALFRED vintage normalization
- provider-neutral market-event contract
- real arXiv metadata discovery with raw-feed provenance

### Research intake
- revision-preserving Research Radar
- deterministic research-attention triage
- review quarantine
- explicit reviewer assignment
- quarantined research catalog
- exact-source SHA-256 verification
- publication and attention never imply trust

### Claim trust
- verified source required before drafting
- exact source locator
- scope, assumptions and limitations
- independent claim reviewer
- mandatory counter-evidence notes
- approval separate from promotion
- promotion rechecks exact source artifact

### Evidence graph
- explicit SUPPORTS / LIMITS / CONTRADICTS / EXTENDS links
- direct / conceptual / reanalysis replication records
- successful, partial, failed and inconclusive replication outcomes
- failed replications remain visible
- no opaque truth score

### Evidence dossiers
- fail-visible evidence posture
- contradiction and failed-replication precedence
- explicit unresolved-evidence flags
- structural summary only; no probability of truth

### Professional reasoning controls
Six independent role contracts:
1. Fundamental Analyst
2. Quant Researcher
3. Portfolio Manager
4. Risk Officer
5. Execution Trader
6. Red Team

Each role records mandatory review dimensions, findings, objections and follow-ups against an exact evidence-dossier fingerprint.

A single blocking objection cannot be outvoted.

Blocking or conditional reviews can be resolved only by the reviewer who raised them, with explicit resolution notes and evidence references. The original objection remains immutable.

### Edge-discovery plumbing
- expectation surprises
- benchmark-adjusted market reactions
- implementability-aware reaction windows
- prospective shadow ledger
- minimum-sample gate
- edge-decay diagnostics

### Capital boundary
`CapitalFirewall.authorize_live_order()` remains unconditionally disabled.

Nothing in the current stack has authority to allocate real capital.

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
six professional role reviews
    ↓
open objections / conditions remain visible
    ↓
documented resolution where justified

    ╳
no automatic capital authorization
    ╳
```

## Research Radar

```bash
quantos-radar scan-arxiv --max-results 20
quantos-radar review-list --status QUEUED
quantos-radar review-start --queue-id review:... --reviewer analyst-1
quantos-radar review-candidate \
  --queue-id review:... \
  --reviewer analyst-1 \
  --notes "Relevant enough for source verification; no claims accepted."
quantos-radar catalog-admit --queue-id review:...
quantos-radar catalog-verify-file \
  --source-id ARXIV:2609.01234v2 \
  --path /secure/intake/paper.pdf \
  --source-uri https://arxiv.org/abs/2609.01234v2 \
  --verifier analyst-2 \
  --notes "Revision, title, authors and canonical source identity checked."
```

## Safety semantics

- `VERIFIED` source means source identity checked, not conclusions proven.
- `APPROVED` claim means a scoped claim passed its review workflow, not that it is universally true.
- `REPLICATION_SUPPORTED` is a structural evidence posture, not a probability.
- `REVIEW_SET_COMPLETE` is a workflow state, not an investment recommendation.
- an objection resolution closes one documented issue; it does not erase the original issue.
- `MEASURED` does not mean profitable.
- `NO_TRADE`, `UNKNOWN`, `QUARANTINED`, `INCOMPLETE` and `INSUFFICIENT_EVIDENCE` are valid outcomes.

See `docs/STAGE_5_COMPLETE.md`.
