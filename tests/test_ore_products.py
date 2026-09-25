import unittest
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from quantos.ore_products import (
    BUCKET_SHIFT,
    OREBondDifferential,
    OREEuropeanOptionDifferential,
    ORESwapRiskAnalytics,
    black_scholes_merton_npv,
    ore_product_result_identity,
)
from quantos.ore_risk import ORE_PINNED_VERSION, OREDifferentialPolicy, OREDifferentialState, ore_is_available
from quantos.pricing_quantlib import QuantLibPricingAdapter
from quantos.pricing_risk_contracts import (
    Currency,
    MarketQuoteType,
    MarketShock,
    OptionType,
    PricingMeasure,
    PricingRequestBuilder,
    ShockKind,
)
from quantos.scenario_revaluation import ScenarioRevaluationEngine

from tests import test_bond_pricing as bonds
from tests import test_pricing_quantlib as options
from tests import test_scenario_revaluation as scen

requires_ore = unittest.skipUnless(ore_is_available(), f"ORE {ORE_PINNED_VERSION} not installed")


def policy(tolerance="1e-6"):
    return OREDifferentialPolicy(
        maximum_absolute_npv_difference=Decimal(tolerance),
        rationale="ORE must reproduce the frozen First Current overlap.",
        evidence_references=("ore-policy:products",),
    )


class FakeRunner:
    ore_version = ORE_PINNED_VERSION

    def __init__(self, value):
        self.value = Decimal(value)

    def npv(self, bundle):
        return self.value


def option_fixture(option_type=OptionType.CALL, multiplier="1"):
    instrument = replace(options.option(), option_type=option_type, multiplier=Decimal(multiplier))
    snapshot = options.option_market()
    model = options.option_model()
    request = PricingRequestBuilder().build(
        instrument=instrument,
        market_snapshot=snapshot,
        model=model,
        measures=(PricingMeasure.NPV,),
        reporting_currency=Currency.USD,
    )
    result = QuantLibPricingAdapter().price(request=request, instrument=instrument, market_snapshot=snapshot, model=model)
    return result, request, instrument, snapshot, model


def compare_option(runner=None, **kwargs):
    result, request, instrument, snapshot, model = option_fixture(**kwargs)
    return OREEuropeanOptionDifferential().compare(
        pricing_result=result, request=request, instrument=instrument,
        market_snapshot=snapshot, model=model, policy=policy(), runner=runner,
    )


def compare_bond(runner=None, **overrides):
    instrument = bonds.bond(**overrides)
    snapshot = bonds.market()
    curve = bonds.curve(snapshot)
    result = bonds.price(instrument=instrument, market_snapshot=snapshot, discount_curve=curve)
    return OREBondDifferential().compare(bond_result=result, instrument=instrument, discount_curve=curve, policy=policy(), runner=runner)


def bucket_scenario(key, tenor, shift=BUCKET_SHIFT):
    return scen.scenario(
        MarketShock(quote_type=MarketQuoteType.ZERO_RATE, market_key=key, tenor=tenor,
                    shock_kind=ShockKind.ABSOLUTE, shock_value=Decimal(shift), currency=Currency.USD)
    )


def revalue(risk_scenario):
    base = scen.full_market()
    instrument = scen.swap()
    model = scen.swap_model()
    return ScenarioRevaluationEngine().revalue_swap(
        request=scen.pricing_request(instrument, base, model, measures=(PricingMeasure.NPV, PricingMeasure.DV01)),
        instrument=instrument, base_snapshot=base, model=model, scenario=risk_scenario,
        discount_curve_policy=scen.curve_policy("USD-DISC"),
        forwarding_curve_policy=scen.curve_policy("USD-FWD-3M"),
        validation_policy=scen.swap_validation(),
    )


