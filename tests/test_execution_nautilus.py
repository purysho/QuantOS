import os
import sys
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
from quantos.execution_contracts import (
    CommissionRounding,
    ImmediatePartialFills,
    LiquidityRefresh,
    MarketOrderResidual,
    OrderActivationMode,
    RestingLimitFillPrice,
)
from quantos.execution_nautilus import (
    DETERMINISTIC_FEES_CONTRACT,
    IOC_ALWAYS_PARTIAL_CONTRACT,
    MARKET_L1_SWEEP_CONTRACT,
    NEXT_ARRIVAL_LATENCY_CONTRACT,
    RESTING_LIMIT_ACCUMULATION_CONTRACT,
    ROUNDED_FEES_CONTRACT,
    IMMEDIATE_TIME_IN_FORCE_CONTRACT,
    LIMIT_TRANSITION_CONTRACT,
    MARKET_DATA_LATENCY_CONTRACT,
    MULTI_INSTRUMENT_SCHEDULE_CONTRACT,
    NAUTILUS_EQUIVALENCE_CONTRACTS,
    ORDER_LATENCY_CONTRACT,
    ZERO_FRICTION_CONTRACT,
    DifferentialState,
    NautilusDifferentialBehavior,
    NautilusDifferentialEngine,
    NautilusDifferentialStore,
    NautilusExecutionResult,
    NautilusHistoricalBacktestAdapter,
    NautilusRawTerminalState,
    nautilus_execution_result_identity,
    nautilus_is_available,
)
from quantos.execution_reference import FirstCurrentReferenceFillEngine
from quantos.pricing_risk_contracts import Currency

UTC = timezone.utc
AT = datetime(2026, 9, 25, 12, tzinfo=UTC)
SUBMIT = AT + timedelta(seconds=1)
MS = timedelta(milliseconds=1)
FIXTURE_NAUTILUS_VERSION = "2.0.0rc5"

requires_nautilus = unittest.skipUnless(
    nautilus_is_available(),
    "NautilusTrader v2 requires Python >= 3.12",
)


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


def quote(
    *,
    at=SUBMIT,
    sequence=1,
    bid="99.99",
    ask="100.01",
    size="1000",
):
    return TopOfBookQuote(
        execution_instrument_id=instrument().execution_instrument_id,
        event_time=at,
        knowledge_time=at,
        sequence=sequence,
        bid_price=Decimal(bid),
        bid_quantity=Decimal(size),
        ask_price=Decimal(ask),
        ask_quantity=Decimal(size),
        source_fact_ids=(f"quote:{sequence}",),
    )


def dataset(*quotes, end=None):
    return HistoricalReplayDatasetBuilder().build(
        start_time=AT,
        end_time=end or AT + timedelta(seconds=10),
        events=quotes or (quote(),),
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
        "rationale": "Exact Nautilus differential fixture.",
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
        code_revision="git:stage12.4",
        created_at=AT,
        evidence_references=("simulation-run:evidence",),
    )


def intent(
    simulation_run,
    *,
    side=ExecutionSide.BUY,
    order_type=ExecutionOrderType.MARKET,
    limit_price=None,
    quantity="100",
    time_in_force=TimeInForce.GTC,
    submitted_at=SUBMIT,
):
    return SimulationOrderIntentBuilder().build(
        run=simulation_run,
        instrument=instrument(),
        side=side,
        order_type=order_type,
        quantity=Decimal(quantity),
        time_in_force=time_in_force,
        submitted_at=submitted_at,
        source_target_id="portfolio-target:SEC:A",
        limit_price=(
            Decimal(limit_price)
            if limit_price is not None
            else None
        ),
    )


def reference_side(data, p, order, tmp):
    reference_run = run("FIRST_CURRENT_REFERENCE", "12.2", data, p)
    reference_intent = intent(reference_run, **order)
    ledger = SimulationOrderLedger(Path(tmp) / "reference.duckdb")
    try:
        result = FirstCurrentReferenceFillEngine().simulate(
            run=reference_run,
            dataset=data,
            policy=p,
            instrument=instrument(),
            intent=reference_intent,
            ledger=ledger,
        )
        fills = ledger.fills(reference_intent.intent_id)
    finally:
        ledger.close()
    return reference_run, reference_intent, result, fills


def compare(data, p, contract=ZERO_FRICTION_CONTRACT, **order):
    adapter = NautilusHistoricalBacktestAdapter()
    with tempfile.TemporaryDirectory() as tmp:
        (
            reference_run,
            reference_intent,
            reference_result,
            reference_fills,
        ) = reference_side(data, p, order, tmp)
    nautilus_run = run("NAUTILUS_TRADER", adapter.engine_version, data, p)
    nautilus_intent = intent(nautilus_run, **order)
    nautilus_result = adapter.simulate(
        run=nautilus_run,
        dataset=data,
        policy=p,
        instrument=instrument(),
        intent=nautilus_intent,
        contract=contract,
    )
    differential = NautilusDifferentialEngine().compare(
        reference_run=reference_run,
        reference_intent=reference_intent,
        reference_result=reference_result,
        reference_fills=reference_fills,
        nautilus_run=nautilus_run,
        nautilus_intent=nautilus_intent,
        nautilus_result=nautilus_result,
    )
    return reference_result, nautilus_result, differential


def simulate_only(data, p, contract=ZERO_FRICTION_CONTRACT, **order):
    adapter = NautilusHistoricalBacktestAdapter()
    nautilus_run = run("NAUTILUS_TRADER", adapter.engine_version, data, p)
    return adapter.simulate(
        run=nautilus_run,
        dataset=data,
        policy=p,
        instrument=instrument(),
        intent=intent(nautilus_run, **order),
        contract=contract,
    )


