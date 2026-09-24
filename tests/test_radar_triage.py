import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quantos.radar_triage import RadarTriageEngine, RadarTriageStore
from quantos.research_radar import DiscoveryItem, make_discovery_id


UTC = timezone.utc


def item(
    canonical_id,
    title,
    summary,
    *,
    categories=("q-fin.PM",),
    updated=None,
):
    updated = updated or datetime(2026, 9, 24, 12, tzinfo=UTC)
    return DiscoveryItem(
        discovery_id=make_discovery_id(
            provider="arxiv",
            canonical_id=canonical_id,
            updated_at=updated,
        ),
        provider="arxiv",
        external_id=canonical_id + "v1",
        canonical_id=canonical_id,
        title=title,
        summary=summary,
        authors=("A Researcher",),
        categories=categories,
        published_at=updated,
        updated_at=updated,
        discovered_at=updated + timedelta(minutes=1),
        source_uri=f"https://arxiv.org/abs/{canonical_id}",
        feed_artifact_id="sha256:test",
    )


class RadarTriageTests(unittest.TestCase):
    def test_quant_research_gets_more_attention_than_irrelevant_topic(self):
        engine = RadarTriageEngine()
        when = datetime(2026, 9, 24, 13, tzinfo=UTC)
        relevant = item(
            "2609.1",
            "Transformer Asset Pricing with Learned Asset Embeddings",
            "We study cross-sectional returns with representation learning.",
            categories=("q-fin.PM", "stat.ML"),
        )
        irrelevant = item(
            "2609.2",
            "A Study of Marine Sediment Layers",
            "We characterize geological samples.",
            categories=("physics.geo-ph",),
        )
        r1 = engine.score(relevant, triaged_at=when)
        r2 = engine.score(irrelevant, triaged_at=when)
        self.assertGreater(r1.relevance, r2.relevance)
        self.assertGreater(r1.attention_score, r2.attention_score)
        self.assertIn("machine_learning", [m.theme for m in r1.theme_matches])

    def test_near_duplicate_is_less_lexically_novel(self):
        engine = RadarTriageEngine()
        when = datetime(2026, 9, 24, 13, tzinfo=UTC)
        prior = item(
            "2609.1",
            "Robust Portfolio Optimization with Covariance Shrinkage",
            "A robust portfolio method using covariance shrinkage.",
        )
        similar = item(
            "2609.2",
            "Robust Portfolio Optimization using Covariance Shrinkage",
            "We present a robust portfolio method with covariance shrinkage.",
        )
        different = item(
            "2609.3",
            "Causal Identification of Institutional Price Impact",
            "A natural experiment estimates treatment effects in market demand.",
            categories=("q-fin.EC",),
        )
        similar_score = engine.score(
            similar,
            triaged_at=when,
            prior_items=(prior,),
        )
        different_score = engine.score(
            different,
            triaged_at=when,
            prior_items=(prior,),
        )
        self.assertLess(
            similar_score.lexical_novelty,
            different_score.lexical_novelty,
        )

    def test_future_updated_paper_fails_closed(self):
        engine = RadarTriageEngine()
        future = item(
            "2609.9",
            "Future Paper",
            "test",
            updated=datetime(2026, 9, 25, tzinfo=UTC),
        )
        with self.assertRaises(ValueError):
            engine.score(
                future,
                triaged_at=datetime(2026, 9, 24, tzinfo=UTC),
            )

    def test_result_language_is_attention_not_investment_advice(self):
        engine = RadarTriageEngine()
        result = engine.score(
            item(
                "2609.1",
                "Market Microstructure and Price Impact",
                "Order flow and transaction cost estimation.",
                categories=("q-fin.TR",),
            ),
            triaged_at=datetime(2026, 9, 24, 13, tzinfo=UTC),
        )
        self.assertTrue(result.attention_band.startswith("ATTENTION_"))
        self.assertIn("does not", result.caveat)

    def test_triage_store_is_idempotent(self):
        engine = RadarTriageEngine()
        result = engine.score(
            item(
                "2609.1",
                "Backtest Overfitting and Multiple Testing",
                "Replication and publication bias in quantitative research.",
                categories=("q-fin.ST",),
            ),
            triaged_at=datetime(2026, 9, 24, 13, tzinfo=UTC),
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = RadarTriageStore(Path(tmp) / "triage.duckdb")
            store.record(result)
            store.record(result)
            count = store._con.execute(
                "SELECT count(*) FROM radar_triage"
            ).fetchone()[0]
            self.assertEqual(count, 1)
            store.close()


if __name__ == "__main__":
    unittest.main()
