"""Stage 13.2 — corporate-action economics, adjustment and total return.

Corporate actions are bitemporal events keyed like Security Master records:
a later version of a ``record_key`` supersedes the earlier one from its
knowledge time onward, and a retraction withdraws it. Every computation
takes a knowledge cut-off, so a dividend announced (or corrected) later
cannot alter an earlier research state.

Nothing is inferred. A total-return dividend adjustment without the prior
close, a spin-off without the child price, a stock merger without the
acquirer price, or a delisting without proceeds is reported, never filled.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

import duckdb


class CorporateActionKind(str, Enum):
    CASH_DIVIDEND = "CASH_DIVIDEND"
    STOCK_SPLIT = "STOCK_SPLIT"
    STOCK_DIVIDEND = "STOCK_DIVIDEND"
    SPINOFF = "SPINOFF"
    CASH_MERGER = "CASH_MERGER"
    STOCK_MERGER = "STOCK_MERGER"
    DELISTING = "DELISTING"


TERMINATING_KINDS = frozenset(
    {
        CorporateActionKind.CASH_MERGER,
        CorporateActionKind.STOCK_MERGER,
        CorporateActionKind.DELISTING,
    }
)
SHARE_COUNT_KINDS = frozenset(
    {CorporateActionKind.STOCK_SPLIT, CorporateActionKind.STOCK_DIVIDEND}
)


class AdjustmentMode(str, Enum):
    SPLIT_ONLY = "SPLIT_ONLY"
    TOTAL_RETURN = "TOTAL_RETURN"


class ReturnIssueKind(str, Enum):
    MISSING_PRIOR_CLOSE = "MISSING_PRIOR_CLOSE"
    MISSING_COUNTERPARTY_PRICE = "MISSING_COUNTERPARTY_PRICE"
    MISSING_DELISTING_PROCEEDS = "MISSING_DELISTING_PROCEEDS"
    PRICE_AFTER_TERMINATION = "PRICE_AFTER_TERMINATION"
    AMBIGUOUS_SAME_WINDOW_ACTIONS = "AMBIGUOUS_SAME_WINDOW_ACTIONS"
    MISSING_SPINOFF_BASIS_ALLOCATION = "MISSING_SPINOFF_BASIS_ALLOCATION"


@dataclass(frozen=True, kw_only=True)
class CorporateActionEvent:
    record_key: str
    security_id: str
    kind: CorporateActionKind
    announced_at: datetime
    knowledge_time: datetime
    ex_date: date
    payment_date: date | None = None
    cash_amount: Decimal | None = None
    currency: str | None = None
    ratio_new: Decimal | None = None
    ratio_old: Decimal | None = None
    counterparty_security_id: str | None = None
    evidence_references: tuple[str, ...]
    retracted: bool = False

    def __post_init__(self) -> None:
        for name in ("record_key", "security_id"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
        for name in ("announced_at", "knowledge_time"):
            if getattr(self, name).tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.knowledge_time < self.announced_at:
            raise ValueError("corporate action known before announcement")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("corporate action requires evidence references")
        if self.payment_date is not None and self.payment_date < self.ex_date:
            raise ValueError("payment date precedes ex date")
        has_cash = self.cash_amount is not None
        has_ratio = self.ratio_new is not None or self.ratio_old is not None
        if has_cash:
            if not self.cash_amount.is_finite() or self.cash_amount < 0:
                raise ValueError("cash_amount must be finite and non-negative")
            if not self.currency or len(self.currency) != 3:
                raise ValueError("cash_amount requires a currency")
        elif self.currency is not None:
            raise ValueError("currency given without cash_amount")
        if has_ratio:
            if self.ratio_new is None or self.ratio_old is None:
                raise ValueError("ratio needs both ratio_new and ratio_old")
            for value in (self.ratio_new, self.ratio_old):
                if not value.is_finite() or value <= 0:
                    raise ValueError("ratio terms must be finite and positive")
        needs_counterparty = self.kind in {
            CorporateActionKind.SPINOFF,
            CorporateActionKind.STOCK_MERGER,
        }
        if needs_counterparty != bool(self.counterparty_security_id):
            raise ValueError(
                "counterparty security is required exactly for spin-offs "
                "and stock mergers"
            )
        if self.counterparty_security_id == self.security_id:
            raise ValueError("counterparty cannot be the security itself")
        kind = self.kind
        if kind is CorporateActionKind.CASH_DIVIDEND and (
            not has_cash or self.cash_amount == 0 or has_ratio
        ):
            raise ValueError("cash dividend needs a positive cash amount only")
        if kind in SHARE_COUNT_KINDS and (not has_ratio or has_cash):
            raise ValueError(f"{kind.value} needs a ratio only")
        if kind is CorporateActionKind.STOCK_SPLIT and (
            self.ratio_new == self.ratio_old
        ):
            raise ValueError("a 1:1 split changes nothing")
        if kind is CorporateActionKind.SPINOFF and (not has_ratio or has_cash):
            raise ValueError("spin-off needs a child-share ratio only")
        if kind is CorporateActionKind.CASH_MERGER and (not has_cash or has_ratio):
            raise ValueError("cash merger needs cash consideration only")
        if kind is CorporateActionKind.STOCK_MERGER and not has_ratio:
            raise ValueError("stock merger needs an exchange ratio")
        if kind is CorporateActionKind.DELISTING and has_ratio:
            raise ValueError("delisting carries optional proceeds only")

    @property
    def share_multiplier(self) -> Decimal:
        """Post-action shares per pre-action share for share-count actions."""

        if self.kind is CorporateActionKind.STOCK_SPLIT:
            return self.ratio_new / self.ratio_old
        if self.kind is CorporateActionKind.STOCK_DIVIDEND:
            return 1 + self.ratio_new / self.ratio_old
        raise ValueError(f"{self.kind.value} does not change the share count")

    @property
    def counterparty_ratio(self) -> Decimal:
        return self.ratio_new / self.ratio_old


class CorporateActionLedger:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS corporate_action_events (
                event_id VARCHAR PRIMARY KEY,
                record_key VARCHAR NOT NULL,
                security_id VARCHAR NOT NULL,
                knowledge_time TIMESTAMPTZ NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, event: CorporateActionEvent) -> bool:
        payload = corporate_action_payload(event)
        event_id = corporate_action_identity(event)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        row = self._con.execute(
            "SELECT payload_json FROM corporate_action_events WHERE event_id = ?",
            [event_id],
        ).fetchone()
        if row is not None:
            if str(row[0]) != encoded:
                raise ValueError("corporate action identity conflict")
            return False
        if self._con.execute(
            """
            SELECT 1 FROM corporate_action_events
            WHERE record_key = ? AND knowledge_time = ?
            """,
            [event.record_key, event.knowledge_time],
        ).fetchone():
            raise ValueError("two versions of one action share a knowledge time")
        owner = self._con.execute(
            "SELECT security_id FROM corporate_action_events WHERE record_key = ? LIMIT 1",
            [event.record_key],
        ).fetchone()
        if owner is not None and owner[0] != event.security_id:
            raise ValueError("record_key already belongs to another security")
        self._con.execute(
            "INSERT INTO corporate_action_events VALUES (?, ?, ?, ?, ?)",
            [event_id, event.record_key, event.security_id, event.knowledge_time, encoded],
        )
        return True

    def actions(
        self,
        *,
        security_id: str,
        known_at: datetime,
    ) -> tuple[CorporateActionEvent, ...]:
        if known_at.tzinfo is None:
            raise ValueError("known_at must be timezone-aware")
        rows = self._con.execute(
            """
            SELECT payload_json FROM corporate_action_events
            WHERE security_id = ? AND knowledge_time <= ?
            ORDER BY record_key, knowledge_time
            """,
            [security_id, known_at],
        ).fetchall()
        latest: dict[str, CorporateActionEvent] = {}
        for (payload,) in rows:
            event = corporate_action_from_payload(json.loads(payload))
            latest[event.record_key] = event
        return tuple(
            sorted(
                (item for item in latest.values() if not item.retracted),
                key=lambda item: (item.ex_date, item.record_key),
            )
        )

    def close(self) -> None:
        self._con.close()


@dataclass(frozen=True)
class AdjustmentFactor:
    ex_date: date
    factor: Decimal
    event_ids: tuple[str, ...]


@dataclass(frozen=True)
class AdjustmentFactorSeries:
    series_id: str
    security_id: str
    mode: AdjustmentMode
    known_at: datetime
    factors: tuple[AdjustmentFactor, ...]

    def cumulative_factor(self, on: date) -> Decimal:
        """Multiplier for a raw price observed on ``on``."""

        result = Decimal("1")
        for item in self.factors:
            if on < item.ex_date:
                result *= item.factor
        return result

    def adjust(self, prices: dict[date, Decimal]) -> dict[date, Decimal]:
        return {day: price * self.cumulative_factor(day) for day, price in prices.items()}


@dataclass(frozen=True)
class ReturnIssue:
    kind: ReturnIssueKind
    on: date
    detail: str


@dataclass(frozen=True)
class TotalReturnObservation:
    start: date
    end: date
    total_return: Decimal
    event_ids: tuple[str, ...]
    terminal: bool


@dataclass(frozen=True)
class TotalReturnSeries:
    series_id: str
    security_id: str
    known_at: datetime
    observations: tuple[TotalReturnObservation, ...]
    issues: tuple[ReturnIssue, ...]

    @property
    def complete(self) -> bool:
        return not self.issues


@dataclass(frozen=True)
class PositionLot:
    security_id: str
    quantity: Decimal
    cost_basis: Decimal


@dataclass(frozen=True)
class PositionActionOutcome:
    outcome_id: str
    starting: PositionLot
    lots: tuple[PositionLot, ...]
    cash: Decimal
    fractional_remainders: tuple[tuple[str, Decimal], ...]
    applied_event_ids: tuple[str, ...]
    issues: tuple[ReturnIssue, ...]
    unresolved_security_ids: tuple[str, ...] = field(default=())

    @property
    def complete(self) -> bool:
        return not self.issues


class CorporateActionEngine:
    def adjustment_factors(
        self,
        *,
        security_id: str,
        actions: tuple[CorporateActionEvent, ...],
        known_at: datetime,
        mode: AdjustmentMode,
        prices: dict[date, Decimal] | None = None,
        counterparty_prices: dict[tuple[str, date], Decimal] | None = None,
    ) -> AdjustmentFactorSeries:
        """Backward adjustment factors; fails closed on missing inputs."""

        _require_actions(security_id, actions, known_at)
        prices = prices or {}
        counterparty_prices = counterparty_prices or {}
        by_date: dict[date, list[tuple[Decimal, str]]] = {}
        for action in actions:
            event_id = corporate_action_identity(action)
            if action.kind in SHARE_COUNT_KINDS:
                factor = 1 / action.share_multiplier
            elif mode is AdjustmentMode.SPLIT_ONLY:
                continue
            elif action.kind is CorporateActionKind.CASH_DIVIDEND:
                prior = _prior_close(prices, action.ex_date)
                if prior is None:
                    raise ValueError(
                        f"total-return dividend adjustment needs the close before {action.ex_date}"
                    )
                factor = 1 - action.cash_amount / prior
            elif action.kind is CorporateActionKind.SPINOFF:
                prior = _prior_close(prices, action.ex_date)
                child = counterparty_prices.get(
                    (action.counterparty_security_id, action.ex_date)
                )
                if prior is None or child is None:
                    raise ValueError(
                        "spin-off adjustment needs the parent prior close and "
                        "child ex-date price"
                    )
                factor = 1 - action.counterparty_ratio * child / prior
            else:
                continue
            if factor <= 0:
                raise ValueError("adjustment factor must remain positive")
            by_date.setdefault(action.ex_date, []).append((factor, event_id))
        factors = []
        for ex_date in sorted(by_date):
            product = Decimal("1")
            for factor, _ in by_date[ex_date]:
                product *= factor
            factors.append(
                AdjustmentFactor(
                    ex_date=ex_date,
                    factor=product,
                    event_ids=tuple(sorted(item for _, item in by_date[ex_date])),
                )
            )
        payload = {
            "security_id": security_id,
            "mode": mode.value,
            "known_at": known_at.isoformat(),
            "factors": [
                {
                    "ex_date": item.ex_date.isoformat(),
                    "factor": str(item.factor),
                    "event_ids": list(item.event_ids),
                }
                for item in factors
            ],
        }
        return AdjustmentFactorSeries(
            series_id=_content_id("price-adjustment-series", payload),
            security_id=security_id,
            mode=mode,
            known_at=known_at,
            factors=tuple(factors),
        )

    def total_returns(
        self,
        *,
        security_id: str,
        actions: tuple[CorporateActionEvent, ...],
        known_at: datetime,
        prices: dict[date, Decimal],
        counterparty_prices: dict[tuple[str, date], Decimal] | None = None,
        terminate_without_close: bool = False,
    ) -> TotalReturnSeries:
        """Close-to-close total returns including every corporate action.

        An action with ex date in (previous close date, close date] belongs
        to that window. A terminating action ends the series with a final
        return from its proceeds; later prices are an issue, not data.
        """

        _require_actions(security_id, actions, known_at)
        counterparty_prices = counterparty_prices or {}
        days = sorted(prices)
        if any(prices[day] <= 0 for day in days):
            raise ValueError("close prices must be positive")
        issues: list[ReturnIssue] = []
        observations: list[TotalReturnObservation] = []
        terminal = next(
            (item for item in actions if item.kind in TERMINATING_KINDS), None
        )
        if terminal is not None:
            for day in days:
                if day > terminal.ex_date:
                    issues.append(
                        ReturnIssue(
                            ReturnIssueKind.PRICE_AFTER_TERMINATION,
                            day,
                            f"price observed after {terminal.kind.value} "
                            f"on {terminal.ex_date}",
                        )
                    )
            days = [day for day in days if day <= terminal.ex_date]
            if terminate_without_close and days and days[-1] < terminal.ex_date:
                # The security stopped trading before its terminal ex date;
                # the terminal window uses proceeds, never a closing print.
                days.append(terminal.ex_date)
        for previous, current in zip(days, days[1:]):
            window = [
                item
                for item in actions
                if previous < item.ex_date <= current
            ]
            ending = next((item for item in window if item.kind in TERMINATING_KINDS), None)
            if ending is not None:
                # Terminal proceeds replace the (possibly stale) closing print.
                proceeds = self._terminal_value(ending, counterparty_prices, issues)
                if proceeds is not None:
                    observations.append(
                        TotalReturnObservation(
                            start=previous,
                            end=ending.ex_date,
                            total_return=proceeds / prices[previous] - 1,
                            event_ids=(corporate_action_identity(ending),),
                            terminal=True,
                        )
                    )
                break
            share_actions = [item for item in window if item.kind in SHARE_COUNT_KINDS]
            income_actions = [item for item in window if item.kind not in SHARE_COUNT_KINDS]
            if share_actions and income_actions:
                issues.append(
                    ReturnIssue(
                        ReturnIssueKind.AMBIGUOUS_SAME_WINDOW_ACTIONS,
                        current,
                        "split and distribution in one window need an explicit per-share basis",
                    )
                )
                continue
            multiplier = Decimal("1")
            for item in share_actions:
                multiplier *= item.share_multiplier
            income = Decimal("0")
            missing = False
            for item in income_actions:
                if item.kind is CorporateActionKind.CASH_DIVIDEND:
                    income += item.cash_amount
                elif item.kind is CorporateActionKind.SPINOFF:
                    child = counterparty_prices.get(
                        (item.counterparty_security_id, current)
                    )
                    if child is None:
                        issues.append(
                            ReturnIssue(
                                ReturnIssueKind.MISSING_COUNTERPARTY_PRICE,
                                current,
                                f"spin-off child {item.counterparty_security_id} unpriced",
                            )
                        )
                        missing = True
                    else:
                        income += item.counterparty_ratio * child
            if missing:
                continue
            observations.append(
                TotalReturnObservation(
                    start=previous,
                    end=current,
                    total_return=(prices[current] * multiplier + income) / prices[previous] - 1,
                    event_ids=tuple(sorted(corporate_action_identity(i) for i in window)),
                    terminal=False,
                )
            )
        payload = {
            "security_id": security_id,
            "known_at": known_at.isoformat(),
            "prices": {day.isoformat(): str(value) for day, value in sorted(prices.items())},
            "action_ids": sorted(corporate_action_identity(i) for i in actions),
            "observations": [
                [o.start.isoformat(), o.end.isoformat(), str(o.total_return), o.terminal]
                for o in observations
            ],
            "issues": [[i.kind.value, i.on.isoformat(), i.detail] for i in issues],
        }
        return TotalReturnSeries(
            series_id=_content_id("total-return-series", payload),
            security_id=security_id,
            known_at=known_at,
            observations=tuple(observations),
            issues=tuple(issues),
        )

    def apply_to_position(
        self,
        *,
        lot: PositionLot,
        actions: tuple[CorporateActionEvent, ...],
        known_at: datetime,
        through: date,
        spinoff_basis_fraction: dict[str, Decimal] | None = None,
        allow_fractional_shares: bool = False,
    ) -> PositionActionOutcome:
        """Carry one lot through its actions with ex date on or before ``through``."""

        _require_actions(lot.security_id, actions, known_at)
        spinoff_basis_fraction = spinoff_basis_fraction or {}
        quantity = lot.quantity
        basis = lot.cost_basis
        cash = Decimal("0")
        lots: list[PositionLot] = []
        remainders: list[tuple[str, Decimal]] = []
        applied: list[str] = []
        issues: list[ReturnIssue] = []
        unresolved: list[str] = []
        alive = True

        def whole(security_id: str, shares: Decimal) -> Decimal:
            if allow_fractional_shares:
                return shares
            integral = shares.to_integral_value(rounding="ROUND_FLOOR")
            if integral != shares:
                remainders.append((security_id, shares - integral))
            return integral

        for action in actions:
            if action.ex_date > through or not alive:
                continue
            event_id = corporate_action_identity(action)
            applied.append(event_id)
            if action.kind in SHARE_COUNT_KINDS:
                quantity = whole(lot.security_id, quantity * action.share_multiplier)
            elif action.kind is CorporateActionKind.CASH_DIVIDEND:
                cash += quantity * action.cash_amount
            elif action.kind is CorporateActionKind.SPINOFF:
                fraction = spinoff_basis_fraction.get(event_id)
                if fraction is None or not (0 <= fraction <= 1):
                    issues.append(
                        ReturnIssue(
                            ReturnIssueKind.MISSING_SPINOFF_BASIS_ALLOCATION,
                            action.ex_date,
                            "spin-off cost-basis allocation must be supplied from evidence",
                        )
                    )
                    fraction = None
                child_quantity = whole(
                    action.counterparty_security_id,
                    quantity * action.counterparty_ratio,
                )
                child_basis = basis * fraction if fraction is not None else Decimal("0")
                if fraction is not None:
                    basis -= child_basis
                lots.append(
                    PositionLot(action.counterparty_security_id, child_quantity, child_basis)
                )
            elif action.kind is CorporateActionKind.CASH_MERGER:
                cash += quantity * action.cash_amount
                alive = False
            elif action.kind is CorporateActionKind.STOCK_MERGER:
                acquirer_quantity = whole(
                    action.counterparty_security_id,
                    quantity * action.counterparty_ratio,
                )
                lots.append(
                    PositionLot(action.counterparty_security_id, acquirer_quantity, basis)
                )
                if action.cash_amount is not None:
                    cash += quantity * action.cash_amount
                alive = False
            elif action.kind is CorporateActionKind.DELISTING:
                if action.cash_amount is None:
                    issues.append(
                        ReturnIssue(
                            ReturnIssueKind.MISSING_DELISTING_PROCEEDS,
                            action.ex_date,
                            "delisted position has unknown proceeds",
                        )
                    )
                    unresolved.append(lot.security_id)
                else:
                    cash += quantity * action.cash_amount
                alive = False
        if alive:
            lots.insert(0, PositionLot(lot.security_id, quantity, basis))
        elif lot.security_id in unresolved:
            lots.insert(0, PositionLot(lot.security_id, quantity, basis))
        payload = {
            "starting": _lot_payload(lot),
            "known_at": known_at.isoformat(),
            "through": through.isoformat(),
            "lots": [_lot_payload(item) for item in lots],
            "cash": str(cash),
            "fractional_remainders": [[s, str(q)] for s, q in remainders],
            "applied_event_ids": applied,
            "issues": [[i.kind.value, i.on.isoformat(), i.detail] for i in issues],
            "unresolved_security_ids": unresolved,
        }
        return PositionActionOutcome(
            outcome_id=_content_id("position-action-outcome", payload),
            starting=lot,
            lots=tuple(lots),
            cash=cash,
            fractional_remainders=tuple(remainders),
            applied_event_ids=tuple(applied),
            issues=tuple(issues),
            unresolved_security_ids=tuple(unresolved),
        )

    @staticmethod
    def _terminal_value(
        action: CorporateActionEvent,
        counterparty_prices: dict[tuple[str, date], Decimal],
        issues: list[ReturnIssue],
    ) -> Decimal | None:
        if action.kind is CorporateActionKind.CASH_MERGER:
            return action.cash_amount
        if action.kind is CorporateActionKind.DELISTING:
            if action.cash_amount is None:
                issues.append(
                    ReturnIssue(
                        ReturnIssueKind.MISSING_DELISTING_PROCEEDS,
                        action.ex_date,
                        "delisting return cannot be computed without proceeds",
                    )
                )
            return action.cash_amount
        price = counterparty_prices.get(
            (action.counterparty_security_id, action.ex_date)
        )
        if price is None:
            issues.append(
                ReturnIssue(
                    ReturnIssueKind.MISSING_COUNTERPARTY_PRICE,
                    action.ex_date,
                    f"acquirer {action.counterparty_security_id} unpriced on ex date",
                )
            )
            return None
        return action.counterparty_ratio * price + (action.cash_amount or Decimal("0"))


def corporate_action_payload(event: CorporateActionEvent) -> dict[str, object]:
    payload: dict[str, object] = {}
    for name in event.__dataclass_fields__:
        value = getattr(event, name)
        if isinstance(value, Enum):
            value = value.value
        elif isinstance(value, (date, datetime)):
            value = value.isoformat()
        elif isinstance(value, Decimal):
            value = str(value)
        elif isinstance(value, tuple):
            value = list(value)
        payload[name] = value
    return payload


def corporate_action_identity(event: CorporateActionEvent) -> str:
    return _content_id("corporate-action-event", corporate_action_payload(event))


def corporate_action_from_payload(payload: dict[str, object]) -> CorporateActionEvent:
    data = dict(payload)
    data["kind"] = CorporateActionKind(data["kind"])
    for name in ("announced_at", "knowledge_time"):
        data[name] = datetime.fromisoformat(data[name])
    for name in ("ex_date", "payment_date"):
        data[name] = date.fromisoformat(data[name]) if data[name] is not None else None
    for name in ("cash_amount", "ratio_new", "ratio_old"):
        data[name] = Decimal(data[name]) if data[name] is not None else None
    data["evidence_references"] = tuple(data["evidence_references"])
    return CorporateActionEvent(**data)


def _require_actions(
    security_id: str,
    actions: tuple[CorporateActionEvent, ...],
    known_at: datetime,
) -> None:
    if known_at.tzinfo is None:
        raise ValueError("known_at must be timezone-aware")
    keys = [item.record_key for item in actions]
    if len(keys) != len(set(keys)):
        raise ValueError("actions contain several versions of one record")
    for action in actions:
        if action.security_id != security_id:
            raise ValueError("action belongs to another security")
        if action.knowledge_time > known_at:
            raise ValueError("action was not yet known at the cut-off")
        if action.retracted:
            raise ValueError("retracted action supplied")
    terminating = [item for item in actions if item.kind in TERMINATING_KINDS]
    if len(terminating) > 1:
        raise ValueError("security has more than one terminating action")
    if terminating and any(item.ex_date > terminating[0].ex_date for item in actions):
        raise ValueError("action dated after the security terminated")


def _prior_close(prices: dict[date, Decimal], ex_date: date) -> Decimal | None:
    earlier = [day for day in prices if day < ex_date]
    return prices[max(earlier)] if earlier else None


def _lot_payload(lot: PositionLot) -> dict[str, str]:
    return {
        "security_id": lot.security_id,
        "quantity": str(lot.quantity),
        "cost_basis": str(lot.cost_basis),
    }


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