class DifferentialAssertions(unittest.TestCase):
    def assertMatch(self, differential):
        self.assertEqual(
            differential.state,
            DifferentialState.MATCH,
            differential.diagnostics,
        )
        self.assertTrue(differential.fill_sequence_match)
        self.assertTrue(differential.fee_match)
        self.assertEqual(
            differential.trust_authority,
            "REFERENCE_MATCH_ONLY",
        )
        self.assertEqual(differential.network_authority, "NONE")
        self.assertEqual(differential.external_order_authority, "NONE")
        self.assertEqual(differential.capital_authority, "NONE")


@requires_nautilus
class ZeroFrictionContractTests(DifferentialAssertions):
    def test_market_buy_matches_reference_exactly(self):
        _, result, diff = compare(dataset(), policy())
        self.assertMatch(diff)
        self.assertEqual(diff.behavior, NautilusDifferentialBehavior.ZERO_FRICTION)
        self.assertEqual(result.filled_quantity, Decimal("100"))
        self.assertEqual(
            result.volume_weighted_average_price,
            Decimal("100.01"),
        )
        self.assertEqual(result.fill_times, (SUBMIT,))
        self.assertEqual(result.raw_terminal_state, NautilusRawTerminalState.FILLED)
        self.assertEqual(result.network_authority, "NONE")
        self.assertEqual(result.external_order_authority, "NONE")
        self.assertEqual(result.capital_authority, "NONE")
        self.assertEqual(result.result_id, nautilus_execution_result_identity(result))

    def test_marketable_limit_buy_matches_reference_exactly(self):
        _, _, diff = compare(
            dataset(),
            policy(),
            order_type=ExecutionOrderType.LIMIT,
            limit_price="100.01",
        )
        self.assertMatch(diff)

    def test_market_sell_matches_reference_exactly(self):
        _, result, diff = compare(
            dataset(),
            policy(),
            side=ExecutionSide.SELL,
        )
        self.assertMatch(diff)
        self.assertEqual(
            result.volume_weighted_average_price,
            Decimal("99.99"),
        )

    def test_fully_liquid_ioc_and_fok_fill_completely(self):
        for tif in (TimeInForce.IOC, TimeInForce.FOK):
            with self.subTest(tif=tif):
                _, result, diff = compare(
                    dataset(),
                    policy(),
                    time_in_force=tif,
                )
                self.assertMatch(diff)
                self.assertEqual(result.final_state, SimulationOrderState.FILLED)

    def test_differential_store_is_idempotent(self):
        _, _, diff = compare(dataset(), policy())
        with tempfile.TemporaryDirectory() as tmp:
            store = NautilusDifferentialStore(
                Path(tmp) / "nautilus-differential.duckdb"
            )
            self.assertTrue(store.add(diff))
            self.assertFalse(store.add(diff))
            store.close()


@requires_nautilus
class DeterministicFeesContractTests(DifferentialAssertions):
    def fee_data(self):
        return dataset(quote(bid="49.90", ask="50.00", size="5000"))

    def test_buy_commission_matches_reference_exactly(self):
        reference, result, diff = compare(
            self.fee_data(),
            policy(commission_bps=Decimal("3")),
            DETERMINISTIC_FEES_CONTRACT,
        )
        self.assertMatch(diff)
        self.assertEqual(reference.total_fees, Decimal("1.5"))
        self.assertEqual(result.total_fees, Decimal("1.50"))
        self.assertEqual(result.fill_fees, (Decimal("1.50"),))

    def test_sell_commission_matches_reference_exactly(self):
        _, result, diff = compare(
            self.fee_data(),
            policy(commission_bps=Decimal("3")),
            DETERMINISTIC_FEES_CONTRACT,
            side=ExecutionSide.SELL,
            quantity="1000",
        )
        self.assertMatch(diff)
        self.assertEqual(result.total_fees, Decimal("14.97"))

    def test_marketable_limit_commission_is_liquidity_side_neutral(self):
        _, _, diff = compare(
            self.fee_data(),
            policy(commission_bps=Decimal("3")),
            DETERMINISTIC_FEES_CONTRACT,
            order_type=ExecutionOrderType.LIMIT,
            limit_price="50.05",
        )
        self.assertMatch(diff)

    def test_commission_requiring_rounding_is_refused(self):
        with self.assertRaisesRegex(ValueError, "currency precision"):
            simulate_only(
                dataset(),
                policy(commission_bps=Decimal("1")),
                DETERMINISTIC_FEES_CONTRACT,
            )

    def test_fee_contract_requires_nonzero_commission(self):
        with self.assertRaisesRegex(ValueError, "requires the behavior"):
            simulate_only(dataset(), policy(), DETERMINISTIC_FEES_CONTRACT)

    def test_fees_are_refused_under_zero_friction_contract(self):
        with self.assertRaisesRegex(ValueError, "DETERMINISTIC_FEES"):
            simulate_only(
                self.fee_data(),
                policy(commission_bps=Decimal("3")),
            )


