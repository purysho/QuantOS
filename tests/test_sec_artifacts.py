import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from quantos.adapters.sec_artifacts import (
    SECFilingArtifactError,
    SECFilingArtifactFetcher,
)
from quantos.artifacts import SourceArtifactStore
from quantos.lineage import LineageStore
from quantos.models import Event


UTC = timezone.utc


def filing_event(primary_document: str = "aapl-20251227.htm") -> Event:
    return Event(
        event_id="sec:0000320193:0000320193-26-000001",
        entity_id="CIK:0000320193",
        event_type="sec.filing.10-Q",
        event_time=datetime(2026, 1, 30, 21, 1, 2, tzinfo=UTC),
        knowledge_time=datetime(2026, 1, 30, 21, 1, 5, tzinfo=UTC),
        source_id="sec://submissions/0000320193/0000320193-26-000001",
        payload={
            "accessionNumber": "0000320193-26-000001",
            "primaryDocument": primary_document,
        },
    )


class SECFilingArtifactTests(unittest.TestCase):
    def test_builds_canonical_archive_url(self):
        url = SECFilingArtifactFetcher.primary_document_url(filing_event())
        self.assertEqual(
            url,
            "https://www.sec.gov/Archives/edgar/data/"
            "320193/000032019326000001/aapl-20251227.htm",
        )

    def test_rejects_document_path_traversal(self):
        with self.assertRaises(SECFilingArtifactError):
            SECFilingArtifactFetcher.primary_document_url(
                filing_event("../secrets.htm")
            )

    def test_capture_hashes_bytes_and_links_event(self):
        seen = {}

        def transport(url, headers, timeout, max_bytes):
            seen["url"] = url
            seen["headers"] = headers
            seen["timeout"] = timeout
            seen["max_bytes"] = max_bytes
            return b"<html>filing body</html>", "text/html"

        with tempfile.TemporaryDirectory() as tmp:
            artifacts = SourceArtifactStore(
                Path(tmp) / "artifacts",
                Path(tmp) / "artifacts.duckdb",
            )
            lineage = LineageStore(Path(tmp) / "lineage.duckdb")
            fetcher = SECFilingArtifactFetcher(
                user_agent="QuantOS test@example.com",
                transport=transport,
            )
            event = filing_event()
            ref = fetcher.capture_primary_document(
                event=event,
                artifact_store=artifacts,
                lineage_store=lineage,
            )

            self.assertEqual(
                artifacts.get(ref.artifact_id),
                b"<html>filing body</html>",
            )
            links = lineage.artifacts_for(event.event_id)
            self.assertEqual(len(links), 1)
            self.assertEqual(links[0].artifact_id, ref.artifact_id)
            self.assertEqual(links[0].role, "sec.primary_document")
            self.assertIn("User-Agent", seen["headers"])
            artifacts.close()
            lineage.close()

    def test_changed_bytes_for_same_event_fail_closed(self):
        responses = iter(
            [
                (b"original", "text/html"),
                (b"changed", "text/html"),
            ]
        )

        def transport(url, headers, timeout, max_bytes):
            return next(responses)

        with tempfile.TemporaryDirectory() as tmp:
            artifacts = SourceArtifactStore(
                Path(tmp) / "artifacts",
                Path(tmp) / "artifacts.duckdb",
            )
            lineage = LineageStore(Path(tmp) / "lineage.duckdb")
            fetcher = SECFilingArtifactFetcher(
                user_agent="QuantOS test@example.com",
                transport=transport,
            )
            event = filing_event()
            fetcher.capture_primary_document(
                event=event,
                artifact_store=artifacts,
                lineage_store=lineage,
            )
            with self.assertRaises(ValueError):
                fetcher.capture_primary_document(
                    event=event,
                    artifact_store=artifacts,
                    lineage_store=lineage,
                )
            artifacts.close()
            lineage.close()


if __name__ == "__main__":
    unittest.main()
