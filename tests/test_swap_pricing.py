import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import QuantLib as ql

from quantos.interest_rate_curves import (
    CurveConstructionPolicy,
    DiscountCurveBuilder,
)
from quantos.pricing_risk_contracts import (
    Currency,
    DayCountConvention,
    FixedFloatSwapInstrument,
    MarketDataSnapshotBuilder,
    MarketQuote,
    MarketQuoteType,
    PayReceive,
    PricingMeasure,
    PricingModelSpecification,
    PricingRequestBuilder,
    QuoteUnit,
)
from quantos.swap_pricing import (
    QuantLibFixedFloatSwapAdapter,
    SwapPricingResultStore,
    SwapPricingValidationPolicy,
)

UTC = timezone.utc
AT = datetime(2026, 9, 25, 12, tzinfo=UTC)


def zero_quote(key, tenor, rate):
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
            zero_quote("USD-DISC", "1Y", "0.030"),
            zero_quote("USD-DISC", "2Y", "0.034"),
            zero_quote("USD-DISC", "5Y", "0.040"),
            zero_quote("USD-FWD-3M", "1Y", "0.032"),
            zero_quote("USD-FWD-3M", "2Y", "0.036"),
            zero_quote("USD-FWD-3M", "5Y", "0.041"),
        ),
    )


def build_curve(key, snapshot=None):
    snap = snapshot or market()
    return DiscountCurveBuilder().build(
        snapshot=snap,
        policy=CurveConstructionPolicy(
            curve_key=key,
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
            rationale=f"Stage 11.5 {key} curve.",
            evidence_references=(f"curve-policy:{key}",),
        ),
    )


def swap(**overrides):
    values = {
        "contract_id": "SWAP:USD:TEST",
        "currency": Currency.USD,
        "notional": Decimal("1000000"),
        "effective_date": date(2026, 10, 1),
        "maturity_date": date(2030, 10, 1),
        "fixed_rate": Decimal("0.038"),
        "fixed_leg_frequency_months": 6,
        "fixed_leg_day_count": DayCountConvention.ACT_365_FIXED,
        "floating_leg_frequency_months": 3,
        "floating_leg_day_count": DayCountConvention.ACT_365_FIXED,
        "floating_index_id": "FC-USD-3M",
        "floating_spread": Decimal("0.001"),
        "fixed_leg_direction": PayReceive.PAY,
    }
    values.update(overrides)
    return FixedFloatSwapInstrument(**values)


def model(**overrides):
    params = {
        "calendar": "NULL_CALENDAR",
        "business_day_convention": "UNADJUSTED",
        "date_generation": "FORWARD",
        "end_of_month": "FALSE",
        "fixing_days": "0",
        "floating_index_mode": "PROJECTED_SIMPLE_FORWARD",
        "discount_curve_key": "USD-DISC",
        "forwarding_curve_key": "USD-FWD-3M",
        "dv01_bump": "0.0001",
    }
    params.update(overrides)
    return PricingModelSpecification(
        model_family="DUAL_CURVE_FIXED_FLOAT_SWAP",
        model_version="1",
        parameters=tuple(sorted(params.items())),
        rationale="Dual-curve future-starting vanilla swap fixture.",
        evidence_references=("pricing-model:swap",),
    )


def request(
    *,
    instrument=None,
    snapshot=None,
    pricing_model=None,
    measures=None,
):
    instrument = instrument or swap()
    snapshot = snapshot or market()
    pricing_model = pricing_model or model()
    return PricingRequestBuilder().build(
        instrument=instrument,
        market_snapshot=snapshot,
        model=pricing_model,
        measures=measures
        or (
            PricingMeasure.NPV,
            PricingMeasure.DV01,
        ),
        reporting_currency=Currency.USD,
    )


def validation(**overrides):
    values = {
        "maximum_absolute_npv_difference": Decimal("1e-7"),
        "maximum_absolute_fixed_leg_dv01_difference": Decimal("1e-7"),
        "rationale": "Independent swap cash-flow differential gate.",
        "evidence_references": ("validation:swap",),
    }
    values.update(overrides)
    return SwapPricingValidationPolicy(**values)


def price(**overrides):
    instrument = overrides.pop("instrument", swap())
    snapshot = overrides.pop("market_snapshot", market())
    pricing_model = overrides.pop("model", model())
    discount = overrides.pop(
        "discount_curve",
        build_curve("USD-DISC", snapshot),
    )
    forwarding = overrides.pop(
        "forwarding_curve",
        build_curve("USD-FWD-3M", snapshot),
    )
    req = overrides.pop(
        "request",
        request(
            instrument=instrument,
            snapshot=snapshot,
            pricing_model=pricing_model,
        ),
    )
    return QuantLibFixedFloatSwapAdapter().price(
        request=req,
        instrument=instrument,
        market_snapshot=snapshot,
        model=pricing_model,
        discount_curve=discount,
        forwarding_curve=forwarding,
        validation_policy=overrides.pop(
            "validation_policy",
            validation(),
        ),
        **overrides,
    )


