import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from quantos.backtest_economics import (
    BacktestEngine,
    BacktestPolicy,
    BenchmarkKind,
    BenchmarkPeriodReturn,
    CostModel,
    PortfolioConstructionPolicy,
    PortfolioMode,
    RebalancePeriod,
    SecurityPeriodReturn,
)
from quantos.factor_contracts import FactorRun, FactorScore
from quantos.performance_analytics import (
    MetricStatus,
    PerformanceAnalyticsEngine,
    PerformancePolicy,
)

UTC = timezone.utc
BASE = datetime(2020, 1, 1, 16, tzinfo=UTC)
FACTOR = "factor-spec:" + "a" * 64
PLAN = "walk-forward-plan:" + "b" * 64


def factor_run(day, scores):
    return FactorRun(
        run_id=f"factor-run:{day}",
        factor_id=FACTOR,
        universe_id=f"investable-universe:{day}",
        decision_time=BASE + timedelta(days=day),
        scores=tuple(
            FactorScore(security_id, Decimal(score), ())
            for security_id, score in scores
        ),
        exclusions=(),
    )


def benchmarks(start, end, market="0.01"):
    values = {
        BenchmarkKind.CASH: Decimal("0"),
        BenchmarkKind.MARKET_CAP: Decimal(market),
        BenchmarkKind.EQUAL_WEIGHT: Decimal("0.005"),
        BenchmarkKind.INVERSE_VOL: Decimal("0.004"),
    }
    return tuple(
        BenchmarkPeriodReturn(
            kind=kind,
            benchmark_id=f"benchmark:{kind.value}",
            start_time=start,
            end_time=end,
            total_return=value,
            source_fact_ids=(f"benchmark:{kind.value}:{start.date()}",),
        )
        for kind, value in values.items()
    )


def rebalance(day, returns, market="0.01"):
    scores = (
        ("SEC:A", "4"),
        ("SEC:B", "3"),
        ("SEC:C", "2"),
        ("SEC:D", "1"),
    )
    run = factor_run(day, scores)
    start = run.decision_time + timedelta(minutes=1)
    end = start + timedelta(days=1)
    return RebalancePeriod(
        factor_run=run,
        holding_start=start,
        holding_end=end,
        security_returns=tuple(
            SecurityPeriodReturn(
                security_id=security_id,
                start_time=start,
                end_time=end,
                total_return=Decimal(value),
                delisted=False,
                source_fact_ids=(f"return:{security_id}:{day}",),
            )
            for security_id, value in returns
        ),
        benchmark_returns=benchmarks(start, end, market),
    )


def backtest_policy():
    return BacktestPolicy(
        portfolio=PortfolioConstructionPolicy(
            mode=PortfolioMode.LONG_ONLY,
            long_count=2,
            short_count=0,
            long_gross=Decimal("1"),
            short_gross=Decimal("0"),
            maximum_absolute_weight=Decimal("0.5"),
            allow_boundary_tie_break=False,
            rationale="Top two equally weighted.",
            evidence_references=("policy:portfolio",),
        ),
        costs=CostModel(
            commission_bps=Decimal("1"),
            half_spread_bps=Decimal("1"),
            slippage_bps=Decimal("1"),
            market_impact_bps=Decimal("1"),
            annual_borrow_bps=Decimal("0"),
            rationale="Explicit costs.",
            evidence_references=("policy:cost",),
        ),
        required_benchmarks=(
            BenchmarkKind.CASH,
            BenchmarkKind.MARKET_CAP,
            BenchmarkKind.EQUAL_WEIGHT,
            BenchmarkKind.INVERSE_VOL,
        ),
        rationale="Mandatory baselines.",
        evidence_references=("policy:backtest",),
    )


def performance_policy(**overrides):
    values = {
        "periods_per_year": 252,
        "cvar_confidence": Decimal("0.95"),
        "minimum_periods_for_risk_metrics": 2,
        "minimum_ic_cross_section": 3,
        "rationale": "Standard research diagnostics.",
        "evidence_references": ("policy:performance",),
    }
    values.update(overrides)
    return PerformancePolicy(**values)


def source_periods():
    return (
        rebalance(
            0,
            (
                ("SEC:A", "0.04"),
                ("SEC:B", "0.03"),
                ("SEC:C", "0.02"),
                ("SEC:D", "0.01"),
            ),
        ),
        rebalance(
            2,
            (
                ("SEC:A", "-0.03"),
                ("SEC:B", "-0.02"),
                ("SEC:C", "-0.01"),
                ("SEC:D", "0.00"),
            ),
        ),
        rebalance(
            4,
            (
                ("SEC:A", "0.02"),
                ("SEC:B", "0.01"),
                ("SEC:C", "0.00"),
                ("SEC:D", "-0.01"),
            ),
        ),
    )


def backtest():
    periods = source_periods()
    return BacktestEngine().run(
        factor_id=FACTOR,
        validation_plan_id=PLAN,
        policy=backtest_policy(),
        periods=periods,
    ), periods


