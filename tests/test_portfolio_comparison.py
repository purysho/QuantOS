import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from quantos.covariance import (
    CovarianceEstimationPolicy,
    CovarianceEstimatorKind,
    SkfolioCovarianceEstimator,
)
from quantos.model_registry import (
    ModelLifecycleStage,
    ResearchRunManifest,
)
from quantos.portfolio_comparison import (
    OutOfSampleBenchmarkReturn,
    OutOfSampleSecurityReturn,
    PortfolioComparisonDossierStore,
    PortfolioComparisonEngine,
    PortfolioComparisonFold,
    PortfolioComparisonPolicy,
    PortfolioMethod,
)
from quantos.portfolio_construction import (
    BaselineAllocator,
    PortfolioConstraintPolicy,
    PortfolioDatasetBuilder,
    PortfolioReturnObservation,
    SkfolioBaselineAllocator,
)
from quantos.portfolio_optimization import (
    MinimumVariancePolicy,
    SkfolioMinimumVarianceOptimizer,
)

UTC = timezone.utc
AT = datetime(2026, 9, 25, 12, tzinfo=UTC)


def manifest():
    return ResearchRunManifest(
        manifest_id="research-run-manifest:" + "m" * 64,
        model_id="research-model:" + "r" * 64,
        experiment_id="research-experiment:" + "e" * 64,
        research_case_id="research-case:" + "c" * 64,
        case_dossier_fingerprint=None,
        factor_id="factor-spec:" + "f" * 64,
        universe_policy_id="universe-policy:" + "u" * 64,
        validation_plan_id="walk-forward-plan:" + "v" * 64,
        backtest_id="economic-backtest:" + "b" * 64,
        backtest_policy_id="backtest-policy:" + "q" * 64,
        performance_analysis_id="performance-analysis:" + "a" * 64,
        performance_policy_id="performance-policy:" + "p" * 64,
        multiple_testing_audit_id="multiple-testing-audit:" + "t" * 64,
        overfitting_policy_id="multiple-testing-policy:" + "o" * 64,
        selected_variant_id="selected",
        selected_variant_series_id="variant-return-series:" + "s" * 64,
        shadow_permit_id=None,
        benchmark_kinds=("CASH", "EQUAL_WEIGHT", "INVERSE_VOL", "MARKET_CAP"),
        dataset_fingerprints=("dataset:prices",),
        code_revision="git:stage10",
        evidence_references=("manifest:evidence",),
        eligible_stage=ModelLifecycleStage.BACKTESTED,
    )


def constraint_policy():
    return PortfolioConstraintPolicy(
        fully_invested=True,
        long_only=True,
        minimum_weight=Decimal("0"),
        maximum_weight=Decimal("0.90"),
        maximum_gross_exposure=Decimal("1"),
        maximum_one_way_turnover=None,
        rationale="Common Stage 10.4 comparison constraints.",
        evidence_references=("policy:portfolio",),
    )


def training_dataset(decision_time):
    values = {
        "SEC:A": ("0.01", "0.03", "-0.02", "0.02", "0.00", "0.04", "0.01", "-0.01", "0.02", "0.00"),
        "SEC:B": ("0.010", "0.011", "0.009", "0.010", "0.012", "0.008", "0.011", "0.009", "0.010", "0.011"),
        "SEC:C": ("-0.02", "0.04", "-0.03", "0.05", "-0.01", "0.03", "-0.04", "0.02", "0.05", "-0.02"),
        "SEC:D": ("0.00", "0.02", "0.01", "-0.01", "0.03", "0.01", "0.00", "0.02", "-0.01", "0.01"),
    }
    rows = []
    for security_id, returns in values.items():
        for index, value in enumerate(returns):
            start = decision_time - timedelta(days=11 - index)
            end = start + timedelta(hours=12)
            rows.append(
                PortfolioReturnObservation(
                    security_id=security_id,
                    period_start=start,
                    period_end=end,
                    knowledge_time=end + timedelta(minutes=1),
                    total_return=Decimal(value),
                    source_fact_ids=(f"return:{security_id}:{index}:{decision_time.date()}",),
                )
            )
    return PortfolioDatasetBuilder().build(
        manifest=manifest(),
        decision_time=decision_time,
        observations=tuple(rows),
        minimum_periods=8,
    )


