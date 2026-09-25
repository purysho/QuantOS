"""Stage 17 — point-in-time market-data provider pipeline.

Provider-agnostic end-of-day capture with raw archival, revision-preserving
bitemporal storage, calendar completeness and staleness checks, OHLC
invariants, cross-provider reconciliation, provider corporate-action
cross-checks, and hand-off to the Stage 13.4 research panel.

Rules:

* raw, unadjusted prices only are stored as facts; First Current applies its
  own corporate actions (Stage 13.2). Provider-adjusted fields are kept for
  reconciliation, never used as truth;
* a bar is known at capture time; a later capture that differs is a new
  revision and both are kept;
* a bar captured before its session closed is intraday and never becomes a
  close;
* credentials come from ``SecretProvider`` and travel in request headers,
  never in URLs; every request passes the egress guard and kill switch;
* disagreement between providers is reported, not averaged.

Tiingo and Polygon adapters are included; which provider is licensed for
research use is an owner decision, and live use needs its API key.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Callable
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import duckdb

from .artifacts import SourceArtifactStore
from .corporate_actions import CorporateActionEvent, CorporateActionKind
from .exchange_calendar import SessionCalendar
from .security import SecretProvider, guarded

if TYPE_CHECKING:
    from .research_price_panel import SessionCloseObservation

Transport = Callable[[str, dict[str, str], float], tuple[bytes, str]]


class MarketDataError(ValueError):
    pass


@dataclass(frozen=True)
class DailyBar:
    provider: str
    provider_symbol: str
    security_id: str
    session_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    knowledge_time: datetime
    capture_id: str
    provider_adjusted_close: Decimal | None = None
    provider_dividend: Decimal | None = None
    provider_split_factor: Decimal | None = None

    @property
    def bar_id(self) -> str:
        return _content_id("daily-bar", _bar_payload(self))


@dataclass(frozen=True)
class MarketDataCapture:
    capture_id: str
    provider: str
    provider_symbol: str
    security_id: str
    request_description: str
    fetched_at: datetime
    raw_artifact_id: str | None
    bars: tuple[DailyBar, ...]


def _default_transport(url: str, headers: dict[str, str], timeout: float) -> tuple[bytes, str]:
    import requests

    guarded(url, "market-data")
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise MarketDataError("market-data request failed") from exc
    if response.status_code != 200:
        raise MarketDataError(f"market-data request failed with HTTP {response.status_code}")
    return response.content, response.headers.get("content-type", "application/json").split(";")[0]


class _ProviderAdapter:
    PROVIDER = ""
    SECRET_NAME = ""

    def __init__(
        self,
        *,
        secrets: SecretProvider | None = None,
        transport: Transport | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.secrets = secrets or SecretProvider()
        self.transport = transport or _default_transport
        self.timeout_seconds = timeout_seconds

    def capture(
        self,
        *,
        symbol: str,
        security_id: str,
        start: date,
        end: date,
        artifacts: SourceArtifactStore | None = None,
        fetched_at: datetime | None = None,
    ) -> MarketDataCapture:
        if not symbol.strip() or "/" in symbol or not security_id.strip():
            raise MarketDataError("symbol and security_id are required")
        if end < start:
            raise MarketDataError("end precedes start")
        url, headers = self.request(symbol=symbol, start=start, end=end)
        body, media_type = self.transport(url, headers, self.timeout_seconds)
        if not body:
            raise MarketDataError("provider returned an empty body")
        fetched_at = fetched_at or datetime.now(timezone.utc)
        artifact = (
            artifacts.put(source_uri=url, content=body, fetched_at=fetched_at, media_type=media_type or "application/json")
            if artifacts is not None
            else None
        )
        capture_id = _content_id(
            "market-data-capture",
            {
                "provider": self.PROVIDER,
                "symbol": symbol,
                "security_id": security_id,
                "url": url,
                "fetched_at": fetched_at.isoformat(),
                "sha256": hashlib.sha256(body).hexdigest(),
            },
        )
        bars = self.parse(body, symbol=symbol, security_id=security_id, fetched_at=fetched_at, capture_id=capture_id)
        for bar in bars:
            if not start <= bar.session_date <= end:
                raise MarketDataError(f"provider returned {bar.session_date} outside the request")
        return MarketDataCapture(
            capture_id=capture_id,
            provider=self.PROVIDER,
            provider_symbol=symbol,
            security_id=security_id,
            request_description=url,
            fetched_at=fetched_at,
            raw_artifact_id=artifact.artifact_id if artifact else None,
            bars=bars,
        )

    def _token(self) -> str:
        return self.secrets.get(self.SECRET_NAME).reveal()


class TiingoEodAdapter(_ProviderAdapter):
    """Tiingo end-of-day prices (``/tiingo/daily/<ticker>/prices``)."""

    PROVIDER = "tiingo"
    SECRET_NAME = "TIINGO_API_KEY"
    BASE_URL = "https://api.tiingo.com/tiingo/daily"

    def request(self, *, symbol: str, start: date, end: date) -> tuple[str, dict[str, str]]:
        query = urlencode({"startDate": start.isoformat(), "endDate": end.isoformat(), "format": "json"})
        return (
            f"{self.BASE_URL}/{symbol.lower()}/prices?{query}",
            {"Authorization": f"Token {self._token()}", "Content-Type": "application/json"},
        )

    def parse(self, body: bytes, *, symbol: str, security_id: str, fetched_at: datetime, capture_id: str) -> tuple[DailyBar, ...]:
        try:
            rows = json.loads(body)
        except json.JSONDecodeError as exc:
            raise MarketDataError("invalid Tiingo JSON") from exc
        if not isinstance(rows, list):
            raise MarketDataError(f"unexpected Tiingo response: {str(rows)[:200]}")
        bars = []
        for row in rows:
            try:
                session = date.fromisoformat(str(row["date"])[:10])
                bars.append(
                    DailyBar(
                        provider=self.PROVIDER,
                        provider_symbol=symbol,
                        security_id=security_id,
                        session_date=session,
                        open=_dec(row["open"]),
                        high=_dec(row["high"]),
                        low=_dec(row["low"]),
                        close=_dec(row["close"]),
                        volume=_dec(row["volume"]),
                        knowledge_time=fetched_at,
                        capture_id=capture_id,
                        provider_adjusted_close=_dec(row["adjClose"]) if row.get("adjClose") is not None else None,
                        provider_dividend=_dec(row.get("divCash", 0)),
                        provider_split_factor=_dec(row.get("splitFactor", 1)),
                    )
                )
            except (KeyError, TypeError) as exc:
                raise MarketDataError(f"Tiingo row missing field: {exc}") from exc
        return _unique_sessions(bars)


class PolygonDailyAdapter(_ProviderAdapter):
    """Polygon unadjusted daily aggregates (``/v2/aggs/.../range/1/day``)."""

    PROVIDER = "polygon"
    SECRET_NAME = "POLYGON_API_KEY"
    BASE_URL = "https://api.polygon.io/v2/aggs/ticker"
    SESSION_ZONE = ZoneInfo("America/New_York")

    def request(self, *, symbol: str, start: date, end: date) -> tuple[str, dict[str, str]]:
        query = urlencode({"adjusted": "false", "sort": "asc", "limit": 50000})
        return (
            f"{self.BASE_URL}/{symbol.upper()}/range/1/day/{start.isoformat()}/{end.isoformat()}?{query}",
            {"Authorization": f"Bearer {self._token()}"},
        )

    def parse(self, body: bytes, *, symbol: str, security_id: str, fetched_at: datetime, capture_id: str) -> tuple[DailyBar, ...]:
        try:
            document = json.loads(body)
        except json.JSONDecodeError as exc:
            raise MarketDataError("invalid Polygon JSON") from exc
        if document.get("status") not in {"OK", "DELAYED"}:
            raise MarketDataError(f"Polygon status {document.get('status')}")
        if document.get("adjusted") is not False:
            raise MarketDataError("Polygon response is adjusted; raw prices are required")
        bars = []
        for row in document.get("results", []) or []:
            try:
                opened = datetime.fromtimestamp(int(row["t"]) / 1000, tz=timezone.utc)
                bars.append(
                    DailyBar(
                        provider=self.PROVIDER,
                        provider_symbol=symbol,
                        security_id=security_id,
                        session_date=opened.astimezone(self.SESSION_ZONE).date(),
                        open=_dec(row["o"]),
                        high=_dec(row["h"]),
                        low=_dec(row["l"]),
                        close=_dec(row["c"]),
                        volume=_dec(row["v"]),
                        knowledge_time=fetched_at,
                        capture_id=capture_id,
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise MarketDataError(f"Polygon row malformed: {exc}") from exc
        return _unique_sessions(bars)


class BarStore:
    """Bitemporal, revision-preserving daily bar store."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_bars (
                bar_id VARCHAR PRIMARY KEY,
                provider VARCHAR NOT NULL,
                security_id VARCHAR NOT NULL,
                session_date DATE NOT NULL,
                knowledge_time TIMESTAMPTZ NOT NULL,
                values_fingerprint VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, capture: MarketDataCapture) -> dict[str, int]:
        counts = {"inserted": 0, "unchanged": 0, "revisions": 0}
        for bar in capture.bars:
            fingerprint = _values_fingerprint(bar)
            latest = self._con.execute(
                """
                SELECT values_fingerprint FROM daily_bars
                WHERE provider = ? AND security_id = ? AND session_date = ? AND knowledge_time <= ?
                ORDER BY knowledge_time DESC LIMIT 1
                """,
                [bar.provider, bar.security_id, bar.session_date, bar.knowledge_time],
            ).fetchone()
            if latest is not None and latest[0] == fingerprint:
                counts["unchanged"] += 1
                continue
            if self._con.execute("SELECT 1 FROM daily_bars WHERE bar_id = ?", [bar.bar_id]).fetchone():
                counts["unchanged"] += 1
                continue
            self._con.execute(
                "INSERT INTO daily_bars VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    bar.bar_id, bar.provider, bar.security_id, bar.session_date, bar.knowledge_time,
                    fingerprint, json.dumps(_bar_payload(bar), sort_keys=True),
                ],
            )
            counts["revisions" if latest is not None else "inserted"] += 1
        return counts

    def bars(self, *, provider: str, security_id: str, known_at: datetime) -> tuple[DailyBar, ...]:
        rows = self._con.execute(
            """
            SELECT payload_json FROM daily_bars
            WHERE provider = ? AND security_id = ? AND knowledge_time <= ?
            ORDER BY session_date, knowledge_time
            """,
            [provider, security_id, known_at],
        ).fetchall()
        latest: dict[date, DailyBar] = {}
        for (payload,) in rows:
            bar = _bar_from_payload(json.loads(payload))
            latest[bar.session_date] = bar
        return tuple(latest[d] for d in sorted(latest))

    def revision_count(self, *, provider: str, security_id: str, known_at: datetime) -> int:
        row = self._con.execute(
            """
            SELECT count(*) - count(DISTINCT session_date) FROM daily_bars
            WHERE provider = ? AND security_id = ? AND knowledge_time <= ?
            """,
            [provider, security_id, known_at],
        ).fetchone()
        return int(row[0])

    def close(self) -> None:
        self._con.close()


