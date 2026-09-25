import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from quantos.bond_pricing import BondPricingValidationPolicy
from quantos.interest_rate_curves import CurveConstructionPolicy
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
    PricingMeasure,
    PricingModelSpecification,
    PricingRequestBuilder,
    QuoteUnit,
    RiskScenario,
    ShockKind,
)
from quantos.scenario_revaluation import (
    ScenarioMarketTransformer,
    ScenarioRevaluationEngine,
    ScenarioRevaluationStore,
    scenario_market_state_identity,
)
from quantos.swap_pricing import SwapPricingValidationPolicy

UTC = timezone.utc
AT = datetime(2026, 9, 25, 12, tzinfo=UTC)


def quote(kind, key, value, *, tenor=None, unit=QuoteUnit.DECIMAL):
    return MarketQuote(
        quote_type=kind,
        market_key=key,
        value=Decimal(value),
        unit=unit,
        event_time=AT - timedelta(minutes=2),
        knowledge_time=AT - timedelta(minutes=1),
        source_fact_ids=(f"source:{kind.value}:{key}:{tenor or 'spot'}",),
        tenor=tenor,
        currency=Currency.USD,
    )


def full_market():
    return MarketDataSnapshotBuilder().build(
        valuation_time=AT,
        base_currency=Currency.USD,
        quotes=(
            quote(
                MarketQuoteType.EQUITY_SPOT,
                "SEC:A",
                "100",
                unit=QuoteUnit.PRICE,
            ),
            quote(MarketQuoteType.ZERO_RATE, "USD-RF", "0.05"),
            quote(MarketQuoteType.ZERO_RATE, "SEC:A-DIV", "0.02"),
            quote(MarketQuoteType.VOLATILITY, "SEC:A-VOL", "0.20"),
            quote(MarketQuoteType.ZERO_RATE, "USD-DISC", "0.030", tenor="1Y"),
            quote(MarketQuoteType.ZERO_RATE, "USD-DISC", "0.034", tenor="2Y"),
            quote(MarketQuoteType.ZERO_RATE, "USD-DISC", "0.040", tenor="5Y"),
            quote(MarketQuoteType.ZERO_RATE, "USD-FWD-3M", "0.032", tenor="1Y"),
            quote(MarketQuoteType.ZERO_RATE, "USD-FWD-3M", "0.036", tenor="2Y"),
            quote(MarketQuoteType.ZERO_RATE, "USD-FWD-3M", "0.041", tenor="5Y"),
        ),
    )


def scenario(*shocks):
    return RiskScenario(
        name="Stage 11.6 deterministic stress",
        shocks=tuple(shocks),
        rationale="Scenario revaluation test fixture.",
        evidence_references=("scenario:test",),
    )


def equity():
    return EquityInstrument(
        security_id="SEC:A",
        currency=Currency.USD,
    )


def option():
    return EuropeanOptionInstrument(
        contract_id="OPT:A",
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
            ("dividend_yield_key", "SEC:A-DIV"),
            ("risk_free_rate_key", "USD-RF"),
            ("volatility_key", "SEC:A-VOL"),
        ),
        rationale="Scenario BSM model.",
        evidence_references=("model:option",),
    )


def spot_model():
    return PricingModelSpecification(
        model_family="SPOT_MARK_TO_MARKET",
        model_version="1",
        parameters=(),
        rationale="Scenario spot model.",
        evidence_references=("model:spot",),
    )


def pricing_request(instrument, market, model, measures=(PricingMeasure.NPV,)):
    return PricingRequestBuilder().build(
        instrument=instrument,
        market_snapshot=market,
        model=model,
        measures=measures,
        reporting_currency=Currency.USD,
    )


def curve_policy(key):
    return CurveConstructionPolicy(
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
        rationale=f"Scenario curve policy {key}.",
        evidence_references=(f"curve:{key}",),
    )


def bond():
    return FixedRateBondInstrument(
        contract_id="BOND:SCENARIO",
        currency=Currency.USD,
        face_value=Decimal("1000"),
        issue_date=date(2025, 3, 15),
        maturity_date=date(2030, 3, 15),
        coupon_rate=Decimal("0.045"),
        coupon_frequency_months=6,
        day_count=DayCountConvention.ACT_365_FIXED,
        settlement_days=2,
    )


def bond_model():
    return PricingModelSpecification(
        model_family="DISCOUNT_CURVE_FIXED_RATE_BOND",
        model_version="1",
        parameters=tuple(
            sorted(
                {
                    "calendar": "NULL_CALENDAR",
                    "business_day_convention": "UNADJUSTED",
                    "date_generation": "FORWARD",
                    "end_of_month": "FALSE",
                    "redemption": "100",
                    "dv01_bump": "0.0001",
                }.items()
            )
        ),
        rationale="Scenario bond model.",
        evidence_references=("model:bond",),
    )


