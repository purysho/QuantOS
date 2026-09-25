"""Stage 12.8 — deterministic transaction-cost analysis (TCA).

Implementation shortfall is measured against the arrival mid: the mid of
the latest book available when the order was submitted. It decomposes
exactly (in Decimal) into

    timing        = sum q * s * (fill-quote mid - arrival mid)
    half spread   = sum q * s * (fill-quote touch - fill-quote mid)
    slippage      = sum q * s * (fill price - fill-quote touch)
    fees          = sum fee
    opportunity   = unfilled * s * (terminal mid - arrival mid)

with s = +1 for buys and -1 for sells, all times contract multiplier.
Positive numbers are costs. The report proves the components add up to the
total before it is returned. A market-VWAP benchmark is given only when
trade prints exist in the order window; otherwise it is explicitly absent.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb

from .execution_contracts import (
    ExecutionInstrument,
    ExecutionSide,
    ExecutionSimulationPolicy,
    HistoricalReplayDataset,
    SimulatedFill,
    SimulationOrderIntent,
    TopOfBookQuote,
    TradePrint,
    historical_replay_dataset_identity,
    simulated_fill_identity,
    simulation_order_intent_identity,
)
from .execution_reference import (
    ReferenceExecutionResult,
    reference_execution_result_identity,
)
from .execution_schedule import reference_order_terminal_time

_BPS = Decimal("10000")


@dataclass(frozen=True)
class TransactionCostReport:
    report_id: str
    intent_id: str
    reference_result_id: str
    dataset_id: str
    execution_instrument_id: str
    side: ExecutionSide
    ordered_quantity: Decimal
    filled_quantity: Decimal
    fill_ratio: Decimal
    arrival_time: datetime
    arrival_quote_event_id: str
    arrival_mid: Decimal
    arrival_spread_bps: Decimal
    terminal_time: datetime
    terminal_mid: Decimal
    execution_vwap: Decimal | None
    paper_notional: Decimal
    timing_cost: Decimal
    half_spread_cost: Decimal
    slippage_impact_cost: Decimal
    fees: Decimal
    opportunity_cost: Decimal
    implementation_shortfall: Decimal
    implementation_shortfall_bps: Decimal
    average_participation: Decimal | None
    activation_delay_ms: int
    time_to_first_fill_ms: int | None
    time_to_last_fill_ms: int | None
    market_vwap: Decimal | None
    slippage_vs_market_vwap_bps: Decimal | None
    diagnostics: tuple[str, ...]
    simulation_authority: str
    capital_authority: str


@dataclass(frozen=True)
class TransactionCostAggregate:
    aggregate_id: str
    report_ids: tuple[str, ...]
    paper_notional: Decimal
    timing_cost: Decimal
    half_spread_cost: Decimal
    slippage_impact_cost: Decimal
    fees: Decimal
    opportunity_cost: Decimal
    implementation_shortfall: Decimal
    implementation_shortfall_bps: Decimal
    filled_quantity_ratio: Decimal


class TransactionCostAnalyzer:
    def analyze(
        self,
        *,
        dataset: HistoricalReplayDataset,
        policy: ExecutionSimulationPolicy,
        instrument: ExecutionInstrument,
        intent: SimulationOrderIntent,
        result: ReferenceExecutionResult,
        fills: tuple[SimulatedFill, ...],
    ) -> TransactionCostReport:
        if dataset.dataset_id != historical_replay_dataset_identity(dataset):
            raise ValueError("historical replay dataset identity mismatch")
        if intent.intent_id != simulation_order_intent_identity(intent):
            raise ValueError("simulation order intent identity mismatch")
        if result.result_id != reference_execution_result_identity(result):
            raise ValueError("reference execution result identity mismatch")
        if result.intent_id != intent.intent_id:
            raise ValueError("execution result belongs to another intent")
        if intent.execution_instrument_id != instrument.execution_instrument_id:
            raise ValueError("intent belongs to another instrument")
        for fill in fills:
            if fill.fill_id != simulated_fill_identity(fill):
                raise ValueError("simulated fill identity mismatch")
        if tuple(item.fill_id for item in fills) != result.fill_ids:
            raise ValueError("fills differ from execution result fill ids")

        latency = timedelta(milliseconds=policy.market_latency_ms)
        quotes = tuple(
            item
            for item in dataset.events
            if isinstance(item, TopOfBookQuote)
            and item.execution_instrument_id == instrument.execution_instrument_id
        )
        quotes_by_id = {item.event_id: item for item in quotes}

        def book_at(moment: datetime) -> TopOfBookQuote | None:
            known = [q for q in quotes if q.knowledge_time + latency <= moment]
            return known[-1] if known else None

        arrival = book_at(intent.submitted_at)
        if arrival is None:
            raise ValueError(
                "arrival benchmark unavailable: no book known at submission"
            )
        arrival_mid = _mid(arrival)
        terminal_time = reference_order_terminal_time(
            intent=intent,
            result=result,
            fills=fills,
            dataset=dataset,
            policy=policy,
        )
        terminal_book = book_at(terminal_time) or arrival
        terminal_mid = _mid(terminal_book)

        sign = Decimal("1") if intent.side is ExecutionSide.BUY else Decimal("-1")
        multiplier = instrument.contract_multiplier
        timing = Decimal("0")
        half_spread = Decimal("0")
        slippage = Decimal("0")
        fees = Decimal("0")
        displayed = Decimal("0")
        for fill in fills:
            quote = quotes_by_id.get(fill.market_event_id)
            if quote is None:
                raise ValueError("fill references a quote outside the replay")
            touch = (
                quote.ask_price
                if intent.side is ExecutionSide.BUY
                else quote.bid_price
            )
            touch_size = (
                quote.ask_quantity
                if intent.side is ExecutionSide.BUY
                else quote.bid_quantity
            )
            mid = _mid(quote)
            scale = fill.quantity * sign * multiplier
            timing += scale * (mid - arrival_mid)
            half_spread += scale * (touch - mid)
            slippage += scale * (fill.price - touch)
            fees += fill.fee
            displayed += touch_size

        filled = result.filled_quantity
        unfilled = intent.quantity - filled
        opportunity = unfilled * sign * multiplier * (terminal_mid - arrival_mid)
        total = timing + half_spread + slippage + fees + opportunity
        direct = (
            sum(
                (
                    fill.quantity * sign * multiplier * (fill.price - arrival_mid)
                    for fill in fills
                ),
                Decimal("0"),
            )
            + fees
            + opportunity
        )
        if total != direct:
            raise AssertionError("implementation shortfall does not reconcile")
        paper_notional = intent.quantity * arrival_mid * multiplier

        diagnostics: list[str] = []
        market_vwap = None
        vwap_slippage = None
        window = [
            trade
            for trade in dataset.events
            if isinstance(trade, TradePrint)
            and trade.execution_instrument_id == instrument.execution_instrument_id
            and intent.submitted_at
            <= trade.knowledge_time + latency
            <= terminal_time
        ]
        if window:
            volume = sum((trade.quantity for trade in window), Decimal("0"))
            market_vwap = (
                sum((trade.quantity * trade.price for trade in window), Decimal("0"))
                / volume
            )
            if result.volume_weighted_average_price is not None:
                vwap_slippage = (
                    sign
                    * (result.volume_weighted_average_price - market_vwap)
                    / market_vwap
                    * _BPS
                )
        else:
            diagnostics.append(
                "market VWAP benchmark unavailable: no trade prints in order window"
            )
        if unfilled > 0:
            diagnostics.append(
                f"opportunity cost charged on {unfilled} unfilled at terminal mid"
            )

        report = TransactionCostReport(
            report_id="",
            intent_id=intent.intent_id,
            reference_result_id=result.result_id,
            dataset_id=dataset.dataset_id,
            execution_instrument_id=instrument.execution_instrument_id,
            side=intent.side,
            ordered_quantity=intent.quantity,
            filled_quantity=filled,
            fill_ratio=filled / intent.quantity,
            arrival_time=intent.submitted_at,
            arrival_quote_event_id=arrival.event_id,
            arrival_mid=arrival_mid,
            arrival_spread_bps=(arrival.ask_price - arrival.bid_price)
            / arrival_mid
            * _BPS,
            terminal_time=terminal_time,
            terminal_mid=terminal_mid,
            execution_vwap=result.volume_weighted_average_price,
            paper_notional=paper_notional,
            timing_cost=timing,
            half_spread_cost=half_spread,
            slippage_impact_cost=slippage,
            fees=fees,
            opportunity_cost=opportunity,
            implementation_shortfall=total,
            implementation_shortfall_bps=total / paper_notional * _BPS,
            average_participation=(filled / displayed if fills else None),
            activation_delay_ms=policy.order_latency_ms,
            time_to_first_fill_ms=(
                _milliseconds(fills[0].fill_time - intent.submitted_at)
                if fills
                else None
            ),
            time_to_last_fill_ms=(
                _milliseconds(fills[-1].fill_time - intent.submitted_at)
                if fills
                else None
            ),
            market_vwap=market_vwap,
            slippage_vs_market_vwap_bps=vwap_slippage,
            diagnostics=tuple(diagnostics),
            simulation_authority="NONE",
            capital_authority="NONE",
        )
        return replace(report, report_id=transaction_cost_report_identity(report))

    def aggregate(
        self,
        reports: tuple[TransactionCostReport, ...],
    ) -> TransactionCostAggregate:
        if not reports:
            raise ValueError("transaction cost aggregate requires reports")
        for report in reports:
            if report.report_id != transaction_cost_report_identity(report):
                raise ValueError("transaction cost report identity mismatch")
        ids = tuple(sorted(item.report_id for item in reports))
        if len(ids) != len(set(ids)):
            raise ValueError("transaction cost aggregate repeats a report")

        def total(name: str) -> Decimal:
            return sum((getattr(item, name) for item in reports), Decimal("0"))

        paper = total("paper_notional")
        shortfall = total("implementation_shortfall")
        payload = {
            "report_ids": list(ids),
            "paper_notional": str(paper),
            "timing_cost": str(total("timing_cost")),
            "half_spread_cost": str(total("half_spread_cost")),
            "slippage_impact_cost": str(total("slippage_impact_cost")),
            "fees": str(total("fees")),
            "opportunity_cost": str(total("opportunity_cost")),
            "implementation_shortfall": str(shortfall),
        }
        ordered = sum(
            (item.ordered_quantity * item.arrival_mid for item in reports),
            Decimal("0"),
        )
        filled = sum(
            (item.filled_quantity * item.arrival_mid for item in reports),
            Decimal("0"),
        )
        return TransactionCostAggregate(
            aggregate_id=_content_id("transaction-cost-aggregate", payload),
            report_ids=ids,
            paper_notional=paper,
            timing_cost=total("timing_cost"),
            half_spread_cost=total("half_spread_cost"),
            slippage_impact_cost=total("slippage_impact_cost"),
            fees=total("fees"),
            opportunity_cost=total("opportunity_cost"),
            implementation_shortfall=shortfall,
            implementation_shortfall_bps=shortfall / paper * _BPS,
            filled_quantity_ratio=filled / ordered,
        )


class TransactionCostReportStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS transaction_cost_reports (
                report_id VARCHAR PRIMARY KEY,
                intent_id VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, report: TransactionCostReport) -> bool:
        if report.report_id != transaction_cost_report_identity(report):
            raise ValueError("transaction cost report identity mismatch")
        payload = json.dumps(
            transaction_cost_report_payload(report),
            sort_keys=True,
            separators=(",", ":"),
        )
        row = self._con.execute(
            "SELECT payload_json FROM transaction_cost_reports WHERE report_id = ?",
            [report.report_id],
        ).fetchone()
        if row is not None:
            if str(row[0]) != payload:
                raise ValueError("transaction cost report identity conflict")
            return False
        self._con.execute(
            "INSERT INTO transaction_cost_reports VALUES (?, ?, ?)",
            [report.report_id, report.intent_id, payload],
        )
        return True

    def close(self) -> None:
        self._con.close()


def transaction_cost_report_payload(report: TransactionCostReport) -> dict[str, object]:
    def text(value: object) -> object:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, (Decimal, ExecutionSide)):
            return value.value if isinstance(value, ExecutionSide) else str(value)
        return value

    return {
        name: (
            list(getattr(report, name))
            if name == "diagnostics"
            else text(getattr(report, name))
        )
        for name in report.__dataclass_fields__
        if name != "report_id"
    }


def transaction_cost_report_identity(report: TransactionCostReport) -> str:
    return _content_id("transaction-cost-report", transaction_cost_report_payload(report))


def _mid(quote: TopOfBookQuote) -> Decimal:
    return (quote.bid_price + quote.ask_price) / 2


def _milliseconds(delta: timedelta) -> int:
    return delta.days * 86_400_000 + delta.seconds * 1_000 + delta.microseconds // 1_000


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
