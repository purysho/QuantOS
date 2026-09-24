import unittest
from datetime import datetime, timezone

from quantos.models import Event
from quantos.store import PointInTimeEventStore


UTC = timezone.utc


class PointInTimeTests(unittest.TestCase):
    def test_future_revision_does_not_leak_backwards(self):
        store = PointInTimeEventStore()
        original = Event(
            entity_id="ABC",
            event_type="fundamental.revenue",
            event_time=datetime(2026, 2, 1, tzinfo=UTC),
            knowledge_time=datetime(2026, 2, 1, 1, tzinfo=UTC),
            source_id="filing:1",
            payload={"value": 100},
        )
        revision = Event(
            entity_id="ABC",
            event_type="fundamental.revenue",
            event_time=datetime(2026, 4, 1, tzinfo=UTC),
            knowledge_time=datetime(2026, 4, 1, 1, tzinfo=UTC),
            source_id="filing:2",
            payload={"value": 110},
        )
        store.append(original)
        store.append(revision)

        feb_state = store.known_as_of(datetime(2026, 2, 15, tzinfo=UTC), entity_id="ABC")
        self.assertEqual([e.payload["value"] for e in feb_state], [100])

    def test_duplicate_event_ids_fail_closed(self):
        store = PointInTimeEventStore()
        event = Event(
            entity_id="ABC",
            event_type="price.close",
            event_time=datetime(2026, 1, 1, tzinfo=UTC),
            knowledge_time=datetime(2026, 1, 1, tzinfo=UTC),
            source_id="market:test",
            payload={"value": 10},
        )
        store.append(event)
        with self.assertRaises(ValueError):
            store.append(event)


if __name__ == "__main__":
    unittest.main()
