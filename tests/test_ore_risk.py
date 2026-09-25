import os
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

from quantos.ore_risk import (
    ORE_PINNED_VERSION,
    OREDifferentialPolicy,
    OREDifferentialState,
    OREDifferentialStore,
    OREEngineRunner,
    OREFixedFloatSwapDifferential,
    OREInputBuilder,
    ore_differential_result_identity,
    ore_input_bundle_identity,
    ore_is_available,
)
from quantos.interest_rate_curves import discount_curve_identity
from quantos.pricing_risk_contracts import DayCountConvention, PayReceive

from tests.test_swap_pricing import build_curve, market, price, swap

requires_ore = unittest.skipUnless(
    ore_is_available(), f"open-source-risk-engine=={ORE_PINNED_VERSION} not installed"
)


def policy(tolerance="1e-6"):
    return OREDifferentialPolicy(
        maximum_absolute_npv_difference=Decimal(tolerance),
        rationale="ORE must reproduce the frozen Stage 11.5 overlap.",
        evidence_references=("ore-policy:swap",),
    )


def fixture(**swap_overrides):
    snapshot = market()
    instrument = swap(**swap_overrides)
    discount = build_curve("USD-DISC", snapshot)
    forwarding = build_curve("USD-FWD-3M", snapshot)
    result = price(
        instrument=instrument,
        market_snapshot=snapshot,
        discount_curve=discount,
        forwarding_curve=forwarding,
    )
    return result, instrument, discount, forwarding


class FakeRunner:
    ore_version = ORE_PINNED_VERSION

    def __init__(self, shift):
        self.shift = Decimal(shift)
        self.reference = None

    def npv(self, bundle):
        return self.reference + self.shift


class OREInputBuilderTests(unittest.TestCase):
    def test_bundle_is_content_addressed_and_deterministic(self):
        _, instrument, discount, forwarding = fixture()
        first = OREInputBuilder().swap_bundle(instrument=instrument, discount_curve=discount, forwarding_curve=forwarding)
        second = OREInputBuilder().swap_bundle(instrument=instrument, discount_curve=discount, forwarding_curve=forwarding)
        self.assertEqual(first, second)
        self.assertEqual(first.bundle_id, ore_input_bundle_identity(first))
        self.assertIn("<FixingDays>0</FixingDays>", first.portfolio_xml)
        self.assertIn("<SettlementDays>0</SettlementDays>", first.conventions_xml)
        self.assertIn("USD-FCREF-3M", first.todays_market_xml)
        self.assertEqual(len(first.market_lines), len(discount.pillars) + len(forwarding.pillars))

    def test_bundle_changes_with_economics(self):
        _, instrument, discount, forwarding = fixture()
        base = OREInputBuilder().swap_bundle(instrument=instrument, discount_curve=discount, forwarding_curve=forwarding)
        other = OREInputBuilder().swap_bundle(
            instrument=replace(instrument, fixed_rate=Decimal("0.039")),
            discount_curve=discount,
            forwarding_curve=forwarding,
        )
        self.assertNotEqual(base.bundle_id, other.bundle_id)

    def test_scope_fails_closed(self):
        _, instrument, discount, forwarding = fixture()
        builder = OREInputBuilder()
        extrapolating = replace(discount, allow_extrapolation=True)
        extrapolating = replace(extrapolating, curve_id=discount_curve_identity(extrapolating))
        with self.assertRaisesRegex(ValueError, "extrapolating"):
            builder.swap_bundle(instrument=instrument, discount_curve=extrapolating, forwarding_curve=forwarding)
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            builder.swap_bundle(instrument=instrument, discount_curve=replace(discount, allow_extrapolation=True), forwarding_curve=forwarding)
        with self.assertRaisesRegex(ValueError, "no frozen ORE mapping"):
            builder.swap_bundle(
                instrument=replace(instrument, fixed_leg_day_count=DayCountConvention.THIRTY_360),
                discount_curve=discount,
                forwarding_curve=forwarding,
            )
        with self.assertRaisesRegex(ValueError, "future-starting"):
            builder.swap_bundle(
                instrument=replace(instrument, effective_date=discount.valuation_date),
                discount_curve=discount,
                forwarding_curve=forwarding,
            )
        with self.assertRaisesRegex(ValueError, "last pillar"):
            builder.swap_bundle(
                instrument=replace(instrument, maturity_date=date(2040, 10, 1)),
                discount_curve=discount,
                forwarding_curve=forwarding,
            )


