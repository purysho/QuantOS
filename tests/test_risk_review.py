import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from quantos.historical_simulation_risk import (
    HistoricalLossObservation,
    HistoricalQuantileConvention,
    HistoricalSimulationRiskEstimate,
    HistoricalSimulationRiskPolicy,
    HistoricalSimulationWeighting,
    historical_simulation_risk_estimate_identity,
)
from quantos.portfolio_risk_cube import (
    PortfolioRiskCube,
    PortfolioRiskCubeState,
    ScenarioRiskSummary,
    portfolio_risk_cube_identity,
)
from quantos.risk_review import (
    ExceptionIndependenceEngine,
    ExceptionIndependencePolicy,
    ExceptionIndependenceState,
    RiskReviewEngine,
    RiskReviewPolicy,
    RiskReviewState,
)
from quantos.var_backtesting import (
    VaRBacktestObservation,
    VaRCalibrationEngine,
    VaRCalibrationPolicy,
    var_backtest_observation_identity,
)

UTC = timezone.utc
AT = datetime(2026, 9, 25, 12, tzinfo=UTC)


def backtest_observation(index, *, exception=False, total=20):
    start = AT - timedelta(days=total - index)
    var_loss = Decimal("10")
    realized_loss = Decimal("12") if exception else Decimal("5")
    draft = VaRBacktestObservation(
        observation_id="placeholder",
        estimate_id=f"risk-estimate:{index:064x}",
        risk_policy_id="risk-policy:" + "r" * 64,
        cube_id=f"risk-cube:{index:064x}",
        outcome_id=f"outcome:{index:064x}",
        forecast_time=start,
        period_start=start,
        period_end=start + timedelta(days=1),
        confidence_level=Decimal("0.95"),
        horizon_seconds=86400,
        value_at_risk_loss=var_loss,
        expected_shortfall_loss=Decimal("15"),
        realized_pnl=-realized_loss,
        realized_loss=realized_loss,
        var_exception=exception,
        var_exception_magnitude=(
            realized_loss - var_loss
            if exception
            else Decimal("0")
        ),
        loss_beyond_expected_shortfall=False,
        expected_shortfall_excess_magnitude=Decimal("0"),
        source_fact_ids=(f"realized:{index}",),
        diagnostics=("fixture",),
        approval_authority="NONE",
        order_authority="NONE",
        capital_authority="NONE",
    )
    return replace(
        draft,
        observation_id=var_backtest_observation_identity(draft),
    )


def calibration(observations):
    return VaRCalibrationEngine().evaluate(
        observations=observations,
        policy=VaRCalibrationPolicy(
            minimum_observations=20,
            kupiec_significance_level=Decimal("0.05"),
            rationale="Risk review calibration.",
            evidence_references=("calibration:policy",),
        ),
    )


def independence_policy(minimum=19):
    return ExceptionIndependencePolicy(
        minimum_contiguous_transitions=minimum,
        significance_level=Decimal("0.05"),
        rationale="Exception clustering diagnostic.",
        evidence_references=("independence:policy",),
    )


def cube():
    draft = PortfolioRiskCube(
        cube_id="placeholder",
        state=PortfolioRiskCubeState.COMPLETE,
        base_snapshot_id="market-data-snapshot:" + "b" * 64,
        valuation_time=AT,
        reporting_currency="USD",
        position_ids=("position:" + "p" * 64,),
        scenario_ids=(
            "scenario:down",
            "scenario:up",
        ),
        coverage=(),
        missing_coverage=(),
        position_base_values=(),
        net_base_value=Decimal("100"),
        gross_base_value=Decimal("100"),
        maximum_base_value_concentration=Decimal("0.60"),
        scenario_summaries=(
            ScenarioRiskSummary(
                scenario_id="scenario:down",
                complete=True,
                portfolio_pnl=Decimal("-20"),
                pnl_on_gross_base=Decimal("-0.20"),
                largest_loss_position_id="position:" + "p" * 64,
                largest_loss=Decimal("-20"),
                largest_gain_position_id="position:" + "p" * 64,
                largest_gain=Decimal("-20"),
            ),
            ScenarioRiskSummary(
                scenario_id="scenario:up",
                complete=True,
                portfolio_pnl=Decimal("10"),
                pnl_on_gross_base=Decimal("0.10"),
                largest_loss_position_id="position:" + "p" * 64,
                largest_loss=Decimal("10"),
                largest_gain_position_id="position:" + "p" * 64,
                largest_gain=Decimal("10"),
            ),
        ),
        finite_difference_sensitivities=(),
        diagnostics=("fixture",),
        var_authority="NONE",
        order_authority="NONE",
        capital_authority="NONE",
        caveat="fixture",
    )
    return replace(
        draft,
        cube_id=portfolio_risk_cube_identity(draft),
    )