class QualityIssueKind(str, Enum):
    MISSING_SESSION = "MISSING_SESSION"
    BAR_ON_NON_SESSION = "BAR_ON_NON_SESSION"
    OHLC_VIOLATION = "OHLC_VIOLATION"
    NEGATIVE_VOLUME = "NEGATIVE_VOLUME"
    INTRADAY_CAPTURE = "INTRADAY_CAPTURE"
    STALE = "STALE"
    REVISED = "REVISED"


@dataclass(frozen=True)
class QualityIssue:
    kind: QualityIssueKind
    session_date: date | None
    detail: str


@dataclass(frozen=True)
class MarketDataQualityReport:
    report_id: str
    provider: str
    security_id: str
    first_session: date
    last_session: date
    known_at: datetime
    bar_count: int
    issues: tuple[QualityIssue, ...]

    @property
    def clean(self) -> bool:
        return not self.issues


def quality_report(
    *,
    store: BarStore,
    calendar: SessionCalendar,
    provider: str,
    security_id: str,
    first_session: date,
    last_session: date,
    known_at: datetime,
    stale_after_sessions: int = 1,
) -> MarketDataQualityReport:
    sessions = {s.session_date: s for s in calendar.sessions_between(first_session, last_session)}
    bars = [b for b in store.bars(provider=provider, security_id=security_id, known_at=known_at)
            if first_session <= b.session_date <= last_session]
    issues: list[QualityIssue] = []
    by_date = {b.session_date: b for b in bars}
    for bar in bars:
        session = sessions.get(bar.session_date)
        if session is None:
            issues.append(QualityIssue(QualityIssueKind.BAR_ON_NON_SESSION, bar.session_date, "no session"))
            continue
        if bar.knowledge_time < session.close_utc:
            issues.append(QualityIssue(QualityIssueKind.INTRADAY_CAPTURE, bar.session_date, "captured before the close"))
        if not (bar.low <= min(bar.open, bar.close) and max(bar.open, bar.close) <= bar.high and bar.low > 0):
            issues.append(QualityIssue(QualityIssueKind.OHLC_VIOLATION, bar.session_date, f"O{bar.open} H{bar.high} L{bar.low} C{bar.close}"))
        if bar.volume < 0:
            issues.append(QualityIssue(QualityIssueKind.NEGATIVE_VOLUME, bar.session_date, str(bar.volume)))
    closed = [d for d, s in sessions.items() if s.close_utc <= known_at]
    for day in sorted(closed):
        if day not in by_date:
            issues.append(QualityIssue(QualityIssueKind.MISSING_SESSION, day, "no bar"))
    if closed:
        latest_expected = max(closed)
        have = sorted(d for d in by_date if d in sessions)
        if not have or len([d for d in closed if d > have[-1]]) >= stale_after_sessions:
            issues.append(QualityIssue(QualityIssueKind.STALE, have[-1] if have else None,
                                       f"latest closed session {latest_expected} has no bar"))
    revisions = store.revision_count(provider=provider, security_id=security_id, known_at=known_at)
    if revisions:
        issues.append(QualityIssue(QualityIssueKind.REVISED, None, f"{revisions} revised bars preserved"))
    ordered = tuple(sorted(issues, key=lambda i: (i.kind.value, i.session_date or date.min, i.detail)))
    report = MarketDataQualityReport(
        report_id="",
        provider=provider,
        security_id=security_id,
        first_session=first_session,
        last_session=last_session,
        known_at=known_at,
        bar_count=len(bars),
        issues=ordered,
    )
    return replace(report, report_id=_content_id("market-data-quality", {
        "provider": provider, "security_id": security_id, "first": first_session.isoformat(),
        "last": last_session.isoformat(), "known_at": known_at.isoformat(),
        "bars": [b.bar_id for b in bars],
        "issues": [[i.kind.value, i.session_date.isoformat() if i.session_date else None, i.detail] for i in ordered],
    }))


