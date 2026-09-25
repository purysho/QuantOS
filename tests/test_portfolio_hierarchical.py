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
from quantos.model_registry import ModelLifecycleStage, ResearchRunManifest
from quantos.portfolio_comparison import (
    OutOfSampleBenchmarkReturn,
    OutOfSampleSecurityReturn,
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
from quantos.portfolio_hierarchical import (
    HierarchicalAllocationPolicy,
    HierarchicalAllocator,
    HierarchicalPortfolioStore,
    SkfolioHierarchicalAllocator,
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
        code_revision="git:stage10.5",
        evidence_references=("manifest:evidence",),
        eligible_stage=ModelLifecycleStage.BACKTESTED,
    )


def constraints():
    return PortfolioConstraintPolicy(
        fully_invested=True,
        long_only=True,
        minimum_weight=Decimal("0"),
        maximum_weight=Decimal("0.90"),
        maximum_gross_exposure=Decimal("1"),
        maximum_one_way_turnover=None,
        rationale="Common Stage 10.5 constraints.",
        evidence_references=("policy:portfolio",),
    )


def dataset(decision_time=AT):
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


def covariance(data):
    return SkfolioCovarianceEstimator().estimate(
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
            rationale="Stage 10.5 frozen covariance.",
            evidence_references=("policy:covariance",),
        ),
    )


def hierarchy_policy():
    return HierarchicalAllocationPolicy(
        linkage_method="WARD",
        pearson_absolute=False,
        pearson_power=Decimal("1"),
        max_clusters=None,
        herc_solver="CLARABEL",
        rationale="Variance HRP/HERC with Pearson distance and Ward linkage.",
        evidence_references=("policy:hierarchical",),
    )


def all_solutions(decision_time):
    data = dataset(decision_time)
    constraint = constraints()
    baseline_engine = SkfolioBaselineAllocator()
    baselines = (
        baseline_engine.allocate(
            dataset=data,
            allocator=BaselineAllocator.EQUAL_WEIGHT,
            constraints=constraint,
        ),
        baseline_engine.allocate(
            dataset=data,
            allocator=BaselineAllocator.INVERSE_VOLATILITY,
            constraints=constraint,
        ),
    )
    cov = covariance(data)
    optimized = SkfolioMinimumVarianceOptimizer().optimize(
        dataset=data,
        covariance=cov,
        constraints=constraint,
        policy=MinimumVariancePolicy(
            solver="CLARABEL",
            l2_regularization=Decimal("0"),
            rationale="Stage 10.5 minimum variance.",
            evidence_references=("policy:min-var",),
        ),
        baselines=baselines,
    )
    engine = SkfolioHierarchicalAllocator()
    hierarchical = (
        engine.allocate(
            dataset=data,
            covariance=cov,
            constraints=constraint,
            policy=hierarchy_policy(),
            allocator=HierarchicalAllocator.HRP,
        ),
        engine.allocate(
            dataset=data,
            covariance=cov,
            constraints=constraint,
            policy=hierarchy_policy(),
            allocator=HierarchicalAllocator.HERC,
        ),
    )
    return baselines, optimized, hierarchical


class HierarchicalPortfolioTests(unittest.TestCase):
    def test_hrp_and_herc_consume_frozen_covariance_and_record_clusters(self):
        data = dataset()
        cov = covariance(data)
        engine = SkfolioHierarchicalAllocator()
        for allocator in (
            HierarchicalAllocator.HRP,
            HierarchicalAllocator.HERC,
        ):
            solution = engine.allocate(
                dataset=data,
                covariance=cov,
                constraints=constraints(),
                policy=hierarchy_policy(),
                allocator=allocator,
            )
            self.assertEqual(
                solution.covariance_artifact_id,
                cov.artifact_id,
            )
            self.assertTrue(solution.covariance_verified)
            self.assertGreaterEqual(solution.cluster_count, 1)
            self.assertEqual(
                len(solution.cluster_labels),
                data.assets,
            )
            self.assertEqual(solution.capital_authority, "NONE")
            self.assertAlmostEqual(
                float(solution.net_exposure),
                1.0,
                places=8,
            )

    def test_hierarchical_store_is_idempotent(self):
        data = dataset()
        solution = SkfolioHierarchicalAllocator().allocate(
            dataset=data,
            covariance=covariance(data),
            constraints=constraints(),
            policy=hierarchy_policy(),
            allocator=HierarchicalAllocator.HRP,
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = HierarchicalPortfolioStore(
                Path(tmp) / "hierarchical.duckdb"
            )
            self.assertTrue(store.add(solution))
            self.assertFalse(store.add(solution))
            store.close()

    def test_common_oos_dossier_expands_to_five_methods(self):
        realized = (
            {
                "SEC:A": "0.03",
                "SEC:B": "0.01",
                "SEC:C": "-0.02",
                "SEC:D": "0.00",
            },
            {
                "SEC:A": "-0.02",
                "SEC:B": "0.01",
                "SEC:C": "0.04",
                "SEC:D": "0.00",
            },
        )
        folds = []
        for number, returns in enumerate(realized):
            start = AT + timedelta(days=2 + number * 2)
            baselines, optimized, hierarchical = all_solutions(
                start - timedelta(hours=1)
            )
            end = start + timedelta(days=1)
            folds.append(
                PortfolioComparisonFold(
                    fold_number=number,
                    period_start=start,
                    period_end=end,
                    baselines=baselines,
                    optimized=optimized,
                    security_returns=tuple(
                        OutOfSampleSecurityReturn(
                            security_id=security_id,
                            period_start=start,
                            period_end=end,
                            total_return=Decimal(value),
                            source_fact_ids=(
                                f"oos:{number}:{security_id}",
                            ),
                        )
                        for security_id, value in returns.items()
                    ),
                    market_benchmark=OutOfSampleBenchmarkReturn(
                        benchmark_id="benchmark:market-cap",
                        period_start=start,
                        period_end=end,
                        total_return=Decimal("0.005"),
                        source_fact_ids=(f"benchmark:{number}",),
                    ),
                    hierarchical=hierarchical,
                )
            )
        dossier = PortfolioComparisonEngine().evaluate(
            folds=tuple(folds),
            policy=PortfolioComparisonPolicy(
                minimum_folds=2,
                implementation_cost_bps_per_traded_notional=Decimal("5"),
                expected_shortfall_confidence=Decimal("0.95"),
                rationale="Five-method common OOS comparison.",
                evidence_references=("policy:comparison",),
            ),
        )
        self.assertEqual(
            {item.method for item in dossier.evaluations},
            {
                PortfolioMethod.EQUAL_WEIGHT,
                PortfolioMethod.INVERSE_VOLATILITY,
                PortfolioMethod.MINIMUM_VARIANCE,
                PortfolioMethod.HRP,
                PortfolioMethod.HERC,
            },
        )
        self.assertEqual(dossier.selection_authority, "NONE")
        self.assertEqual(dossier.capital_authority, "NONE")


if __name__ == "__main__":
    unittest.main()
