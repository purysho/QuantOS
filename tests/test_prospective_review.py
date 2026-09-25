import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from quantos.backtest_economics import BenchmarkKind
from quantos.calibration import (
    ForecastRecord,
    OutcomeRecord,
    ScenarioProbability,
)
from quantos.model_registry import (
    ModelLifecycleStage,
    ResearchRunManifest,
)
from quantos.paper_monitoring import (
    MonitoringConditionCheck,
    PaperBenchmarkObservation,
    PaperHealthState,
    PaperPortfolioObservation,
    PaperPostmortem,
    PostmortemReason,
)
from quantos.prospective_review import (
    ExpectationComparisonState,
    ForecastCalibrationState,
    ProspectiveReviewEngine,
    ProspectiveReviewStore,
)
from quantos.readiness import ProspectiveShadowPermit
from quantos.research_case import ResearchCase, ResearchCaseType

UTC = timezone.utc
AT = datetime(2026, 9, 25, 12, tzinfo=UTC)
CASE_ID = "research-case:" + "c" * 64
MODEL_ID = "research-model:" + "m" * 64
MANIFEST_ID = "research-run-manifest:" + "r" * 64
PERMIT_ID = "shadow-permit:" + "p" * 64
DOSSIER = "case-dossier:" + "d" * 64


def manifest():
    return ResearchRunManifest(
        manifest_id=MANIFEST_ID,
        model_id=MODEL_ID,
        experiment_id="research-experiment:" + "e" * 64,
        research_case_id=CASE_ID,
        case_dossier_fingerprint=DOSSIER,
        factor_id="factor-spec:" + "f" * 64,
        universe_policy_id="universe-policy:" + "u" * 64,
        validation_plan_id="walk-forward-plan:" + "v" * 64,
        backtest_id="economic-backtest:" + "b" * 64,
        backtest_policy_id="backtest-policy:" + "q" * 64,
        performance_analysis_id="performance-analysis:" + "a" * 64,
        performance_policy_id="performance-policy:" + "x" * 64,
        multiple_testing_audit_id="multiple-testing-audit:" + "t" * 64,
        overfitting_policy_id="multiple-testing-policy:" + "o" * 64,
        selected_variant_id="selected",
        selected_variant_series_id="variant-return-series:" + "s" * 64,
        shadow_permit_id=PERMIT_ID,
        benchmark_kinds=("CASH", "EQUAL_WEIGHT", "INVERSE_VOL", "MARKET_CAP"),
        dataset_fingerprints=("dataset:prices",),
        code_revision="git:abc",
        evidence_references=("run:evidence",),
        eligible_stage=ModelLifecycleStage.PAPER,
    )


def permit():
    return ProspectiveShadowPermit(
        permit_id=PERMIT_ID,
        case_id=CASE_ID,
        case_dossier_fingerprint=DOSSIER,
        scenario_set_id="scenario-set:" + "s" * 64,
        issued_at=AT,
        issued_by="reviewer",
        purpose="PROSPECTIVE_SHADOW_ONLY",
    )


def forecast():
    return ForecastRecord(
        forecast_id="forecast:" + "f" * 64,
        permit_id=PERMIT_ID,
        case_id=CASE_ID,
        case_dossier_fingerprint=DOSSIER,
        scenario_set_id="scenario-set:" + "s" * 64,
        forecaster="analyst",
        forecast_at=AT + timedelta(hours=1),
        horizon_end=AT + timedelta(days=10),
        probabilities=(
            ScenarioProbability("bear", "Bear", 0.2),
            ScenarioProbability("base", "Base", 0.5),
            ScenarioProbability("bull", "Bull", 0.3),
        ),
    )


def outcome(realized="base"):
    return OutcomeRecord(
        outcome_id="forecast-outcome:" + realized,
        forecast_id=forecast().forecast_id,
        realized_scenario_id=realized,
        observed_at=AT + timedelta(days=11),
        adjudicator="independent-reviewer",
        notes="Outcome adjudicated from frozen evidence.",
        evidence_references=("outcome:evidence",),
    )


def research_case(*, case_id=CASE_ID, supersedes=None, created_at=None):
    return ResearchCase(
        case_id=case_id,
        case_type=ResearchCaseType.SYSTEMATIC,
        subject_ids=("universe:us-equities",),
        universe="US equities",
        thesis="Frozen thesis.",
        mechanism="Quality/value mechanism.",
        horizon="10 days",
        as_of=AT,
        supporting_claim_ids=("claim:1",),
        limiting_claim_ids=(),
        contradicting_claim_ids=(),
        alternative_explanations=("risk compensation",),
        falsifiers=("prospective degradation",),
        monitoring_conditions=("signal remains directionally consistent",),
        assumptions=(),
        author="researcher",
        created_at=created_at or AT,
        supersedes_case_id=supersedes,
    )


