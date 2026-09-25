"""Stage 12.7 — replay data-quality and execution-robustness gate.

A clean-looking execution simulation on a broken tape is the most dangerous
execution result. This gate inspects a historical replay dataset (and,
optionally, the orders that will run on it) under a frozen policy and
reports every issue it finds. It never repairs, interpolates or drops data.

Crossed or locked books and knowledge-before-event timestamps are already
rejected by the Stage 12.1 contracts, so they cannot reach this gate.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path

import duckdb

from .execution_contracts import (
    HistoricalReplayDataset,
    SimulationOrderIntent,
    TopOfBookQuote,
    TradePrint,
    historical_replay_dataset_identity,
    simulation_order_intent_identity,
)


class ReplayQualityState(str, Enum):
    CLEAN = "CLEAN"
    DEGRADED = "DEGRADED"
    UNUSABLE = "UNUSABLE"


class ReplayIssueKind(str, Enum):
    # UNUSABLE: replay order or book existence cannot be trusted.
    INSUFFICIENT_QUOTES = "INSUFFICIENT_QUOTES"
    SEQUENCE_REGRESSION = "SEQUENCE_REGRESSION"
    NO_BOOK_AT_SUBMISSION = "NO_BOOK_AT_SUBMISSION"
    # DEGRADED: usable only with explicit human acknowledgement.
    AMBIGUOUS_ARRIVAL_ORDER = "AMBIGUOUS_ARRIVAL_ORDER"
    QUOTE_GAP = "QUOTE_GAP"
    WIDE_SPREAD = "WIDE_SPREAD"
    MID_JUMP = "MID_JUMP"
    THIN_BOOK = "THIN_BOOK"
    KNOWLEDGE_LAG = "KNOWLEDGE_LAG"
    STALE_BOOK_AT_SUBMISSION = "STALE_BOOK_AT_SUBMISSION"
    TRADE_OUTSIDE_QUOTE = "TRADE_OUTSIDE_QUOTE"


UNUSABLE_ISSUES = frozenset(
    {
        ReplayIssueKind.INSUFFICIENT_QUOTES,
        ReplayIssueKind.SEQUENCE_REGRESSION,
        ReplayIssueKind.NO_BOOK_AT_SUBMISSION,
    }
)


@dataclass(frozen=True)
class ReplayQualityPolicy:
    minimum_quotes_per_instrument: int
    maximum_quote_gap_ms: int
    maximum_spread_bps: Decimal
    maximum_mid_jump_bps: Decimal
    minimum_displayed_quantity: Decimal
    maximum_knowledge_lag_ms: int
    maximum_quote_age_at_submission_ms: int
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.minimum_quotes_per_instrument < 1:
            raise ValueError("minimum_quotes_per_instrument must be positive")
        for name in (
            "maximum_quote_gap_ms",
            "maximum_knowledge_lag_ms",
            "maximum_quote_age_at_submission_ms",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        for name in (
            "maximum_spread_bps",
            "maximum_mid_jump_bps",
            "minimum_displayed_quantity",
        ):
            value = getattr(self, name)
            if not value.is_finite() or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if not self.rationale.strip():
            raise ValueError("replay quality policy rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError(
                "replay quality policy requires evidence references"
            )

    @property
    def policy_id(self) -> str:
        return _content_id(
            "replay-quality-policy",
            {
                "minimum_quotes_per_instrument": (
                    self.minimum_quotes_per_instrument
                ),
                "maximum_quote_gap_ms": self.maximum_quote_gap_ms,
                "maximum_spread_bps": str(self.maximum_spread_bps),
                "maximum_mid_jump_bps": str(self.maximum_mid_jump_bps),
                "minimum_displayed_quantity": str(
                    self.minimum_displayed_quantity
                ),
                "maximum_knowledge_lag_ms": self.maximum_knowledge_lag_ms,
                "maximum_quote_age_at_submission_ms": (
                    self.maximum_quote_age_at_submission_ms
                ),
                "rationale": self.rationale,
                "evidence_references": sorted(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class ReplayIssue:
    kind: ReplayIssueKind
    execution_instrument_id: str
    reference_id: str
    detail: str


@dataclass(frozen=True)
class InstrumentReplayProfile:
    execution_instrument_id: str
    quote_count: int
    trade_count: int
    maximum_quote_gap_ms: int | None
    maximum_spread_bps: Decimal | None
    maximum_mid_jump_bps: Decimal | None
    minimum_displayed_quantity: Decimal | None
    maximum_knowledge_lag_ms: int | None


@dataclass(frozen=True)
class ReplayQualityReport:
    report_id: str
    dataset_id: str
    policy_id: str
    market_latency_ms: int
    intent_ids: tuple[str, ...]
    state: ReplayQualityState
    profiles: tuple[InstrumentReplayProfile, ...]
    issues: tuple[ReplayIssue, ...]
    simulation_authority: str
    capital_authority: str


class ReplayQualityEngine:
    def evaluate(
        self,
        *,
        dataset: HistoricalReplayDataset,
        policy: ReplayQualityPolicy,
        intents: tuple[SimulationOrderIntent, ...] = (),
        market_latency_ms: int = 0,
    ) -> ReplayQualityReport:
        if dataset.dataset_id != historical_replay_dataset_identity(dataset):
            raise ValueError("historical replay dataset identity mismatch")
        if market_latency_ms < 0:
            raise ValueError("market latency cannot be negative")
        for intent in intents:
            if intent.intent_id != simulation_order_intent_identity(intent):
                raise ValueError("simulation order intent identity mismatch")
            if (
                intent.execution_instrument_id
                not in dataset.execution_instrument_ids
            ):
                raise ValueError(
                    "order instrument is absent from replay dataset"
                )

        issues: list[ReplayIssue] = []
        profiles: list[InstrumentReplayProfile] = []
        latency = timedelta(milliseconds=market_latency_ms)
        for instrument_id in dataset.execution_instrument_ids:
            quotes = tuple(
                item
                for item in dataset.events
                if isinstance(item, TopOfBookQuote)
                and item.execution_instrument_id == instrument_id
            )
            trades = tuple(
                item
                for item in dataset.events
                if isinstance(item, TradePrint)
                and item.execution_instrument_id == instrument_id
            )
            profiles.append(
                self._profile(
                    instrument_id=instrument_id,
                    quotes=quotes,
                    trades=trades,
                    policy=policy,
                    issues=issues,
                )
            )
            for intent in intents:
                if intent.execution_instrument_id != instrument_id:
                    continue
                known = [
                    quote
                    for quote in quotes
                    if quote.knowledge_time + latency <= intent.submitted_at
                ]
                if not known:
                    issues.append(
                        ReplayIssue(
                            ReplayIssueKind.NO_BOOK_AT_SUBMISSION,
                            instrument_id,
                            intent.intent_id,
                            "no quote was available when the order was "
                            "submitted",
                        )
                    )
                    continue
                age = _milliseconds(
                    intent.submitted_at - (known[-1].knowledge_time + latency)
                )
                if age > policy.maximum_quote_age_at_submission_ms:
                    issues.append(
                        ReplayIssue(
                            ReplayIssueKind.STALE_BOOK_AT_SUBMISSION,
                            instrument_id,
                            intent.intent_id,
                            f"latest book was {age}ms old at submission",
                        )
                    )

        ordered_issues = tuple(
            sorted(
                issues,
                key=lambda item: (
                    item.execution_instrument_id,
                    item.kind.value,
                    item.reference_id,
                    item.detail,
                ),
            )
        )
        kinds = {item.kind for item in ordered_issues}
        if kinds & UNUSABLE_ISSUES:
            state = ReplayQualityState.UNUSABLE
        elif kinds:
            state = ReplayQualityState.DEGRADED
        else:
            state = ReplayQualityState.CLEAN
        report = ReplayQualityReport(
            report_id="",
            dataset_id=dataset.dataset_id,
            policy_id=policy.policy_id,
            market_latency_ms=market_latency_ms,
            intent_ids=tuple(sorted(item.intent_id for item in intents)),
            state=state,
            profiles=tuple(profiles),
            issues=ordered_issues,
            simulation_authority="NONE",
            capital_authority="NONE",
        )
        return replace(report, report_id=replay_quality_report_identity(report))

    @staticmethod
    def _profile(
        *,
        instrument_id: str,
        quotes: tuple[TopOfBookQuote, ...],
        trades: tuple[TradePrint, ...],
        policy: ReplayQualityPolicy,
        issues: list[ReplayIssue],
    ) -> InstrumentReplayProfile:
        def flag(kind: ReplayIssueKind, reference: str, detail: str) -> None:
            issues.append(ReplayIssue(kind, instrument_id, reference, detail))

        if len(quotes) < policy.minimum_quotes_per_instrument:
            flag(
                ReplayIssueKind.INSUFFICIENT_QUOTES,
                instrument_id,
                f"{len(quotes)} quotes below minimum "
                f"{policy.minimum_quotes_per_instrument}",
            )
        max_gap: int | None = None
        max_spread: Decimal | None = None
        max_jump: Decimal | None = None
        min_size: Decimal | None = None
        max_lag: int | None = None
        previous: TopOfBookQuote | None = None
        for quote in quotes:
            mid = (quote.bid_price + quote.ask_price) / 2
            spread = (quote.ask_price - quote.bid_price) / mid * Decimal("10000")
            size = min(quote.bid_quantity, quote.ask_quantity)
            lag = _milliseconds(quote.knowledge_time - quote.event_time)
            max_spread = spread if max_spread is None else max(max_spread, spread)
            min_size = size if min_size is None else min(min_size, size)
            max_lag = lag if max_lag is None else max(max_lag, lag)
            if spread > policy.maximum_spread_bps:
                flag(ReplayIssueKind.WIDE_SPREAD, quote.event_id, f"spread {spread}bps")
            if size < policy.minimum_displayed_quantity:
                flag(ReplayIssueKind.THIN_BOOK, quote.event_id, f"displayed {size}")
            if lag > policy.maximum_knowledge_lag_ms:
                flag(ReplayIssueKind.KNOWLEDGE_LAG, quote.event_id, f"known {lag}ms after event")
            if previous is not None:
                gap = _milliseconds(quote.knowledge_time - previous.knowledge_time)
                max_gap = gap if max_gap is None else max(max_gap, gap)
                if gap > policy.maximum_quote_gap_ms:
                    flag(ReplayIssueKind.QUOTE_GAP, quote.event_id, f"{gap}ms since previous quote")
                if gap == 0:
                    flag(
                        ReplayIssueKind.AMBIGUOUS_ARRIVAL_ORDER,
                        quote.event_id,
                        "shares knowledge time with previous quote",
                    )
                if quote.sequence <= previous.sequence:
                    flag(
                        ReplayIssueKind.SEQUENCE_REGRESSION,
                        quote.event_id,
                        f"sequence {quote.sequence} after {previous.sequence}",
                    )
                previous_mid = (previous.bid_price + previous.ask_price) / 2
                jump = abs(mid / previous_mid - 1) * Decimal("10000")
                max_jump = jump if max_jump is None else max(max_jump, jump)
                if jump > policy.maximum_mid_jump_bps:
                    flag(ReplayIssueKind.MID_JUMP, quote.event_id, f"mid moved {jump}bps")
            previous = quote

        for trade in trades:
            known = [q for q in quotes if q.knowledge_time <= trade.knowledge_time]
            if known and not (
                known[-1].bid_price <= trade.price <= known[-1].ask_price
            ):
                flag(
                    ReplayIssueKind.TRADE_OUTSIDE_QUOTE,
                    trade.event_id,
                    f"trade {trade.price} outside "
                    f"{known[-1].bid_price}/{known[-1].ask_price}",
                )
        return InstrumentReplayProfile(
            execution_instrument_id=instrument_id,
            quote_count=len(quotes),
            trade_count=len(trades),
            maximum_quote_gap_ms=max_gap,
            maximum_spread_bps=max_spread,
            maximum_mid_jump_bps=max_jump,
            minimum_displayed_quantity=min_size,
            maximum_knowledge_lag_ms=max_lag,
        )


class ReplayQualityReportStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS replay_quality_reports (
                report_id VARCHAR PRIMARY KEY,
                dataset_id VARCHAR NOT NULL,
                state VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, report: ReplayQualityReport) -> bool:
        if report.report_id != replay_quality_report_identity(report):
            raise ValueError("replay quality report identity mismatch")
        payload = json.dumps(
            replay_quality_report_payload(report),
            sort_keys=True,
            separators=(",", ":"),
        )
        row = self._con.execute(
            "SELECT payload_json FROM replay_quality_reports WHERE report_id = ?",
            [report.report_id],
        ).fetchone()
        if row is not None:
            if str(row[0]) != payload:
                raise ValueError("replay quality report identity conflict")
            return False
        self._con.execute(
            "INSERT INTO replay_quality_reports VALUES (?, ?, ?, ?)",
            [report.report_id, report.dataset_id, report.state.value, payload],
        )
        return True

    def close(self) -> None:
        self._con.close()


def replay_quality_report_payload(report: ReplayQualityReport) -> dict[str, object]:
    return {
        "dataset_id": report.dataset_id,
        "policy_id": report.policy_id,
        "market_latency_ms": report.market_latency_ms,
        "intent_ids": list(report.intent_ids),
        "state": report.state.value,
        "profiles": [
            {
                "execution_instrument_id": item.execution_instrument_id,
                "quote_count": item.quote_count,
                "trade_count": item.trade_count,
                "maximum_quote_gap_ms": item.maximum_quote_gap_ms,
                "maximum_spread_bps": _optional(item.maximum_spread_bps),
                "maximum_mid_jump_bps": _optional(item.maximum_mid_jump_bps),
                "minimum_displayed_quantity": _optional(
                    item.minimum_displayed_quantity
                ),
                "maximum_knowledge_lag_ms": item.maximum_knowledge_lag_ms,
            }
            for item in report.profiles
        ],
        "issues": [
            {
                "kind": item.kind.value,
                "execution_instrument_id": item.execution_instrument_id,
                "reference_id": item.reference_id,
                "detail": item.detail,
            }
            for item in report.issues
        ],
        "simulation_authority": report.simulation_authority,
        "capital_authority": report.capital_authority,
    }


def replay_quality_report_identity(report: ReplayQualityReport) -> str:
    return _content_id("replay-quality-report", replay_quality_report_payload(report))


def _milliseconds(delta: timedelta) -> int:
    return (
        delta.days * 86_400_000
        + delta.seconds * 1_000
        + delta.microseconds // 1_000
    )


def _optional(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