class FixedFloatSwapPricingTests(unittest.TestCase):
    def test_quantlib_matches_independent_dual_curve_reference(self):
        result = price()
        self.assertTrue(result.reference_verified)
        self.assertLessEqual(
            result.absolute_npv_difference,
            Decimal("1e-7"),
        )
        self.assertLessEqual(
            result.absolute_fixed_leg_dv01_difference,
            Decimal("1e-7"),
        )
        self.assertNotEqual(
            result.discount_curve_id,
            result.forwarding_curve_id,
        )
        self.assertEqual(result.order_authority, "NONE")
        self.assertEqual(result.capital_authority, "NONE")

    def test_pay_and_receive_fixed_reverse_npv_sign(self):
        payer = price()
        receiver_instrument = swap(
            fixed_leg_direction=PayReceive.RECEIVE
        )
        snap = market()
        pricing_model = model()
        receiver = price(
            instrument=receiver_instrument,
            market_snapshot=snap,
            model=pricing_model,
            request=request(
                instrument=receiver_instrument,
                snapshot=snap,
                pricing_model=pricing_model,
            ),
            discount_curve=build_curve("USD-DISC", snap),
            forwarding_curve=build_curve(
                "USD-FWD-3M",
                snap,
            ),
        )
        self.assertAlmostEqual(
            float(payer.measures[1].value),
            -float(receiver.measures[1].value),
            places=7,
        )

    def test_same_curve_can_be_bound_explicitly_to_both_roles(self):
        snap = market()
        single_model = model(
            discount_curve_key="USD-DISC",
            forwarding_curve_key="USD-DISC",
        )
        discount = build_curve("USD-DISC", snap)
        result = price(
            market_snapshot=snap,
            model=single_model,
            request=request(
                snapshot=snap,
                pricing_model=single_model,
            ),
            discount_curve=discount,
            forwarding_curve=discount,
        )
        self.assertEqual(
            result.discount_curve_id,
            result.forwarding_curve_id,
        )

    def test_swap_start_on_or_before_valuation_requires_fixing_support(self):
        instrument = swap(effective_date=AT.date())
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
                discount_curve=build_curve("USD-DISC", snap),
                forwarding_curve=build_curve(
                    "USD-FWD-3M",
                    snap,
                ),
            )

    def test_stub_schedule_fails_closed(self):
        instrument = swap(maturity_date=date(2030, 10, 17))
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
                discount_curve=build_curve("USD-DISC", snap),
                forwarding_curve=build_curve(
                    "USD-FWD-3M",
                    snap,
                ),
            )

    def test_curve_from_another_snapshot_fails_closed(self):
        other = MarketDataSnapshotBuilder().build(
            valuation_time=AT,
            base_currency=Currency.USD,
            quotes=(
                zero_quote("USD-DISC", "1Y", "0.031"),
                zero_quote("USD-DISC", "2Y", "0.035"),
                zero_quote("USD-DISC", "5Y", "0.041"),
                zero_quote("USD-FWD-3M", "1Y", "0.033"),
                zero_quote("USD-FWD-3M", "2Y", "0.037"),
                zero_quote("USD-FWD-3M", "5Y", "0.042"),
            ),
        )
        with self.assertRaises(ValueError):
            price(
                forwarding_curve=build_curve(
                    "USD-FWD-3M",
                    other,
                )
            )

    def test_unsupported_measure_fails_closed(self):
        snap = market()
        instrument = swap()
        pricing_model = model()
        req = request(
            instrument=instrument,
            snapshot=snap,
            pricing_model=pricing_model,
            measures=(PricingMeasure.GAMMA,),
        )
        with self.assertRaises(ValueError):
            price(
                instrument=instrument,
                market_snapshot=snap,
                model=pricing_model,
                request=req,
                discount_curve=build_curve("USD-DISC", snap),
                forwarding_curve=build_curve(
                    "USD-FWD-3M",
                    snap,
                ),
            )

    def test_quantlib_global_evaluation_date_is_restored(self):
        settings = ql.Settings.instance()
        original = ql.Date(1, 1, 2002)
        previous = settings.evaluationDate
        try:
            settings.evaluationDate = original
            price()
            self.assertEqual(settings.evaluationDate, original)
        finally:
            settings.evaluationDate = previous

    def test_result_store_is_idempotent(self):
        result = price()
        with tempfile.TemporaryDirectory() as tmp:
            store = SwapPricingResultStore(
                Path(tmp) / "swap-pricing.duckdb"
            )
            self.assertTrue(store.add(result))
            self.assertFalse(store.add(result))
            store.close()


if __name__ == "__main__":
    unittest.main()
