import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from quantos.validation import (
    ResearchExperimentSpecification,
    ValidationPlanStore,
    ValidationSample,
    WalkForwardPolicy,
    WalkForwardValidationEngine,
)

UTC = timezone.utc
BASE = datetime(2020, 1, 1, 16, tzinfo=UTC)
FACTOR = "factor-spec:" + "a" * 64


def sample(
    day,
    security="SEC:A",
    *,
    label_days=1,
    known_delay_days=0,
    factor_id=FACTOR,
):
    decision = BASE + timedelta(days=day)
    start = decision + timedelta(minutes=1)
    end = decision + timedelta(days=label_days)
    known = end + timedelta(days=known_delay_days)
    return ValidationSample(
        factor_id=factor_id,
        factor_run_id=f"factor-run:{day}:{security}",
        universe_id=f"investable-universe:{day}",
        security_id=security,
        decision_time=decision,
        label_start_time=start,
        label_end_time=end,
        outcome_known_at=known,
        realized_return=Decimal("0.01"),
        source_fact_ids=(f"price:{day}:{security}",),
        evidence_references=(f"artifact:return:{day}:{security}",),
    )


def panel(days=8):
    output = []
    for day in range(days):
        output.append(sample(day, "SEC:A"))
        output.append(sample(day, "SEC:B"))
    return tuple(output)


def experiment(**overrides):
    values = {
        "name": "quality-value-forward-return",
        "version": "1",
        "factor_id": FACTOR,
        "benchmark_id": "benchmark:market-cap",
        "hypothesis_reference": "hypothesis:qv",
        "variants_tested": 1,
        "primary_metric": "information_coefficient",
        "rationale": "Chronological validation of a frozen factor definition.",
        "evidence_references": ("research-plan:qv",),
    }
    values.update(overrides)
    return ResearchExperimentSpecification(**values)


def policy(**overrides):
    values = {
        "minimum_train_decision_times": 3,
        "test_decision_times_per_fold": 1,
        "step_decision_times": 1,
        "purge_seconds": 0,
        "embargo_seconds": 0,
        "minimum_train_samples": 4,
        "minimum_test_samples": 2,
        "expanding_window": True,
        "rolling_train_decision_times": None,
        "rationale": "Expanding walk-forward evaluation.",
        "evidence_references": ("validation-policy:qv",),
    }
    values.update(overrides)
    return WalkForwardPolicy(**values)


class WalkForwardValidationTests(unittest.TestCase):
    def test_walk_forward_test_windows_are_chronological_and_non_overlapping(self):
        result = WalkForwardValidationEngine().build(
            experiment=experiment(),
            policy=policy(),
            samples=panel(),
        )
        self.assertGreaterEqual(len(result.folds), 1)
        for left, right in zip(result.folds, result.folds[1:]):
            self.assertLess(left.test_end, right.test_start)

    def test_training_outcome_not_known_by_test_start_is_excluded(self):
        samples = list(panel())
        target = sample(
            1,
            "SEC:A",
            label_days=1,
            known_delay_days=5,
        )
        samples = [
            item
            for item in samples
            if not (
                item.decision_time == target.decision_time
                and item.security_id == target.security_id
            )
        ]
        samples.append(target)
        result = WalkForwardValidationEngine().build(
            experiment=experiment(),
            policy=policy(minimum_train_samples=3),
            samples=tuple(samples),
        )
        fold = result.folds[0]
        self.assertIn(target.sample_id, fold.not_yet_known_sample_ids)
        self.assertNotIn(target.sample_id, fold.train_sample_ids)

    def test_overlapping_label_is_purged(self):
        samples = list(panel())
        target = sample(2, "SEC:A", label_days=3)
        samples = [
            item
            for item in samples
            if not (
                item.decision_time == target.decision_time
                and item.security_id == target.security_id
            )
        ]
        samples.append(target)
        result = WalkForwardValidationEngine().build(
            experiment=experiment(),
            policy=policy(minimum_train_samples=3),
            samples=tuple(samples),
        )
        fold = result.folds[0]
        self.assertIn(target.sample_id, fold.purged_sample_ids)
        self.assertNotIn(target.sample_id, fold.train_sample_ids)

    def test_embargo_excludes_recent_training_decisions(self):
        result = WalkForwardValidationEngine().build(
            experiment=experiment(),
            policy=policy(
                embargo_seconds=86400 * 2,
                minimum_train_samples=2,
            ),
            samples=panel(),
        )
        first = result.folds[0]
        day_two_ids = {
            item.sample_id
            for item in panel()
            if item.decision_time == BASE + timedelta(days=2)
        }
        self.assertTrue(day_two_ids.issubset(set(first.embargoed_sample_ids)))

    def test_factor_mismatch_fails_closed(self):
        samples = list(panel())
        samples[0] = sample(
            0,
            "SEC:A",
            factor_id="factor-spec:" + "b" * 64,
        )
        with self.assertRaises(ValueError):
            WalkForwardValidationEngine().build(
                experiment=experiment(),
                policy=policy(),
                samples=tuple(samples),
            )

    def test_variants_tested_changes_experiment_identity(self):
        self.assertNotEqual(
            experiment(variants_tested=1).experiment_id,
            experiment(variants_tested=2).experiment_id,
        )

    def test_rolling_window_limits_training_dates(self):
        result = WalkForwardValidationEngine().build(
            experiment=experiment(),
            policy=policy(
                expanding_window=False,
                rolling_train_decision_times=3,
            ),
            samples=panel(days=9),
        )
        for fold in result.folds:
            self.assertLessEqual(
                (fold.train_end - fold.train_start).days,
                2,
            )

    def test_validation_plan_store_is_idempotent(self):
        result = WalkForwardValidationEngine().build(
            experiment=experiment(),
            policy=policy(),
            samples=panel(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = ValidationPlanStore(Path(tmp) / "validation.duckdb")
            self.assertTrue(store.add(result))
            self.assertFalse(store.add(result))
            manifest = store.get_manifest(result.plan_id)
            self.assertEqual(manifest["plan_id"], result.plan_id)
            store.close()


if __name__ == "__main__":
    unittest.main()
