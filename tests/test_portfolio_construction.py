import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from quantos.model_registry import (
    ModelLifecycleStage,
    ResearchRunManifest,
)
from quantos.portfolio_construction import (
    BaselineAllocator,
    PortfolioConstraintPolicy,
    PortfolioDatasetBuilder,
    PortfolioReturnObservation,
    PortfolioSolutionStore,
    PortfolioWeight,
    SkfolioBaselineAllocator,
)

UTC = timezone.utc
AT = datetime(2026, 9, 25, 12, tzinfo=UTC)


def manifest(stage=ModelLifecycleStage.BACKTESTED):
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
        eligible_stage=stage,
    )


def observations():
    security_returns = {
        "SEC:A": ("0.01", "0.03", "-0.02", "0.02", "0.00", "0.04"),
        "SEC:B": ("0.01", "0.011", "0.009", "0.010", "0.012", "0.008"),
        "SEC:C": ("-0.02", "0.04", "-0.03", "0.05", "-0.01", "0.03"),
        "SEC:D": ("0.00", "0.02", "0.01", "-0.01", "0.03", "0.01"),
    }
    rows = []
    for security_id, values in security_returns.items():
        for index, value in enumerate(values):
            start = AT - timedelta(days=7 - index)
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
    return tuple(rows)


def dataset(obs=None):
    return PortfolioDatasetBuilder().build(
        manifest=manifest(),
        decision_time=AT,
        observations=obs or observations(),
        minimum_periods=4,
    )


def constraints(**overrides):
    values = {
        "fully_invested": True,
        "long_only": True,
        "minimum_weight": Decimal("0"),
        "maximum_weight": Decimal("1"),
        "maximum_gross_exposure": Decimal("1"),
        "maximum_one_way_turnover": None,
        "rationale": "Stage 10.1 baseline policy.",
        "evidence_references": ("policy:portfolio",),
    }
    values.update(overrides)
    return PortfolioConstraintPolicy(**values)


class PortfolioConstructionTests(unittest.TestCase):
    def test_dataset_binds_stage9_manifest_and_synchronous_history(self):
        item = dataset()
        self.assertEqual(item.manifest_id, manifest().manifest_id)
        self.assertEqual(item.model_id, manifest().model_id)
        self.assertEqual(item.assets, 4)
        self.assertEqual(item.observations, 6)
        self.assertEqual(item.security_ids, ("SEC:A", "SEC:B", "SEC:C", "SEC:D"))

    def test_dataset_rejects_future_knowledge(self):
        rows = list(observations())
        row = rows[0]
        rows[0] = PortfolioReturnObservation(
            security_id=row.security_id,
            period_start=row.period_start,
            period_end=row.period_end,
            knowledge_time=AT + timedelta(seconds=1),
            total_return=row.total_return,
            source_fact_ids=row.source_fact_ids,
        )
        with self.assertRaises(ValueError):
            dataset(tuple(rows))

    def test_dataset_rejects_asynchronous_histories(self):
        rows = tuple(
            item for item in observations()
            if not (
                item.security_id == "SEC:D"
                and item.period_end == max(
                    row.period_end
                    for row in observations()
                    if row.security_id == "SEC:D"
                )
            )
        )
        with self.assertRaises(ValueError):
            dataset(rows)

    def test_research_only_manifest_cannot_construct_portfolio_dataset(self):
        with self.assertRaises(ValueError):
            PortfolioDatasetBuilder().build(
                manifest=manifest(ModelLifecycleStage.RESEARCH),
                decision_time=AT,
                observations=observations(),
                minimum_periods=4,
            )

    def test_equal_weight_baseline_is_exact_and_has_no_capital_authority(self):
        solution = SkfolioBaselineAllocator().allocate(
            dataset=dataset(),
            allocator=BaselineAllocator.EQUAL_WEIGHT,
            constraints=constraints(),
        )
        self.assertEqual(
            tuple(item.weight for item in solution.weights),
            (
                Decimal("0.25"),
                Decimal("0.25"),
                Decimal("0.25"),
                Decimal("0.25"),
            ),
        )
        self.assertEqual(solution.net_exposure, Decimal("1.00"))
        self.assertEqual(solution.gross_exposure, Decimal("1.00"))
        self.assertEqual(solution.one_way_turnover, Decimal("1.00"))
        self.assertEqual(solution.capital_authority, "NONE")
        self.assertEqual(solution.engine_name, "skfolio")

    def test_inverse_volatility_favors_lower_volatility_security(self):
        solution = SkfolioBaselineAllocator().allocate(
            dataset=dataset(),
            allocator=BaselineAllocator.INVERSE_VOLATILITY,
            constraints=constraints(),
        )
        weights = {
            item.security_id: item.weight
            for item in solution.weights
        }
        self.assertGreater(weights["SEC:B"], weights["SEC:C"])

    def test_baseline_output_is_not_silently_clipped_to_max_weight(self):
        with self.assertRaises(ValueError):
            SkfolioBaselineAllocator().allocate(
                dataset=dataset(),
                allocator=BaselineAllocator.EQUAL_WEIGHT,
                constraints=constraints(maximum_weight=Decimal("0.20")),
            )

    def test_turnover_limit_requires_previous_weights(self):
        with self.assertRaises(ValueError):
            SkfolioBaselineAllocator().allocate(
                dataset=dataset(),
                allocator=BaselineAllocator.EQUAL_WEIGHT,
                constraints=constraints(
                    maximum_one_way_turnover=Decimal("0.10")
                ),
            )

    def test_turnover_policy_fails_closed_when_rebalance_is_too_large(self):
        previous = (
            PortfolioWeight("SEC:A", Decimal("1")),
            PortfolioWeight("SEC:B", Decimal("0")),
            PortfolioWeight("SEC:C", Decimal("0")),
            PortfolioWeight("SEC:D", Decimal("0")),
        )
        with self.assertRaises(ValueError):
            SkfolioBaselineAllocator().allocate(
                dataset=dataset(),
                allocator=BaselineAllocator.EQUAL_WEIGHT,
                constraints=constraints(
                    maximum_one_way_turnover=Decimal("0.50")
                ),
                previous_weights=previous,
            )

    def test_solution_store_is_idempotent(self):
        solution = SkfolioBaselineAllocator().allocate(
            dataset=dataset(),
            allocator=BaselineAllocator.EQUAL_WEIGHT,
            constraints=constraints(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = PortfolioSolutionStore(Path(tmp) / "portfolio.duckdb")
            self.assertTrue(store.add(solution))
            self.assertFalse(store.add(solution))
            store.close()


if __name__ == "__main__":
    unittest.main()
