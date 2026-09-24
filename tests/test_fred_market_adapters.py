import unittest
from datetime import datetime, timezone

from quantos.adapters.fred import FREDVintageAdapter
from quantos.adapters.market import MarketEventNormalizer, MarketRecord


UTC = timezone.utc


class FREDAndMarketAdapterTests(unittest.TestCase):
    def test_fred_vintage_is_conservatively_timestamped(self):
        payload = {
            "observations": [
                {
                    "realtime_start": "2026-02-15",
                    "realtime_end": "2026-03-14",
                    "date": "2026-01-01",
                    "value": "3.2",
                }
            ]
        }
        events = FREDVintageAdapter.events_from_payload(
            payload,
            series_id="TEST",
        )
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.payload["value"], 3.2)
        self.assertEqual(event.knowledge_time.date().isoformat(), "2026-02-15")
        self.assertEqual(event.knowledge_time.hour, 23)
        self.assertIn("conservative", event.payload["knowledge_time_resolution"])

    def test_market_clock_skew_fails_closed(self):
        record = MarketRecord(
            security_id="ABC",
            event_time=datetime(2026, 1, 1, 12, 0, 1, tzinfo=UTC),
            received_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
            price=10.0,
            size=100.0,
            venue="TEST",
            source="feed:test",
        )
        with self.assertRaises(ValueError):
            MarketEventNormalizer.normalize(record)

    def test_valid_market_record_becomes_point_in_time_event(self):
        record = MarketRecord(
            security_id="ABC",
            event_time=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
            received_at=datetime(2026, 1, 1, 12, 0, 0, 500000, tzinfo=UTC),
            price=10.0,
            size=100.0,
            venue="TEST",
            source="feed:test",
            sequence=7,
        )
        event = MarketEventNormalizer.normalize(record)
        self.assertEqual(event.event_type, "market.trade")
        self.assertEqual(event.payload["sequence"], 7)


if __name__ == "__main__":
    unittest.main()