class OREDifferentialGateTests(unittest.TestCase):
    def compare(self, shift, tolerance="1e-6"):
        result, instrument, discount, forwarding = fixture()
        runner = FakeRunner(shift)
        runner.reference = result.reference_npv
        return OREFixedFloatSwapDifferential().compare(
            swap_result=result,
            instrument=instrument,
            discount_curve=discount,
            forwarding_curve=forwarding,
            policy=policy(tolerance),
            runner=runner,
        )

    def test_divergence_is_preserved_as_mismatch(self):
        diff = self.compare("0.5")
        self.assertEqual(diff.state, OREDifferentialState.MISMATCH)
        self.assertEqual(diff.trust_authority, "NONE")
        self.assertEqual(diff.absolute_difference_vs_reference, Decimal("0.5"))
        self.assertEqual(diff.result_id, ore_differential_result_identity(diff))

    def test_tampered_swap_result_is_refused(self):
        result, instrument, discount, forwarding = fixture()
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            OREFixedFloatSwapDifferential().compare(
                swap_result=replace(result, reference_npv=Decimal("0")),
                instrument=instrument,
                discount_curve=discount,
                forwarding_curve=forwarding,
                policy=policy(),
                runner=FakeRunner("0"),
            )

    def test_policy_validation(self):
        with self.assertRaises(ValueError):
            policy("-1")


@requires_ore
class ORERuntimeDifferentialTests(unittest.TestCase):
    def run_case(self, **overrides):
        result, instrument, discount, forwarding = fixture(**overrides)
        return OREFixedFloatSwapDifferential().compare(
            swap_result=result,
            instrument=instrument,
            discount_curve=discount,
            forwarding_curve=forwarding,
            policy=policy(),
        )

    def test_ore_matches_reference_and_quantlib(self):
        diff = self.run_case()
        self.assertEqual(diff.state, OREDifferentialState.MATCH, diff)
        self.assertEqual(diff.trust_authority, "REFERENCE_MATCH_ONLY")
        self.assertEqual(diff.ore_version, ORE_PINNED_VERSION)
        self.assertEqual(diff.order_authority, "NONE")
        self.assertEqual(diff.capital_authority, "NONE")

    def test_act360_floating_leg_and_receive_fixed_match(self):
        diff = self.run_case(
            floating_leg_day_count=DayCountConvention.ACT_360,
            fixed_leg_direction=PayReceive.RECEIVE,
            fixed_leg_frequency_months=12,
            floating_leg_frequency_months=6,
        )
        self.assertEqual(diff.state, OREDifferentialState.MATCH, diff)

    def test_store_is_idempotent(self):
        diff = self.run_case()
        with tempfile.TemporaryDirectory() as tmp:
            store = OREDifferentialStore(Path(tmp) / "ore.duckdb")
            self.assertTrue(store.add(diff))
            self.assertFalse(store.add(diff))
            store.close()

    def test_runner_refuses_tampered_bundle(self):
        _, instrument, discount, forwarding = fixture()
        bundle = OREInputBuilder().swap_bundle(instrument=instrument, discount_curve=discount, forwarding_curve=forwarding)
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            OREEngineRunner().npv(replace(bundle, market_lines=bundle.market_lines[:-1]))


class OREEnvironmentTests(unittest.TestCase):
    def test_ci_requires_ore_where_declared(self):
        if os.environ.get("QUANTOS_REQUIRE_ORE") == "1":
            self.assertTrue(ore_is_available())


if __name__ == "__main__":
    unittest.main()


from quantos.ore_risk import OREScenarioDifferential  # noqa: E402
from quantos.pricing_risk_contracts import (  # noqa: E402
    Currency,
    MarketQuoteType,
    MarketShock,
    PricingMeasure,
    ShockKind,
)
from quantos.scenario_revaluation import ScenarioRevaluationEngine  # noqa: E402
from tests import test_scenario_revaluation as scen  # noqa: E402