def historical_policy():
    return HistoricalSimulationRiskPolicy(
        confidence_level=Decimal("0.95"),
        minimum_observations=20,
        horizon_seconds=86400,
        window_start=AT - timedelta(days=100),
        window_end=AT - timedelta(days=1),
        weighting=HistoricalSimulationWeighting.EQUAL_WEIGHT,
        quantile_convention=(
            HistoricalQuantileConvention.NEAREST_RANK
        ),
        missing_data_policy="FAIL_CLOSED",
        rationale="Current risk policy.",
        evidence_references=("risk-policy:current",),
    )


def historical_risk(risk_cube):
    policy = historical_policy()
    losses = tuple(
        HistoricalLossObservation(
            observation_id=(
                f"historical-scenario-observation:{index:064x}"
            ),
            scenario_id=f"history-scenario:{index}",
            period_start=AT - timedelta(days=40 - index),
            period_end=AT - timedelta(days=39 - index),
            portfolio_pnl=Decimal(str(index - 10)),
            loss=Decimal(str(10 - index)),
        )
        for index in range(20)
    )
    draft = HistoricalSimulationRiskEstimate(
        estimate_id="placeholder",
        cube_id=risk_cube.cube_id,
        policy_id=policy.policy_id,
        base_snapshot_id=risk_cube.base_snapshot_id,
        valuation_time=risk_cube.valuation_time,
        reporting_currency=risk_cube.reporting_currency,
        observation_ids=tuple(
            item.observation_id for item in losses
        ),
        observation_count=len(losses),
        confidence_level=Decimal("0.95"),
        weighting=HistoricalSimulationWeighting.EQUAL_WEIGHT,
        quantile_convention=(
            HistoricalQuantileConvention.NEAREST_RANK
        ),
        tail_count=1,
        ordered_losses=losses,
        value_at_risk_loss=Decimal("10"),
        expected_shortfall_loss=Decimal("15"),
        worst_loss=Decimal("20"),
        best_loss=Decimal("-10"),
        mean_loss=Decimal("2"),
        value_at_risk_on_gross_base=Decimal("0.10"),
        expected_shortfall_on_gross_base=Decimal("0.15"),
        diagnostics=("fixture",),
        var_authority="RESEARCH_ONLY",
        order_authority="NONE",
        capital_authority="NONE",
        caveat="fixture",
    )
    return replace(
        draft,
        estimate_id=historical_simulation_risk_estimate_identity(
            draft
        ),
    )


def review_policy(**overrides):
    values = {
        "maximum_base_value_concentration": Decimal("0.70"),
        "maximum_worst_scenario_loss_on_gross_base": Decimal("0.25"),
        "rationale": "Final independent risk review.",
        "evidence_references": ("risk-review:policy",),
    }
    values.update(overrides)
    return RiskReviewPolicy(**values)


