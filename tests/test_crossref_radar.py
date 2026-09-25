import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import duckdb

from quantos.adapters.crossref_radar import (
    CrossrefRadarAdapter,
    CrossrefRadarError,
)
from quantos.artifacts import SourceArtifactStore
from quantos.radar_cli import scan_crossref

UTC = timezone.utc
FETCHED = datetime(2026, 9, 25, 9, tzinfo=UTC)


def work(doi="10.1111/jofi.13001", **overrides):
    value = {
        "DOI": doi,
        "type": "journal-article",
        "title": ["  Factor   Momentum and Portfolio Risk "],
        "author": [
            {"given": "Ada", "family": "Quant"},
            {"name": "Research Consortium"},
        ],
        "abstract": "<jats:p>We study <jats:italic>momentum</jats:italic> in factor portfolios.</jats:p>",
        "published": {"date-parts": [[2026, 8]]},
        "indexed": {"date-time": "2026-09-24T10:11:12Z"},
        "container-title": ["The Journal of Finance"],
        "subject": ["Finance"],
        "ISSN": ["0022-1082", "1540-6261"],
        "URL": f"https://doi.org/{doi}",
    }
    value.update(overrides)
    return value


def response(*works):
    return json.dumps(
        {
            "status": "ok",
            "message-type": "work-list",
            "message": {"items": list(works)},
        }
    ).encode()


class CrossrefParsingTests(unittest.TestCase):
    def parse(self, body):
        return CrossrefRadarAdapter.parse_response(body, fetched_at=FETCHED, feed_artifact_id=None)

    def test_work_becomes_discovery_item_without_padding_precision(self):
        (item,) = self.parse(response(work()))
        self.assertEqual(item.provider, "crossref")
        self.assertEqual(item.canonical_id, "10.1111/jofi.13001")
        self.assertEqual(item.title, "Factor Momentum and Portfolio Risk")
        self.assertEqual(item.summary, "We study momentum in factor portfolios.")
        self.assertEqual(item.authors, ("Ada Quant", "Research Consortium"))
        self.assertEqual(item.published_at, datetime(2026, 8, 1, tzinfo=UTC))
        self.assertIn("published-precision:month", item.categories)
        self.assertIn("journal:The Journal of Finance", item.categories)
        self.assertEqual(item.updated_at, datetime(2026, 9, 24, 10, 11, 12, tzinfo=UTC))

    def test_doi_is_case_normalized_for_identity(self):
        (item,) = self.parse(response(work(doi="10.1111/JOFI.13001")))
        self.assertEqual(item.external_id, "10.1111/JOFI.13001")
        self.assertEqual(item.canonical_id, "10.1111/jofi.13001")

    def test_invalid_records_fail_closed(self):
        with self.assertRaises(CrossrefRadarError):
            self.parse(response(work(doi="not-a-doi")))
        with self.assertRaises(CrossrefRadarError):
            self.parse(response(work(title=[])))
        with self.assertRaises(CrossrefRadarError):
            self.parse(response(work(published=None)))
        with self.assertRaises(CrossrefRadarError):
            self.parse(response(work(), work(doi="10.1111/JOFI.13001")))
        with self.assertRaises(CrossrefRadarError):
            self.parse(b'{"status":"error"}')
        with self.assertRaises(CrossrefRadarError):
            self.parse(b"not json")


class CrossrefAdapterTests(unittest.TestCase):
    def test_url_filters_are_explicit(self):
        url = CrossrefRadarAdapter.build_url(
            issns=("0022-1082",),
            from_index_date=date(2026, 9, 1),
            query="momentum",
            max_results=5,
            mailto="ops@example.com",
        )
        params = parse_qs(urlparse(url).query)
        self.assertEqual(
            params["filter"],
            ["from-index-date:2026-09-01,type:journal-article,issn:0022-1082"],
        )
        self.assertEqual(params["mailto"], ["ops@example.com"])
        self.assertEqual(params["query.bibliographic"], ["momentum"])
        for bad in ({"issns": ()}, {"issns": ("22-1082",)}, {"max_results": 101}):
            kwargs = dict(issns=("0022-1082",), from_index_date=date(2026, 9, 1), query=None, max_results=5, mailto="a@b")
            kwargs.update(bad)
            with self.assertRaises(CrossrefRadarError):
                CrossrefRadarAdapter.build_url(**kwargs)

    def test_etiquette_requires_contact_and_throttles(self):
        with self.assertRaises(CrossrefRadarError):
            CrossrefRadarAdapter(mailto="", user_agent="ua")
        calls, sleeps, now = [], [], [100.0]

        def transport(url, headers, timeout):
            calls.append(headers["user-agent"])
            return response(work()), "application/json"

        adapter = CrossrefRadarAdapter(
            mailto="ops@example.com",
            user_agent="QuantOS/test",
            transport=transport,
            clock=lambda: now[0],
            sleeper=sleeps.append,
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = SourceArtifactStore(Path(tmp) / "a", Path(tmp) / "a.duckdb")
            first = adapter.fetch(from_index_date=date(2026, 9, 1), artifact_store=store)
            adapter.fetch(from_index_date=date(2026, 9, 1))
            store.close()
        self.assertIsNotNone(first.feed_artifact)
        self.assertEqual(first.items[0].feed_artifact_id, first.feed_artifact.artifact_id)
        self.assertEqual(sleeps, [1.0])
        self.assertIn("mailto:ops@example.com", calls[0])


class FakeCrossref:
    def fetch(self, **kwargs):
        from quantos.adapters.crossref_radar import CrossrefRadarFetch

        return CrossrefRadarFetch(
            query_url="https://example.test/works",
            fetched_at=FETCHED,
            items=CrossrefRadarAdapter.parse_response(
                response(work()), fetched_at=FETCHED, feed_artifact_id=None
            ),
            feed_artifact=None,
        )


class CrossrefScanTests(unittest.TestCase):
    def test_scan_enters_discovery_workflow_without_promotion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ranked = scan_crossref(
                issns=("0022-1082",),
                from_index_date=date(2026, 9, 1),
                query=None,
                max_results=10,
                radar_db=str(root / "radar.duckdb"),
                artifact_root=str(root / "artifacts"),
                artifact_db=str(root / "artifacts.duckdb"),
                triage_db=str(root / "triage.duckdb"),
                review_db=str(root / "review.duckdb"),
                queue_threshold=0.0,
                adapter=FakeCrossref(),
            )
            self.assertEqual(len(ranked), 1)
            rows = duckdb.connect(str(root / "radar.duckdb")).execute(
                "SELECT provider, canonical_id FROM radar_items"
            ).fetchall()
            self.assertEqual(rows, [("crossref", "10.1111/jofi.13001")])
            queued = duckdb.connect(str(root / "review.duckdb")).execute(
                "SELECT count(*) FROM research_review_queue"
            ).fetchone()[0]
            self.assertEqual(queued, 1)


if __name__ == "__main__":
    unittest.main()