def solutions(decision_time):
    data = training_dataset(decision_time)
    constraints = constraint_policy()
    allocator = SkfolioBaselineAllocator()
    baselines = (
        allocator.allocate(
            dataset=data,
            allocator=BaselineAllocator.EQUAL_WEIGHT,
            constraints=constraints,
        ),
        allocator.allocate(
            dataset=data,
            allocator=BaselineAllocator.INVERSE_VOLATILITY,
            constraints=constraints,
        ),
    )
    covariance = SkfolioCovarianceEstimator().estimate(
        dataset=data,
        policy=CovarianceEstimationPolicy(
            estimator=CovarianceEstimatorKind.LEDOIT_WOLF,
            minimum_observations=8,
            nearest=True,
            higham=False,
            higham_max_iteration=100,
            empirical_ddof=1,
            require_positive_semidefinite=True,
            maximum_condition_number=None,
            rationale="Stage 10.4 frozen covariance.",
            evidence_references=("policy:covariance",),
        ),
    )
    optimized = SkfolioMinimumVarianceOptimizer().optimize(
        dataset=data,
        covariance=covariance,
        constraints=constraints,
        policy=MinimumVariancePolicy(
            solver="CLARABEL",
            l2_regularization=Decimal("0"),
            rationale="Stage 10.4 minimum variance.",
            evidence_references=("policy:min-var",),
        ),
        baselines=baselines,
    )
    return baselines, optimized


def fold(number, start, realized):
    baselines, optimized = solutions(start - timedelta(hours=1))
    end = start + timedelta(days=1)
    returns = tuple(
        OutOfSampleSecurityReturn(
            security_id=security_id,
            period_start=start,
            period_end=end,
            total_return=Decimal(value),
            source_fact_ids=(f"oos:{number}:{security_id}",),
        )
        for security_id, value in realized.items()
    )
    return PortfolioComparisonFold(
        fold_number=number,
        period_start=start,
        period_end=end,
        baselines=baselines,
        optimized=optimized,
        security_returns=returns,
        market_benchmark=OutOfSampleBenchmarkReturn(
            benchmark_id="benchmark:market-cap",
            period_start=start,
            period_end=end,
            total_return=Decimal("0.005"),
            source_fact_ids=(f"benchmark:{number}",),
        ),
    )


def folds():
    return (
        fold(
            0,
            AT + timedelta(days=2),
            {
                "SEC:A": "0.03",
                "SEC:B": "0.01",
                "SEC:C": "-0.02",
                "SEC:D": "0.00",
            },
        ),
        fold(
            1,
            AT + timedelta(days=4),
            {
                "SEC:A": "-0.02",
                "SEC:B": "0.01",
                "SEC:C": "0.04",
                "SEC:D": "0.00",
            },
        ),
        fold(
            2,
            AT + timedelta(days=6),
            {
                "SEC:A": "0.01",
                "SEC:B": "0.009",
                "SEC:C": "-0.01",
                "SEC:D": "0.02",
            },
        ),
    )


def comparison_policy(**overrides):
    values = {
        "minimum_folds": 2,
        "implementation_cost_bps_per_traded_notional": Decimal("5"),
        "expected_shortfall_confidence": Decimal("0.95"),
        "rationale": "Common OOS comparison without automatic selection.",
        "evidence_references": ("policy:comparison",),
    }
    values.update(overrides)
    return PortfolioComparisonPolicy(**values)


