# Stage 19 — Keyless Public Data, First-Run Setup and Daily Runbook

Modules:
- `quantos.keyless`
- `quantos.home`
- `quantos.doctor`
- `quantos.runbook`
- `quantos.terminal_vendor`
- `quantos.cli_support`

CLI: `quantos setup | doctor | daily`, plus `quantos terminal vendor`.

## Goal

Anyone can install First Current and get useful, point-in-time data **with no signup and no API key**. Optional free personal keys add stock prices and macro vintages.

## Keyless sources

All five were verified live on 2026-09-25.

| Source | Adapter | Knowledge time |
|---|---|---|
| SEC ticker directory (`company_tickers_exchange.json`) | `SECTickerDirectoryAdapter` | capture time |
| SEC XBRL company facts | `SECCompanyFactsAdapter` → `XbrlFactStore` | end of the filing day in New York, the latest possible EDGAR acceptance |
| US Treasury par yield curve | `TreasuryYieldCurveAdapter` | `CAPTURE_TIME`, or `PUBLICATION_SCHEDULE` (18:00 New York) |
| ECB euro FX reference rates | `ECBReferenceRateAdapter` | `CAPTURE_TIME`, or `PUBLICATION_SCHEDULE` (16:00 Frankfurt) |
| FRED graph CSV | `FredCsvAdapter` | always `CAPTURE_TIME`: the values are latest revisions |

Details:
- **XBRL facts.** A restatement in a later filing is a new version. `as_of(cik, concept, known_at)` returns the latest value per period known at that time.
- **Publication-schedule policy.** It is an explicit, recorded assumption (stored on every row). It never produces a time later than the capture itself.
- **Security Master seeding.** Records are valid from the snapshot date only; no historical ticker mapping is claimed. The security kind is not in SEC's directory, so it is recorded as an assumption for review. A later identical snapshot adds nothing.
- **Tickers.** Share-class spellings (`BRK.B`, `BRK-B`, `BRK/B`) all resolve.
- **Missing filings.** A 404 from SEC is expected for funds and trusts that file no XBRL financials, and is reported as such.
- **Networking.** Every request identifies itself as SEC requires ("Company contact@email"; SEC rejects agents containing URLs). Requests go through the egress guard, are archived as source artifacts, and retry with backoff on timeouts, 429 and 5xx responses.
- **Performance.** Rows are written with chunked multi-row inserts in one transaction; DuckDB's `executemany` was about 30× slower. A first run for 3 companies with 2 years of backfill takes about 1–2 minutes; a re-run stores nothing new.

## Home and configuration

- `QUANTOS_HOME` holds `quantos.toml` (no secrets), `secrets/` (0700 directory, 0600 files) and `data/`. When `QUANTOS_HOME` is unset, the current directory is the home.
- Every CLI calls `activate()`. It changes into the home, points the secret provider at `secrets/`, and exports the contact identity for SEC, Crossref and feed etiquette. Explicitly set environment variables win.
- `quantos setup` works interactively or with flags. It requires a contact email and chooses the price provider from the keys that are present.

## Doctor

`quantos doctor [--online]` reports `ok`, `warn` or `FAIL` for:
- Python;
- configuration and contact identity;
- the universe;
- secret permissions and each optional key;
- the price provider;
- data directory space;
- the kill switch;
- QuantLib, NautilusTrader and ORE;
- terminal assets;
- with `--online`, each keyless source, probed through the egress guard.

It exits non-zero if any check fails.

## Daily runbook

`quantos daily` runs universe → fundamentals → rates → prices → research → terminal.
- Each step is idempotent.
- A failing step is reported and does not stop the others. One bad ticker or one unavailable feed does not hide the rest.
- The exit code is non-zero if any step failed.
- `--only` runs a subset, and `--backfill-from YEAR` loads history.

## Friendly errors

All three CLIs report expected failures as one line with their error category (validation, scope refusal, network denied, secret unavailable, kill switch, provider) and exit with code 2. `QUANTOS_DEBUG=1` shows the traceback.

## Offline terminal

`quantos terminal vendor`:
1. downloads the four Perspective 3.8.0 npm tarballs;
2. verifies each against its frozen npm `sha512` integrity;
3. refuses tarballs without a license file or with unsafe paths;
4. extracts only the runtime files and licenses.

Exports then rewrite the pinned CDN URLs to `./vendor/npm/...`. The browser's SRI checks still apply, and a smoke test with every CDN request blocked passes.
