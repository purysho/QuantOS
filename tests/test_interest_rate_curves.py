import math
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from quantos.interest_rate_curves import (
    CurveConstructionPolicy,
    DiscountCurveBuilder,
    DiscountCurveStore,
    discount_curve_identity,
)
from quantos.pricing_risk_contracts import (
    Currency,
    MarketDataSnapshotBuilder,
    MarketQuote,
    MarketQuoteType,
    QuoteUnit,
)

UTC = timezone.utc
AT = datetime(2026, 9, 25, 12, tzinfo=UTC)


def zero_quote(tenor, rate):
    return MarketQuote(
        quote_type=MarketQuoteType.ZERO_RATE,
        market_key="USD-OIS",
        value=Decimal(rate),
        unit=QuoteUnit.DECIMAL,
        event_time=AT,
        knowledge_time=AT,
        source_fact_ids=(f"ois:{tenor}",),
        tenor=tenor,
        currency=Currency.USD,
    )


def snapshot(quotes=None):
    return MarketDataSnapshotBuilder().build(
        valuation_time=AT,
        base_currency=Currency.USD,
        quotes=quotes
        or (
            zero_quote("5Y", "0.040"),
            zero_quote("1Y", "0.030"),
            zero_quote("2Y", "0.035"),
        ),
    )


def policy(**overrides):
    values = {
        "curve_key": "USD-OIS",
        "currency": Currency.USD,
        "minimum_pillars": 2,
        "maximum_absolute_zero_rate": Decimal("1"),
        "maximum_absolute_forward_rate": Decimal("2"),
        "repricing_tolerance": Decimal("1e-12"),
        "allow_extrapolation": False,
        "day_count": "ACT_365_FIXED",
        "interpolation": "LOG_LINEAR_DISCOUNT",
        "compounding": "CONTINUOUS",
        "calendar": "NULL_CALENDAR",
        "rationale": "Stage 11.3 OIS curve fixture.",
        "evidence_references": ("curve-policy:evidence",),
    }
    values.update(overrides)
    return CurveConstructionPolicy(**values)


class InterestRateCurveTests(unittest.TestCase):
    def test_curve_is_order_independent_and_quantlib_verified(self):
        left = DiscountCurveBuilder().build(
            snapshot=snapshot(),
            policy=policy(),
        )
        right = DiscountCurveBuilder().build(
            snapshot=snapshot(tuple(reversed(snapshot().quotes))),
            policy=policy(),
        )
        self.assertEqual(left.curve_id, right.curve_id)
        self.assertTrue(left.quantlib_verified)
        self.assertEqual(
            left.curve_id,
            discount_curve_identity(left),
        )
        self.assertEqual(
            tuple(item.tenor for item in left.pillars),
            ("1Y", "2Y", "5Y"),
        )
        self.assertEqual(left.order_authority, "NONE")
        self.assertEqual(left.capital_authority, "NONE")

    def test_negative_rates_allow_discount_factor_above_one(self):
        curve = DiscountCurveBuilder().build(
            snapshot=snapshot(
                (
                    zero_quote("1Y", "-0.01"),
                    zero_quote("2Y", "-0.005"),
                )
            ),
            policy=policy(),
        )
        self.assertGreater(
            curve.pillars[0].discount_factor,
            Decimal("1"),
        )
        self.assertTrue(curve.quantlib_verified)

    def test_log_linear_interpolation_matches_reference(self):
        curve = DiscountCurveBuilder().build(
            snapshot=snapshot(
                (
                    zero_quote("1Y", "0.02"),
                    zero_quote("2Y", "0.04"),
                )
            ),
            policy=policy(),
        )
        one = curve.pillars[0]
        two = curve.pillars[1]
        midpoint_days = (
            curve.valuation_date
            + (two.pillar_date - curve.valuation_date) // 2
        )
        target_t = Decimal(
            (midpoint_days - curve.valuation_date).days
        ) / Decimal("365")
        weight = (
            target_t - Decimal("0")
        ) / two.year_fraction
        expected = math.exp(
            float(weight) * math.log(float(two.discount_factor))
        )
        observed = float(curve.discount_factor(midpoint_days))
        self.assertAlmostEqual(observed, expected, places=12)

    def test_extrapolation_fails_closed_by_default(self):
        curve = DiscountCurveBuilder().build(
            snapshot=snapshot(),
            policy=policy(),
        )
        with self.assertRaises(ValueError):
            curve.discount_factor(
                curve.pillars[-1].pillar_date.replace(
                    year=curve.pillars[-1].pillar_date.year + 1
                )
            )

    def test_extreme_zero_rate_fails_policy_sanity_bound(self):
        with self.assertRaises(ValueError):
            DiscountCurveBuilder().build(
                snapshot=snapshot(
                    (
                        zero_quote("1Y", "0.03"),
                        zero_quote("2Y", "1.50"),
                    )
                ),
                policy=policy(
                    maximum_absolute_zero_rate=Decimal("1")
                ),
            )

    def test_extreme_implied_forward_rate_fails_closed(self):
        with self.assertRaises(ValueError):
            DiscountCurveBuilder().build(
                snapshot=snapshot(
                    (
                        zero_quote("1Y", "-0.50"),
                        zero_quote("2Y", "0.50"),
                    )
                ),
                policy=policy(
                    maximum_absolute_zero_rate=Decimal("1"),
                    maximum_absolute_forward_rate=Decimal("0.60"),
                ),
            )

    def test_tampered_nested_pillar_breaks_curve_identity(self):
        curve = DiscountCurveBuilder().build(
            snapshot=snapshot(),
            policy=policy(),
        )
        tampered_pillar = replace(
            curve.pillars[0],
            discount_factor=Decimal("0.1"),
        )
        tampered = replace(
            curve,
            pillars=(tampered_pillar, *curve.pillars[1:]),
        )
        self.assertNotEqual(
            curve.curve_id,
            discount_curve_identity(tampered),
        )

    def test_store_is_idempotent(self):
        curve = DiscountCurveBuilder().build(
            snapshot=snapshot(),
            policy=policy(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = DiscountCurveStore(Path(tmp) / "curves.duckdb")
            self.assertTrue(store.add(curve))
            self.assertFalse(store.add(curve))
            store.close()


if __name__ == "__main__":
    unittest.main()