import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import QuantLib as ql

from quantos.bond_pricing import (
    BondPricingResultStore,
    BondPricingValidationPolicy,
    QuantLibFixedRateBondAdapter,
)
from quantos.interest_rate_curves import (
    CurveConstructionPolicy,
    DiscountCurveBuilder,
)
from quantos.pricing_risk_contracts import (
    Currency,
    DayCountConvention,
    FixedRateBondInstrument,
    MarketDataSnapshotBuilder,
    MarketQuote,
    MarketQuoteType,
    PricingMeasure,
    PricingModelSpecification,
    PricingRequestBuilder,
    QuoteUnit,
)

UTC = timezone.utc
AT = datetime(2026, 9, 25, 12, tzinfo=UTC)


def zero_quote(tenor, rate, key="USD-OIS"):
    return MarketQuote(
        quote_type=MarketQuoteType.ZERO_RATE,
        market_key=key,
        value=Decimal(rate),
        unit=QuoteUnit.DECIMAL,
        event_time=AT - timedelta(minutes=2),
        knowledge_time=AT - timedelta(minutes=1),
        source_fact_ids=(f"zero:{key}:{tenor}",),
        tenor=tenor,
        currency=Currency.USD,
    )


def market():
    return MarketDataSnapshotBuilder().build(
        valuation_time=AT,
        base_currency=Currency.USD,
        quotes=(
            zero_quote("1Y", "0.030"),
            zero_quote("2Y", "0.034"),
            zero_quote("5Y", "0.040"),
        ),
    )


def curve(snapshot=None):
    snap = snapshot or market()
    return DiscountCurveBuilder().build(
        snapshot=snap,
        policy=CurveConstructionPolicy(
            curve_key="USD-OIS",
            currency=Currency.USD,
            minimum_pillars=2,
            maximum_absolute_zero_rate=Decimal("1"),
            maximum_absolute_forward_rate=Decimal("2"),
            repricing_tolerance=Decimal("1e-12"),
            allow_extrapolation=False,
            day_count="ACT_365_FIXED",
            interpolation="LOG_LINEAR_DISCOUNT",
            compounding="CONTINUOUS",
            calendar="NULL_CALENDAR",
            rationale="Stage 11.4 bond curve.",
            evidence_references=("curve-policy:bond",),
        ),
    )


def bond(**overrides):
    values = {
        "contract_id": "BOND:USD:TEST",
        "currency": Currency.USD,
        "face_value": Decimal("1000"),
        "issue_date": date(2025, 3, 15),
        "maturity_date": date(2030, 3, 15),
        "coupon_rate": Decimal("0.045"),
        "coupon_frequency_months": 6,
        "day_count": DayCountConvention.ACT_365_FIXED,
        "settlement_days": 2,
    }
    values.update(overrides)
    return FixedRateBondInstrument(**values)


def model(**parameter_overrides):
    params = {
        "calendar": "NULL_CALENDAR",
        "business_day_convention": "UNADJUSTED",
        "date_generation": "FORWARD",
        "end_of_month": "FALSE",
        "redemption": "100",
        "dv01_bump": "0.0001",
    }
    params.update(parameter_overrides)
    return PricingModelSpecification(
        model_family="DISCOUNT_CURVE_FIXED_RATE_BOND",
        model_version="1",
        parameters=tuple(sorted(params.items())),
        rationale="Frozen-curve fixed-rate bond pricing.",
        evidence_references=("pricing-model:bond",),
    )


def request(
    *,
    instrument=None,
    snapshot=None,
    pricing_model=None,
    measures=None,
):
    instrument = instrument or bond()
    snapshot = snapshot or market()
    pricing_model = pricing_model or model()
    return PricingRequestBuilder().build(
        instrument=instrument,
        market_snapshot=snapshot,
        model=pricing_model,
        measures=measures
        or (
            PricingMeasure.NPV,
            PricingMeasure.DIRTY_PRICE,
            PricingMeasure.CLEAN_PRICE,
            PricingMeasure.DV01,
        ),
        reporting_currency=Currency.USD,
    )


def validation(**overrides):
    values = {
        "maximum_absolute_npv_difference": Decimal("1e-8"),
        "maximum_absolute_price_difference": Decimal("1e-8"),
        "maximum_absolute_accrued_difference": Decimal("1e-8"),
        "maximum_absolute_dv01_difference": Decimal("1e-8"),
        "rationale": "Independent bond cash-flow differential gate.",
        "evidence_references": ("validation:bond",),
    }
    values.update(overrides)
    return BondPricingValidationPolicy(**values)