@requires_nautilus
class OrderLatencyContractTests(DifferentialAssertions):
    def latency_data(self):
        return dataset(
            quote(at=SUBMIT, sequence=1, ask="100.01", bid="99.99"),
            quote(at=SUBMIT + 2 * MS, sequence=2, ask="100.02", bid="100.00"),
            quote(at=SUBMIT + 5 * MS, sequence=3, ask="100.03", bid="100.01"),
        )

    def test_order_fills_on_activation_book_not_submission_book(self):
        reference, result, diff = compare(
            self.latency_data(),
            policy(order_latency_ms=5),
            ORDER_LATENCY_CONTRACT,
        )
        self.assertMatch(diff)
        self.assertEqual(reference.active_at, SUBMIT + 5 * MS)
        self.assertEqual(result.volume_weighted_average_price, Decimal("100.03"))
        self.assertEqual(result.fill_times, (SUBMIT + 5 * MS,))

    def test_sell_limit_with_latency_matches_reference(self):
        _, result, diff = compare(
            self.latency_data(),
            policy(order_latency_ms=5),
            ORDER_LATENCY_CONTRACT,
            side=ExecutionSide.SELL,
            order_type=ExecutionOrderType.LIMIT,
            limit_price="100.00",
        )
        self.assertMatch(diff)
        self.assertEqual(result.volume_weighted_average_price, Decimal("100.01"))

    def test_immediate_time_in_force_uses_activation_book(self):
        for tif in (TimeInForce.IOC, TimeInForce.FOK):
            with self.subTest(tif=tif):
                _, result, diff = compare(
                    self.latency_data(),
                    policy(order_latency_ms=2),
                    ORDER_LATENCY_CONTRACT,
                    time_in_force=tif,
                )
                self.assertMatch(diff)
                self.assertEqual(
                    result.volume_weighted_average_price,
                    Decimal("100.02"),
                )

    def test_activation_between_quotes_is_refused(self):
        with self.assertRaisesRegex(ValueError, "activation time"):
            simulate_only(
                self.latency_data(),
                policy(order_latency_ms=4),
                ORDER_LATENCY_CONTRACT,
            )

    def test_activation_after_horizon_is_refused(self):
        with self.assertRaisesRegex(ValueError, "horizon"):
            simulate_only(
                dataset(quote(), end=SUBMIT + 3 * MS),
                policy(order_latency_ms=5),
                ORDER_LATENCY_CONTRACT,
            )

    def test_latency_is_refused_under_zero_friction_contract(self):
        with self.assertRaisesRegex(ValueError, "ORDER_LATENCY"):
            simulate_only(self.latency_data(), policy(order_latency_ms=5))

    def test_latency_and_fees_cannot_be_combined(self):
        with self.assertRaisesRegex(ValueError, "DETERMINISTIC_FEES"):
            simulate_only(
                self.latency_data(),
                policy(order_latency_ms=5, commission_bps=Decimal("3")),
                ORDER_LATENCY_CONTRACT,
            )


@requires_nautilus
class MarketDataLatencyContractTests(DifferentialAssertions):
    def delayed_data(self, end=None):
        return dataset(
            quote(at=SUBMIT, sequence=1, ask="100.01", bid="99.99"),
            quote(at=SUBMIT + 4 * MS, sequence=2, ask="100.02", bid="100.00"),
            end=end,
        )

    def test_order_submitted_on_delayed_arrival_matches_reference(self):
        _, result, diff = compare(
            self.delayed_data(),
            policy(market_latency_ms=3),
            MARKET_DATA_LATENCY_CONTRACT,
            submitted_at=SUBMIT + 3 * MS,
        )
        self.assertMatch(diff)
        self.assertEqual(result.volume_weighted_average_price, Decimal("100.01"))
        self.assertEqual(result.fill_times, (SUBMIT + 3 * MS,))

    def test_post_horizon_arrivals_are_not_loaded(self):
        _, _, diff = compare(
            self.delayed_data(end=SUBMIT + 5 * MS),
            policy(market_latency_ms=3),
            MARKET_DATA_LATENCY_CONTRACT,
            submitted_at=SUBMIT + 3 * MS,
        )
        self.assertMatch(diff)

    def test_submission_at_undelayed_knowledge_time_is_refused(self):
        with self.assertRaisesRegex(ValueError, "submission time"):
            simulate_only(
                self.delayed_data(),
                policy(market_latency_ms=3),
                MARKET_DATA_LATENCY_CONTRACT,
            )


@requires_nautilus
class ImmediateTimeInForceContractTests(DifferentialAssertions):
    def short_book(self):
        return dataset(quote(size="60"))

    def test_ioc_shortfall_fills_displayed_and_expires_remainder(self):
        for side in (ExecutionSide.BUY, ExecutionSide.SELL):
            with self.subTest(side=side):
                reference, result, diff = compare(
                    self.short_book(),
                    policy(allow_partial_fills=True),
                    IMMEDIATE_TIME_IN_FORCE_CONTRACT,
                    side=side,
                    time_in_force=TimeInForce.IOC,
                )
                self.assertMatch(diff)
                self.assertEqual(result.filled_quantity, Decimal("60"))
                self.assertEqual(result.final_state, SimulationOrderState.EXPIRED)
                self.assertEqual(
                    result.raw_terminal_state,
                    NautilusRawTerminalState.CANCELED,
                )
                self.assertEqual(reference.final_state, SimulationOrderState.EXPIRED)

    def test_marketable_limit_ioc_shortfall_fills_only_at_touch(self):
        _, result, diff = compare(
            self.short_book(),
            policy(allow_partial_fills=True),
            IMMEDIATE_TIME_IN_FORCE_CONTRACT,
            order_type=ExecutionOrderType.LIMIT,
            limit_price="100.03",
            time_in_force=TimeInForce.IOC,
        )
        self.assertMatch(diff)
        self.assertEqual(result.fill_prices, (Decimal("100.01"),))

    def test_fok_shortfall_kills_without_fill(self):
        for partial in (False, True):
            with self.subTest(allow_partial_fills=partial):
                _, result, diff = compare(
                    self.short_book(),
                    policy(allow_partial_fills=partial),
                    IMMEDIATE_TIME_IN_FORCE_CONTRACT,
                    time_in_force=TimeInForce.FOK,
                )
                self.assertMatch(diff)
                self.assertEqual(result.fill_count, 0)
                self.assertEqual(result.final_state, SimulationOrderState.EXPIRED)

    def test_non_marketable_immediate_limits_expire_without_fill(self):
        for tif in (TimeInForce.IOC, TimeInForce.FOK):
            with self.subTest(tif=tif):
                _, result, diff = compare(
                    dataset(),
                    policy(),
                    IMMEDIATE_TIME_IN_FORCE_CONTRACT,
                    order_type=ExecutionOrderType.LIMIT,
                    limit_price="100.00",
                    time_in_force=tif,
                )
                self.assertMatch(diff)
                self.assertIsNone(result.volume_weighted_average_price)

    def test_ioc_shortfall_without_partial_fills_is_refused(self):
        with self.assertRaisesRegex(ValueError, "known"):
            simulate_only(
                self.short_book(),
                policy(),
                IMMEDIATE_TIME_IN_FORCE_CONTRACT,
                time_in_force=TimeInForce.IOC,
            )

    def test_fully_liquid_fixture_is_refused_as_not_exercising_contract(self):
        with self.assertRaisesRegex(ValueError, "fills completely"):
            simulate_only(
                dataset(),
                policy(),
                IMMEDIATE_TIME_IN_FORCE_CONTRACT,
                time_in_force=TimeInForce.IOC,
            )

    def test_resting_time_in_force_is_refused(self):
        with self.assertRaisesRegex(ValueError, "IOC or FOK"):
            simulate_only(
                self.short_book(),
                policy(allow_partial_fills=True),
                IMMEDIATE_TIME_IN_FORCE_CONTRACT,
            )