class ReferenceAndGateTests(unittest.TestCase):
    def test_closed_form_put_call_parity(self):
        args = dict(spot=Decimal("100"), strike=Decimal("95"), risk_free_rate=Decimal("0.05"),
                    dividend_yield=Decimal("0.02"), volatility=Decimal("0.2"), time_years=Decimal("1"))
        call = black_scholes_merton_npv(option_type=OptionType.CALL, **args)
        put = black_scholes_merton_npv(option_type=OptionType.PUT, **args)
        import math
        parity = 100 * math.exp(-0.02) - 95 * math.exp(-0.05)
        self.assertAlmostEqual(float(call - put), parity, places=12)

    def test_option_mismatch_is_preserved(self):
        diff = compare_option(runner=FakeRunner("1"))
        self.assertEqual(diff.state, OREDifferentialState.MISMATCH)
        self.assertEqual(diff.trust_authority, "NONE")
        self.assertEqual(diff.result_id, ore_product_result_identity(diff))

    def test_bond_scope_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "issue day"):
            compare_bond(runner=FakeRunner("0"), issue_date=date(2025, 3, 31), maturity_date=date(2030, 3, 31))
        with self.assertRaisesRegex(ValueError, "between valuation and settlement"):
            OREBondDifferential().bundle(
                instrument=bonds.bond(issue_date=date(2025, 3, 26), maturity_date=date(2030, 3, 26)),
                discount_curve=bonds.curve(),
                settlement_date=date(2026, 9, 30),
            )

    def test_sensitivity_requires_complete_single_pillar_buckets(self):
        partial = (bucket_scenario("USD-DISC", "1Y"),)
        with self.assertRaisesRegex(ValueError, "every curve pillar"):
            ORESwapRiskAnalytics().sensitivity(
                instrument=scen.swap(), base_snapshot=scen.full_market(),
                discount_curve_policy=scen.curve_policy("USD-DISC"),
                forwarding_curve_policy=scen.curve_policy("USD-FWD-3M"),
                bucket_revaluations=tuple(revalue(s) for s in partial), bucket_scenarios=partial,
                policy=policy(), runner=FakeRunner("0"),
            )

    def test_stress_refuses_relative_shocks(self):
        relative = scen.scenario(MarketShock(quote_type=MarketQuoteType.ZERO_RATE, market_key="USD-DISC", tenor="1Y",
                                             shock_kind=ShockKind.RELATIVE, shock_value=Decimal("0.1"), currency=Currency.USD))
        with self.assertRaisesRegex(ValueError, "absolute zero-rate"):
            ORESwapRiskAnalytics().stress(
                revaluation=revalue(relative), instrument=scen.swap(), base_snapshot=scen.full_market(), scenario=relative,
                discount_curve_policy=scen.curve_policy("USD-DISC"), forwarding_curve_policy=scen.curve_policy("USD-FWD-3M"),
                policy=policy(), runner=FakeRunner("0"),
            )


@requires_ore
class ORERuntimeProductTests(unittest.TestCase):
    def test_bond_matches_reference_and_quantlib(self):
        diff = compare_bond()
        self.assertEqual(diff.state, OREDifferentialState.MATCH, diff)
        self.assertEqual(diff.product, "FIXED_RATE_BOND")

    def test_options_match_closed_form_and_quantlib(self):
        for option_type in (OptionType.CALL, OptionType.PUT):
            with self.subTest(option_type=option_type):
                diff = compare_option(option_type=option_type, multiplier="100")
                self.assertEqual(diff.state, OREDifferentialState.MATCH, diff)
                self.assertLess(diff.absolute_difference_vs_reference, Decimal("1e-8"))

    def test_ore_bucketed_sensitivities_match_single_pillar_revaluations(self):
        scenarios = tuple(bucket_scenario(key, tenor) for key in ("USD-DISC", "USD-FWD-3M") for tenor in ("1Y", "2Y", "5Y"))
        result = ORESwapRiskAnalytics().sensitivity(
            instrument=scen.swap(), base_snapshot=scen.full_market(),
            discount_curve_policy=scen.curve_policy("USD-DISC"),
            forwarding_curve_policy=scen.curve_policy("USD-FWD-3M"),
            bucket_revaluations=tuple(revalue(s) for s in scenarios), bucket_scenarios=scenarios,
            policy=policy(),
        )
        self.assertEqual(result.state, OREDifferentialState.MATCH, result)
        self.assertEqual(len(result.buckets), 6)
        self.assertTrue(any(b.factor.startswith("IndexCurve/USD-FCREF-3M/") for b in result.buckets))

    def test_ore_stress_matches_scenario_revaluation(self):
        risk_scenario = scen.scenario(
            *(MarketShock(quote_type=MarketQuoteType.ZERO_RATE, market_key=key, tenor=tenor, shock_kind=ShockKind.ABSOLUTE,
                          shock_value=Decimal(value), currency=Currency.USD)
              for key, tenor, value in (("USD-DISC", "1Y", "0.01"), ("USD-DISC", "5Y", "0.0025"), ("USD-FWD-3M", "2Y", "-0.002")))
        )
        result = ORESwapRiskAnalytics().stress(
            revaluation=revalue(risk_scenario), instrument=scen.swap(), base_snapshot=scen.full_market(), scenario=risk_scenario,
            discount_curve_policy=scen.curve_policy("USD-DISC"), forwarding_curve_policy=scen.curve_policy("USD-FWD-3M"),
            policy=policy(),
        )
        self.assertEqual(result.state, OREDifferentialState.MATCH, result)
        self.assertNotEqual(result.ore_pnl, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
