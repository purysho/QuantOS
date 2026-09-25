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
    SkfolioHierarchicalAllocator,
)
from quantos.portfolio_optimization import (
    MinimumVariancePolicy,
    SkfolioMinimumVarianceOptimizer,
)
from quantos.portfolio_robustness import (
    PortfolioRobustnessEngine,
    PortfolioRobustnessPolicy,
    PortfolioRobustnessState,
    PortfolioRobustnessStore,
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
        code_revision="git:stage10.6",
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
        rationale="Stage 10.6 constraints.",
        evidence_references=("policy:portfolio",),
    )


def dataset(decision_time):
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


def covariance(data, kind):
    return SkfolioCovarianceEstimator().estimate(
        dataset=data,
        policy=CovarianceEstimationPolicy(
            estimator=kind,
            minimum_observations=8,
            nearest=True,
            higham=False,
            higham_max_iteration=100,
            empirical_ddof=1,
            require_positive_semidefinite=True,
            maximum_condition_number=None,
            rationale=f"Stage 10.6 {kind.value} covariance.",
            evidence_references=(f"policy:{kind.value}",),
        ),
    )


def hierarchy_policy():
    return HierarchicalAllocationPolicy(
        linkage_method="WARD",
        pearson_absolute=False,
        pearson_power=Decimal("1"),
        max_clusters=None,
        herc_solver="CLARABEL",
        rationale="Stage 10.6 hierarchy policy.",
        evidence_references=("policy:hierarchical",),
    )


def solution_bundle(decision_time, covariance_kind=CovarianceEstimatorKind.LEDOIT_WOLF):
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
    cov = covariance(data, covariance_kind)
    min_policy = MinimumVariancePolicy(
        solver="CLARABEL",
        l2_regularization=Decimal("0"),
        rationale="Stage 10.6 min-var.",
        evidence_references=("policy:min-var",),
    )
    optimized = SkfolioMinimumVarianceOptimizer().optimize(
        dataset=data,
        covariance=cov,
        constraints=constraint,
        policy=min_policy,
        baselines=baselines,
    )
    hierarchical_engine = SkfolioHierarchicalAllocator()
    hierarchical = (
        hierarchical_engine.allocate(
            dataset=data,
            covariance=cov,
            constraints=constraint,
            policy=hierarchy_policy(),
            allocator=HierarchicalAllocator.HRP,
        ),
        hierarchical_engine.allocate(
            dataset=data,
            covariance=cov,
            constraints=constraint,
            policy=hierarchy_policy(),
            allocator=HierarchicalAllocator.HERC,
        ),
    )
    return data, baselines, cov, optimized, hierarchical, min_policy


