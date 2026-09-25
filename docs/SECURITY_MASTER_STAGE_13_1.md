# Stage 13.1 — Bitemporal Security Master

HANDOFF.md required a full Security Master before serious real-universe backtests. Stage 13.1 builds its identity core.

## Hierarchy

```
Company (company_id, legal name, country)
 └── Security (security_id, kind, share class)   ← permanent identity
      ├── Listing (venue MIC, ticker, currency, primary flag)
      └── Identifiers (ISIN, FIGI, CUSIP, SEDOL, vendor IDs)
Company identifiers (CIK)
```

A ticker is never an identity. It is a time-bounded attribute of a listing, and an unrelated security can reuse it later.

## Bitemporal records

Every record carries:

- `valid_from` / `valid_to`: world validity, with `valid_to` exclusive;
- `knowledge_time`: when First Current learned the fact;
- evidence references;
- a logical `record_key`.

A later version of a key supersedes the earlier one from its knowledge time onward. A `retracted` version withdraws the key.

Every query takes both the date of interest and a knowledge cut-off. A symbol change or correction learned in June 2022 therefore cannot change what a May 2022 research state believed. The tests prove this.

The store is append-only DuckDB and idempotent. It rejects:

- two versions of one key at the same knowledge time;
- reusing a key for another record kind.

## Resolution (fail closed)

- `resolve_ticker(ticker, venue_mic, on, known_at)` finds the security whose listing had that ticker on that date.
- `resolve_identifier(scheme, value, on, known_at)` returns the security, or the company for CIK.
- Two matches raise `AmbiguousResolution`. There is never a guess. An unknown ticker or identifier returns `None`.
- `listings()` gives a security's full ticker history. `primary_listing()` raises if a security has overlapping primary listings.

## Integrity report

`integrity_report(known_at)` is content-addressed. It reports:

- `TICKER_COLLISION`: one venue/ticker maps to two securities over overlapping validity;
- `IDENTIFIER_COLLISION`: one identifier value is assigned to two entities at once;
- `MULTIPLE_PRIMARY_LISTINGS`;
- `DANGLING_REFERENCE`: a security, listing or identifier points at an unknown parent;
- `SECURITY_OUTSIDE_ISSUER_LIFE` and `LISTING_OUTSIDE_SECURITY_LIFE`;
- `INVALID_CHECK_DIGIT`: structural validation of ISIN (Luhn over base-36), CUSIP, SEDOL, FIGI, and the CIK format.

The report never repairs anything.

## Relation to Stage 9.1

The Stage 9.1 universe records already separate `security_id` from ticker. The Security Master is now the authority those IDs should resolve against. Corporate-action economics follow in Stage 13.2.

## Not yet built

- A licensed identifier and reference-data provider feed.
- Exchange calendars.
- Cross-vendor identifier reconciliation.
