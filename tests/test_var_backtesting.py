import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from quantos.historical_simulation_risk import (
    HistoricalQuantileConvention,
    HistoricalSimulationRiskEstimate,
    HistoricalSimulationRiskPolicy,
    HistoricalSimulationWeighting,
    historical_simulation_risk_estimate_identity,
)
from quantos.var_backtesting import (
    ProspectivePortfolioOutcome,
    VaRBacktestObservationEngine,
    VaRBacktestStore,
    VaRCalibrationEngine,
    VaRCalibrationPolicy,
    VaRCalibrationState,
)

UTC = timezone.utc
BASE_TIME = datetime(2026, 1, 1, 12, tzinfo=UTC)


def risk_policy(
    *,
    valuation_time=BASE_TIME,
    policy_suffix="a",
):
    return HistoricalSimulationRiskPolicy(
        confidence_level=Decimal("0.95"),
        minimum_observations=20,
        horizon_seconds=86400,
        window_start=valuation_time - timedelta(days=100),
        window_end=valuation_time - timedelta(days=1),
        weighting=HistoricalSimulationWeighting.EQUAL_WEIGHT,
        quantile_convention=(
            HistoricalQuantileConvention.NEAREST_RANK
        ),
        missing_data_policy="FAIL_CLOSED",
        rationale="Backtest risk policy.",
        evidence_references=(
            f"risk-policy:{policy_suffix}",
        ),
    )


def estimate(
    index=0,
    *,
    var_loss=Decimal("10"),
    es_loss=Decimal("15"),
    confidence=Decimal("0.95"),
):
    valuation_time = BASE_TIME + timedelta(days=index * 2)
    policy = risk_policy(
        valuation_time=valuation_time,
        policy_suffix=str(index),
    )
    draft = HistoricalSimulationRiskEstimate(
        estimate_id="placeholder",
        cube_id=(
            "portfolio-risk-cube:"
            + f"{index:064x}"[-64:]
        ),
        policy_id=policy.policy_id,
        base_snapshot_id=(
            "market-data-snapshot:"
            + f"{index + 100:064x}"[-64:]
        ),
        valuation_time=valuation_time,
        reporting_currency="USD",
        observation_ids=tuple(
            f"historical-scenario-observation:{i:064x}"
            for i in range(20)
        ),
        observation_count=20,
        confidence_level=confidence,
        weighting=HistoricalSimulationWeighting.EQUAL_WEIGHT,
        quantile_convention=(
            HistoricalQuantileConvention.NEAREST_RANK
        ),
        tail_count=1,
        ordered_losses=(),
        value_at_risk_loss=var_loss,
        expected_shortfall_loss=es_loss,
        worst_loss=Decimal("20"),
        best_loss=Decimal("-5"),
        mean_loss=Decimal("2"),
        value_at_risk_on_gross_base=Decimal("0.10"),
        expected_shortfall_on_gross_base=Decimal("0.15"),
        diagnostics=("fixture",),
        var_authority="RESEARCH_ONLY",
        order_authority="NONE",
        capital_authority="NONE",
        caveat="fixture",
    )
    return (
        replace(
            draft,
            estimate_id=historical_simulation_risk_estimate_identity(
                draft
            ),
        ),
        policy,
    )


def outcome(
    estimate_item,
    realized_pnl,
    *,
    start=None,
):
    start = start or estimate_item.valuation_time
    return ProspectivePortfolioOutcome(
        estimate_id=estimate_item.estimate_id,
        cube_id=estimate_item.cube_id,
        period_start=start,
        period_end=start + timedelta(days=1),
        recorded_at=start + timedelta(days=1, minutes=1),
        realized_pnl=Decimal(realized_pnl),
        source_fact_ids=(
            f"realized-pnl:{estimate_item.estimate_id}",
        ),
        evidence_references=("realized-pnl:evidence",),
    )


def observation(
    index,
    *,
    realized_pnl="-5",
    var_loss=Decimal("10"),
):
    estimate_item, policy = estimate(
        index,
        var_loss=var_loss,
    )
    return VaRBacktestObservationEngine().observe(
        estimate=estimate_item,
        risk_policy=policy,
        outcome=outcome(
            estimate_item,
            realized_pnl,
        ),
    )


def calibration_policy(minimum=20):
    return VaRCalibrationPolicy(
        minimum_observations=minimum,
        kupiec_significance_level=Decimal("0.05"),
        rationale="Prospective VaR calibration gate.",
        evidence_references=("var-calibration:policy",),
    )


