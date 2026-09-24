import unittest
from dataclasses import replace
from datetime import datetime, timezone

from quantos.ingestion import EventConflictError, IngestionEngine
from quantos.models import Event
from quantos.store import PointInTimeEventStore


UTC = timezone.utc


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.store = PointInTimeEventStore()
        self.engine = IngestionEngine(self.store)
        self.event = Event(
            event_id="provider:1",
            entity_id="ABC",
            event_type="market.trade",
            event_time=datetime(2026, 1, 1, tzinfo=UTC),
            knowledge_time=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
            source_id="feed:test",
            payload={"price": 10.0},
        )

    def test_identical_repoll_is_safe_noop(self):
        first = self.engine.ingest([self.event])
        second = self.engine.ingest([self.event])
        self.assertEqual(first.inserted, 1)
        self.assertEqual(second.skipped_identical, 1)
        self.assertEqual(len(self.store.all()), 1)

    def test_same_id_changed_contents_is_conflict(self):
        self.engine.ingest([self.event])
        changed = replace(self.event, payload={"price": 11.0})
        with self.assertRaises(EventConflictError):
            self.engine.ingest([changed])


if __name__ == "__main__":
    unittest.main()