class PerformanceAnalyticsTests(unittest.TestCase):
    def test_analysis_separates_gross_and_net_cost_drag(self):
        result, periods = backtest()
        analysis = PerformanceAnalyticsEngine().analyze(
            backtest=result,
            policy=performance_policy(),
            source_periods=periods,
        )
        self.assertGreater(
            analysis.cumulative_gross_return,
            analysis.cumulative_net_return,
        )
        self.assertGreater(
            analysis.total_transaction_cost_rate,
            Decimal("0"),
        )
        self.assertGreater(
            analysis.terminal_wealth_cost_drag,
            Decimal("0"),
        )

    def test_maximum_drawdown_is_negative_when_wealth_falls(self):
        result, periods = backtest()
        analysis = PerformanceAnalyticsEngine().analyze(
            backtest=result,
            policy=performance_policy(),
            source_periods=periods,
        )
        self.assertLess(analysis.maximum_drawdown, Decimal("0"))

    def test_expected_shortfall_uses_worst_tail_return(self):
        result, periods = backtest()
        analysis = PerformanceAnalyticsEngine().analyze(
            backtest=result,
            policy=performance_policy(cvar_confidence=Decimal("0.80")),
            source_periods=periods,
        )
        self.assertEqual(
            analysis.expected_shortfall_return.status,
            MetricStatus.AVAILABLE,
        )
        self.assertEqual(
            analysis.expected_shortfall_return.value,
            min(item.net_return for item in result.periods),
        )

    def test_perfect_monotonic_cross_section_has_ic_one(self):
        result, periods = backtest()
        analysis = PerformanceAnalyticsEngine().analyze(
            backtest=result,
            policy=performance_policy(),
            source_periods=periods,
        )
        self.assertEqual(len(analysis.ic_periods), 3)
        self.assertEqual(
            tuple(item.value for item in analysis.ic_periods),
            (Decimal("1"), Decimal("-1"), Decimal("1")),
        )
        self.assertEqual(
            analysis.mean_information_coefficient.value,
            Decimal("0.3333333333333333333333333333"),
        )

    def test_missing_cross_section_return_is_fail_visible_not_imputed(self):
        result, periods = backtest()
        first = periods[0]
        incomplete = RebalancePeriod(
            factor_run=first.factor_run,
            holding_start=first.holding_start,
            holding_end=first.holding_end,
            security_returns=first.security_returns[:-1],
            benchmark_returns=first.benchmark_returns,
        )
        analysis = PerformanceAnalyticsEngine().analyze(
            backtest=result,
            policy=performance_policy(),
            source_periods=(incomplete, periods[1], periods[2]),
        )
        self.assertEqual(
            analysis.ic_periods[0].status,
            MetricStatus.PARTIAL,
        )
        self.assertEqual(
            analysis.mean_information_coefficient.status,
            MetricStatus.PARTIAL,
        )

    def test_benchmark_relative_metrics_exist_for_all_baselines(self):
        result, periods = backtest()
        analysis = PerformanceAnalyticsEngine().analyze(
            backtest=result,
            policy=performance_policy(),
            source_periods=periods,
        )
        self.assertEqual(
            {item.kind for item in analysis.benchmark_analytics},
            {
                BenchmarkKind.CASH,
                BenchmarkKind.MARKET_CAP,
                BenchmarkKind.EQUAL_WEIGHT,
                BenchmarkKind.INVERSE_VOL,
            },
        )

    def test_zero_variance_ratio_is_not_infinite(self):
        flat_periods = (
            rebalance(
                0,
                (
                    ("SEC:A", "0"),
                    ("SEC:B", "0"),
                    ("SEC:C", "0"),
                    ("SEC:D", "0"),
                ),
                market="0",
            ),
            rebalance(
                2,
                (
                    ("SEC:A", "0"),
                    ("SEC:B", "0"),
                    ("SEC:C", "0"),
                    ("SEC:D", "0"),
                ),
                market="0",
            ),
        )
        zero_cost = backtest_policy()
        zero_cost = BacktestPolicy(
            portfolio=zero_cost.portfolio,
            costs=CostModel(
                commission_bps=Decimal("0"),
                half_spread_bps=Decimal("0"),
                slippage_bps=Decimal("0"),
                market_impact_bps=Decimal("0"),
                annual_borrow_bps=Decimal("0"),
                rationale="Zero-cost test.",
                evidence_references=("test:zero-cost",),
            ),
            required_benchmarks=zero_cost.required_benchmarks,
            rationale=zero_cost.rationale,
            evidence_references=zero_cost.evidence_references,
        )
        result = BacktestEngine().run(
            factor_id=FACTOR,
            validation_plan_id=PLAN,
            policy=zero_cost,
            periods=flat_periods,
        )
        analysis = PerformanceAnalyticsEngine().analyze(
            backtest=result,
            policy=performance_policy(),
            source_periods=flat_periods,
        )
        self.assertEqual(
            analysis.sharpe_ratio.status,
            MetricStatus.ZERO_VARIANCE,
        )
        self.assertIsNone(analysis.sharpe_ratio.value)


if __name__ == "__main__":
    unittest.main()
