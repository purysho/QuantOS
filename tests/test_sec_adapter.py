import unittest
from datetime import datetime, timezone

from quantos.adapters.sec import SECAdapterError, SECSubmissionsAdapter


class SECAdapterTests(unittest.TestCase):
    def test_recent_filings_become_timestamped_events(self):
        payload = {
            "cik": "320193",
            "filings": {
                "recent": {
                    "accessionNumber": ["0000320193-26-000001"],
                    "filingDate": ["2026-01-30"],
                    "reportDate": ["2025-12-27"],
                    "acceptanceDateTime": ["2026-01-30T21:01:02.000Z"],
                    "form": ["10-Q"],
                    "primaryDocument": ["aapl-20251227.htm"],
                }
            },
        }
        fetched_at = datetime(2026, 1, 30, 21, 1, 5, tzinfo=timezone.utc)
        events = SECSubmissionsAdapter.events_from_payload(
            payload, fetched_at=fetched_at
        )

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.entity_id, "CIK:0000320193")
        self.assertEqual(event.event_type, "sec.filing.10-Q")
        self.assertEqual(event.knowledge_time, fetched_at)
        self.assertEqual(event.payload["reportDate"], "2025-12-27")

    def test_future_acceptance_time_fails_closed(self):
        payload = {
            "cik": "1",
            "filings": {
                "recent": {
                    "accessionNumber": ["x"],
                    "filingDate": ["2026-01-30"],
                    "acceptanceDateTime": ["2026-01-30T22:00:00Z"],
                    "form": ["8-K"],
                }
            },
        }
        with self.assertRaises(SECAdapterError):
            SECSubmissionsAdapter.events_from_payload(
                payload,
                fetched_at=datetime(2026, 1, 30, 21, 0, tzinfo=timezone.utc),
            )

    def test_live_adapter_requires_identifying_user_agent(self):
        with self.assertRaises(SECAdapterError):
            SECSubmissionsAdapter(user_agent="anonymous")


if __name__ == "__main__":
    unittest.main()
