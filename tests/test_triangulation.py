import unittest
from datetime import datetime, timezone
from decimal import Decimal

from quantos.triangulation import (
    TriangulationPolicy,
    TriangulationStatus,
    ValuationFamily,
    ValuationTriangulationEngine,
    build_observation,
)

UTC = timezone.utc
AS_OF = datetime(2026, 9, 25, tzinfo=UTC)
PERMIT = "valuation-method-permit:" + "a" * 64


def obs(family, label, low, central, high):
    return build_observation(
        family=family,
        label=label,
        reference_id=f"valuation:{label}",
        method_permit_id=PERMIT,
        as_of=AS_OF,
        low=Decimal(low),
        central=Decimal(central),
        high=Decimal(high),
        evidence_references=(f"evidence:{label}",),
    )


def policy(**overrides):
    values = {
        "minimum_method_families": 2,
        "wide_central_dispersion_ratio": Decimal("0.30"),
        "rationale": "Surface cross-method disagreement without averaging it away.",
        "evidence_references": ("review:triangulation-policy",),
    }
    values.update(overrides)
    return TriangulationPolicy(**values)


class TriangulationTests(unittest.TestCase):
    def test_multiple_comps_metrics_count_as_one_method_family(self):
        result = ValuationTriangulationEngine().build(
            observations=(
                obs(
                    ValuationFamily.TRADING_COMPS,
                    "EV_EBITDA",
                    "8",
                    "10",
                    "12",
                ),
                obs(
                    ValuationFamily.TRADING_COMPS,
                    "EV_REVENUE",
                    "9",
                    "11",
                    "13",
                ),
            ),
            policy=policy(),
        )
        self.assertEqual(
            result.status,
            TriangulationStatus.INSUFFICIENT_METHOD_FAMILIES,
        )
        self.assertEqual(len(result.family_summaries), 1)

    def test_common_overlap_is_structural_not_weighted_fair_value(self):
        result = ValuationTriangulationEngine().build(
            observations=(
                obs(ValuationFamily.DCF, "DCF", "9", "11", "14"),
                obs(
                    ValuationFamily.TRADING_COMPS,
                    "Comps",
                    "10",
                    "12",
                    "15",
                ),
                obs(ValuationFamily.SOTP, "SOTP", "12", "12", "12"),
            ),
            policy=policy(),
        )
        self.assertEqual(result.status, TriangulationStatus.COMMON_OVERLAP)
        self.assertEqual(result.common_overlap_low, Decimal("12"))
        self.assertEqual(result.common_overlap_high, Decimal("12"))
        self.assertNotIn("fair value", result.__dict__)
        self.assertIn("does not calculate", result.caveat)

    def test_disjoint_methods_remain_disjoint(self):
        result = ValuationTriangulationEngine().build(
            observations=(
                obs(ValuationFamily.DCF, "DCF", "5", "6", "7"),
                obs(
                    ValuationFamily.TRADING_COMPS,
                    "Comps",
                    "10",
                    "11",
                    "12",
                ),
            ),
            policy=policy(),
        )
        self.assertEqual(result.status, TriangulationStatus.DISJOINT)
        self.assertIn(
            "NO_CROSS_METHOD_OVERLAP",
            {flag.code for flag in result.flags},
        )

    def test_partial_overlap_preserves_no_common_range(self):
        result = ValuationTriangulationEngine().build(
            observations=(
                obs(ValuationFamily.DCF, "DCF", "8", "10", "12"),
                obs(
                    ValuationFamily.TRADING_COMPS,
                    "Comps",
                    "11",
                    "13",
                    "15",
                ),
                obs(ValuationFamily.SOTP, "SOTP", "14", "14", "14"),
            ),
            policy=policy(),
        )
        self.assertEqual(
            result.status,
            TriangulationStatus.PARTIAL_OVERLAP,
        )
        self.assertIsNone(result.common_overlap_low)
        self.assertIsNone(result.common_overlap_high)

    def test_wide_dispersion_threshold_is_policy_bound(self):
        result = ValuationTriangulationEngine().build(
            observations=(
                obs(ValuationFamily.DCF, "DCF", "8", "10", "12"),
                obs(
                    ValuationFamily.TRADING_COMPS,
                    "Comps",
                    "18",
                    "20",
                    "22",
                ),
            ),
            policy=policy(wide_central_dispersion_ratio=Decimal("0.20")),
        )
        self.assertIn(
            "WIDE_CROSS_METHOD_DISPERSION",
            {flag.code for flag in result.flags},
        )

    def test_mismatched_as_of_fails_closed(self):
        first = obs(ValuationFamily.DCF, "DCF", "8", "10", "12")
        second = build_observation(
            family=ValuationFamily.SOTP,
            label="SOTP",
            reference_id="sotp:test",
            method_permit_id=PERMIT,
            as_of=datetime(2026, 9, 24, tzinfo=UTC),
            low=Decimal("10"),
            central=Decimal("10"),
            high=Decimal("10"),
            evidence_references=("evidence:sotp",),
        )
        with self.assertRaises(ValueError):
            ValuationTriangulationEngine().build(
                observations=(first, second),
                policy=policy(),
            )


if __name__ == "__main__":
    unittest.main()
