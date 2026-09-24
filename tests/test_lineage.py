import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from quantos.lineage import LineageStore


UTC = timezone.utc


class LineageTests(unittest.TestCase):
    def test_exact_relink_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LineageStore(Path(tmp) / "lineage.duckdb")
            first = store.link(
                event_id="e1",
                role="sec.primary_document",
                artifact_id="sha256:a",
                linked_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            second = store.link(
                event_id="e1",
                role="sec.primary_document",
                artifact_id="sha256:a",
                linked_at=datetime(2026, 1, 2, tzinfo=UTC),
            )
            self.assertEqual(first, second)
            self.assertEqual(len(store.artifacts_for("e1")), 1)
            store.close()

    def test_same_event_role_cannot_silently_change_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LineageStore(Path(tmp) / "lineage.duckdb")
            store.link(
                event_id="e1",
                role="sec.primary_document",
                artifact_id="sha256:a",
                linked_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            with self.assertRaises(ValueError):
                store.link(
                    event_id="e1",
                    role="sec.primary_document",
                    artifact_id="sha256:b",
                    linked_at=datetime(2026, 1, 2, tzinfo=UTC),
                )
            store.close()


if __name__ == "__main__":
    unittest.main()
