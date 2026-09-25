import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
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
    SimulationOrderState,
    TimeInForce,
    TopOfBookQuote,
)
from quantos.execution_reference import (
    FirstCurrentReferenceFillEngine,
    ReferenceExecutionResultStore,
)
from quantos.pricing_risk_contracts import Currency

UTC = timezone.utc
AT = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)


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


def book(
    *,
    seconds,
    sequence,
    bid="99.99",
    bid_qty="1000",
    ask="100.01",
    ask_qty="1000",
):
    event = AT + timedelta(seconds=seconds)
    return TopOfBookQuote(
        execution_instrument_id=instrument().execution_instrument_id,
        event_time=event,
        knowledge_time=event,
        sequence=sequence,
        bid_price=Decimal(bid),
        bid_quantity=Decimal(bid_qty),
        ask_price=Decimal(ask),
        ask_quantity=Decimal(ask_qty),
        source_fact_ids=(f"book:{sequence}",),
    )


def replay(events=None):
    return HistoricalReplayDatasetBuilder().build(
        start_time=AT,
        end_time=AT + timedelta(seconds=20),
        events=events
        or (
            book(seconds=1, sequence=1),
            book(seconds=5, sequence=2),
            book(seconds=10, sequence=3),
        ),
    )


def policy(**overrides):
    values = {
        "market_latency_ms": 5,
        "order_latency_ms": 10,
        "commission_bps": Decimal("0.5"),
        "slippage_bps": Decimal("1"),
        "market_impact_bps": Decimal("1"),
        "maximum_participation_rate": Decimal("0.20"),
        "allow_partial_fills": True,
        "rationale": "Stage 12.2 reference fill policy.",
        "evidence_references": ("execution-policy:evidence",),
    }
    values.update(overrides)
    return ExecutionSimulationPolicy(**values)


def run(data=None, simulation_policy=None):
    data = data or replay()
    simulation_policy = simulation_policy or policy()
    return ExecutionSimulationRunManifestBuilder().build(
        research_run_manifest_id="research-run-manifest:" + "r" * 64,
        portfolio_solution_id="portfolio-solution:" + "p" * 64,
        replay_dataset=data,
        simulation_policy=simulation_policy,
        engine_name="FIRST_CURRENT_REFERENCE",
        engine_version="12.2",
        code_revision="git:stage12.2",
        created_at=AT,
        evidence_references=("simulation-run:evidence",),
    )


def intent(
    *,
    data=None,
    simulation_policy=None,
    quantity="100",
    order_type=ExecutionOrderType.MARKET,
    tif=TimeInForce.DAY,
    limit_price=None,
    side=ExecutionSide.BUY,
    submitted_seconds=3,
):
    data = data or replay()
    simulation_policy = simulation_policy or policy()
    return SimulationOrderIntentBuilder().build(
        run=run(data, simulation_policy),
        instrument=instrument(),
        side=side,
        order_type=order_type,
        quantity=Decimal(quantity),
        time_in_force=tif,
        submitted_at=AT + timedelta(seconds=submitted_seconds),
        source_target_id="portfolio-target:SEC:A",
        limit_price=(
            Decimal(limit_price)
            if limit_price is not None
            else None
        ),
    )


def simulate(
    tmp,
    *,
    data=None,
    simulation_policy=None,
    order=None,
):
    data = data or replay()
    simulation_policy = simulation_policy or policy()
    simulation_run = run(data, simulation_policy)
    order = order or intent(
        data=data,
        simulation_policy=simulation_policy,
    )
    ledger = SimulationOrderLedger(
        Path(tmp) / "execution.duckdb"
    )
    result = FirstCurrentReferenceFillEngine().simulate(
        run=simulation_run,
        dataset=data,
        policy=simulation_policy,
        instrument=instrument(),
        intent=order,
        ledger=ledger,
    )
    return result, ledger


