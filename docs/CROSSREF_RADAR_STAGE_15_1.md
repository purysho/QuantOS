# Stage 15.1 — Crossref DOI Research Radar

The Research Radar only proved live arXiv discovery. HANDOFF.md asked for a broader, verified research library. Stage 15.1 adds peer-reviewed journal discovery through the Crossref REST API. It uses the same trust workflow.

```
Crossref work metadata → immutable response artifact → DiscoveryItem (provider=crossref)
→ deterministic triage → review queue → exact-source verification → Claim Workbench
```

Nothing becomes knowledge on discovery. `RADAR_TRUST DISCOVERY_ONLY` still applies.

## Usage

```bash
export CROSSREF_MAILTO=you@example.com     # required by Crossref etiquette
quantos-radar scan-crossref --from-index-date 2026-09-01 --max-results 20
quantos-radar scan-crossref --issn 0022-1082 --query "momentum" --from-index-date 2026-01-01
```

The default journal set, by ISSN, is:

- Journal of Finance;
- Journal of Financial Economics;
- Review of Financial Studies;
- JFQA;
- Journal of Portfolio Management;
- Financial Analysts Journal.

Extend it deliberately.

## Etiquette and provenance

- A contact `mailto` is mandatory. It selects Crossref's polite pool and lets Crossref reach the operator.
- Requests are throttled to at most one per second.
- The exact response bytes are stored as a SHA-256 source artifact, and every item links to it.

## Parsing rules (fail closed)

- A DOI must match `10.<registrant>/<suffix>`. Identity is the lower-cased DOI, because DOIs are case-insensitive. The DOI as published is kept as `external_id`.
- A missing DOI, title, index time or publication date raises an error.
- A DOI repeated within one response raises an error.
- A malformed envelope or malformed JSON raises an error.
- JATS markup is stripped from abstracts. A missing abstract stays empty; nothing is written in its place.
- Partial publication dates (year or year-month) are **not padded silently**. The first day is used only because a timestamp is needed, and the real precision is recorded as a `published-precision:year|month|day` category.
- `updated_at` is Crossref's index time.

## Important semantic note

`from-index-date` filters on when Crossref (re)indexed a record, not on when it was published. Crossref reindexes old works, so a scan can surface classic papers. That is useful for seeding the canonical library, but the difference must be kept in mind. Publication time and discovery time remain separate fields.

## Not in scope yet

- NBER.
- SSRN.
- Regulator and central-bank publications.
- Full-text retrieval.
- Automatic linking of DOIs to arXiv preprints.
