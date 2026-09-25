from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

import duckdb

from .pricing_risk_contracts import Currency


class ExecutionAssetClass(str, Enum):
    EQUITY = "EQUITY"
    FUTURE = "FUTURE"
    OPTION = "OPTION"
    FX = "FX"
    FIXED_INCOME = "FIXED_INCOME"


class ExecutionSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class ExecutionOrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class TimeInForce(str, Enum):
    DAY = "DAY"
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"


class SimulationOrderState(str, Enum):
    CREATED = "CREATED"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class FillLiquidity(str, Enum):
    MAKER = "MAKER"
    TAKER = "TAKER"
    UNKNOWN = "UNKNOWN"


class SimulationMode(str, Enum):
    HISTORICAL_REPLAY = "HISTORICAL_REPLAY"


@dataclass(frozen=True)
class ExecutionInstrument:
    canonical_instrument_id: str
    venue_id: str
    venue_symbol: str
    asset_class: ExecutionAssetClass
    quote_currency: Currency
    price_increment: Decimal
    quantity_increment: Decimal
    minimum_quantity: Decimal
    contract_multiplier: Decimal

    def __post_init__(self) -> None:
        for name in (
            "canonical_instrument_id",
            "venue_id",
            "venue_symbol",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
        for name in (
            "price_increment",
            "quantity_increment",
            "minimum_quantity",
            "contract_multiplier",
        ):
            _require_positive_finite(getattr(self, name), name)
        if (
            self.minimum_quantity / self.quantity_increment
        ) % Decimal("1") != 0:
            raise ValueError(
                "minimum_quantity must align to quantity_increment"
            )

    @property
    def execution_instrument_id(self) -> str:
        return _content_id(
            "execution-instrument",
            execution_instrument_payload(self),
        )


@dataclass(frozen=True)
class TopOfBookQuote:
    execution_instrument_id: str
    event_time: datetime
    knowledge_time: datetime
    sequence: int
    bid_price: Decimal
    bid_quantity: Decimal
    ask_price: Decimal
    ask_quantity: Decimal
    source_fact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_market_event_header(
            execution_instrument_id=self.execution_instrument_id,
            event_time=self.event_time,
            knowledge_time=self.knowledge_time,
            sequence=self.sequence,
            source_fact_ids=self.source_fact_ids,
        )
        for name in (
            "bid_price",
            "bid_quantity",
            "ask_price",
            "ask_quantity",
        ):
            _require_positive_finite(getattr(self, name), name)
        if self.bid_price >= self.ask_price:
            raise ValueError(
                "top-of-book bid must be strictly below ask"
            )

    @property
    def event_id(self) -> str:
        return _content_id(
            "execution-market-event",
            {
                "kind": "TOP_OF_BOOK",
                "execution_instrument_id": self.execution_instrument_id,
                "event_time": self.event_time.isoformat(),
                "knowledge_time": self.knowledge_time.isoformat(),
                "sequence": self.sequence,
                "bid_price": str(self.bid_price),
                "bid_quantity": str(self.bid_quantity),
                "ask_price": str(self.ask_price),
                "ask_quantity": str(self.ask_quantity),
                "source_fact_ids": sorted(self.source_fact_ids),
            },
        )


@dataclass(frozen=True)
class TradePrint:
    execution_instrument_id: str
    event_time: datetime
    knowledge_time: datetime
    sequence: int
    price: Decimal
    quantity: Decimal
    source_fact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_market_event_header(
            execution_instrument_id=self.execution_instrument_id,
            event_time=self.event_time,
            knowledge_time=self.knowledge_time,
            sequence=self.sequence,
            source_fact_ids=self.source_fact_ids,
        )
        _require_positive_finite(self.price, "trade price")
        _require_positive_finite(self.quantity, "trade quantity")

    @property
    def event_id(self) -> str:
        return _content_id(
            "execution-market-event",
            {
                "kind": "TRADE_PRINT",
                "execution_instrument_id": self.execution_instrument_id,
                "event_time": self.event_time.isoformat(),
                "knowledge_time": self.knowledge_time.isoformat(),
                "sequence": self.sequence,
                "price": str(self.price),
                "quantity": str(self.quantity),
                "source_fact_ids": sorted(self.source_fact_ids),
            },
        )


HistoricalMarketEvent = TopOfBookQuote | TradePrint


@dataclass(frozen=True)
class HistoricalReplayDataset:
    dataset_id: str
    start_time: datetime
    end_time: datetime
    execution_instrument_ids: tuple[str, ...]
    events: tuple[HistoricalMarketEvent, ...]
    source_fingerprint: str


class HistoricalReplayDatasetBuilder:
    def build(
        self,
        *,
        start_time: datetime,
        end_time: datetime,
        events: tuple[HistoricalMarketEvent, ...],
    ) -> HistoricalReplayDataset:
        if start_time.tzinfo is None or end_time.tzinfo is None:
            raise ValueError(
                "historical replay timestamps must be timezone-aware"
            )
        if end_time <= start_time:
            raise ValueError("historical replay end_time must follow start_time")
        if not events:
            raise ValueError("historical replay requires market events")
        if len({item.event_id for item in events}) != len(events):
            raise ValueError("historical replay contains duplicate events")
        if any(
            item.event_time < start_time or item.event_time > end_time
            for item in events
        ):
            raise ValueError(
                "historical replay event lies outside dataset interval"
            )
        if any(item.knowledge_time > end_time for item in events):
            raise ValueError(
                "historical replay market data becomes known after dataset end"
            )
        sequence_keys = [
            (item.execution_instrument_id, item.sequence)
            for item in events
        ]
        if len(sequence_keys) != len(set(sequence_keys)):
            raise ValueError(
                "historical replay contains duplicate instrument sequence"
            )
        canonical = tuple(
            sorted(
                events,
                key=lambda item: (
                    item.knowledge_time,
                    item.event_time,
                    item.execution_instrument_id,
                    item.sequence,
                    item.event_id,
                ),
            )
        )
        for left, right in zip(canonical, canonical[1:]):
            if right.knowledge_time < left.knowledge_time:
                raise AssertionError(
                    "canonical replay knowledge time is not monotonic"
                )

        instrument_ids = tuple(
            sorted(
                {
                    item.execution_instrument_id
                    for item in canonical
                }
            )
        )
        source_fingerprint = _content_id(
            "execution-market-source",
            {
                "event_ids": [
                    item.event_id for item in canonical
                ],
                "source_fact_ids": sorted(
                    {
                        source_id
                        for item in canonical
                        for source_id in item.source_fact_ids
                    }
                ),
            },
        )
        payload = {
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "execution_instrument_ids": list(instrument_ids),
            "event_ids": [item.event_id for item in canonical],
            "source_fingerprint": source_fingerprint,
        }
        return HistoricalReplayDataset(
            dataset_id=_content_id(
                "historical-replay-dataset",
                payload,
            ),
            start_time=start_time,
            end_time=end_time,
            execution_instrument_ids=instrument_ids,
            events=canonical,
            source_fingerprint=source_fingerprint,
        )


class OrderActivationMode(str, Enum):
    """When a latency-delayed order starts matching (Stage 12.10)."""

    IMMEDIATE_LATEST_BOOK = "IMMEDIATE_LATEST_BOOK"
    NEXT_QUOTE_ARRIVAL = "NEXT_QUOTE_ARRIVAL"


class MarketOrderResidual(str, Enum):
    WAIT_FOR_LIQUIDITY = "WAIT_FOR_LIQUIDITY"
    ONE_TICK_THROUGH = "ONE_TICK_THROUGH"


class RestingLimitFillPrice(str, Enum):
    CONTRA_TOUCH = "CONTRA_TOUCH"
    LIMIT_PRICE = "LIMIT_PRICE"


class LiquidityRefresh(str, Enum):
    """EVERY_QUOTE: each book offers its full displayed size again.
    ON_LEVEL_SIZE_CHANGE: quantity this order took at a price level stays
    consumed until that level is shown with a different size."""

    EVERY_QUOTE = "EVERY_QUOTE"
    ON_LEVEL_SIZE_CHANGE = "ON_LEVEL_SIZE_CHANGE"


class CommissionRounding(str, Enum):
    EXACT = "EXACT"
    HALF_EVEN_MINOR_UNIT = "HALF_EVEN_MINOR_UNIT"


class ImmediatePartialFills(str, Enum):
    FOLLOW_POLICY = "FOLLOW_POLICY"
    ALWAYS_ALLOW = "ALWAYS_ALLOW"


# ISO 4217 minor units for supported quote currencies.
CURRENCY_MINOR_UNITS: dict[Currency, int] = {
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

_MODE_DEFAULTS = {
    "order_activation": OrderActivationMode.IMMEDIATE_LATEST_BOOK,
    "market_order_residual": MarketOrderResidual.WAIT_FOR_LIQUIDITY,
    "resting_limit_fill_price": RestingLimitFillPrice.CONTRA_TOUCH,
    "liquidity_refresh": LiquidityRefresh.EVERY_QUOTE,
    "commission_rounding": CommissionRounding.EXACT,
    "immediate_partial_fills": ImmediatePartialFills.FOLLOW_POLICY,
}


@dataclass(frozen=True)
class ExecutionSimulationPolicy:
    market_latency_ms: int
    order_latency_ms: int
    commission_bps: Decimal
    slippage_bps: Decimal
    market_impact_bps: Decimal
    maximum_participation_rate: Decimal
    allow_partial_fills: bool
    rationale: str
    evidence_references: tuple[str, ...]
    # Stage 12.10 explicit semantics modes. Defaults reproduce Stage 12.2
    # exactly and are omitted from the policy identity, so existing policy
    # IDs are unchanged.
    order_activation: OrderActivationMode = OrderActivationMode.IMMEDIATE_LATEST_BOOK
    market_order_residual: MarketOrderResidual = MarketOrderResidual.WAIT_FOR_LIQUIDITY
    resting_limit_fill_price: RestingLimitFillPrice = RestingLimitFillPrice.CONTRA_TOUCH
    liquidity_refresh: LiquidityRefresh = LiquidityRefresh.EVERY_QUOTE
    commission_rounding: CommissionRounding = CommissionRounding.EXACT
    immediate_partial_fills: ImmediatePartialFills = ImmediatePartialFills.FOLLOW_POLICY

    def non_default_modes(self) -> dict[str, str]:
        return {
            name: getattr(self, name).value
            for name, default in _MODE_DEFAULTS.items()
            if getattr(self, name) is not default
        }

    def __post_init__(self) -> None:
        if self.market_latency_ms < 0 or self.order_latency_ms < 0:
            raise ValueError("simulation latency cannot be negative")
        for name in (
            "commission_bps",
            "slippage_bps",
            "market_impact_bps",
            "maximum_participation_rate",
        ):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(
                    f"{name} must be finite and non-negative"
                )
        if (
            self.maximum_participation_rate <= 0
            or self.maximum_participation_rate > 1
        ):
            raise ValueError(
                "maximum_participation_rate must be in (0, 1]"
            )
        for name, default in _MODE_DEFAULTS.items():
            if not isinstance(getattr(self, name), type(default)):
                raise ValueError(f"{name} must be a {type(default).__name__}")
        if not self.rationale.strip():
            raise ValueError(
                "execution simulation policy rationale is required"
            )
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError(
                "execution simulation policy requires evidence references"
            )

    @property
    def policy_id(self) -> str:
        return _content_id(
            "execution-simulation-policy",
            {
                "market_latency_ms": self.market_latency_ms,
                "order_latency_ms": self.order_latency_ms,
                "commission_bps": str(self.commission_bps),
                "slippage_bps": str(self.slippage_bps),
                "market_impact_bps": str(self.market_impact_bps),
                "maximum_participation_rate": str(
                    self.maximum_participation_rate
                ),
                "allow_partial_fills": self.allow_partial_fills,
                "rationale": self.rationale,
                "evidence_references": sorted(
                    self.evidence_references
                ),
                **self.non_default_modes(),
            },
        )


@dataclass(frozen=True)
class ExecutionSimulationRunManifest:
    run_id: str
    mode: SimulationMode
    research_run_manifest_id: str
    portfolio_solution_id: str
    replay_dataset_id: str
    simulation_policy_id: str
    engine_name: str
    engine_version: str
    code_revision: str
    created_at: datetime
    evidence_references: tuple[str, ...]
    network_authority: str
    external_order_authority: str
    capital_authority: str


class ExecutionSimulationRunManifestBuilder:
    def build(
        self,
        *,
        research_run_manifest_id: str,
        portfolio_solution_id: str,
        replay_dataset: HistoricalReplayDataset,
        simulation_policy: ExecutionSimulationPolicy,
        engine_name: str,
        engine_version: str,
        code_revision: str,
        created_at: datetime,
        evidence_references: tuple[str, ...],
    ) -> ExecutionSimulationRunManifest:
        for name, value in (
            ("research_run_manifest_id", research_run_manifest_id),
            ("portfolio_solution_id", portfolio_solution_id),
            ("engine_name", engine_name),
            ("engine_version", engine_version),
            ("code_revision", code_revision),
        ):
            if not value.strip():
                raise ValueError(f"{name} is required")
        if created_at.tzinfo is None:
            raise ValueError("simulation run created_at must be timezone-aware")
        if not evidence_references or not all(
            item.strip() for item in evidence_references
        ):
            raise ValueError(
                "simulation run requires evidence references"
            )
        payload = {
            "mode": SimulationMode.HISTORICAL_REPLAY.value,
            "research_run_manifest_id": research_run_manifest_id,
            "portfolio_solution_id": portfolio_solution_id,
            "replay_dataset_id": replay_dataset.dataset_id,
            "simulation_policy_id": simulation_policy.policy_id,
            "engine_name": engine_name.strip(),
            "engine_version": engine_version.strip(),
            "code_revision": code_revision.strip(),
            "created_at": created_at.isoformat(),
            "evidence_references": sorted(evidence_references),
            "network_authority": "NONE",
            "external_order_authority": "NONE",
            "capital_authority": "NONE",
        }
        return ExecutionSimulationRunManifest(
            run_id=_content_id(
                "execution-simulation-run",
                payload,
            ),
            mode=SimulationMode.HISTORICAL_REPLAY,
            research_run_manifest_id=research_run_manifest_id,
            portfolio_solution_id=portfolio_solution_id,
            replay_dataset_id=replay_dataset.dataset_id,
            simulation_policy_id=simulation_policy.policy_id,
            engine_name=engine_name.strip(),
            engine_version=engine_version.strip(),
            code_revision=code_revision.strip(),
            created_at=created_at,
            evidence_references=tuple(
                sorted(evidence_references)
            ),
            network_authority="NONE",
            external_order_authority="NONE",
            capital_authority="NONE",
        )


@dataclass(frozen=True)
class SimulationOrderIntent:
    intent_id: str
    run_id: str
    execution_instrument_id: str
    side: ExecutionSide
    order_type: ExecutionOrderType
    quantity: Decimal
    limit_price: Decimal | None
    time_in_force: TimeInForce
    submitted_at: datetime
    source_target_id: str
    simulation_authority: str
    external_order_authority: str
    capital_authority: str


class SimulationOrderIntentBuilder:
    def build(
        self,
        *,
        run: ExecutionSimulationRunManifest,
        instrument: ExecutionInstrument,
        side: ExecutionSide,
        order_type: ExecutionOrderType,
        quantity: Decimal,
        time_in_force: TimeInForce,
        submitted_at: datetime,
        source_target_id: str,
        limit_price: Decimal | None = None,
    ) -> SimulationOrderIntent:
        if run.mode is not SimulationMode.HISTORICAL_REPLAY:
            raise ValueError(
                "Stage 12.1 supports historical replay only"
            )
        if (
            run.network_authority != "NONE"
            or run.external_order_authority != "NONE"
            or run.capital_authority != "NONE"
        ):
            raise ValueError(
                "simulation run unexpectedly carries external authority"
            )
        if submitted_at.tzinfo is None:
            raise ValueError(
                "simulation order submitted_at must be timezone-aware"
            )
        if not source_target_id.strip():
            raise ValueError(
                "simulation order requires source_target_id"
            )
        _require_positive_finite(quantity, "simulation order quantity")
        _require_increment_alignment(
            quantity,
            instrument.quantity_increment,
            "simulation order quantity",
        )
        if quantity < instrument.minimum_quantity:
            raise ValueError(
                "simulation order quantity is below minimum_quantity"
            )
        if order_type is ExecutionOrderType.MARKET:
            if limit_price is not None:
                raise ValueError(
                    "market simulation order cannot have limit_price"
                )
        elif order_type is ExecutionOrderType.LIMIT:
            if limit_price is None:
                raise ValueError(
                    "limit simulation order requires limit_price"
                )
            _require_positive_finite(
                limit_price,
                "simulation order limit_price",
            )
            _require_increment_alignment(
                limit_price,
                instrument.price_increment,
                "simulation order limit_price",
            )
        else:
            raise ValueError(
                f"unsupported simulation order type: {order_type.value}"
            )
        payload = {
            "run_id": run.run_id,
            "execution_instrument_id": instrument.execution_instrument_id,
            "side": side.value,
            "order_type": order_type.value,
            "quantity": str(quantity),
            "limit_price": (
                str(limit_price)
                if limit_price is not None
                else None
            ),
            "time_in_force": time_in_force.value,
            "submitted_at": submitted_at.isoformat(),
            "source_target_id": source_target_id.strip(),
            "simulation_authority": "HISTORICAL_REPLAY_ONLY",
            "external_order_authority": "NONE",
            "capital_authority": "NONE",
        }
        return SimulationOrderIntent(
            intent_id=_content_id(
                "simulation-order-intent",
                payload,
            ),
            run_id=run.run_id,
            execution_instrument_id=instrument.execution_instrument_id,
            side=side,
            order_type=order_type,
            quantity=quantity,
            limit_price=limit_price,
            time_in_force=time_in_force,
            submitted_at=submitted_at,
            source_target_id=source_target_id.strip(),
            simulation_authority="HISTORICAL_REPLAY_ONLY",
            external_order_authority="NONE",
            capital_authority="NONE",
        )


@dataclass(frozen=True)
class SimulatedFill:
    fill_id: str
    run_id: str
    intent_id: str
    execution_instrument_id: str
    fill_time: datetime
    market_event_id: str
    side: ExecutionSide
    quantity: Decimal
    price: Decimal
    fee: Decimal
    liquidity: FillLiquidity
    cumulative_filled_quantity: Decimal
    remaining_quantity: Decimal
    simulation_authority: str
    external_order_authority: str
    capital_authority: str


class SimulatedFillBuilder:
    def build(
        self,
        *,
        run: ExecutionSimulationRunManifest,
        intent: SimulationOrderIntent,
        market_event: HistoricalMarketEvent,
        fill_time: datetime,
        quantity: Decimal,
        price: Decimal,
        fee: Decimal,
        liquidity: FillLiquidity,
        prior_filled_quantity: Decimal,
    ) -> SimulatedFill:
        if intent.run_id != run.run_id:
            raise ValueError("fill intent belongs to another simulation run")
        if (
            intent.execution_instrument_id
            != market_event.execution_instrument_id
        ):
            raise ValueError(
                "fill market event belongs to another instrument"
            )
        if fill_time.tzinfo is None:
            raise ValueError("fill_time must be timezone-aware")
        if fill_time < intent.submitted_at:
            raise ValueError(
                "simulated fill cannot predate order submission"
            )
        if fill_time < market_event.knowledge_time:
            raise ValueError(
                "simulated fill cannot use market data before it was known"
            )
        _require_positive_finite(quantity, "fill quantity")
        _require_positive_finite(price, "fill price")
        if not fee.is_finite() or fee < 0:
            raise ValueError("fill fee must be finite and non-negative")
        if (
            not prior_filled_quantity.is_finite()
            or prior_filled_quantity < 0
        ):
            raise ValueError(
                "prior_filled_quantity must be finite and non-negative"
            )
        cumulative = prior_filled_quantity + quantity
        if cumulative > intent.quantity:
            raise ValueError(
                "simulated fill exceeds original order quantity"
            )
        remaining = intent.quantity - cumulative
        payload = {
            "run_id": run.run_id,
            "intent_id": intent.intent_id,
            "execution_instrument_id": intent.execution_instrument_id,
            "fill_time": fill_time.isoformat(),
            "market_event_id": market_event.event_id,
            "side": intent.side.value,
            "quantity": str(quantity),
            "price": str(price),
            "fee": str(fee),
            "liquidity": liquidity.value,
            "cumulative_filled_quantity": str(cumulative),
            "remaining_quantity": str(remaining),
            "simulation_authority": "HISTORICAL_REPLAY_ONLY",
            "external_order_authority": "NONE",
            "capital_authority": "NONE",
        }
        return SimulatedFill(
            fill_id=_content_id("simulated-fill", payload),
            run_id=run.run_id,
            intent_id=intent.intent_id,
            execution_instrument_id=intent.execution_instrument_id,
            fill_time=fill_time,
            market_event_id=market_event.event_id,
            side=intent.side,
            quantity=quantity,
            price=price,
            fee=fee,
            liquidity=liquidity,
            cumulative_filled_quantity=cumulative,
            remaining_quantity=remaining,
            simulation_authority="HISTORICAL_REPLAY_ONLY",
            external_order_authority="NONE",
            capital_authority="NONE",
        )


@dataclass(frozen=True)
class SimulationOrderTransition:
    transition_id: str
    ordinal: int
    run_id: str
    intent_id: str
    occurred_at: datetime
    prior_state: SimulationOrderState
    new_state: SimulationOrderState
    fill_id: str | None
    reason: str


_ALLOWED_TRANSITIONS: dict[
    SimulationOrderState,
    frozenset[SimulationOrderState],
] = {
    SimulationOrderState.CREATED: frozenset(
        {
            SimulationOrderState.ACCEPTED,
            SimulationOrderState.REJECTED,
        }
    ),
    SimulationOrderState.ACCEPTED: frozenset(
        {
            SimulationOrderState.PARTIALLY_FILLED,
            SimulationOrderState.FILLED,
            SimulationOrderState.CANCELED,
            SimulationOrderState.EXPIRED,
        }
    ),
    SimulationOrderState.PARTIALLY_FILLED: frozenset(
        {
            SimulationOrderState.PARTIALLY_FILLED,
            SimulationOrderState.FILLED,
            SimulationOrderState.CANCELED,
            SimulationOrderState.EXPIRED,
        }
    ),
    SimulationOrderState.FILLED: frozenset(),
    SimulationOrderState.CANCELED: frozenset(),
    SimulationOrderState.REJECTED: frozenset(),
    SimulationOrderState.EXPIRED: frozenset(),
}


class SimulationOrderLedger:
    """Immutable execution-state ledger for historical replay only."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS simulation_order_intents (
                intent_id VARCHAR PRIMARY KEY,
                run_id VARCHAR NOT NULL,
                execution_instrument_id VARCHAR NOT NULL,
                submitted_at TIMESTAMPTZ NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS simulation_order_transitions (
                transition_id VARCHAR PRIMARY KEY,
                ordinal INTEGER NOT NULL,
                run_id VARCHAR NOT NULL,
                intent_id VARCHAR NOT NULL,
                occurred_at TIMESTAMPTZ NOT NULL,
                prior_state VARCHAR NOT NULL,
                new_state VARCHAR NOT NULL,
                fill_id VARCHAR,
                reason VARCHAR NOT NULL,
                UNIQUE (intent_id, ordinal)
            )
            """
        )
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS simulated_fills (
                fill_id VARCHAR PRIMARY KEY,
                run_id VARCHAR NOT NULL,
                intent_id VARCHAR NOT NULL,
                fill_time TIMESTAMPTZ NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add_intent(self, intent: SimulationOrderIntent) -> bool:
        if intent.intent_id != simulation_order_intent_identity(intent):
            raise ValueError(
                "simulation order intent identity does not match content"
            )
        if (
            intent.simulation_authority != "HISTORICAL_REPLAY_ONLY"
            or intent.external_order_authority != "NONE"
            or intent.capital_authority != "NONE"
        ):
            raise ValueError(
                "simulation order intent carries invalid authority"
            )
        payload = json.dumps(
            simulation_order_intent_payload(intent),
            sort_keys=True,
            separators=(",", ":"),
        )
        row = self._con.execute(
            """
            SELECT payload_json
            FROM simulation_order_intents
            WHERE intent_id = ?
            """,
            [intent.intent_id],
        ).fetchone()
        if row is not None:
            if str(row[0]) != payload:
                raise ValueError(
                    "simulation order intent identity conflict"
                )
            return False
        self._con.execute(
            """
            INSERT INTO simulation_order_intents
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                intent.intent_id,
                intent.run_id,
                intent.execution_instrument_id,
                intent.submitted_at,
                payload,
            ],
        )
        return True

    def transition(
        self,
        *,
        intent: SimulationOrderIntent,
        new_state: SimulationOrderState,
        occurred_at: datetime,
        reason: str,
        fill: SimulatedFill | None = None,
    ) -> SimulationOrderTransition:
        if occurred_at.tzinfo is None:
            raise ValueError(
                "simulation order transition time must be timezone-aware"
            )
        if occurred_at < intent.submitted_at:
            raise ValueError(
                "simulation order transition cannot predate submission"
            )
        if not reason.strip():
            raise ValueError(
                "simulation order transition reason is required"
            )
        if self._con.execute(
            """
            SELECT 1 FROM simulation_order_intents
            WHERE intent_id = ?
            """,
            [intent.intent_id],
        ).fetchone() is None:
            raise ValueError(
                "simulation order intent must be persisted before transition"
            )
        prior_state = self.state(intent.intent_id)
        if new_state not in _ALLOWED_TRANSITIONS[prior_state]:
            raise ValueError(
                f"invalid simulation order transition "
                f"{prior_state.value} -> {new_state.value}"
            )
        if new_state in {
            SimulationOrderState.PARTIALLY_FILLED,
            SimulationOrderState.FILLED,
        }:
            if fill is None:
                raise ValueError(
                    "fill transition requires SimulatedFill"
                )
            if fill.intent_id != intent.intent_id:
                raise ValueError(
                    "fill belongs to another simulation order intent"
                )
            if (
                fill.run_id != intent.run_id
                or fill.execution_instrument_id
                != intent.execution_instrument_id
            ):
                raise ValueError(
                    "fill lineage differs from simulation order intent"
                )
            if fill.fill_time != occurred_at:
                raise ValueError(
                    "fill transition time must equal fill_time"
                )
            if (
                new_state is SimulationOrderState.FILLED
                and fill.remaining_quantity != 0
            ):
                raise ValueError(
                    "FILLED transition requires zero remaining quantity"
                )
            if (
                new_state is SimulationOrderState.PARTIALLY_FILLED
                and (
                    fill.remaining_quantity <= 0
                    or fill.cumulative_filled_quantity <= 0
                )
            ):
                raise ValueError(
                    "PARTIALLY_FILLED requires positive filled and remaining quantity"
                )
        elif fill is not None:
            raise ValueError(
                "non-fill transition cannot carry SimulatedFill"
            )

        ordinal = self._next_ordinal(intent.intent_id)
        payload = {
            "ordinal": ordinal,
            "run_id": intent.run_id,
            "intent_id": intent.intent_id,
            "occurred_at": occurred_at.isoformat(),
            "prior_state": prior_state.value,
            "new_state": new_state.value,
            "fill_id": fill.fill_id if fill is not None else None,
            "reason": reason.strip(),
        }
        transition = SimulationOrderTransition(
            transition_id=_content_id(
                "simulation-order-transition",
                payload,
            ),
            ordinal=ordinal,
            run_id=intent.run_id,
            intent_id=intent.intent_id,
            occurred_at=occurred_at,
            prior_state=prior_state,
            new_state=new_state,
            fill_id=fill.fill_id if fill is not None else None,
            reason=reason.strip(),
        )

        self._con.execute("BEGIN TRANSACTION")
        try:
            if fill is not None:
                self._add_fill(fill)
            self._con.execute(
                """
                INSERT INTO simulation_order_transitions
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    transition.transition_id,
                    transition.ordinal,
                    transition.run_id,
                    transition.intent_id,
                    transition.occurred_at,
                    transition.prior_state.value,
                    transition.new_state.value,
                    transition.fill_id,
                    transition.reason,
                ],
            )
            self._con.execute("COMMIT")
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        return transition

    def state(self, intent_id: str) -> SimulationOrderState:
        row = self._con.execute(
            """
            SELECT new_state
            FROM simulation_order_transitions
            WHERE intent_id = ?
            ORDER BY ordinal DESC
            LIMIT 1
            """,
            [intent_id],
        ).fetchone()
        if row is None:
            return SimulationOrderState.CREATED
        return SimulationOrderState(str(row[0]))

    def fills(self, intent_id: str) -> tuple[SimulatedFill, ...]:
        rows = self._con.execute(
            """
            SELECT payload_json
            FROM simulated_fills
            WHERE intent_id = ?
            """,
            [intent_id],
        ).fetchall()
        # Execution order: fills at the same instant (a sweep through
        # several levels) are ordered by how much had filled, never by
        # their content hashes.
        return tuple(
            sorted(
                (
                    simulated_fill_from_payload(json.loads(str(row[0])))
                    for row in rows
                ),
                key=lambda fill: (
                    fill.fill_time,
                    fill.cumulative_filled_quantity,
                ),
            )
        )

    def close(self) -> None:
        self._con.close()

    def _add_fill(self, fill: SimulatedFill) -> None:
        if fill.fill_id != simulated_fill_identity(fill):
            raise ValueError(
                "simulated fill identity does not match content"
            )
        if (
            fill.simulation_authority != "HISTORICAL_REPLAY_ONLY"
            or fill.external_order_authority != "NONE"
            or fill.capital_authority != "NONE"
        ):
            raise ValueError(
                "simulated fill carries invalid authority"
            )
        payload = json.dumps(
            simulated_fill_payload(fill),
            sort_keys=True,
            separators=(",", ":"),
        )
        if self._con.execute(
            """
            SELECT 1 FROM simulated_fills
            WHERE fill_id = ?
            """,
            [fill.fill_id],
        ).fetchone() is not None:
            raise ValueError("duplicate simulated fill")
        self._con.execute(
            """
            INSERT INTO simulated_fills
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                fill.fill_id,
                fill.run_id,
                fill.intent_id,
                fill.fill_time,
                payload,
            ],
        )

    def _next_ordinal(self, intent_id: str) -> int:
        row = self._con.execute(
            """
            SELECT COALESCE(MAX(ordinal), 0)
            FROM simulation_order_transitions
            WHERE intent_id = ?
            """,
            [intent_id],
        ).fetchone()
        return int(row[0]) + 1


def execution_instrument_payload(
    instrument: ExecutionInstrument,
) -> dict[str, object]:
    return {
        "canonical_instrument_id": instrument.canonical_instrument_id.strip(),
        "venue_id": instrument.venue_id.strip(),
        "venue_symbol": instrument.venue_symbol.strip(),
        "asset_class": instrument.asset_class.value,
        "quote_currency": instrument.quote_currency.value,
        "price_increment": str(instrument.price_increment),
        "quantity_increment": str(instrument.quantity_increment),
        "minimum_quantity": str(instrument.minimum_quantity),
        "contract_multiplier": str(instrument.contract_multiplier),
    }


def historical_replay_dataset_identity(
    dataset: HistoricalReplayDataset,
) -> str:
    canonical = tuple(
        sorted(
            dataset.events,
            key=lambda item: (
                item.knowledge_time,
                item.event_time,
                item.execution_instrument_id,
                item.sequence,
                item.event_id,
            ),
        )
    )
    event_ids = [item.event_id for item in canonical]
    instrument_ids = tuple(
        sorted(
            {
                item.execution_instrument_id
                for item in canonical
            }
        )
    )
    if instrument_ids != dataset.execution_instrument_ids:
        raise ValueError(
            "historical replay instrument IDs do not match nested events"
        )
    source_fingerprint = _content_id(
        "execution-market-source",
        {
            "event_ids": event_ids,
            "source_fact_ids": sorted(
                {
                    source_id
                    for item in canonical
                    for source_id in item.source_fact_ids
                }
            ),
        },
    )
    if source_fingerprint != dataset.source_fingerprint:
        raise ValueError(
            "historical replay source fingerprint mismatch"
        )
    return _content_id(
        "historical-replay-dataset",
        {
            "start_time": dataset.start_time.isoformat(),
            "end_time": dataset.end_time.isoformat(),
            "execution_instrument_ids": list(instrument_ids),
            "event_ids": event_ids,
            "source_fingerprint": source_fingerprint,
        },
    )


def execution_simulation_run_identity(
    run: ExecutionSimulationRunManifest,
) -> str:
    return _content_id(
        "execution-simulation-run",
        {
            "mode": run.mode.value,
            "research_run_manifest_id": run.research_run_manifest_id,
            "portfolio_solution_id": run.portfolio_solution_id,
            "replay_dataset_id": run.replay_dataset_id,
            "simulation_policy_id": run.simulation_policy_id,
            "engine_name": run.engine_name,
            "engine_version": run.engine_version,
            "code_revision": run.code_revision,
            "created_at": run.created_at.isoformat(),
            "evidence_references": list(run.evidence_references),
            "network_authority": run.network_authority,
            "external_order_authority": run.external_order_authority,
            "capital_authority": run.capital_authority,
        },
    )


def simulation_order_intent_payload(
    intent: SimulationOrderIntent,
) -> dict[str, object]:
    return {
        "run_id": intent.run_id,
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
        "simulation_authority": intent.simulation_authority,
        "external_order_authority": intent.external_order_authority,
        "capital_authority": intent.capital_authority,
    }


def simulation_order_intent_identity(
    intent: SimulationOrderIntent,
) -> str:
    return _content_id(
        "simulation-order-intent",
        simulation_order_intent_payload(intent),
    )


def simulated_fill_payload(
    fill: SimulatedFill,
) -> dict[str, object]:
    return {
        "run_id": fill.run_id,
        "intent_id": fill.intent_id,
        "execution_instrument_id": fill.execution_instrument_id,
        "fill_time": fill.fill_time.isoformat(),
        "market_event_id": fill.market_event_id,
        "side": fill.side.value,
        "quantity": str(fill.quantity),
        "price": str(fill.price),
        "fee": str(fill.fee),
        "liquidity": fill.liquidity.value,
        "cumulative_filled_quantity": str(
            fill.cumulative_filled_quantity
        ),
        "remaining_quantity": str(fill.remaining_quantity),
        "simulation_authority": fill.simulation_authority,
        "external_order_authority": fill.external_order_authority,
        "capital_authority": fill.capital_authority,
    }


def simulated_fill_identity(fill: SimulatedFill) -> str:
    return _content_id(
        "simulated-fill",
        simulated_fill_payload(fill),
    )


def simulated_fill_from_payload(
    payload: dict[str, object],
) -> SimulatedFill:
    return SimulatedFill(
        fill_id=_content_id("simulated-fill", payload),
        run_id=str(payload["run_id"]),
        intent_id=str(payload["intent_id"]),
        execution_instrument_id=str(
            payload["execution_instrument_id"]
        ),
        fill_time=datetime.fromisoformat(
            str(payload["fill_time"])
        ),
        market_event_id=str(payload["market_event_id"]),
        side=ExecutionSide(str(payload["side"])),
        quantity=Decimal(str(payload["quantity"])),
        price=Decimal(str(payload["price"])),
        fee=Decimal(str(payload["fee"])),
        liquidity=FillLiquidity(str(payload["liquidity"])),
        cumulative_filled_quantity=Decimal(
            str(payload["cumulative_filled_quantity"])
        ),
        remaining_quantity=Decimal(
            str(payload["remaining_quantity"])
        ),
        simulation_authority=str(payload["simulation_authority"]),
        external_order_authority=str(
            payload["external_order_authority"]
        ),
        capital_authority=str(payload["capital_authority"]),
    )


def _validate_market_event_header(
    *,
    execution_instrument_id: str,
    event_time: datetime,
    knowledge_time: datetime,
    sequence: int,
    source_fact_ids: tuple[str, ...],
) -> None:
    if not execution_instrument_id.strip():
        raise ValueError(
            "market event requires execution_instrument_id"
        )
    if event_time.tzinfo is None or knowledge_time.tzinfo is None:
        raise ValueError(
            "market event timestamps must be timezone-aware"
        )
    if knowledge_time < event_time:
        raise ValueError(
            "market event cannot be known before event_time"
        )
    if sequence < 0:
        raise ValueError("market event sequence cannot be negative")
    if not source_fact_ids or not all(
        item.strip() for item in source_fact_ids
    ):
        raise ValueError(
            "market event requires source fact IDs"
        )
    if len(source_fact_ids) != len(set(source_fact_ids)):
        raise ValueError(
            "market event source fact IDs must be unique"
        )


def _require_increment_alignment(
    value: Decimal,
    increment: Decimal,
    name: str,
) -> None:
    if (value / increment) % Decimal("1") != 0:
        raise ValueError(
            f"{name} must align to its configured increment"
        )


def _require_positive_finite(value: Decimal, name: str) -> None:
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{name} must be finite and positive")


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()