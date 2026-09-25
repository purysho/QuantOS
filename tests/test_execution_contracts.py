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
    FillLiquidity,
    HistoricalReplayDatasetBuilder,
    SimulatedFillBuilder,
    SimulationMode,
    SimulationOrderIntentBuilder,
    SimulationOrderLedger,
    SimulationOrderState,
    TimeInForce,
    TopOfBookQuote,
    TradePrint,
    historical_replay_dataset_identity,
)
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


def quote(sequence=1, known_offset_ms=0):
    event = AT + timedelta(seconds=sequence)
    return TopOfBookQuote(
        execution_instrument_id=instrument().execution_instrument_id,
        event_time=event,
        knowledge_time=event + timedelta(milliseconds=known_offset_ms),
        sequence=sequence,
        bid_price=Decimal("99.99"),
        bid_quantity=Decimal("500"),
        ask_price=Decimal("100.01"),
        ask_quantity=Decimal("400"),
        source_fact_ids=(f"quote:{sequence}",),
    )


def trade(sequence=2):
    event = AT + timedelta(seconds=sequence)
    return TradePrint(
        execution_instrument_id=instrument().execution_instrument_id,
        event_time=event,
        knowledge_time=event,
        sequence=sequence,
        price=Decimal("100.00"),
        quantity=Decimal("200"),
        source_fact_ids=(f"trade:{sequence}",),
    )


def dataset():
    return HistoricalReplayDatasetBuilder().build(
        start_time=AT,
        end_time=AT + timedelta(minutes=1),
        events=(trade(), quote()),
    )


def policy():
    return ExecutionSimulationPolicy(
        market_latency_ms=5,
        order_latency_ms=10,
        commission_bps=Decimal("0.5"),
        slippage_bps=Decimal("1"),
        market_impact_bps=Decimal("1"),
        maximum_participation_rate=Decimal("0.10"),
        allow_partial_fills=True,
        rationale="Stage 12.1 deterministic historical replay.",
        evidence_references=("execution-policy:evidence",),
    )


def run():
    return ExecutionSimulationRunManifestBuilder().build(
        research_run_manifest_id="research-run-manifest:" + "r" * 64,
        portfolio_solution_id="portfolio-solution:" + "p" * 64,
        replay_dataset=dataset(),
        simulation_policy=policy(),
        engine_name="QUANTOS_REFERENCE",
        engine_version="12.1",
        code_revision="git:stage12.1",
        created_at=AT,
        evidence_references=("simulation-run:evidence",),
    )


def market_order(quantity=Decimal("100")):
    return SimulationOrderIntentBuilder().build(
        run=run(),
        instrument=instrument(),
        side=ExecutionSide.BUY,
        order_type=ExecutionOrderType.MARKET,
        quantity=quantity,
        time_in_force=TimeInForce.DAY,
        submitted_at=AT + timedelta(seconds=3),
        source_target_id="portfolio-target:SEC:A",
    )