class RiskReviewTests(unittest.TestCase):
    def test_nonclustered_exception_history_is_within_test_tolerance(self):
        observations = tuple(
            backtest_observation(
                index,
                exception=(index == 10),
            )
            for index in range(20)
        )
        report = calibration(observations)
        diagnostic = ExceptionIndependenceEngine().evaluate(
            observations=observations,
            calibration=report,
            policy=independence_policy(),
        )
        self.assertEqual(
            diagnostic.state,
            ExceptionIndependenceState.WITHIN_TEST_TOLERANCE,
        )
        self.assertEqual(
            diagnostic.contiguous_transition_count,
            19,
        )

    def test_clustered_exceptions_are_flagged_even_when_frequency_is_plausible(self):
        observations = tuple(
            backtest_observation(
                index,
                exception=index in {30, 31},
                total=60,
            )
            for index in range(60)
        )
        report = calibration(observations)
        self.assertNotEqual(
            report.state.value,
            "REVIEW_REQUIRED",
        )
        diagnostic = ExceptionIndependenceEngine().evaluate(
            observations=observations,
            calibration=report,
            policy=independence_policy(),
        )
        self.assertEqual(
            diagnostic.state,
            ExceptionIndependenceState.REVIEW_REQUIRED,
        )
        self.assertLess(
            diagnostic.christoffersen_p_value,
            Decimal("0.05"),
        )

    def test_gaps_are_not_silently_treated_as_transitions(self):
        observations = list(
            backtest_observation(index)
            for index in range(20)
        )
        observations[10] = replace(
            observations[10],
            period_start=observations[10].period_start
            + timedelta(hours=1),
            period_end=observations[10].period_end
            + timedelta(hours=1),
        )
        observations[10] = replace(
            observations[10],
            observation_id=var_backtest_observation_identity(
                observations[10]
            ),
        )
        report = calibration(tuple(observations))
        diagnostic = ExceptionIndependenceEngine().evaluate(
            observations=tuple(observations),
            calibration=report,
            policy=independence_policy(),
        )
        self.assertEqual(
            diagnostic.state,
            ExceptionIndependenceState.INSUFFICIENT_EVIDENCE,
        )
        self.assertLess(
            diagnostic.contiguous_transition_count,
            19,
        )

    def test_clean_combined_evidence_can_be_within_research_policy(self):
        observations = tuple(
            backtest_observation(
                index,
                exception=(index == 10),
            )
            for index in range(20)
        )
        report = calibration(observations)
        diagnostic = ExceptionIndependenceEngine().evaluate(
            observations=observations,
            calibration=report,
            policy=independence_policy(),
        )
        risk_cube = cube()
        dossier = RiskReviewEngine().review(
            cube=risk_cube,
            historical_risk=historical_risk(risk_cube),
            historical_risk_policy=historical_policy(),
            calibration=report,
            independence=diagnostic,
            policy=review_policy(),
            reviewed_at=AT + timedelta(hours=1),
            reviewer="risk-reviewer",
            independent_challenger="independent-risk-challenger",
            limitations=(
                "Historical simulation remains backward-looking.",
                "Stress scenarios do not have assigned probabilities.",
            ),
            challenger_objections=(
                "Continue collecting prospective calibration history.",
            ),
            unresolved_objections=(),
            evidence_references=("risk-review:evidence",),
        )
        self.assertEqual(
            dossier.state,
            RiskReviewState.WITHIN_POLICY,
        )
        self.assertEqual(dossier.approval_authority, "NONE")
        self.assertEqual(dossier.live_authority, "NONE")
        self.assertEqual(dossier.order_authority, "NONE")
        self.assertEqual(dossier.capital_authority, "NONE")

    def test_unresolved_challenger_objection_forces_review(self):
        observations = tuple(
            backtest_observation(
                index,
                exception=(index == 10),
            )
            for index in range(20)
        )
        report = calibration(observations)
        diagnostic = ExceptionIndependenceEngine().evaluate(
            observations=observations,
            calibration=report,
            policy=independence_policy(),
        )
        risk_cube = cube()
        dossier = RiskReviewEngine().review(
            cube=risk_cube,
            historical_risk=historical_risk(risk_cube),
            historical_risk_policy=historical_policy(),
            calibration=report,
            independence=diagnostic,
            policy=review_policy(),
            reviewed_at=AT + timedelta(hours=1),
            reviewer="risk-reviewer",
            independent_challenger="independent-risk-challenger",
            limitations=("Shadow evidence is limited.",),
            challenger_objections=(
                "Concentration needs separate remediation.",
            ),
            unresolved_objections=(
                "Concentration needs separate remediation.",
            ),
            evidence_references=("risk-review:evidence",),
        )
        self.assertEqual(
            dossier.state,
            RiskReviewState.REVIEW_REQUIRED,
        )

    def test_concentration_policy_breach_forces_review(self):
        observations = tuple(
            backtest_observation(
                index,
                exception=(index == 10),
            )
            for index in range(20)
        )
        report = calibration(observations)
        diagnostic = ExceptionIndependenceEngine().evaluate(
            observations=observations,
            calibration=report,
            policy=independence_policy(),
        )
        risk_cube = cube()
        dossier = RiskReviewEngine().review(
            cube=risk_cube,
            historical_risk=historical_risk(risk_cube),
            historical_risk_policy=historical_policy(),
            calibration=report,
            independence=diagnostic,
            policy=review_policy(
                maximum_base_value_concentration=Decimal("0.50")
            ),
            reviewed_at=AT + timedelta(hours=1),
            reviewer="risk-reviewer",
            independent_challenger="independent-risk-challenger",
            limitations=("Concentration metric is NPV-based.",),
            challenger_objections=(),
            unresolved_objections=(),
            evidence_references=("risk-review:evidence",),
        )
        self.assertEqual(
            dossier.state,
            RiskReviewState.REVIEW_REQUIRED,
        )


if __name__ == "__main__":
    unittest.main()
