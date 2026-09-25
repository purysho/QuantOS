from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
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
    SimulationOrderIntent,
    SimulationOrderState,
    TimeInForce,
    TopOfBookQuote,
    execution_simulation_run_identity,
    historical_replay_dataset_identity,
    simulation_order_intent_identity,
)
from .execution_reference import (
    ReferenceExecutionResult,
    reference_execution_result_identity,
)
from .pricing_risk_contracts import Currency


NAUTILUS_DISTRIBUTION = "nautilus_trader"
ADAPTER_VERSION = "12.3"


class DifferentialState(str, Enum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"


@dataclass(frozen=True)
class NautilusExecutionResult:
    result_id: str
    run_id: str
    intent_id: str
    execution_instrument_id: str
    engine_name: str
    engine_version: str
    adapter_version: str
    config_fingerprint: str
    final_state: SimulationOrderState
    fill_count: int
    filled_quantity: Decimal
    remaining_quantity: Decimal
    volume_weighted_average_price: Decimal | None
    fill_prices: tuple[Decimal, ...]
    fill_quantities: tuple[Decimal, ...]
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
    nautilus_version: str
    adapter_version: str
    state: DifferentialState
    final_state_match: bool
    fill_count_match: bool
    quantity_match: bool
    price_match: bool
    reference_final_state: SimulationOrderState
    nautilus_final_state: SimulationOrderState
    reference_filled_quantity: Decimal
    nautilus_filled_quantity: Decimal
    quantity_error: Decimal
    reference_vwap: Decimal | None
    nautilus_vwap: Decimal | None
    absolute_vwap_error: Decimal | None
    diagnostics: tuple[str, ...]
    trust_authority: str
    network_authority: str
    external_order_authority: str
    capital_authority: str


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

    This adapter intentionally supports only the exact overlap where First
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
    ) -> NautilusExecutionResult:
        trigger_quote = self._validate_scope(
            run=run,
            dataset=dataset,
            policy=policy,
            instrument=instrument,
            intent=intent,
        )

        from nautilus_trader.backtest import BacktestEngine
        from nautilus_trader.common import LogLevel
        from nautilus_trader.config import (
            BacktestEngineConfig,
            LoggerConfig,
            StrategyConfig,
        )
        from nautilus_trader.execution import MakerTakerFeeModel
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

        nt_id = InstrumentId.from_str(
            f"{instrument.venue_symbol}.{instrument.venue_id}"
        )
        nt_currency = NTCurrency.from_str(
            instrument.quote_currency.value
        )
        price_precision = _decimal_precision(
            instrument.price_increment
        )
        nt_instrument = Equity(
            instrument_id=nt_id,
            raw_symbol=Symbol(instrument.venue_symbol),
            currency=nt_currency,
            price_precision=price_precision,
            price_increment=Price.from_str(
                _plain_decimal(instrument.price_increment)
            ),
            ts_event=0,
            ts_init=0,
            lot_size=Quantity.from_int(1),
            min_quantity=Quantity.from_int(
                int(instrument.minimum_quantity)
            ),
            maker_fee=Decimal("0"),
            taker_fee=Decimal("0"),
        )

        nt_quotes = [
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
                ts_init=_unix_nanos(event.knowledge_time),
            )
            for event in dataset.events
            if isinstance(event, TopOfBookQuote)
            and event.execution_instrument_id
            == instrument.execution_instrument_id
        ]

        target_ns = _unix_nanos(intent.submitted_at)
        nt_side = (
            OrderSide.BUY
            if intent.side is ExecutionSide.BUY
            else OrderSide.SELL
        )
        nt_tif = {
            TimeInForce.DAY: NTTimeInForce.DAY,
            TimeInForce.GTC: NTTimeInForce.GTC,
            TimeInForce.IOC: NTTimeInForce.IOC,
            TimeInForce.FOK: NTTimeInForce.FOK,
        }[intent.time_in_force]

        class _SingleOrderConfig(StrategyConfig):
            def __init__(self, **_kwargs: object) -> None:
                super().__init__()
                self.instrument_id = nt_id
                self.quantity = intent.quantity
                self.limit_price = intent.limit_price
                self.order_type = intent.order_type
                self.order_side = nt_side
                self.time_in_force = nt_tif
                self.submit_ns = target_ns

        class _SingleOrderStrategy(Strategy):
            def __init__(self) -> None:
                super().__init__(_SingleOrderConfig())
                self.submitted = False
                self.fill_prices: list[Decimal] = []
                self.fill_quantities: list[Decimal] = []
                self.terminal_state: SimulationOrderState | None = None
                self.submission_tick_ns: int | None = None

            def on_start(self) -> None:
                self.subscribe_quotes(self.config.instrument_id)

            def on_quote(self, tick) -> None:
                if self.submitted:
                    return
                tick_ns = int(tick.ts_init)
                if tick_ns < self.config.submit_ns:
                    return
                if tick_ns != self.config.submit_ns:
                    return
                instrument_obj = self.cache.instrument(
                    self.config.instrument_id
                )
                if instrument_obj is None:
                    raise RuntimeError(
                        "Nautilus cache missing mapped equity instrument"
                    )
                quantity = instrument_obj.make_qty(
                    self.config.quantity
                )
                if self.config.order_type is ExecutionOrderType.MARKET:
                    order = self.order_factory.market(
                        self.config.instrument_id,
                        self.config.order_side,
                        quantity,
                        time_in_force=self.config.time_in_force,
                    )
                else:
                    assert self.config.limit_price is not None
                    order = self.order_factory.limit(
                        self.config.instrument_id,
                        self.config.order_side,
                        quantity,
                        instrument_obj.make_price(
                            self.config.limit_price
                        ),
                        time_in_force=self.config.time_in_force,
                    )
                self.submitted = True
                self.submission_tick_ns = tick_ns
                self.submit_order(order)

            def on_order_filled(self, event) -> None:
                self.fill_quantities.append(
                    Decimal(str(event.last_qty))
                )
                self.fill_prices.append(
                    Decimal(str(event.last_px))
                )

            def on_order_rejected(self, event) -> None:
                self.terminal_state = SimulationOrderState.REJECTED

            def on_order_canceled(self, event) -> None:
                self.terminal_state = SimulationOrderState.CANCELED

            def on_order_expired(self, event) -> None:
                self.terminal_state = SimulationOrderState.EXPIRED

        strategy = _SingleOrderStrategy()
        config_fingerprint = _content_id(
            "nautilus-backtest-config",
            {
                "adapter_version": ADAPTER_VERSION,
                "nautilus_version": self.engine_version,
                "venue_id": instrument.venue_id,
                "oms_type": "NETTING",
                "account_type": "MARGIN",
                "base_currency": instrument.quote_currency.value,
                "starting_balance": "1000000",
                "book_type": "L1_MBP",
                "trade_execution": False,
                "liquidity_consumption": True,
                "queue_position": False,
                "maker_fee": "0",
                "taker_fee": "0",
            },
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
                venue=Venue(instrument.venue_id),
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
            )
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

        if not strategy.submitted:
            raise ValueError(
                "Nautilus strategy did not observe the exact submission quote"
            )
        if strategy.submission_tick_ns != target_ns:
            raise ValueError(
                "Nautilus order was not submitted on exact PIT trigger"
            )

        filled = sum(
            strategy.fill_quantities,
            Decimal("0"),
        )
        remaining = intent.quantity - filled
        if filled == intent.quantity:
            final_state = SimulationOrderState.FILLED
        elif filled > 0:
            final_state = SimulationOrderState.PARTIALLY_FILLED
        elif strategy.terminal_state is not None:
            final_state = strategy.terminal_state
        else:
            final_state = SimulationOrderState.ACCEPTED

        vwap = (
            sum(
                (
                    quantity * price
                    for quantity, price in zip(
                        strategy.fill_quantities,
                        strategy.fill_prices,
                    )
                ),
                Decimal("0"),
            )
            / filled
            if filled > 0
            else None
        )
        diagnostics = (
            "historical BacktestEngine only",
            "L1_MBP quote-driven matching",
            "trade_execution disabled",
            "liquidity_consumption enabled",
            "queue_position disabled",
            "zero Nautilus fee model",
            "no live node or venue adapter imported",
            (
                "exact submission quote "
                + trigger_quote.event_id
            ),
        )
        payload = {
            "run_id": run.run_id,
            "intent_id": intent.intent_id,
            "execution_instrument_id": (
                instrument.execution_instrument_id
            ),
            "engine_name": self.ENGINE_NAME,
            "engine_version": self.engine_version,
            "adapter_version": ADAPTER_VERSION,
            "config_fingerprint": config_fingerprint,
            "final_state": final_state.value,
            "fill_count": len(strategy.fill_prices),
            "filled_quantity": str(filled),
            "remaining_quantity": str(remaining),
            "volume_weighted_average_price": (
                str(vwap) if vwap is not None else None
            ),
            "fill_prices": [
                str(item) for item in strategy.fill_prices
            ],
            "fill_quantities": [
                str(item) for item in strategy.fill_quantities
            ],
            "source_quote_event_ids": [trigger_quote.event_id],
            "diagnostics": list(diagnostics),
            "network_authority": "NONE",
            "external_order_authority": "NONE",
            "capital_authority": "NONE",
        }
        return NautilusExecutionResult(
            result_id=_content_id(
                "nautilus-execution-result",
                payload,
            ),
            run_id=run.run_id,
            intent_id=intent.intent_id,
            execution_instrument_id=(
                instrument.execution_instrument_id
            ),
            engine_name=self.ENGINE_NAME,
            engine_version=self.engine_version,
            adapter_version=ADAPTER_VERSION,
            config_fingerprint=config_fingerprint,
            final_state=final_state,
            fill_count=len(strategy.fill_prices),
            filled_quantity=filled,
            remaining_quantity=remaining,
            volume_weighted_average_price=vwap,
            fill_prices=tuple(strategy.fill_prices),
            fill_quantities=tuple(strategy.fill_quantities),
            source_quote_event_ids=(trigger_quote.event_id,),
            diagnostics=diagnostics,
            network_authority="NONE",
            external_order_authority="NONE",
            capital_authority="NONE",
        )

    def _validate_scope(
        self,
        *,
        run: ExecutionSimulationRunManifest,
        dataset: HistoricalReplayDataset,
        policy: ExecutionSimulationPolicy,
        instrument: ExecutionInstrument,
        intent: SimulationOrderIntent,
    ) -> TopOfBookQuote:
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
                "Stage 12.3 Nautilus differential supports equity only"
            )
        if (
            instrument.quantity_increment != Decimal("1")
            or instrument.contract_multiplier != Decimal("1")
            or instrument.minimum_quantity < Decimal("1")
        ):
            raise ValueError(
                "Stage 12.3 Nautilus equity mapping requires whole shares "
                "with unit multiplier"
            )
        if "." in instrument.venue_symbol or "." in instrument.venue_id:
            raise ValueError(
                "Nautilus differential fixture symbol/venue cannot contain '.'"
            )
        if any(
            value != 0
            for value in (
                policy.market_latency_ms,
                policy.order_latency_ms,
            )
        ):
            raise ValueError(
                "Stage 12.3 exact differential requires zero latency"
            )
        if any(
            value != Decimal("0")
            for value in (
                policy.commission_bps,
                policy.slippage_bps,
                policy.market_impact_bps,
            )
        ):
            raise ValueError(
                "Stage 12.3 exact differential requires zero execution costs"
            )
        if policy.maximum_participation_rate != Decimal("1"):
            raise ValueError(
                "Stage 12.3 exact differential requires full participation"
            )
        if policy.allow_partial_fills:
            raise ValueError(
                "Stage 12.3 exact differential disables partial fills"
            )

        matching_quotes = tuple(
            event
            for event in dataset.events
            if isinstance(event, TopOfBookQuote)
            and event.execution_instrument_id
            == instrument.execution_instrument_id
            and event.knowledge_time == intent.submitted_at
        )
        if len(matching_quotes) != 1:
            raise ValueError(
                "Stage 12.3 requires exactly one quote at order submission time"
            )
        quote = matching_quotes[0]
        touch_price, touch_quantity = (
            (quote.ask_price, quote.ask_quantity)
            if intent.side is ExecutionSide.BUY
            else (quote.bid_price, quote.bid_quantity)
        )
        if touch_quantity < intent.quantity:
            raise ValueError(
                "Stage 12.3 exact differential requires full displayed liquidity"
            )
        if intent.order_type is ExecutionOrderType.LIMIT:
            assert intent.limit_price is not None
            if intent.side is ExecutionSide.BUY:
                marketable = touch_price <= intent.limit_price
            else:
                marketable = touch_price >= intent.limit_price
            if not marketable:
                raise ValueError(
                    "Stage 12.3 differential supports only marketable limits"
                )
        return quote


