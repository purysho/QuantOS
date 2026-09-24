import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from quantos.claims import (
    ClaimCard,
    ClaimStance,
    ClaimStore,
    ClaimType,
    make_claim_id,
)
from quantos.models import EpistemicState
from quantos.research_catalog import (
    ResearchCatalog,
    ResearchCatalogError,
    VerificationStatus,
)


UTC = timezone.utc
ARTIFACT = "sha256:" + "a" * 64


def registry(title="Test Paper"):
    return {
        "library": "test",
        "version": "1",
        "source_count": 1,
        "sources": [
            {
                "id": "TEST-001",
                "domain": "research_methodology",
                "type": "peer_reviewed_paper",
                "authority": "A",
                "title": title,
                "authors": "Researcher",
                "year": 2026,
                "url": "https://example.test/paper",
                "stance": "empirical",
                "supports": ["A scoped historical proposition"],
                "do_not_infer": ["Future profitability"],
            }
        ],
    }


class ResearchCatalogTests(unittest.TestCase):
    def test_import_is_quarantined_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = ResearchCatalog(Path(tmp) / "catalog.duckdb")
            first = catalog.import_registry(registry())
            second = catalog.import_registry(registry())
            self.assertEqual(first.inserted, 1)
            self.assertEqual(second.skipped_identical, 1)
            ref = catalog.get("TEST-001")
            self.assertEqual(ref.status, VerificationStatus.QUARANTINED)
            self.assertIsNone(ref.artifact_id)
            catalog.close()

    def test_changed_metadata_for_same_source_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = ResearchCatalog(Path(tmp) / "catalog.duckdb")
            catalog.import_registry(registry())
            with self.assertRaises(ResearchCatalogError):
                catalog.import_registry(registry(title="Changed title"))
            catalog.close()

    def test_quarantined_source_cannot_promote_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = ResearchCatalog(Path(tmp) / "catalog.duckdb")
            claims = ClaimStore(Path(tmp) / "claims.duckdb")
            catalog.import_registry(registry())
            text = "The paper documents a scoped historical proposition."
            card = ClaimCard(
                text=text,
                claim_type=ClaimType.EMPIRICAL,
                stance=ClaimStance.SUPPORTS,
                epistemic_state=EpistemicState.OBSERVED,
                topic="test",
                source_artifact_ids=(ARTIFACT,),
                locator="p. 1",
                scope={"sample": "test"},
                assumptions=(),
                limitations=("Not a forecast.",),
                as_of=datetime(2026, 9, 24, tzinfo=UTC),
                claim_id=make_claim_id(
                    text=text,
                    source_artifact_ids=(ARTIFACT,),
                    locator="p. 1",
                ),
            )
            with self.assertRaises(ResearchCatalogError):
                catalog.promote_research_claim(card, claim_store=claims)
            claims.close()
            catalog.close()

    def test_verified_artifact_can_promote_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = ResearchCatalog(Path(tmp) / "catalog.duckdb")
            claims = ClaimStore(Path(tmp) / "claims.duckdb")
            catalog.import_registry(registry())
            catalog.verify(
                source_id="TEST-001",
                artifact_id=ARTIFACT,
                verified_at=datetime(2026, 9, 24, tzinfo=UTC),
                verifier="independent-review",
                notes="Artifact and bibliographic identity checked.",
            )
            text = "The paper documents a scoped historical proposition."
            card = ClaimCard(
                text=text,
                claim_type=ClaimType.EMPIRICAL,
                stance=ClaimStance.SUPPORTS,
                epistemic_state=EpistemicState.OBSERVED,
                topic="test",
                source_artifact_ids=(ARTIFACT,),
                locator="p. 1",
                scope={"sample": "test"},
                assumptions=(),
                limitations=("Not a forecast.",),
                as_of=datetime(2026, 9, 24, tzinfo=UTC),
                claim_id=make_claim_id(
                    text=text,
                    source_artifact_ids=(ARTIFACT,),
                    locator="p. 1",
                ),
            )
            catalog.promote_research_claim(card, claim_store=claims)
            self.assertEqual(len(claims.all()), 1)
            claims.close()
            catalog.close()


if __name__ == "__main__":
    unittest.main()
