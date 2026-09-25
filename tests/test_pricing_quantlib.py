import math
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import QuantLib as ql

from quantos.pricing_quantlib import (
    PricingResultStore,
    QuantLibPricingAdapter,
)
from quantos.pricing_risk_contracts import (
    Currency,
    EquityInstrument,
    EuropeanOptionInstrument,
    MarketDataSnapshotBuilder,
    MarketQuote,
    MarketQuoteType,
    OptionType,
    PricingMeasure,
    PricingModelSpecification,
    PricingRequestBuilder,
    QuoteUnit,
)

UTC = timezone.utc
AT = datetime(2026, 9, 25, 12, tzinfo=UTC)


def quote(
    quote_type,
    key,
    value,
    unit=QuoteUnit.DECIMAL,
):
    return MarketQuote(
        quote_type=quote_type,
        market_key=key,
        value=Decimal(value),
        unit=unit,
        event_time=AT - timedelta(minutes=2),
        knowledge_time=AT - timedelta(minutes=1),
        source_fact_ids=(f"source:{quote_type.value}:{key}",),
        currency=Currency.USD,
    )


def option_market():
    return MarketDataSnapshotBuilder().build(
        valuation_time=AT,
        base_currency=Currency.USD,
        quotes=(
            quote(
                MarketQuoteType.EQUITY_SPOT,
                "SEC:A",
                "100",
                QuoteUnit.PRICE,
            ),
            quote(
                MarketQuoteType.ZERO_RATE,
                "USD-RISK-FREE",
                "0.05",
            ),
            quote(
                MarketQuoteType.ZERO_RATE,
                "SEC:A-DIVIDEND",
                "0.02",
            ),
            quote(
                MarketQuoteType.VOLATILITY,
                "SEC:A-BLACK-VOL",
                "0.20",
            ),
        ),
    )


def option():
    return EuropeanOptionInstrument(
        contract_id="OPT:SEC:A:100C",
        underlying_security_id="SEC:A",
        currency=Currency.USD,
        option_type=OptionType.CALL,
        strike=Decimal("100"),
        expiry=AT + timedelta(days=365),
        multiplier=Decimal("1"),
    )


def option_model():
    return PricingModelSpecification(
        model_family="BLACK_SCHOLES_MERTON",
        model_version="1",
        parameters=(
            ("calendar", "NULL_CALENDAR"),
            ("day_count", "ACT_365_FIXED"),
            ("dividend_yield_key", "SEC:A-DIVIDEND"),
            ("risk_free_rate_key", "USD-RISK-FREE"),
            ("volatility_key", "SEC:A-BLACK-VOL"),
        ),
        rationale="Analytic European BSM contract.",
        evidence_references=("pricing-model:bsm",),
    )


def option_request(measures=None):
    return PricingRequestBuilder().build(
        instrument=option(),
        market_snapshot=option_market(),
        model=option_model(),
        measures=measures
        or (
            PricingMeasure.NPV,
            PricingMeasure.DELTA,
            PricingMeasure.GAMMA,
            PricingMeasure.VEGA,
        ),
        reporting_currency=Currency.USD,
    )


