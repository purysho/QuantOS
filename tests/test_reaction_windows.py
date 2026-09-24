import unittest
from datetime import datetime, timedelta, timezone

from quantos.models import Event
from quantos.reaction_windows import (
    ReactionAnchor,
    ReactionWindowEngine,
    ReactionWindowUnavailable,
)
from quantos.store import PointInTimeEventStore


UTC = timezone.utc


def market(
    event_id: str,
    entity: str,
    event_time: datetime,
    knowledge_time: datetime,
    price: float,
) -> Event:
    return Event(
        event_id=event_id,
        entity_id=entity,
        event_type="market.trade",
        event_time=event_time,
        knowledge_time=knowledge_time,
        source_id="market:test",
        payload={"price": price, "size": 1, "venue": "TEST"},
    )


class ReactionWindowTests(unittest.TestCase):
    def _fixture(self):
        store = PointInTimeEventStore()
        focal = Event(
            event_id="news-1",
            entity_id="ABC",
            event_type="earnings.release",
            event_time=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
            knowledge_time=datetime(2026, 1, 1, 12, 0, 10, tzinfo=UTC),
            source_id="company:test",
            payload={},
        )
        rows = [
            market("s0", "ABC", datetime(2026, 1, 1, 11, 59, 59, tzinfo=UTC), datetime(2026, 1, 1, 11, 59, 59, 500000, tzinfo=UTC), 100),
            market("s1", "ABC", datetime(2026, 1, 1, 12, 0, 9, tzinfo=UTC), datetime(2026, 1, 1, 12, 0, 9, 500000, tzinfo=UTC), 102),
            market("s2", "ABC", datetime(2026, 1, 1, 13, 0, 10, tzinfo=UTC), datetime(2026, 1, 1, 13, 0, 10, 100000, tzinfo=UTC), 104),
            market("b0", "BENCH", datetime(2026, 1, 1, 11, 59, 59, tzinfo=UTC), datetime(2026, 1, 1, 11, 59, 59, 500000, tzinfo=UTC), 200),
            market("b1", "BENCH", datetime(2026, 1, 1, 12, 0, 9, tzinfo=UTC), datetime(2026, 1, 1, 12, 0, 9, 500000, tzinfo=UTC), 201),
            market("b2", "BENCH", datetime(2026, 1, 1, 13, 0, 10, tzinfo=UTC), datetime(2026, 1, 1, 13, 0, 10, 100000, tzinfo=UTC), 202),
        ]
        for row in rows:
            store.append(row)
        return store, focal

    def test_knowledge_anchor_uses_price_known_by_system(self):
        store, focal = self._fixture()
        result = ReactionWindowEngine(store).measure(
            event=focal,
            security_id="ABC",
            benchmark_id="BENCH",
            horizon=timedelta(hours=1),
            measured_at=datetime(2026, 1, 1, 13, 0, 11, tzinfo=UTC),
        )
        self.assertEqual(result.anchor, ReactionAnchor.KNOWLEDGE_TIME)
        self.assertEqual(result.security_pre_event_id, "s1")
        self.assertEqual(result.benchmark_pre_event_id, "b1")
        expected = (104 / 102 - 1) - (202 / 201 - 1)
        self.assertAlmostEqual(result.reaction.residual_return, expected)

    def test_public_event_anchor_is_distinct(self):
        store, focal = self._fixture()
        result = ReactionWindowEngine(store).measure(
            event=focal,
            security_id="ABC",
            benchmark_id="BENCH",
            horizon=timedelta(hours=1),
            measured_at=datetime(2026, 1, 1, 13, 0, 11, tzinfo=UTC),
            anchor=ReactionAnchor.EVENT_TIME,
            max_post_lag=timedelta(seconds=15),
        )
        self.assertEqual(result.security_pre_event_id, "s0")
        self.assertEqual(result.benchmark_pre_event_id, "b0")

    def test_rejects_incomplete_horizon(self):
        store, focal = self._fixture()
        with self.assertRaises(ReactionWindowUnavailable):
            ReactionWindowEngine(store).measure(
                event=focal,
                security_id="ABC",
                benchmark_id="BENCH",
                horizon=timedelta(hours=1),
                measured_at=datetime(2026, 1, 1, 12, 30, tzinfo=UTC),
            )

    def test_rejects_stale_pre_price(self):
        store = PointInTimeEventStore()
        focal = Event(
            event_id="e",
            entity_id="ABC",
            event_type="event",
            event_time=datetime(2026, 1, 1, 12, tzinfo=UTC),
            knowledge_time=datetime(2026, 1, 1, 12, tzinfo=UTC),
            source_id="test",
            payload={},
        )
        store.append(
            market(
                "s-old",
                "ABC",
                datetime(2026, 1, 1, 11, 30, tzinfo=UTC),
                datetime(2026, 1, 1, 11, 30, tzinfo=UTC),
                100,
            )
        )
        with self.assertRaises(ReactionWindowUnavailable):
            ReactionWindowEngine(store).measure(
                event=focal,
                security_id="ABC",
                benchmark_id="BENCH",
                horizon=timedelta(minutes=1),
                measured_at=datetime(2026, 1, 1, 12, 2, tzinfo=UTC),
            )


if __name__ == "__main__":
    unittest.main()
