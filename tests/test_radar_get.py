import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from quantos.research_radar import DiscoveryItem, ResearchRadarStore, make_discovery_id


UTC = timezone.utc


class RadarGetTests(unittest.TestCase):
    def test_get_returns_exact_revision(self):
        updated = datetime(2026, 9, 24, tzinfo=UTC)
        item = DiscoveryItem(
            discovery_id=make_discovery_id(
                provider="arxiv",
                canonical_id="2609.1",
                updated_at=updated,
            ),
            provider="arxiv",
            external_id="2609.1v3",
            canonical_id="2609.1",
            title="Paper",
            summary="Summary",
            authors=("A",),
            categories=("q-fin.PM",),
            published_at=updated,
            updated_at=updated,
            discovered_at=updated,
            source_uri="https://arxiv.org/abs/2609.1v3",
            feed_artifact_id=None,
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = ResearchRadarStore(Path(tmp) / "radar.duckdb")
            store.ingest((item,))
            self.assertEqual(store.get(item.discovery_id), item)
            self.assertIsNone(store.get("missing"))
            store.close()


if __name__ == "__main__":
    unittest.main()
