import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import duckdb

from quantos.adapters.arxiv_radar import ArxivRadarFetch
from quantos.radar_cli import main, scan_arxiv
from quantos.research_radar import DiscoveryItem, make_discovery_id


UTC = timezone.utc


def discovery():
    updated = datetime(2026, 9, 24, 12, tzinfo=UTC)
    return DiscoveryItem(
        discovery_id=make_discovery_id(
            provider="arxiv",
            canonical_id="2609.01234",
            updated_at=updated,
        ),
        provider="arxiv",
        external_id="2609.01234v1",
        canonical_id="2609.01234",
        title="Transformer Asset Pricing and Asset Embeddings",
        summary="Representation learning for cross-sectional returns.",
        authors=("A Researcher",),
        categories=("q-fin.PM", "stat.ML"),
        published_at=updated,
        updated_at=updated,
        discovered_at=updated,
        source_uri="https://arxiv.org/abs/2609.01234",
        feed_artifact_id=None,
    )


class FakeAdapter:
    def fetch(self, **kwargs):
        return ArxivRadarFetch(
            query_url="https://example.test/feed",
            fetched_at=datetime.now(UTC),
            items=(discovery(),),
            feed_artifact=None,
        )


class RadarCLITests(unittest.TestCase):
    def test_scan_persists_discovery_and_triage_without_promotion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ranked = scan_arxiv(
                categories=("q-fin.PM",),
                max_results=10,
                radar_db=str(root / "radar.duckdb"),
                artifact_root=str(root / "artifacts"),
                artifact_db=str(root / "artifacts.duckdb"),
                triage_db=str(root / "triage.duckdb"),
                print_limit=5,
                adapter=FakeAdapter(),
            )
            self.assertEqual(len(ranked), 1)
            self.assertTrue(ranked[0].attention_band.startswith("ATTENTION_"))
            radar_count = duckdb.connect(str(root / "radar.duckdb")).execute(
                "SELECT count(*) FROM radar_items"
            ).fetchone()[0]
            triage_count = duckdb.connect(str(root / "triage.duckdb")).execute(
                "SELECT count(*) FROM radar_triage"
            ).fetchone()[0]
            self.assertEqual(radar_count, 1)
            self.assertEqual(triage_count, 1)

    def test_cli_defaults_to_finance_categories(self):
        argv = [
            "quantos-radar",
            "scan-arxiv",
            "--max-results",
            "5",
        ]
        with patch("sys.argv", argv), patch(
            "quantos.radar_cli.scan_arxiv",
            return_value=(),
        ) as command:
            self.assertEqual(main(), 0)
            categories = command.call_args.kwargs["categories"]
            self.assertEqual(len(categories), 9)
            self.assertTrue(all(category.startswith("q-fin.") for category in categories))

    def test_cli_accepts_explicit_cross_discipline_categories(self):
        argv = [
            "quantos-radar",
            "scan-arxiv",
            "--category",
            "q-fin.PM",
            "--category",
            "stat.ML",
        ]
        with patch("sys.argv", argv), patch(
            "quantos.radar_cli.scan_arxiv",
            return_value=(),
        ) as command:
            self.assertEqual(main(), 0)
            self.assertEqual(
                command.call_args.kwargs["categories"],
                ("q-fin.PM", "stat.ML"),
            )


if __name__ == "__main__":
    unittest.main()
