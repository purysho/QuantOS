import itertools
import unittest
from decimal import Decimal

from quantos.overfitting_diagnostics import (
    MultipleTestingEngine,
    MultipleTestingPolicy,
    VariantReturnSeries,
)
from quantos.validation import ResearchExperimentSpecification


def experiment(variants):
    return ResearchExperimentSpecification(
        name="variant-search",
        version="1",
        factor_id="factor-spec:" + "a" * 64,
        benchmark_id="benchmark:market",
        hypothesis_reference="hypothesis:edge",
        variants_tested=variants,
        primary_metric="sharpe",
        rationale="Register the complete parameter search.",
        evidence_references=("research-plan:search",),
    )


def policy(slices=4, unique=True):
    return MultipleTestingPolicy(
        cscv_slices=slices,
        require_unique_is_winner=unique,
        rationale="Bailey-style CSCV and DSR diagnostics.",
        evidence_references=("paper:bailey-dsr", "paper:bailey-pbo"),
    )


def series(exp, variant_id, returns):
    return VariantReturnSeries(
        experiment_id=exp.experiment_id,
        variant_id=variant_id,
        backtest_id=f"economic-backtest:{variant_id}",
        net_period_returns=tuple(Decimal(str(item)) for item in returns),
        evidence_references=(f"backtest:{variant_id}",),
    )


def overfit_matrix():
    blocks = 4
    block_size = 2
    pairs = list(itertools.combinations(range(blocks), 2))
    exp = experiment(len(pairs))
    variants = []
    for index, strong_blocks in enumerate(pairs):
        returns = []
        for block in range(blocks):
            if block in strong_blocks:
                returns.extend(("0.02", "0.04"))
            else:
                returns.extend(("-0.02", "-0.04"))
        variants.append(series(exp, f"v{index}", returns))
    return exp, tuple(variants)


class MultipleTestingTests(unittest.TestCase):
    def test_registry_must_include_every_declared_variant(self):
        exp = experiment(3)
        variants = (
            series(exp, "a", ("0.01", "0.02", "0.00", "0.01")),
            series(exp, "b", ("0.00", "0.01", "-0.01", "0.02")),
        )
        with self.assertRaises(ValueError):
            MultipleTestingEngine().audit(
                experiment=exp,
                variants=variants,
                selected_variant_id="a",
                policy=policy(),
            )

    def test_dsr_probability_is_bounded_and_uses_trial_count(self):
        exp = experiment(3)
        variants = (
            series(exp, "a", ("0.01", "0.02", "0.03", "0.04", "0.02", "0.03", "0.04", "0.05")),
            series(exp, "b", ("0.00", "0.01", "-0.01", "0.02", "0.00", "0.01", "-0.01", "0.02")),
            series(exp, "c", ("-0.01", "0.00", "0.01", "0.00", "-0.01", "0.00", "0.01", "0.00")),
        )
        result = MultipleTestingEngine().audit(
            experiment=exp,
            variants=variants,
            selected_variant_id="a",
            policy=policy(),
        )
        dsr = result.deflated_sharpe
        self.assertEqual(dsr.trial_count, 3)
        self.assertGreaterEqual(dsr.probability, Decimal("0"))
        self.assertLessEqual(dsr.probability, Decimal("1"))
        self.assertGreaterEqual(
            dsr.expected_max_sharpe_under_null,
            Decimal("0"),
        )

    def test_cscv_detects_pair_specific_overfit_matrix(self):
        exp, variants = overfit_matrix()
        result = MultipleTestingEngine().audit(
            experiment=exp,
            variants=variants,
            selected_variant_id="v0",
            policy=policy(),
        )
        pbo = result.probability_backtest_overfitting
        self.assertEqual(pbo.combination_count, 6)
        self.assertEqual(pbo.pbo, Decimal("1"))

    def test_pbo_requires_synchronous_series(self):
        exp = experiment(2)
        variants = (
            series(exp, "a", ("0.01", "0.02", "0.03", "0.01")),
            series(exp, "b", ("0.01", "0.02", "0.03", "0.01", "0.00")),
        )
        with self.assertRaises(ValueError):
            MultipleTestingEngine().audit(
                experiment=exp,
                variants=variants,
                selected_variant_id="a",
                policy=policy(),
            )

    def test_period_count_must_divide_into_cscv_slices_exactly(self):
        exp = experiment(2)
        variants = (
            series(exp, "a", ("0.01", "0.02", "0.03", "0.04", "0.05", "0.06")),
            series(exp, "b", ("0.02", "0.01", "0.04", "0.03", "0.06", "0.05")),
        )
        with self.assertRaises(ValueError):
            MultipleTestingEngine().audit(
                experiment=exp,
                variants=variants,
                selected_variant_id="a",
                policy=policy(slices=4),
            )

    def test_selected_variant_must_be_registered(self):
        exp = experiment(2)
        variants = (
            series(exp, "a", ("0.01", "0.02", "0.03", "0.04")),
            series(exp, "b", ("0.02", "0.01", "0.04", "0.03")),
        )
        with self.assertRaises(ValueError):
            MultipleTestingEngine().audit(
                experiment=exp,
                variants=variants,
                selected_variant_id="missing",
                policy=policy(),
            )

    def test_is_winner_tie_fails_closed_by_default(self):
        exp = experiment(2)
        variants = (
            series(exp, "a", ("0.01", "0.02", "0.01", "0.02", "0.01", "0.02", "0.01", "0.02")),
            series(exp, "b", ("0.01", "0.02", "0.01", "0.02", "0.01", "0.02", "0.01", "0.02")),
        )
        with self.assertRaises(ValueError):
            MultipleTestingEngine().audit(
                experiment=exp,
                variants=variants,
                selected_variant_id="a",
                policy=policy(),
            )


if __name__ == "__main__":
    unittest.main()
