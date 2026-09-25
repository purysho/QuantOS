"""Stage 13.3 — exchange session calendars.

QuantOS owns a frozen, rule-based session calendar (XNYS first) and
treats the ``exchange_calendars`` package as an external engine to be
differentially compared, never trusted blindly.

A ``SessionCalendar`` is a content-addressed artifact: the exact list of
sessions (local date, UTC open, UTC close, early-close flag) over a frozen
range. Queries outside the range fail closed instead of extrapolating.

Unscheduled closures (national days of mourning, weather) cannot be derived
from rules. They live in an explicit, evidence-referenced table; anything
missing from it shows up as a differential mismatch rather than silently.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from importlib.metadata import PackageNotFoundError, version as package_version
from zoneinfo import ZoneInfo


class CalendarSource(str, Enum):
    QUANTOS_RULES = "QUANTOS_RULES"
    EXCHANGE_CALENDARS = "EXCHANGE_CALENDARS"


class CalendarDifferentialState(str, Enum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"


@dataclass(frozen=True)
class TradingSession:
    session_date: date
    open_utc: datetime
    close_utc: datetime
    early_close: bool


@dataclass(frozen=True)
class SessionCalendar:
    calendar_id: str
    exchange_mic: str
    timezone_name: str
    source: CalendarSource
    source_version: str
    start: date
    end: date
    sessions: tuple[TradingSession, ...]

    def _require_in_range(self, day: date) -> None:
        if not self.start <= day <= self.end:
            raise ValueError(
                f"{day} is outside the frozen {self.exchange_mic} calendar "
                f"range {self.start}..{self.end}"
            )

    def _index(self) -> dict[date, TradingSession]:
        return {item.session_date: item for item in self.sessions}

    def is_session(self, day: date) -> bool:
        self._require_in_range(day)
        return day in self._index()

    def session(self, day: date) -> TradingSession:
        self._require_in_range(day)
        found = self._index().get(day)
        if found is None:
            raise ValueError(f"{day} is not a {self.exchange_mic} session")
        return found

    def sessions_between(self, first: date, last: date) -> tuple[TradingSession, ...]:
        self._require_in_range(first)
        self._require_in_range(last)
        return tuple(s for s in self.sessions if first <= s.session_date <= last)

    def previous_session(self, day: date) -> TradingSession:
        self._require_in_range(day)
        earlier = [s for s in self.sessions if s.session_date < day]
        if not earlier:
            raise ValueError("no earlier session inside the frozen range")
        return earlier[-1]

    def next_session(self, day: date) -> TradingSession:
        self._require_in_range(day)
        later = [s for s in self.sessions if s.session_date > day]
        if not later:
            raise ValueError("no later session inside the frozen range")
        return later[0]


@dataclass(frozen=True)
class UnscheduledClosure:
    day: date
    reason: str
    evidence_reference: str


# Unscheduled full-day NYSE closures inside the frozen rule range.
XNYS_UNSCHEDULED_CLOSURES = (
    UnscheduledClosure(date(2012, 10, 29), "Hurricane Sandy", "nyse-notice:2012-10-28"),
    UnscheduledClosure(date(2012, 10, 30), "Hurricane Sandy", "nyse-notice:2012-10-29"),
    UnscheduledClosure(date(2018, 12, 5), "National Day of Mourning, George H.W. Bush", "nyse-notice:2018-12-01"),
    UnscheduledClosure(date(2025, 1, 9), "National Day of Mourning, Jimmy Carter", "nyse-notice:2024-12-30"),
)

XNYS_RULE_RANGE = (date(2010, 1, 1), date(2030, 12, 31))


class XNYSRuleCalendarBuilder:
    """Independent New York Stock Exchange session rules.

    Regular session 09:30-16:00 America/New_York; early close 13:00 on the
    trading day before Independence Day, the day after Thanksgiving and
    Christmas Eve. Saturday holidays are observed on Friday and Sunday
    holidays on Monday, except that a Saturday New Year's Day is not
    observed. Juneteenth applies from 2022.
    """

    MIC = "XNYS"
    TIMEZONE = "America/New_York"
    RULES_VERSION = "13.3"
    OPEN = time(9, 30)
    CLOSE = time(16, 0)
    EARLY_CLOSE = time(13, 0)

    def build(self, *, start: date, end: date) -> SessionCalendar:
        low, high = XNYS_RULE_RANGE
        if start < low or end > high or end < start:
            raise ValueError(
                f"XNYS rules are frozen for {low}..{high} only"
            )
        holidays: set[date] = set()
        for year in range(start.year - 1, end.year + 2):
            holidays |= _xnys_holidays(year)
        holidays |= {item.day for item in XNYS_UNSCHEDULED_CLOSURES}
        zone = ZoneInfo(self.TIMEZONE)
        sessions = []
        day = start
        while day <= end:
            if day.weekday() < 5 and day not in holidays:
                early = _xnys_early_close(day)
                close = self.EARLY_CLOSE if early else self.CLOSE
                sessions.append(
                    TradingSession(
                        session_date=day,
                        open_utc=_utc(day, self.OPEN, zone),
                        close_utc=_utc(day, close, zone),
                        early_close=early,
                    )
                )
            day += timedelta(days=1)
        return _calendar(
            mic=self.MIC,
            timezone_name=self.TIMEZONE,
            source=CalendarSource.QUANTOS_RULES,
            source_version=self.RULES_VERSION,
            start=start,
            end=end,
            sessions=tuple(sessions),
        )


class ExchangeCalendarsAdapter:
    """Builds the same artifact from the exchange_calendars package."""

    DISTRIBUTION = "exchange_calendars"

    def __init__(self) -> None:
        try:
            self.version = package_version(self.DISTRIBUTION)
        except PackageNotFoundError as exc:
            raise RuntimeError("exchange_calendars is not installed") from exc

    def build(self, *, mic: str, start: date, end: date) -> SessionCalendar:
        import exchange_calendars

        calendar = exchange_calendars.get_calendar(
            mic,
            start=start.isoformat(),
            end=end.isoformat(),
        )
        schedule = calendar.schedule.loc[start.isoformat():end.isoformat()]
        regular_close = calendar.close_times[-1][1]
        sessions = tuple(
            TradingSession(
                session_date=label.date(),
                open_utc=row["open"].to_pydatetime().astimezone(timezone.utc),
                close_utc=row["close"].to_pydatetime().astimezone(timezone.utc),
                early_close=(
                    row["close"].tz_convert(calendar.tz).time() < regular_close
                ),
            )
            for label, row in schedule.iterrows()
        )
        return _calendar(
            mic=mic,
            timezone_name=str(calendar.tz),
            source=CalendarSource.EXCHANGE_CALENDARS,
            source_version=self.version,
            start=start,
            end=end,
            sessions=sessions,
        )


@dataclass(frozen=True)
class CalendarDifferential:
    differential_id: str
    reference_calendar_id: str
    external_calendar_id: str
    state: CalendarDifferentialState
    missing_in_external: tuple[date, ...]
    missing_in_reference: tuple[date, ...]
    time_mismatches: tuple[date, ...]
    trust_authority: str


def compare_calendars(
    reference: SessionCalendar,
    external: SessionCalendar,
) -> CalendarDifferential:
    for item in (reference, external):
        if item.calendar_id != session_calendar_identity(item):
            raise ValueError("session calendar identity mismatch")
    if (reference.exchange_mic, reference.start, reference.end) != (
        external.exchange_mic,
        external.start,
        external.end,
    ):
        raise ValueError("calendars cover different exchanges or ranges")
    ref = {s.session_date: s for s in reference.sessions}
    ext = {s.session_date: s for s in external.sessions}
    missing_ext = tuple(sorted(set(ref) - set(ext)))
    missing_ref = tuple(sorted(set(ext) - set(ref)))
    mismatched = tuple(
        sorted(
            day
            for day in set(ref) & set(ext)
            if (ref[day].open_utc, ref[day].close_utc, ref[day].early_close)
            != (ext[day].open_utc, ext[day].close_utc, ext[day].early_close)
        )
    )
    matched = not (missing_ext or missing_ref or mismatched)
    payload = {
        "reference_calendar_id": reference.calendar_id,
        "external_calendar_id": external.calendar_id,
        "missing_in_external": [d.isoformat() for d in missing_ext],
        "missing_in_reference": [d.isoformat() for d in missing_ref],
        "time_mismatches": [d.isoformat() for d in mismatched],
    }
    return CalendarDifferential(
        differential_id=_content_id("calendar-differential", payload),
        reference_calendar_id=reference.calendar_id,
        external_calendar_id=external.calendar_id,
        state=(
            CalendarDifferentialState.MATCH
            if matched
            else CalendarDifferentialState.MISMATCH
        ),
        missing_in_external=missing_ext,
        missing_in_reference=missing_ref,
        time_mismatches=mismatched,
        trust_authority="REFERENCE_MATCH_ONLY" if matched else "NONE",
    )


def session_calendar_payload(calendar: SessionCalendar) -> dict[str, object]:
    return {
        "exchange_mic": calendar.exchange_mic,
        "timezone_name": calendar.timezone_name,
        "source": calendar.source.value,
        "source_version": calendar.source_version,
        "start": calendar.start.isoformat(),
        "end": calendar.end.isoformat(),
        "sessions": [
            [
                s.session_date.isoformat(),
                s.open_utc.isoformat(),
                s.close_utc.isoformat(),
                s.early_close,
            ]
            for s in calendar.sessions
        ],
    }


def session_calendar_identity(calendar: SessionCalendar) -> str:
    return _content_id("session-calendar", session_calendar_payload(calendar))


def _calendar(**kwargs) -> SessionCalendar:
    sessions = kwargs["sessions"]
    dates = [s.session_date for s in sessions]
    if dates != sorted(set(dates)):
        raise ValueError("calendar sessions must be unique and ordered")
    for s in sessions:
        if s.open_utc.tzinfo is None or s.close_utc <= s.open_utc:
            raise ValueError("session times must be aware with close after open")
    calendar = SessionCalendar(
        calendar_id="",
        exchange_mic=kwargs["mic"],
        timezone_name=kwargs["timezone_name"],
        source=kwargs["source"],
        source_version=kwargs["source_version"],
        start=kwargs["start"],
        end=kwargs["end"],
        sessions=sessions,
    )
    return replace(calendar, calendar_id=session_calendar_identity(calendar))


def _utc(day: date, local: time, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, local, tzinfo=zone).astimezone(timezone.utc)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    following = date(year + (month == 12), month % 12 + 1, 1)
    last = following - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    # Anonymous Gregorian algorithm (Meeus/Jones/Butcher).
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _xnys_holidays(year: int) -> set[date]:
    days = set()
    new_year = date(year, 1, 1)
    if new_year.weekday() == 6:
        days.add(new_year + timedelta(days=1))
    elif new_year.weekday() < 5:
        days.add(new_year)
    days.add(_nth_weekday(year, 1, 0, 3))  # Martin Luther King Jr. Day
    days.add(_nth_weekday(year, 2, 0, 3))  # Washington's Birthday
    days.add(_easter(year) - timedelta(days=2))  # Good Friday
    days.add(_last_weekday(year, 5, 0))  # Memorial Day
    if year >= 2022:
        days.add(_observed(date(year, 6, 19)))  # Juneteenth
    days.add(_observed(date(year, 7, 4)))
    days.add(_nth_weekday(year, 9, 0, 1))  # Labor Day
    days.add(_nth_weekday(year, 11, 3, 4))  # Thanksgiving
    days.add(_observed(date(year, 12, 25)))
    return days


def _xnys_early_close(day: date) -> bool:
    if day.month == 7 and day.day == 3:
        return True
    if day.month == 12 and day.day == 24:
        return True
    thanksgiving = _nth_weekday(day.year, 11, 3, 4)
    return day == thanksgiving + timedelta(days=1)


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
