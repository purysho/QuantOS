# Quant OS Prototype Test Plan

## Objective

Break the intelligence loop before adding ML, LLMs, portfolio optimization, or live execution.

## Test track A — Point-in-time integrity

- ingest an original filing event;
- ingest a later revision;
- reconstruct state before the revision;
- prove the revision is absent;
- inject duplicate event IDs and confirm rejection;
- inject naive timestamps and confirm rejection.

## Test track B — Evidence integrity

- observed/derived/estimated/inferred claims without provenance must fail;
- UNKNOWN may exist without provenance;
- current facts must carry an as-of time;
- later prototype: claim locator must resolve to a stored source artifact.

## Test track C — Intelligence behavior

- create expectations before observations;
- ingest positive, negative, and neutral surprises;
- confirm hypotheses are labeled INFERRED;
- confirm hypotheses describe a testable proposition rather than a trade directive;
- later prototype: attach counter-hypotheses and contradictory evidence.

## Test track D — Live adapter boundary

- parse SEC fixture into timestamped events;
- fail closed on impossible clock ordering;
- require an identifying SEC User-Agent;
- later prototype: record source-body hashes and retry/backoff behavior.

## Test track E — Capital firewall

- every order proposal is rejected in v0.1;
- research-gate PASS means SHADOW_ONLY, never live;
- fuzz quantity, side, missing IDs, duplicate requests;
- confirm research code has no broker credential interface.

## Promotion criteria for v0.2

All CI tests green plus:
- persistent append-only event ledger;
- source artifact hashing;
- expectations stored as their own point-in-time events;
- hypothesis ledger;
- event replay;
- one real SEC pull exercised manually;
- zero path from adapter/research modules to execution credentials.
