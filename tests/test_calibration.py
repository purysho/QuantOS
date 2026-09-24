import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quantos.calibration import ForecastCalibrationLedger
from quantos.case_dossier import CaseDossier
from quantos.readiness import ProspectiveShadowPermit
from quantos.research_case import ResearchCase, ResearchCaseType
from quantos.scenarios import (
    OutcomeRange,
    ProbabilityBand,
    ScenarioSet,
    make_scenario,
)


UTC = timezone.utc


def dossier():
    case = ResearchCase(
        case_id="research-case:" + "a" * 64,
        case_type=ResearchCaseType.SYSTEMATIC,
        subject_ids=("signal:test",),
        universe="Test universe",
        thesis="Test thesis.",
        mechanism="Test mechanism.",
        horizon="12 months",
        as_of=datetime(2026, 9, 24, 12, tzinfo=UTC),
        supporting_claim_ids=("claim:" + "b" * 64,),
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
    scenarios = (
        make_scenario(
            name="bear",
            description="Bear scenario.",
            probability=ProbabilityBand(0.1, 0.2, 0.3),
            probability_rationale="Downside remains plausible.",
            assumptions=("A",),
            conditions=("C1",),
            outcomes=(OutcomeRange("net_return", "decimal", -0.1, -0.05, 0.0),),
        ),
        make_scenario(
            name="base",
            description="Base scenario.",
            probability=ProbabilityBand(0.4, 0.5, 0.6),
            probability_rationale="Central case.",
            assumptions=("B",),
            conditions=("C2",),
            outcomes=(OutcomeRange("net_return", "decimal", 0.0, 0.03, 0.06),),
        ),
        make_scenario(
            name="bull",
            description="Bull scenario.",
            probability=ProbabilityBand(0.2, 0.3, 0.4),
            probability_rationale="Upside remains plausible.",
            assumptions=("C",),
            conditions=("C3",),
            outcomes=(OutcomeRange("net_return", "decimal", 0.05, 0.1, 0.2),),
        ),
    )
    scenario_set = ScenarioSet(
        scenario_set_id="scenario-set:" + "c" * 64,
        case_id=case.case_id,
        scenarios=scenarios,
        author="researcher",
        created_at=datetime(2026, 9, 24, 12, 2, tzinfo=UTC),
        supersedes_scenario_set_id=None,
    )
    return CaseDossier(
        case=case,
        scenario_set=scenario_set,
        claim_dossiers=(),
        case_dossier_fingerprint="case-dossier:" + "d" * 64,
        caveat="test",
    )


def permit(target, issued_at=None):
    return ProspectiveShadowPermit(
        permit_id="shadow-permit:" + "e" * 64,
        case_id=target.case.case_id,
        case_dossier_fingerprint=target.case_dossier_fingerprint,
        scenario_set_id=target.scenario_set.scenario_set_id,
        issued_at=issued_at or datetime(2026, 9, 24, 13, tzinfo=UTC),
        issued_by="research-committee",
        purpose="PROSPECTIVE_SHADOW_ONLY",
    )


class ForecastCalibrationTests(unittest.TestCase):
    def test_forecast_requires_matching_shadow_permit(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = ForecastCalibrationLedger(Path(tmp) / "calibration.duckdb")
            target = dossier()
            bad = ProspectiveShadowPermit(
                permit_id="shadow-permit:" + "f" * 64,
                case_id=target.case.case_id,
                case_dossier_fingerprint="case-dossier:" + "0" * 64,
                scenario_set_id=target.scenario_set.scenario_set_id,
                issued_at=datetime(2026, 9, 24, 13, tzinfo=UTC),
                issued_by="committee",
                purpose="PROSPECTIVE_SHADOW_ONLY",
            )
            with self.assertRaises(ValueError):
                ledger.record_forecast(
                    dossier=target,
                    permit=bad,
                    forecaster="forecast-a",
                    forecast_at=datetime(2026, 9, 24, 14, tzinfo=UTC),
                    horizon_end=datetime(2027, 9, 24, 14, tzinfo=UTC),
                )
            ledger.close()

    def test_frozen_forecast_uses_scenario_central_probabilities(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = ForecastCalibrationLedger(Path(tmp) / "calibration.duckdb")
            target = dossier()
            record = ledger.record_forecast(
                dossier=target,
                permit=permit(target),
                forecaster="forecast-a",
                forecast_at=datetime(2026, 9, 24, 14, tzinfo=UTC),
                horizon_end=datetime(2027, 9, 24, 14, tzinfo=UTC),
            )
            self.assertEqual(
                [item.probability for item in record.probabilities],
                [0.2, 0.5, 0.3],
            )
            self.assertEqual(ledger.get_forecast(record.forecast_id), record)
            ledger.close()

    def test_outcome_cannot_be_classified_before_horizon_or_by_forecaster(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = ForecastCalibrationLedger(Path(tmp) / "calibration.duckdb")
            target = dossier()
            record = ledger.record_forecast(
                dossier=target,
                permit=permit(target),
                forecaster="forecast-a",
                forecast_at=datetime(2026, 9, 24, 14, tzinfo=UTC),
                horizon_end=datetime(2027, 9, 24, 14, tzinfo=UTC),
            )
            realized = record.probabilities[1].scenario_id
            with self.assertRaises(ValueError):
                ledger.record_outcome(
                    forecast_id=record.forecast_id,
                    realized_scenario_id=realized,
                    observed_at=datetime(2027, 9, 23, 14, tzinfo=UTC),
                    adjudicator="outcome-b",
                    notes="Too early.",
                    evidence_references=("artifact:outcome",),
                )
            with self.assertRaises(ValueError):
                ledger.record_outcome(
                    forecast_id=record.forecast_id,
                    realized_scenario_id=realized,
                    observed_at=datetime(2027, 9, 25, 14, tzinfo=UTC),
                    adjudicator="forecast-a",
                    notes="Same person.",
                    evidence_references=("artifact:outcome",),
                )
            ledger.close()

    def test_multiclass_brier_and_log_loss_are_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = ForecastCalibrationLedger(Path(tmp) / "calibration.duckdb")
            target = dossier()
            record = ledger.record_forecast(
                dossier=target,
                permit=permit(target),
                forecaster="forecast-a",
                forecast_at=datetime(2026, 9, 24, 14, tzinfo=UTC),
                horizon_end=datetime(2027, 9, 24, 14, tzinfo=UTC),
            )
            realized = record.probabilities[1].scenario_id
            ledger.record_outcome(
                forecast_id=record.forecast_id,
                realized_scenario_id=realized,
                observed_at=datetime(2027, 9, 25, 14, tzinfo=UTC),
                adjudicator="outcome-b",
                notes="Base scenario best matches the predefined observable conditions.",
                evidence_references=("artifact:outcome-001",),
            )
            score = ledger.score(record.forecast_id)
            expected_brier = 0.2**2 + (0.5 - 1.0) ** 2 + 0.3**2
            self.assertAlmostEqual(score.brier_score, expected_brier)
            self.assertAlmostEqual(score.log_loss, -__import__("math").log(0.5))
            ledger.close()

    def test_calibration_summary_refuses_small_sample_conclusion(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = ForecastCalibrationLedger(Path(tmp) / "calibration.duckdb")
            target = dossier()
            start = datetime(2026, 9, 24, 14, tzinfo=UTC)
            for i in range(3):
                issued = start + timedelta(days=i * 400)
                local_permit = ProspectiveShadowPermit(
                    permit_id="shadow-permit:" + f"{i:064x}",
                    case_id=target.case.case_id,
                    case_dossier_fingerprint=target.case_dossier_fingerprint,
                    scenario_set_id=target.scenario_set.scenario_set_id,
                    issued_at=issued - timedelta(hours=1),
                    issued_by="committee",
                    purpose="PROSPECTIVE_SHADOW_ONLY",
                )
                record = ledger.record_forecast(
                    dossier=target,
                    permit=local_permit,
                    forecaster="forecast-a",
                    forecast_at=issued,
                    horizon_end=issued + timedelta(days=365),
                )
                ledger.record_outcome(
                    forecast_id=record.forecast_id,
                    realized_scenario_id=record.probabilities[i % 3].scenario_id,
                    observed_at=issued + timedelta(days=366),
                    adjudicator="outcome-b",
                    notes="Outcome classified from the predefined scenario conditions.",
                    evidence_references=(f"artifact:outcome-{i}",),
                )
            summary = ledger.summary(
                forecaster="forecast-a",
                scenario_count=3,
            )
            self.assertEqual(summary.state, "INSUFFICIENT_EVIDENCE")
            self.assertEqual(summary.observations, 3)
            self.assertIsNone(summary.mean_brier_score)
            self.assertIsNone(summary.mean_log_loss)
            ledger.close()


if __name__ == "__main__":
    unittest.main()