@requires_nautilus
class LimitTransitionContractTests(DifferentialAssertions):
    def transition_data(self, final_ask="100.00", final_size="500"):
        return dataset(
            quote(at=SUBMIT, sequence=1, ask="100.01", bid="99.99"),
            quote(at=SUBMIT + 5 * MS, sequence=2, ask="100.03", bid="100.00"),
            quote(
                at=SUBMIT + 10 * MS,
                sequence=3,
                ask=final_ask,
                bid="99.97",
                size=final_size,
            ),
        )

    def test_buy_limit_fills_when_touch_reaches_limit(self):
        for tif in (TimeInForce.GTC, TimeInForce.DAY):
            with self.subTest(tif=tif):
                _, result, diff = compare(
                    self.transition_data(),
                    policy(),
                    LIMIT_TRANSITION_CONTRACT,
                    order_type=ExecutionOrderType.LIMIT,
                    limit_price="100.00",
                    time_in_force=tif,
                )
                self.assertMatch(diff)
                self.assertEqual(result.fill_times, (SUBMIT + 10 * MS,))
                self.assertEqual(result.fill_prices, (Decimal("100.00"),))

    def test_sell_limit_fills_when_touch_reaches_limit(self):
        data = dataset(
            quote(at=SUBMIT, sequence=1, ask="100.01", bid="99.99"),
            quote(at=SUBMIT + 5 * MS, sequence=2, ask="100.02", bid="100.00"),
        )
        _, result, diff = compare(
            data,
            policy(),
            LIMIT_TRANSITION_CONTRACT,
            side=ExecutionSide.SELL,
            order_type=ExecutionOrderType.LIMIT,
            limit_price="100.00",
        )
        self.assertMatch(diff)
        self.assertEqual(result.fill_prices, (Decimal("100.00"),))

    def test_never_marketable_limit_expires_at_horizon(self):
        _, result, diff = compare(
            self.transition_data(final_ask="100.02"),
            policy(),
            LIMIT_TRANSITION_CONTRACT,
            order_type=ExecutionOrderType.LIMIT,
            limit_price="100.00",
        )
        self.assertMatch(diff)
        self.assertEqual(result.final_state, SimulationOrderState.EXPIRED)
        self.assertEqual(
            result.raw_terminal_state,
            NautilusRawTerminalState.OPEN_AT_HORIZON,
        )

    def test_transition_crossing_beyond_limit_is_refused(self):
        with self.assertRaisesRegex(ValueError, "beyond the limit"):
            simulate_only(
                self.transition_data(final_ask="99.99"),
                policy(),
                LIMIT_TRANSITION_CONTRACT,
                order_type=ExecutionOrderType.LIMIT,
                limit_price="100.00",
            )

    def test_transition_with_short_book_is_refused(self):
        with self.assertRaisesRegex(ValueError, "full displayed liquidity"):
            simulate_only(
                self.transition_data(final_size="50"),
                policy(),
                LIMIT_TRANSITION_CONTRACT,
                order_type=ExecutionOrderType.LIMIT,
                limit_price="100.00",
            )

    def test_marketable_limit_is_refused(self):
        with self.assertRaisesRegex(ValueError, "non-marketable"):
            simulate_only(
                self.transition_data(),
                policy(),
                LIMIT_TRANSITION_CONTRACT,
                order_type=ExecutionOrderType.LIMIT,
                limit_price="100.01",
            )

    def test_day_fixture_crossing_utc_date_is_refused(self):
        late = datetime(2026, 9, 25, 23, 59, 59, tzinfo=UTC)
        data = HistoricalReplayDatasetBuilder().build(
            start_time=late - timedelta(seconds=1),
            end_time=late + timedelta(seconds=2),
            events=(quote(at=late),),
        )
        with self.assertRaisesRegex(ValueError, "UTC date"):
            simulate_only(
                data,
                policy(),
                LIMIT_TRANSITION_CONTRACT,
                order_type=ExecutionOrderType.LIMIT,
                limit_price="100.00",
                time_in_force=TimeInForce.DAY,
                submitted_at=late,
            )


