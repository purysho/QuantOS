import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from quantos.artifacts import SourceArtifactStore
from quantos.radar_triage import RadarTriageEngine
from quantos.research_catalog import ResearchCatalog, VerificationStatus
from quantos.research_radar import DiscoveryItem, ResearchRadarStore, make_discovery_id
from quantos.research_review import ResearchReviewError, ResearchReviewService
from quantos.review_queue import ResearchReviewQueue


UTC = timezone.utc


def discovery() -> DiscoveryItem:
    updated = datetime(2026, 9, 24, 12, tzinfo=UTC)
    return DiscoveryItem(
        discovery_id=make_discovery_id(
            provider="arxiv",
            canonical_id="2609.01234",
            updated_at=updated,
        ),
        provider="arxiv",
        external_id="2609.01234v2",
        canonical_id="2609.01234",
        title="A Quantitative Research Paper",
        summary="A descriptive abstract that is not yet trusted evidence.",
        authors=("One Author", "Two Author"),
        categories=("q-fin.PM",),
        published_at=datetime(2026, 9, 20, tzinfo=UTC),
        updated_at=updated,
        discovered_at=datetime(2026, 9, 24, 13, tzinfo=UTC),
        source_uri="https://arxiv.org/abs/2609.01234v2",
        feed_artifact_id="sha256:" + "a" * 64,
    )


class ResearchReviewServiceTests(unittest.TestCase):
    def _fixture(self, root: Path):
        item = discovery()
        radar = ResearchRadarStore(root / "radar.duckdb")
        radar.ingest((item,))
        triage = RadarTriageEngine().score(
            item,
            triaged_at=datetime(2026, 9, 24, 14, tzinfo=UTC),
        )
        queue = ResearchReviewQueue(root / "review.duckdb")
        queued = queue.enqueue(
            discovery=item,
            triage=triage,
            queued_at=datetime(2026, 9, 24, 14, tzinfo=UTC),
        )
        catalog = ResearchCatalog(root / "catalog.duckdb")
        return item, radar, queue, queued, catalog

    def test_queued_item_cannot_skip_review_into_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            item, radar, queue, queued, catalog = self._fixture(Path(tmp))
            with self.assertRaises(ResearchReviewError):
                ResearchReviewService().admit_catalog_candidate(
                    queue_id=queued.queue_id,
                    review_queue=queue,
                    radar_store=radar,
                    catalog=catalog,
                )
            self.assertEqual(catalog.list_status(VerificationStatus.QUARANTINED), ())
            catalog.close()
            queue.close()
            radar.close()

    def test_reviewed_candidate_enters_catalog_quarantined(self):
        with tempfile.TemporaryDirectory() as tmp:
            item, radar, queue, queued, catalog = self._fixture(Path(tmp))
            queue.start_review(
                queue_id=queued.queue_id,
                reviewer="reviewer",
                reviewed_at=datetime(2026, 9, 24, 15, tzinfo=UTC),
            )
            queue.mark_catalog_candidate(
                queue_id=queued.queue_id,
                reviewer="reviewer",
                reviewed_at=datetime(2026, 9, 24, 16, tzinfo=UTC),
                notes="Relevant enough for source verification; no claims accepted.",
            )
            admission = ResearchReviewService().admit_catalog_candidate(
                queue_id=queued.queue_id,
                review_queue=queue,
                radar_store=radar,
                catalog=catalog,
            )
            self.assertEqual(admission.source_id, "ARXIV:2609.01234v2")
            self.assertEqual(admission.catalog_status, VerificationStatus.QUARANTINED)
            ref = catalog.get(admission.source_id)
            self.assertEqual(ref.metadata["authority"], "UNASSESSED")
            self.assertEqual(ref.metadata["supports"], [])
            self.assertIsNone(ref.artifact_id)
            catalog.close()
            queue.close()
            radar.close()

    def test_exact_source_file_verification_is_a_separate_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item, radar, queue, queued, catalog = self._fixture(root)
            queue.start_review(
                queue_id=queued.queue_id,
                reviewer="reviewer",
                reviewed_at=datetime(2026, 9, 24, 15, tzinfo=UTC),
            )
            queue.mark_catalog_candidate(
                queue_id=queued.queue_id,
                reviewer="reviewer",
                reviewed_at=datetime(2026, 9, 24, 16, tzinfo=UTC),
                notes="Candidate only.",
            )
            admission = ResearchReviewService().admit_catalog_candidate(
                queue_id=queued.queue_id,
                review_queue=queue,
                radar_store=radar,
                catalog=catalog,
            )
            source_file = root / "paper.txt"
            source_file.write_bytes(b"exact source bytes used for later claim review")
            artifacts = SourceArtifactStore(
                root / "artifacts",
                root / "artifacts.duckdb",
            )
            verified = ResearchReviewService().verify_source_file(
                source_id=admission.source_id,
                file_path=source_file,
                source_uri=item.source_uri,
                verifier="independent-reviewer",
                notes="Revision ID, title, authors and source URI checked against the catalog record.",
                catalog=catalog,
                artifact_store=artifacts,
                verified_at=datetime(2026, 9, 24, 17, tzinfo=UTC),
            )
            self.assertEqual(verified.reference.status, VerificationStatus.VERIFIED)
            self.assertTrue(verified.artifact.artifact_id.startswith("sha256:"))
            self.assertEqual(
                artifacts.get(verified.artifact.artifact_id),
                source_file.read_bytes(),
            )
            artifacts.close()
            catalog.close()
            queue.close()
            radar.close()


if __name__ == "__main__":
    unittest.main()
