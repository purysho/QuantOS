import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from quantos.case_dossier import CaseDossier
from quantos.case_reviews import (
    CASE_ROLE_CHECKS,
    CaseReviewLedger,
)
from quantos.professional_reviews import (
    ProfessionalRole,
    ReviewDisposition,
    ReviewPanelState,
)
from quantos.research_case import ResearchCase, ResearchCaseType
from quantos.scenarios import ScenarioSet


UTC = timezone.utc


def case_dossier(fingerprint="case-dossier:" + "a" * 64):
    case = ResearchCase(
        case_id="research-case:" + "b" * 64,
        case_type=ResearchCaseType.SYSTEMATIC,
        subject_ids=("signal:test",),
        universe="Test universe",
        thesis="Test thesis.",
        mechanism="Test mechanism.",
        horizon="12 months",
        as_of=datetime(2026, 9, 24, 12, tzinfo=UTC),
        supporting_claim_ids=("claim:" + "c" * 64,),
        limiting_claim_ids=(),
        contradicting_claim_ids=(),
        alternative_explanations=("Alternative.",),
        falsifiers=("Falsifier.",),
        monitoring_conditions=("Monitor.",),
        assumptions=(),
        author="researcher",
        created_at=datetime(2026, 9, 24, 12, 1, tzinfo=UTC),
        supersedes_case_id=None,
    )
    scenario_set = ScenarioSet(
        scenario_set_id="scenario-set:" + "d" * 64,
        case_id=case.case_id,
        scenarios=(),
        author="researcher",
        created_at=datetime(2026, 9, 24, 12, 2, tzinfo=UTC),
        supersedes_scenario_set_id=None,
    )
    return CaseDossier(
        case=case,
        scenario_set=scenario_set,
        claim_dossiers=(),
        case_dossier_fingerprint=fingerprint,
        caveat="test",
    )


def checks(role):
    return {
        key: f"Reviewed {key.replace('_', ' ')}."
        for key in CASE_ROLE_CHECKS[role]
    }


