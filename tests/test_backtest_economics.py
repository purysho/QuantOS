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

UTC = timezone.utc
BASE = datetime(2020, 1, 1, 16, tzinfo=UTC)
FACTOR = "factor-spec:" + "a" * 64
PLAN = "walk-forward-plan:" + "b" * 64


def factor_run(day, values):
    return FactorRun(
        run_id=f"factor-run:{day}",
        factor_id=FACTOR,
        universe_id=f"investable-universe:{day}",
        decision_time=BASE + timedelta(days=day),
        scores=tuple(
            FactorScore(
                security_id=security_id,
                score=Decimal(score),
                components=(),
            )
            for security_id, score in values
        ),
        exclusions=(),
    )


def portfolio(mode=PortfolioMode.LONG_ONLY, **overrides):
    values = {
        "mode": mode,
        "long_count": 2,
        "short_count": 0 if mode is PortfolioMode.LONG_ONLY else 1,
        "long_gross": Decimal("1") if mode is PortfolioMode.LONG_ONLY else Decimal("0.5"),
        "short_gross": Decimal("0") if mode is PortfolioMode.LONG_ONLY else Decimal("0.5"),
        "maximum_absolute_weight": Decimal("0.5"),
        "allow_boundary_tie_break": False,
        "rationale": "Deterministic rank portfolio.",
        "evidence_references": ("policy:portfolio",),
    }
    values.update(overrides)
    return PortfolioConstructionPolicy(**values)


def costs(**overrides):
    values = {
        "commission_bps": Decimal("1"),
        "half_spread_bps": Decimal("2"),
        "slippage_bps": Decimal("1"),
        "market_impact_bps": Decimal("1"),
        "annual_borrow_bps": Decimal("100"),
        "rationale": "Explicit conservative implementation costs.",
        "evidence_references": ("policy:costs",),
    }
    values.update(overrides)
    return CostModel(**values)


def policy(mode=PortfolioMode.LONG_ONLY, **cost_overrides):
    return BacktestPolicy(
        portfolio=portfolio(mode),
        costs=costs(**cost_overrides),
        required_benchmarks=(
            BenchmarkKind.CASH,
            BenchmarkKind.MARKET_CAP,
            BenchmarkKind.EQUAL_WEIGHT,
            BenchmarkKind.INVERSE_VOL,
        ),
        rationale="Compare candidate against mandatory baselines.",
        evidence_references=("policy:backtest",),
    )


def security_return(security_id, start, end, value, *, delisted=False):
    return SecurityPeriodReturn(
        security_id=security_id,
        start_time=start,
        end_time=end,
        total_return=Decimal(value),
        delisted=delisted,
        source_fact_ids=(f"price:{security_id}:{start.date()}",),
        corporate_action_fact_ids=(
            (f"action:{security_id}",) if delisted else ()
        ),
    )


def benchmarks(start, end):
    return tuple(
        BenchmarkPeriodReturn(
            kind=kind,
            benchmark_id=f"benchmark:{kind.value}",
            start_time=start,
            end_time=end,
            total_return=Decimal("0.01") if kind is not BenchmarkKind.CASH else Decimal("0"),
            source_fact_ids=(f"benchmark-return:{kind.value}:{start.date()}",),
        )
        for kind in (
            BenchmarkKind.CASH,
            BenchmarkKind.MARKET_CAP,
            BenchmarkKind.EQUAL_WEIGHT,
            BenchmarkKind.INVERSE_VOL,
        )
    )


def period(day, values=None, returns=None):
    run = factor_run(
        day,
        values or (
            ("SEC:A", "3"),
            ("SEC:B", "2"),
            ("SEC:C", "1"),
            ("SEC:D", "0"),
        ),
    )
    start = run.decision_time + timedelta(minutes=1)
    end = start + timedelta(days=1)
    return RebalancePeriod(
        factor_run=run,
        holding_start=start,
        holding_end=end,
        security_returns=tuple(
            security_return(security_id, start, end, value)
            for security_id, value in (
                returns
                or (
                    ("SEC:A", "0.10"),
                    ("SEC:B", "0.00"),
                    ("SEC:C", "-0.02"),
                    ("SEC:D", "-0.05"),
                )
            )
        ),
        benchmark_returns=benchmarks(start, end),
    )