@requires_nautilus
class NautilusScopeTests(unittest.TestCase):
    def test_nonzero_slippage_fails_closed(self):
        with self.assertRaises(ValueError):
            simulate_only(dataset(), policy(slippage_bps=Decimal("1")))

    def test_partial_participation_fails_closed(self):
        with self.assertRaises(ValueError):
            simulate_only(
                dataset(),
                policy(maximum_participation_rate=Decimal("0.5")),
            )

    def test_partial_fills_outside_immediate_contract_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "partial fills"):
            simulate_only(dataset(), policy(allow_partial_fills=True))

    def test_tampered_contract_fails_closed(self):
        widened = replace(ZERO_FRICTION_CONTRACT, scope=("anything",))
        with self.assertRaisesRegex(ValueError, "frozen equivalence"):
            simulate_only(dataset(), policy(), widened)

    def test_duplicate_quote_arrival_time_fails_closed(self):
        data = dataset(
            quote(sequence=1),
            quote(sequence=2, ask="100.02"),
        )
        with self.assertRaisesRegex(ValueError, "unique quote arrival"):
            simulate_only(data, policy())

    def test_fractional_equity_scope_fails_closed(self):
        adapter = NautilusHistoricalBacktestAdapter()
        data = dataset()
        p = policy()
        r = run("NAUTILUS_TRADER", adapter.engine_version, data, p)
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

    def test_partial_liquidity_fixture_is_rejected_before_nautilus(self):
        with self.assertRaisesRegex(ValueError, "full displayed liquidity"):
            simulate_only(dataset(quote(size="50")), policy())


def other_instrument():
    return ExecutionInstrument(
        canonical_instrument_id="SEC:B",
        venue_id="XTEST",
        venue_symbol="B",
        asset_class=ExecutionAssetClass.EQUITY,
        quote_currency=Currency.USD,
        price_increment=Decimal("0.01"),
        quantity_increment=Decimal("1"),
        minimum_quantity=Decimal("1"),
        contract_multiplier=Decimal("1"),
    )


def other_quote(*, at, sequence, bid, ask, size="1000"):
    return TopOfBookQuote(
        execution_instrument_id=other_instrument().execution_instrument_id,
        event_time=at,
        knowledge_time=at,
        sequence=sequence,
        bid_price=Decimal(bid),
        bid_quantity=Decimal(size),
        ask_price=Decimal(ask),
        ask_quantity=Decimal(size),
        source_fact_ids=(f"quote:B:{sequence}",),
    )


@requires_nautilus
class MultiInstrumentScheduleContractTests(DifferentialAssertions):
    def schedule_data(self):
        return dataset(
            quote(at=SUBMIT, sequence=1, ask="100.01", bid="99.99"),
            other_quote(at=SUBMIT + 1 * MS, sequence=1, bid="49.99", ask="50.01"),
            quote(at=SUBMIT + 2 * MS, sequence=2, ask="100.05", bid="100.03"),
            other_quote(at=SUBMIT + 3 * MS, sequence=2, bid="49.95", ask="49.97"),
        )

    def run_schedule(self, data, p, orders):
        adapter = NautilusHistoricalBacktestAdapter()
        nautilus_run = run("NAUTILUS_TRADER", adapter.engine_version, data, p)
        reference_run = run("FIRST_CURRENT_REFERENCE", "12.2", data, p)
        instruments = (instrument(), other_instrument())
        by_id = {i.execution_instrument_id: i for i in instruments}
        nautilus_intents = tuple(
            SimulationOrderIntentBuilder().build(run=nautilus_run, **o)
            for o in orders
        )
        results = adapter.simulate_schedule(
            run=nautilus_run,
            dataset=data,
            policy=p,
            instruments=instruments,
            intents=nautilus_intents,
        )
        results_by_intent = {r.intent_id: r for r in results}
        engine = NautilusDifferentialEngine()
        differentials = []
        with tempfile.TemporaryDirectory() as tmp:
            ledger = SimulationOrderLedger(Path(tmp) / "reference.duckdb")
            try:
                for o, nautilus_intent in zip(orders, nautilus_intents):
                    reference_intent = SimulationOrderIntentBuilder().build(
                        run=reference_run, **o
                    )
                    reference_result = FirstCurrentReferenceFillEngine().simulate(
                        run=reference_run,
                        dataset=data,
                        policy=p,
                        instrument=by_id[reference_intent.execution_instrument_id],
                        intent=reference_intent,
                        ledger=ledger,
                    )
                    differentials.append(
                        engine.compare(
                            reference_run=reference_run,
                            reference_intent=reference_intent,
                            reference_result=reference_result,
                            reference_fills=ledger.fills(reference_intent.intent_id),
                            nautilus_run=nautilus_run,
                            nautilus_intent=nautilus_intent,
                            nautilus_result=results_by_intent[nautilus_intent.intent_id],
                        )
                    )
            finally:
                ledger.close()
        return results, tuple(differentials)

    def orders(self):
        return (
            {
                "instrument": instrument(),
                "side": ExecutionSide.BUY,
                "order_type": ExecutionOrderType.MARKET,
                "quantity": Decimal("100"),
                "time_in_force": TimeInForce.GTC,
                "submitted_at": SUBMIT,
                "source_target_id": "portfolio-target:SEC:A",
            },
            {
                "instrument": other_instrument(),
                "side": ExecutionSide.SELL,
                "order_type": ExecutionOrderType.LIMIT,
                "limit_price": Decimal("49.95"),
                "quantity": Decimal("200"),
                "time_in_force": TimeInForce.IOC,
                "submitted_at": SUBMIT + 3 * MS,
                "source_target_id": "portfolio-target:SEC:B",
            },
        )

    def test_interleaved_instruments_match_reference_per_order(self):
        results, differentials = self.run_schedule(
            self.schedule_data(), policy(), self.orders()
        )
        for diff in differentials:
            self.assertMatch(diff)
        prices = sorted(r.volume_weighted_average_price for r in results)
        self.assertEqual(prices, [Decimal("49.95"), Decimal("100.01")])
        schedule = NautilusDifferentialEngine().compare_schedule(
            order_differentials=differentials,
            nautilus_results=results,
        )
        self.assertEqual(schedule.state, DifferentialState.MATCH)
        self.assertEqual(schedule.trust_authority, "REFERENCE_MATCH_ONLY")
        self.assertEqual(schedule.capital_authority, "NONE")
        self.assertEqual(len(schedule.execution_instrument_ids), 2)

    def test_one_mismatching_order_keeps_schedule_mismatched(self):
        results, differentials = self.run_schedule(
            self.schedule_data(), policy(), self.orders()
        )
        target = differentials[0]
        tampered = replace(target, state=DifferentialState.MISMATCH, trust_authority="NONE")
        from quantos.execution_nautilus import nautilus_differential_result_identity

        tampered = replace(
            tampered,
            differential_id=nautilus_differential_result_identity(tampered),
        )
        schedule = NautilusDifferentialEngine().compare_schedule(
            order_differentials=(tampered, differentials[1]),
            nautilus_results=results,
        )
        self.assertEqual(schedule.state, DifferentialState.MISMATCH)
        self.assertEqual(schedule.trust_authority, "NONE")
        self.assertEqual(schedule.mismatched_differential_ids, (tampered.differential_id,))

    def test_two_orders_on_one_instrument_are_refused(self):
        orders = self.orders()
        same = dict(orders[1], instrument=instrument(), limit_price=Decimal("100.03"), quantity=Decimal("10"), submitted_at=SUBMIT + 2 * MS)
        with self.assertRaisesRegex(ValueError, "one order per instrument"):
            self.run_schedule(self.schedule_data(), policy(), (orders[0], same))

    def test_schedule_contract_requires_schedule_entry_point(self):
        with self.assertRaisesRegex(ValueError, "simulate_schedule"):
            simulate_only(dataset(), policy(), MULTI_INSTRUMENT_SCHEDULE_CONTRACT)

    def test_schedule_with_fees_is_outside_contract(self):
        with self.assertRaisesRegex(ValueError, "DETERMINISTIC_FEES"):
            self.run_schedule(
                self.schedule_data(),
                policy(commission_bps=Decimal("3")),
                self.orders(),
            )


