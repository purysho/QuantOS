import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from quantos.pricing_risk_contracts import (
    Currency,
    DayCountConvention,
    EquityInstrument,
    EuropeanOptionInstrument,
    FixedFloatSwapInstrument,
    FixedRateBondInstrument,
    MarketDataSnapshotBuilder,
    MarketQuote,
    MarketQuoteType,
    MarketShock,
    OptionType,
    PayReceive,
    PricingContractStore,
    PricingMeasure,
    PricingModelSpecification,
    PricingRequestBuilder,
    QuoteUnit,
    RiskScenario,
    ShockKind,
    market_data_snapshot_identity,
)

UTC = timezone.utc
AT = datetime(2026, 9, 25, 12, tzinfo=UTC)


def spot(
    key="SEC:A",
    value="100",
    event_time=None,
    knowledge_time=None,
):
    event = event_time or AT - timedelta(minutes=2)
    known = knowledge_time or AT - timedelta(minutes=1)
    return MarketQuote(
        quote_type=MarketQuoteType.EQUITY_SPOT,
        market_key=key,
        value=Decimal(value),
        unit=QuoteUnit.PRICE,
        event_time=event,
        knowledge_time=known,
        source_fact_ids=(f"source:{key}:{value}",),
        currency=Currency.USD,
    )


def snapshot():
    return MarketDataSnapshotBuilder().build(
        valuation_time=AT,
        base_currency=Currency.USD,
        quotes=(
            spot("SEC:B", "50"),
            spot("SEC:A", "100"),
        ),
    )


def model():
    return PricingModelSpecification(
        model_family="REFERENCE_CONTRACT_ONLY",
        model_version="1",
        parameters=(("curve_mode", "frozen"),),
        rationale="Stage 11.1 contract fixture.",
        evidence_references=("pricing-model:evidence",),
    )


