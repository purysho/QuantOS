# Stage 17 — Market-Data Provider Pipeline

Module: `quantos.market_data`. CLI: `quantos market-data --provider {tiingo,polygon} --symbol SPY --security-id <id> --start YYYY-MM-DD --end YYYY-MM-DD`.

## Flow

```
provider API ──(EgressGuard + kill switch, key from SecretProvider in a header)──▶ raw bytes
   └─▶ SourceArtifactStore (exact response archived, SHA-256 addressed)
   └─▶ DailyBar[] (raw, unadjusted; knowledge_time = capture time)
        └─▶ BarStore (bitemporal; revisions kept, never overwritten)
             ├─▶ quality_report(calendar)        completeness / staleness / OHLC / intraday / revisions
             ├─▶ reconcile_providers(a, b)        cross-provider close agreement in bps
             ├─▶ cross_check_provider_actions()   provider div/split fields vs the reviewed ledger
             └─▶ to_session_closes()  ──▶  Stage 13.4 ResearchReturnPanelBuilder
```

## Rules

- **Raw prices only are facts.** First Current applies its own corporate actions (Stage 13.2). Tiingo's `adjClose`, `divCash` and `splitFactor` fields are kept only to reconcile against the reviewed ledger. Nothing is ingested from them automatically. Polygon responses must be `adjusted=false`; an adjusted response is refused.
- **Point in time.** A bar is known when it was captured. A later capture with different values is a *revision*: both rows are kept and `BarStore.bars(known_at=...)` returns what was known at that moment. Identical re-captures are deduplicated.
- **Intraday captures are never closes.** A bar captured before its session closed is flagged `INTRADAY_CAPTURE` and excluded by `to_session_closes`.
- **Calendar completeness.** Every `SessionCalendar` session that has closed by `known_at` must have a bar. A missing one gives `MISSING_SESSION`. A bar on a non-session gives `BAR_ON_NON_SESSION`. If there is no bar for the latest closed session(s), the report shows `STALE`.
- **Disagreement is reported, not averaged.** `reconcile_providers` lists sessions present on only one side and closes differing by more than `tolerance_bps`.
- **Credentials.** `TIINGO_API_KEY` and `POLYGON_API_KEY` are read through `SecretProvider`, from `QUANTOS_SECRET_<NAME>` or a private `QUANTOS_SECRETS_DIR`. They are sent as an `Authorization` header, never in the URL, so archived source URIs and logs contain no key. A missing key fails before any request.
- **Egress.** `api.tiingo.com` and `api.polygon.io` are on the default allowlist. The engaged kill switch blocks capture.

## Status

Both adapters are verified against recorded response fixtures (`tests/test_market_data.py`). No live key was available while building this stage. Choosing which provider is licensed for research use, and whether its terms allow archiving raw responses, is an owner decision. Run one live capture after a key is configured.