@dataclass(frozen=True)
class CloseDisagreement:
    session_date: date
    close_a: Decimal
    close_b: Decimal
    difference_bps: Decimal


@dataclass(frozen=True)
class ReconciliationReport:
    report_id: str
    provider_a: str
    provider_b: str
    security_id: str
    tolerance_bps: Decimal
    compared_sessions: int
    only_in_a: tuple[date, ...]
    only_in_b: tuple[date, ...]
    disagreements: tuple[CloseDisagreement, ...]

    @property
    def agreed(self) -> bool:
        return not (self.only_in_a or self.only_in_b or self.disagreements)


def reconcile_providers(
    *,
    bars_a: tuple[DailyBar, ...],
    bars_b: tuple[DailyBar, ...],
    tolerance_bps: Decimal,
) -> ReconciliationReport:
    if not bars_a or not bars_b:
        raise MarketDataError("reconciliation needs bars from both providers")
    providers = ({b.provider for b in bars_a}, {b.provider for b in bars_b})
    securities = {b.security_id for b in bars_a + bars_b}
    if len(providers[0]) != 1 or len(providers[1]) != 1 or providers[0] == providers[1] or len(securities) != 1:
        raise MarketDataError("reconcile one security across two distinct providers")
    a = {b.session_date: b.close for b in bars_a}
    b = {x.session_date: x.close for x in bars_b}
    common = sorted(set(a) & set(b))
    disagreements = tuple(
        CloseDisagreement(day, a[day], b[day], abs(a[day] / b[day] - 1) * Decimal("10000"))
        for day in common
        if abs(a[day] / b[day] - 1) * Decimal("10000") > tolerance_bps
    )
    payload = {
        "a": sorted(x.bar_id for x in bars_a), "b": sorted(x.bar_id for x in bars_b),
        "tolerance_bps": str(tolerance_bps),
    }
    return ReconciliationReport(
        report_id=_content_id("provider-reconciliation", payload),
        provider_a=next(iter(providers[0])),
        provider_b=next(iter(providers[1])),
        security_id=next(iter(securities)),
        tolerance_bps=tolerance_bps,
        compared_sessions=len(common),
        only_in_a=tuple(sorted(set(a) - set(b))),
        only_in_b=tuple(sorted(set(b) - set(a))),
        disagreements=disagreements,
    )


