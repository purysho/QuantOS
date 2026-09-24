import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from quantos.radar_triage import RadarTriageEngine
from quantos.research_radar import DiscoveryItem, make_discovery_id
from quantos.review_queue import ResearchReviewQueue, ReviewStatus


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
        external_id="2609.01234v1",
        canonical_id="2609.01234",
        title="Transformer Asset Pricing and Asset Embeddings",
        summary="Representation learning for cross-sectional returns.",
        authors=("Researcher",),
        categories=("q-fin.PM", "stat.ML"),
        published_at=updated,
        updated_at=updated,
        discovered_at=updated,
        source_uri="https://arxiv.org/abs/2609.01234",
        feed_artifact_id="sha256:" + "a" * 64,
    )


class ReviewQueueTests(unittest.TestCase):
    def _triage(self):
        item = discovery()
        result = RadarTriageEngine().score(
            item,
            triaged_at=datetime(2026, 9, 24, 13, tzinfo=UTC),
        )
        return item, result

    def test_enqueue_is_idempotent_and_means_only_queued(self):
        item, triage = self._triage()
        with tempfile.TemporaryDirectory() as tmp:
            queue = ResearchReviewQueue(Path(tmp) / "review.duckdb")
            when = datetime(2026, 9, 24, 13, tzinfo=UTC)
            first = queue.enqueue(
                discovery=item,
                triage=triage,
                queued_at=when,
            )
            second = queue.enqueue(
                discovery=item,
                triage=triage,
                queued_at=datetime(2026, 9, 24, 14, tzinfo=UTC),
            )
            self.assertEqual(first, second)
            self.assertEqual(first.status, ReviewStatus.QUEUED)
            self.assertIsNone(first.reviewer)
            queue.close()

    def test_review_requires_explicit_reviewer_and_notes(self):
        item, triage = self._triage()
        with tempfile.TemporaryDirectory() as tmp:
            queue = ResearchReviewQueue(Path(tmp) / "review.duckdb")
            queued = queue.enqueue(
                discovery=item,
                triage=triage,
                queued_at=datetime(2026, 9, 24, 13, tzinfo=UTC),
            )
            active = queue.start_review(
                queue_id=queued.queue_id,
                reviewer="independent-reviewer",
                reviewed_at=datetime(2026, 9, 24, 14, tzinfo=UTC),
            )
            self.assertEqual(active.status, ReviewStatus.UNDER_REVIEW)
            with self.assertRaises(ValueError):
                queue.mark_catalog_candidate(
                    queue_id=queued.queue_id,
                    reviewer="independent-reviewer",
                    reviewed_at=datetime(2026, 9, 24, 15, tzinfo=UTC),
                    notes="",
                )
            candidate = queue.mark_catalog_candidate(
                queue_id=queued.queue_id,
                reviewer="independent-reviewer",
                reviewed_at=datetime(2026, 9, 24, 15, tzinfo=UTC),
                notes="Metadata and research relevance reviewed; source still unverified.",
            )
            self.assertEqual(candidate.status, ReviewStatus.CATALOG_CANDIDATE)
            self.assertIn("unverified", candidate.review_notes)
            queue.close()

    def test_unassigned_reviewer_cannot_complete_review(self):
        item, triage = self._triage()
        with tempfile.TemporaryDirectory() as tmp:
            queue = ResearchReviewQueue(Path(tmp) / "review.duckdb")
            queued = queue.enqueue(
                discovery=item,
                triage=triage,
                queued_at=datetime(2026, 9, 24, 13, tzinfo=UTC),
            )
            queue.start_review(
                queue_id=queued.queue_id,
                reviewer="reviewer-a",
                reviewed_at=datetime(2026, 9, 24, 14, tzinfo=UTC),
            )
            with self.assertRaises(ValueError):
                queue.dismiss(
                    queue_id=queued.queue_id,
                    reviewer="reviewer-b",
                    reviewed_at=datetime(2026, 9, 24, 15, tzinfo=UTC),
                    notes="Not relevant.",
                )
            queue.close()


if __name__ == "__main__":
    unittest.main()
