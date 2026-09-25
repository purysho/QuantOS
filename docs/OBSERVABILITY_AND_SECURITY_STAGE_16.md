# Stage 16 — Observability and Security

## 16.1 Observability (`quantos.observability`)

- **Run and span IDs.** `span(component, operation)` opens a traced unit of work. It creates a run ID if none is active, and propagates run, span and parent IDs through `contextvars`, so nested work across modules is linked.
- **Structured events.** `emit()` writes one JSON object per line: timestamp, level, component, event, run and span, and fields. Output can go to a stream, a file (`QUANTOS_EVENT_LOG`) or both. Every line passes through the security redactor first.
- **Error taxonomy.** `classify_exception` sorts failures into:
  - `FAIL_CLOSED_VALIDATION`;
  - `IDENTITY_MISMATCH`;
  - `SCOPE_REFUSED`;
  - `EXTERNAL_ENGINE_FAILURE`;
  - `PROVIDER_FAILURE`;
  - `NETWORK_DENIED`;
  - `KILL_SWITCH_ENGAGED`;
  - `SECRET_UNAVAILABLE`;
  - `INTERNAL_ERROR`.

  A span that fails records its category.
- **Metrics.** An in-process registry keeps labelled counters (events, errors by category) and duration summaries per operation.
- **Persistence.** `ObservabilityStore` (DuckDB) keeps spans. `quantos ops-report --db …` summarizes runs, operations, error counts and the slowest durations.
- **Instrumented today:** ORE worker runs and radar fetches, plus every egress decision.

Observability records. It never changes a result.

## 16.2 Security (`quantos.security`)

- **Kill switch.** An operator stop for all outbound activity, engaged by `data/KILL_SWITCH` (or `QUANTOS_KILL_SWITCH_FILE`) or by `QUANTOS_KILL_SWITCH=1`. It is checked inside the egress guard, so no research, strategy or adapter code can bypass it. Operators run `quantos kill-switch status|engage|release --actor … --reason …`. Engaging and releasing are audited.
- **Egress guard.** Every outbound request must be HTTPS to an allowlisted host, with the kill switch released. The allowlist covers arXiv, Crossref, SEC, FRED, Tiingo, Polygon, NBER, the Federal Reserve, BIS and the ECB. `QUANTOS_EGRESS_EXTRA_HOSTS` adds hosts explicitly. The arXiv, Crossref, SEC submissions, SEC filing-artifact and FRED adapters now route through it, and so do all new adapters. Denials raise `NetworkDenied` or `KillSwitchEngaged` and are logged.
- **Secrets.** `SecretProvider` resolves `QUANTOS_SECRET_<NAME>`, or a file in `QUANTOS_SECRETS_DIR`. On POSIX, a file readable by group or others is refused. A `Secret` never shows its value in `repr`/`str`, refuses pickling, and registers its value with the process redactor. Credential-like URL parameters (`api_key`, `token`, …) are always masked. Resolving a secret is audited by name, never by value.
- **Audit log.** An append-only, SHA-256 hash-chained ledger in DuckDB. `quantos audit-verify` detects altered, deleted or reordered records.
- **Repository secret lint.** A test scans tracked files for credential patterns: AWS keys, private keys, GitHub tokens, Anthropic keys and Slack tokens.

## Threat model (prototype scope)

| Threat | Control | Residual risk |
| --- | --- | --- |
| Credentials leak into logs, errors or artifacts | Redactor on every event and audit record; `Secret` wrapper; credential-param masking | A value shorter than 4 characters is not registered |
| Adapter calls an unexpected host | Egress allowlist in one guard; HTTPS only | DNS/TLS trust is delegated to the OS and `requests` |
| Runaway or compromised process keeps calling out | Kill switch checked on every request, independent of strategy code | Requests already in flight finish |
| Audit trail edited after an incident | Hash chain; `audit-verify` | Truncating the tail can't be detected without an external anchor. Publish the head hash periodically |
| Credentials committed to git | Secret lint test | Pattern-based only |
| External engine crash or state bleed | ORE runs in an isolated worker process | The worker still runs with the parent's privileges |

## Not in scope

- A secrets manager integration (Vault or cloud KMS).
- OS-level network egress enforcement.
- Signed releases and SBOM attestation.
- An external audit anchor.
- Formal penetration testing.

All of these are required before any network execution or broker sandbox, as HANDOFF.md states. Nothing here grants authority.
