import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from quantos.artifacts import SourceArtifactStore
from quantos.claim_workbench import (
    ClaimDraftStatus,
    ClaimWorkbench,
    ClaimWorkbenchError,
)
from quantos.claims import ClaimStance, ClaimStore, ClaimType
from quantos.models import EpistemicState
from quantos.research_catalog import ResearchCatalog


UTC = timezone.utc


def verified_catalog(root: Path, *, artifact_bytes: bytes = b"paper source"):
    catalog = ResearchCatalog(root / "catalog.duckdb")
    metadata = {
        "id": "TEST:PAPER:v1",
        "domain": "asset_pricing",
        "type": "paper",
        "authority": "A",
        "title": "Test Paper",
        "authors": "Researcher",
        "year": 2026,
        "url": "https://example.test/paper",
        "stance": "empirical",
        "supports": [],
        "do_not_infer": ["Future profitability."],
    }
    catalog.import_registry(
        {
            "library": "test",
            "version": "1",
            "source_count": 1,
            "sources": [metadata],
        }
    )
    artifacts = SourceArtifactStore(
        root / "artifacts",
        root / "artifacts.duckdb",
    )
    ref = artifacts.put(
        source_uri="https://example.test/paper",
        content=artifact_bytes,
        fetched_at=datetime(2026, 9, 24, 12, tzinfo=UTC),
        media_type="text/plain",
    )
    catalog.verify(
        source_id="TEST:PAPER:v1",
        artifact_id=ref.artifact_id,
        verified_at=datetime(2026, 9, 24, 12, 5, tzinfo=UTC),
        verifier="source-reviewer",
        notes="Source identity checked.",
    )
    return catalog, artifacts, ref.artifact_id


def draft_kwargs():
    return {
        "source_id": "TEST:PAPER:v1",
        "text": "The studied sample showed a positive association between the factor and subsequent returns.",
        "claim_type": ClaimType.EMPIRICAL,
        "stance": ClaimStance.SUPPORTS,
        "epistemic_state": EpistemicState.OBSERVED,
        "topic": "factor returns",
        "locator": "Section 4, Table 3",
        "scope": {
            "population": "study sample",
            "period": "historical study window",
        },
        "assumptions": ("Published table is interpreted as reported.",),
        "limitations": (
            "Historical association does not establish future profitability.",
        ),
        "as_of": datetime(2026, 9, 24, 12, tzinfo=UTC),
        "drafter": "analyst-a",
        "created_at": datetime(2026, 9, 24, 13, tzinfo=UTC),
    }