def folds():
    realized = (
        {"SEC:A": "0.03", "SEC:B": "0.01", "SEC:C": "-0.02", "SEC:D": "0.00"},
        {"SEC:A": "-0.02", "SEC:B": "0.01", "SEC:C": "0.04", "SEC:D": "0.00"},
        {"SEC:A": "0.01", "SEC:B": "0.009", "SEC:C": "-0.01", "SEC:D": "0.02"},
    )
    output = []
    for number, returns in enumerate(realized):
        start = AT + timedelta(days=2 + number * 2)
        _, baselines, _, optimized, hierarchical, _ = solution_bundle(
            start - timedelta(hours=1)
        )
        end = start + timedelta(days=1)
        output.append(
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
                        source_fact_ids=(f"oos:{number}:{security_id}",),
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
    return tuple(output)


def comparison(fold_set):
    return PortfolioComparisonEngine().evaluate(
        folds=fold_set,
        policy=PortfolioComparisonPolicy(
            minimum_folds=2,
            implementation_cost_bps_per_traded_notional=Decimal("5"),
            expected_shortfall_confidence=Decimal("0.95"),
            rationale="Stage 10.6 common OOS dossier.",
            evidence_references=("policy:comparison",),
        ),
    )


def robustness_policy(**overrides):
    values = {
        "minimum_folds": 3,
        "maximum_adjacent_weight_turnover": Decimal("0.50"),
        "minimum_cluster_pair_agreement": Decimal("0.50"),
        "minimum_covariance_sensitivity_observations": 1,
        "maximum_covariance_sensitivity_turnover": Decimal("0.50"),
        "minimum_upper_weight_headroom": Decimal("0.01"),
        "minimum_turnover_headroom": Decimal("0"),
        "maximum_covariance_condition_number": Decimal("1000000"),
        "allow_optimal_inaccurate": False,
        "rationale": "Stage 10.6 stability and fragility policy.",
        "evidence_references": ("policy:robustness",),
    }
    values.update(overrides)
    return PortfolioRobustnessPolicy(**values)


class PortfolioRobustnessTests(unittest.TestCase):
    def test_robustness_dossier_covers_weights_clusters_and_covariance(self):
        fold_set = folds()
        dossier = comparison(fold_set)
        decision = AT + timedelta(days=1, hours=23)
        data, baselines, ledoit, ledoit_solution, _, min_policy = (
            solution_bundle(decision)
        )
        empirical = covariance(
            data,
            CovarianceEstimatorKind.EMPIRICAL,
        )
        empirical_solution = SkfolioMinimumVarianceOptimizer().optimize(
            dataset=data,
            covariance=empirical,
            constraints=constraints(),
            policy=min_policy,
            baselines=baselines,
        )
        engine = PortfolioRobustnessEngine()
        sensitivity = engine.covariance_sensitivity(
            reference_covariance=ledoit,
            alternate_covariance=empirical,
            reference_solution=ledoit_solution,
            alternate_solution=empirical_solution,
        )
        result = engine.assess(
            comparison=dossier,
            folds=fold_set,
            constraints=constraints(),
            policy=robustness_policy(),
            covariance_sensitivity=(sensitivity,),
        )
        self.assertEqual(result.state, PortfolioRobustnessState.WITHIN_POLICY)
        self.assertEqual(len(result.method_weight_stability), 5)
        self.assertEqual(len(result.cluster_stability), 2)
        self.assertEqual(len(result.covariance_sensitivity), 1)
        self.assertEqual(result.selection_authority, "NONE")
        self.assertEqual(result.capital_authority, "NONE")

    def test_zero_covariance_sensitivity_tolerance_requires_review(self):
        fold_set = folds()
        dossier = comparison(fold_set)
        decision = AT + timedelta(days=1, hours=23)
        data, baselines, ledoit, ledoit_solution, _, min_policy = (
            solution_bundle(decision)
        )
        empirical = covariance(data, CovarianceEstimatorKind.EMPIRICAL)
        empirical_solution = SkfolioMinimumVarianceOptimizer().optimize(
            dataset=data,
            covariance=empirical,
            constraints=constraints(),
            policy=min_policy,
            baselines=baselines,
        )
        engine = PortfolioRobustnessEngine()
        sensitivity = engine.covariance_sensitivity(
            reference_covariance=ledoit,
            alternate_covariance=empirical,
            reference_solution=ledoit_solution,
            alternate_solution=empirical_solution,
        )
        result = engine.assess(
            comparison=dossier,
            folds=fold_set,
            constraints=constraints(),
            policy=robustness_policy(
                maximum_covariance_sensitivity_turnover=Decimal("0")
            ),
            covariance_sensitivity=(sensitivity,),
        )
        self.assertEqual(
            result.state,
            PortfolioRobustnessState.REVIEW_REQUIRED,
        )

    def test_missing_required_covariance_sensitivity_is_insufficient(self):
        fold_set = folds()
        result = PortfolioRobustnessEngine().assess(
            comparison=comparison(fold_set),
            folds=fold_set,
            constraints=constraints(),
            policy=robustness_policy(),
            covariance_sensitivity=(),
        )
        self.assertEqual(
            result.state,
            PortfolioRobustnessState.INSUFFICIENT_EVIDENCE,
        )

    def test_too_few_folds_is_insufficient_not_pass(self):
        fold_set = folds()[:2]
        result = PortfolioRobustnessEngine().assess(
            comparison=comparison(fold_set),
            folds=fold_set,
            constraints=constraints(),
            policy=robustness_policy(minimum_folds=3),
        )
        self.assertEqual(
            result.state,
            PortfolioRobustnessState.INSUFFICIENT_EVIDENCE,
        )

    def test_store_is_idempotent(self):
        fold_set = folds()
        result = PortfolioRobustnessEngine().assess(
            comparison=comparison(fold_set),
            folds=fold_set,
            constraints=constraints(),
            policy=robustness_policy(
                minimum_covariance_sensitivity_observations=0
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = PortfolioRobustnessStore(Path(tmp) / "robustness.duckdb")
            self.assertTrue(store.add(result))
            self.assertFalse(store.add(result))
            store.close()


if __name__ == "__main__":
    unittest.main()