ACCUMULATE = {
    "allow_partial_fills": True,
    "resting_limit_fill_price": RestingLimitFillPrice.LIMIT_PRICE,
    "liquidity_refresh": LiquidityRefresh.ON_LEVEL_SIZE_CHANGE,
}


@requires_nautilus
class Stage1210ContractTests(DifferentialAssertions):
    def test_rounded_fees_match_half_even_reference(self):
        p = policy(commission_bps=Decimal("1"), commission_rounding=CommissionRounding.HALF_EVEN_MINOR_UNIT)
        reference, result, diff = compare(dataset(), p, ROUNDED_FEES_CONTRACT)
        self.assertMatch(diff)
        self.assertEqual(reference.total_fees, Decimal("1.00"))
        self.assertEqual(result.total_fees, Decimal("1.00"))

    def test_rounded_fee_tie_is_refused(self):
        p = policy(commission_bps=Decimal("1"), commission_rounding=CommissionRounding.HALF_EVEN_MINOR_UNIT)
        with self.assertRaisesRegex(ValueError, "ties"):
            simulate_only(dataset(quote(bid="49.90", ask="50.00")), p, ROUNDED_FEES_CONTRACT, quantity="3")

    def test_exact_fee_contract_still_refuses_rounding_mode(self):
        p = policy(commission_bps=Decimal("3"), commission_rounding=CommissionRounding.HALF_EVEN_MINOR_UNIT)
        with self.assertRaisesRegex(ValueError, "reference mode"):
            simulate_only(dataset(quote(bid="49.90", ask="50.00")), p, DETERMINISTIC_FEES_CONTRACT)

    def latency_data(self):
        return dataset(
            quote(at=SUBMIT, sequence=1, ask="100.01", bid="99.99"),
            quote(at=SUBMIT + 5 * MS, sequence=2, ask="100.02", bid="100.00"),
            quote(at=SUBMIT + 10 * MS, sequence=3, ask="100.03", bid="100.01"),
        )

    def test_next_arrival_latency_matches_between_quotes(self):
        p = policy(order_latency_ms=7, order_activation=OrderActivationMode.NEXT_QUOTE_ARRIVAL)
        for tif in (TimeInForce.GTC, TimeInForce.IOC):
            with self.subTest(tif=tif):
                reference, result, diff = compare(self.latency_data(), p, NEXT_ARRIVAL_LATENCY_CONTRACT, time_in_force=tif)
                self.assertMatch(diff)
                self.assertEqual(result.volume_weighted_average_price, Decimal("100.03"))
                self.assertEqual(reference.active_at, SUBMIT + 10 * MS)

    def test_next_arrival_without_later_quote_is_refused(self):
        p = policy(order_latency_ms=12, order_activation=OrderActivationMode.NEXT_QUOTE_ARRIVAL)
        with self.assertRaisesRegex(ValueError, "at or after activation"):
            simulate_only(self.latency_data(), p, NEXT_ARRIVAL_LATENCY_CONTRACT)

    def test_market_sweep_fills_residual_one_tick_through(self):
        p = policy(market_order_residual=MarketOrderResidual.ONE_TICK_THROUGH)
        for side, prices in ((ExecutionSide.BUY, (Decimal("100.01"), Decimal("100.02"))),
                             (ExecutionSide.SELL, (Decimal("99.99"), Decimal("99.98")))):
            with self.subTest(side=side):
                _, result, diff = compare(dataset(quote(size="60")), p, MARKET_L1_SWEEP_CONTRACT, side=side, quantity="250")
                self.assertMatch(diff)
                self.assertEqual(result.fill_prices, prices)
                self.assertEqual(result.fill_quantities, (Decimal("60"), Decimal("190")))

    def test_ioc_always_partial(self):
        p = policy(immediate_partial_fills=ImmediatePartialFills.ALWAYS_ALLOW)
        _, result, diff = compare(dataset(quote(size="60")), p, IOC_ALWAYS_PARTIAL_CONTRACT, time_in_force=TimeInForce.IOC)
        self.assertMatch(diff)
        self.assertEqual(result.filled_quantity, Decimal("60"))
        self.assertEqual(result.final_state, SimulationOrderState.EXPIRED)

    def test_resting_limit_accumulation_documented_cases(self):
        cases = {
            "refresh-on-change": [("100.01", "60"), ("100.01", "60"), ("100.01", "30"), ("100.01", "30"), ("100.01", "25")],
            "cross-through-at-limit": [("100.02", "60"), ("100.00", "30"), ("100.00", "30"), ("99.99", "30")],
            "horizon-expiry": [("100.01", "20"), ("100.03", "500"), ("100.01", "20")],
        }
        for name, book in cases.items():
            with self.subTest(case=name):
                quotes = tuple(
                    quote(at=SUBMIT + i * MS, sequence=i + 1, ask=ask, bid=str(Decimal(ask) - Decimal("0.02")), size=size)
                    for i, (ask, size) in enumerate(book)
                )
                _, result, diff = compare(
                    dataset(*quotes), policy(**ACCUMULATE), RESTING_LIMIT_ACCUMULATION_CONTRACT,
                    order_type=ExecutionOrderType.LIMIT, limit_price="100.01",
                )
                self.assertMatch(diff)

    def test_resting_limit_accumulation_randomized_differential(self):
        import random

        rng = random.Random(1210)
        for trial in range(120):
            side = rng.choice((ExecutionSide.BUY, ExecutionSide.SELL))
            quotes = []
            previous = None
            for i in range(rng.randint(3, 8)):
                if previous is not None and rng.random() < 0.3:
                    ask, size = previous
                else:
                    ask = Decimal("100.01") + Decimal("0.01") * rng.randint(-2, 2)
                    size = str(rng.choice((10, 20, 30, 40, 60, 80)))
                previous = (ask, size)
                bid = ask - Decimal("0.02")
                if side is ExecutionSide.SELL:
                    bid, ask = ask - Decimal("0.02") + Decimal("0.02"), ask + Decimal("0.02")
                quotes.append(quote(at=SUBMIT + i * MS, sequence=i + 1, bid=str(bid), ask=str(ask), size=size))
            first_touch = quotes[0].ask_price if side is ExecutionSide.BUY else quotes[0].bid_price
            if first_touch == Decimal("100.01") and quotes[0].ask_quantity >= 100:
                continue
            try:
                _, _, diff = compare(
                    dataset(*quotes), policy(**ACCUMULATE), RESTING_LIMIT_ACCUMULATION_CONTRACT,
                    side=side, order_type=ExecutionOrderType.LIMIT, limit_price="100.01",
                )
            except ValueError as exc:
                if "fills completely" in str(exc):
                    continue
                raise
            with self.subTest(trial=trial):
                self.assertMatch(diff)


