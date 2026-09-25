from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path

import duckdb

from .execution_contracts import (
    ExecutionAssetClass,
    ExecutionInstrument,
    ExecutionOrderType,
    ExecutionSide,
    ExecutionSimulationPolicy,
    ExecutionSimulationRunManifest,
    HistoricalReplayDataset,
    SimulatedFill,
    SimulationOrderIntent,
    SimulationOrderState,
    TimeInForce,
    TopOfBookQuote,
    execution_simulation_run_identity,
    historical_replay_dataset_identity,
    simulated_fill_identity,
    simulation_order_intent_identity,
)
from .execution_reference import (
    ReferenceExecutionResult,
    reference_execution_result_identity,
)
from .pricing_risk_contracts import Currency


NAUTILUS_DISTRIBUTION = "nautilus_trader"
ADAPTER_VERSION = "12.4"

# Minor-unit precision Nautilus applies when it rounds commissions to Money.
# A currency absent from this table cannot enter a fee differential.
CURRENCY_PRECISION: dict[Currency, int] = {
    Currency.USD: 2,
    Currency.GBP: 2,
    Currency.EUR: 2,
    Currency.CNY: 2,
    Currency.JPY: 0,
    Currency.CHF: 2,
    Currency.CAD: 2,
    Currency.AUD: 2,
    Currency.HKD: 2,
}

_IMMEDIATE_TIME_IN_FORCE = frozenset({TimeInForce.IOC, TimeInForce.FOK})
_RESTING_TIME_IN_FORCE = frozenset({TimeInForce.DAY, TimeInForce.GTC})


