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


def focal_card():
    sources = ("sha256:a",)
    text = "A scoped historical factor relation was observed."
    return ClaimCard(
        text=text,
        claim_type=ClaimType.EMPIRICAL,
        stance=ClaimStance.SUPPORTS,
        epistemic_state=EpistemicState.OBSERVED,
        topic="factor evidence",
        source_artifact_ids=sources,
        locator="Table 1",
        scope={"sample": "historical"},
        assumptions=(),
        limitations=("May not persist out of sample.",),
        as_of=datetime(2026, 9, 24, tzinfo=UTC),
        claim_id=make_claim_id(
            text=text,
            source_artifact_ids=sources,
            locator="Table 1",
        ),
    )


def checks(role):
    return {
        key: f"Reviewed {key.replace('_', ' ')}."
        for key in ROLE_CHECKS[role]
    }


class ProfessionalReviewTests(unittest.TestCase):
    def _dossier(self, root):
        claims = ClaimStore(root / "claims.duckdb")
        graph = ClaimEvidenceGraph(root / "evidence.duckdb")
        focal = focal_card()
        claims.add(focal)
        dossier = EvidenceDossierBuilder().build(
            focal.claim_id,
            claims=claims,
            evidence_graph=graph,
        )
        return claims, graph, dossier

    def test_role_contract_requires_all_role_specific_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claims, graph, dossier = self._dossier(root)
            ledger = ProfessionalReviewLedger(root / "reviews.duckdb")
            incomplete = checks(ProfessionalRole.QUANT_RESEARCHER)
            incomplete.pop("data_leakage_and_multiple_testing")
            with self.assertRaises(ValueError):
                ledger.record(
                    dossier=dossier,
                    role=ProfessionalRole.QUANT_RESEARCHER,
                    reviewer="quant-a",
                    recorded_at=datetime(2026, 9, 24, 16, tzinfo=UTC),
                    disposition=ReviewDisposition.NO_OBJECTION,
                    check_notes=incomplete,
                    findings=("Reviewed the evidence structure.",),
                )
            ledger.close()
            graph.close()
            claims.close()

    def test_blocking_review_requires_explicit_objection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claims, graph, dossier = self._dossier(root)
            ledger = ProfessionalReviewLedger(root / "reviews.duckdb")
            with self.assertRaises(ValueError):
                ledger.record(
                    dossier=dossier,
                    role=ProfessionalRole.RED_TEAM,
                    reviewer="red-a",
                    recorded_at=datetime(2026, 9, 24, 16, tzinfo=UTC),
                    disposition=ReviewDisposition.BLOCKING_OBJECTION,
                    check_notes=checks(ProfessionalRole.RED_TEAM),
                    findings=("Reviewed falsifiers.",),
                    objections=(),
                )
            ledger.close()
            graph.close()
            claims.close()

    def test_one_blocking_role_is_not_erased_by_other_reviews(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claims, graph, dossier = self._dossier(root)
            ledger = ProfessionalReviewLedger(root / "reviews.duckdb")
            for role in ProfessionalRole:
                disposition = (
                    ReviewDisposition.BLOCKING_OBJECTION
                    if role is ProfessionalRole.RISK_OFFICER
                    else ReviewDisposition.NO_OBJECTION
                )
                ledger.record(
                    dossier=dossier,
                    role=role,
                    reviewer=f"{role.value.lower()}-reviewer",
                    recorded_at=datetime(2026, 9, 24, 16, tzinfo=UTC),
                    disposition=disposition,
                    check_notes=checks(role),
                    findings=("Role review completed.",),
                    objections=(
                        ("Tail-loss mechanism is not bounded.",)
                        if disposition is ReviewDisposition.BLOCKING_OBJECTION
                        else ()
                    ),
                )
            panel = ledger.panel(dossier)
            self.assertEqual(
                panel.state,
                ReviewPanelState.BLOCKING_OBJECTION_PRESENT,
            )
            self.assertEqual(len(panel.missing_roles), 0)
            self.assertEqual(len(panel.blocking_review_ids), 1)
            self.assertEqual(len(panel.reviews), 6)
            ledger.close()
            graph.close()
            claims.close()

    def test_panel_is_incomplete_until_all_six_roles_are_represented(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claims, graph, dossier = self._dossier(root)
            ledger = ProfessionalReviewLedger(root / "reviews.duckdb")
            ledger.record(
                dossier=dossier,
                role=ProfessionalRole.QUANT_RESEARCHER,
                reviewer="quant",
                recorded_at=datetime(2026, 9, 24, 16, tzinfo=UTC),
                disposition=ReviewDisposition.NO_OBJECTION,
                check_notes=checks(ProfessionalRole.QUANT_RESEARCHER),
                findings=("Quant review completed.",),
            )
            panel = ledger.panel(dossier)
            self.assertEqual(panel.state, ReviewPanelState.INCOMPLETE)
            self.assertEqual(len(panel.missing_roles), 5)
            ledger.close()
            graph.close()
            claims.close()

    def test_conditional_review_requires_followups_and_remains_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claims, graph, dossier = self._dossier(root)
            ledger = ProfessionalReviewLedger(root / "reviews.duckdb")
            for role in ProfessionalRole:
                disposition = (
                    ReviewDisposition.CONDITIONAL
                    if role is ProfessionalRole.EXECUTION_TRADER
                    else ReviewDisposition.NO_OBJECTION
                )
                ledger.record(
                    dossier=dossier,
                    role=role,
                    reviewer=f"{role.value.lower()}-reviewer",
                    recorded_at=datetime(2026, 9, 24, 16, tzinfo=UTC),
                    disposition=disposition,
                    check_notes=checks(role),
                    findings=("Review completed.",),
                    required_followups=(
                        ("Measure slippage and capacity prospectively.",)
                        if disposition is ReviewDisposition.CONDITIONAL
                        else ()
                    ),
                )
            panel = ledger.panel(dossier)
            self.assertEqual(panel.state, ReviewPanelState.CONDITIONS_OPEN)
            self.assertEqual(len(panel.conditional_review_ids), 1)
            ledger.close()
            graph.close()
            claims.close()

    def test_complete_review_set_is_not_capital_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claims, graph, dossier = self._dossier(root)
            ledger = ProfessionalReviewLedger(root / "reviews.duckdb")
            for role in ProfessionalRole:
                ledger.record(
                    dossier=dossier,
                    role=role,
                    reviewer=f"{role.value.lower()}-reviewer",
                    recorded_at=datetime(2026, 9, 24, 16, tzinfo=UTC),
                    disposition=ReviewDisposition.NO_OBJECTION,
                    check_notes=checks(role),
                    findings=("No blocking issue recorded in this review.",),
                )
            panel = ledger.panel(dossier)
            self.assertEqual(panel.state, ReviewPanelState.REVIEW_SET_COMPLETE)
            self.assertIn("not an investment decision", panel.caveat)
            self.assertIn("capital authorization", panel.caveat)
            ledger.close()
            graph.close()
            claims.close()


if __name__ == "__main__":
    unittest.main()
