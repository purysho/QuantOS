import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from quantos.portfolio_risk_cube import (
    PortfolioRiskCubeEngine,
    PortfolioRiskCubeState,
    PortfolioRiskCubeStore,
    PortfolioRiskPosition,
)
from quantos.pricing_risk_contracts import (
    Currency,
    EquityInstrument,
    MarketDataSnapshotBuilder,
    MarketQuote,
    MarketQuoteType,
    MarketShock,
    PricingMeasure,
    PricingModelSpecification,
    PricingRequestBuilder,
    QuoteUnit,
    RiskScenario,
    ShockKind,
)
from quantos.scenario_revaluation import (
    ScenarioRevaluationEngine,
)

UTC = timezone.utc
AT = datetime(2026, 9, 25, 12, tzinfo=UTC)


def market(a="100", b="50"):
    return MarketDataSnapshotBuilder().build(
        valuation_time=AT,
        base_currency=Currency.USD,
        quotes=(
            MarketQuote(
                quote_type=MarketQuoteType.EQUITY_SPOT,
                market_key="SEC:A",
                value=Decimal(a),
                unit=QuoteUnit.PRICE,
                event_time=AT - timedelta(minutes=2),
                knowledge_time=AT - timedelta(minutes=1),
                source_fact_ids=("source:A",),
                currency=Currency.USD,
            ),
            MarketQuote(
                quote_type=MarketQuoteType.EQUITY_SPOT,
                market_key="SEC:B",
                value=Decimal(b),
                unit=QuoteUnit.PRICE,
                event_time=AT - timedelta(minutes=2),
                knowledge_time=AT - timedelta(minutes=1),
                source_fact_ids=("source:B",),
                currency=Currency.USD,
            ),
        ),
    )


def model():
    return PricingModelSpecification(
        model_family="SPOT_MARK_TO_MARKET",
        model_version="1",
        parameters=(),
        rationale="Risk cube spot fixture.",
        evidence_references=("model:spot",),
    )


def instrument(security_id):
    return EquityInstrument(
        security_id=security_id,
        currency=Currency.USD,
    )


def scenario_a():
    return RiskScenario(
        name="A down ten percent",
        shocks=(
            MarketShock(
                quote_type=MarketQuoteType.EQUITY_SPOT,
                market_key="SEC:A",
                shock_kind=ShockKind.RELATIVE,
                shock_value=Decimal("-0.10"),
                currency=Currency.USD,
            ),
        ),
        rationale="Single-factor A stress.",
        evidence_references=("scenario:A",),
    )


def scenario_b():
    return RiskScenario(
        name="B down twenty percent",
        shocks=(
            MarketShock(
                quote_type=MarketQuoteType.EQUITY_SPOT,
                market_key="SEC:B",
                shock_kind=ShockKind.RELATIVE,
                shock_value=Decimal("-0.20"),
                currency=Currency.USD,
            ),
        ),
        rationale="Single-factor B stress.",
        evidence_references=("scenario:B",),
    )


def revaluation(security_id, scenario, base=None):
    base = base or market()
    inst = instrument(security_id)
    pricing_model = model()
    request = PricingRequestBuilder().build(
        instrument=inst,
        market_snapshot=base,
        model=pricing_model,
        measures=(PricingMeasure.NPV,),
        reporting_currency=Currency.USD,
    )
    return ScenarioRevaluationEngine().revalue_spot_or_option(
        request=request,
        instrument=inst,
        base_snapshot=base,
        model=pricing_model,
        scenario=scenario,
    )


def positions():
    return (
        PortfolioRiskPosition(
            instrument_id=instrument("SEC:A").instrument_id,
            quantity=Decimal("2"),
            book_id="BOOK:CORE",
            label="A holding",
        ),
        PortfolioRiskPosition(
            instrument_id=instrument("SEC:B").instrument_id,
            quantity=Decimal("3"),
            book_id="BOOK:CORE",
            label="B holding",
        ),
    )


def full_revaluations(base=None):
    base = base or market()
    return (
        revaluation("SEC:A", scenario_a(), base),
        revaluation("SEC:B", scenario_a(), base),
        revaluation("SEC:A", scenario_b(), base),
        revaluation("SEC:B", scenario_b(), base),
    )


