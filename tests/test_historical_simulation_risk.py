import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from quantos.historical_simulation_risk import (
    HistoricalQuantileConvention,
    HistoricalScenarioObservation,
    HistoricalSimulationRiskEngine,
    HistoricalSimulationRiskPolicy,
    HistoricalSimulationRiskStore,
    HistoricalSimulationWeighting,
)
from quantos.portfolio_risk_cube import (
    PortfolioRiskCubeEngine,
    PortfolioRiskCubeState,
    PortfolioRiskPosition,
)
from quantos.pricing_risk_contracts import (
    Currency,
    EquityInstrument,
    MarketQuoteType,
    MarketShock,
    RiskScenario,
    ShockKind,
)
from quantos.scenario_revaluation import (
    ScenarioQuoteTransformation,
    ScenarioRevaluationResult,
    scenario_revaluation_identity,
)

UTC = timezone.utc
AT = datetime(2026, 9, 25, 12, tzinfo=UTC)
BASE_SNAPSHOT = "market-data-snapshot:" + "b" * 64


def instrument():
    return EquityInstrument(
        security_id="SEC:A",
        currency=Currency.USD,
    )


def scenarios(count=20):
    return tuple(
        RiskScenario(
            name=f"Historical scenario {index:02d}",
            shocks=(
                MarketShock(
                    quote_type=MarketQuoteType.EQUITY_SPOT,
                    market_key="SEC:A",
                    shock_kind=ShockKind.ABSOLUTE,
                    shock_value=Decimal(str(-(index + 1))),
                    currency=Currency.USD,
                ),
            ),
            rationale="Historical scenario fixture.",
            evidence_references=(f"scenario:{index}",),
        )
        for index in range(count)
    )


def revaluation(scenario, pnl):
    transform = ScenarioQuoteTransformation(
        base_quote_id="market-quote:" + "q" * 64,
        shocked_quote_id=(
            "market-quote:"
            + hashlib_char(scenario.scenario_id)
        ),
        quote_type=MarketQuoteType.EQUITY_SPOT.value,
        market_key="SEC:A",
        tenor=None,
        currency=Currency.USD.value,
        shock_kind=ShockKind.ABSOLUTE,
        shock_value=pnl,
        base_value=Decimal("100"),
        shocked_value=Decimal("100") + pnl,
    )
    draft = ScenarioRevaluationResult(
        revaluation_id="placeholder",
        scenario_id=scenario.scenario_id,
        scenario_market_state_id=(
            "scenario-market-state:"
            + hashlib_char(scenario.scenario_id)
        ),
        instrument_id=instrument().instrument_id,
        base_request_id="pricing-request:" + "r" * 64,
        shocked_request_id=(
            "pricing-request:"
            + hashlib_char("shocked:" + scenario.scenario_id)
        ),
        base_snapshot_id=BASE_SNAPSHOT,
        shocked_snapshot_id=(
            "market-data-snapshot:"
            + hashlib_char("snapshot:" + scenario.scenario_id)
        ),
        valuation_time=AT,
        base_pricing_result_id="pricing-result:" + "p" * 64,
        shocked_pricing_result_id=(
            "pricing-result:"
            + hashlib_char("result:" + scenario.scenario_id)
        ),
        base_curve_ids=(),
        shocked_curve_ids=(),
        reporting_currency="USD",
        base_npv=Decimal("100"),
        shocked_npv=Decimal("100") + pnl,
        scenario_pnl=pnl,
        transformations=(transform,),
        diagnostics=("historical fixture",),
        order_authority="NONE",
        capital_authority="NONE",
    )
    return replace(
        draft,
        revaluation_id=scenario_revaluation_identity(draft),
    )


def cube(pnls=None):
    pnls = pnls or tuple(
        Decimal(str(-index))
        for index in range(1, 21)
    )
    scens = scenarios(len(pnls))
    return PortfolioRiskCubeEngine().build(
        positions=(
            PortfolioRiskPosition(
                instrument_id=instrument().instrument_id,
                quantity=Decimal("1"),
                book_id="BOOK:RISK",
            ),
        ),
        scenarios=scens,
        revaluations=tuple(
            revaluation(scenario, pnl)
            for scenario, pnl in zip(scens, pnls)
        ),
    )


def observations(scens=None):
    scens = scens or scenarios()
    start = datetime(2026, 8, 1, tzinfo=UTC)
    return tuple(
        HistoricalScenarioObservation(
            scenario_id=scenario.scenario_id,
            period_start=start + timedelta(days=index),
            period_end=start + timedelta(days=index + 1),
            source_fact_ids=(f"history:{index}",),
            derivation_evidence_references=(
                f"historical-shock-derivation:{index}",
            ),
        )
        for index, scenario in enumerate(scens)
    )


