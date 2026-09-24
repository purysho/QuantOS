import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from quantos.case_dossier import CaseDossier
from quantos.case_reviews import CASE_ROLE_CHECKS, CaseReviewLedger
from quantos.gates import CapitalFirewall, LiveTradingDisabled
from quantos.models import OrderProposal
from quantos.professional_reviews import (
    ProfessionalRole,
    ReviewDisposition,
)
from quantos.readiness import (
    ResearchReadinessGate,
    ResearchReadinessLedger,
    ResearchReadinessState,
)
from quantos.research_case import ResearchCase, ResearchCaseType
from quantos.scenarios import ScenarioSet


UTC = timezone.utc


def dossier(fingerprint="case-dossier:" + "a" * 64):
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


def complete_reviews(ledger, target):
    for role in ProfessionalRole:
        ledger.record(
            dossier=target,
            role=role,
            reviewer=f"{role.value.lower()}-reviewer",
            recorded_at=datetime(2026, 9, 24, 16, tzinfo=UTC),
            disposition=ReviewDisposition.NO_OBJECTION,
            check_notes=checks(role),
            findings=("No blocking issue recorded for this case snapshot.",),
        )


class ReadinessTests(unittest.TestCase):
    def test_incomplete_case_cannot_receive_shadow_permit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reviews = CaseReviewLedger(root / "reviews.duckdb")
            permits = ResearchReadinessLedger(root / "permits.duckdb")
            assessment = ResearchReadinessGate().assess(
                dossier(),
                reviews=reviews,
            )
            self.assertEqual(
                assessment.state,
                ResearchReadinessState.INCOMPLETE,
            )
            with self.assertRaises(ValueError):
                permits.issue(
                    assessment=assessment,
                    issued_at=datetime(2026, 9, 24, 17, tzinfo=UTC),
                    issued_by="research-committee",
                )
            permits.close()
            reviews.close()

    def test_unresolved_block_maps_to_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reviews = CaseReviewLedger(root / "reviews.duckdb")
            target = dossier()
            complete_reviews(reviews, target)
            reviews.record(
                dossier=target,
                role=ProfessionalRole.RED_TEAM,
                reviewer="red-team-blocker",
                recorded_at=datetime(2026, 9, 24, 16, 30, tzinfo=UTC),
                disposition=ReviewDisposition.BLOCKING_OBJECTION,
                check_notes=checks(ProfessionalRole.RED_TEAM),
                findings=("Additional red-team review found an unresolved issue.",),
                objections=("Case mechanism is not distinguishable from the strongest alternative.",),
            )
            assessment = ResearchReadinessGate().assess(
                target,
                reviews=reviews,
            )
            self.assertEqual(
                assessment.state,
                ResearchReadinessState.BLOCKED,
            )
            self.assertTrue(
                any("blocking" in reason for reason in assessment.reasons)
            )
            reviews.close()

    def test_complete_panel_can_issue_shadow_only_permit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reviews = CaseReviewLedger(root / "reviews.duckdb")
            permits = ResearchReadinessLedger(root / "permits.duckdb")
            target = dossier()
            complete_reviews(reviews, target)
            assessment = ResearchReadinessGate().assess(
                target,
                reviews=reviews,
            )
            self.assertEqual(
                assessment.state,
                ResearchReadinessState.READY_FOR_PROSPECTIVE_SHADOW,
            )
            permit = permits.issue(
                assessment=assessment,
                issued_at=datetime(2026, 9, 24, 17, tzinfo=UTC),
                issued_by="research-committee",
            )
            self.assertEqual(
                permit.purpose,
                "PROSPECTIVE_SHADOW_ONLY",
            )
            self.assertTrue(
                ResearchReadinessLedger.applies_to(permit, target)
            )
            permits.close()
            reviews.close()

    def test_new_case_dossier_fingerprint_invalidates_old_permit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reviews = CaseReviewLedger(root / "reviews.duckdb")
            permits = ResearchReadinessLedger(root / "permits.duckdb")
            original = dossier()
            complete_reviews(reviews, original)
            assessment = ResearchReadinessGate().assess(
                original,
                reviews=reviews,
            )
            permit = permits.issue(
                assessment=assessment,
                issued_at=datetime(2026, 9, 24, 17, tzinfo=UTC),
                issued_by="research-committee",
            )
            revised = replace(
                original,
                case_dossier_fingerprint="case-dossier:" + "e" * 64,
            )
            self.assertFalse(
                ResearchReadinessLedger.applies_to(permit, revised)
            )
            revised_assessment = ResearchReadinessGate().assess(
                revised,
                reviews=reviews,
            )
            self.assertEqual(
                revised_assessment.state,
                ResearchReadinessState.INCOMPLETE,
            )
            permits.close()
            reviews.close()

    def test_shadow_permit_does_not_change_live_capital_firewall(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reviews = CaseReviewLedger(root / "reviews.duckdb")
            permits = ResearchReadinessLedger(root / "permits.duckdb")
            target = dossier()
            complete_reviews(reviews, target)
            assessment = ResearchReadinessGate().assess(
                target,
                reviews=reviews,
            )
            permits.issue(
                assessment=assessment,
                issued_at=datetime(2026, 9, 24, 17, tzinfo=UTC),
                issued_by="research-committee",
            )
            with self.assertRaises(LiveTradingDisabled):
                CapitalFirewall().authorize_live_order(
                    OrderProposal(
                        security_id="DEMO",
                        side="BUY",
                        quantity=1,
                        reason_hypothesis_id="hypothesis:test",
                    )
                )
            permits.close()
            reviews.close()


if __name__ == "__main__":
    unittest.main()
