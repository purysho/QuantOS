import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quantos.edge_decay import EdgeDecayMonitor
from quantos.shadow import ShadowLedger


UTC = timezone.utc


class EdgeDecayTests(unittest.TestCase):
    def _record_scores(self, ledger, signal_id, scores):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        for i, score in enumerate(scores):
            ledger.record(
                signal_id=signal_id,
                hypothesis_id=f"h{i}",
                measured_at=start + timedelta(days=i),
                expected_direction=1,
                residual_return=score,
            )

    def test_small_sample_remains_insufficient(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = ShadowLedger(Path(tmp) / "shadow.duckdb")
            self._record_scores(ledger, "s", [0.01] * 30)
            result = EdgeDecayMonitor(ledger).diagnose("s")
            self.assertEqual(result.state, "INSUFFICIENT_EVIDENCE")
            self.assertIsNone(result.standardized_change)
            ledger.close()

    def test_large_recent_deterioration_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = ShadowLedger(Path(tmp) / "shadow.duckdb")
            prior = [0.019, 0.020, 0.021, 0.020] * 5
            recent = [-0.021, -0.020, -0.019, -0.020] * 5
            self._record_scores(ledger, "s", prior + recent)
            result = EdgeDecayMonitor(ledger).diagnose("s")
            self.assertEqual(result.state, "NEGATIVE_DIVERGENCE")
            self.assertLess(result.standardized_change or 0, -2)
            self.assertLess(result.recent.mean_score or 0, 0)
            ledger.close()

    def test_unchanged_distribution_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = ShadowLedger(Path(tmp) / "shadow.duckdb")
            scores = [0.009, 0.010, 0.011, 0.010] * 10
            self._record_scores(ledger, "s", scores)
            result = EdgeDecayMonitor(ledger).diagnose("s")
            self.assertEqual(result.state, "NO_LARGE_DIVERGENCE")
            self.assertAlmostEqual(result.mean_change or 0, 0)
            ledger.close()

    def test_observation_order_is_time_based(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = ShadowLedger(Path(tmp) / "shadow.duckdb")
            base = datetime(2026, 1, 1, tzinfo=UTC)
            ledger.record(
                signal_id="s",
                hypothesis_id="later",
                measured_at=base + timedelta(days=1),
                expected_direction=1,
                residual_return=0.02,
            )
            ledger.record(
                signal_id="s",
                hypothesis_id="earlier",
                measured_at=base,
                expected_direction=1,
                residual_return=0.01,
            )
            observations = ledger.observations("s")
            self.assertEqual(
                [item.hypothesis_id for item in observations],
                ["earlier", "later"],
            )
            ledger.close()


if __name__ == "__main__":
    unittest.main()