def policy(**overrides):
    values = {
        "confidence_level": Decimal("0.95"),
        "minimum_observations": 20,
        "horizon_seconds": 86400,
        "window_start": datetime(2026, 7, 1, tzinfo=UTC),
        "window_end": datetime(2026, 9, 1, tzinfo=UTC),
        "weighting": HistoricalSimulationWeighting.EQUAL_WEIGHT,
        "quantile_convention": (
            HistoricalQuantileConvention.NEAREST_RANK
        ),
        "missing_data_policy": "FAIL_CLOSED",
        "rationale": "Frozen historical simulation fixture.",
        "evidence_references": ("risk-policy:historical",),
    }
    values.update(overrides)
    return HistoricalSimulationRiskPolicy(**values)


def hashlib_char(value):
    import hashlib
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class HistoricalSimulationRiskTests(unittest.TestCase):
    def test_nearest_rank_var_and_worst_tail_expected_shortfall(self):
        risk_cube = cube()
        estimate = HistoricalSimulationRiskEngine().estimate(
            cube=risk_cube,
            observations=observations(),
            policy=policy(),
        )
        self.assertEqual(
            risk_cube.state,
            PortfolioRiskCubeState.COMPLETE,
        )
        self.assertEqual(
            estimate.value_at_risk_loss,
            Decimal("19"),
        )
        self.assertEqual(
            estimate.expected_shortfall_loss,
            Decimal("20"),
        )
        self.assertEqual(estimate.tail_count, 1)
        self.assertEqual(estimate.worst_loss, Decimal("20"))
        self.assertEqual(estimate.best_loss, Decimal("1"))
        self.assertEqual(estimate.var_authority, "RESEARCH_ONLY")
        self.assertEqual(estimate.order_authority, "NONE")
        self.assertEqual(estimate.capital_authority, "NONE")

    def test_negative_var_is_preserved_when_empirical_quantile_is_gain(self):
        pnls = tuple(
            Decimal(str(index))
            for index in range(1, 21)
        )
        scens = scenarios()
        estimate = HistoricalSimulationRiskEngine().estimate(
            cube=cube(pnls),
            observations=observations(scens),
            policy=policy(),
        )
        self.assertLess(
            estimate.value_at_risk_loss,
            Decimal("0"),
        )
        self.assertLess(
            estimate.expected_shortfall_loss,
            Decimal("0"),
        )

    def test_incomplete_cube_fails_closed(self):
        complete = cube()
        incomplete = replace(
            complete,
            state=PortfolioRiskCubeState.INCOMPLETE,
        )
        with self.assertRaises(ValueError):
            HistoricalSimulationRiskEngine().estimate(
                cube=incomplete,
                observations=observations(),
                policy=policy(),
            )

    def test_missing_historical_scenario_fails_closed(self):
        with self.assertRaises(ValueError):
            HistoricalSimulationRiskEngine().estimate(
                cube=cube(),
                observations=observations()[:-1],
                policy=policy(),
            )

    def test_duplicate_observation_period_fails_closed(self):
        items = list(observations())
        items[1] = replace(
            items[1],
            period_start=items[0].period_start,
            period_end=items[0].period_end,
        )
        with self.assertRaises(ValueError):
            HistoricalSimulationRiskEngine().estimate(
                cube=cube(),
                observations=tuple(items),
                policy=policy(),
            )

    def test_wrong_horizon_fails_closed(self):
        items = list(observations())
        items[0] = replace(
            items[0],
            period_end=items[0].period_end
            + timedelta(hours=1),
        )
        with self.assertRaises(ValueError):
            HistoricalSimulationRiskEngine().estimate(
                cube=cube(),
                observations=tuple(items),
                policy=policy(),
            )

    def test_window_cannot_reach_valuation_time(self):
        with self.assertRaises(ValueError):
            HistoricalSimulationRiskEngine().estimate(
                cube=cube(),
                observations=observations(),
                policy=policy(window_end=AT),
            )

    def test_structural_minimum_rejects_tiny_var_sample(self):
        with self.assertRaises(ValueError):
            policy(minimum_observations=5)

    def test_store_is_idempotent(self):
        estimate = HistoricalSimulationRiskEngine().estimate(
            cube=cube(),
            observations=observations(),
            policy=policy(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = HistoricalSimulationRiskStore(
                Path(tmp) / "historical-risk.duckdb"
            )
            self.assertTrue(store.add(estimate))
            self.assertFalse(store.add(estimate))
            store.close()


if __name__ == "__main__":
    unittest.main()