def benchmark_rows(market="0.01"):
    return tuple(
        PaperBenchmarkObservation(
            kind=kind,
            total_return=(
                Decimal(market)
                if kind is BenchmarkKind.MARKET_CAP
                else Decimal("0")
            ),
            source_fact_ids=(f"benchmark:{kind.value}",),
        )
        for kind in (
            BenchmarkKind.CASH,
            BenchmarkKind.MARKET_CAP,
            BenchmarkKind.EQUAL_WEIGHT,
            BenchmarkKind.INVERSE_VOL,
        )
    )


def paper_observation(index, net="0.02", market="0.01", cost="0.001", turnover="0.2"):
    start = AT + timedelta(days=1 + index * 2)
    gross = Decimal(net) + Decimal(cost)
    return PaperPortfolioObservation(
        observation_id=f"paper-observation:{index}",
        model_id=MODEL_ID,
        manifest_id=MANIFEST_ID,
        permit_id=PERMIT_ID,
        case_id=CASE_ID,
        period_start=start,
        period_end=start + timedelta(days=1),
        observed_at=start + timedelta(days=1, minutes=1),
        gross_return=gross,
        net_return=Decimal(net),
        transaction_cost_rate=Decimal(cost),
        borrow_cost_rate=Decimal("0"),
        one_way_turnover_ratio=Decimal(turnover),
        benchmarks=benchmark_rows(market),
        condition_checks=(
            MonitoringConditionCheck(
                condition="signal remains directionally consistent",
                breached=False,
                evidence_references=("monitor:evidence",),
            ),
        ),
        evidence_references=("paper:evidence",),
    )


def postmortem():
    return PaperPostmortem(
        postmortem_id="paper-postmortem:" + "p" * 64,
        model_id=MODEL_ID,
        manifest_id=MANIFEST_ID,
        closed_at=AT + timedelta(days=10),
        reviewer="paper-reviewer",
        reason=PostmortemReason.COMPLETED_EVALUATION,
        notes="Closed at the frozen horizon.",
        evidence_references=("postmortem:evidence",),
        final_health_state=PaperHealthState.MEASURED,
    )


def expectation():
    return ProspectiveReviewEngine().freeze_expectation(
        manifest=manifest(),
        permit=permit(),
        forecast=forecast(),
        cumulative_net_return_lower=Decimal("-0.10"),
        cumulative_net_return_upper=Decimal("0.10"),
        market_relative_return_lower=Decimal("-0.10"),
        market_relative_return_upper=Decimal("0.10"),
        maximum_average_implementation_cost_rate=Decimal("0.01"),
        maximum_average_one_way_turnover=Decimal("0.50"),
        rationale="Freeze expected portfolio behavior before observation.",
        evidence_references=("expectation:evidence",),
    )


