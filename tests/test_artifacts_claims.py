import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from quantos.artifacts import SourceArtifactStore
from quantos.claims import (
    ClaimCard,
    ClaimStance,
    ClaimStore,
    ClaimType,
    EvidenceRetriever,
    make_claim_id,
)
from quantos.models import EpistemicState


UTC = timezone.utc


class ArtifactAndClaimTests(unittest.TestCase):
    def test_artifact_is_content_addressed_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SourceArtifactStore(
                Path(tmp) / "artifacts",
                Path(tmp) / "artifacts.duckdb",
            )
            when = datetime(2026, 9, 24, tzinfo=UTC)
            one = store.put(
                source_uri="https://example.test/source",
                content=b"evidence",
                fetched_at=when,
                media_type="text/plain",
            )
            two = store.put(
                source_uri="https://example.test/source",
                content=b"evidence",
                fetched_at=when,
                media_type="text/plain",
            )
            self.assertEqual(one.artifact_id, two.artifact_id)
            self.assertEqual(one.observation_id, two.observation_id)
            self.assertEqual(store.get(one.artifact_id), b"evidence")
            store.close()

    def test_retrieval_separates_support_and_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            claim_store = ClaimStore(Path(tmp) / "claims.duckdb")
            now = datetime(2026, 9, 24, tzinfo=UTC)
            for text, stance, artifact in [
                (
                    "Historical momentum continuation was documented in the studied equity sample.",
                    ClaimStance.SUPPORTS,
                    "sha256:a",
                ),
                (
                    "Momentum evidence can decay after publication and implementation costs matter.",
                    ClaimStance.LIMITS,
                    "sha256:b",
                ),
            ]:
                sources = (artifact,)
                card = ClaimCard(
                    text=text,
                    claim_type=ClaimType.EMPIRICAL,
                    stance=stance,
                    epistemic_state=EpistemicState.OBSERVED,
                    topic="momentum",
                    source_artifact_ids=sources,
                    locator="test",
                    scope={"population": "test"},
                    assumptions=(),
                    limitations=(),
                    as_of=now,
                    claim_id=make_claim_id(
                        text=text,
                        source_artifact_ids=sources,
                        locator="test",
                    ),
                )
                claim_store.add(card)

            bundle = EvidenceRetriever(claim_store).bundle("momentum historical")
            self.assertEqual(len(bundle.supporting), 1)
            self.assertEqual(len(bundle.limiting), 1)
            self.assertEqual(len(bundle.contradicting), 0)
            claim_store.close()


if __name__ == "__main__":
    unittest.main()