class ClaimWorkbenchTests(unittest.TestCase):
    def test_quarantined_source_cannot_create_claim_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = ResearchCatalog(root / "catalog.duckdb")
            catalog.import_registry(
                {
                    "library": "test",
                    "version": "1",
                    "source_count": 1,
                    "sources": [
                        {
                            "id": "TEST:PAPER:v1",
                            "domain": "test",
                            "type": "paper",
                            "authority": "UNASSESSED",
                            "title": "Paper",
                            "authors": "A",
                            "year": 2026,
                            "url": "https://example.test",
                            "stance": "UNASSESSED",
                            "supports": [],
                            "do_not_infer": ["Anything."],
                        }
                    ],
                }
            )
            workbench = ClaimWorkbench(root / "workbench.duckdb")
            with self.assertRaises(ClaimWorkbenchError):
                workbench.create_draft(catalog=catalog, **draft_kwargs())
            workbench.close()
            catalog.close()

    def test_empirical_claim_requires_locator_scope_and_limitations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog, artifacts, _ = verified_catalog(root)
            workbench = ClaimWorkbench(root / "workbench.duckdb")
            for override in (
                {"locator": ""},
                {"scope": {}},
                {"limitations": ()},
            ):
                kwargs = draft_kwargs()
                kwargs.update(override)
                with self.assertRaises(ClaimWorkbenchError):
                    workbench.create_draft(catalog=catalog, **kwargs)
            workbench.close()
            artifacts.close()
            catalog.close()

    def test_drafter_cannot_review_own_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog, artifacts, _ = verified_catalog(root)
            workbench = ClaimWorkbench(root / "workbench.duckdb")
            draft = workbench.create_draft(catalog=catalog, **draft_kwargs())
            with self.assertRaises(ClaimWorkbenchError):
                workbench.submit_for_review(
                    draft_id=draft.draft_id,
                    reviewer="analyst-a",
                    reviewed_at=datetime(2026, 9, 24, 14, tzinfo=UTC),
                )
            workbench.close()
            artifacts.close()
            catalog.close()

    def test_approval_requires_counter_evidence_notes_and_does_not_promote(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog, artifacts, _ = verified_catalog(root)
            workbench = ClaimWorkbench(root / "workbench.duckdb")
            claims = ClaimStore(root / "claims.duckdb")
            draft = workbench.create_draft(catalog=catalog, **draft_kwargs())
            workbench.submit_for_review(
                draft_id=draft.draft_id,
                reviewer="reviewer-b",
                reviewed_at=datetime(2026, 9, 24, 14, tzinfo=UTC),
            )
            with self.assertRaises(ClaimWorkbenchError):
                workbench.approve(
                    draft_id=draft.draft_id,
                    reviewer="reviewer-b",
                    reviewed_at=datetime(2026, 9, 24, 15, tzinfo=UTC),
                    counter_evidence_notes="",
                    review_notes="Looks scoped correctly.",
                )
            approved = workbench.approve(
                draft_id=draft.draft_id,
                reviewer="reviewer-b",
                reviewed_at=datetime(2026, 9, 24, 15, tzinfo=UTC),
                counter_evidence_notes=(
                    "Checked the paper's robustness section and searched the trusted corpus for limiting/contradicting evidence."
                ),
                review_notes="Approved only for the stated sample and historical period.",
            )
            self.assertEqual(approved.status, ClaimDraftStatus.APPROVED)
            self.assertEqual(claims.all(), ())
            claims.close()
            workbench.close()
            artifacts.close()
            catalog.close()

    def test_approved_claim_promotes_only_after_artifact_recheck(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog, artifacts, artifact_id = verified_catalog(root)
            workbench = ClaimWorkbench(root / "workbench.duckdb")
            claims = ClaimStore(root / "claims.duckdb")
            draft = workbench.create_draft(catalog=catalog, **draft_kwargs())
            workbench.submit_for_review(
                draft_id=draft.draft_id,
                reviewer="reviewer-b",
                reviewed_at=datetime(2026, 9, 24, 14, tzinfo=UTC),
            )
            workbench.approve(
                draft_id=draft.draft_id,
                reviewer="reviewer-b",
                reviewed_at=datetime(2026, 9, 24, 15, tzinfo=UTC),
                counter_evidence_notes="Counter-evidence search completed and limitations retained.",
                review_notes="Scoped claim accepted.",
            )
            card = workbench.promote(
                draft_id=draft.draft_id,
                catalog=catalog,
                claim_store=claims,
            )
            self.assertEqual(card.source_artifact_ids, (artifact_id,))
            self.assertEqual(len(claims.all()), 1)
            promoted = workbench.get(draft.draft_id)
            self.assertEqual(promoted.status, ClaimDraftStatus.PROMOTED)
            self.assertEqual(promoted.promoted_claim_id, card.claim_id)
            claims.close()
            workbench.close()
            artifacts.close()
            catalog.close()

    def test_promotion_fails_if_reviewed_artifact_does_not_match_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original_catalog, original_artifacts, _ = verified_catalog(
                root / "original",
                artifact_bytes=b"original",
            )
            workbench = ClaimWorkbench(root / "workbench.duckdb")
            claims = ClaimStore(root / "claims.duckdb")
            draft = workbench.create_draft(
                catalog=original_catalog,
                **draft_kwargs(),
            )
            workbench.submit_for_review(
                draft_id=draft.draft_id,
                reviewer="reviewer-b",
                reviewed_at=datetime(2026, 9, 24, 14, tzinfo=UTC),
            )
            workbench.approve(
                draft_id=draft.draft_id,
                reviewer="reviewer-b",
                reviewed_at=datetime(2026, 9, 24, 15, tzinfo=UTC),
                counter_evidence_notes="Completed.",
                review_notes="Approved.",
            )

            replacement_catalog, replacement_artifacts, _ = verified_catalog(
                root / "replacement",
                artifact_bytes=b"different revision bytes",
            )
            with self.assertRaises(ClaimWorkbenchError):
                workbench.promote(
                    draft_id=draft.draft_id,
                    catalog=replacement_catalog,
                    claim_store=claims,
                )
            self.assertEqual(claims.all(), ())

            replacement_artifacts.close()
            replacement_catalog.close()
            claims.close()
            workbench.close()
            original_artifacts.close()
            original_catalog.close()


if __name__ == "__main__":
    unittest.main()
