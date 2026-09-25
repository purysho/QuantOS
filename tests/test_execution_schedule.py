import tempfile
import unittest
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
    TimeInForce,
    TopOfBookQuote,
)
from quantos.execution_schedule import (
    ExecutionScheduleBuilder,
    ExecutionScheduleEngine,
    ExecutionSchedulePolicy,
    ExecutionScheduleResultStore,
    ExecutionScheduleState,
    ExecutionScheduleTarget,
    execution_schedule_result_identity,
)
from quantos.pricing_risk_contracts import Currency

UTC = timezone.utc
AT = datetime(2026, 9, 25, 12, tzinfo=UTC)
T1 = AT + timedelta(seconds=1)
T2 = AT + timedelta(seconds=2)


def instrument(symbol, currency=Currency.USD):
    return ExecutionInstrument(
        canonical_instrument_id=f"SEC:{symbol}",
        venue_id="XTEST",
        venue_symbol=symbol,
        asset_class=ExecutionAssetClass.EQUITY,
        quote_currency=currency,
        price_increment=Decimal("0.01"),
        quantity_increment=Decimal("1"),
        minimum_quantity=Decimal("1"),
        contract_multiplier=Decimal("1"),
    )


A = instrument("A")
B = instrument("B")


def quote(inst, at, sequence, bid, ask, size="1000"):
    return TopOfBookQuote(
        execution_instrument_id=inst.execution_instrument_id,
        event_time=at,
        knowledge_time=at,
        sequence=sequence,
        bid_price=Decimal(bid),
        bid_quantity=Decimal(size),
        ask_price=Decimal(ask),
        ask_quantity=Decimal(size),
        source_fact_ids=(f"quote:{inst.venue_symbol}:{sequence}",),
    )


def dataset(a_size="1000"):
    return HistoricalReplayDatasetBuilder().build(
        start_time=AT,
        end_time=AT + timedelta(seconds=10),
        events=(
            quote(A, T1, 1, "99.99", "100.01", a_size),
            quote(A, T2, 2, "100.00", "100.02", a_size),
            quote(B, T1, 1, "49.99", "50.01"),
        ),
    )


def sim_policy(**overrides):
    values = {
        "market_latency_ms": 0,
        "order_latency_ms": 0,
        "commission_bps": Decimal("1"),
        "slippage_bps": Decimal("0"),
        "market_impact_bps": Decimal("0"),
        "maximum_participation_rate": Decimal("1"),
        "allow_partial_fills": True,
        "rationale": "Schedule fixture.",
        "evidence_references": ("execution-policy:schedule",),
    }
    values.update(overrides)
    return ExecutionSimulationPolicy(**values)


def schedule_policy(cash="100000", short=False, negative_cash=False, currency=Currency.USD):
    return ExecutionSchedulePolicy(
        cash_currency=currency,
        starting_cash=Decimal(cash),
        allow_short_positions=short,
        allow_negative_cash=negative_cash,
        rationale="Fully funded long-only schedule.",
        evidence_references=("schedule-policy:evidence",),
    )


def run(data, p):
    return ExecutionSimulationRunManifestBuilder().build(
        research_run_manifest_id="research-run-manifest:" + "r" * 64,
        portfolio_solution_id="portfolio-solution:" + "p" * 64,
        replay_dataset=data,
        simulation_policy=p,
        engine_name="QUANTOS_REFERENCE",
        engine_version="12.2",
        code_revision="git:stage12.5",
        created_at=AT,
        evidence_references=("simulation-run:evidence",),
    )


def order(r, inst, side, qty, target, at=T1, tif=TimeInForce.GTC):
    return SimulationOrderIntentBuilder().build(
        run=r,
        instrument=inst,
        side=side,
        order_type=ExecutionOrderType.MARKET,
        quantity=Decimal(qty),
        time_in_force=tif,
        submitted_at=at,
        source_target_id=target,
    )


TARGETS = (
    ExecutionScheduleTarget("target:A", A.execution_instrument_id, Decimal("0"), Decimal("100")),
    ExecutionScheduleTarget("target:B", B.execution_instrument_id, Decimal("50"), Decimal("0")),
)


