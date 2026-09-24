import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from quantos.adapters.arxiv_radar import (
    ARXIV_FINANCE_CATEGORIES,
    ArxivRadarAdapter,
    ArxivRadarError,
)
from quantos.artifacts import SourceArtifactStore
from quantos.research_radar import ResearchRadarStore


UTC = timezone.utc

FEED = b'''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Test feed</title>
  <entry>
    <id>http://arxiv.org/abs/2609.01234v2</id>
    <updated>2026-09-24T12:30:00Z</updated>
    <published>2026-09-20T10:00:00Z</published>
    <title>  A New   Asset Pricing Model </title>
    <summary> We test a model across several markets. </summary>
    <author><name>Researcher One</name></author>
    <author><name>Researcher Two</name></author>
    <category term="q-fin.PM"/>
    <category term="stat.ML"/>
  </entry>
</feed>
'''


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class ArxivRadarTests(unittest.TestCase):
    def test_finance_categories_are_explicit(self):
        self.assertEqual(len(ARXIV_FINANCE_CATEGORIES), 9)
        self.assertTrue(all(category.startswith("q-fin.") for category in ARXIV_FINANCE_CATEGORIES))

    def test_feed_parses_revision_and_metadata(self):
        items = ArxivRadarAdapter.parse_feed(
            FEED,
            fetched_at=datetime(2026, 9, 24, 13, tzinfo=UTC),
            source_uri="https://export.arxiv.org/api/query",
            feed_artifact_id="sha256:test",
        )
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.external_id, "2609.01234v2")
        self.assertEqual(item.canonical_id, "2609.01234")
        self.assertEqual(item.title, "A New Asset Pricing Model")
        self.assertEqual(item.authors, ("Researcher One", "Researcher Two"))
        self.assertIn("q-fin.PM", item.categories)
        self.assertEqual(item.feed_artifact_id, "sha256:test")

    def test_fetch_archives_raw_atom_metadata_and_ingests_idempotently(self):
        def transport(url, headers, timeout):
            return FEED, "application/atom+xml"

        with tempfile.TemporaryDirectory() as tmp:
            artifacts = SourceArtifactStore(
                Path(tmp) / "artifacts",
                Path(tmp) / "artifacts.duckdb",
            )
            radar = ResearchRadarStore(Path(tmp) / "radar.duckdb")
            adapter = ArxivRadarAdapter(
                user_agent="First Current Quant OS test@example.com",
                transport=transport,
            )
            fetched = adapter.fetch(
                categories=("q-fin.PM",),
                max_results=10,
                artifact_store=artifacts,
            )
            self.assertIsNotNone(fetched.feed_artifact)
            self.assertEqual(
                artifacts.get(fetched.feed_artifact.artifact_id),
                FEED,
            )
            first = radar.ingest(fetched.items)
            second = radar.ingest(fetched.items)
            self.assertEqual(first.inserted, 1)
            self.assertEqual(second.skipped_identical, 1)
            self.assertEqual(len(radar.latest(provider="arxiv")), 1)
            radar.close()
            artifacts.close()

    def test_repeated_requests_enforce_three_second_interval(self):
        fake = FakeClock()

        def transport(url, headers, timeout):
            return FEED, "application/atom+xml"

        adapter = ArxivRadarAdapter(
            user_agent="First Current Quant OS test@example.com",
            transport=transport,
            clock=fake.clock,
            sleeper=fake.sleep,
        )
        adapter.fetch(categories=("q-fin.PM",), max_results=1)
        adapter.fetch(categories=("q-fin.PM",), max_results=1)
        self.assertEqual(len(fake.sleeps), 1)
        self.assertGreaterEqual(fake.sleeps[0], 3.0)

    def test_query_rejects_wildcards_and_oversized_pages(self):
        with self.assertRaises(ArxivRadarError):
            ArxivRadarAdapter.build_url(
                categories=("q-fin.*",),
                max_results=10,
            )
        with self.assertRaises(ArxivRadarError):
            ArxivRadarAdapter.build_url(
                categories=("q-fin.PM",),
                max_results=101,
            )


class RadarStoreTests(unittest.TestCase):
    def test_revision_history_is_preserved(self):
        first_feed = FEED.replace(
            b"2609.01234v2",
            b"2609.01234v1",
        ).replace(
            b"2026-09-24T12:30:00Z",
            b"2026-09-20T10:00:00Z",
        )
        first = ArxivRadarAdapter.parse_feed(
            first_feed,
            fetched_at=datetime(2026, 9, 20, 11, tzinfo=UTC),
            source_uri="feed:first",
            feed_artifact_id="sha256:first",
        )[0]
        second = ArxivRadarAdapter.parse_feed(
            FEED,
            fetched_at=datetime(2026, 9, 24, 13, tzinfo=UTC),
            source_uri="feed:second",
            feed_artifact_id="sha256:second",
        )[0]

        with tempfile.TemporaryDirectory() as tmp:
            store = ResearchRadarStore(Path(tmp) / "radar.duckdb")
            store.ingest((first, second))
            versions = store.versions(
                provider="arxiv",
                canonical_id="2609.01234",
            )
            self.assertEqual(
                [item.external_id for item in versions],
                ["2609.01234v1", "2609.01234v2"],
            )
            store.close()


if __name__ == "__main__":
    unittest.main()