class NautilusDifferentialEngine:
    """Exact differential gate for the narrow Stage 12.3 overlap."""

    def compare(
        self,
        *,
        reference_run: ExecutionSimulationRunManifest,
        reference_intent: SimulationOrderIntent,
        reference_result: ReferenceExecutionResult,
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

        matched = (
            final_state_match
            and fill_count_match
            and quantity_match
            and price_match
        )
        state = (
            DifferentialState.MATCH
            if matched
            else DifferentialState.MISMATCH
        )
        diagnostics = (
            "exact zero-friction historical overlap",
            "First Current reference is independent differential oracle",
            "Nautilus live adapters are outside Stage 12.3 scope",
        )
        payload = {
            "reference_result_id": reference_result.result_id,
            "nautilus_result_id": nautilus_result.result_id,
            "reference_run_id": reference_run.run_id,
            "nautilus_run_id": nautilus_run.run_id,
            "replay_dataset_id": reference_run.replay_dataset_id,
            "simulation_policy_id": reference_run.simulation_policy_id,
            "economic_intent_fingerprint": economics_fingerprint,
            "nautilus_version": nautilus_result.engine_version,
            "adapter_version": ADAPTER_VERSION,
            "state": state.value,
            "final_state_match": final_state_match,
            "fill_count_match": fill_count_match,
            "quantity_match": quantity_match,
            "price_match": price_match,
            "reference_final_state": reference_result.final_state.value,
            "nautilus_final_state": nautilus_result.final_state.value,
            "reference_filled_quantity": str(
                reference_result.filled_quantity
            ),
            "nautilus_filled_quantity": str(
                nautilus_result.filled_quantity
            ),
            "quantity_error": str(quantity_error),
            "reference_vwap": (
                str(reference_result.volume_weighted_average_price)
                if reference_result.volume_weighted_average_price is not None
                else None
            ),
            "nautilus_vwap": (
                str(nautilus_result.volume_weighted_average_price)
                if nautilus_result.volume_weighted_average_price is not None
                else None
            ),
            "absolute_vwap_error": (
                str(price_error) if price_error is not None else None
            ),
            "diagnostics": list(diagnostics),
            "trust_authority": (
                "REFERENCE_MATCH_ONLY"
                if matched
                else "NONE"
            ),
            "network_authority": "NONE",
            "external_order_authority": "NONE",
            "capital_authority": "NONE",
        }
        return NautilusDifferentialResult(
            differential_id=_content_id(
                "nautilus-execution-differential",
                payload,
            ),
            reference_result_id=reference_result.result_id,
            nautilus_result_id=nautilus_result.result_id,
            reference_run_id=reference_run.run_id,
            nautilus_run_id=nautilus_run.run_id,
            replay_dataset_id=reference_run.replay_dataset_id,
            simulation_policy_id=reference_run.simulation_policy_id,
            economic_intent_fingerprint=economics_fingerprint,
            nautilus_version=nautilus_result.engine_version,
            adapter_version=ADAPTER_VERSION,
            state=state,
            final_state_match=final_state_match,
            fill_count_match=fill_count_match,
            quantity_match=quantity_match,
            price_match=price_match,
            reference_final_state=reference_result.final_state,
            nautilus_final_state=nautilus_result.final_state,
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
        "config_fingerprint": result.config_fingerprint,
        "final_state": result.final_state.value,
        "fill_count": result.fill_count,
        "filled_quantity": str(result.filled_quantity),
        "remaining_quantity": str(result.remaining_quantity),
        "volume_weighted_average_price": (
            str(result.volume_weighted_average_price)
            if result.volume_weighted_average_price is not None
            else None
        ),
        "fill_prices": [
            str(item) for item in result.fill_prices
        ],
        "fill_quantities": [
            str(item) for item in result.fill_quantities
        ],
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
        "nautilus_version": result.nautilus_version,
        "adapter_version": result.adapter_version,
        "state": result.state.value,
        "final_state_match": result.final_state_match,
        "fill_count_match": result.fill_count_match,
        "quantity_match": result.quantity_match,
        "price_match": result.price_match,
        "reference_final_state": result.reference_final_state.value,
        "nautilus_final_state": result.nautilus_final_state.value,
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


def _unix_nanos(value: datetime) -> int:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    utc = value.astimezone(timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = utc - epoch
    return (
        delta.days * 86_400 * 1_000_000_000
        + delta.seconds * 1_000_000_000
        + delta.microseconds * 1_000
    )


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