class ExecutionScheduleTests(unittest.TestCase):
    def execute(self, data, intents_fn, sp=None, p=None, targets=TARGETS):
        p = p or sim_policy()
        sp = sp or schedule_policy()
        r = run(data, p)
        intents = intents_fn(r)
        schedule = ExecutionScheduleBuilder().build(
            run=r, policy=sp, instruments=(A, B), targets=targets, intents=intents
        )
        with tempfile.TemporaryDirectory() as tmp:
            ledger = SimulationOrderLedger(Path(tmp) / "ledger.duckdb")
            try:
                return ExecutionScheduleEngine().execute(
                    run=r,
                    dataset=data,
                    simulation_policy=p,
                    schedule_policy=sp,
                    schedule=schedule,
                    instruments=(A, B),
                    intents=intents,
                    ledger=ledger,
                )
            finally:
                ledger.close()

    def standard(self, r):
        return (
            order(r, A, ExecutionSide.BUY, "100", "target:A"),
            order(r, B, ExecutionSide.SELL, "50", "target:B"),
        )

    def test_complete_schedule_reconciles_cash_and_inventory(self):
        result = self.execute(dataset(), self.standard)
        self.assertEqual(result.state, ExecutionScheduleState.COMPLETE)
        bought = Decimal("100") * Decimal("100.01")
        sold = Decimal("50") * Decimal("49.99")
        fees = (bought + sold) / Decimal("10000")
        self.assertEqual(result.total_notional_bought, bought)
        self.assertEqual(result.total_notional_sold, sold)
        self.assertEqual(result.total_fees, fees)
        self.assertEqual(result.final_cash, Decimal("100000") - bought + sold - fees)
        positions = {o.source_target_id: o.final_position for o in result.target_outcomes}
        self.assertEqual(positions, {"target:A": Decimal("100"), "target:B": Decimal("0")})
        self.assertEqual(result.result_id, execution_schedule_result_identity(result))
        self.assertEqual(result.capital_authority, "NONE")
        self.assertEqual(result.external_order_authority, "NONE")

    def test_short_book_ioc_leaves_target_incomplete(self):
        def intents(r):
            return (
                order(r, A, ExecutionSide.BUY, "100", "target:A", tif=TimeInForce.IOC),
                order(r, B, ExecutionSide.SELL, "50", "target:B"),
            )

        result = self.execute(dataset(a_size="60"), intents)
        self.assertEqual(result.state, ExecutionScheduleState.INCOMPLETE)
        outcome = {o.source_target_id: o for o in result.target_outcomes}["target:A"]
        self.assertEqual(outcome.filled_quantity, Decimal("60"))
        self.assertEqual(outcome.unfilled_quantity, Decimal("40"))

    def test_sequential_child_orders_on_one_instrument_are_allowed(self):
        def intents(r):
            return (
                order(r, A, ExecutionSide.BUY, "60", "target:A", at=T1, tif=TimeInForce.IOC),
                order(r, A, ExecutionSide.BUY, "40", "target:A", at=T2, tif=TimeInForce.IOC),
                order(r, B, ExecutionSide.SELL, "50", "target:B"),
            )

        result = self.execute(dataset(), intents)
        self.assertEqual(result.state, ExecutionScheduleState.COMPLETE)
        self.assertEqual(len(result.order_result_ids), 3)

    def test_concurrent_orders_on_one_instrument_fail_closed(self):
        def intents(r):
            return (
                order(r, A, ExecutionSide.BUY, "60", "target:A", at=T1),
                order(r, A, ExecutionSide.BUY, "40", "target:A", at=T1 + timedelta(milliseconds=1)),
                order(r, B, ExecutionSide.SELL, "50", "target:B"),
            )

        with self.assertRaisesRegex(ValueError, "share top-of-book"):
            self.execute(dataset(a_size="50"), intents)

    def test_cash_breach_is_preserved_not_hidden(self):
        result = self.execute(dataset(), self.standard, sp=schedule_policy(cash="5000"))
        self.assertEqual(result.state, ExecutionScheduleState.CONSTRAINT_BREACH)
        self.assertTrue(result.breaches)
        self.assertLess(result.minimum_cash, 0)

    def test_negative_cash_allowed_by_policy_is_not_a_breach(self):
        result = self.execute(
            dataset(),
            self.standard,
            sp=schedule_policy(cash="5000", negative_cash=True),
        )
        self.assertEqual(result.state, ExecutionScheduleState.COMPLETE)

    def test_intents_must_sum_exactly_to_target(self):
        def intents(r):
            return (
                order(r, A, ExecutionSide.BUY, "90", "target:A"),
                order(r, B, ExecutionSide.SELL, "50", "target:B"),
            )

        with self.assertRaisesRegex(ValueError, "sum exactly"):
            self.execute(dataset(), intents)

    def test_intent_moving_away_from_target_fails_closed(self):
        def intents(r):
            return (
                order(r, A, ExecutionSide.SELL, "100", "target:A"),
                order(r, B, ExecutionSide.SELL, "50", "target:B"),
            )

        with self.assertRaisesRegex(ValueError, "away from its target"):
            self.execute(dataset(), intents)

    def test_currency_mismatch_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "implicit FX"):
            self.execute(dataset(), self.standard, sp=schedule_policy(currency=Currency.EUR))

    def test_short_targets_require_policy(self):
        targets = (
            TARGETS[0],
            ExecutionScheduleTarget("target:B", B.execution_instrument_id, Decimal("0"), Decimal("-50")),
        )
        with self.assertRaisesRegex(ValueError, "short target"):
            self.execute(dataset(), self.standard, targets=targets)

    def test_store_is_idempotent(self):
        result = self.execute(dataset(), self.standard)
        with tempfile.TemporaryDirectory() as tmp:
            store = ExecutionScheduleResultStore(Path(tmp) / "schedule.duckdb")
            self.assertTrue(store.add(result))
            self.assertFalse(store.add(result))
            store.close()


if __name__ == "__main__":
    unittest.main()
