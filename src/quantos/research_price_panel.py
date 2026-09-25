"""Stage 13.4 — point-in-time adjusted and total-return research panels.

Joins four frozen inputs into the return observations the Stage 9 research
lab and Stage 10 portfolio engine consume:

* raw, unadjusted session closes with knowledge times (revisions allowed;
  the latest version known at the decision time wins);
* the exchange session calendar (Stage 13.3), which defines which sessions
  must have a close;
* corporate actions known at the decision time (Stage 13.2);
* the Security Master (Stage 13.1), which must know every security.

Nothing is forward-filled. A missing close inside a security's life, a
close on a non-session day, a close known before the session closed, or an
unpriceable corporate action makes that security INCOMPLETE and keeps it
out of synchronous datasets, with the exact reason recorded.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from .corporate_actions import (
    AdjustmentMode,
    CorporateActionEngine,
    CorporateActionEvent,
    corporate_action_identity,
    TERMINATING_KINDS,
)
from .exchange_calendar import SessionCalendar, session_calendar_identity
from .portfolio_construction import PortfolioReturnObservation
from .security_master import SecurityMaster, SecurityRecord


class PanelIssueKind(str, Enum):
    UNKNOWN_SECURITY = "UNKNOWN_SECURITY"
    CLOSE_ON_NON_SESSION = "CLOSE_ON_NON_SESSION"
    CLOSE_KNOWN_BEFORE_SESSION_CLOSE = "CLOSE_KNOWN_BEFORE_SESSION_CLOSE"
    MISSING_SESSION_CLOSE = "MISSING_SESSION_CLOSE"
    TERMINATION_NOT_ON_SESSION = "TERMINATION_NOT_ON_SESSION"
    CORPORATE_ACTION_UNRESOLVED = "CORPORATE_ACTION_UNRESOLVED"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"


@dataclass(frozen=True)
class SessionCloseObservation:
    security_id: str
    session_date: date
    close: Decimal
    knowledge_time: datetime
    source_fact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.security_id.strip():
            raise ValueError("close observation requires security_id")
        if not self.close.is_finite() or self.close <= 0:
            raise ValueError("close must be finite and positive")
        if self.knowledge_time.tzinfo is None:
            raise ValueError("knowledge_time must be timezone-aware")
        if not self.source_fact_ids or not all(i.strip() for i in self.source_fact_ids):
            raise ValueError("close observation requires source fact IDs")

    @property
    def fact_id(self) -> str:
        return _content_id(
            "session-close-fact",
            {
                "security_id": self.security_id,
                "session_date": self.session_date.isoformat(),
                "close": str(self.close),
                "knowledge_time": self.knowledge_time.isoformat(),
                "source_fact_ids": sorted(self.source_fact_ids),
            },
        )


@dataclass(frozen=True)
class PanelIssue:
    kind: PanelIssueKind
    security_id: str
    detail: str


@dataclass(frozen=True)
class AdjustedCloseSeries:
    security_id: str
    mode: AdjustmentMode
    adjustment_series_id: str
    closes: tuple[tuple[date, Decimal], ...]


@dataclass(frozen=True)
class ResearchReturnPanel:
    panel_id: str
    calendar_id: str
    decision_time: datetime
    first_session: date
    last_session: date
    complete_security_ids: tuple[str, ...]
    incomplete_security_ids: tuple[str, ...]
    observations: tuple[PortfolioReturnObservation, ...]
    adjusted_closes: tuple[AdjustedCloseSeries, ...]
    issues: tuple[PanelIssue, ...]

    def portfolio_observations(
        self,
        security_ids: tuple[str, ...],
    ) -> tuple[PortfolioReturnObservation, ...]:
        """Synchronous observations for complete securities only."""

        incomplete = set(security_ids) & set(self.incomplete_security_ids)
        if incomplete:
            raise ValueError(
                "requested securities are INCOMPLETE: " + ", ".join(sorted(incomplete))
            )
        unknown = set(security_ids) - set(self.complete_security_ids)
        if unknown:
            raise ValueError("securities absent from panel: " + ", ".join(sorted(unknown)))
        chosen = tuple(
            item for item in self.observations if item.security_id in set(security_ids)
        )
        periods = {
            security_id: tuple(
                sorted(o.period_end for o in chosen if o.security_id == security_id)
            )
            for security_id in security_ids
        }
        if len(set(periods.values())) != 1:
            raise ValueError(
                "security histories are not synchronous; choose a common window"
            )
        return chosen


class ResearchReturnPanelBuilder:
    def build(
        self,
        *,
        security_ids: tuple[str, ...],
        closes: tuple[SessionCloseObservation, ...],
        actions: tuple[CorporateActionEvent, ...],
        calendar: SessionCalendar,
        first_session: date,
        last_session: date,
        decision_time: datetime,
        master: SecurityMaster,
        adjustment_mode: AdjustmentMode = AdjustmentMode.TOTAL_RETURN,
        counterparty_prices: dict[tuple[str, date], Decimal] | None = None,
        minimum_returns: int = 1,
    ) -> ResearchReturnPanel:
        if calendar.calendar_id != session_calendar_identity(calendar):
            raise ValueError("session calendar identity mismatch")
        if decision_time.tzinfo is None:
            raise ValueError("decision_time must be timezone-aware")
        if len(set(security_ids)) != len(security_ids) or not security_ids:
            raise ValueError("panel requires distinct securities")
        sessions = calendar.sessions_between(first_session, last_session)
        if not sessions or sessions[0].session_date != first_session or (
            sessions[-1].session_date != last_session
        ):
            raise ValueError("panel bounds must be calendar sessions")
        if sessions[-1].close_utc > decision_time:
            raise ValueError("last session closes after the decision time")
        session_by_date = {s.session_date: s for s in sessions}
        known_securities = {
            record.security_id
            for record in master.records(known_at=decision_time)
            if isinstance(record, SecurityRecord)
        }
        engine = CorporateActionEngine()
        issues: list[PanelIssue] = []
        observations: list[PortfolioReturnObservation] = []
        adjusted: list[AdjustedCloseSeries] = []
        complete: list[str] = []
        incomplete: list[str] = []

        for security_id in sorted(security_ids):
            own_issues: list[PanelIssue] = []

            def flag(kind: PanelIssueKind, detail: str) -> None:
                own_issues.append(PanelIssue(kind, security_id, detail))

            if security_id not in known_securities:
                flag(PanelIssueKind.UNKNOWN_SECURITY, "not in the Security Master at decision time")
            latest: dict[date, SessionCloseObservation] = {}
            for close in closes:
                if close.security_id != security_id or close.knowledge_time > decision_time:
                    continue
                if not first_session <= close.session_date <= last_session:
                    continue
                session = session_by_date.get(close.session_date)
                if session is None:
                    flag(PanelIssueKind.CLOSE_ON_NON_SESSION, f"close on {close.session_date}")
                    continue
                if close.knowledge_time < session.close_utc:
                    flag(
                        PanelIssueKind.CLOSE_KNOWN_BEFORE_SESSION_CLOSE,
                        f"{close.session_date} known {close.knowledge_time.isoformat()}",
                    )
                    continue
                prior = latest.get(close.session_date)
                if prior is None or close.knowledge_time > prior.knowledge_time:
                    latest[close.session_date] = close
                elif close.knowledge_time == prior.knowledge_time and close.close != prior.close:
                    raise ValueError("two different closes share one knowledge time")
            own_actions = tuple(
                a
                for a in actions
                if a.security_id == security_id and a.knowledge_time <= decision_time
            )
            terminal = next((a for a in own_actions if a.kind in TERMINATING_KINDS), None)
            if terminal is not None and terminal.ex_date not in session_by_date and (
                first_session <= terminal.ex_date <= last_session
            ):
                flag(PanelIssueKind.TERMINATION_NOT_ON_SESSION, f"ex date {terminal.ex_date}")
            life_end = (
                terminal.ex_date
                if terminal is not None and terminal.ex_date <= last_session
                else last_session
            )
            priced = sorted(latest)
            if priced:
                expected = [
                    s.session_date
                    for s in sessions
                    if priced[0] <= s.session_date <= life_end
                    and not (terminal is not None and s.session_date == terminal.ex_date)
                ]
                for day in expected:
                    if day not in latest:
                        flag(PanelIssueKind.MISSING_SESSION_CLOSE, f"no close for {day}")
            prices = {day: latest[day].close for day in priced}
            relevant_actions = tuple(a for a in own_actions if a.ex_date <= last_session)
            series = engine.total_returns(
                security_id=security_id,
                actions=relevant_actions,
                known_at=decision_time,
                prices=prices,
                counterparty_prices=counterparty_prices,
                terminate_without_close=True,
            )
            for item in series.issues:
                flag(PanelIssueKind.CORPORATE_ACTION_UNRESOLVED, f"{item.kind.value} on {item.on}: {item.detail}")
            if len(series.observations) < minimum_returns:
                flag(PanelIssueKind.INSUFFICIENT_HISTORY, f"{len(series.observations)} returns")

            if own_issues:
                issues.extend(own_issues)
                incomplete.append(security_id)
                continue
            action_by_id = {corporate_action_identity(a): a for a in relevant_actions}
            for item in series.observations:
                start_close = latest[item.start]
                end_session = session_by_date[item.end]
                end_close = latest.get(item.end)
                knowledge = max(
                    [end_session.close_utc, start_close.knowledge_time]
                    + ([end_close.knowledge_time] if end_close else [])
                    + [action_by_id[e].knowledge_time for e in item.event_ids]
                )
                facts = [start_close.fact_id] + ([end_close.fact_id] if end_close else [])
                observations.append(
                    PortfolioReturnObservation(
                        security_id=security_id,
                        period_start=session_by_date[item.start].close_utc,
                        period_end=end_session.close_utc,
                        knowledge_time=knowledge,
                        total_return=item.total_return,
                        source_fact_ids=tuple(facts + list(item.event_ids)),
                    )
                )
            factors = engine.adjustment_factors(
                security_id=security_id,
                actions=tuple(a for a in relevant_actions if a.kind not in TERMINATING_KINDS),
                known_at=decision_time,
                mode=adjustment_mode,
                prices=prices,
                counterparty_prices=counterparty_prices,
            )
            adjusted.append(
                AdjustedCloseSeries(
                    security_id=security_id,
                    mode=adjustment_mode,
                    adjustment_series_id=factors.series_id,
                    closes=tuple(
                        (day, price) for day, price in sorted(factors.adjust(prices).items())
                    ),
                )
            )
            complete.append(security_id)

        panel = ResearchReturnPanel(
            panel_id="",
            calendar_id=calendar.calendar_id,
            decision_time=decision_time,
            first_session=first_session,
            last_session=last_session,
            complete_security_ids=tuple(complete),
            incomplete_security_ids=tuple(incomplete),
            observations=tuple(observations),
            adjusted_closes=tuple(adjusted),
            issues=tuple(issues),
        )
        return replace(panel, panel_id=research_return_panel_identity(panel))


def research_return_panel_identity(panel: ResearchReturnPanel) -> str:
    return _content_id(
        "research-return-panel",
        {
            "calendar_id": panel.calendar_id,
            "decision_time": panel.decision_time.isoformat(),
            "first_session": panel.first_session.isoformat(),
            "last_session": panel.last_session.isoformat(),
            "complete_security_ids": list(panel.complete_security_ids),
            "incomplete_security_ids": list(panel.incomplete_security_ids),
            "observation_ids": [o.observation_id for o in panel.observations],
            "adjusted_closes": [
                [a.security_id, a.mode.value, a.adjustment_series_id,
                 [[d.isoformat(), str(p)] for d, p in a.closes]]
                for a in panel.adjusted_closes
            ],
            "issues": [[i.kind.value, i.security_id, i.detail] for i in panel.issues],
        },
    )


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
