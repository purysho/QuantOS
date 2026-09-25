import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from quantos.factor_contracts import (
    CrossSectionTransform,
    FactorComponent,
    FactorEngine,
    FactorSpecification,
    FeatureObservation,
)
from quantos.research_universe import (
    InvestableUniverse,
    UniverseDecision,
)

UTC = timezone.utc
DECISION = datetime(2026, 9, 25, 20, tzinfo=UTC)


def universe(universe_id="investable-universe:test"):
    return InvestableUniverse(
        universe_id=universe_id,
        as_of=DECISION - timedelta(minutes=5),
        policy_id="universe-policy:test",
        decisions=(
            UniverseDecision(
                "SEC:A", "ISSUER:A", "LISTING:A", "AAA", True, (), ("fact:a",)
            ),
            UniverseDecision(
                "SEC:B", "ISSUER:B", "LISTING:B", "BBB", True, (), ("fact:b",)
            ),
            UniverseDecision(
                "SEC:C", "ISSUER:C", "LISTING:C", "CCC", True, (), ("fact:c",)
            ),
        ),
    )


def spec(version="1", universe_id="investable-universe:test"):
    return FactorSpecification(
        name="quality-value",
        version=version,
        universe_id=universe_id,
        components=(
            FactorComponent(
                feature_name="quality",
                lookback_periods=4,
                minimum_lag_seconds=3600,
                maximum_staleness_seconds=86400 * 120,
                weight=Decimal("0.5"),
                transform=CrossSectionTransform.PERCENTILE_RANK,
            ),
            FactorComponent(
                feature_name="value",
                lookback_periods=1,
                minimum_lag_seconds=3600,
                maximum_staleness_seconds=86400 * 30,
                weight=Decimal("0.5"),
                transform=CrossSectionTransform.PERCENTILE_RANK,
            ),
        ),
        rationale="Reviewed cross-sectional quality/value specification.",
        evidence_references=("paper:qv",),
    )


def feature(
    security_id,
    name,
    value,
    *,
    lookback,
    end=DECISION - timedelta(hours=2),
    knowledge=None,
):
    return FeatureObservation(
        security_id=security_id,
        feature_name=name,
        feature_end_time=end,
        knowledge_time=knowledge or end + timedelta(minutes=1),
        lookback_periods=lookback,
        value=Decimal(value),
        source_fact_ids=(f"source:{security_id}:{name}",),
        evidence_references=(f"evidence:{security_id}:{name}",),
    )


def complete_features():
    return (
        feature("SEC:A", "quality", "10", lookback=4),
        feature("SEC:B", "quality", "20", lookback=4),
        feature("SEC:C", "quality", "30", lookback=4),
        feature("SEC:A", "value", "30", lookback=1),
        feature("SEC:B", "value", "20", lookback=1),
        feature("SEC:C", "value", "10", lookback=1),
    )


class FactorContractTests(unittest.TestCase):
    def test_factor_is_bound_to_exact_universe(self):
        with self.assertRaises(ValueError):
            FactorEngine().run(
                specification=spec(),
                universe=universe("investable-universe:other"),
                decision_time=DECISION,
                features=complete_features(),
            )

    def test_future_known_feature_is_missing_not_used(self):
        features = list(complete_features())
        features[0] = feature(
            "SEC:A",
            "quality",
            "999",
            lookback=4,
            end=DECISION - timedelta(hours=2),
            knowledge=DECISION + timedelta(seconds=1),
        )
        result = FactorEngine().run(
            specification=spec(),
            universe=universe(),
            decision_time=DECISION,
            features=tuple(features),
        )
        exclusion = next(
            item for item in result.exclusions if item.security_id == "SEC:A"
        )
        self.assertIn("MISSING_FEATURE:quality", exclusion.reasons)
        self.assertNotIn("SEC:A", {item.security_id for item in result.scores})

    def test_minimum_lag_is_enforced(self):
        features = list(complete_features())
        features[0] = feature(
            "SEC:A",
            "quality",
            "999",
            lookback=4,
            end=DECISION - timedelta(minutes=30),
            knowledge=DECISION - timedelta(minutes=29),
        )
        result = FactorEngine().run(
            specification=spec(),
            universe=universe(),
            decision_time=DECISION,
            features=tuple(features),
        )
        self.assertIn(
            "SEC:A",
            {item.security_id for item in result.exclusions},
        )

    def test_lookback_mismatch_is_not_silently_accepted(self):
        features = list(complete_features())
        features[0] = feature(
            "SEC:A",
            "quality",
            "10",
            lookback=3,
        )
        result = FactorEngine().run(
            specification=spec(),
            universe=universe(),
            decision_time=DECISION,
            features=tuple(features),
        )
        exclusion = next(
            item for item in result.exclusions if item.security_id == "SEC:A"
        )
        self.assertIn("MISSING_FEATURE:quality", exclusion.reasons)

    def test_percentile_ranks_and_weighted_score_are_deterministic(self):
        result = FactorEngine().run(
            specification=spec(),
            universe=universe(),
            decision_time=DECISION,
            features=complete_features(),
        )
        by_id = {item.security_id: item for item in result.scores}
        self.assertEqual(by_id["SEC:A"].score, Decimal("0.5"))
        self.assertEqual(by_id["SEC:B"].score, Decimal("0.5"))
        self.assertEqual(by_id["SEC:C"].score, Decimal("0.5"))
        self.assertEqual(len(result.exclusions), 0)

    def test_tied_values_receive_average_rank(self):
        features = list(complete_features())
        features[1] = feature("SEC:B", "quality", "10", lookback=4)
        result = FactorEngine().run(
            specification=spec(),
            universe=universe(),
            decision_time=DECISION,
            features=tuple(features),
        )
        a = next(item for item in result.scores if item.security_id == "SEC:A")
        b = next(item for item in result.scores if item.security_id == "SEC:B")
        a_quality = next(
            item for item in a.components if item.feature_name == "quality"
        )
        b_quality = next(
            item for item in b.components if item.feature_name == "quality"
        )
        self.assertEqual(
            a_quality.transformed_value,
            b_quality.transformed_value,
        )

    def test_spec_version_change_changes_factor_identity(self):
        self.assertNotEqual(spec("1").factor_id, spec("2").factor_id)

    def test_universe_cannot_be_from_after_decision_time(self):
        future_universe = InvestableUniverse(
            universe_id="investable-universe:test",
            as_of=DECISION + timedelta(seconds=1),
            policy_id="universe-policy:test",
            decisions=universe().decisions,
        )
        with self.assertRaises(ValueError):
            FactorEngine().run(
                specification=spec(),
                universe=future_universe,
                decision_time=DECISION,
                features=complete_features(),
            )


if __name__ == "__main__":
    unittest.main()
