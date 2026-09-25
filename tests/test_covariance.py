import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np

from quantos.covariance import (
    CovarianceArtifactStore,
    CovarianceEstimationPolicy,
    CovarianceEstimatorKind,
    SkfolioCovarianceEstimator,
)
from quantos.model_registry import (
    ModelLifecycleStage,
    ResearchRunManifest,
)
from quantos.portfolio_construction import (
    PortfolioDatasetBuilder,
    PortfolioReturnObservation,
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
        "SEC:A": ("0.01", "0.03", "-0.02", "0.02", "0.00", "0.04", "0.01", "-0.01"),
        "SEC:B": ("0.01", "0.011", "0.009", "0.010", "0.012", "0.008", "0.011", "0.009"),
        "SEC:C": ("-0.02", "0.04", "-0.03", "0.05", "-0.01", "0.03", "-0.04", "0.02"),
        "SEC:D": ("0.00", "0.02", "0.01", "-0.01", "0.03", "0.01", "0.00", "0.02"),
    }
    rows = []
    for security_id, returns in values.items():
        for index, value in enumerate(returns):
            start = AT - timedelta(days=9 - index)
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
        minimum_periods=6,
    )


def policy(kind, **overrides):
    values = {
        "estimator": kind,
        "minimum_observations": 6,
        "nearest": True,
        "higham": False,
        "higham_max_iteration": 100,
        "empirical_ddof": 1,
        "require_positive_semidefinite": True,
        "maximum_condition_number": None,
        "rationale": "Stage 10.2 covariance policy.",
        "evidence_references": ("policy:covariance",),
    }
    values.update(overrides)
    return CovarianceEstimationPolicy(**values)


class CovarianceTests(unittest.TestCase):
    def test_empirical_covariance_binds_exact_portfolio_dataset(self):
        data = dataset()
        artifact = SkfolioCovarianceEstimator().estimate(
            dataset=data,
            policy=policy(CovarianceEstimatorKind.EMPIRICAL),
        )
        self.assertEqual(artifact.dataset_id, data.dataset_id)
        self.assertEqual(artifact.manifest_id, data.manifest_id)
        self.assertEqual(artifact.security_ids, data.security_ids)
        self.assertEqual(len(artifact.covariance), data.assets)
        self.assertTrue(artifact.symmetric)
        self.assertTrue(artifact.positive_semidefinite)
        self.assertIsNone(artifact.shrinkage)

    def test_empirical_ddof_one_matches_sample_covariance_diagonal(self):
        data = dataset()
        artifact = SkfolioCovarianceEstimator().estimate(
            dataset=data,
            policy=policy(CovarianceEstimatorKind.EMPIRICAL),
        )
        matrix = np.asarray(
            [[float(value) for value in row] for row in data.returns],
            dtype=float,
        )
        expected = np.cov(matrix, rowvar=False, ddof=1)
        for index in range(data.assets):
            self.assertAlmostEqual(
                float(artifact.covariance[index][index]),
                float(expected[index][index]),
                places=12,
            )

    def test_ledoit_wolf_records_shrinkage_and_psd_diagnostics(self):
        artifact = SkfolioCovarianceEstimator().estimate(
            dataset=dataset(),
            policy=policy(CovarianceEstimatorKind.LEDOIT_WOLF),
        )
        self.assertIsNotNone(artifact.shrinkage)
        self.assertGreaterEqual(artifact.shrinkage, Decimal("0"))
        self.assertLessEqual(artifact.shrinkage, Decimal("1"))
        self.assertTrue(artifact.positive_semidefinite)
        self.assertGreaterEqual(artifact.minimum_eigenvalue, Decimal("-1e-12"))

    def test_condition_number_limit_fails_closed(self):
        with self.assertRaises(ValueError):
            SkfolioCovarianceEstimator().estimate(
                dataset=dataset(),
                policy=policy(
                    CovarianceEstimatorKind.EMPIRICAL,
                    maximum_condition_number=Decimal("1"),
                ),
            )

    def test_dataset_minimum_observation_policy_is_enforced(self):
        with self.assertRaises(ValueError):
            SkfolioCovarianceEstimator().estimate(
                dataset=dataset(),
                policy=policy(
                    CovarianceEstimatorKind.LEDOIT_WOLF,
                    minimum_observations=20,
                    empirical_ddof=1,
                ),
            )

    def test_store_is_idempotent(self):
        artifact = SkfolioCovarianceEstimator().estimate(
            dataset=dataset(),
            policy=policy(CovarianceEstimatorKind.LEDOIT_WOLF),
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = CovarianceArtifactStore(Path(tmp) / "covariance.duckdb")
            self.assertTrue(store.add(artifact))
            self.assertFalse(store.add(artifact))
            store.close()


if __name__ == "__main__":
    unittest.main()