@dataclass(frozen=True)
class ActionCrossCheck:
    session_date: date
    kind: str
    provider_value: Decimal | None
    ledger_value: Decimal | None
    detail: str


def cross_check_provider_actions(
    *,
    bars: tuple[DailyBar, ...],
    actions: tuple[CorporateActionEvent, ...],
) -> tuple[ActionCrossCheck, ...]:
    """Compares provider dividend/split fields with the reviewed ledger.

    Nothing is ingested from the provider; differences are listed for review.
    """

    findings = []
    dividends = {
        a.ex_date: a.cash_amount
        for a in actions
        if a.kind is CorporateActionKind.CASH_DIVIDEND and not a.retracted
    }
    splits = {
        a.ex_date: a.ratio_new / a.ratio_old
        for a in actions
        if a.kind is CorporateActionKind.STOCK_SPLIT and not a.retracted
    }
    seen_div, seen_split = set(), set()
    for bar in bars:
        if bar.provider_dividend is not None and bar.provider_dividend != 0:
            seen_div.add(bar.session_date)
            if dividends.get(bar.session_date) != bar.provider_dividend:
                findings.append(ActionCrossCheck(bar.session_date, "DIVIDEND", bar.provider_dividend,
                                                 dividends.get(bar.session_date), "provider dividend not matched in ledger"))
        if bar.provider_split_factor is not None and bar.provider_split_factor != 1:
            seen_split.add(bar.session_date)
            if splits.get(bar.session_date) != bar.provider_split_factor:
                findings.append(ActionCrossCheck(bar.session_date, "SPLIT", bar.provider_split_factor,
                                                 splits.get(bar.session_date), "provider split not matched in ledger"))
    covered = {b.session_date for b in bars}
    for day, amount in dividends.items():
        if day in covered and day not in seen_div:
            findings.append(ActionCrossCheck(day, "DIVIDEND", None, amount, "ledger dividend absent from provider"))
    for day, ratio in splits.items():
        if day in covered and day not in seen_split:
            findings.append(ActionCrossCheck(day, "SPLIT", None, ratio, "ledger split absent from provider"))
    return tuple(sorted(findings, key=lambda f: (f.session_date, f.kind)))