class PortfolioRiskCubeTests(unittest.TestCase):
    def test_complete_cube_scales_position_pnl_and_base_values(self):
        cube = PortfolioRiskCubeEngine().build(
            positions=positions(),
            scenarios=(scenario_a(), scenario_b()),
            revaluations=full_revaluations(),
        )
        self.assertEqual(cube.state, PortfolioRiskCubeState.COMPLETE)
        self.assertEqual(cube.valuation_time, AT)
        self.assertEqual(cube.net_base_value, Decimal("350"))
        self.assertEqual(cube.gross_base_value, Decimal("350"))
        summaries = {
            item.scenario_id: item
            for item in cube.scenario_summaries
        }
        self.assertEqual(
            summaries[scenario_a().scenario_id].portfolio_pnl,
            Decimal("-20.00"),
        )
        self.assertEqual(
            summaries[scenario_b().scenario_id].portfolio_pnl,
            Decimal("-30.00"),
        )
        self.assertEqual(cube.var_authority, "NONE")
        self.assertEqual(cube.order_authority, "NONE")
        self.assertEqual(cube.capital_authority, "NONE")

    def test_concentration_is_absolute_base_npv_share(self):
        cube = PortfolioRiskCubeEngine().build(
            positions=positions(),
            scenarios=(scenario_a(), scenario_b()),
            revaluations=full_revaluations(),
        )
        expected = Decimal("200") / Decimal("350")
        self.assertEqual(
            cube.maximum_base_value_concentration,
            expected,
        )

    def test_single_shock_sensitivity_is_finite_difference_ratio(self):
        cube = PortfolioRiskCubeEngine().build(
            positions=positions(),
            scenarios=(scenario_a(), scenario_b()),
            revaluations=full_revaluations(),
        )
        sensitivities = {
            item.scenario_id: item
            for item in cube.finite_difference_sensitivities
        }
        a = sensitivities[scenario_a().scenario_id]
        self.assertEqual(a.portfolio_pnl, Decimal("-20.00"))
        self.assertEqual(
            a.pnl_per_unit_shock,
            Decimal("200.0"),
        )

    def test_missing_cell_is_visible_and_suppresses_partial_portfolio_pnl(self):
        values = full_revaluations()[:-1]
        cube = PortfolioRiskCubeEngine().build(
            positions=positions(),
            scenarios=(scenario_a(), scenario_b()),
            revaluations=values,
        )
        self.assertEqual(
            cube.state,
            PortfolioRiskCubeState.INCOMPLETE,
        )
        self.assertEqual(len(cube.missing_coverage), 1)
        b_summary = next(
            item
            for item in cube.scenario_summaries
            if item.scenario_id == scenario_b().scenario_id
        )
        self.assertFalse(b_summary.complete)
        self.assertIsNone(b_summary.portfolio_pnl)

    def test_mixed_base_snapshots_fail_closed(self):
        first_base = market()
        second_base = market(a="101")
        values = (
            revaluation("SEC:A", scenario_a(), first_base),
            revaluation("SEC:B", scenario_a(), first_base),
            revaluation("SEC:A", scenario_b(), second_base),
            revaluation("SEC:B", scenario_b(), second_base),
        )
        with self.assertRaises(ValueError):
            PortfolioRiskCubeEngine().build(
                positions=positions(),
                scenarios=(scenario_a(), scenario_b()),
                revaluations=values,
            )

    def test_ambiguous_duplicate_cell_fails_closed(self):
        values = full_revaluations()
        duplicate = replace(
            values[0],
            diagnostics=(
                *values[0].diagnostics,
                "tampered duplicate",
            ),
        )
        with self.assertRaises(ValueError):
            PortfolioRiskCubeEngine().build(
                positions=positions(),
                scenarios=(scenario_a(), scenario_b()),
                revaluations=(
                    *values,
                    duplicate,
                ),
            )

    def test_short_position_reverses_scenario_pnl_direction(self):
        base = market()
        short_positions = (
            PortfolioRiskPosition(
                instrument_id=instrument("SEC:A").instrument_id,
                quantity=Decimal("-2"),
                book_id="BOOK:HEDGE",
            ),
        )
        cube = PortfolioRiskCubeEngine().build(
            positions=short_positions,
            scenarios=(scenario_a(),),
            revaluations=(
                revaluation("SEC:A", scenario_a(), base),
            ),
        )
        self.assertEqual(
            cube.scenario_summaries[0].portfolio_pnl,
            Decimal("20.00"),
        )
        self.assertEqual(cube.net_base_value, Decimal("-200"))
        self.assertEqual(cube.gross_base_value, Decimal("200"))

    def test_store_is_idempotent(self):
        cube = PortfolioRiskCubeEngine().build(
            positions=positions(),
            scenarios=(scenario_a(), scenario_b()),
            revaluations=full_revaluations(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = PortfolioRiskCubeStore(
                Path(tmp) / "risk-cube.duckdb"
            )
            self.assertTrue(store.add(cube))
            self.assertFalse(store.add(cube))
            store.close()


if __name__ == "__main__":
    unittest.main()