def price(**overrides):
    instrument = overrides.pop("instrument", bond())
    snapshot = overrides.pop("market_snapshot", market())
    pricing_model = overrides.pop("model", model())
    discount = overrides.pop("discount_curve", curve(snapshot))
    req = overrides.pop(
        "request",
        request(
            instrument=instrument,
            snapshot=snapshot,
            pricing_model=pricing_model,
        ),
    )
    return QuantLibFixedRateBondAdapter().price(
        request=req,
        instrument=instrument,
        market_snapshot=snapshot,
        model=pricing_model,
        discount_curve=discount,
        validation_policy=overrides.pop(
            "validation_policy",
            validation(),
        ),
        **overrides,
    )


class FixedRateBondPricingTests(unittest.TestCase):
    def test_quantlib_matches_independent_cashflow_reference(self):
        result = price()
        self.assertTrue(result.reference_verified)
        self.assertLessEqual(
            result.absolute_npv_difference,
            Decimal("1e-8"),
        )
        self.assertLessEqual(
            result.absolute_dirty_price_difference,
            Decimal("1e-8"),
        )
        self.assertLessEqual(
            result.absolute_clean_price_difference,
            Decimal("1e-8"),
        )
        self.assertLessEqual(
            result.absolute_accrued_difference,
            Decimal("1e-8"),
        )
        self.assertLessEqual(
            result.absolute_dv01_difference,
            Decimal("1e-8"),
        )
        self.assertEqual(result.order_authority, "NONE")
        self.assertEqual(result.capital_authority, "NONE")

    def test_clean_price_equals_dirty_price_less_accrued(self):
        result = price()
        measures = {
            item.measure: item.value
            for item in result.measures
        }
        self.assertAlmostEqual(
            float(measures[PricingMeasure.CLEAN_PRICE]),
            float(
                measures[PricingMeasure.DIRTY_PRICE]
                - result.quantlib_accrued_amount_per_100
            ),
            places=10,
        )

    def test_dv01_is_positive_for_plain_positive_duration_bond(self):
        result = price()
        measures = {
            item.measure: item.value
            for item in result.measures
        }
        self.assertGreater(
            measures[PricingMeasure.DV01],
            Decimal("0"),
        )

    def test_quantlib_global_evaluation_date_is_restored(self):
        settings = ql.Settings.instance()
        original = ql.Date(1, 1, 2001)
        previous = settings.evaluationDate
        try:
            settings.evaluationDate = original
            price()
            self.assertEqual(settings.evaluationDate, original)
        finally:
            settings.evaluationDate = previous

    def test_unsupported_bond_day_count_fails_closed(self):
        instrument = bond(day_count=DayCountConvention.ACT_360)
        snap = market()
        pricing_model = model()
        with self.assertRaises(ValueError):
            price(
                instrument=instrument,
                market_snapshot=snap,
                model=pricing_model,
                request=request(
                    instrument=instrument,
                    snapshot=snap,
                    pricing_model=pricing_model,
                ),
                discount_curve=curve(snap),
            )

    def test_stub_schedule_fails_closed(self):
        instrument = bond(maturity_date=date(2030, 4, 1))
        snap = market()
        pricing_model = model()
        with self.assertRaises(ValueError):
            price(
                instrument=instrument,
                market_snapshot=snap,
                model=pricing_model,
                request=request(
                    instrument=instrument,
                    snapshot=snap,
                    pricing_model=pricing_model,
                ),
                discount_curve=curve(snap),
            )

    def test_curve_from_another_snapshot_fails_closed(self):
        other = MarketDataSnapshotBuilder().build(
            valuation_time=AT,
            base_currency=Currency.USD,
            quotes=(
                zero_quote("1Y", "0.031"),
                zero_quote("2Y", "0.035"),
                zero_quote("5Y", "0.041"),
            ),
        )
        with self.assertRaises(ValueError):
            price(discount_curve=curve(other))

    def test_maturity_beyond_non_extrapolating_curve_fails_closed(self):
        instrument = bond(maturity_date=date(2032, 3, 15))
        snap = market()
        pricing_model = model()
        with self.assertRaises(ValueError):
            price(
                instrument=instrument,
                market_snapshot=snap,
                model=pricing_model,
                request=request(
                    instrument=instrument,
                    snapshot=snap,
                    pricing_model=pricing_model,
                ),
                discount_curve=curve(snap),
            )

    def test_unsupported_measure_fails_closed(self):
        snap = market()
        instrument = bond()
        pricing_model = model()
        req = request(
            instrument=instrument,
            snapshot=snap,
            pricing_model=pricing_model,
            measures=(PricingMeasure.YIELD,),
        )
        with self.assertRaises(ValueError):
            price(
                instrument=instrument,
                market_snapshot=snap,
                model=pricing_model,
                request=req,
                discount_curve=curve(snap),
            )

    def test_result_store_is_idempotent(self):
        result = price()
        with tempfile.TemporaryDirectory() as tmp:
            store = BondPricingResultStore(
                Path(tmp) / "bond-pricing.duckdb"
            )
            self.assertTrue(store.add(result))
            self.assertFalse(store.add(result))
            store.close()


if __name__ == "__main__":
    unittest.main()