def to_session_closes(
    *,
    bars: tuple[DailyBar, ...],
    calendar: SessionCalendar,
) -> tuple["SessionCloseObservation", ...]:
    """Raw closes for the Stage 13.4 panel; intraday captures are excluded."""

    # Imported here: the research panel pulls in the portfolio stack
    # (cvxpy, skfolio), which the capture path does not need.
    from .research_price_panel import SessionCloseObservation

    output = []
    for bar in bars:
        if not calendar.is_session(bar.session_date):
            continue
        if bar.knowledge_time < calendar.session(bar.session_date).close_utc:
            continue
        output.append(
            SessionCloseObservation(
                security_id=bar.security_id,
                session_date=bar.session_date,
                close=bar.close,
                knowledge_time=bar.knowledge_time,
                source_fact_ids=(bar.bar_id, bar.capture_id),
            )
        )
    return tuple(output)


def _unique_sessions(bars: list[DailyBar]) -> tuple[DailyBar, ...]:
    days = [b.session_date for b in bars]
    if len(days) != len(set(days)):
        raise MarketDataError("provider returned duplicate sessions")
    return tuple(sorted(bars, key=lambda b: b.session_date))


def _dec(value) -> Decimal:
    if value is None or isinstance(value, bool):
        raise TypeError("numeric field missing")
    result = Decimal(str(value))
    if not result.is_finite():
        raise TypeError("non-finite numeric field")
    return result


def _bar_payload(bar: DailyBar) -> dict[str, object]:
    return {
        name: (
            getattr(bar, name).isoformat() if isinstance(getattr(bar, name), (date, datetime))
            else str(getattr(bar, name)) if isinstance(getattr(bar, name), Decimal)
            else getattr(bar, name)
        )
        for name in bar.__dataclass_fields__
    }


def _bar_from_payload(payload: dict) -> DailyBar:
    data = dict(payload)
    data["session_date"] = date.fromisoformat(data["session_date"])
    data["knowledge_time"] = datetime.fromisoformat(data["knowledge_time"])
    for name in ("open", "high", "low", "close", "volume", "provider_adjusted_close", "provider_dividend", "provider_split_factor"):
        data[name] = Decimal(data[name]) if data[name] is not None else None
    return DailyBar(**data)


def _values_fingerprint(bar: DailyBar) -> str:
    payload = _bar_payload(bar)
    for name in ("knowledge_time", "capture_id"):
        payload.pop(name)
    return _content_id("bar-values", payload)


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