class CaseReviewTests(unittest.TestCase):
    def test_case_role_contract_requires_every_mandatory_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = CaseReviewLedger(Path(tmp) / "case-reviews.duckdb")
            role = ProfessionalRole.QUANT_RESEARCHER
            incomplete = checks(role)
            incomplete.pop("replication_and_regime_stability")
            with self.assertRaises(ValueError):
                ledger.record(
                    dossier=case_dossier(),
                    role=role,
                    reviewer="quant-a",
                    recorded_at=datetime(2026, 9, 24, 16, tzinfo=UTC),
                    disposition=ReviewDisposition.NO_OBJECTION,
                    check_notes=incomplete,
                    findings=("Review completed.",),
                )
            ledger.close()

    def test_one_case_block_cannot_be_outvoted(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = CaseReviewLedger(Path(tmp) / "case-reviews.duckdb")
            dossier = case_dossier()
            for role in ProfessionalRole:
                disposition = (
                    ReviewDisposition.BLOCKING_OBJECTION
                    if role is ProfessionalRole.RED_TEAM
                    else ReviewDisposition.NO_OBJECTION
                )
                ledger.record(
                    dossier=dossier,
                    role=role,
                    reviewer=f"{role.value.lower()}-reviewer",
                    recorded_at=datetime(2026, 9, 24, 16, tzinfo=UTC),
                    disposition=disposition,
                    check_notes=checks(role),
                    findings=("Case review completed.",),
                    objections=(
                        ("The strongest alternative explanation remains unresolved.",)
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
            ledger.close()

    def test_resolution_preserves_original_case_objection(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = CaseReviewLedger(Path(tmp) / "case-reviews.duckdb")
            dossier = case_dossier()
            review = ledger.record(
                dossier=dossier,
                role=ProfessionalRole.RISK_OFFICER,
                reviewer="risk-a",
                recorded_at=datetime(2026, 9, 24, 16, tzinfo=UTC),
                disposition=ReviewDisposition.BLOCKING_OBJECTION,
                check_notes=checks(ProfessionalRole.RISK_OFFICER),
                findings=("Risk review completed.",),
                objections=("Scenario set omits a liquidity shock.",),
            )
            resolution = ledger.resolve_review(
                review_id=review.review_id,
                resolver="risk-a",
                resolved_at=datetime(2026, 9, 24, 17, tzinfo=UTC),
                notes="Liquidity shock was added to a revised supporting stress artifact.",
                evidence_references=("stress-artifact:liquidity-001",),
            )
            preserved = ledger.get(review.review_id)
            self.assertEqual(
                preserved.objections,
                ("Scenario set omits a liquidity shock.",),
            )
            self.assertEqual(
                ledger.resolution_for(review.review_id),
                resolution,
            )
            ledger.close()

    def test_only_original_case_reviewer_can_resolve_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = CaseReviewLedger(Path(tmp) / "case-reviews.duckdb")
            review = ledger.record(
                dossier=case_dossier(),
                role=ProfessionalRole.EXECUTION_TRADER,
                reviewer="trader-a",
                recorded_at=datetime(2026, 9, 24, 16, tzinfo=UTC),
                disposition=ReviewDisposition.CONDITIONAL,
                check_notes=checks(ProfessionalRole.EXECUTION_TRADER),
                findings=("Execution review completed.",),
                required_followups=("Measure capacity prospectively.",),
            )
            with self.assertRaises(ValueError):
                ledger.resolve_review(
                    review_id=review.review_id,
                    resolver="pm-b",
                    resolved_at=datetime(2026, 9, 24, 17, tzinfo=UTC),
                    notes="Capacity measured.",
                    evidence_references=("capacity:001",),
                )
            ledger.close()

    def test_changed_case_dossier_fingerprint_makes_old_reviews_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = CaseReviewLedger(Path(tmp) / "case-reviews.duckdb")
            original = case_dossier()
            ledger.record(
                dossier=original,
                role=ProfessionalRole.QUANT_RESEARCHER,
                reviewer="quant-a",
                recorded_at=datetime(2026, 9, 24, 16, tzinfo=UTC),
                disposition=ReviewDisposition.NO_OBJECTION,
                check_notes=checks(ProfessionalRole.QUANT_RESEARCHER),
                findings=("Reviewed original evidence snapshot.",),
            )
            revised = replace(
                original,
                case_dossier_fingerprint="case-dossier:" + "e" * 64,
            )
            original_panel = ledger.panel(original)
            revised_panel = ledger.panel(revised)
            self.assertEqual(len(original_panel.reviews), 1)
            self.assertEqual(revised_panel.reviews, ())
            self.assertEqual(
                revised_panel.state,
                ReviewPanelState.INCOMPLETE,
            )
            self.assertEqual(len(revised_panel.missing_roles), 6)
            ledger.close()

    def test_complete_case_review_set_is_explicitly_not_capital_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = CaseReviewLedger(Path(tmp) / "case-reviews.duckdb")
            dossier = case_dossier()
            for role in ProfessionalRole:
                ledger.record(
                    dossier=dossier,
                    role=role,
                    reviewer=f"{role.value.lower()}-reviewer",
                    recorded_at=datetime(2026, 9, 24, 16, tzinfo=UTC),
                    disposition=ReviewDisposition.NO_OBJECTION,
                    check_notes=checks(role),
                    findings=("No blocking issue recorded for this role.",),
                )
            panel = ledger.panel(dossier)
            self.assertEqual(
                panel.state,
                ReviewPanelState.REVIEW_SET_COMPLETE,
            )
            self.assertIn("not an investment recommendation", panel.caveat)
            self.assertIn("allocate capital", panel.caveat)
            ledger.close()


if __name__ == "__main__":
    unittest.main()
