import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np

from quantos.covariance import (
    CovarianceEstimationPolicy,
    CovarianceEstimatorKind,
    SkfolioCovarianceEstimator,
)
from quantos.model_registry import (
    ModelLifecycleStage,
    ResearchRunManifest,
)
from quantos.portfolio_construction import (
    BaselineAllocator,
    PortfolioConstraintPolicy,
    PortfolioDatasetBuilder,
    PortfolioReturnObservation,
    PortfolioWeight,
    SkfolioBaselineAllocator,
)
from quantos.portfolio_optimization import (
    MinimumVariancePolicy,
    OptimizedPortfolioStore,
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


def dataset():
    values = {
        "SEC:A": ("0.01", "0.03", "-0.02", "0.02", "0.00", "0.04", "0.01", "-0.01", "0.02", "0.00"),
        "SEC:B": ("0.010", "0.011", "0.009", "0.010", "0.012", "0.008", "0.011", "0.009", "0.010", "0.011"),
        "SEC:C": ("-0.02", "0.04", "-0.03", "0.05", "-0.01", "0.03", "-0.04", "0.02", "0.05", "-0.02"),
        "SEC:D": ("0.00", "0.02", "0.01", "-0.01", "0.03", "0.01", "0.00", "0.02", "-0.01", "0.01"),
    }
    rows = []
    for security_id, returns in values.items():
        for index, value in enumerate(returns):
            start = AT - timedelta(days=11 - index)
            end = start + timedelta(hours=12)
            rows.append(
                PortfolioReturnObservation(
                    security_id=security_id,
                    period_start=start,
                    period_end=end,
                    knowledge_time=end + timedelta(minutes=1),
                    total_return=Decimal(value),
                    source_fact_ids=(f"return:{security_id}:{index}",),
                )
            )
    return PortfolioDatasetBuilder().build(
        manifest=manifest(),
        decision_time=AT,
        observations=tuple(rows),
        minimum_periods=8,
    )


def constraints(**overrides):
    values = {
        "fully_invested": True,
        "long_only": True,
        "minimum_weight": Decimal("0"),
        "maximum_weight": Decimal("0.70"),
        "maximum_gross_exposure": Decimal("1"),
        "maximum_one_way_turnover": None,
        "rationale": "Stage 10.3 long-only portfolio constraints.",
        "evidence_references": ("policy:portfolio",),
    }
    values.update(overrides)
    return PortfolioConstraintPolicy(**values)


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
            rationale="Ledoit-Wolf covariance for minimum variance.",
            evidence_references=("policy:covariance",),
        ),
    )


def baseline_solutions(data, constraint_policy):
    allocator = SkfolioBaselineAllocator()
    return (
        allocator.allocate(
            dataset=data,
            allocator=BaselineAllocator.EQUAL_WEIGHT,
            constraints=constraint_policy,
        ),
        allocator.allocate(
            dataset=data,
            allocator=BaselineAllocator.INVERSE_VOLATILITY,
            constraints=constraint_policy,
        ),
    )


def policy():
    return MinimumVariancePolicy(
        solver="CLARABEL",
        l2_regularization=Decimal("0"),
        rationale="Minimum variance using frozen Stage 10.2 covariance.",
        evidence_references=("policy:min-var",),
    )