class ReferenceFillEngineTests(unittest.TestCase):
    def test_market_order_uses_current_known_book_after_order_latency(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, ledger = simulate(tmp)
            self.assertEqual(
                result.final_state,
                SimulationOrderState.FILLED,
            )
            self.assertEqual(
                result.filled_quantity,
                Decimal("100"),
            )
            self.assertEqual(
                result.volume_weighted_average_price,
                Decimal("100.03"),
            )
            self.assertEqual(
                result.total_fees,
                Decimal("0.50015"),
            )
            self.assertEqual(
                result.first_market_event_id,
                replay().events[0].event_id,
            )
            self.assertEqual(
                result.external_order_authority,
                "NONE",
            )
            self.assertEqual(result.capital_authority, "NONE")
            ledger.close()

    def test_participation_cap_creates_deterministic_partial_fills(self):
        data = replay(
            (
                book(
                    seconds=1,
                    sequence=1,
                    ask_qty="100",
                ),
                book(
                    seconds=5,
                    sequence=2,
                    ask_qty="100",
                ),
                book(
                    seconds=10,
                    sequence=3,
                    ask_qty="100",
                ),
            )
        )
        order = intent(
            data=data,
            quantity="50",
        )
        with tempfile.TemporaryDirectory() as tmp:
            result, ledger = simulate(
                tmp,
                data=data,
                order=order,
            )
            self.assertEqual(
                result.filled_quantity,
                Decimal("50"),
            )
            self.assertEqual(len(result.fill_ids), 3)
            fills = ledger.fills(order.intent_id)
            self.assertEqual(
                tuple(item.quantity for item in fills),
                (
                    Decimal("20"),
                    Decimal("20"),
                    Decimal("10"),
                ),
            )
            ledger.close()

    def test_ioc_partial_fill_expires_remainder_after_one_book(self):
        data = replay(
            (
                book(
                    seconds=1,
                    sequence=1,
                    ask_qty="100",
                ),
                book(
                    seconds=5,
                    sequence=2,
                    ask_qty="1000",
                ),
            )
        )
        order = intent(
            data=data,
            quantity="50",
            tif=TimeInForce.IOC,
        )
        with tempfile.TemporaryDirectory() as tmp:
            result, ledger = simulate(
                tmp,
                data=data,
                order=order,
            )
            self.assertEqual(
                result.final_state,
                SimulationOrderState.EXPIRED,
            )
            self.assertEqual(
                result.filled_quantity,
                Decimal("20"),
            )
            self.assertEqual(
                result.remaining_quantity,
                Decimal("30"),
            )
            ledger.close()

    def test_fok_does_not_create_partial_fill_when_capacity_is_insufficient(self):
        data = replay(
            (
                book(
                    seconds=1,
                    sequence=1,
                    ask_qty="100",
                ),
            )
        )
        order = intent(
            data=data,
            quantity="50",
            tif=TimeInForce.FOK,
        )
        with tempfile.TemporaryDirectory() as tmp:
            result, ledger = simulate(
                tmp,
                data=data,
                order=order,
            )
            self.assertEqual(
                result.final_state,
                SimulationOrderState.EXPIRED,
            )
            self.assertEqual(
                result.filled_quantity,
                Decimal("0"),
            )
            self.assertEqual(ledger.fills(order.intent_id), ())
            ledger.close()

    def test_limit_order_never_executes_worse_than_limit_after_cost_adjustment(self):
        data = replay(
            (
                book(
                    seconds=1,
                    sequence=1,
                    ask="100.01",
                    ask_qty="1000",
                ),
            )
        )
        order = intent(
            data=data,
            quantity="100",
            order_type=ExecutionOrderType.LIMIT,
            tif=TimeInForce.DAY,
            limit_price="100.02",
        )
        with tempfile.TemporaryDirectory() as tmp:
            result, ledger = simulate(
                tmp,
                data=data,
                order=order,
            )
            self.assertEqual(
                result.final_state,
                SimulationOrderState.EXPIRED,
            )
            self.assertEqual(
                result.filled_quantity,
                Decimal("0"),
            )
            ledger.close()

    def test_sell_execution_rounds_adversely_to_tick(self):
        data = replay(
            (
                book(
                    seconds=1,
                    sequence=1,
                    bid="100.00",
                    bid_qty="1000",
                    ask="100.02",
                ),
            )
        )
        order = intent(
            data=data,
            quantity="100",
            side=ExecutionSide.SELL,
        )
        with tempfile.TemporaryDirectory() as tmp:
            result, ledger = simulate(
                tmp,
                data=data,
                order=order,
            )
            self.assertEqual(
                result.volume_weighted_average_price,
                Decimal("99.98"),
            )
            self.assertGreater(
                result.average_adverse_slippage_bps,
                Decimal("0"),
            )
            ledger.close()

    def test_market_data_arriving_after_order_activation_is_not_used_early(self):
        data = replay(
            (
                book(
                    seconds=5,
                    sequence=1,
                    ask_qty="1000",
                ),
            )
        )
        order = intent(
            data=data,
            submitted_seconds=3,
        )
        with tempfile.TemporaryDirectory() as tmp:
            result, ledger = simulate(
                tmp,
                data=data,
                order=order,
            )
            fill = ledger.fills(order.intent_id)[0]
            self.assertGreaterEqual(
                fill.fill_time,
                data.events[0].knowledge_time
                + timedelta(milliseconds=policy().market_latency_ms),
            )
            ledger.close()

    def test_market_data_arriving_after_replay_end_cannot_fill(self):
        p = policy(market_latency_ms=2000)
        data = replay(
            (
                book(
                    seconds=19,
                    sequence=1,
                    ask_qty="1000",
                ),
            )
        )
        order = intent(
            data=data,
            simulation_policy=p,
            submitted_seconds=18,
        )
        with tempfile.TemporaryDirectory() as tmp:
            result, ledger = simulate(
                tmp,
                data=data,
                simulation_policy=p,
                order=order,
            )
            self.assertEqual(
                result.final_state,
                SimulationOrderState.EXPIRED,
            )
            self.assertEqual(
                result.filled_quantity,
                Decimal("0"),
            )
            self.assertEqual(ledger.fills(order.intent_id), ())
            ledger.close()

    def test_run_policy_mismatch_fails_closed(self):
        data = replay()
        run_policy = policy()
        simulation_run = run(data, run_policy)
        different_policy = policy(slippage_bps=Decimal("2"))
        order = intent(
            data=data,
            simulation_policy=run_policy,
        )
        with tempfile.TemporaryDirectory() as tmp:
            ledger = SimulationOrderLedger(
                Path(tmp) / "execution.duckdb"
            )
            with self.assertRaises(ValueError):
                FirstCurrentReferenceFillEngine().simulate(
                    run=simulation_run,
                    dataset=data,
                    policy=different_policy,
                    instrument=instrument(),
                    intent=order,
                    ledger=ledger,
                )
            ledger.close()

    def test_result_is_deterministic_and_store_is_idempotent(self):
        data = replay()
        p = policy()
        order = intent(
            data=data,
            simulation_policy=p,
        )
        with tempfile.TemporaryDirectory() as left_tmp, tempfile.TemporaryDirectory() as right_tmp:
            left, left_ledger = simulate(
                left_tmp,
                data=data,
                simulation_policy=p,
                order=order,
            )
            right, right_ledger = simulate(
                right_tmp,
                data=data,
                simulation_policy=p,
                order=order,
            )
            self.assertEqual(left.result_id, right.result_id)
            store = ReferenceExecutionResultStore(
                Path(left_tmp) / "results.duckdb"
            )
            self.assertTrue(store.add(left))
            self.assertFalse(store.add(left))
            store.close()
            left_ledger.close()
            right_ledger.close()


if __name__ == "__main__":
    unittest.main()