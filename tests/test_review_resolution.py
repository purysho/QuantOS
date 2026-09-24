import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from quantos.claim_evidence_graph import ClaimEvidenceGraph
from quantos.claims import (
    ClaimCard,
    ClaimStance,
    ClaimStore,
    ClaimType,
    make_claim_id,
)
from quantos.models import EpistemicState
from quantos.professional_reviews import (
    ProfessionalReviewLedger,
    ProfessionalRole,
    ReviewDisposition,
    ReviewPanelState,
    ROLE_CHECKS,
)
from quantos.reasoning_dossier import EvidenceDossierBuilder


UTC = timezone.utc


def card():
    text = "A historical relation was observed."
    sources = ("sha256:a",)
    return ClaimCard(
        text=text,
        claim_type=ClaimType.EMPIRICAL,
        stance=ClaimStance.SUPPORTS,
        epistemic_state=EpistemicState.OBSERVED,
        topic="research",
        source_artifact_ids=sources,
        locator="Table 1",
        scope={"sample": "historical"},
        assumptions=(),
        limitations=("May not persist.",),
        as_of=datetime(2026, 9, 24, tzinfo=UTC),
        claim_id=make_claim_id(
            text=text,
            source_artifact_ids=sources,
            locator="Table 1",
        ),
    )


def checks(role):
    return {key: f"Reviewed {key}." for key in ROLE_CHECKS[role]}


class ReviewResolutionTests(unittest.TestCase):
    def _fixture(self, root):
        claims = ClaimStore(root / "claims.duckdb")
        graph = ClaimEvidenceGraph(root / "evidence.duckdb")
        focal = card()
        claims.add(focal)
        dossier = EvidenceDossierBuilder().build(
            focal.claim_id,
            claims=claims,
            evidence_graph=graph,
        )
        ledger = ProfessionalReviewLedger(root / "reviews.duckdb")
        return claims, graph, dossier, ledger

    def test_other_reviewer_cannot_resolve_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            claims, graph, dossier, ledger = self._fixture(Path(tmp))
            review = ledger.record(
                dossier=dossier,
                role=ProfessionalRole.RISK_OFFICER,
                reviewer="risk-a",
                recorded_at=datetime(2026, 9, 24, 16, tzinfo=UTC),
                disposition=ReviewDisposition.BLOCKING_OBJECTION,
                check_notes=checks(ProfessionalRole.RISK_OFFICER),
                findings=("Risk review complete.",),
                objections=("Tail mechanism is unbounded.",),
            )
            with self.assertRaises(ValueError):
                ledger.resolve_review(
                    review_id=review.review_id,
                    resolver="pm-b",
                    resolved_at=datetime(2026, 9, 24, 17, tzinfo=UTC),
                    notes="Looks fine now.",
                    evidence_references=("stress-run:1",),
                )
            ledger.close()
            graph.close()
            claims.close()

    def test_resolution_requires_evidence_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            claims, graph, dossier, ledger = self._fixture(Path(tmp))
            review = ledger.record(
                dossier=dossier,
                role=ProfessionalRole.EXECUTION_TRADER,
                reviewer="trader-a",
                recorded_at=datetime(2026, 9, 24, 16, tzinfo=UTC),
                disposition=ReviewDisposition.CONDITIONAL,
                check_notes=checks(ProfessionalRole.EXECUTION_TRADER),
                findings=("Execution review complete.",),
                required_followups=("Measure slippage.",),
            )
            with self.assertRaises(ValueError):
                ledger.resolve_review(
                    review_id=review.review_id,
                    resolver="trader-a",
                    resolved_at=datetime(2026, 9, 24, 17, tzinfo=UTC),
                    notes="Slippage measured.",
                    evidence_references=(),
                )
            ledger.close()
            graph.close()
            claims.close()

    def test_original_block_remains_auditable_after_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            claims, graph, dossier, ledger = self._fixture(Path(tmp))
            reviews = []
            for role in ProfessionalRole:
                disposition = (
                    ReviewDisposition.BLOCKING_OBJECTION
                    if role is ProfessionalRole.RISK_OFFICER
                    else ReviewDisposition.NO_OBJECTION
                )
                reviews.append(
                    ledger.record(
                        dossier=dossier,
                        role=role,
                        reviewer=f"{role.value.lower()}-reviewer",
                        recorded_at=datetime(2026, 9, 24, 16, tzinfo=UTC),
                        disposition=disposition,
                        check_notes=checks(role),
                        findings=("Review complete.",),
                        objections=(
                            ("Stress loss not bounded.",)
                            if disposition is ReviewDisposition.BLOCKING_OBJECTION
                            else ()
                        ),
                    )
                )
            blocked = ledger.panel(dossier)
            self.assertEqual(
                blocked.state,
                ReviewPanelState.BLOCKING_OBJECTION_PRESENT,
            )
            risk_review = next(
                review
                for review in reviews
                if review.role is ProfessionalRole.RISK_OFFICER
            )
            resolution = ledger.resolve_review(
                review_id=risk_review.review_id,
                resolver=risk_review.reviewer,
                resolved_at=datetime(2026, 9, 24, 18, tzinfo=UTC),
                notes="Stress test added and the stated loss boundary is now explicit.",
                evidence_references=("stress-run:2026-09-24:001",),
            )
            panel = ledger.panel(dossier)
            self.assertEqual(panel.state, ReviewPanelState.REVIEW_SET_COMPLETE)
            self.assertEqual(panel.blocking_review_ids, ())
            self.assertIn(risk_review.review_id, panel.resolved_review_ids)
            preserved = ledger.get(risk_review.review_id)
            self.assertEqual(
                preserved.disposition,
                ReviewDisposition.BLOCKING_OBJECTION,
            )
            self.assertEqual(
                preserved.objections,
                ("Stress loss not bounded.",),
            )
            self.assertEqual(
                ledger.resolution_for(risk_review.review_id),
                resolution,
            )
            ledger.close()
            graph.close()
            claims.close()

    def test_no_objection_review_cannot_be_given_fake_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            claims, graph, dossier, ledger = self._fixture(Path(tmp))
            review = ledger.record(
                dossier=dossier,
                role=ProfessionalRole.RED_TEAM,
                reviewer="red-a",
                recorded_at=datetime(2026, 9, 24, 16, tzinfo=UTC),
                disposition=ReviewDisposition.NO_OBJECTION,
                check_notes=checks(ProfessionalRole.RED_TEAM),
                findings=("No blocking objection recorded.",),
            )
            with self.assertRaises(ValueError):
                ledger.resolve_review(
                    review_id=review.review_id,
                    resolver="red-a",
                    resolved_at=datetime(2026, 9, 24, 17, tzinfo=UTC),
                    notes="N/A",
                    evidence_references=("ref:1",),
                )
            ledger.close()
            graph.close()
            claims.close()


if __name__ == "__main__":
    unittest.main()
