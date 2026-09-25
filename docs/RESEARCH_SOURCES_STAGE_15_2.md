# Stage 15.2 — NBER, SSRN and Regulator / Central-Bank Research Sources

Modules: `quantos.adapters.feed_radar` (new) and `quantos.adapters.crossref_radar` (SSRN extension).
CLI: `quantos-radar scan-feeds [--source ID ...]` and `quantos-radar scan-ssrn --query "..." --from-posted-date YYYY-MM-DD`.

All sources are **discovery only**. Items enter the Research Radar, then go through triage and the human review queue, exactly like arXiv and Crossref items. A regulator press release or a working paper never becomes a claim automatically.

## Feed registry (frozen; extend deliberately)

| source_id | Publisher | Kind | Format | Dates |
|---|---|---|---|---|
| `nber-wp` | NBER | WORKING_PAPER | RSS 2.0 (`back.nber.org/rss/new.xml`) | none → first-seen |
| `fed-feds` | Federal Reserve Board | WORKING_PAPER | RSS 2.0 | pubDate |
| `fed-ifdp` | Federal Reserve Board | WORKING_PAPER | RSS 2.0 | pubDate |
| `fed-press` | Federal Reserve Board | REGULATORY_RELEASE | RSS 2.0 | pubDate |
| `fed-speeches` | Federal Reserve Board | SPEECH | RSS 2.0 | pubDate |
| `sec-press` | SEC | REGULATORY_RELEASE | RSS 2.0 | pubDate |
| `bis-wp` | BIS | WORKING_PAPER | RSS 1.0 (RDF) | dc:date |
| `bis-cbspeeches` | BIS | SPEECH | RSS 1.0 (RDF) | dc:date |
| `ecb-wp` | ECB | WORKING_PAPER | RSS 2.0 | pubDate |
| `ecb-press` | ECB | REGULATORY_RELEASE | RSS 2.0 | pubDate |

Atom is supported too, for future sources. `scan-feeds` without `--source` scans the working-paper feeds.

## Rules

- **Identity.** An NBER paper's identity is its working-paper number (`nber:w35758`). Every other item's identity is its link, normalized to https with a lower-case host, no fragment and no duplicate slashes, and namespaced by the feed.
- **Point in time.** Feed dates are converted to UTC. A date without a timezone is refused. NBER's feed carries no dates, so an item is stamped with the first time QuantOS saw it and tagged `published-precision:first-seen`. Re-scans reuse that stamp, so the discovery ID stays stable and nothing is back-dated.
- **Authors.** NBER authors are split from the title's `-- by` byline. Fed FEDS and IFDP authors are taken from the byline at the start of the description. RSS 1.0 uses `dc:creator` and Atom uses `author/name`.
- **Safety.**
  - Only registered feeds are fetched. Every feed host is on the Stage 16 egress allowlist (`back.nber.org` was added), and the kill switch applies.
  - The User-Agent must carry a contact address or URL, as SEC fair-access requires. Requests are rate-limited to at least 1s apart. Redirects are not followed.
  - XML containing a DOCTYPE or ENTITY declaration is refused, which rules out entity expansion. Bodies over 5 MB are refused. Exact response bytes are archived.

## SSRN

SSRN registers its DOIs with Crossref under the prefix `10.2139` as `posted-content`. The Crossref adapter gained `fetch_ssrn` / `build_ssrn_url`, which filter on `prefix:10.2139`, `type:posted-content` and `from-posted-date`. SSRN re-indexes tens of thousands of records a month, so a bibliographic query is mandatory. Posted content has no `published` date, so the `posted` date is used, with its precision tagged. Items carry `repository:SSRN`.

## Verification

- Fixture tests: `tests/test_feed_radar.py`.
- Live smoke check (`QUANTOS_LIVE_FEEDS=1`): all ten feeds parsed on 2026-09-25. This check now runs in the Radar Live Smoke workflow.