def bond_validation():
    return BondPricingValidationPolicy(
        maximum_absolute_npv_difference=Decimal("1e-8"),
        maximum_absolute_price_difference=Decimal("1e-8"),
        maximum_absolute_accrued_difference=Decimal("1e-8"),
        maximum_absolute_dv01_difference=Decimal("1e-8"),
        rationale="Scenario bond differential gate.",
        evidence_references=("validation:bond",),
    )


def swap():
    return FixedFloatSwapInstrument(
        contract_id="SWAP:SCENARIO",
        currency=Currency.USD,
        notional=Decimal("1000000"),
        effective_date=date(2026, 10, 1),
        maturity_date=date(2030, 10, 1),
        fixed_rate=Decimal("0.038"),
        fixed_leg_frequency_months=6,
        fixed_leg_day_count=DayCountConvention.ACT_365_FIXED,
        floating_leg_frequency_months=3,
        floating_leg_day_count=DayCountConvention.ACT_365_FIXED,
        floating_index_id="FC-USD-3M",
        floating_spread=Decimal("0.001"),
        fixed_leg_direction=PayReceive.PAY,
    )


def swap_model():
    return PricingModelSpecification(
        model_family="DUAL_CURVE_FIXED_FLOAT_SWAP",
        model_version="1",
        parameters=tuple(
            sorted(
                {
                    "calendar": "NULL_CALENDAR",
                    "business_day_convention": "UNADJUSTED",
                    "date_generation": "FORWARD",
                    "end_of_month": "FALSE",
                    "fixing_days": "0",
                    "floating_index_mode": "PROJECTED_SIMPLE_FORWARD",
                    "discount_curve_key": "USD-DISC",
                    "forwarding_curve_key": "USD-FWD-3M",
                    "dv01_bump": "0.0001",
                }.items()
            )
        ),
        rationale="Scenario swap model.",
        evidence_references=("model:swap",),
    )


def swap_validation():
    return SwapPricingValidationPolicy(
        maximum_absolute_npv_difference=Decimal("1e-7"),
        maximum_absolute_fixed_leg_dv01_difference=Decimal("1e-7"),
        rationale="Scenario swap differential gate.",
        evidence_references=("validation:swap",),
    )