class BacktestEconomicsTests(unittest.TestCase):
    def test_long_only_selects_top_scores(self):
        result = BacktestEngine().run(
            factor_id=FACTOR,
            validation_plan_id=PLAN,
            policy=policy(),
            periods=(period(0),),
        )
        positions = {
            item.security_id: item.weight
            for item in result.periods[0].target_positions
        }
        self.assertEqual(
            positions,
            {"SEC:A": Decimal("0.5"), "SEC:B": Decimal("0.5")},
        )

    def test_transaction_cost_reduces_net_return(self):
        result = BacktestEngine().run(
            factor_id=FACTOR,
            validation_plan_id=PLAN,
            policy=policy(
                commission_bps=Decimal("2"),
                half_spread_bps=Decimal("3"),
                slippage_bps=Decimal("4"),
                market_impact_bps=Decimal("1"),
            ),
            periods=(period(0),),
        )
        first = result.periods[0]
        self.assertEqual(first.traded_notional_ratio, Decimal("1.0"))
        self.assertEqual(first.transaction_cost, Decimal("0.0010"))
        self.assertEqual(
            first.net_return,
            first.gross_return - first.transaction_cost,
        )

    def test_short_book_pays_borrow_cost(self):
        result = BacktestEngine().run(
            factor_id=FACTOR,
            validation_plan_id=PLAN,
            policy=policy(PortfolioMode.LONG_SHORT),
            periods=(period(0),),
        )
        first = result.periods[0]
        self.assertGreater(first.borrow_cost, Decimal("0"))
        weights = {
            item.security_id: item.weight
            for item in first.target_positions
        }
        self.assertEqual(weights["SEC:D"], Decimal("-0.5"))

    def test_missing_return_for_held_security_fails_closed(self):
        broken = period(
            0,
            returns=(
                ("SEC:A", "0.10"),
                ("SEC:C", "-0.02"),
                ("SEC:D", "-0.05"),
            ),
        )
        with self.assertRaises(ValueError):
            BacktestEngine().run(
                factor_id=FACTOR,
                validation_plan_id=PLAN,
                policy=policy(),
                periods=(broken,),
            )

    def test_delisting_return_requires_corporate_action_lineage(self):
        start = BASE
        end = BASE + timedelta(days=1)
        with self.assertRaises(ValueError):
            SecurityPeriodReturn(
                security_id="SEC:X",
                start_time=start,
                end_time=end,
                total_return=Decimal("-1"),
                delisted=True,
                source_fact_ids=("price:SEC:X",),
                corporate_action_fact_ids=(),
            )

    def test_boundary_tie_fails_without_explicit_tie_break_policy(self):
        tied = period(
            0,
            values=(
                ("SEC:A", "3"),
                ("SEC:B", "2"),
                ("SEC:C", "2"),
                ("SEC:D", "1"),
            ),
        )
        with self.assertRaises(ValueError):
            BacktestEngine().run(
                factor_id=FACTOR,
                validation_plan_id=PLAN,
                policy=policy(),
                periods=(tied,),
            )

    def test_all_four_baselines_are_mandatory(self):
        with self.assertRaises(ValueError):
            BacktestPolicy(
                portfolio=portfolio(),
                costs=costs(),
                required_benchmarks=(
                    BenchmarkKind.CASH,
                    BenchmarkKind.MARKET_CAP,
                ),
                rationale="Incomplete baseline set.",
                evidence_references=("policy:bad",),
            )

    def test_drifted_weights_affect_next_rebalance_trade_notional(self):
        first = period(0)
        second = period(2)
        result = BacktestEngine().run(
            factor_id=FACTOR,
            validation_plan_id=PLAN,
            policy=policy(
                commission_bps=Decimal("0"),
                half_spread_bps=Decimal("0"),
                slippage_bps=Decimal("0"),
                market_impact_bps=Decimal("0"),
            ),
            periods=(first, second),
        )
        self.assertGreater(
            result.periods[1].traded_notional_ratio,
            Decimal("0"),
        )
        self.assertLess(
            result.periods[1].traded_notional_ratio,
            Decimal("1"),
        )

    def test_holding_periods_cannot_overlap(self):
        first = period(0)
        second = period(0)
        with self.assertRaises(ValueError):
            BacktestEngine().run(
                factor_id=FACTOR,
                validation_plan_id=PLAN,
                policy=policy(),
                periods=(first, second),
            )


if __name__ == "__main__":
    unittest.main()
