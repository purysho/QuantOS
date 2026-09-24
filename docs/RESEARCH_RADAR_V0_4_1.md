# Research Radar v0.4.1

## Purpose

Continuously discover potentially relevant quantitative research without treating publication as validation.

## arXiv boundary

The first adapter uses the arXiv metadata API only.

- queries use explicit categories rather than a wildcard;
- the default universe is the nine `q-fin.*` subcategories;
- results are sorted by last update date descending;
- prototype pages are capped at 100 items;
- repeat calls from one adapter instance are separated by at least three seconds;
- raw Atom response bytes may be stored as immutable metadata artifacts;
- PDF/source content is not downloaded or redistributed by this adapter.

## Time model

Each paper carries three distinct clocks:

- `published_at` — first arXiv publication timestamp;
- `updated_at` — timestamp for the retrieved revision;
- `discovered_at` — when First Current observed the feed.

The radar's deterministic discovery identity is based on provider + canonical paper ID + revision update timestamp. New revisions therefore create new discovery records rather than silently rewriting history.

## Trust model

```text
arXiv metadata
    ↓
DISCOVERED
    ↓
radar store
    ↓
triage (future)
    ↓
research catalog QUARANTINE
    ↓
artifact verification
    ↓
claim extraction / counter-evidence
    ↓
trusted claim store
```

Discovery is not evidence and publication is not verification.