class PortfolioComparisonTests(unittest.TestCase):
    def test_three_methods_are_evaluated_on_identical_oos_folds(self):
        dossier = PortfolioComparisonEngine().evaluate(
            folds=folds(),
            policy=comparison_policy(),
        )
        self.assertEqual(
            {item.method for item in dossier.evaluations},
            {
                PortfolioMethod.EQUAL_WEIGHT,
                PortfolioMethod.INVERSE_VOLATILITY,
                PortfolioMethod.MINIMUM_VARIANCE,
            },
        )
        self.assertEqual(
            {len(item.fold_outcomes) for item in dossier.evaluations},
            {3},
        )
        self.assertEqual(dossier.selection_authority, "NONE")
        self.assertEqual(dossier.capital_authority, "NONE")
        self.assertFalse(hasattr(dossier, "selected_method"))

    def test_implementation_cost_is_applied_to_security_traded_notional(self):
        dossier = PortfolioComparisonEngine().evaluate(
            folds=folds(),
            policy=comparison_policy(
                implementation_cost_bps_per_traded_notional=Decimal("10")
            ),
        )
        equal = next(
            item for item in dossier.evaluations
            if item.method is PortfolioMethod.EQUAL_WEIGHT
        )
        first = equal.fold_outcomes[0]
        self.assertEqual(first.implementation_cost_rate, Decimal("0.0010"))
        self.assertEqual(
            first.net_return,
            first.gross_return - first.implementation_cost_rate,
        )

    def test_realized_risk_metrics_are_oos_not_solver_objective(self):
        dossier = PortfolioComparisonEngine().evaluate(
            folds=folds(),
            policy=comparison_policy(),
        )
        optimized = next(
            item for item in dossier.evaluations
            if item.method is PortfolioMethod.MINIMUM_VARIANCE
        )
        self.assertGreaterEqual(
            optimized.realized_period_volatility,
            Decimal("0"),
        )
        self.assertEqual(len(optimized.fold_outcomes), 3)

    def test_missing_oos_security_return_fails_closed(self):
        first, *rest = folds()
        broken = PortfolioComparisonFold(
            fold_number=first.fold_number,
            period_start=first.period_start,
            period_end=first.period_end,
            baselines=first.baselines,
            optimized=first.optimized,
            security_returns=first.security_returns[:-1],
            market_benchmark=first.market_benchmark,
        )
        with self.assertRaises(ValueError):
            PortfolioComparisonEngine().evaluate(
                folds=(broken, *rest),
                policy=comparison_policy(),
            )

    def test_overlapping_oos_folds_fail_closed(self):
        first, second, _ = folds()
        overlapping = PortfolioComparisonFold(
            fold_number=1,
            period_start=first.period_start + timedelta(hours=12),
            period_end=first.period_end + timedelta(hours=12),
            baselines=second.baselines,
            optimized=second.optimized,
            security_returns=tuple(
                OutOfSampleSecurityReturn(
                    security_id=item.security_id,
                    period_start=first.period_start + timedelta(hours=12),
                    period_end=first.period_end + timedelta(hours=12),
                    total_return=item.total_return,
                    source_fact_ids=item.source_fact_ids,
                )
                for item in second.security_returns
            ),
            market_benchmark=OutOfSampleBenchmarkReturn(
                benchmark_id=second.market_benchmark.benchmark_id,
                period_start=first.period_start + timedelta(hours=12),
                period_end=first.period_end + timedelta(hours=12),
                total_return=second.market_benchmark.total_return,
                source_fact_ids=second.market_benchmark.source_fact_ids,
            ),
        )
        with self.assertRaises(ValueError):
            PortfolioComparisonEngine().evaluate(
                folds=(first, overlapping),
                policy=comparison_policy(),
            )

    def test_too_few_folds_do_not_produce_comparison(self):
        with self.assertRaises(ValueError):
            PortfolioComparisonEngine().evaluate(
                folds=(folds()[0],),
                policy=comparison_policy(),
            )

    def test_store_is_idempotent(self):
        dossier = PortfolioComparisonEngine().evaluate(
            folds=folds(),
            policy=comparison_policy(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = PortfolioComparisonDossierStore(
                Path(tmp) / "comparison.duckdb"
            )
            self.assertTrue(store.add(dossier))
            self.assertFalse(store.add(dossier))
            store.close()


if __name__ == "__main__":
    unittest.main()
