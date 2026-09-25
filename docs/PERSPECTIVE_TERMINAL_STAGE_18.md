# Stage 18 — Read-Only Perspective Terminal

Modules: `quantos.terminal` and `quantos/terminal_static/` (index.html, terminal.js, terminal.css).

```
quantos terminal export --data-dir data --out data/terminal   # immutable, content-addressed export
quantos terminal serve  --out data/terminal --port 8765       # http://127.0.0.1:8765/
```

## Design rule

The terminal consumes immutable domain artifacts. It holds no business logic and has no write path. Every number shown was computed and stored by a First Current module; the UI only lays it out, using Perspective's pivot, filter, sort and chart tools on the exported rows.

## Export

- Every `*.duckdb` under the data directory is opened **read-only**, recursively, excluding the export directory itself. A store that cannot be opened (for example, locked by a writer) is listed under `unreadable_sources` rather than skipped silently.
- Every table becomes `tables/<store>__<table>.json`, holding a Perspective schema, a display column order (identifiers and hashes last) and rows.
  - Timestamps become epoch milliseconds (UTC), dates become ISO dates, and DECIMAL and BIGINT values become floats. Non-finite values become null.
  - The top-level scalar fields of a `payload_json` column are promoted to typed columns (numeric strings become floats). Nested values stay as JSON text, and the raw payload is left in DuckDB.
  - Values pass through the Stage 16 secret redactor.
- The row limit per table defaults to 200,000 (the most recent rows are kept); a table cut at the limit is flagged `truncated`.
- `terminal_manifest.json` records:
  - `export_id`: the SHA-256 over (file, sha256) pairs, so the same stores give the same ID;
  - the per-file SHA-256;
  - `authority: "NONE"` and `read_only: true`.

## Workspaces

Every table declared in the codebase maps to a HANDOFF workspace. A unit test enforces that new tables get a mapping:

Markets · Research Radar · Evidence / Claims · Company / Financials · Valuation · Portfolio · Risk · Strategy Lab · PAPER Monitoring · Execution · P&L / TCA · Audit / Lineage. Tables that are not declared in the codebase land in **Other**.

There is no stored Backtests table yet (backtest economics are computed in memory), so that workspace appears once a backtest store exists.

## Browser

- Perspective **3.8.0** (Apache-2.0) is loaded from jsDelivr, pinned to exact files. The entry modules and theme carry **SRI** (`sha384`) hashes.
- `terminal.js` refuses to display an export that does not declare authority `NONE` / read-only.
- `terminal.js` recomputes every table file's **SHA-256** with WebCrypto and checks it against the manifest before loading. The header shows the verified count.
- The datagrid runs in `READ_ONLY` edit mode.
- There are no inline scripts.

## Server

- Loopback only (`127.0.0.1`, `::1`, `localhost`); any other bind address is refused.
- GET and HEAD only; POST returns 501. Directory listings return 404, and path traversal outside the export is refused.
- Headers:
  - `Content-Security-Policy`: `default-src 'none'`; scripts only from self and `cdn.jsdelivr.net`, plus the `wasm-unsafe-eval` and `blob:` sources Perspective's wasm and worker need;
  - `X-Content-Type-Options: nosniff`;
  - `Referrer-Policy: no-referrer`;
  - `Cache-Control: no-store`;
  - `Cross-Origin-Opener-Policy: same-origin`.

## Verification

- `tests/test_terminal.py` covers the export, types, redaction, truncation, workspace coverage, SRI pinning and the server's security properties.
- `QUANTOS_TERMINAL_SMOKE=1` adds a headless Chromium smoke check (`tests/terminal_smoke.mjs`, run with the Node Playwright install). It asserts that the page loads, a table is SHA-256 verified and the grid holds rows. For offline or proxied environments, set `PERSPECTIVE_VENDOR_DIR` to unpacked `npm pack @finos/perspective*@3.8.0` tarballs; the smoke script then serves those exact files, and SRI still verifies them.
- Two issues were found and fixed while building this stage:
  - the client wasm is initialized by the viewer module, so the worker is created only after `perspective-viewer` is defined;
  - a container `en-US@posix` locale breaks `Intl`, so the smoke check sets an explicit locale.
