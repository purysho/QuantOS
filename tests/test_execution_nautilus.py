import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from importlib.metadata import version as package_version
from pathlib import Path

from quantos.execution_contracts import (
    ExecutionAssetClass,
    ExecutionInstrument,
    ExecutionOrderType,
    ExecutionSide,
    ExecutionSimulationPolicy,
    ExecutionSimulationRunManifestBuilder,
    HistoricalReplayDatasetBuilder,
    SimulationOrderIntentBuilder,
    SimulationOrderLedger,
    TimeInForce,
    TopOfBookQuote,
)
from quantos.execution_nautilus import (
    DifferentialState,
    NautilusDifferentialEngine,
    NautilusDifferentialStore,
    NautilusHistoricalBacktestAdapter,
    nautilus_is_available,
)
from quantos.execution_reference import FirstCurrentReferenceFillEngine
from quantos.pricing_risk_contracts import Currency

UTC = timezone.utc
AT = datetime(2026, 9, 25, 12, tzinfo=UTC)


def instrument():
    return ExecutionInstrument(
        canonical_instrument_id="SEC:A",
        venue_id="XTEST",
        venue_symbol="A",
        asset_class=ExecutionAssetClass.EQUITY,
        quote_currency=Currency.USD,
        price_increment=Decimal("0.01"),
        quantity_increment=Decimal("1"),
        minimum_quantity=Decimal("1"),
        contract_multiplier=Decimal("1"),
    )


def quote(side_size="1000"):
    return TopOfBookQuote(
        execution_instrument_id=instrument().execution_instrument_id,
        event_time=AT + timedelta(seconds=1),
        knowledge_time=AT + timedelta(seconds=1),
        sequence=1,
        bid_price=Decimal("99.99"),
        bid_quantity=Decimal(side_size),
        ask_price=Decimal("100.01"),
        ask_quantity=Decimal(side_size),
        source_fact_ids=("quote:1",),
    )


def dataset():
    return HistoricalReplayDatasetBuilder().build(
        start_time=AT,
        end_time=AT + timedelta(seconds=10),
        events=(quote(),),
    )


def policy(**overrides):
    values = {
        "market_latency_ms": 0,
        "order_latency_ms": 0,
        "commission_bps": Decimal("0"),
        "slippage_bps": Decimal("0"),
        "market_impact_bps": Decimal("0"),
        "maximum_participation_rate": Decimal("1"),
        "allow_partial_fills": False,
        "rationale": "Exact Stage 12.3 differential fixture.",
        "evidence_references": ("execution-policy:differential",),
    }
    values.update(overrides)
    return ExecutionSimulationPolicy(**values)


def run(engine_name, engine_version, data=None, p=None):
    data = data or dataset()
    p = p or policy()
    return ExecutionSimulationRunManifestBuilder().build(
        research_run_manifest_id="research-run-manifest:" + "r" * 64,
        portfolio_solution_id="portfolio-solution:" + "p" * 64,
        replay_dataset=data,
        simulation_policy=p,
        engine_name=engine_name,
        engine_version=engine_version,
        code_revision="git:stage12.3",
        created_at=AT,
        evidence_references=("simulation-run:evidence",),
    )


def intent(simulation_run, *, side=ExecutionSide.BUY, order_type=ExecutionOrderType.MARKET, limit_price=None):
    return SimulationOrderIntentBuilder().build(
        run=simulation_run,
        instrument=instrument(),
        side=side,
        order_type=order_type,
        quantity=Decimal("100"),
        time_in_force=TimeInForce.GTC,
        submitted_at=quote().knowledge_time,
        source_target_id="portfolio-target:SEC:A",
        limit_price=(
            Decimal(limit_price)
            if limit_price is not None
            else None
        ),
    )