class VaRBacktestingTests(unittest.TestCase):
    def test_realized_loss_strictly_above_var_is_exception(self):
        estimate_item, policy = estimate()
        item = VaRBacktestObservationEngine().observe(
            estimate=estimate_item,
            risk_policy=policy,
            outcome=outcome(estimate_item, "-12"),
        )
        self.assertTrue(item.var_exception)
        self.assertEqual(item.realized_loss, Decimal("12"))
        self.assertEqual(
            item.var_exception_magnitude,
            Decimal("2"),
        )
        self.assertEqual(item.approval_authority, "NONE")
        self.assertEqual(item.order_authority, "NONE")
        self.assertEqual(item.capital_authority, "NONE")

    def test_realized_loss_equal_to_var_is_not_exception(self):
        estimate_item, policy = estimate()
        item = VaRBacktestObservationEngine().observe(
            estimate=estimate_item,
            risk_policy=policy,
            outcome=outcome(estimate_item, "-10"),
        )
        self.assertFalse(item.var_exception)
        self.assertEqual(
            item.var_exception_magnitude,
            Decimal("0"),
        )

    def test_outcome_must_start_at_frozen_forecast_time(self):
        estimate_item, policy = estimate()
        with self.assertRaises(ValueError):
            VaRBacktestObservationEngine().observe(
                estimate=estimate_item,
                risk_policy=policy,
                outcome=outcome(
                    estimate_item,
                    "-5",
                    start=estimate_item.valuation_time
                    + timedelta(seconds=1),
                ),
            )

    def test_risk_policy_binding_is_exact(self):
        estimate_item, _ = estimate()
        wrong_policy = risk_policy(
            valuation_time=estimate_item.valuation_time,
            policy_suffix="wrong",
        )
        with self.assertRaises(ValueError):
            VaRBacktestObservationEngine().observe(
                estimate=estimate_item,
                risk_policy=wrong_policy,
                outcome=outcome(estimate_item, "-5"),
            )

    def test_one_exception_in_twenty_does_not_trigger_kupiec_review(self):
        observations = tuple(
            observation(
                index,
                realized_pnl=(
                    "-12" if index == 19 else "-5"
                ),
            )
            for index in range(20)
        )
        report = VaRCalibrationEngine().evaluate(
            observations=observations,
            policy=calibration_policy(),
        )
        self.assertEqual(report.exception_count, 1)
        self.assertEqual(
            report.exception_rate,
            Decimal("0.05"),
        )
        self.assertEqual(
            report.expected_exception_rate,
            Decimal("0.05"),
        )
        self.assertEqual(
            report.state,
            VaRCalibrationState.WITHIN_TEST_TOLERANCE,
        )
        self.assertIsNotNone(report.kupiec_p_value)
        self.assertGreaterEqual(
            report.kupiec_p_value,
            Decimal("0.05"),
        )

    def test_many_exceptions_trigger_kupiec_review(self):
        observations = tuple(
            observation(
                index,
                realized_pnl=(
                    "-12" if index < 10 else "-5"
                ),
            )
            for index in range(20)
        )
        report = VaRCalibrationEngine().evaluate(
            observations=observations,
            policy=calibration_policy(),
        )
        self.assertEqual(report.exception_count, 10)
        self.assertEqual(
            report.state,
            VaRCalibrationState.REVIEW_REQUIRED,
        )
        self.assertLess(
            report.kupiec_p_value,
            Decimal("0.05"),
        )

    def test_short_calibration_history_is_insufficient_not_green(self):
        observations = tuple(
            observation(index)
            for index in range(10)
        )
        report = VaRCalibrationEngine().evaluate(
            observations=observations,
            policy=calibration_policy(),
        )
        self.assertEqual(
            report.state,
            VaRCalibrationState.INSUFFICIENT_EVIDENCE,
        )
        self.assertIsNone(report.kupiec_p_value)

    def test_mixed_confidence_levels_fail_closed(self):
        items = [
            observation(index)
            for index in range(20)
        ]
        items[-1] = replace(
            items[-1],
            confidence_level=Decimal("0.99"),
        )
        with self.assertRaises(ValueError):
            VaRCalibrationEngine().evaluate(
                observations=tuple(items),
                policy=calibration_policy(),
            )

    def test_store_is_idempotent_and_one_outcome_per_estimate(self):
        item = observation(0)
        report = VaRCalibrationEngine().evaluate(
            observations=tuple(
                observation(index)
                for index in range(20)
            ),
            policy=calibration_policy(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = VaRBacktestStore(
                Path(tmp) / "var-backtest.duckdb"
            )
            self.assertTrue(store.add_observation(item))
            self.assertFalse(store.add_observation(item))
            self.assertTrue(store.add_report(report))
            self.assertFalse(store.add_report(report))
            store.close()


if __name__ == "__main__":
    unittest.main()
