import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from quantos.ledger import ResearchLedger
from quantos.models import Event
from quantos.persistent import DuckDBEventStore
from quantos.service import QuantOS


UTC = timezone.utc


class ExpectationsAndLedgerTests(unittest.TestCase):
    def test_analysis_uses_expectation_known_at_event_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DuckDBEventStore(Path(tmp) / "events.duckdb")
            ledger = ResearchLedger(Path(tmp) / "research.duckdb")
            os = QuantOS(event_store=store, ledger=ledger)

            os.expectations.record(
                entity_id="ABC",
                metric="EPS",
                value=1.00,
                source_id="consensus:v1",
                knowledge_time=datetime(2026, 1, 1, tzinfo=UTC),
            )
            os.expectations.record(
                entity_id="ABC",
                metric="EPS",
                value=1.20,
                source_id="consensus:v2",
                knowledge_time=datetime(2026, 1, 20, tzinfo=UTC),
            )

            event = Event(
                entity_id="ABC",
                event_type="earnings.release",
                event_time=datetime(2026, 1, 15, 12, tzinfo=UTC),
                knowledge_time=datetime(2026, 1, 15, 12, 0, 1, tzinfo=UTC),
                source_id="company:earnings",
                payload={"eps": 1.10},
            )
            os.ingest(event)
            hypothesis, decision = os.analyze_against_latest_expectation(
                event=event,
                metric="EPS",
                observed=1.10,
            )

            self.assertAlmostEqual(hypothesis.expected_value, 1.00)
            self.assertAlmostEqual(hypothesis.surprise, 0.10)
            self.assertTrue(decision.approved_for_shadow)
            self.assertEqual(len(ledger.hypotheses()), 1)
            ledger.close()
            store.close()


if __name__ == "__main__":
    unittest.main()