class ScenarioRevaluationTests(unittest.TestCase):
    def test_relative_spot_shock_creates_content_addressed_derived_state(self):
        base = full_market()
        stress = scenario(
            MarketShock(
                quote_type=MarketQuoteType.EQUITY_SPOT,
                market_key="SEC:A",
                shock_kind=ShockKind.RELATIVE,
                shock_value=Decimal("-0.10"),
                currency=Currency.USD,
            )
        )
        shocked, state = ScenarioMarketTransformer().apply(
            base_snapshot=base,
            scenario=stress,
        )
        shocked_spot = next(
            item
            for item in shocked.quotes
            if item.quote_type is MarketQuoteType.EQUITY_SPOT
        )
        self.assertEqual(shocked_spot.value, Decimal("90.00"))
        self.assertNotEqual(base.snapshot_id, shocked.snapshot_id)
        self.assertEqual(
            state.state_id,
            scenario_market_state_identity(state),
        )

    def test_unmatched_shock_fails_closed(self):
        with self.assertRaises(ValueError):
            ScenarioMarketTransformer().apply(
                base_snapshot=full_market(),
                scenario=scenario(
                    MarketShock(
                        quote_type=MarketQuoteType.EQUITY_SPOT,
                        market_key="MISSING",
                        shock_kind=ShockKind.RELATIVE,
                        shock_value=Decimal("-0.10"),
                        currency=Currency.USD,
                    )
                ),
            )

    def test_equity_scenario_pnl_is_exact_mark_to_market_change(self):
        base = full_market()
        instrument = equity()
        model = spot_model()
        result = ScenarioRevaluationEngine().revalue_spot_or_option(
            request=pricing_request(instrument, base, model),
            instrument=instrument,
            base_snapshot=base,
            model=model,
            scenario=scenario(
                MarketShock(
                    quote_type=MarketQuoteType.EQUITY_SPOT,
                    market_key="SEC:A",
                    shock_kind=ShockKind.ABSOLUTE,
                    shock_value=Decimal("-15"),
                    currency=Currency.USD,
                )
            ),
        )
        self.assertEqual(result.base_npv, Decimal("100"))
        self.assertEqual(result.shocked_npv, Decimal("85"))
        self.assertEqual(result.scenario_pnl, Decimal("-15"))
        self.assertEqual(result.order_authority, "NONE")
        self.assertEqual(result.capital_authority, "NONE")

    def test_equity_down_shock_reduces_call_value(self):
        base = full_market()
        instrument = option()
        model = option_model()
        result = ScenarioRevaluationEngine().revalue_spot_or_option(
            request=pricing_request(instrument, base, model),
            instrument=instrument,
            base_snapshot=base,
            model=model,
            scenario=scenario(
                MarketShock(
                    quote_type=MarketQuoteType.EQUITY_SPOT,
                    market_key="SEC:A",
                    shock_kind=ShockKind.RELATIVE,
                    shock_value=Decimal("-0.20"),
                    currency=Currency.USD,
                )
            ),
        )
        self.assertLess(result.scenario_pnl, Decimal("0"))

    def test_parallel_rate_shock_rebuilds_bond_curve_and_reduces_value(self):
        base = full_market()
        instrument = bond()
        model = bond_model()
        shocks = tuple(
            MarketShock(
                quote_type=MarketQuoteType.ZERO_RATE,
                market_key="USD-DISC",
                tenor=tenor,
                shock_kind=ShockKind.ABSOLUTE,
                shock_value=Decimal("0.01"),
                currency=Currency.USD,
            )
            for tenor in ("1Y", "2Y", "5Y")
        )
        result = ScenarioRevaluationEngine().revalue_bond(
            request=pricing_request(
                instrument,
                base,
                model,
                measures=(
                    PricingMeasure.NPV,
                    PricingMeasure.DV01,
                ),
            ),
            instrument=instrument,
            base_snapshot=base,
            model=model,
            scenario=scenario(*shocks),
            curve_policy=curve_policy("USD-DISC"),
            validation_policy=bond_validation(),
        )
        self.assertLess(result.scenario_pnl, Decimal("0"))
        self.assertEqual(len(result.base_curve_ids), 1)
        self.assertEqual(len(result.shocked_curve_ids), 1)
        self.assertNotEqual(
            result.base_curve_ids,
            result.shocked_curve_ids,
        )

    def test_forward_curve_shock_changes_pay_fixed_swap_value(self):
        base = full_market()
        instrument = swap()
        model = swap_model()
        shocks = tuple(
            MarketShock(
                quote_type=MarketQuoteType.ZERO_RATE,
                market_key="USD-FWD-3M",
                tenor=tenor,
                shock_kind=ShockKind.ABSOLUTE,
                shock_value=Decimal("0.005"),
                currency=Currency.USD,
            )
            for tenor in ("1Y", "2Y", "5Y")
        )
        result = ScenarioRevaluationEngine().revalue_swap(
            request=pricing_request(
                instrument,
                base,
                model,
                measures=(
                    PricingMeasure.NPV,
                    PricingMeasure.DV01,
                ),
            ),
            instrument=instrument,
            base_snapshot=base,
            model=model,
            scenario=scenario(*shocks),
            discount_curve_policy=curve_policy("USD-DISC"),
            forwarding_curve_policy=curve_policy("USD-FWD-3M"),
            validation_policy=swap_validation(),
        )
        self.assertGreater(result.scenario_pnl, Decimal("0"))
        self.assertEqual(len(result.base_curve_ids), 2)
        self.assertEqual(len(result.shocked_curve_ids), 2)

    def test_tampered_base_snapshot_fails_closed(self):
        base = full_market()
        tampered = replace(
            base,
            base_currency=Currency.GBP,
        )
        instrument = equity()
        model = spot_model()
        with self.assertRaises(ValueError):
            ScenarioRevaluationEngine().revalue_spot_or_option(
                request=pricing_request(instrument, base, model),
                instrument=instrument,
                base_snapshot=tampered,
                model=model,
                scenario=scenario(
                    MarketShock(
                        quote_type=MarketQuoteType.EQUITY_SPOT,
                        market_key="SEC:A",
                        shock_kind=ShockKind.RELATIVE,
                        shock_value=Decimal("-0.10"),
                        currency=Currency.USD,
                    )
                ),
            )

    def test_store_is_idempotent(self):
        base = full_market()
        instrument = equity()
        model = spot_model()
        result = ScenarioRevaluationEngine().revalue_spot_or_option(
            request=pricing_request(instrument, base, model),
            instrument=instrument,
            base_snapshot=base,
            model=model,
            scenario=scenario(
                MarketShock(
                    quote_type=MarketQuoteType.EQUITY_SPOT,
                    market_key="SEC:A",
                    shock_kind=ShockKind.RELATIVE,
                    shock_value=Decimal("-0.10"),
                    currency=Currency.USD,
                )
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = ScenarioRevaluationStore(
                Path(tmp) / "scenario-revaluation.duckdb"
            )
            self.assertTrue(store.add(result))
            self.assertFalse(store.add(result))
            store.close()


if __name__ == "__main__":
    unittest.main()
