import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from quantos.models import Event
from quantos.persistent import DuckDBEventStore


UTC = timezone.utc


class PersistentStoreTests(unittest.TestCase):
    def test_reopen_preserves_point_in_time_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "events.duckdb"
            store = DuckDBEventStore(db)
            first = Event(
                entity_id="ABC",
                event_type="fundamental.revenue",
                event_time=datetime(2026, 1, 31, tzinfo=UTC),
                knowledge_time=datetime(2026, 2, 1, tzinfo=UTC),
                source_id="filing:first",
                payload={"value": 100},
            )
            revision = Event(
                entity_id="ABC",
                event_type="fundamental.revenue",
                event_time=datetime(2026, 3, 31, tzinfo=UTC),
                knowledge_time=datetime(2026, 4, 1, tzinfo=UTC),
                source_id="filing:revision",
                payload={"value": 110},
            )
            store.append(first)
            store.append(revision)
            store.close()

            reopened = DuckDBEventStore(db)
            rows = reopened.known_as_of(
                datetime(2026, 3, 1, tzinfo=UTC),
                entity_id="ABC",
            )
            self.assertEqual([row.payload["value"] for row in rows], [100])
            reopened.close()

    def test_parquet_export_is_queryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "events.duckdb"
            parquet = Path(tmp) / "events.parquet"
            store = DuckDBEventStore(db)
            store.append(
                Event(
                    entity_id="ABC",
                    event_type="market.trade",
                    event_time=datetime(2026, 1, 1, tzinfo=UTC),
                    knowledge_time=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
                    source_id="market:test",
                    payload={"price": 42.0},
                )
            )
            store.export_parquet(parquet)
            count = duckdb.connect().execute(
                "SELECT count(*) FROM read_parquet(?)", [str(parquet)]
            ).fetchone()[0]
            self.assertEqual(count, 1)
            store.close()


if __name__ == "__main__":
    unittest.main()