@unittest.skipUnless(
    nautilus_is_available(),
    "NautilusTrader v2 requires Python >= 3.12",
)
class NautilusHistoricalAdapterRuntimeTests(unittest.TestCase):
    def compare_fixture(
        self,
        *,
        side=ExecutionSide.BUY,
        order_type=ExecutionOrderType.MARKET,
        limit_price=None,
    ):
        data = dataset()
        p = policy()
        reference_run = run(
            "FIRST_CURRENT_REFERENCE",
            "12.2",
            data,
            p,
        )
        nautilus_version = package_version("nautilus_trader")
        nautilus_run = run(
            "NAUTILUS_TRADER",
            nautilus_version,
            data,
            p,
        )
        reference_intent = intent(
            reference_run,
            side=side,
            order_type=order_type,
            limit_price=limit_price,
        )
        nautilus_intent = intent(
            nautilus_run,
            side=side,
            order_type=order_type,
            limit_price=limit_price,
        )
        with tempfile.TemporaryDirectory() as tmp:
            reference_ledger = SimulationOrderLedger(
                Path(tmp) / "reference.duckdb"
            )
            reference_result = FirstCurrentReferenceFillEngine().simulate(
                run=reference_run,
                dataset=data,
                policy=p,
                instrument=instrument(),
                intent=reference_intent,
                ledger=reference_ledger,
            )
            nautilus_result = NautilusHistoricalBacktestAdapter().simulate(
                run=nautilus_run,
                dataset=data,
                policy=p,
                instrument=instrument(),
                intent=nautilus_intent,
            )
            differential = NautilusDifferentialEngine().compare(
                reference_run=reference_run,
                reference_intent=reference_intent,
                reference_result=reference_result,
                nautilus_run=nautilus_run,
                nautilus_intent=nautilus_intent,
                nautilus_result=nautilus_result,
            )
            reference_ledger.close()
            return nautilus_result, differential

    def test_market_buy_matches_reference_exactly(self):
        result, diff = self.compare_fixture()
        self.assertEqual(diff.state, DifferentialState.MATCH)
        self.assertEqual(result.filled_quantity, Decimal("100"))
        self.assertEqual(
            result.volume_weighted_average_price,
            Decimal("100.01"),
        )
        self.assertEqual(
            result.engine_version,
            package_version("nautilus_trader"),
        )
        self.assertEqual(result.network_authority, "NONE")
        self.assertEqual(result.external_order_authority, "NONE")
        self.assertEqual(result.capital_authority, "NONE")
        self.assertEqual(
            diff.trust_authority,
            "REFERENCE_MATCH_ONLY",
        )

    def test_marketable_limit_buy_matches_reference_exactly(self):
        _, diff = self.compare_fixture(
            order_type=ExecutionOrderType.LIMIT,
            limit_price="100.01",
        )
        self.assertEqual(diff.state, DifferentialState.MATCH)

    def test_market_sell_matches_reference_exactly(self):
        result, diff = self.compare_fixture(
            side=ExecutionSide.SELL,
        )
        self.assertEqual(diff.state, DifferentialState.MATCH)
        self.assertEqual(
            result.volume_weighted_average_price,
            Decimal("99.99"),
        )

    def test_differential_store_is_idempotent(self):
        _, diff = self.compare_fixture()
        with tempfile.TemporaryDirectory() as tmp:
            store = NautilusDifferentialStore(
                Path(tmp) / "nautilus-differential.duckdb"
            )
            self.assertTrue(store.add(diff))
            self.assertFalse(store.add(diff))
            store.close()


class NautilusHistoricalAdapterScopeTests(unittest.TestCase):
    def test_python_311_reports_nautilus_unavailable(self):
        if sys.version_info < (3, 12):
            self.assertFalse(nautilus_is_available())

    @unittest.skipUnless(
        nautilus_is_available(),
        "NautilusTrader v2 requires Python >= 3.12",
    )
    def test_nonzero_execution_cost_policy_fails_closed(self):
        data = dataset()
        p = policy(slippage_bps=Decimal("1"))
        adapter = NautilusHistoricalBacktestAdapter()
        r = run(
            "NAUTILUS_TRADER",
            adapter.engine_version,
            data,
            p,
        )
        with self.assertRaises(ValueError):
            adapter.simulate(
                run=r,
                dataset=data,
                policy=p,
                instrument=instrument(),
                intent=intent(r),
            )

    @unittest.skipUnless(
        nautilus_is_available(),
        "NautilusTrader v2 requires Python >= 3.12",
    )
    def test_nonzero_latency_policy_fails_closed(self):
        data = dataset()
        p = policy(order_latency_ms=1)
        adapter = NautilusHistoricalBacktestAdapter()
        r = run(
            "NAUTILUS_TRADER",
            adapter.engine_version,
            data,
            p,
        )
        with self.assertRaises(ValueError):
            adapter.simulate(
                run=r,
                dataset=data,
                policy=p,
                instrument=instrument(),
                intent=intent(r),
            )

    @unittest.skipUnless(
        nautilus_is_available(),
        "NautilusTrader v2 requires Python >= 3.12",
    )
    def test_fractional_equity_scope_fails_closed(self):
        data = dataset()
        p = policy()
        adapter = NautilusHistoricalBacktestAdapter()
        r = run(
            "NAUTILUS_TRADER",
            adapter.engine_version,
            data,
            p,
        )
        fractional = ExecutionInstrument(
            canonical_instrument_id="SEC:A",
            venue_id="XTEST",
            venue_symbol="A",
            asset_class=ExecutionAssetClass.EQUITY,
            quote_currency=Currency.USD,
            price_increment=Decimal("0.01"),
            quantity_increment=Decimal("0.1"),
            minimum_quantity=Decimal("0.1"),
            contract_multiplier=Decimal("1"),
        )
        with self.assertRaises(ValueError):
            adapter.simulate(
                run=r,
                dataset=data,
                policy=p,
                instrument=fractional,
                intent=intent(r),
            )

    @unittest.skipUnless(
        nautilus_is_available(),
        "NautilusTrader v2 requires Python >= 3.12",
    )
    def test_partial_liquidity_fixture_is_rejected_before_nautilus(self):
        data = HistoricalReplayDatasetBuilder().build(
            start_time=AT,
            end_time=AT + timedelta(seconds=10),
            events=(quote(side_size="50"),),
        )
        p = policy()
        adapter = NautilusHistoricalBacktestAdapter()
        r = run(
            "NAUTILUS_TRADER",
            adapter.engine_version,
            data,
            p,
        )
        order = SimulationOrderIntentBuilder().build(
            run=r,
            instrument=instrument(),
            side=ExecutionSide.BUY,
            order_type=ExecutionOrderType.MARKET,
            quantity=Decimal("100"),
            time_in_force=TimeInForce.GTC,
            submitted_at=data.events[0].knowledge_time,
            source_target_id="portfolio-target:SEC:A",
        )
        with self.assertRaises(ValueError):
            adapter.simulate(
                run=r,
                dataset=data,
                policy=p,
                instrument=instrument(),
                intent=order,
            )


if __name__ == "__main__":
    unittest.main()