def swap_scenario(key, shift):
    return scen.scenario(
        *(
            MarketShock(
                quote_type=MarketQuoteType.ZERO_RATE,
                market_key=key,
                tenor=tenor,
                shock_kind=ShockKind.ABSOLUTE,
                shock_value=Decimal(shift),
                currency=Currency.USD,
            )
            for tenor in ("1Y", "2Y", "5Y")
        )
    )


def revalue(risk_scenario):
    base = scen.full_market()
    instrument = scen.swap()
    model = scen.swap_model()
    result = ScenarioRevaluationEngine().revalue_swap(
        request=scen.pricing_request(
            instrument, base, model, measures=(PricingMeasure.NPV, PricingMeasure.DV01)
        ),
        instrument=instrument,
        base_snapshot=base,
        model=model,
        scenario=risk_scenario,
        discount_curve_policy=scen.curve_policy("USD-DISC"),
        forwarding_curve_policy=scen.curve_policy("USD-FWD-3M"),
        validation_policy=scen.swap_validation(),
    )
    return result, instrument, base


class OREScenarioGateTests(unittest.TestCase):
    def compare(self, risk_scenario, runner):
        result, instrument, base = revalue(risk_scenario)
        return OREScenarioDifferential().compare(
            revaluation=result,
            instrument=instrument,
            base_snapshot=base,
            scenario=risk_scenario,
            discount_curve_policy=scen.curve_policy("USD-DISC"),
            forwarding_curve_policy=scen.curve_policy("USD-FWD-3M"),
            policy=policy(),
            runner=runner,
        ), result

    def test_scenario_mismatch_propagates_to_cube(self):
        class Runner:
            ore_version = ORE_PINNED_VERSION
            calls = 0

            def npv(self, bundle):
                Runner.calls += 1
                return Decimal("100") if Runner.calls % 2 else Decimal("150")

        diff, reference = self.compare(swap_scenario("USD-FWD-3M", "0.005"), Runner())
        self.assertEqual(diff.ore_scenario_pnl, Decimal("50"))
        self.assertEqual(diff.state, OREDifferentialState.MISMATCH)
        cube = OREScenarioDifferential().cube((diff,))
        self.assertEqual(cube.state, OREDifferentialState.MISMATCH)
        self.assertEqual(cube.trust_authority, "NONE")

    def test_other_curve_policy_is_refused(self):
        result, instrument, base = revalue(swap_scenario("USD-DISC", "0.01"))
        other = replace(scen.curve_policy("USD-DISC"), repricing_tolerance=Decimal("1e-11"))
        with self.assertRaisesRegex(ValueError, "rebuilt base curves"):
            OREScenarioDifferential().compare(
                revaluation=result,
                instrument=instrument,
                base_snapshot=base,
                scenario=swap_scenario("USD-DISC", "0.01"),
                discount_curve_policy=other,
                forwarding_curve_policy=scen.curve_policy("USD-FWD-3M"),
                policy=policy(),
                runner=FakeRunner("0"),
            )


@requires_ore
class OREScenarioRuntimeTests(unittest.TestCase):
    def test_ore_reproduces_scenario_pnl_across_cube(self):
        differentials = []
        for key, shift in (("USD-DISC", "0.01"), ("USD-FWD-3M", "0.005"), ("USD-DISC", "-0.0075")):
            risk_scenario = swap_scenario(key, shift)
            result, instrument, base = revalue(risk_scenario)
            diff = OREScenarioDifferential().compare(
                revaluation=result,
                instrument=instrument,
                base_snapshot=base,
                scenario=risk_scenario,
                discount_curve_policy=scen.curve_policy("USD-DISC"),
                forwarding_curve_policy=scen.curve_policy("USD-FWD-3M"),
                policy=policy(),
            )
            self.assertEqual(diff.state, OREDifferentialState.MATCH, diff)
            self.assertNotEqual(diff.ore_scenario_pnl, Decimal("0"))
            differentials.append(diff)
        cube = OREScenarioDifferential().cube(tuple(differentials))
        self.assertEqual(cube.state, OREDifferentialState.MATCH)
        self.assertEqual(cube.trust_authority, "REFERENCE_MATCH_ONLY")
        self.assertLess(cube.maximum_absolute_pnl_difference, Decimal("1e-6"))