class QuantLibPricingAdapterTests(unittest.TestCase):
    def test_equity_spot_mark_to_market(self):
        market = MarketDataSnapshotBuilder().build(
            valuation_time=AT,
            base_currency=Currency.USD,
            quotes=(
                quote(
                    MarketQuoteType.EQUITY_SPOT,
                    "SEC:A",
                    "123.45",
                    QuoteUnit.PRICE,
                ),
            ),
        )
        instrument = EquityInstrument(
            security_id="SEC:A",
            currency=Currency.USD,
            multiplier=Decimal("2"),
        )
        model = PricingModelSpecification(
            model_family="SPOT_MARK_TO_MARKET",
            model_version="1",
            parameters=(),
            rationale="Direct spot mark.",
            evidence_references=("pricing-model:spot",),
        )
        request = PricingRequestBuilder().build(
            instrument=instrument,
            market_snapshot=market,
            model=model,
            measures=(PricingMeasure.NPV,),
            reporting_currency=Currency.USD,
        )
        result = QuantLibPricingAdapter().price(
            request=request,
            instrument=instrument,
            market_snapshot=market,
            model=model,
        )
        self.assertEqual(
            result.measures[0].value,
            Decimal("246.90"),
        )
        self.assertEqual(result.order_authority, "NONE")
        self.assertEqual(result.capital_authority, "NONE")

    def test_european_option_matches_independent_black_scholes_reference(self):
        result = QuantLibPricingAdapter().price(
            request=option_request(),
            instrument=option(),
            market_snapshot=option_market(),
            model=option_model(),
        )
        values = {
            item.measure: float(item.value)
            for item in result.measures
        }
        expected = black_scholes_call(
            spot=100.0,
            strike=100.0,
            risk_free=0.05,
            dividend=0.02,
            volatility=0.20,
            time_years=1.0,
        )
        self.assertAlmostEqual(
            values[PricingMeasure.NPV],
            expected["npv"],
            places=9,
        )
        self.assertAlmostEqual(
            values[PricingMeasure.DELTA],
            expected["delta"],
            places=9,
        )
        self.assertAlmostEqual(
            values[PricingMeasure.GAMMA],
            expected["gamma"],
            places=9,
        )
        self.assertAlmostEqual(
            values[PricingMeasure.VEGA],
            expected["vega"],
            places=8,
        )
        self.assertEqual(len(result.quote_ids_used), 4)

    def test_unsupported_measure_fails_closed(self):
        request = option_request(
            measures=(PricingMeasure.DV01,)
        )
        with self.assertRaises(ValueError):
            QuantLibPricingAdapter().price(
                request=request,
                instrument=option(),
                market_snapshot=option_market(),
                model=option_model(),
            )

    def test_missing_required_market_quote_fails_closed(self):
        market = MarketDataSnapshotBuilder().build(
            valuation_time=AT,
            base_currency=Currency.USD,
            quotes=option_market().quotes[:-1],
        )
        request = PricingRequestBuilder().build(
            instrument=option(),
            market_snapshot=market,
            model=option_model(),
            measures=(PricingMeasure.NPV,),
            reporting_currency=Currency.USD,
        )
        with self.assertRaises(ValueError):
            QuantLibPricingAdapter().price(
                request=request,
                instrument=option(),
                market_snapshot=market,
                model=option_model(),
            )

    def test_no_implicit_fx_conversion(self):
        request = PricingRequestBuilder().build(
            instrument=option(),
            market_snapshot=option_market(),
            model=option_model(),
            measures=(PricingMeasure.NPV,),
            reporting_currency=Currency.GBP,
        )
        with self.assertRaises(ValueError):
            QuantLibPricingAdapter().price(
                request=request,
                instrument=option(),
                market_snapshot=option_market(),
                model=option_model(),
            )

    def test_quantlib_global_evaluation_date_is_restored(self):
        settings = ql.Settings.instance()
        original = ql.Date(1, 1, 2000)
        previous = settings.evaluationDate
        try:
            settings.evaluationDate = original
            QuantLibPricingAdapter().price(
                request=option_request(),
                instrument=option(),
                market_snapshot=option_market(),
                model=option_model(),
            )
            self.assertEqual(settings.evaluationDate, original)
        finally:
            settings.evaluationDate = previous

    def test_tampered_request_binding_fails_closed(self):
        request = replace(
            option_request(),
            instrument_id="pricing-instrument:tampered",
        )
        with self.assertRaises(ValueError):
            QuantLibPricingAdapter().price(
                request=request,
                instrument=option(),
                market_snapshot=option_market(),
                model=option_model(),
            )

    def test_result_store_is_idempotent(self):
        result = QuantLibPricingAdapter().price(
            request=option_request(),
            instrument=option(),
            market_snapshot=option_market(),
            model=option_model(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = PricingResultStore(Path(tmp) / "pricing-results.duckdb")
            self.assertTrue(store.add(result))
            self.assertFalse(store.add(result))
            store.close()


def black_scholes_call(
    *,
    spot,
    strike,
    risk_free,
    dividend,
    volatility,
    time_years,
):
    d1 = (
        math.log(spot / strike)
        + (
            risk_free
            - dividend
            + 0.5 * volatility * volatility
        )
        * time_years
    ) / (volatility * math.sqrt(time_years))
    d2 = d1 - volatility * math.sqrt(time_years)
    nd1 = normal_cdf(d1)
    nd2 = normal_cdf(d2)
    discount_r = math.exp(-risk_free * time_years)
    discount_q = math.exp(-dividend * time_years)
    pdf_d1 = (
        math.exp(-0.5 * d1 * d1)
        / math.sqrt(2.0 * math.pi)
    )
    return {
        "npv": (
            spot * discount_q * nd1
            - strike * discount_r * nd2
        ),
        "delta": discount_q * nd1,
        "gamma": (
            discount_q
            * pdf_d1
            / (spot * volatility * math.sqrt(time_years))
        ),
        "vega": (
            spot
            * discount_q
            * pdf_d1
            * math.sqrt(time_years)
        ),
    }


def normal_cdf(value):
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


if __name__ == "__main__":
    unittest.main()
