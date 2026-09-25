"""Stage 12.5 — multi-order execution schedules with inventory and cash.

A schedule turns explicit position targets into several simulated orders
across instruments, runs every order through the First Current reference
fill engine, and reconstructs cash and inventory from the resulting fills.

The top-of-book reference model has no shared-liquidity semantics between
orders, so two orders may not work on the same instrument at the same time.
That case fails closed instead of double-counting displayed liquidity.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path

import duckdb

from .execution_contracts import (
    ExecutionInstrument,
    ExecutionSide,
    ExecutionSimulationPolicy,
    ExecutionSimulationRunManifest,
    HistoricalReplayDataset,
    SimulatedFill,
    SimulationOrderIntent,
    SimulationOrderLedger,
    SimulationOrderState,
    TimeInForce,
    execution_simulation_run_identity,
    historical_replay_dataset_identity,
    simulation_order_intent_identity,
)
from .execution_reference import (
    FirstCurrentReferenceFillEngine,
    ReferenceExecutionResult,
)
from .pricing_risk_contracts import Currency


class ExecutionScheduleState(str, Enum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    CONSTRAINT_BREACH = "CONSTRAINT_BREACH"


@dataclass(frozen=True)
class ExecutionScheduleTarget:
    source_target_id: str
    execution_instrument_id: str
    starting_position: Decimal
    target_position: Decimal

    def __post_init__(self) -> None:
        if not self.source_target_id.strip():
            raise ValueError("schedule target requires source_target_id")
        if not self.execution_instrument_id.strip():
            raise ValueError("schedule target requires an instrument")
        for name in ("starting_position", "target_position"):
            if not getattr(self, name).is_finite():
                raise ValueError(f"{name} must be finite")
        if self.starting_position == self.target_position:
            raise ValueError(
                "schedule target must change the position"
            )

    @property
    def required_change(self) -> Decimal:
        return self.target_position - self.starting_position

    @property
    def side(self) -> ExecutionSide:
        return (
            ExecutionSide.BUY
            if self.required_change > 0
            else ExecutionSide.SELL
        )


@dataclass(frozen=True)
class ExecutionSchedulePolicy:
    cash_currency: Currency
    starting_cash: Decimal
    allow_short_positions: bool
    allow_negative_cash: bool
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.starting_cash.is_finite():
            raise ValueError("starting_cash must be finite")
        if not self.rationale.strip():
            raise ValueError("schedule policy rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError(
                "schedule policy requires evidence references"
            )

    @property
    def policy_id(self) -> str:
        return _content_id(
            "execution-schedule-policy",
            {
                "cash_currency": self.cash_currency.value,
                "starting_cash": str(self.starting_cash),
                "allow_short_positions": self.allow_short_positions,
                "allow_negative_cash": self.allow_negative_cash,
                "rationale": self.rationale,
                "evidence_references": sorted(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class ExecutionSchedule:
    schedule_id: str
    run_id: str
    schedule_policy_id: str
    targets: tuple[ExecutionScheduleTarget, ...]
    intent_ids: tuple[str, ...]


class ExecutionScheduleBuilder:
    def build(
        self,
        *,
        run: ExecutionSimulationRunManifest,
        policy: ExecutionSchedulePolicy,
        instruments: tuple[ExecutionInstrument, ...],
        targets: tuple[ExecutionScheduleTarget, ...],
        intents: tuple[SimulationOrderIntent, ...],
    ) -> ExecutionSchedule:
        if run.run_id != execution_simulation_run_identity(run):
            raise ValueError("execution simulation run identity mismatch")
        if not targets or not intents:
            raise ValueError("execution schedule requires targets and intents")
        by_instrument = _instrument_map(instruments)
        target_ids = [item.source_target_id for item in targets]
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("execution schedule has duplicate targets")
        target_instruments = [
            item.execution_instrument_id for item in targets
        ]
        if len(target_instruments) != len(set(target_instruments)):
            raise ValueError(
                "execution schedule has two targets for one instrument"
            )
        targets_by_id = {item.source_target_id: item for item in targets}
        for target in targets:
            instrument = by_instrument.get(target.execution_instrument_id)
            if instrument is None:
                raise ValueError(
                    "schedule target instrument is not supplied"
                )
            if instrument.quote_currency is not policy.cash_currency:
                raise ValueError(
                    "schedule instrument currency differs from cash "
                    "currency; no implicit FX"
                )
            if not policy.allow_short_positions and (
                target.starting_position < 0
                or target.target_position < 0
            ):
                raise ValueError(
                    "short target positions are outside schedule policy"
                )
        intent_ids = [item.intent_id for item in intents]
        if len(intent_ids) != len(set(intent_ids)):
            raise ValueError("execution schedule has duplicate intents")
        allocated: dict[str, Decimal] = {
            item.source_target_id: Decimal("0") for item in targets
        }
        for intent in intents:
            if intent.intent_id != simulation_order_intent_identity(intent):
                raise ValueError("simulation order intent identity mismatch")
            if intent.run_id != run.run_id:
                raise ValueError("schedule intent belongs to another run")
            target = targets_by_id.get(intent.source_target_id)
            if target is None:
                raise ValueError("schedule intent has no matching target")
            if intent.execution_instrument_id != target.execution_instrument_id:
                raise ValueError(
                    "schedule intent instrument differs from its target"
                )
            if intent.side is not target.side:
                raise ValueError(
                    "schedule intent side moves away from its target"
                )
            allocated[target.source_target_id] += intent.quantity
        for target in targets:
            if allocated[target.source_target_id] != abs(
                target.required_change
            ):
                raise ValueError(
                    "schedule intents must sum exactly to each target change"
                )
        ordered = tuple(
            sorted(intents, key=lambda item: (item.submitted_at, item.intent_id))
        )
        ordered_targets = tuple(
            sorted(targets, key=lambda item: item.source_target_id)
        )
        payload = {
            "run_id": run.run_id,
            "schedule_policy_id": policy.policy_id,
            "targets": [_target_payload(item) for item in ordered_targets],
            "intent_ids": [item.intent_id for item in ordered],
        }
        return ExecutionSchedule(
            schedule_id=_content_id("execution-schedule", payload),
            run_id=run.run_id,
            schedule_policy_id=policy.policy_id,
            targets=ordered_targets,
            intent_ids=tuple(item.intent_id for item in ordered),
        )


@dataclass(frozen=True)
class ExecutionTargetOutcome:
    source_target_id: str
    execution_instrument_id: str
    starting_position: Decimal
    target_position: Decimal
    final_position: Decimal
    filled_quantity: Decimal
    unfilled_quantity: Decimal


@dataclass(frozen=True)
class ExecutionScheduleResult:
    result_id: str
    schedule_id: str
    run_id: str
    state: ExecutionScheduleState
    order_result_ids: tuple[str, ...]
    fill_ids: tuple[str, ...]
    target_outcomes: tuple[ExecutionTargetOutcome, ...]
    starting_cash: Decimal
    final_cash: Decimal
    minimum_cash: Decimal
    total_notional_bought: Decimal
    total_notional_sold: Decimal
    total_fees: Decimal
    breaches: tuple[str, ...]
    reasons: tuple[str, ...]
    simulation_authority: str
    external_order_authority: str
    capital_authority: str


class ExecutionScheduleEngine:
    """Runs a schedule through the reference engine and reconciles books."""

    def execute(
        self,
        *,
        run: ExecutionSimulationRunManifest,
        dataset: HistoricalReplayDataset,
        simulation_policy: ExecutionSimulationPolicy,
        schedule_policy: ExecutionSchedulePolicy,
        schedule: ExecutionSchedule,
        instruments: tuple[ExecutionInstrument, ...],
        intents: tuple[SimulationOrderIntent, ...],
        ledger: SimulationOrderLedger,
    ) -> ExecutionScheduleResult:
        if schedule.schedule_id != execution_schedule_identity(schedule):
            raise ValueError("execution schedule identity mismatch")
        if schedule.run_id != run.run_id:
            raise ValueError("execution schedule belongs to another run")
        if schedule.schedule_policy_id != schedule_policy.policy_id:
            raise ValueError("execution schedule binds another policy")
        if dataset.dataset_id != historical_replay_dataset_identity(dataset):
            raise ValueError("historical replay dataset identity mismatch")
        intents_by_id = {item.intent_id: item for item in intents}
        if set(intents_by_id) != set(schedule.intent_ids) or len(
            intents
        ) != len(schedule.intent_ids):
            raise ValueError("supplied intents differ from the schedule")
        by_instrument = _instrument_map(instruments)

        reference = FirstCurrentReferenceFillEngine()
        working_until: dict[str, datetime] = {}
        order_results: list[ReferenceExecutionResult] = []
        fills: list[SimulatedFill] = []
        for intent_id in schedule.intent_ids:
            intent = intents_by_id[intent_id]
            busy_until = working_until.get(intent.execution_instrument_id)
            if busy_until is not None and intent.submitted_at < busy_until:
                raise ValueError(
                    "concurrent working orders on one instrument would share "
                    "top-of-book liquidity; outside reference semantics"
                )
            instrument = by_instrument[intent.execution_instrument_id]
            result = reference.simulate(
                run=run,
                dataset=dataset,
                policy=simulation_policy,
                instrument=instrument,
                intent=intent,
                ledger=ledger,
            )
            order_fills = ledger.fills(intent.intent_id)
            working_until[intent.execution_instrument_id] = reference_order_terminal_time(
                intent=intent,
                result=result,
                fills=order_fills,
                dataset=dataset,
                policy=simulation_policy,
            )
            order_results.append(result)
            fills.extend(order_fills)

        return self._reconcile(
            run=run,
            schedule=schedule,
            schedule_policy=schedule_policy,
            instruments=by_instrument,
            order_results=tuple(order_results),
            fills=tuple(fills),
        )

    @staticmethod
    def _reconcile(
        *,
        run: ExecutionSimulationRunManifest,
        schedule: ExecutionSchedule,
        schedule_policy: ExecutionSchedulePolicy,
        instruments: dict[str, ExecutionInstrument],
        order_results: tuple[ReferenceExecutionResult, ...],
        fills: tuple[SimulatedFill, ...],
    ) -> ExecutionScheduleResult:
        positions = {
            item.execution_instrument_id: item.starting_position
            for item in schedule.targets
        }
        filled = {item.execution_instrument_id: Decimal("0") for item in schedule.targets}
        cash = schedule_policy.starting_cash
        minimum_cash = cash
        bought = Decimal("0")
        sold = Decimal("0")
        fees = Decimal("0")
        breaches: list[str] = []
        for fill in sorted(fills, key=lambda item: (item.fill_time, item.fill_id)):
            multiplier = instruments[fill.execution_instrument_id].contract_multiplier
            notional = fill.quantity * fill.price * multiplier
            signed = fill.quantity if fill.side is ExecutionSide.BUY else -fill.quantity
            positions[fill.execution_instrument_id] += signed
            filled[fill.execution_instrument_id] += fill.quantity
            if fill.side is ExecutionSide.BUY:
                cash -= notional
                bought += notional
            else:
                cash += notional
                sold += notional
            cash -= fill.fee
            fees += fill.fee
            minimum_cash = min(minimum_cash, cash)
            if not schedule_policy.allow_negative_cash and cash < 0:
                breaches.append(
                    f"cash {cash} below zero after fill {fill.fill_id}"
                )
            if (
                not schedule_policy.allow_short_positions
                and positions[fill.execution_instrument_id] < 0
            ):
                breaches.append(
                    "short position "
                    f"{positions[fill.execution_instrument_id]} after fill "
                    f"{fill.fill_id}"
                )

        outcomes = tuple(
            ExecutionTargetOutcome(
                source_target_id=target.source_target_id,
                execution_instrument_id=target.execution_instrument_id,
                starting_position=target.starting_position,
                target_position=target.target_position,
                final_position=positions[target.execution_instrument_id],
                filled_quantity=filled[target.execution_instrument_id],
                unfilled_quantity=abs(target.required_change)
                - filled[target.execution_instrument_id],
            )
            for target in schedule.targets
        )
        reasons: list[str] = []
        if breaches:
            state = ExecutionScheduleState.CONSTRAINT_BREACH
            reasons.append("schedule breached frozen inventory/cash policy")
        elif any(item.unfilled_quantity != 0 for item in outcomes):
            state = ExecutionScheduleState.INCOMPLETE
            reasons.extend(
                f"{item.source_target_id} unfilled {item.unfilled_quantity}"
                for item in outcomes
                if item.unfilled_quantity != 0
            )
        else:
            state = ExecutionScheduleState.COMPLETE
            reasons.append("every target reached within frozen policy")
        result = ExecutionScheduleResult(
            result_id="",
            schedule_id=schedule.schedule_id,
            run_id=run.run_id,
            state=state,
            order_result_ids=tuple(item.result_id for item in order_results),
            fill_ids=tuple(item.fill_id for item in fills),
            target_outcomes=outcomes,
            starting_cash=schedule_policy.starting_cash,
            final_cash=cash,
            minimum_cash=minimum_cash,
            total_notional_bought=bought,
            total_notional_sold=sold,
            total_fees=fees,
            breaches=tuple(breaches),
            reasons=tuple(reasons),
            simulation_authority="HISTORICAL_REPLAY_ONLY",
            external_order_authority="NONE",
            capital_authority="NONE",
        )
        return replace(result, result_id=execution_schedule_result_identity(result))


class ExecutionScheduleResultStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_schedule_results (
                result_id VARCHAR PRIMARY KEY,
                schedule_id VARCHAR NOT NULL,
                state VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, result: ExecutionScheduleResult) -> bool:
        if result.result_id != execution_schedule_result_identity(result):
            raise ValueError("execution schedule result identity mismatch")
        payload = json.dumps(
            execution_schedule_result_payload(result),
            sort_keys=True,
            separators=(",", ":"),
        )
        row = self._con.execute(
            "SELECT payload_json FROM execution_schedule_results WHERE result_id = ?",
            [result.result_id],
        ).fetchone()
        if row is not None:
            if str(row[0]) != payload:
                raise ValueError("execution schedule result identity conflict")
            return False
        self._con.execute(
            "INSERT INTO execution_schedule_results VALUES (?, ?, ?, ?)",
            [result.result_id, result.schedule_id, result.state.value, payload],
        )
        return True

    def close(self) -> None:
        self._con.close()


def execution_schedule_identity(schedule: ExecutionSchedule) -> str:
    return _content_id(
        "execution-schedule",
        {
            "run_id": schedule.run_id,
            "schedule_policy_id": schedule.schedule_policy_id,
            "targets": [_target_payload(item) for item in schedule.targets],
            "intent_ids": list(schedule.intent_ids),
        },
    )


def execution_schedule_result_payload(
    result: ExecutionScheduleResult,
) -> dict[str, object]:
    return {
        "schedule_id": result.schedule_id,
        "run_id": result.run_id,
        "state": result.state.value,
        "order_result_ids": list(result.order_result_ids),
        "fill_ids": list(result.fill_ids),
        "target_outcomes": [
            {
                "source_target_id": item.source_target_id,
                "execution_instrument_id": item.execution_instrument_id,
                "starting_position": str(item.starting_position),
                "target_position": str(item.target_position),
                "final_position": str(item.final_position),
                "filled_quantity": str(item.filled_quantity),
                "unfilled_quantity": str(item.unfilled_quantity),
            }
            for item in result.target_outcomes
        ],
        "starting_cash": str(result.starting_cash),
        "final_cash": str(result.final_cash),
        "minimum_cash": str(result.minimum_cash),
        "total_notional_bought": str(result.total_notional_bought),
        "total_notional_sold": str(result.total_notional_sold),
        "total_fees": str(result.total_fees),
        "breaches": list(result.breaches),
        "reasons": list(result.reasons),
        "simulation_authority": result.simulation_authority,
        "external_order_authority": result.external_order_authority,
        "capital_authority": result.capital_authority,
    }


def execution_schedule_result_identity(result: ExecutionScheduleResult) -> str:
    return _content_id(
        "execution-schedule-result",
        execution_schedule_result_payload(result),
    )


def reference_order_terminal_time(
    *,
    intent: SimulationOrderIntent,
    result: ReferenceExecutionResult,
    fills: tuple[SimulatedFill, ...],
    dataset: HistoricalReplayDataset,
    policy: ExecutionSimulationPolicy,
) -> datetime:
    """When the reference order stopped working, per Stage 12.2 semantics."""

    if result.final_state is SimulationOrderState.REJECTED:
        return intent.submitted_at
    if result.final_state is SimulationOrderState.FILLED:
        return max(item.fill_time for item in fills)
    if intent.time_in_force in {TimeInForce.IOC, TimeInForce.FOK}:
        return intent.submitted_at + timedelta(milliseconds=policy.order_latency_ms)
    return dataset.end_time


def _instrument_map(
    instruments: tuple[ExecutionInstrument, ...],
) -> dict[str, ExecutionInstrument]:
    mapped = {item.execution_instrument_id: item for item in instruments}
    if len(mapped) != len(instruments):
        raise ValueError("duplicate execution instruments supplied")
    return mapped


def _target_payload(target: ExecutionScheduleTarget) -> dict[str, str]:
    return {
        "source_target_id": target.source_target_id,
        "execution_instrument_id": target.execution_instrument_id,
        "starting_position": str(target.starting_position),
        "target_position": str(target.target_position),
    }


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
