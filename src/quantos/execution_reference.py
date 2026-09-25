from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from decimal import (
    Decimal,
    ROUND_CEILING,
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
)
from pathlib import Path

import duckdb

from .execution_contracts import (
    ExecutionInstrument,
    ExecutionOrderType,
    ExecutionSide,
    ExecutionSimulationPolicy,
    ExecutionSimulationRunManifest,
    FillLiquidity,
    HistoricalReplayDataset,
    SimulatedFill,
    SimulatedFillBuilder,
    SimulationOrderIntent,
    SimulationOrderLedger,
    SimulationOrderState,
    CURRENCY_MINOR_UNITS,
    CommissionRounding,
    ImmediatePartialFills,
    LiquidityRefresh,
    MarketOrderResidual,
    OrderActivationMode,
    RestingLimitFillPrice,
    TimeInForce,
    TopOfBookQuote,
    execution_simulation_run_identity,
    historical_replay_dataset_identity,
)


@dataclass(frozen=True)
class ReferenceExecutionResult:
    result_id: str
    run_id: str
    intent_id: str
    execution_instrument_id: str
    engine_name: str
    engine_version: str
    active_at: object
    final_state: SimulationOrderState
    transition_ids: tuple[str, ...]
    fill_ids: tuple[str, ...]
    filled_quantity: Decimal
    remaining_quantity: Decimal
    volume_weighted_average_price: Decimal | None
    total_notional: Decimal
    total_fees: Decimal
    average_adverse_slippage_bps: Decimal | None
    first_market_event_id: str | None
    last_market_event_id: str | None
    simulation_authority: str
    external_order_authority: str
    capital_authority: str