class NautilusEnvironmentTests(unittest.TestCase):
    def test_python_311_reports_nautilus_unavailable(self):
        if sys.version_info < (3, 12):
            self.assertFalse(nautilus_is_available())

    def test_ci_requires_nautilus_runtime_where_declared(self):
        # CI sets this on Python 3.12+ so a failed install cannot turn the
        # runtime differential tests into silent skips.
        if os.environ.get("QUANTOS_REQUIRE_NAUTILUS") == "1":
            self.assertTrue(nautilus_is_available())


class EquivalenceContractRegistryTests(unittest.TestCase):
    def test_every_behavior_has_exactly_one_frozen_contract(self):
        self.assertEqual(
            set(NAUTILUS_EQUIVALENCE_CONTRACTS),
            set(NautilusDifferentialBehavior),
        )
        ids = [c.contract_id for c in NAUTILUS_EQUIVALENCE_CONTRACTS.values()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_contract_identity_changes_with_scope(self):
        widened = replace(
            ORDER_LATENCY_CONTRACT,
            known_divergences=(),
        )
        self.assertNotEqual(
            widened.contract_id,
            ORDER_LATENCY_CONTRACT.contract_id,
        )

    def test_divergent_behaviors_record_known_divergences(self):
        for contract in (
            DETERMINISTIC_FEES_CONTRACT,
            ORDER_LATENCY_CONTRACT,
            IMMEDIATE_TIME_IN_FORCE_CONTRACT,
            LIMIT_TRANSITION_CONTRACT,
        ):
            self.assertTrue(contract.known_divergences, contract.behavior)


class SyntheticDifferentialTests(unittest.TestCase):
    """Differential-gate logic, runnable without NautilusTrader."""

    def setUp(self):
        self.data = dataset(quote(bid="49.90", ask="50.00", size="5000"))
        self.policy = policy(commission_bps=Decimal("3"))
        with tempfile.TemporaryDirectory() as tmp:
            (
                self.reference_run,
                self.reference_intent,
                self.reference_result,
                self.reference_fills,
            ) = reference_side(self.data, self.policy, {}, tmp)
        self.nautilus_run = run(
            "NAUTILUS_TRADER",
            FIXTURE_NAUTILUS_VERSION,
            self.data,
            self.policy,
        )
        self.nautilus_intent = intent(self.nautilus_run)

    def nautilus_result(self, **overrides):
        values = {
            "result_id": "",
            "run_id": self.nautilus_run.run_id,
            "intent_id": self.nautilus_intent.intent_id,
            "execution_instrument_id": instrument().execution_instrument_id,
            "engine_name": "NAUTILUS_TRADER",
            "engine_version": FIXTURE_NAUTILUS_VERSION,
            "adapter_version": "12.4",
            "contract_id": DETERMINISTIC_FEES_CONTRACT.contract_id,
            "behavior": NautilusDifferentialBehavior.DETERMINISTIC_FEES,
            "config_fingerprint": "nautilus-backtest-config:" + "c" * 64,
            "final_state": SimulationOrderState.FILLED,
            "raw_terminal_state": NautilusRawTerminalState.FILLED,
            "fill_count": 1,
            "filled_quantity": Decimal("100"),
            "remaining_quantity": Decimal("0"),
            "volume_weighted_average_price": Decimal("50.00"),
            "total_fees": Decimal("1.50"),
            "fill_times": (SUBMIT,),
            "fill_prices": (Decimal("50.00"),),
            "fill_quantities": (Decimal("100"),),
            "fill_fees": (Decimal("1.50"),),
            "fill_liquidity_sides": ("TAKER",),
            "source_quote_event_ids": (self.data.events[0].event_id,),
            "diagnostics": ("synthetic",),
            "network_authority": "NONE",
            "external_order_authority": "NONE",
            "capital_authority": "NONE",
        }
        values.update(overrides)
        result = NautilusExecutionResult(**values)
        return replace(
            result,
            result_id=nautilus_execution_result_identity(result),
        )

    def compare(self, nautilus_result, reference_fills=None):
        return NautilusDifferentialEngine().compare(
            reference_run=self.reference_run,
            reference_intent=self.reference_intent,
            reference_result=self.reference_result,
            reference_fills=(
                self.reference_fills
                if reference_fills is None
                else reference_fills
            ),
            nautilus_run=self.nautilus_run,
            nautilus_intent=self.nautilus_intent,
            nautilus_result=nautilus_result,
        )

    def test_identical_outcome_matches(self):
        diff = self.compare(self.nautilus_result())
        self.assertEqual(diff.state, DifferentialState.MATCH)
        self.assertEqual(diff.trust_authority, "REFERENCE_MATCH_ONLY")
        self.assertEqual(diff.contract_id, DETERMINISTIC_FEES_CONTRACT.contract_id)

    def test_fee_divergence_is_preserved_as_mismatch(self):
        diff = self.compare(
            self.nautilus_result(
                total_fees=Decimal("1.51"),
                fill_fees=(Decimal("1.51"),),
            )
        )
        self.assertEqual(diff.state, DifferentialState.MISMATCH)
        self.assertFalse(diff.fee_match)
        self.assertFalse(diff.fill_sequence_match)
        self.assertEqual(diff.trust_authority, "NONE")
        self.assertIn(
            "fill[0] fee reference=1.50 nautilus=1.51",
            diff.diagnostics,
        )

    def test_fill_time_divergence_is_preserved_as_mismatch(self):
        diff = self.compare(
            self.nautilus_result(fill_times=(SUBMIT + 5 * MS,))
        )
        self.assertEqual(diff.state, DifferentialState.MISMATCH)
        self.assertTrue(diff.price_match)
        self.assertFalse(diff.fill_sequence_match)

    def test_split_fill_with_same_vwap_is_still_a_mismatch(self):
        diff = self.compare(
            self.nautilus_result(
                fill_count=2,
                fill_times=(SUBMIT, SUBMIT),
                fill_prices=(Decimal("50.00"), Decimal("50.00")),
                fill_quantities=(Decimal("40"), Decimal("60")),
                fill_fees=(Decimal("0.60"), Decimal("0.90")),
            )
        )
        self.assertEqual(diff.state, DifferentialState.MISMATCH)
        self.assertTrue(diff.quantity_match)
        self.assertTrue(diff.price_match)
        self.assertTrue(diff.fee_match)
        self.assertFalse(diff.fill_count_match)

    def test_tampered_nautilus_result_is_refused(self):
        tampered = replace(
            self.nautilus_result(),
            total_fees=Decimal("0"),
        )
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            self.compare(tampered)

    def test_unfrozen_contract_binding_is_refused(self):
        with self.assertRaisesRegex(ValueError, "frozen equivalence"):
            self.compare(
                self.nautilus_result(
                    contract_id=ZERO_FRICTION_CONTRACT.contract_id,
                )
            )

    def test_schedule_aggregation_requires_schedule_contract(self):
        diff = self.compare(self.nautilus_result())
        with self.assertRaisesRegex(ValueError, "schedule contract"):
            NautilusDifferentialEngine().compare_schedule(
                order_differentials=(diff, diff),
                nautilus_results=(self.nautilus_result(), self.nautilus_result()),
            )

    def test_reference_fills_must_match_reference_result(self):
        with self.assertRaisesRegex(ValueError, "reference fills differ"):
            self.compare(self.nautilus_result(), reference_fills=())

    def test_tampered_reference_fill_is_refused(self):
        tampered = (replace(self.reference_fills[0], fee=Decimal("0")),)
        with self.assertRaisesRegex(ValueError, "reference fill identity"):
            self.compare(self.nautilus_result(), reference_fills=tampered)


if __name__ == "__main__":
    unittest.main()