class DifferentialState(str, Enum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"


class NautilusDifferentialBehavior(str, Enum):
    """The single behavior a frozen equivalence contract exercises."""

    ZERO_FRICTION = "ZERO_FRICTION"
    DETERMINISTIC_FEES = "DETERMINISTIC_FEES"
    ORDER_LATENCY = "ORDER_LATENCY"
    MARKET_DATA_LATENCY = "MARKET_DATA_LATENCY"
    IMMEDIATE_TIME_IN_FORCE = "IMMEDIATE_TIME_IN_FORCE"
    LIMIT_TRANSITION = "LIMIT_TRANSITION"
    MULTI_INSTRUMENT_SCHEDULE = "MULTI_INSTRUMENT_SCHEDULE"


class NautilusRawTerminalState(str, Enum):
    """What Nautilus itself reported before First Current mapping."""

    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"
    OPEN_AT_HORIZON = "OPEN_AT_HORIZON"


COMPARED_FIELDS = (
    "final_state",
    "fill_count",
    "filled_quantity",
    "volume_weighted_average_price",
    "total_fees",
    "fill_sequence.fill_time",
    "fill_sequence.quantity",
    "fill_sequence.price",
    "fill_sequence.fee",
)


@dataclass(frozen=True)
class NautilusEquivalenceContract:
    """Frozen statement of one overlap where both engines must agree.

    Each contract adds exactly one behavior to the Stage 12.3 zero-friction
    baseline. Contracts are never composed: a fixture that needs two new
    behaviors at once is outside every frozen contract and fails closed.
    """

    behavior: NautilusDifferentialBehavior
    stage: str
    scope: tuple[str, ...]
    mapping: tuple[str, ...]
    compared_fields: tuple[str, ...]
    known_divergences: tuple[str, ...]

    @property
    def contract_id(self) -> str:
        return _content_id(
            "nautilus-equivalence-contract",
            nautilus_equivalence_contract_payload(self),
        )


_BASELINE_SCOPE = (
    "whole-share equity with unit multiplier",
    "slippage and market impact are zero",
    "maximum participation is exactly 1",
    "every mapped quote arrival time is unique",
    "exactly one quote arrives at order submission time",
)

ZERO_FRICTION_CONTRACT = NautilusEquivalenceContract(
    behavior=NautilusDifferentialBehavior.ZERO_FRICTION,
    stage="12.3",
    scope=_BASELINE_SCOPE
    + (
        "market and order latency are zero",
        "commission is zero",
        "partial fills are disabled",
        "displayed contra liquidity fills the complete order",
        "limit orders are already marketable",
    ),
    mapping=(
        "event_time -> QuoteTick.ts_event",
        "knowledge_time -> QuoteTick.ts_init",
        "L1_MBP book, trade_execution disabled",
        "liquidity_consumption enabled, queue_position disabled",
        "zero MakerTakerFeeModel rates",
    ),
    compared_fields=COMPARED_FIELDS,
    known_divergences=(),
)

DETERMINISTIC_FEES_CONTRACT = NautilusEquivalenceContract(
    behavior=NautilusDifferentialBehavior.DETERMINISTIC_FEES,
    stage="12.4.1",
    scope=_BASELINE_SCOPE
    + (
        "commission_bps is strictly positive",
        "market and order latency are zero",
        "partial fills are disabled",
        "displayed contra liquidity fills the complete order",
        "limit orders are already marketable",
        "exact commission is representable at currency minor-unit precision",
    ),
    mapping=(
        "commission_bps / 10000 -> Equity.maker_fee and Equity.taker_fee",
        "maker and taker rates are equal so liquidity side cannot change fees",
        "MakerTakerFeeModel commission -> per-fill fee",
    ),
    compared_fields=COMPARED_FIELDS,
    known_divergences=(
        "Nautilus rounds commission half-even to currency precision; the "
        "First Current reference keeps the exact decimal. Fixtures whose "
        "exact commission needs rounding are refused.",
    ),
)

ORDER_LATENCY_CONTRACT = NautilusEquivalenceContract(
    behavior=NautilusDifferentialBehavior.ORDER_LATENCY,
    stage="12.4.2",
    scope=_BASELINE_SCOPE
    + (
        "order_latency_ms is strictly positive",
        "market latency and commission are zero",
        "partial fills are disabled",
        "order activation time is at or before the replay horizon",
        "exactly one quote arrives at order activation time",
        "activation quote displays liquidity for the complete order",
        "limit orders are marketable on the activation quote",
    ),
    mapping=(
        "order_latency_ms -> StaticLatencyModel insert/update/cancel "
        "latency with zero base latency",
    ),
    compared_fields=COMPARED_FIELDS,
    known_divergences=(
        "Nautilus processes an in-flight order only when the next data "
        "point arrives and matches it against that post-activation book; "
        "the reference matches immediately against the latest book known "
        "at activation. Activation between quote arrivals is refused.",
    ),
)

MARKET_DATA_LATENCY_CONTRACT = NautilusEquivalenceContract(
    behavior=NautilusDifferentialBehavior.MARKET_DATA_LATENCY,
    stage="12.4.3",
    scope=_BASELINE_SCOPE
    + (
        "market_latency_ms is strictly positive",
        "order latency and commission are zero",
        "partial fills are disabled",
        "displayed contra liquidity fills the complete order",
        "limit orders are already marketable",
    ),
    mapping=(
        "knowledge_time + market_latency_ms -> QuoteTick.ts_init",
        "quotes arriving after the replay horizon are not loaded",
    ),
    compared_fields=COMPARED_FIELDS,
    known_divergences=(),
)

IMMEDIATE_TIME_IN_FORCE_CONTRACT = NautilusEquivalenceContract(
    behavior=NautilusDifferentialBehavior.IMMEDIATE_TIME_IN_FORCE,
    stage="12.4.4",
    scope=_BASELINE_SCOPE
    + (
        "time in force is IOC or FOK",
        "market and order latency are zero",
        "commission is zero",
        "the order leaves an unfilled remainder on the submission quote",
        "IOC displayed-liquidity shortfall requires partial fills enabled",
    ),
    mapping=(
        "Nautilus venue cancel of an IOC/FOK remainder -> EXPIRED",
    ),
    compared_fields=COMPARED_FIELDS,
    known_divergences=(
        "With partial fills disabled the reference IOC takes nothing from "
        "a short book while Nautilus IOC takes the displayed quantity; "
        "that fixture is refused.",
    ),
)

LIMIT_TRANSITION_CONTRACT = NautilusEquivalenceContract(
    behavior=NautilusDifferentialBehavior.LIMIT_TRANSITION,
    stage="12.4.5",
    scope=_BASELINE_SCOPE
    + (
        "limit order with DAY or GTC time in force",
        "market and order latency are zero",
        "commission is zero",
        "partial fills are disabled",
        "limit is not marketable on the submission quote",
        "first later marketable quote has contra touch exactly at the limit",
        "that quote displays liquidity for the complete order",
        "DAY fixtures do not cross a UTC date boundary",
    ),
    mapping=(
        "resting limit filled by a later quote at the limit price",
        "order still working when replay data ends -> EXPIRED at horizon",
    ),
    compared_fields=COMPARED_FIELDS,
    known_divergences=(
        "Nautilus fills a resting limit at its limit price even when the "
        "later book crosses through it; the reference fills at the contra "
        "touch. Transitions that cross beyond the limit are refused.",
        "Nautilus liquidity consumption does not refresh an unchanged "
        "repeated quote; multi-quote partial accumulation is refused.",
    ),
)

MULTI_INSTRUMENT_SCHEDULE_CONTRACT = NautilusEquivalenceContract(
    behavior=NautilusDifferentialBehavior.MULTI_INSTRUMENT_SCHEDULE,
    stage="12.6",
    scope=_BASELINE_SCOPE
    + (
        "at least two orders run in one backtest",
        "exactly one order per instrument",
        "all instruments share one venue and quote currency",
        "every order individually satisfies the zero-friction scope",
    ),
    mapping=(
        "one Nautilus Equity per execution instrument on one venue",
        "quotes from every instrument interleaved by ts_init",
        "fills attributed to orders by client_order_id",
    ),
    compared_fields=COMPARED_FIELDS,
    known_divergences=(
        "Two working orders on one instrument would share displayed "
        "liquidity; the reference has no shared-liquidity semantics, so "
        "such schedules are refused.",
    ),
)

NAUTILUS_EQUIVALENCE_CONTRACTS: dict[
    NautilusDifferentialBehavior,
    NautilusEquivalenceContract,
] = {
    contract.behavior: contract
    for contract in (
        ZERO_FRICTION_CONTRACT,
        DETERMINISTIC_FEES_CONTRACT,
        ORDER_LATENCY_CONTRACT,
        MARKET_DATA_LATENCY_CONTRACT,
        IMMEDIATE_TIME_IN_FORCE_CONTRACT,
        LIMIT_TRANSITION_CONTRACT,
        MULTI_INSTRUMENT_SCHEDULE_CONTRACT,
    )
}


@dataclass(frozen=True)
class NautilusExecutionResult:
    result_id: str
    run_id: str
    intent_id: str
    execution_instrument_id: str
    engine_name: str
    engine_version: str
    adapter_version: str
    contract_id: str
    behavior: NautilusDifferentialBehavior
    config_fingerprint: str
    final_state: SimulationOrderState
    raw_terminal_state: NautilusRawTerminalState
    fill_count: int
    filled_quantity: Decimal
    remaining_quantity: Decimal
    volume_weighted_average_price: Decimal | None
    total_fees: Decimal
    fill_times: tuple[datetime, ...]
    fill_prices: tuple[Decimal, ...]
    fill_quantities: tuple[Decimal, ...]
    fill_fees: tuple[Decimal, ...]
    fill_liquidity_sides: tuple[str, ...]
    source_quote_event_ids: tuple[str, ...]
    diagnostics: tuple[str, ...]
    network_authority: str
    external_order_authority: str
    capital_authority: str


@dataclass(frozen=True)
class NautilusDifferentialResult:
    differential_id: str
    reference_result_id: str
    nautilus_result_id: str
    reference_run_id: str
    nautilus_run_id: str
    replay_dataset_id: str
    simulation_policy_id: str
    economic_intent_fingerprint: str
    contract_id: str
    behavior: NautilusDifferentialBehavior
    nautilus_version: str
    adapter_version: str
    state: DifferentialState
    final_state_match: bool
    fill_count_match: bool
    quantity_match: bool
    price_match: bool
    fee_match: bool
    fill_sequence_match: bool
    reference_final_state: SimulationOrderState
    nautilus_final_state: SimulationOrderState
    nautilus_raw_terminal_state: NautilusRawTerminalState
    reference_filled_quantity: Decimal
    nautilus_filled_quantity: Decimal
    quantity_error: Decimal
    reference_vwap: Decimal | None
    nautilus_vwap: Decimal | None
    absolute_vwap_error: Decimal | None
    reference_total_fees: Decimal
    nautilus_total_fees: Decimal
    diagnostics: tuple[str, ...]
    trust_authority: str
    network_authority: str
    external_order_authority: str
    capital_authority: str


@dataclass(frozen=True)
class NautilusScheduleDifferentialResult:
    schedule_differential_id: str
    replay_dataset_id: str
    simulation_policy_id: str
    contract_id: str
    order_differential_ids: tuple[str, ...]
    execution_instrument_ids: tuple[str, ...]
    state: DifferentialState
    mismatched_differential_ids: tuple[str, ...]
    trust_authority: str
    network_authority: str
    external_order_authority: str
    capital_authority: str


@dataclass(frozen=True)
class _ScopedFixture:
    submission_quote: TopOfBookQuote
    activation_quote: TopOfBookQuote
    arrivals: tuple[tuple[TopOfBookQuote, datetime], ...]


@dataclass(frozen=True)
class _OrderPlan:
    intent: SimulationOrderIntent
    instrument: ExecutionInstrument
    fixture: _ScopedFixture


def nautilus_is_available() -> bool:
    if sys.version_info < (3, 12):
        return False
    try:
        package_version(NAUTILUS_DISTRIBUTION)
    except PackageNotFoundError:
        return False
    return True


class NautilusHistoricalBacktestAdapter:
    """Historical-only NautilusTrader differential adapter.

    This adapter supports only frozen equivalence contracts where First
    Current's reference engine and Nautilus can be compared without hidden
    latency, cost, participation, or queue assumptions.
    """

    ENGINE_NAME = "NAUTILUS_TRADER"

    def __init__(self) -> None:
        if not nautilus_is_available():
            raise RuntimeError(
                "NautilusTrader v2 adapter requires Python >= 3.12 and the "
                "nautilus_trader distribution"
            )
        self.engine_version = package_version(NAUTILUS_DISTRIBUTION)

    def simulate(
        self,
        *,
        run: ExecutionSimulationRunManifest,
        dataset: HistoricalReplayDataset,
        policy: ExecutionSimulationPolicy,
        instrument: ExecutionInstrument,
        intent: SimulationOrderIntent,
        contract: NautilusEquivalenceContract = ZERO_FRICTION_CONTRACT,
    ) -> NautilusExecutionResult:
        if (
            contract.behavior
            is NautilusDifferentialBehavior.MULTI_INSTRUMENT_SCHEDULE
        ):
            raise ValueError(
                "MULTI_INSTRUMENT_SCHEDULE contract requires simulate_schedule"
            )
        fixture = self._validate_scope(
            run=run,
            dataset=dataset,
            policy=policy,
            instrument=instrument,
            intent=intent,
            contract=contract,
        )
        return self._run_backtest(
            run=run,
            policy=policy,
            contract=contract,
            plans=(_OrderPlan(intent, instrument, fixture),),
        )[0]

    def simulate_schedule(
        self,
        *,
        run: ExecutionSimulationRunManifest,
        dataset: HistoricalReplayDataset,
        policy: ExecutionSimulationPolicy,
        instruments: tuple[ExecutionInstrument, ...],
        intents: tuple[SimulationOrderIntent, ...],
        contract: NautilusEquivalenceContract = (
            MULTI_INSTRUMENT_SCHEDULE_CONTRACT
        ),
    ) -> tuple[NautilusExecutionResult, ...]:
        """Run several single-instrument orders in one Nautilus backtest."""

        if (
            contract.behavior
            is not NautilusDifferentialBehavior.MULTI_INSTRUMENT_SCHEDULE
        ):
            raise ValueError(
                "simulate_schedule requires the MULTI_INSTRUMENT_SCHEDULE "
                "contract"
            )
        if len(intents) < 2:
            raise ValueError(
                "MULTI_INSTRUMENT_SCHEDULE requires at least two orders"
            )
        by_instrument = {
            item.execution_instrument_id: item for item in instruments
        }
        if len(by_instrument) != len(instruments):
            raise ValueError("duplicate execution instruments supplied")
        order_instruments = [
            item.execution_instrument_id for item in intents
        ]
        if len(order_instruments) != len(set(order_instruments)):
            raise ValueError(
                "MULTI_INSTRUMENT_SCHEDULE allows one order per instrument; "
                "orders sharing a book are outside every frozen contract"
            )
        plans: list[_OrderPlan] = []
        for intent in sorted(
            intents,
            key=lambda item: (item.submitted_at, item.intent_id),
        ):
            instrument = by_instrument.get(intent.execution_instrument_id)
            if instrument is None:
                raise ValueError(
                    "schedule order instrument is not supplied"
                )
            fixture = self._validate_scope(
                run=run,
                dataset=dataset,
                policy=policy,
                instrument=instrument,
                intent=intent,
                contract=contract,
            )
            plans.append(_OrderPlan(intent, instrument, fixture))
        venues = {plan.instrument.venue_id for plan in plans}
        currencies = {plan.instrument.quote_currency for plan in plans}
        if len(venues) != 1 or len(currencies) != 1:
            raise ValueError(
                "MULTI_INSTRUMENT_SCHEDULE requires one venue and currency"
            )
        return self._run_backtest(
            run=run,
            policy=policy,
            contract=contract,
            plans=tuple(plans),
        )

    def _run_backtest(
        self,
        *,
        run: ExecutionSimulationRunManifest,
        policy: ExecutionSimulationPolicy,
        contract: NautilusEquivalenceContract,
        plans: tuple["_OrderPlan", ...],
    ) -> tuple[NautilusExecutionResult, ...]:
        from nautilus_trader.backtest import BacktestEngine
        from nautilus_trader.common import LogLevel
        from nautilus_trader.config import (
            BacktestEngineConfig,
            LoggerConfig,
            StrategyConfig,
        )
        from nautilus_trader.execution import (
            MakerTakerFeeModel,
            StaticLatencyModel,
        )
        from nautilus_trader.model import (
            AccountType,
            BookType,
            Currency as NTCurrency,
            Equity,
            InstrumentId,
            Money,
            OmsType,
            OrderSide,
            Price,
            Quantity,
            QuoteTick,
            Symbol,
            TimeInForce as NTTimeInForce,
            Venue,
        )
        from nautilus_trader.trading import Strategy

        first = plans[0].instrument
        nt_currency = NTCurrency.from_str(first.quote_currency.value)
        if nt_currency.precision != CURRENCY_PRECISION[first.quote_currency]:
            raise ValueError(
                "Nautilus currency precision differs from frozen fee table"
            )
        fee_rate = policy.commission_bps / Decimal("10000")
        order_latency_ns = policy.order_latency_ms * 1_000_000
        nt_tifs = {
            TimeInForce.DAY: NTTimeInForce.DAY,
            TimeInForce.GTC: NTTimeInForce.GTC,
            TimeInForce.IOC: NTTimeInForce.IOC,
            TimeInForce.FOK: NTTimeInForce.FOK,
        }

        nt_instruments = []
        nt_quotes = []
        specs: list[dict[str, object]] = []
        for plan in plans:
            instrument = plan.instrument
            nt_id = InstrumentId.from_str(
                f"{instrument.venue_symbol}.{instrument.venue_id}"
            )
            nt_instruments.append(
                Equity(
                    instrument_id=nt_id,
                    raw_symbol=Symbol(instrument.venue_symbol),
                    currency=nt_currency,
                    price_precision=_decimal_precision(
                        instrument.price_increment
                    ),
                    price_increment=Price.from_str(
                        _plain_decimal(instrument.price_increment)
                    ),
                    ts_event=0,
                    ts_init=0,
                    lot_size=Quantity.from_int(1),
                    min_quantity=Quantity.from_int(
                        int(instrument.minimum_quantity)
                    ),
                    maker_fee=fee_rate,
                    taker_fee=fee_rate,
                )
            )
            nt_quotes.extend(
                QuoteTick(
                    instrument_id=nt_id,
                    bid_price=Price.from_str(
                        _plain_decimal(event.bid_price)
                    ),
                    ask_price=Price.from_str(
                        _plain_decimal(event.ask_price)
                    ),
                    bid_size=Quantity.from_int(int(event.bid_quantity)),
                    ask_size=Quantity.from_int(int(event.ask_quantity)),
                    ts_event=_unix_nanos(event.event_time),
                    ts_init=_unix_nanos(available_at),
                )
                for event, available_at in plan.fixture.arrivals
            )
            specs.append(
                {
                    "instrument_id": nt_id,
                    "instrument_key": str(nt_id),
                    "quantity": plan.intent.quantity,
                    "limit_price": plan.intent.limit_price,
                    "order_type": plan.intent.order_type,
                    "order_side": (
                        OrderSide.BUY
                        if plan.intent.side is ExecutionSide.BUY
                        else OrderSide.SELL
                    ),
                    "time_in_force": nt_tifs[plan.intent.time_in_force],
                    "submit_ns": _unix_nanos(plan.intent.submitted_at),
                }
            )
        nt_quotes.sort(key=lambda tick: int(tick.ts_init))

        class _OrderRecord:
            def __init__(self) -> None:
                self.submitted = False
                self.accepted = False
                self.submission_tick_ns: int | None = None
                self.fill_times_ns: list[int] = []
                self.fill_prices: list[Decimal] = []
                self.fill_quantities: list[Decimal] = []
                self.fill_fees: list[Decimal] = []
                self.fill_liquidity_sides: list[str] = []
                self.terminal_state: NautilusRawTerminalState | None = None

        class _ScheduleConfig(StrategyConfig):
            def __init__(self, **_kwargs: object) -> None:
                super().__init__()

        class _OrderScheduleStrategy(Strategy):
            def __init__(self) -> None:
                super().__init__(_ScheduleConfig())
                self.records = [_OrderRecord() for _ in specs]
                self.by_client_order_id: dict[str, int] = {}

            def on_start(self) -> None:
                for spec in specs:
                    self.subscribe_quotes(spec["instrument_id"])

            def on_quote(self, tick) -> None:
                tick_ns = int(tick.ts_init)
                key = str(tick.instrument_id)
                for index, spec in enumerate(specs):
                    record = self.records[index]
                    if (
                        record.submitted
                        or spec["instrument_key"] != key
                        or spec["submit_ns"] != tick_ns
                    ):
                        continue
                    self._submit(index, spec, record, tick_ns)

            def _submit(self, index, spec, record, tick_ns) -> None:
                instrument_obj = self.cache.instrument(
                    spec["instrument_id"]
                )
                if instrument_obj is None:
                    raise RuntimeError(
                        "Nautilus cache missing mapped equity instrument"
                    )
                quantity = instrument_obj.make_qty(spec["quantity"])
                if spec["order_type"] is ExecutionOrderType.MARKET:
                    order = self.order_factory.market(
                        spec["instrument_id"],
                        spec["order_side"],
                        quantity,
                        time_in_force=spec["time_in_force"],
                    )
                else:
                    order = self.order_factory.limit(
                        spec["instrument_id"],
                        spec["order_side"],
                        quantity,
                        instrument_obj.make_price(spec["limit_price"]),
                        time_in_force=spec["time_in_force"],
                    )
                record.submitted = True
                record.submission_tick_ns = tick_ns
                self.by_client_order_id[str(order.client_order_id)] = index
                self.submit_order(order)

            def _record(self, event) -> _OrderRecord:
                return self.records[
                    self.by_client_order_id[str(event.client_order_id)]
                ]

            def on_order_accepted(self, event) -> None:
                self._record(event).accepted = True

            def on_order_filled(self, event) -> None:
                record = self._record(event)
                record.fill_times_ns.append(int(event.ts_event))
                record.fill_quantities.append(Decimal(str(event.last_qty)))
                record.fill_prices.append(Decimal(str(event.last_px)))
                record.fill_fees.append(
                    Decimal(str(event.commission.as_decimal()))
                )
                record.fill_liquidity_sides.append(str(event.liquidity_side))

            def on_order_rejected(self, event) -> None:
                self._record(event).terminal_state = (
                    NautilusRawTerminalState.REJECTED
                )

            def on_order_canceled(self, event) -> None:
                self._record(event).terminal_state = (
                    NautilusRawTerminalState.CANCELED
                )

            def on_order_expired(self, event) -> None:
                self._record(event).terminal_state = (
                    NautilusRawTerminalState.EXPIRED
                )

        strategy = _OrderScheduleStrategy()
        config_fingerprint = _content_id(
            "nautilus-backtest-config",
            {
                "adapter_version": ADAPTER_VERSION,
                "nautilus_version": self.engine_version,
                "contract_id": contract.contract_id,
                "venue_id": first.venue_id,
                "instruments": sorted(
                    plan.instrument.execution_instrument_id
                    for plan in plans
                ),
                "oms_type": "NETTING",
                "account_type": "MARGIN",
                "base_currency": first.quote_currency.value,
                "starting_balance": "1000000",
                "book_type": "L1_MBP",
                "trade_execution": False,
                "liquidity_consumption": True,
                "queue_position": False,
                "maker_fee": str(fee_rate),
                "taker_fee": str(fee_rate),
                "base_latency_nanos": 0,
                "insert_latency_nanos": order_latency_ns,
                "market_data_shift_nanos": (
                    policy.market_latency_ms * 1_000_000
                ),
            },
        )

        venue_options: dict[str, object] = {}
        if order_latency_ns:
            venue_options["latency_model"] = StaticLatencyModel(
                base_latency_nanos=0,
                insert_latency_nanos=order_latency_ns,
                update_latency_nanos=order_latency_ns,
                cancel_latency_nanos=order_latency_ns,
            )

        engine = BacktestEngine(
            config=BacktestEngineConfig(
                logging=LoggerConfig(
                    stdout_level=LogLevel.ERROR,
                ),
            )
        )
        try:
            engine.add_venue(
                venue=Venue(first.venue_id),
                oms_type=OmsType.NETTING,
                account_type=AccountType.MARGIN,
                base_currency=nt_currency,
                starting_balances=[
                    Money(1_000_000, nt_currency),
                ],
                default_leverage=Decimal("10"),
                fee_model=MakerTakerFeeModel(),
                book_type=BookType.L1_MBP,
                trade_execution=False,
                liquidity_consumption=True,
                queue_position=False,
                **venue_options,
            )
            for nt_instrument in nt_instruments:
                engine.add_instrument(nt_instrument)
            engine.add_data(nt_quotes)
            engine.add_strategy(strategy)
            engine.run()
        except Exception as exc:
            raise ValueError(
                f"Nautilus historical backtest failed: {exc}"
            ) from exc
        finally:
            engine.dispose()

        return tuple(
            self._result(
                run=run,
                policy=policy,
                contract=contract,
                plan=plan,
                record=record,
                submit_ns=spec["submit_ns"],
                fee_rate=fee_rate,
                config_fingerprint=config_fingerprint,
            )
            for plan, record, spec in zip(plans, strategy.records, specs)
        )

    def _result(
        self,
        *,
        run: ExecutionSimulationRunManifest,
        policy: ExecutionSimulationPolicy,
        contract: NautilusEquivalenceContract,
        plan: "_OrderPlan",
        record,
        submit_ns: int,
        fee_rate: Decimal,
        config_fingerprint: str,
    ) -> NautilusExecutionResult:
        intent = plan.intent
        instrument = plan.instrument
        fixture = plan.fixture
        if not record.submitted:
            raise ValueError(
                "Nautilus strategy did not observe the exact submission quote"
            )
        if record.submission_tick_ns != submit_ns:
            raise ValueError(
                "Nautilus order was not submitted on exact PIT trigger"
            )

        filled = sum(record.fill_quantities, Decimal("0"))
        remaining = intent.quantity - filled
        mapping_notes: list[str] = []
        if filled == intent.quantity:
            raw_state = NautilusRawTerminalState.FILLED
            final_state = SimulationOrderState.FILLED
        elif record.terminal_state is not None:
            raw_state = record.terminal_state
            if (
                raw_state is NautilusRawTerminalState.CANCELED
                and intent.time_in_force in _IMMEDIATE_TIME_IN_FORCE
            ):
                final_state = SimulationOrderState.EXPIRED
                mapping_notes.append(
                    "Nautilus venue cancel of "
                    + intent.time_in_force.value
                    + " remainder mapped to EXPIRED"
                )
            else:
                final_state = SimulationOrderState(raw_state.value)
        else:
            if intent.time_in_force not in _RESTING_TIME_IN_FORCE:
                raise ValueError(
                    "Nautilus immediate order ended without a terminal event"
                )
            if (
                intent.order_type is ExecutionOrderType.LIMIT
                and not record.accepted
            ):
                raise ValueError(
                    "Nautilus limit order was never accepted by the venue"
                )
            if (
                intent.order_type is ExecutionOrderType.MARKET
                and not record.fill_quantities
            ):
                raise ValueError(
                    "Nautilus market order neither filled nor terminated"
                )
            raw_state = NautilusRawTerminalState.OPEN_AT_HORIZON
            final_state = SimulationOrderState.EXPIRED
            mapping_notes.append(
                "order working when replay data ended mapped to EXPIRED "
                "by First Current horizon rule"
            )

        vwap = (
            sum(
                (
                    quantity * price
                    for quantity, price in zip(
                        record.fill_quantities,
                        record.fill_prices,
                    )
                ),
                Decimal("0"),
            )
            / filled
            if filled > 0
            else None
        )
        total_fees = sum(record.fill_fees, Decimal("0"))
        fill_times = tuple(
            _from_unix_nanos(item) for item in record.fill_times_ns
        )
        source_quote_event_ids = tuple(
            dict.fromkeys(
                (
                    fixture.submission_quote.event_id,
                    fixture.activation_quote.event_id,
                )
            )
        )
        diagnostics = (
            "historical BacktestEngine only",
            "equivalence contract "
            + contract.behavior.value
            + " (stage "
            + contract.stage
            + ")",
            "L1_MBP quote-driven matching",
            "trade_execution disabled",
            "liquidity_consumption enabled",
            "queue_position disabled",
            "maker/taker fee rate " + str(fee_rate),
            "order latency " + str(policy.order_latency_ms) + "ms",
            "market data latency " + str(policy.market_latency_ms) + "ms",
            "no live node or venue adapter imported",
            "exact submission quote " + fixture.submission_quote.event_id,
            "activation quote " + fixture.activation_quote.event_id,
        ) + tuple(mapping_notes)
        result = NautilusExecutionResult(
            result_id="",
            run_id=run.run_id,
            intent_id=intent.intent_id,
            execution_instrument_id=(
                instrument.execution_instrument_id
            ),
            engine_name=self.ENGINE_NAME,
            engine_version=self.engine_version,
            adapter_version=ADAPTER_VERSION,
            contract_id=contract.contract_id,
            behavior=contract.behavior,
            config_fingerprint=config_fingerprint,
            final_state=final_state,
            raw_terminal_state=raw_state,
            fill_count=len(record.fill_prices),
            filled_quantity=filled,
            remaining_quantity=remaining,
            volume_weighted_average_price=vwap,
            total_fees=total_fees,
            fill_times=fill_times,
            fill_prices=tuple(record.fill_prices),
            fill_quantities=tuple(record.fill_quantities),
            fill_fees=tuple(record.fill_fees),
            fill_liquidity_sides=tuple(record.fill_liquidity_sides),
            source_quote_event_ids=source_quote_event_ids,
            diagnostics=diagnostics,
            network_authority="NONE",
            external_order_authority="NONE",
            capital_authority="NONE",
        )
        return _with_result_identity(result)

    def _validate_scope(
        self,
        *,
        run: ExecutionSimulationRunManifest,
        dataset: HistoricalReplayDataset,
        policy: ExecutionSimulationPolicy,
        instrument: ExecutionInstrument,
        intent: SimulationOrderIntent,
        contract: NautilusEquivalenceContract,
    ) -> _ScopedFixture:
        _require_frozen_contract(contract)
        if run.run_id != execution_simulation_run_identity(run):
            raise ValueError(
                "Nautilus execution simulation run identity mismatch"
            )
        if run.engine_name != self.ENGINE_NAME:
            raise ValueError(
                "Nautilus adapter requires NAUTILUS_TRADER run"
            )
        if run.engine_version != self.engine_version:
            raise ValueError(
                "Nautilus run engine version differs from installed package"
            )
        if (
            run.network_authority != "NONE"
            or run.external_order_authority != "NONE"
            or run.capital_authority != "NONE"
        ):
            raise ValueError(
                "Nautilus historical run carries external authority"
            )
        if (
            dataset.dataset_id
            != historical_replay_dataset_identity(dataset)
        ):
            raise ValueError(
                "Nautilus replay dataset identity mismatch"
            )
        if run.replay_dataset_id != dataset.dataset_id:
            raise ValueError(
                "Nautilus run binds another replay dataset"
            )
        if run.simulation_policy_id != policy.policy_id:
            raise ValueError(
                "Nautilus run binds another simulation policy"
            )
        if intent.intent_id != simulation_order_intent_identity(intent):
            raise ValueError(
                "Nautilus simulation order intent identity mismatch"
            )
        if intent.run_id != run.run_id:
            raise ValueError(
                "Nautilus intent belongs to another run"
            )
        if (
            intent.execution_instrument_id
            != instrument.execution_instrument_id
        ):
            raise ValueError(
                "Nautilus intent belongs to another instrument"
            )
        if (
            intent.simulation_authority != "HISTORICAL_REPLAY_ONLY"
            or intent.external_order_authority != "NONE"
            or intent.capital_authority != "NONE"
        ):
            raise ValueError(
                "Nautilus intent carries invalid authority"
            )
        if instrument.asset_class is not ExecutionAssetClass.EQUITY:
            raise ValueError(
                "Nautilus differential supports equity only"
            )
        if (
            instrument.quantity_increment != Decimal("1")
            or instrument.contract_multiplier != Decimal("1")
            or instrument.minimum_quantity < Decimal("1")
        ):
            raise ValueError(
                "Nautilus equity mapping requires whole shares "
                "with unit multiplier"
            )
        if "." in instrument.venue_symbol or "." in instrument.venue_id:
            raise ValueError(
                "Nautilus differential fixture symbol/venue cannot contain '.'"
            )
        if intent.submitted_at < dataset.start_time:
            raise ValueError(
                "Nautilus order submitted before replay dataset start"
            )

        behavior = contract.behavior
        self._validate_policy(policy=policy, behavior=behavior)

        arrivals = _quote_arrivals(
            dataset=dataset,
            instrument=instrument,
            market_latency_ms=policy.market_latency_ms,
        )
        submission_quote = _single_arrival(
            arrivals,
            intent.submitted_at,
            "exactly one quote must arrive at order submission time",
        )
        active_at = intent.submitted_at + timedelta(
            milliseconds=policy.order_latency_ms
        )
        if active_at > dataset.end_time:
            raise ValueError(
                "order activation lies outside the replay horizon"
            )
        activation_quote = (
            _single_arrival(
                arrivals,
                active_at,
                "ORDER_LATENCY requires exactly one quote at order "
                "activation time; Nautilus matches in-flight orders only "
                "on the next data arrival",
            )
            if behavior is NautilusDifferentialBehavior.ORDER_LATENCY
            else submission_quote
        )
        touch_price, touch_quantity = _contra_touch(
            intent.side,
            activation_quote,
        )
        marketable = _is_marketable(intent, touch_price)
        full_liquidity = touch_quantity >= intent.quantity

        if behavior is NautilusDifferentialBehavior.IMMEDIATE_TIME_IN_FORCE:
            if intent.time_in_force not in _IMMEDIATE_TIME_IN_FORCE:
                raise ValueError(
                    "IMMEDIATE_TIME_IN_FORCE contract requires IOC or FOK"
                )
            if marketable and full_liquidity:
                raise ValueError(
                    "IMMEDIATE_TIME_IN_FORCE fixture fills completely; use "
                    "the zero-friction contract"
                )
            if (
                marketable
                and intent.time_in_force is TimeInForce.IOC
                and not policy.allow_partial_fills
            ):
                raise ValueError(
                    "IOC shortfall with partial fills disabled is a known "
                    "reference/Nautilus divergence"
                )
        elif behavior is NautilusDifferentialBehavior.LIMIT_TRANSITION:
            self._validate_limit_transition(
                dataset=dataset,
                intent=intent,
                arrivals=arrivals,
                active_at=active_at,
                marketable=marketable,
            )
        else:
            if not full_liquidity:
                raise ValueError(
                    "exact differential requires full displayed liquidity"
                )
            if not marketable:
                raise ValueError(
                    "exact differential supports only marketable limits "
                    "outside the LIMIT_TRANSITION contract"
                )
            if behavior is NautilusDifferentialBehavior.DETERMINISTIC_FEES:
                _require_exact_commission(
                    quantity=intent.quantity,
                    price=touch_price,
                    commission_bps=policy.commission_bps,
                    currency=instrument.quote_currency,
                )
        return _ScopedFixture(
            submission_quote=submission_quote,
            activation_quote=activation_quote,
            arrivals=arrivals,
        )

    @staticmethod
    def _validate_policy(
        *,
        policy: ExecutionSimulationPolicy,
        behavior: NautilusDifferentialBehavior,
    ) -> None:
        if (
            policy.slippage_bps != Decimal("0")
            or policy.market_impact_bps != Decimal("0")
        ):
            raise ValueError(
                "Nautilus differential requires zero slippage and impact"
            )
        if policy.maximum_participation_rate != Decimal("1"):
            raise ValueError(
                "Nautilus differential requires full participation"
            )
        exercised = {
            NautilusDifferentialBehavior.DETERMINISTIC_FEES: (
                policy.commission_bps != Decimal("0")
            ),
            NautilusDifferentialBehavior.ORDER_LATENCY: (
                policy.order_latency_ms != 0
            ),
            NautilusDifferentialBehavior.MARKET_DATA_LATENCY: (
                policy.market_latency_ms != 0
            ),
        }
        for other, active in exercised.items():
            if other is behavior and not active:
                raise ValueError(
                    behavior.value
                    + " contract requires the behavior it names"
                )
            if other is not behavior and active:
                raise ValueError(
                    other.value
                    + " is outside the "
                    + behavior.value
                    + " equivalence contract"
                )
        if (
            policy.allow_partial_fills
            and behavior
            is not NautilusDifferentialBehavior.IMMEDIATE_TIME_IN_FORCE
        ):
            raise ValueError(
                "partial fills are outside the "
                + behavior.value
                + " equivalence contract"
            )

    @staticmethod
    def _validate_limit_transition(
        *,
        dataset: HistoricalReplayDataset,
        intent: SimulationOrderIntent,
        arrivals: tuple[tuple[TopOfBookQuote, datetime], ...],
        active_at: datetime,
        marketable: bool,
    ) -> None:
        if intent.order_type is not ExecutionOrderType.LIMIT:
            raise ValueError(
                "LIMIT_TRANSITION contract requires a limit order"
            )
        if intent.time_in_force not in _RESTING_TIME_IN_FORCE:
            raise ValueError(
                "LIMIT_TRANSITION contract requires DAY or GTC"
            )
        if marketable:
            raise ValueError(
                "LIMIT_TRANSITION requires a non-marketable limit at "
                "submission"
            )
        if intent.time_in_force is TimeInForce.DAY and (
            intent.submitted_at.astimezone(timezone.utc).date()
            != dataset.end_time.astimezone(timezone.utc).date()
        ):
            raise ValueError(
                "LIMIT_TRANSITION DAY fixture crosses a UTC date boundary"
            )
        assert intent.limit_price is not None
        for quote, available_at in arrivals:
            if available_at <= active_at:
                continue
            touch_price, touch_quantity = _contra_touch(
                intent.side,
                quote,
            )
            if not _is_marketable(intent, touch_price):
                continue
            if touch_price != intent.limit_price:
                raise ValueError(
                    "LIMIT_TRANSITION later book crosses beyond the limit; "
                    "Nautilus fills at the limit, the reference at the touch"
                )
            if touch_quantity < intent.quantity:
                raise ValueError(
                    "LIMIT_TRANSITION requires full displayed liquidity on "
                    "the transition quote"
                )
            return


class NautilusDifferentialEngine:
    """Exact differential gate for frozen reference/Nautilus overlaps."""

    def compare(
        self,
        *,
        reference_run: ExecutionSimulationRunManifest,
        reference_intent: SimulationOrderIntent,
        reference_result: ReferenceExecutionResult,
        reference_fills: tuple[SimulatedFill, ...],
        nautilus_run: ExecutionSimulationRunManifest,
        nautilus_intent: SimulationOrderIntent,
        nautilus_result: NautilusExecutionResult,
    ) -> NautilusDifferentialResult:
        if (
            reference_result.result_id
            != reference_execution_result_identity(reference_result)
        ):
            raise ValueError(
                "reference execution result identity mismatch"
            )
        if (
            nautilus_result.result_id
            != nautilus_execution_result_identity(nautilus_result)
        ):
            raise ValueError(
                "Nautilus execution result identity mismatch"
            )
        contract = NAUTILUS_EQUIVALENCE_CONTRACTS.get(
            nautilus_result.behavior
        )
        if (
            contract is None
            or contract.contract_id != nautilus_result.contract_id
        ):
            raise ValueError(
                "Nautilus result is not bound to a frozen equivalence contract"
            )
        for run in (reference_run, nautilus_run):
            if run.run_id != execution_simulation_run_identity(run):
                raise ValueError(
                    "differential simulation run identity mismatch"
                )
        for intent in (reference_intent, nautilus_intent):
            if intent.intent_id != simulation_order_intent_identity(intent):
                raise ValueError(
                    "differential intent identity mismatch"
                )
        if reference_result.run_id != reference_run.run_id:
            raise ValueError(
                "reference result belongs to another run"
            )
        if nautilus_result.run_id != nautilus_run.run_id:
            raise ValueError(
                "Nautilus result belongs to another run"
            )
        if reference_result.intent_id != reference_intent.intent_id:
            raise ValueError(
                "reference result belongs to another intent"
            )
        if nautilus_result.intent_id != nautilus_intent.intent_id:
            raise ValueError(
                "Nautilus result belongs to another intent"
            )
        if reference_run.engine_name != "FIRST_CURRENT_REFERENCE":
            raise ValueError(
                "differential reference run is not First Current reference"
            )
        if nautilus_run.engine_name != "NAUTILUS_TRADER":
            raise ValueError(
                "differential comparison run is not NautilusTrader"
            )
        if (
            reference_run.replay_dataset_id
            != nautilus_run.replay_dataset_id
            or reference_run.simulation_policy_id
            != nautilus_run.simulation_policy_id
            or reference_run.research_run_manifest_id
            != nautilus_run.research_run_manifest_id
            or reference_run.portfolio_solution_id
            != nautilus_run.portfolio_solution_id
        ):
            raise ValueError(
                "differential runs do not share exact research inputs"
            )
        for fill in reference_fills:
            if fill.fill_id != simulated_fill_identity(fill):
                raise ValueError(
                    "reference fill identity mismatch"
                )
            if fill.intent_id != reference_intent.intent_id:
                raise ValueError(
                    "reference fill belongs to another intent"
                )
        if (
            tuple(fill.fill_id for fill in reference_fills)
            != reference_result.fill_ids
        ):
            raise ValueError(
                "reference fills differ from reference result fill ids"
            )
        reference_economics = _economic_intent_payload(
            reference_intent
        )
        nautilus_economics = _economic_intent_payload(
            nautilus_intent
        )
        if reference_economics != nautilus_economics:
            raise ValueError(
                "differential order intents differ economically"
            )
        economics_fingerprint = _content_id(
            "execution-economic-intent",
            reference_economics,
        )

        final_state_match = (
            reference_result.final_state
            is nautilus_result.final_state
        )
        fill_count_match = (
            len(reference_result.fill_ids)
            == nautilus_result.fill_count
        )
        quantity_error = abs(
            reference_result.filled_quantity
            - nautilus_result.filled_quantity
        )
        quantity_match = quantity_error == 0
        if (
            reference_result.volume_weighted_average_price is None
            or nautilus_result.volume_weighted_average_price is None
        ):
            price_match = (
                reference_result.volume_weighted_average_price
                is nautilus_result.volume_weighted_average_price
            )
            price_error = None
        else:
            price_error = abs(
                reference_result.volume_weighted_average_price
                - nautilus_result.volume_weighted_average_price
            )
            price_match = price_error == 0
        fee_match = (
            reference_result.total_fees == nautilus_result.total_fees
        )
        sequence_mismatches = _fill_sequence_mismatches(
            reference_fills,
            nautilus_result,
        )
        fill_sequence_match = not sequence_mismatches

        matched = (
            final_state_match
            and fill_count_match
            and quantity_match
            and price_match
            and fee_match
            and fill_sequence_match
        )
        state = (
            DifferentialState.MATCH
            if matched
            else DifferentialState.MISMATCH
        )
        diagnostics = (
            "equivalence contract "
            + contract.behavior.value
            + " (stage "
            + contract.stage
            + ")",
            "First Current reference is independent differential oracle",
            "Nautilus live adapters are outside Stage 12 scope",
        ) + sequence_mismatches
        result = NautilusDifferentialResult(
            differential_id="",
            reference_result_id=reference_result.result_id,
            nautilus_result_id=nautilus_result.result_id,
            reference_run_id=reference_run.run_id,
            nautilus_run_id=nautilus_run.run_id,
            replay_dataset_id=reference_run.replay_dataset_id,
            simulation_policy_id=reference_run.simulation_policy_id,
            economic_intent_fingerprint=economics_fingerprint,
            contract_id=contract.contract_id,
            behavior=contract.behavior,
            nautilus_version=nautilus_result.engine_version,
            adapter_version=ADAPTER_VERSION,
            state=state,
            final_state_match=final_state_match,
            fill_count_match=fill_count_match,
            quantity_match=quantity_match,
            price_match=price_match,
            fee_match=fee_match,
            fill_sequence_match=fill_sequence_match,
            reference_final_state=reference_result.final_state,
            nautilus_final_state=nautilus_result.final_state,
            nautilus_raw_terminal_state=(
                nautilus_result.raw_terminal_state
            ),
            reference_filled_quantity=reference_result.filled_quantity,
            nautilus_filled_quantity=nautilus_result.filled_quantity,
            quantity_error=quantity_error,
            reference_vwap=(
                reference_result.volume_weighted_average_price
            ),
            nautilus_vwap=(
                nautilus_result.volume_weighted_average_price
            ),
            absolute_vwap_error=price_error,
            reference_total_fees=reference_result.total_fees,
            nautilus_total_fees=nautilus_result.total_fees,
            diagnostics=diagnostics,
            trust_authority=(
                "REFERENCE_MATCH_ONLY"
                if matched
                else "NONE"
            ),
            network_authority="NONE",
            external_order_authority="NONE",
            capital_authority="NONE",
        )
        return _with_differential_identity(result)


    def compare_schedule(
        self,
        *,
        order_differentials: tuple[NautilusDifferentialResult, ...],
        nautilus_results: tuple[NautilusExecutionResult, ...],
    ) -> NautilusScheduleDifferentialResult:
        """Aggregate per-order differentials from one schedule backtest.

        The schedule matches only if every order matches; one mismatch keeps
        the whole schedule at MISMATCH with no trust authority.
        """

        if len(order_differentials) < 2:
            raise ValueError(
                "schedule differential requires at least two orders"
            )
        if len(order_differentials) != len(nautilus_results):
            raise ValueError(
                "schedule differential needs one Nautilus result per order"
            )
        results_by_id = {
            item.result_id: item for item in nautilus_results
        }
        for result in nautilus_results:
            if result.result_id != nautilus_execution_result_identity(result):
                raise ValueError(
                    "Nautilus execution result identity mismatch"
                )
        for item in order_differentials:
            if item.differential_id != nautilus_differential_result_identity(
                item
            ):
                raise ValueError(
                    "Nautilus differential result identity mismatch"
                )
            if (
                item.behavior
                is not NautilusDifferentialBehavior.MULTI_INSTRUMENT_SCHEDULE
                or item.contract_id
                != MULTI_INSTRUMENT_SCHEDULE_CONTRACT.contract_id
            ):
                raise ValueError(
                    "order differential is not bound to the schedule contract"
                )
            if item.nautilus_result_id not in results_by_id:
                raise ValueError(
                    "order differential references an unknown Nautilus result"
                )
        if len({item.nautilus_result_id for item in order_differentials}) != len(
            order_differentials
        ):
            raise ValueError("schedule differential repeats an order")
        if (
            len({item.replay_dataset_id for item in order_differentials}) != 1
            or len({item.simulation_policy_id for item in order_differentials})
            != 1
            or len({item.nautilus_run_id for item in order_differentials}) != 1
            or len(
                {
                    results_by_id[item.nautilus_result_id].config_fingerprint
                    for item in order_differentials
                }
            )
            != 1
        ):
            raise ValueError(
                "schedule order differentials come from different backtests"
            )
        instruments = tuple(
            sorted(
                results_by_id[item.nautilus_result_id].execution_instrument_id
                for item in order_differentials
            )
        )
        if len(set(instruments)) != len(instruments):
            raise ValueError(
                "schedule differential repeats an instrument"
            )
        ordered = tuple(
            sorted(item.differential_id for item in order_differentials)
        )
        mismatched = tuple(
            sorted(
                item.differential_id
                for item in order_differentials
                if item.state is not DifferentialState.MATCH
            )
        )
        state = (
            DifferentialState.MISMATCH
            if mismatched
            else DifferentialState.MATCH
        )
        first = order_differentials[0]
        payload = {
            "replay_dataset_id": first.replay_dataset_id,
            "simulation_policy_id": first.simulation_policy_id,
            "contract_id": first.contract_id,
            "order_differential_ids": list(ordered),
            "execution_instrument_ids": list(instruments),
            "state": state.value,
            "mismatched_differential_ids": list(mismatched),
            "trust_authority": (
                "REFERENCE_MATCH_ONLY" if not mismatched else "NONE"
            ),
            "network_authority": "NONE",
            "external_order_authority": "NONE",
            "capital_authority": "NONE",
        }
        return NautilusScheduleDifferentialResult(
            schedule_differential_id=_content_id(
                "nautilus-schedule-differential",
                payload,
            ),
            replay_dataset_id=first.replay_dataset_id,
            simulation_policy_id=first.simulation_policy_id,
            contract_id=first.contract_id,
            order_differential_ids=ordered,
            execution_instrument_ids=instruments,
            state=state,
            mismatched_differential_ids=mismatched,
            trust_authority=payload["trust_authority"],
            network_authority="NONE",
            external_order_authority="NONE",
            capital_authority="NONE",
        )


class NautilusDifferentialStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS nautilus_execution_differentials (
                differential_id VARCHAR PRIMARY KEY,
                reference_result_id VARCHAR NOT NULL,
                nautilus_result_id VARCHAR NOT NULL,
                replay_dataset_id VARCHAR NOT NULL,
                state VARCHAR NOT NULL,
                nautilus_version VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, result: NautilusDifferentialResult) -> bool:
        if (
            result.differential_id
            != nautilus_differential_result_identity(result)
        ):
            raise ValueError(
                "Nautilus differential result identity mismatch"
            )
        payload = json.dumps(
            nautilus_differential_result_payload(result),
            sort_keys=True,
            separators=(",", ":"),
        )
        row = self._con.execute(
            """
            SELECT payload_json
            FROM nautilus_execution_differentials
            WHERE differential_id = ?
            """,
            [result.differential_id],
        ).fetchone()
        if row is not None:
            if str(row[0]) != payload:
                raise ValueError(
                    "Nautilus differential identity conflict"
                )
            return False
        self._con.execute(
            """
            INSERT INTO nautilus_execution_differentials
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                result.differential_id,
                result.reference_result_id,
                result.nautilus_result_id,
                result.replay_dataset_id,
                result.state.value,
                result.nautilus_version,
                payload,
            ],
        )
        return True

    def close(self) -> None:
        self._con.close()


def nautilus_equivalence_contract_payload(
    contract: NautilusEquivalenceContract,
) -> dict[str, object]:
    return {
        "behavior": contract.behavior.value,
        "stage": contract.stage,
        "scope": list(contract.scope),
        "mapping": list(contract.mapping),
        "compared_fields": list(contract.compared_fields),
        "known_divergences": list(contract.known_divergences),
    }


def nautilus_execution_result_payload(
    result: NautilusExecutionResult,
) -> dict[str, object]:
    return {
        "run_id": result.run_id,
        "intent_id": result.intent_id,
        "execution_instrument_id": result.execution_instrument_id,
        "engine_name": result.engine_name,
        "engine_version": result.engine_version,
        "adapter_version": result.adapter_version,
        "contract_id": result.contract_id,
        "behavior": result.behavior.value,
        "config_fingerprint": result.config_fingerprint,
        "final_state": result.final_state.value,
        "raw_terminal_state": result.raw_terminal_state.value,
        "fill_count": result.fill_count,
        "filled_quantity": str(result.filled_quantity),
        "remaining_quantity": str(result.remaining_quantity),
        "volume_weighted_average_price": (
            str(result.volume_weighted_average_price)
            if result.volume_weighted_average_price is not None
            else None
        ),
        "total_fees": str(result.total_fees),
        "fill_times": [
            item.isoformat() for item in result.fill_times
        ],
        "fill_prices": [
            str(item) for item in result.fill_prices
        ],
        "fill_quantities": [
            str(item) for item in result.fill_quantities
        ],
        "fill_fees": [
            str(item) for item in result.fill_fees
        ],
        "fill_liquidity_sides": list(result.fill_liquidity_sides),
        "source_quote_event_ids": list(
            result.source_quote_event_ids
        ),
        "diagnostics": list(result.diagnostics),
        "network_authority": result.network_authority,
        "external_order_authority": result.external_order_authority,
        "capital_authority": result.capital_authority,
    }


def nautilus_execution_result_identity(
    result: NautilusExecutionResult,
) -> str:
    return _content_id(
        "nautilus-execution-result",
        nautilus_execution_result_payload(result),
    )


def nautilus_differential_result_payload(
    result: NautilusDifferentialResult,
) -> dict[str, object]:
    return {
        "reference_result_id": result.reference_result_id,
        "nautilus_result_id": result.nautilus_result_id,
        "reference_run_id": result.reference_run_id,
        "nautilus_run_id": result.nautilus_run_id,
        "replay_dataset_id": result.replay_dataset_id,
        "simulation_policy_id": result.simulation_policy_id,
        "economic_intent_fingerprint": (
            result.economic_intent_fingerprint
        ),
        "contract_id": result.contract_id,
        "behavior": result.behavior.value,
        "nautilus_version": result.nautilus_version,
        "adapter_version": result.adapter_version,
        "state": result.state.value,
        "final_state_match": result.final_state_match,
        "fill_count_match": result.fill_count_match,
        "quantity_match": result.quantity_match,
        "price_match": result.price_match,
        "fee_match": result.fee_match,
        "fill_sequence_match": result.fill_sequence_match,
        "reference_final_state": result.reference_final_state.value,
        "nautilus_final_state": result.nautilus_final_state.value,
        "nautilus_raw_terminal_state": (
            result.nautilus_raw_terminal_state.value
        ),
        "reference_filled_quantity": str(
            result.reference_filled_quantity
        ),
        "nautilus_filled_quantity": str(
            result.nautilus_filled_quantity
        ),
        "quantity_error": str(result.quantity_error),
        "reference_vwap": (
            str(result.reference_vwap)
            if result.reference_vwap is not None
            else None
        ),
        "nautilus_vwap": (
            str(result.nautilus_vwap)
            if result.nautilus_vwap is not None
            else None
        ),
        "absolute_vwap_error": (
            str(result.absolute_vwap_error)
            if result.absolute_vwap_error is not None
            else None
        ),
        "reference_total_fees": str(result.reference_total_fees),
        "nautilus_total_fees": str(result.nautilus_total_fees),
        "diagnostics": list(result.diagnostics),
        "trust_authority": result.trust_authority,
        "network_authority": result.network_authority,
        "external_order_authority": result.external_order_authority,
        "capital_authority": result.capital_authority,
    }


def nautilus_differential_result_identity(
    result: NautilusDifferentialResult,
) -> str:
    return _content_id(
        "nautilus-execution-differential",
        nautilus_differential_result_payload(result),
    )


def _with_result_identity(
    result: NautilusExecutionResult,
) -> NautilusExecutionResult:
    return replace(
        result,
        result_id=nautilus_execution_result_identity(result),
    )


def _with_differential_identity(
    result: NautilusDifferentialResult,
) -> NautilusDifferentialResult:
    return replace(
        result,
        differential_id=nautilus_differential_result_identity(result),
    )


def _require_frozen_contract(
    contract: NautilusEquivalenceContract,
) -> None:
    frozen = NAUTILUS_EQUIVALENCE_CONTRACTS.get(contract.behavior)
    if frozen is None or frozen != contract:
        raise ValueError(
            "Nautilus differential requires a frozen equivalence contract"
        )


def _quote_arrivals(
    *,
    dataset: HistoricalReplayDataset,
    instrument: ExecutionInstrument,
    market_latency_ms: int,
) -> tuple[tuple[TopOfBookQuote, datetime], ...]:
    latency = timedelta(milliseconds=market_latency_ms)
    arrivals = tuple(
        (event, event.knowledge_time + latency)
        for event in dataset.events
        if isinstance(event, TopOfBookQuote)
        and event.execution_instrument_id
        == instrument.execution_instrument_id
        and event.knowledge_time + latency <= dataset.end_time
    )
    times = [available_at for _, available_at in arrivals]
    if len(times) != len(set(times)):
        raise ValueError(
            "Nautilus differential requires unique quote arrival times"
        )
    if times != sorted(times):
        raise AssertionError(
            "mapped quote arrivals are not monotonic"
        )
    return arrivals


def _single_arrival(
    arrivals: tuple[tuple[TopOfBookQuote, datetime], ...],
    at: datetime,
    message: str,
) -> TopOfBookQuote:
    matches = tuple(
        quote for quote, available_at in arrivals if available_at == at
    )
    if len(matches) != 1:
        raise ValueError(message)
    return matches[0]


def _contra_touch(
    side: ExecutionSide,
    quote: TopOfBookQuote,
) -> tuple[Decimal, Decimal]:
    if side is ExecutionSide.BUY:
        return quote.ask_price, quote.ask_quantity
    return quote.bid_price, quote.bid_quantity


def _is_marketable(
    intent: SimulationOrderIntent,
    touch_price: Decimal,
) -> bool:
    if intent.order_type is ExecutionOrderType.MARKET:
        return True
    assert intent.limit_price is not None
    if intent.side is ExecutionSide.BUY:
        return touch_price <= intent.limit_price
    return touch_price >= intent.limit_price


def _require_exact_commission(
    *,
    quantity: Decimal,
    price: Decimal,
    commission_bps: Decimal,
    currency: Currency,
) -> None:
    precision = CURRENCY_PRECISION.get(currency)
    if precision is None:
        raise ValueError(
            "fee differential currency has no frozen precision"
        )
    commission = quantity * price * commission_bps / Decimal("10000")
    quantum = Decimal(1).scaleb(-precision)
    if commission != commission.quantize(quantum):
        raise ValueError(
            "DETERMINISTIC_FEES requires commission exactly representable "
            "at currency precision; Nautilus would round it"
        )


def _fill_sequence_mismatches(
    reference_fills: tuple[SimulatedFill, ...],
    nautilus_result: NautilusExecutionResult,
) -> tuple[str, ...]:
    nautilus_fills = tuple(
        zip(
            nautilus_result.fill_times,
            nautilus_result.fill_quantities,
            nautilus_result.fill_prices,
            nautilus_result.fill_fees,
        )
    )
    mismatches: list[str] = []
    if len(reference_fills) != len(nautilus_fills):
        mismatches.append(
            "fill sequence length reference="
            + str(len(reference_fills))
            + " nautilus="
            + str(len(nautilus_fills))
        )
    for index, (reference, nautilus) in enumerate(
        zip(reference_fills, nautilus_fills)
    ):
        for name, left, right in (
            ("fill_time", reference.fill_time, nautilus[0]),
            ("quantity", reference.quantity, nautilus[1]),
            ("price", reference.price, nautilus[2]),
            ("fee", reference.fee, nautilus[3]),
        ):
            if left != right:
                mismatches.append(
                    f"fill[{index}] {name} reference="
                    + _render(left)
                    + " nautilus="
                    + _render(right)
                )
    return tuple(mismatches)


def _render(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _economic_intent_payload(
    intent: SimulationOrderIntent,
) -> dict[str, object]:
    return {
        "execution_instrument_id": intent.execution_instrument_id,
        "side": intent.side.value,
        "order_type": intent.order_type.value,
        "quantity": str(intent.quantity),
        "limit_price": (
            str(intent.limit_price)
            if intent.limit_price is not None
            else None
        ),
        "time_in_force": intent.time_in_force.value,
        "submitted_at": intent.submitted_at.isoformat(),
        "source_target_id": intent.source_target_id,
    }


def _decimal_precision(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    return max(0, -int(exponent))


def _plain_decimal(value: Decimal) -> str:
    return format(value, "f")


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _unix_nanos(value: datetime) -> int:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    delta = value.astimezone(timezone.utc) - _EPOCH
    return (
        delta.days * 86_400 * 1_000_000_000
        + delta.seconds * 1_000_000_000
        + delta.microseconds * 1_000
    )


def _from_unix_nanos(value: int) -> datetime:
    if value % 1_000:
        raise ValueError(
            "Nautilus timestamp has sub-microsecond precision"
        )
    return _EPOCH + timedelta(microseconds=value // 1_000)


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