class ExecutionContractTests(unittest.TestCase):
    def test_replay_dataset_is_canonical_and_content_addressed(self):
        replay = dataset()
        self.assertEqual(
            replay.events[0].sequence,
            1,
        )
        self.assertEqual(
            replay.dataset_id,
            historical_replay_dataset_identity(replay),
        )
        self.assertEqual(
            replay.execution_instrument_ids,
            (instrument().execution_instrument_id,),
        )

    def test_market_event_cannot_be_known_before_it_occurs(self):
        event = AT + timedelta(seconds=1)
        with self.assertRaises(ValueError):
            TopOfBookQuote(
                execution_instrument_id=instrument().execution_instrument_id,
                event_time=event,
                knowledge_time=event - timedelta(microseconds=1),
                sequence=1,
                bid_price=Decimal("99"),
                bid_quantity=Decimal("1"),
                ask_price=Decimal("100"),
                ask_quantity=Decimal("1"),
                source_fact_ids=("quote:bad",),
            )

    def test_crossed_or_locked_book_fails_closed(self):
        with self.assertRaises(ValueError):
            replace(
                quote(),
                bid_price=Decimal("100.01"),
            )

    def test_nested_market_event_tampering_breaks_dataset_identity(self):
        replay = dataset()
        tampered = replace(
            replay,
            events=(
                replace(
                    replay.events[0],
                    bid_price=Decimal("98.00"),
                ),
                *replay.events[1:],
            ),
        )
        with self.assertRaises(ValueError):
            historical_replay_dataset_identity(tampered)

    def test_run_manifest_has_no_network_order_or_capital_authority(self):
        item = run()
        self.assertEqual(item.mode, SimulationMode.HISTORICAL_REPLAY)
        self.assertEqual(item.network_authority, "NONE")
        self.assertEqual(item.external_order_authority, "NONE")
        self.assertEqual(item.capital_authority, "NONE")

    def test_limit_order_requires_tick_alignment(self):
        with self.assertRaises(ValueError):
            SimulationOrderIntentBuilder().build(
                run=run(),
                instrument=instrument(),
                side=ExecutionSide.BUY,
                order_type=ExecutionOrderType.LIMIT,
                quantity=Decimal("100"),
                time_in_force=TimeInForce.DAY,
                submitted_at=AT + timedelta(seconds=3),
                source_target_id="portfolio-target:SEC:A",
                limit_price=Decimal("100.005"),
            )

    def test_fill_cannot_use_market_data_before_known_time(self):
        delayed_quote = quote(
            sequence=4,
            known_offset_ms=100,
        )
        intent = market_order()
        with self.assertRaises(ValueError):
            SimulatedFillBuilder().build(
                run=run(),
                intent=intent,
                market_event=delayed_quote,
                fill_time=delayed_quote.event_time,
                quantity=Decimal("100"),
                price=Decimal("100.01"),
                fee=Decimal("0.05"),
                liquidity=FillLiquidity.TAKER,
                prior_filled_quantity=Decimal("0"),
            )

    def test_order_state_machine_accepts_partial_then_fill(self):
        intent = market_order()
        with tempfile.TemporaryDirectory() as tmp:
            ledger = SimulationOrderLedger(
                Path(tmp) / "execution.duckdb"
            )
            self.assertTrue(ledger.add_intent(intent))
            self.assertFalse(ledger.add_intent(intent))
            accepted_at = intent.submitted_at + timedelta(milliseconds=10)
            accepted = ledger.transition(
                intent=intent,
                new_state=SimulationOrderState.ACCEPTED,
                occurred_at=accepted_at,
                reason="historical replay accepted order",
            )
            self.assertEqual(
                accepted.prior_state,
                SimulationOrderState.CREATED,
            )

            event = quote(sequence=5)
            first = SimulatedFillBuilder().build(
                run=run(),
                intent=intent,
                market_event=event,
                fill_time=event.knowledge_time,
                quantity=Decimal("40"),
                price=Decimal("100.01"),
                fee=Decimal("0.02"),
                liquidity=FillLiquidity.TAKER,
                prior_filled_quantity=Decimal("0"),
            )
            ledger.transition(
                intent=intent,
                new_state=SimulationOrderState.PARTIALLY_FILLED,
                occurred_at=first.fill_time,
                reason="partial historical fill",
                fill=first,
            )
            self.assertEqual(
                ledger.state(intent.intent_id),
                SimulationOrderState.PARTIALLY_FILLED,
            )

            later = quote(sequence=6)
            second = SimulatedFillBuilder().build(
                run=run(),
                intent=intent,
                market_event=later,
                fill_time=later.knowledge_time,
                quantity=Decimal("60"),
                price=Decimal("100.02"),
                fee=Decimal("0.03"),
                liquidity=FillLiquidity.TAKER,
                prior_filled_quantity=Decimal("40"),
            )
            ledger.transition(
                intent=intent,
                new_state=SimulationOrderState.FILLED,
                occurred_at=second.fill_time,
                reason="remaining historical fill",
                fill=second,
            )
            self.assertEqual(
                ledger.state(intent.intent_id),
                SimulationOrderState.FILLED,
            )
            self.assertEqual(len(ledger.fills(intent.intent_id)), 2)
            ledger.close()

    def test_terminal_order_state_cannot_transition_again(self):
        intent = market_order()
        with tempfile.TemporaryDirectory() as tmp:
            ledger = SimulationOrderLedger(
                Path(tmp) / "execution.duckdb"
            )
            ledger.add_intent(intent)
            ledger.transition(
                intent=intent,
                new_state=SimulationOrderState.REJECTED,
                occurred_at=intent.submitted_at,
                reason="simulation rejected fixture",
            )
            with self.assertRaises(ValueError):
                ledger.transition(
                    intent=intent,
                    new_state=SimulationOrderState.ACCEPTED,
                    occurred_at=intent.submitted_at + timedelta(seconds=1),
                    reason="should fail",
                )
            ledger.close()

    def test_fill_cannot_exceed_original_order_quantity(self):
        intent = market_order()
        with self.assertRaises(ValueError):
            SimulatedFillBuilder().build(
                run=run(),
                intent=intent,
                market_event=quote(sequence=5),
                fill_time=quote(sequence=5).knowledge_time,
                quantity=Decimal("101"),
                price=Decimal("100.01"),
                fee=Decimal("0.05"),
                liquidity=FillLiquidity.TAKER,
                prior_filled_quantity=Decimal("0"),
            )


if __name__ == "__main__":
    unittest.main()