class PricingRiskContractTests(unittest.TestCase):
    def test_snapshot_is_order_independent_and_point_in_time(self):
        left = snapshot()
        right = MarketDataSnapshotBuilder().build(
            valuation_time=AT,
            base_currency=Currency.USD,
            quotes=tuple(reversed(left.quotes)),
        )
        self.assertEqual(left.snapshot_id, right.snapshot_id)
        self.assertEqual(left.quote_ids, right.quote_ids)
        self.assertEqual(
            left.snapshot_id,
            market_data_snapshot_identity(left),
        )

    def test_future_known_quote_fails_closed(self):
        future = spot(
            knowledge_time=AT + timedelta(seconds=1),
        )
        with self.assertRaises(ValueError):
            MarketDataSnapshotBuilder().build(
                valuation_time=AT,
                base_currency=Currency.USD,
                quotes=(future,),
            )

    def test_duplicate_semantic_market_key_fails_closed(self):
        duplicate = replace(
            spot(),
            value=Decimal("101"),
            source_fact_ids=("source:duplicate",),
        )
        with self.assertRaises(ValueError):
            MarketDataSnapshotBuilder().build(
                valuation_time=AT,
                base_currency=Currency.USD,
                quotes=(spot(), duplicate),
            )

    def test_nested_quote_tampering_breaks_snapshot_identity(self):
        snap = snapshot()
        tampered_quote = replace(
            snap.quotes[0],
            value=Decimal("999"),
        )
        tampered = replace(
            snap,
            quotes=(tampered_quote, *snap.quotes[1:]),
        )
        with self.assertRaises(ValueError):
            market_data_snapshot_identity(tampered)


    def test_discount_factor_above_one_is_valid_under_negative_rates(self):
        item = MarketQuote(
            quote_type=MarketQuoteType.DISCOUNT_FACTOR,
            market_key="EUR-OIS",
            value=Decimal("1.01"),
            unit=QuoteUnit.DISCOUNT_FACTOR,
            event_time=AT,
            knowledge_time=AT,
            source_fact_ids=("source:negative-rate-df",),
            tenor="1Y",
            currency=Currency.EUR,
        )
        self.assertEqual(item.value, Decimal("1.01"))

    def test_fixed_rate_bond_contract_validates_dates_and_terms(self):
        bond = FixedRateBondInstrument(
            contract_id="BOND:TEST",
            currency=Currency.USD,
            face_value=Decimal("1000"),
            issue_date=date(2026, 1, 1),
            maturity_date=date(2031, 1, 1),
            coupon_rate=Decimal("0.05"),
            coupon_frequency_months=6,
            day_count=DayCountConvention.ACT_365_FIXED,
            settlement_days=2,
        )
        self.assertTrue(bond.instrument_id.startswith("pricing-instrument:"))
        with self.assertRaises(ValueError):
            replace(
                bond,
                maturity_date=bond.issue_date,
            )

    def test_option_requires_positive_strike_and_future_valuation_expiry(self):
        option = EuropeanOptionInstrument(
            contract_id="OPT:TEST",
            underlying_security_id="SEC:A",
            currency=Currency.USD,
            option_type=OptionType.CALL,
            strike=Decimal("100"),
            expiry=AT + timedelta(days=90),
            multiplier=Decimal("100"),
        )
        request = PricingRequestBuilder().build(
            instrument=option,
            market_snapshot=snapshot(),
            model=model(),
            measures=(PricingMeasure.NPV, PricingMeasure.DELTA),
            reporting_currency=Currency.USD,
        )
        self.assertEqual(request.order_authority, "NONE")
        self.assertEqual(request.capital_authority, "NONE")
        with self.assertRaises(ValueError):
            replace(option, strike=Decimal("0"))
        expired = replace(
            option,
            expiry=AT - timedelta(seconds=1),
        )
        with self.assertRaises(ValueError):
            PricingRequestBuilder().build(
                instrument=expired,
                market_snapshot=snapshot(),
                model=model(),
                measures=(PricingMeasure.NPV,),
                reporting_currency=Currency.USD,
            )

    def test_swap_contract_is_typed_and_content_addressed(self):
        swap = FixedFloatSwapInstrument(
            contract_id="SWAP:TEST",
            currency=Currency.USD,
            notional=Decimal("1000000"),
            effective_date=date(2026, 9, 25),
            maturity_date=date(2031, 9, 25),
            fixed_rate=Decimal("0.04"),
            fixed_leg_frequency_months=6,
            floating_index_id="USD-SOFR",
            floating_spread=Decimal("0"),
            fixed_leg_direction=PayReceive.PAY,
            day_count=DayCountConvention.ACT_360,
        )
        self.assertTrue(swap.instrument_id.startswith("pricing-instrument:"))

    def test_risk_scenario_is_order_independent_and_rejects_double_shock(self):
        first = MarketShock(
            quote_type=MarketQuoteType.EQUITY_SPOT,
            market_key="SEC:A",
            shock_kind=ShockKind.RELATIVE,
            shock_value=Decimal("-0.20"),
            currency=Currency.USD,
        )
        second = MarketShock(
            quote_type=MarketQuoteType.ZERO_RATE,
            market_key="USD-OIS",
            tenor="5Y",
            shock_kind=ShockKind.ABSOLUTE,
            shock_value=Decimal("0.01"),
            currency=Currency.USD,
        )
        a = RiskScenario(
            name="Equity down / rates up",
            shocks=(first, second),
            rationale="Deterministic stress fixture.",
            evidence_references=("scenario:evidence",),
        )
        b = RiskScenario(
            name="Equity down / rates up",
            shocks=(second, first),
            rationale="Deterministic stress fixture.",
            evidence_references=("scenario:evidence",),
        )
        self.assertEqual(a.scenario_id, b.scenario_id)
        with self.assertRaises(ValueError):
            RiskScenario(
                name="Duplicate target",
                shocks=(first, first),
                rationale="Should fail.",
                evidence_references=("scenario:evidence",),
            )

    def test_store_is_idempotent_for_stage_11_contracts(self):
        snap = snapshot()
        equity = EquityInstrument(
            security_id="SEC:A",
            currency=Currency.USD,
        )
        request = PricingRequestBuilder().build(
            instrument=equity,
            market_snapshot=snap,
            model=model(),
            measures=(PricingMeasure.NPV,),
            reporting_currency=Currency.USD,
        )
        scenario = RiskScenario(
            name="Equity down",
            shocks=(
                MarketShock(
                    quote_type=MarketQuoteType.EQUITY_SPOT,
                    market_key="SEC:A",
                    shock_kind=ShockKind.RELATIVE,
                    shock_value=Decimal("-0.10"),
                    currency=Currency.USD,
                ),
            ),
            rationale="Store fixture.",
            evidence_references=("scenario:evidence",),
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = PricingContractStore(Path(tmp) / "pricing.duckdb")
            self.assertTrue(store.add_market_snapshot(snap))
            self.assertFalse(store.add_market_snapshot(snap))
            self.assertTrue(store.add_instrument(equity))
            self.assertFalse(store.add_instrument(equity))
            self.assertTrue(store.add_request(request))
            self.assertFalse(store.add_request(request))
            self.assertTrue(store.add_scenario(scenario))
            self.assertFalse(store.add_scenario(scenario))
            store.close()


if __name__ == "__main__":
    unittest.main()