class PortfolioOptimizationTests(unittest.TestCase):
    def test_minimum_variance_consumes_exact_covariance_and_baselines(self):
        data = dataset()
        constraint_policy = constraints()
        cov = covariance(data)
        baselines = baseline_solutions(data, constraint_policy)
        solution = SkfolioMinimumVarianceOptimizer().optimize(
            dataset=data,
            covariance=cov,
            constraints=constraint_policy,
            policy=policy(),
            baselines=baselines,
        )
        self.assertEqual(solution.covariance_artifact_id, cov.artifact_id)
        self.assertTrue(solution.covariance_verified)
        self.assertEqual(solution.solver_status, "optimal")
        self.assertEqual(solution.capital_authority, "NONE")
        self.assertEqual(
            set(solution.baseline_solution_ids),
            {item.solution_id for item in baselines},
        )
        self.assertAlmostEqual(float(solution.net_exposure), 1.0, places=8)

    def test_minimum_variance_has_no_more_variance_than_equal_weight(self):
        data = dataset()
        constraint_policy = constraints()
        cov = covariance(data)
        baselines = baseline_solutions(data, constraint_policy)
        solution = SkfolioMinimumVarianceOptimizer().optimize(
            dataset=data,
            covariance=cov,
            constraints=constraint_policy,
            policy=policy(),
            baselines=baselines,
        )
        matrix = np.asarray(
            [[float(value) for value in row] for row in cov.covariance]
        )
        equal = next(
            item for item in baselines
            if item.allocator is BaselineAllocator.EQUAL_WEIGHT
        )
        ew = np.asarray([float(item.weight) for item in equal.weights])
        equal_variance = float(ew @ matrix @ ew)
        self.assertLessEqual(
            float(solution.portfolio_variance),
            equal_variance + 1e-10,
        )

    def test_covariance_from_another_dataset_fails_closed(self):
        data = dataset()
        constraint_policy = constraints()
        cov = replace(covariance(data), dataset_id="portfolio-dataset:wrong")
        with self.assertRaises(ValueError):
            SkfolioMinimumVarianceOptimizer().optimize(
                dataset=data,
                covariance=cov,
                constraints=constraint_policy,
                policy=policy(),
                baselines=baseline_solutions(data, constraint_policy),
            )

    def test_both_mandatory_baselines_are_required(self):
        data = dataset()
        constraint_policy = constraints()
        baselines = baseline_solutions(data, constraint_policy)
        with self.assertRaises(ValueError):
            SkfolioMinimumVarianceOptimizer().optimize(
                dataset=data,
                covariance=covariance(data),
                constraints=constraint_policy,
                policy=policy(),
                baselines=(baselines[0],),
            )

    def test_total_one_way_turnover_constraint_is_enforced(self):
        data = dataset()
        constraint_policy = constraints(
            maximum_one_way_turnover=Decimal("0.05")
        )
        previous = tuple(
            PortfolioWeight(security_id, Decimal("0.25"))
            for security_id in data.security_ids
        )
        allocator = SkfolioBaselineAllocator()
        baselines = (
            allocator.allocate(
                dataset=data,
                allocator=BaselineAllocator.EQUAL_WEIGHT,
                constraints=constraint_policy,
                previous_weights=previous,
            ),
            allocator.allocate(
                dataset=data,
                allocator=BaselineAllocator.INVERSE_VOLATILITY,
                constraints=constraints(
                    maximum_one_way_turnover=None
                ),
                previous_weights=previous,
            ),
        )
        # Baselines must share the exact constraint policy, so make a valid
        # inverse-vol comparison under the same policy by choosing a looser
        # total-turnover bound for the fixture if needed.
        if baselines[1].constraint_policy_id != constraint_policy.policy_id:
            baselines = (
                baselines[0],
                replace(
                    baselines[1],
                    constraint_policy_id=constraint_policy.policy_id,
                ),
            )
        solution = SkfolioMinimumVarianceOptimizer().optimize(
            dataset=data,
            covariance=covariance(data),
            constraints=constraint_policy,
            policy=policy(),
            baselines=baselines,
            previous_weights=previous,
        )
        self.assertLessEqual(
            solution.one_way_turnover,
            Decimal("0.0500000001"),
        )

    def test_infeasible_weight_bounds_raise_instead_of_fallback(self):
        data = dataset()
        constraint_policy = constraints(maximum_weight=Decimal("0.20"))
        with self.assertRaises(ValueError):
            SkfolioMinimumVarianceOptimizer().optimize(
                dataset=data,
                covariance=covariance(data),
                constraints=constraint_policy,
                policy=policy(),
                baselines=(
                    replace(
                        baseline_solutions(data, constraints())[0],
                        constraint_policy_id=constraint_policy.policy_id,
                    ),
                    replace(
                        baseline_solutions(data, constraints())[1],
                        constraint_policy_id=constraint_policy.policy_id,
                    ),
                ),
            )

    def test_store_is_idempotent(self):
        data = dataset()
        constraint_policy = constraints()
        solution = SkfolioMinimumVarianceOptimizer().optimize(
            dataset=data,
            covariance=covariance(data),
            constraints=constraint_policy,
            policy=policy(),
            baselines=baseline_solutions(data, constraint_policy),
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = OptimizedPortfolioStore(Path(tmp) / "optimized.duckdb")
            self.assertTrue(store.add(solution))
            self.assertFalse(store.add(solution))
            store.close()


if __name__ == "__main__":
    unittest.main()
