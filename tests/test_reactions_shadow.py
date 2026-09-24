import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from quantos.reactions import MarketReactionEngine, ReactionLedger
from quantos.shadow import ShadowLedger


UTC = timezone.utc


class ReactionAndShadowTests(unittest.TestCase):
    def test_reaction_residual_is_benchmark_adjusted(self):
        reaction = MarketReactionEngine.measure(
            event_id="e1",
            security_id="ABC",
            measured_at=datetime(2026, 9, 24, 15, tzinfo=UTC),
            horizon="1h",
            security_pre=100,
            security_post=105,
            benchmark_pre=200,
            benchmark_post=202,
        )
        self.assertAlmostEqual(reaction.security_return, 0.05)
        self.assertAlmostEqual(reaction.benchmark_return, 0.01)
        self.assertAlmostEqual(reaction.residual_return, 0.04)

    def test_conflicting_reaction_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = ReactionLedger(Path(tmp) / "reactions.duckdb")
            first = MarketReactionEngine.measure(
                event_id="e1",
                security_id="ABC",
                measured_at=datetime(2026, 9, 24, 15, tzinfo=UTC),
                horizon="1h",
                security_pre=100,
                security_post=101,
                benchmark_pre=100,
                benchmark_post=100,
            )
            ledger.record(first)
            conflict = MarketReactionEngine.measure(
                event_id="e1",
                security_id="ABC",
                measured_at=datetime(2026, 9, 24, 16, tzinfo=UTC),
                horizon="1h",
                security_pre=100,
                security_post=102,
                benchmark_pre=100,
                benchmark_post=100,
            )
            with self.assertRaises(ValueError):
                ledger.record(conflict)
            ledger.close()

    def test_edge_health_refuses_small_sample_conclusion(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = ShadowLedger(Path(tmp) / "shadow.duckdb")
            for i in range(5):
                ledger.record(
                    signal_id="earnings-surprise-v1",
                    hypothesis_id=f"h{i}",
                    measured_at=datetime(2026, 9, 24, 15, i, tzinfo=UTC),
                    expected_direction=1,
                    residual_return=0.01,
                )
            health = ledger.health("earnings-surprise-v1")
            self.assertEqual(health.state, "INSUFFICIENT_EVIDENCE")
            self.assertIsNone(health.hit_rate)
            ledger.close()

    def test_edge_health_measures_after_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = ShadowLedger(Path(tmp) / "shadow.duckdb")
            for i in range(20):
                ledger.record(
                    signal_id="test",
                    hypothesis_id=f"h{i}",
                    measured_at=datetime(2026, 9, 24, 15, i, tzinfo=UTC),
                    expected_direction=1,
                    residual_return=0.01 if i < 15 else -0.01,
                )
            health = ledger.health("test")
            self.assertEqual(health.state, "MEASURED")
            self.assertEqual(health.observations, 20)
            self.assertAlmostEqual(health.hit_rate or 0, 0.75)
            ledger.close()


if __name__ == "__main__":
    unittest.main()