class ProspectiveReviewTests(unittest.TestCase):
    def test_expectation_is_frozen_at_forecast_time(self):
        item = expectation()
        self.assertEqual(item.frozen_at, forecast().forecast_at)
        self.assertEqual(item.horizon_end, forecast().horizon_end)

    def test_expectation_rejects_mismatched_permit(self):
        wrong = ProspectiveShadowPermit(
            permit_id="shadow-permit:" + "z" * 64,
            case_id=CASE_ID,
            case_dossier_fingerprint=DOSSIER,
            scenario_set_id="scenario-set:" + "s" * 64,
            issued_at=AT,
            issued_by="reviewer",
            purpose="PROSPECTIVE_SHADOW_ONLY",
        )
        with self.assertRaises(ValueError):
            ProspectiveReviewEngine().freeze_expectation(
                manifest=manifest(),
                permit=wrong,
                forecast=forecast(),
                cumulative_net_return_lower=Decimal("-0.1"),
                cumulative_net_return_upper=Decimal("0.1"),
                market_relative_return_lower=Decimal("-0.1"),
                market_relative_return_upper=Decimal("0.1"),
                maximum_average_implementation_cost_rate=Decimal("0.01"),
                maximum_average_one_way_turnover=Decimal("0.5"),
                rationale="test",
                evidence_references=("evidence",),
            )

    def test_comparison_scores_calibration_and_portfolio_behavior(self):
        review = ProspectiveReviewEngine().compare(
            manifest=manifest(),
            expectation=expectation(),
            forecast=forecast(),
            outcome=outcome(),
            observations=(
                paper_observation(0),
                paper_observation(1),
            ),
            postmortem=postmortem(),
            reviewed_at=AT + timedelta(days=12),
            reviewer="reviewer",
            lessons=("Prospective evidence remained inside frozen ranges.",),
            evidence_references=("review:evidence",),
        )
        self.assertEqual(
            review.state,
            ExpectationComparisonState.WITHIN_FROZEN_EXPECTATIONS,
        )
        self.assertEqual(
            review.calibration.state,
            ForecastCalibrationState.SCORED,
        )
        self.assertEqual(
            review.calibration.realized_probability,
            Decimal("0.5"),
        )
        self.assertEqual(review.calibration.brier_score, Decimal("0.38"))

    def test_behavior_deviation_is_explicit(self):
        review = ProspectiveReviewEngine().compare(
            manifest=manifest(),
            expectation=expectation(),
            forecast=forecast(),
            outcome=outcome("bear"),
            observations=(
                paper_observation(0, net="-0.08", market="0.02"),
                paper_observation(1, net="-0.08", market="0.02"),
            ),
            postmortem=postmortem(),
            reviewed_at=AT + timedelta(days=12),
            reviewer="reviewer",
            lessons=("Observed return behavior differed from the frozen range.",),
            evidence_references=("review:evidence",),
        )
        self.assertEqual(
            review.state,
            ExpectationComparisonState.DEVIATION_PRESENT,
        )
        self.assertTrue(review.deviations)

    def test_observation_after_frozen_horizon_fails_closed(self):
        late = paper_observation(5)
        with self.assertRaises(ValueError):
            ProspectiveReviewEngine().compare(
                manifest=manifest(),
                expectation=expectation(),
                forecast=forecast(),
                outcome=outcome(),
                observations=(late,),
                postmortem=postmortem(),
                reviewed_at=AT + timedelta(days=12),
                reviewer="reviewer",
                lessons=("late",),
                evidence_references=("review:evidence",),
            )

    def test_revision_seed_requires_new_superseding_case(self):
        engine = ProspectiveReviewEngine()
        review = engine.compare(
            manifest=manifest(),
            expectation=expectation(),
            forecast=forecast(),
            outcome=outcome(),
            observations=(paper_observation(0),),
            postmortem=postmortem(),
            reviewed_at=AT + timedelta(days=12),
            reviewer="reviewer",
            lessons=("Carry this lesson into a new immutable case.",),
            evidence_references=("review:evidence",),
        )
        seed = engine.make_revision_seed(
            prior_case=research_case(),
            comparison=review,
            created_at=AT + timedelta(days=13),
            author="researcher",
            evidence_references=("seed:evidence",),
        )
        revised = research_case(
            case_id="research-case:" + "n" * 64,
            supersedes=CASE_ID,
            created_at=AT + timedelta(days=14),
        )
        engine.validate_case_revision(
            seed=seed,
            prior_case=research_case(),
            revised_case=revised,
        )
        mutated = research_case(
            case_id=CASE_ID,
            supersedes=CASE_ID,
            created_at=AT + timedelta(days=14),
        )
        with self.assertRaises(ValueError):
            engine.validate_case_revision(
                seed=seed,
                prior_case=research_case(),
                revised_case=mutated,
            )

    def test_store_is_idempotent(self):
        engine = ProspectiveReviewEngine()
        exp = expectation()
        review = engine.compare(
            manifest=manifest(),
            expectation=exp,
            forecast=forecast(),
            outcome=outcome(),
            observations=(paper_observation(0),),
            postmortem=postmortem(),
            reviewed_at=AT + timedelta(days=12),
            reviewer="reviewer",
            lessons=("Persist exact prospective comparison.",),
            evidence_references=("review:evidence",),
        )
        seed = engine.make_revision_seed(
            prior_case=research_case(),
            comparison=review,
            created_at=AT + timedelta(days=13),
            author="researcher",
            evidence_references=("seed:evidence",),
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = ProspectiveReviewStore(Path(tmp) / "review.duckdb")
            self.assertTrue(store.add_expectation(exp))
            self.assertFalse(store.add_expectation(exp))
            self.assertTrue(store.add_comparison(review))
            self.assertFalse(store.add_comparison(review))
            self.assertTrue(store.add_revision_seed(seed))
            self.assertFalse(store.add_revision_seed(seed))
            store.close()


if __name__ == "__main__":
    unittest.main()