class FirstCurrentReferenceFillEngine:
    """Deterministic top-of-book historical replay oracle."""

    ENGINE_NAME = "FIRST_CURRENT_REFERENCE"
    ENGINE_VERSION = "12.10"

    def simulate(
        self,
        *,
        run: ExecutionSimulationRunManifest,
        dataset: HistoricalReplayDataset,
        policy: ExecutionSimulationPolicy,
        instrument: ExecutionInstrument,
        intent: SimulationOrderIntent,
        ledger: SimulationOrderLedger,
    ) -> ReferenceExecutionResult:
        self._validate_binding(
            run=run,
            dataset=dataset,
            policy=policy,
            instrument=instrument,
            intent=intent,
        )
        ledger.add_intent(intent)

        immediate = intent.time_in_force in {TimeInForce.IOC, TimeInForce.FOK}
        active_at = intent.submitted_at + timedelta(
            milliseconds=policy.order_latency_ms
        )
        quotes = tuple(
            event
            for event in dataset.events
            if isinstance(event, TopOfBookQuote)
            and event.execution_instrument_id
            == instrument.execution_instrument_id
        )
        quoted_arrivals = tuple(
            (
                quote,
                quote.knowledge_time
                + timedelta(milliseconds=policy.market_latency_ms),
            )
            for quote in quotes
            if (
                quote.knowledge_time
                + timedelta(milliseconds=policy.market_latency_ms)
            )
            <= dataset.end_time
        )

        candidate_events: list[tuple[TopOfBookQuote, object]] = []
        effective_at = active_at
        if policy.order_activation is OrderActivationMode.NEXT_QUOTE_ARRIVAL:
            # The order starts matching only when the next book arrives at or
            # after activation, and matches against that book.
            candidate_events = [
                (quote, available_at)
                for quote, available_at in quoted_arrivals
                if available_at >= active_at
            ]
            if candidate_events:
                effective_at = candidate_events[0][1]
        else:
            current_quote: TopOfBookQuote | None = None
            later_quotes: list[tuple[TopOfBookQuote, object]] = []
            for quote, available_at in quoted_arrivals:
                if available_at <= active_at:
                    current_quote = quote
                else:
                    later_quotes.append((quote, available_at))
            if current_quote is not None:
                candidate_events.append((current_quote, active_at))
            candidate_events.extend(later_quotes)

        no_arrival = (
            policy.order_activation is OrderActivationMode.NEXT_QUOTE_ARRIVAL
            and not candidate_events
        )
        if active_at > dataset.end_time or no_arrival:
            transition = ledger.transition(
                intent=intent,
                new_state=SimulationOrderState.REJECTED,
                occurred_at=intent.submitted_at,
                reason=(
                    "no market data arrives after activation within replay horizon"
                    if no_arrival and active_at <= dataset.end_time
                    else "order latency places simulated order outside replay horizon"
                ),
            )
            return self._result(
                run=run,
                intent=intent,
                active_at=active_at,
                final_state=SimulationOrderState.REJECTED,
                transitions=(transition.transition_id,),
                fills=(),
                instrument=instrument,
                touch_prices=(),
                market_event_ids=(),
            )

        accepted = ledger.transition(
            intent=intent,
            new_state=SimulationOrderState.ACCEPTED,
            occurred_at=effective_at,
            reason="historical replay order became active",
        )
        transition_ids: list[str] = [accepted.transition_id]
        fills: list[SimulatedFill] = []
        touch_prices: list[Decimal] = []
        market_event_ids: list[str] = []
        if immediate:
            candidate_events = candidate_events[:1]

        allow_partial = policy.allow_partial_fills or (
            intent.time_in_force is TimeInForce.IOC
            and policy.immediate_partial_fills is ImmediatePartialFills.ALWAYS_ALLOW
        )
        refresh_on_change = (
            policy.liquidity_refresh is LiquidityRefresh.ON_LEVEL_SIZE_CHANGE
        )
        # Per price level: (last displayed size, quantity this order took).
        level_state: dict[Decimal, tuple[Decimal, Decimal]] = {}
        prior_filled = Decimal("0")
        remaining = intent.quantity

        def record(quote, fill_time, quantity, price, touch, liquidity):
            nonlocal prior_filled, remaining
            fee = _commission(
                quantity=quantity,
                price=price,
                multiplier=instrument.contract_multiplier,
                commission_bps=policy.commission_bps,
                rounding=policy.commission_rounding,
                currency=instrument.quote_currency,
            )
            fill = SimulatedFillBuilder().build(
                run=run,
                intent=intent,
                market_event=quote,
                fill_time=fill_time,
                quantity=quantity,
                price=price,
                fee=fee,
                liquidity=liquidity,
                prior_filled_quantity=prior_filled,
            )
            next_state = (
                SimulationOrderState.FILLED
                if fill.remaining_quantity == 0
                else SimulationOrderState.PARTIALLY_FILLED
            )
            transition = ledger.transition(
                intent=intent,
                new_state=next_state,
                occurred_at=fill.fill_time,
                reason="deterministic top-of-book historical replay fill",
                fill=fill,
            )
            transition_ids.append(transition.transition_id)
            fills.append(fill)
            touch_prices.append(touch)
            market_event_ids.append(quote.event_id)
            prior_filled = fill.cumulative_filled_quantity
            remaining = fill.remaining_quantity

        for index, (quote, fill_time) in enumerate(candidate_events):
            if remaining <= 0:
                break
            touch_price, displayed_quantity = _contra_touch(
                intent.side,
                quote,
            )
            available = displayed_quantity
            if refresh_on_change:
                seen_size, taken = level_state.get(touch_price, (None, Decimal("0")))
                if seen_size == displayed_quantity:
                    available = displayed_quantity - taken
                else:
                    taken = Decimal("0")
                level_state[touch_price] = (displayed_quantity, taken)

            def take(quantity: Decimal) -> None:
                if refresh_on_change:
                    size, taken_so_far = level_state[touch_price]
                    level_state[touch_price] = (size, taken_so_far + quantity)
            if not _is_marketable(intent, quote):
                if immediate:
                    break
                continue

            capacity = _aligned_capacity(
                displayed_quantity=available,
                participation_rate=policy.maximum_participation_rate,
                quantity_increment=instrument.quantity_increment,
            )
            resting = (
                index > 0
                and intent.order_type is ExecutionOrderType.LIMIT
                and policy.resting_limit_fill_price
                is RestingLimitFillPrice.LIMIT_PRICE
            )
            base_price = intent.limit_price if resting else touch_price
            liquidity = (
                FillLiquidity.MAKER
                if resting
                else FillLiquidity.TAKER
                if intent.order_type is ExecutionOrderType.MARKET
                else FillLiquidity.UNKNOWN
            )
            sweep = (
                intent.order_type is ExecutionOrderType.MARKET
                and not immediate
                and policy.market_order_residual
                is MarketOrderResidual.ONE_TICK_THROUGH
                and capacity < remaining
            )
            if sweep:
                # Synthetic L1 depth: displayed size at the touch, the whole
                # residual one tick through it, at the same instant.
                if capacity > 0:
                    record(
                        quote,
                        fill_time,
                        capacity,
                        _execution_price(
                            side=intent.side,
                            touch_price=touch_price,
                            fill_quantity=capacity,
                            displayed_quantity=displayed_quantity,
                            policy=policy,
                            price_increment=instrument.price_increment,
                        ),
                        touch_price,
                        liquidity,
                    )
                through = (
                    touch_price + instrument.price_increment
                    if intent.side is ExecutionSide.BUY
                    else touch_price - instrument.price_increment
                )
                residual = remaining
                record(
                    quote,
                    fill_time,
                    residual,
                    _execution_price(
                        side=intent.side,
                        touch_price=through,
                        fill_quantity=residual,
                        displayed_quantity=residual,
                        policy=policy,
                        price_increment=instrument.price_increment,
                    ),
                    touch_price,
                    liquidity,
                )
                take(capacity)
                break

            if capacity <= 0:
                if immediate:
                    break
                continue

            if intent.time_in_force is TimeInForce.FOK:
                if capacity < remaining:
                    break
                fill_quantity = remaining
            elif not allow_partial:
                if capacity < remaining:
                    if intent.time_in_force is TimeInForce.IOC:
                        break
                    continue
                fill_quantity = remaining
            else:
                fill_quantity = min(remaining, capacity)

            execution_price = _execution_price(
                side=intent.side,
                touch_price=base_price,
                fill_quantity=fill_quantity,
                displayed_quantity=displayed_quantity,
                policy=policy,
                price_increment=instrument.price_increment,
            )
            if (
                intent.order_type is ExecutionOrderType.LIMIT
                and intent.limit_price is not None
                and not _price_respects_limit(
                    side=intent.side,
                    execution_price=execution_price,
                    limit_price=intent.limit_price,
                )
            ):
                if immediate:
                    break
                continue

            record(quote, fill_time, fill_quantity, execution_price, touch_price, liquidity)
            take(fill_quantity)
            if immediate:
                break

        final_state = ledger.state(intent.intent_id)
        if final_state is not SimulationOrderState.FILLED:
            expire_time = effective_at if immediate else dataset.end_time
            transition = ledger.transition(
                intent=intent,
                new_state=SimulationOrderState.EXPIRED,
                occurred_at=expire_time,
                reason=(
                    "immediate-or-cancel remainder expired"
                    if intent.time_in_force is TimeInForce.IOC
                    else "fill-or-kill order was not fully executable"
                    if intent.time_in_force is TimeInForce.FOK
                    else "historical replay horizon ended with unfilled quantity"
                ),
            )
            transition_ids.append(transition.transition_id)
            final_state = SimulationOrderState.EXPIRED

        return self._result(
            run=run,
            intent=intent,
            active_at=effective_at,
            final_state=final_state,
            transitions=tuple(transition_ids),
            fills=tuple(fills),
            instrument=instrument,
            touch_prices=tuple(touch_prices),
            market_event_ids=tuple(market_event_ids),
        )

    @staticmethod
    def _validate_binding(
        *,
        run: ExecutionSimulationRunManifest,
        dataset: HistoricalReplayDataset,
        policy: ExecutionSimulationPolicy,
        instrument: ExecutionInstrument,
        intent: SimulationOrderIntent,
    ) -> None:
        if run.run_id != execution_simulation_run_identity(run):
            raise ValueError(
                "execution simulation run identity mismatch"
            )
        if run.engine_name != FirstCurrentReferenceFillEngine.ENGINE_NAME:
            raise ValueError(
                "reference fill engine requires FIRST_CURRENT_REFERENCE run"
            )
        if (
            run.network_authority != "NONE"
            or run.external_order_authority != "NONE"
            or run.capital_authority != "NONE"
        ):
            raise ValueError(
                "execution simulation run carries external authority"
            )
        if (
            dataset.dataset_id
            != historical_replay_dataset_identity(dataset)
        ):
            raise ValueError(
                "historical replay dataset identity mismatch"
            )
        if run.replay_dataset_id != dataset.dataset_id:
            raise ValueError(
                "execution simulation run binds another replay dataset"
            )
        if run.simulation_policy_id != policy.policy_id:
            raise ValueError(
                "execution simulation run binds another simulation policy"
            )
        if intent.run_id != run.run_id:
            raise ValueError(
                "simulation order intent belongs to another run"
            )
        if (
            intent.execution_instrument_id
            != instrument.execution_instrument_id
        ):
            raise ValueError(
                "simulation order intent belongs to another instrument"
            )
        if (
            instrument.execution_instrument_id
            not in dataset.execution_instrument_ids
        ):
            raise ValueError(
                "execution instrument is absent from replay dataset"
            )
        if intent.submitted_at < dataset.start_time:
            raise ValueError(
                "simulation order submitted before replay dataset start"
            )
        if intent.submitted_at > dataset.end_time:
            raise ValueError(
                "simulation order submitted after replay dataset end"
            )
        if (
            intent.simulation_authority != "HISTORICAL_REPLAY_ONLY"
            or intent.external_order_authority != "NONE"
            or intent.capital_authority != "NONE"
        ):
            raise ValueError(
                "simulation order intent carries invalid authority"
            )

    def _result(
        self,
        *,
        run: ExecutionSimulationRunManifest,
        intent: SimulationOrderIntent,
        active_at,
        final_state: SimulationOrderState,
        transitions: tuple[str, ...],
        fills: tuple[SimulatedFill, ...],
        instrument: ExecutionInstrument,
        touch_prices: tuple[Decimal, ...],
        market_event_ids: tuple[str, ...],
    ) -> ReferenceExecutionResult:
        filled = sum(
            (item.quantity for item in fills),
            Decimal("0"),
        )
        remaining = intent.quantity - filled
        total_notional = sum(
            (
                item.quantity
                * item.price
                * instrument.contract_multiplier
                for item in fills
            ),
            Decimal("0"),
        )
        total_fees = sum(
            (item.fee for item in fills),
            Decimal("0"),
        )
        vwap = (
            sum(
                (
                    item.quantity * item.price
                    for item in fills
                ),
                Decimal("0"),
            )
            / filled
            if filled > 0
            else None
        )
        adverse_slippages: list[Decimal] = []
        for fill, touch in zip(fills, touch_prices):
            if intent.side is ExecutionSide.BUY:
                bps = (
                    (fill.price / touch - Decimal("1"))
                    * Decimal("10000")
                )
            else:
                bps = (
                    (Decimal("1") - fill.price / touch)
                    * Decimal("10000")
                )
            adverse_slippages.append(bps)
        average_slippage = (
            sum(adverse_slippages, Decimal("0"))
            / Decimal(len(adverse_slippages))
            if adverse_slippages
            else None
        )

        payload = {
            "run_id": run.run_id,
            "intent_id": intent.intent_id,
            "execution_instrument_id": intent.execution_instrument_id,
            "engine_name": self.ENGINE_NAME,
            "engine_version": self.ENGINE_VERSION,
            "active_at": active_at.isoformat(),
            "final_state": final_state.value,
            "transition_ids": list(transitions),
            "fill_ids": [item.fill_id for item in fills],
            "filled_quantity": str(filled),
            "remaining_quantity": str(remaining),
            "volume_weighted_average_price": (
                str(vwap) if vwap is not None else None
            ),
            "total_notional": str(total_notional),
            "total_fees": str(total_fees),
            "average_adverse_slippage_bps": (
                str(average_slippage)
                if average_slippage is not None
                else None
            ),
            "first_market_event_id": (
                market_event_ids[0] if market_event_ids else None
            ),
            "last_market_event_id": (
                market_event_ids[-1] if market_event_ids else None
            ),
            "simulation_authority": "HISTORICAL_REPLAY_ONLY",
            "external_order_authority": "NONE",
            "capital_authority": "NONE",
        }
        return ReferenceExecutionResult(
            result_id=_content_id(
                "reference-execution-result",
                payload,
            ),
            run_id=run.run_id,
            intent_id=intent.intent_id,
            execution_instrument_id=intent.execution_instrument_id,
            engine_name=self.ENGINE_NAME,
            engine_version=self.ENGINE_VERSION,
            active_at=active_at,
            final_state=final_state,
            transition_ids=transitions,
            fill_ids=tuple(item.fill_id for item in fills),
            filled_quantity=filled,
            remaining_quantity=remaining,
            volume_weighted_average_price=vwap,
            total_notional=total_notional,
            total_fees=total_fees,
            average_adverse_slippage_bps=average_slippage,
            first_market_event_id=(
                market_event_ids[0] if market_event_ids else None
            ),
            last_market_event_id=(
                market_event_ids[-1] if market_event_ids else None
            ),
            simulation_authority="HISTORICAL_REPLAY_ONLY",
            external_order_authority="NONE",
            capital_authority="NONE",
        )


class ReferenceExecutionResultStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS reference_execution_results (
                result_id VARCHAR PRIMARY KEY,
                run_id VARCHAR NOT NULL,
                intent_id VARCHAR NOT NULL,
                execution_instrument_id VARCHAR NOT NULL,
                final_state VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, result: ReferenceExecutionResult) -> bool:
        if result.result_id != reference_execution_result_identity(
            result
        ):
            raise ValueError(
                "reference execution result identity mismatch"
            )
        payload = json.dumps(
            reference_execution_result_payload(result),
            sort_keys=True,
            separators=(",", ":"),
        )
        row = self._con.execute(
            """
            SELECT payload_json
            FROM reference_execution_results
            WHERE result_id = ?
            """,
            [result.result_id],
        ).fetchone()
        if row is not None:
            if str(row[0]) != payload:
                raise ValueError(
                    "reference execution result identity conflict"
                )
            return False
        self._con.execute(
            """
            INSERT INTO reference_execution_results
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                result.result_id,
                result.run_id,
                result.intent_id,
                result.execution_instrument_id,
                result.final_state.value,
                payload,
            ],
        )
        return True

    def close(self) -> None:
        self._con.close()


def reference_execution_result_payload(
    result: ReferenceExecutionResult,
) -> dict[str, object]:
    return {
        "run_id": result.run_id,
        "intent_id": result.intent_id,
        "execution_instrument_id": result.execution_instrument_id,
        "engine_name": result.engine_name,
        "engine_version": result.engine_version,
        "active_at": result.active_at.isoformat(),
        "final_state": result.final_state.value,
        "transition_ids": list(result.transition_ids),
        "fill_ids": list(result.fill_ids),
        "filled_quantity": str(result.filled_quantity),
        "remaining_quantity": str(result.remaining_quantity),
        "volume_weighted_average_price": (
            str(result.volume_weighted_average_price)
            if result.volume_weighted_average_price is not None
            else None
        ),
        "total_notional": str(result.total_notional),
        "total_fees": str(result.total_fees),
        "average_adverse_slippage_bps": (
            str(result.average_adverse_slippage_bps)
            if result.average_adverse_slippage_bps is not None
            else None
        ),
        "first_market_event_id": result.first_market_event_id,
        "last_market_event_id": result.last_market_event_id,
        "simulation_authority": result.simulation_authority,
        "external_order_authority": result.external_order_authority,
        "capital_authority": result.capital_authority,
    }


def reference_execution_result_identity(
    result: ReferenceExecutionResult,
) -> str:
    return _content_id(
        "reference-execution-result",
        reference_execution_result_payload(result),
    )


def _is_marketable(
    intent: SimulationOrderIntent,
    quote: TopOfBookQuote,
) -> bool:
    if intent.order_type is ExecutionOrderType.MARKET:
        return True
    assert intent.limit_price is not None
    if intent.side is ExecutionSide.BUY:
        return quote.ask_price <= intent.limit_price
    return quote.bid_price >= intent.limit_price


def _contra_touch(
    side: ExecutionSide,
    quote: TopOfBookQuote,
) -> tuple[Decimal, Decimal]:
    if side is ExecutionSide.BUY:
        return quote.ask_price, quote.ask_quantity
    return quote.bid_price, quote.bid_quantity


def _aligned_capacity(
    *,
    displayed_quantity: Decimal,
    participation_rate: Decimal,
    quantity_increment: Decimal,
) -> Decimal:
    raw = displayed_quantity * participation_rate
    units = (
        raw / quantity_increment
    ).to_integral_value(rounding=ROUND_FLOOR)
    return units * quantity_increment


def _execution_price(
    *,
    side: ExecutionSide,
    touch_price: Decimal,
    fill_quantity: Decimal,
    displayed_quantity: Decimal,
    policy: ExecutionSimulationPolicy,
    price_increment: Decimal,
) -> Decimal:
    participation = fill_quantity / displayed_quantity
    adverse_bps = (
        policy.slippage_bps
        + policy.market_impact_bps * participation
    )
    multiplier = adverse_bps / Decimal("10000")
    raw = (
        touch_price * (Decimal("1") + multiplier)
        if side is ExecutionSide.BUY
        else touch_price * (Decimal("1") - multiplier)
    )
    rounding = (
        ROUND_CEILING
        if side is ExecutionSide.BUY
        else ROUND_FLOOR
    )
    ticks = (
        raw / price_increment
    ).to_integral_value(rounding=rounding)
    price = ticks * price_increment
    if price <= 0:
        raise ValueError(
            "simulated execution price must remain positive"
        )
    return price


def _price_respects_limit(
    *,
    side: ExecutionSide,
    execution_price: Decimal,
    limit_price: Decimal,
) -> bool:
    if side is ExecutionSide.BUY:
        return execution_price <= limit_price
    return execution_price >= limit_price


def _commission(
    *,
    quantity: Decimal,
    price: Decimal,
    multiplier: Decimal,
    commission_bps: Decimal,
    rounding: CommissionRounding = CommissionRounding.EXACT,
    currency=None,
) -> Decimal:
    notional = quantity * price * multiplier
    exact = (
        notional
        * commission_bps
        / Decimal("10000")
    )
    if rounding is CommissionRounding.EXACT:
        return exact
    units = CURRENCY_MINOR_UNITS.get(currency)
    if units is None:
        raise ValueError("commission rounding needs a currency with known minor units")
    return exact.quantize(Decimal(1).scaleb(-units), rounding=ROUND_HALF_EVEN)


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()