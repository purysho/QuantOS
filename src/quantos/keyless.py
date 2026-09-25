"""Stage 19 — keyless public data: works for anyone, with no signup.

Sources (all official, all free, none needs an API key):

* **SEC EDGAR ticker directory**: ticker → CIK → exchange. Seeds the Security
  Master for the user's universe.
* **SEC EDGAR XBRL company facts**: every reported financial fact with its
  accession and filing date. These are point-in-time fundamentals.
* **US Treasury** daily par yield curve.
* **ECB** euro foreign-exchange reference rates.
* **FRED graph CSV**: the *latest* values of any FRED series. With a free
  FRED key, the existing ALFRED adapter provides true vintages instead.

Knowledge-time rules:

* An XBRL fact is known at the end of its filing day in New York, the
  latest moment EDGAR could have accepted it. Restatements in later filings
  become later versions, and ``XbrlFactStore.as_of`` returns what was
  known at the time.
* Every other observation defaults to ``CAPTURE_TIME``: it is known when First
  Current fetched it. Treasury and ECB rows may instead use
  ``PUBLICATION_SCHEDULE``, an explicit, recorded assumption (Treasury
  18:00 New York, ECB 16:00 Frankfurt) that makes the history usable for
  backtests. The chosen policy is stored on every row.
* FRED CSV values are latest revisions, so a schedule assumption would leak
  future revisions. They are always ``CAPTURE_TIME``.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import duckdb

from .artifacts import SourceArtifactStore
from .security import guarded
from .security_master import (
    CompanyRecord,
    IdentifierRecord,
    IdentifierScheme,
    ListingRecord,
    SecurityKind,
    SecurityMaster,
    SecurityRecord,
)

Transport = Callable[[str, dict[str, str], float], tuple[bytes, str]]
NEW_YORK = ZoneInfo("America/New_York")
FRANKFURT = ZoneInfo("Europe/Berlin")
MAX_BODY_BYTES = 50_000_000

NASDAQ_SYMBOLS_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
TREASURY_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve"
    "&field_tdr_date_value={year}&page&_format=csv"
)
ECB_URL = "https://data-api.ecb.europa.eu/service/data/EXR/D.{currency}.EUR.SP00.A?{query}"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"

KEYLESS_HEALTH_URLS = {
    "Fama-French factors": "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Momentum_Factor_daily_CSV.zip",
    "Nasdaq symbols": NASDAQ_SYMBOLS_URL,
    "SEC tickers": SEC_TICKERS_URL,
    "SEC XBRL facts": SEC_FACTS_URL.format(cik=320193),
    "US Treasury": TREASURY_URL.format(year=date.today().year),
    "ECB FX": ECB_URL.format(currency="USD", query="lastNObservations=1&format=csvdata"),
    "FRED CSV": FRED_CSV_URL.format(series="DGS10"),
}

EXCHANGE_MIC = {"NASDAQ": "XNAS", "NYSE": "XNYS", "CBOE": "BATS", "OTC": "OTCM"}
# Listing-exchange codes used in Nasdaq Trader's symbol directory.
NASDAQ_LISTING_MIC = {"Q": "XNAS", "N": "XNYS", "A": "XASE", "P": "ARCX", "Z": "BATS", "V": "IEXG"}


class KeylessError(ValueError):
    pass


class NotFound(KeylessError):
    """The source has no document for this request (HTTP 404)."""


class KnowledgeTimePolicy(str, Enum):
    CAPTURE_TIME = "CAPTURE_TIME"
    PUBLICATION_SCHEDULE = "PUBLICATION_SCHEDULE"


def _default_transport(url: str, headers: dict[str, str], timeout: float, *,
                       attempts: int = 3, sleeper: Callable[[float], None] | None = None) -> tuple[bytes, str]:
    """GET with bounded retries for timeouts, connection errors, 429 and 5xx.

    Public sources (treasury.gov in particular) are occasionally slow; a
    client error other than 429 is final.
    """

    import time as _time

    import requests

    sleeper = sleeper or _time.sleep
    guarded(url, "keyless-data")
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, headers=headers, timeout=timeout, allow_redirects=False)
        except (requests.Timeout, requests.ConnectionError) as exc:
            if attempt == attempts:
                raise KeylessError(f"public data request failed after {attempts} attempts: {url}") from exc
            sleeper(2 ** attempt)
            continue
        except requests.RequestException as exc:
            raise KeylessError("public data request failed") from exc
        if (response.status_code == 429 or response.status_code >= 500) and attempt < attempts:
            sleeper(2 ** attempt)
            continue
        break
    if response.status_code == 404:
        raise NotFound(f"no document at {url}")
    if response.status_code != 200:
        raise KeylessError(f"public data request failed with HTTP {response.status_code}")
    return response.content, response.headers.get("content-type", "application/octet-stream").split(";")[0]


class _Client:
    def __init__(
        self,
        *,
        user_agent: str,
        transport: Transport | None = None,
        timeout_seconds: float = 30.0,
        artifacts: SourceArtifactStore | None = None,
    ) -> None:
        if "@" not in user_agent:
            raise KeylessError("user_agent must include a contact email (run `quantos setup`)")
        self.user_agent = user_agent
        self.transport = transport or _default_transport
        self.timeout_seconds = timeout_seconds
        self.artifacts = artifacts

    def get(self, url: str, *, fetched_at: datetime | None = None) -> tuple[bytes, datetime, str | None]:
        body, media_type = self.transport(
            url, {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"}, self.timeout_seconds
        )
        if not body:
            raise KeylessError(f"empty response from {url}")
        if len(body) > MAX_BODY_BYTES:
            raise KeylessError(f"response from {url} exceeds the size limit")
        fetched_at = fetched_at or datetime.now(timezone.utc)
        artifact_id = None
        if self.artifacts is not None:
            artifact_id = self.artifacts.put(
                source_uri=url, content=body, fetched_at=fetched_at, media_type=media_type or "application/octet-stream"
            ).artifact_id
        return body, fetched_at, artifact_id


# --------------------------------------------------------------- SEC tickers


@dataclass(frozen=True)
class TickerEntry:
    cik: int
    ticker: str
    name: str
    exchange: str


@dataclass(frozen=True)
class TickerDirectory:
    fetched_at: datetime
    artifact_id: str | None
    entries: tuple[TickerEntry, ...]

    def lookup(self, ticker: str) -> TickerEntry | None:
        """Exact match first; share classes may be written BRK.B, BRK-B or BRK/B."""

        wanted = ticker.strip().upper()
        matches = [e for e in self.entries if e.ticker == wanted]
        if not matches:
            canonical = re.sub(r"[./]", "-", wanted)
            matches = [e for e in self.entries if re.sub(r"[./]", "-", e.ticker) == canonical]
        if len(matches) > 1:
            raise KeylessError(f"ticker {wanted} is ambiguous in the SEC directory")
        return matches[0] if matches else None


class SECTickerDirectoryAdapter(_Client):
    def fetch(self, *, fetched_at: datetime | None = None) -> TickerDirectory:
        body, fetched_at, artifact_id = self.get(SEC_TICKERS_URL, fetched_at=fetched_at)
        return TickerDirectory(fetched_at, artifact_id, parse_ticker_directory(body))

    def cached(self, cache_dir: str | Path, *, max_age: timedelta = timedelta(hours=20)) -> TickerDirectory:
        """The directory from a local copy younger than ``max_age``, else a fresh fetch.

        SEC publishes it once a day; re-downloading ~800 KB for every lookup
        is wasteful and gets a client throttled. The copy keeps its original
        fetch time, so its knowledge time stays honest.
        """

        cache = Path(cache_dir)
        meta_path, body_path = cache / "sec_tickers.json", cache / "sec_tickers.bin"
        try:
            meta = json.loads(meta_path.read_text())
            fetched_at = datetime.fromisoformat(meta["fetched_at"])
            if datetime.now(timezone.utc) - fetched_at < max_age:
                body = body_path.read_bytes()
                if hashlib.sha256(body).hexdigest() == meta["sha256"]:
                    return TickerDirectory(fetched_at, meta.get("artifact_id"), parse_ticker_directory(body))
        except (OSError, ValueError, KeyError, KeylessError):
            pass
        body, fetched_at, artifact_id = self.get(SEC_TICKERS_URL)
        directory = TickerDirectory(fetched_at, artifact_id, parse_ticker_directory(body))
        cache.mkdir(parents=True, exist_ok=True)
        body_path.write_bytes(body)
        meta_path.write_text(json.dumps({"fetched_at": fetched_at.isoformat(), "artifact_id": artifact_id,
                                         "sha256": hashlib.sha256(body).hexdigest()}))
        return directory


def parse_ticker_directory(body: bytes) -> tuple[TickerEntry, ...]:
    try:
        document = json.loads(body)
        fields = document["fields"]
        index = {name: fields.index(name) for name in ("cik", "name", "ticker", "exchange")}
        rows = document["data"]
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        raise KeylessError("unexpected SEC ticker directory format") from exc
    entries = []
    for row in rows:
        ticker = str(row[index["ticker"]] or "").strip().upper()
        if not ticker:
            continue
        entries.append(
            TickerEntry(
                cik=int(row[index["cik"]]),
                ticker=ticker,
                name=" ".join(str(row[index["name"]]).split()),
                exchange=str(row[index["exchange"]] or "").strip(),
            )
        )
    if not entries:
        raise KeylessError("SEC ticker directory is empty")
    return tuple(entries)


@dataclass(frozen=True)
class SeedResult:
    added: int
    unchanged: int
    security_ids: dict[str, str]
    unresolved: tuple[str, ...]
    kinds: dict[str, str] = None  # ticker -> security kind ("?" suffix = unverified)


def seed_security_master(
    *,
    master: SecurityMaster,
    directory: TickerDirectory,
    tickers: tuple[str, ...],
    symbols: SymbolDirectory | None = None,
) -> SeedResult:
    """Adds company, security, listing and CIK records for the given tickers.

    The directory is a snapshot, so records are valid from the capture date
    only. No historical ticker mapping is claimed. SEC's directory has no
    security kind; with Nasdaq's symbol directory the kind (ETF, ADR,
    preferred, common) and the precise listing venue come from the exchange
    listing. When neither says, the security is recorded as COMMON_STOCK
    with ``assumption:security-kind-unverified`` in its evidence, for a
    reviewer to correct.
    """

    known_at = directory.fetched_at
    valid_from = known_at.astimezone(NEW_YORK).date()
    evidence = (f"sec-ticker-directory:{directory.artifact_id or known_at.isoformat()}",)
    if symbols is not None:
        evidence = evidence + (f"nasdaq-symbol-directory:{symbols.artifact_id or symbols.file_created}",)
    added = unchanged = 0
    # A later snapshot that says the same thing is not a new fact.
    existing = {r.record_key: r for r in master.records(known_at=known_at)}
    ids: dict[str, str] = {}
    kinds: dict[str, str] = {}
    unresolved = []
    for ticker in tickers:
        entry = directory.lookup(ticker)
        if entry is None:
            listing = symbols.lookup(ticker) if symbols is not None else None
            if listing is None or listing.listing_mic is None:
                unresolved.append(ticker.upper())
                continue
            # Listed on a US exchange but not an SEC company filer (many funds
            # file under series IDs): seeded from the exchange listing, no CIK.
            records, security_id, kind, assumption = _listing_only_records(
                listing, valid_from=valid_from, known_at=known_at, evidence=evidence)
            for record in records:
                if _same_as_known(existing.get(record.record_key), record) or not master.add(record):
                    unchanged += 1
                else:
                    added += 1
            ids[listing.symbol] = security_id
            kinds[listing.symbol] = kind.value + ("?" if assumption else "")
            continue
        company_id = f"CIK:{entry.cik:010d}"
        security_id = f"SEC:{entry.cik:010d}:{entry.ticker}"
        listing = symbols.lookup(entry.ticker) if symbols is not None else None
        kind, assumption = classify_security(listing)
        mic = (listing.listing_mic if listing and listing.listing_mic else None) or EXCHANGE_MIC.get(entry.exchange.upper())
        records = [
            CompanyRecord(record_key=f"company:{company_id}", company_id=company_id, legal_name=entry.name,
                          country="US", valid_from=valid_from, valid_to=None, knowledge_time=known_at,
                          evidence_references=evidence),
            IdentifierRecord(record_key=f"cik:{company_id}", scheme=IdentifierScheme.CIK, value=f"{entry.cik:010d}",
                             security_id=None, company_id=company_id, valid_from=valid_from, valid_to=None,
                             knowledge_time=known_at, evidence_references=evidence),
            SecurityRecord(record_key=f"security:{security_id}", security_id=security_id, company_id=company_id,
                           kind=kind, share_class=entry.ticker,
                           description=f"{listing.name if listing else entry.name} ({entry.ticker})",
                           valid_from=valid_from, valid_to=None, knowledge_time=known_at,
                           evidence_references=evidence + ((assumption,) if assumption else ())),
        ]
        if mic is not None:
            records.append(
                # One primary-listing key per security: a corrected venue supersedes the old one.
                ListingRecord(record_key=f"listing:{security_id}", listing_id=f"{security_id}:{mic}",
                              security_id=security_id, venue_mic=mic, ticker=entry.ticker, currency="USD",
                              is_primary=True, valid_from=valid_from, valid_to=None, knowledge_time=known_at,
                              evidence_references=evidence)
            )
        else:
            unresolved.append(f"{entry.ticker}(exchange {entry.exchange or 'unknown'})")
        for record in records:
            if _same_as_known(existing.get(record.record_key), record) or not master.add(record):
                unchanged += 1
            else:
                added += 1
        ids[entry.ticker] = security_id
        kinds[entry.ticker] = kind.value + ("?" if assumption else "")
    return SeedResult(added, unchanged, ids, tuple(unresolved), kinds)


def _listing_only_records(listing: SymbolEntry, *, valid_from: date, known_at: datetime, evidence: tuple[str, ...]):
    kind, assumption = classify_security(listing)
    company_id = f"LISTING:{listing.symbol}"
    security_id = f"LISTING:{listing.symbol}"
    security_evidence = evidence + ((assumption,) if assumption else ())
    records = [
        CompanyRecord(record_key=f"company:{company_id}", company_id=company_id, legal_name=listing.name,
                      country="US", valid_from=valid_from, valid_to=None, knowledge_time=known_at,
                      evidence_references=evidence),
        SecurityRecord(record_key=f"security:{security_id}", security_id=security_id, company_id=company_id,
                       kind=kind, share_class=listing.symbol, description=f"{listing.name} ({listing.symbol})",
                       valid_from=valid_from, valid_to=None, knowledge_time=known_at,
                       evidence_references=security_evidence),
        ListingRecord(record_key=f"listing:{security_id}", listing_id=f"{security_id}:{listing.listing_mic}",
                      security_id=security_id, venue_mic=listing.listing_mic, ticker=listing.symbol, currency="USD",
                      is_primary=True, valid_from=valid_from, valid_to=None, knowledge_time=known_at,
                      evidence_references=evidence),
    ]
    return records, security_id, kind, assumption


def _same_as_known(known, candidate) -> bool:
    if known is None or type(known) is not type(candidate) or known.retracted:
        return False
    return replace(known, knowledge_time=candidate.knowledge_time, valid_from=candidate.valid_from,
                   evidence_references=candidate.evidence_references) == candidate


# ------------------------------------------------ Nasdaq symbol directory


@dataclass(frozen=True)
class SymbolEntry:
    symbol: str
    name: str
    listing_mic: str | None
    is_etf: bool


@dataclass(frozen=True)
class SymbolDirectory:
    fetched_at: datetime
    artifact_id: str | None
    file_created: str
    entries: dict[str, SymbolEntry]

    def lookup(self, ticker: str) -> SymbolEntry | None:
        wanted = re.sub(r"[-/]", ".", ticker.strip().upper())
        return self.entries.get(wanted)


class NasdaqSymbolDirectoryAdapter(_Client):
    """Every US-listed symbol with its listing exchange and ETF flag (keyless)."""

    def fetch(self, *, fetched_at: datetime | None = None) -> SymbolDirectory:
        body, fetched_at, artifact_id = self.get(NASDAQ_SYMBOLS_URL, fetched_at=fetched_at)
        created, entries = parse_symbol_directory(body)
        return SymbolDirectory(fetched_at, artifact_id, created, entries)


def parse_symbol_directory(body: bytes) -> tuple[str, dict[str, SymbolEntry]]:
    lines = body.decode("utf-8", errors="replace").splitlines()
    if not lines or not lines[0].startswith("Nasdaq Traded|Symbol|Security Name|Listing Exchange"):
        raise KeylessError("unexpected Nasdaq symbol directory header")
    header = lines[0].split("|")
    index = {name: header.index(name) for name in ("Symbol", "Security Name", "Listing Exchange", "ETF", "Test Issue")}
    created = ""
    entries: dict[str, SymbolEntry] = {}
    for line in lines[1:]:
        if line.startswith("File Creation Time"):
            created = line.split(":", 1)[1].split("|")[0].strip()
            continue
        cells = line.split("|")
        if len(cells) < len(header) or cells[index["Test Issue"]] == "Y":
            continue
        symbol = cells[index["Symbol"]].strip().upper()
        if not symbol:
            continue
        entries[symbol] = SymbolEntry(
            symbol=symbol,
            name=" ".join(cells[index["Security Name"]].split()),
            listing_mic=NASDAQ_LISTING_MIC.get(cells[index["Listing Exchange"]].strip()),
            is_etf=cells[index["ETF"]].strip() == "Y",
        )
    if not created or len(entries) < 100:
        raise KeylessError("Nasdaq symbol directory is incomplete (no creation time or too few symbols)")
    return created, entries


def classify_security(entry: SymbolEntry | None) -> tuple[SecurityKind, str | None]:
    """Security kind from the exchange directory, or COMMON_STOCK with a
    stated assumption when the directory does not say."""

    if entry is None:
        return SecurityKind.COMMON_STOCK, "assumption:security-kind-unverified"
    name = entry.name.lower()
    if entry.is_etf:
        return SecurityKind.ETF, None
    if "depositary" in name:
        return SecurityKind.ADR, None
    if "preferred" in name or re.search(r"\bpfd\b", name):
        return SecurityKind.PREFERRED, None
    if any(term in name for term in ("common stock", "ordinary share", "class a", "class b", "common shares")):
        return SecurityKind.COMMON_STOCK, None
    return SecurityKind.COMMON_STOCK, "assumption:security-kind-unverified"


# ---------------------------------------------------------- SEC XBRL facts


@dataclass(frozen=True)
class XbrlFact:
    cik: int
    taxonomy: str
    concept: str
    unit: str
    value: Decimal
    period_start: date | None
    period_end: date
    fiscal_year: int | None
    fiscal_period: str | None
    form: str
    accession: str
    filed: date
    frame: str | None
    knowledge_time: datetime

    @property
    def fact_id(self) -> str:
        return _content_id("xbrl-fact", {
            "cik": self.cik, "taxonomy": self.taxonomy, "concept": self.concept, "unit": self.unit,
            "value": str(self.value), "start": _iso(self.period_start), "end": self.period_end.isoformat(),
            "accession": self.accession, "form": self.form,
        })


def filing_knowledge_time(filed: date) -> datetime:
    """End of the filing day in New York: the latest possible EDGAR acceptance."""

    return datetime.combine(filed, time(23, 59, 59, 999999), tzinfo=NEW_YORK).astimezone(timezone.utc)


class SECCompanyFactsAdapter(_Client):
    def fetch(self, cik: int, *, fetched_at: datetime | None = None) -> tuple[tuple[XbrlFact, ...], str | None]:
        if cik <= 0:
            raise KeylessError("CIK must be positive")
        body, _, artifact_id = self.get(SEC_FACTS_URL.format(cik=cik), fetched_at=fetched_at)
        return parse_company_facts(body, expected_cik=cik), artifact_id


def parse_company_facts(body: bytes, *, expected_cik: int) -> tuple[XbrlFact, ...]:
    try:
        document = json.loads(body)
    except json.JSONDecodeError as exc:
        raise KeylessError("invalid SEC company facts JSON") from exc
    if int(document.get("cik", -1)) != expected_cik:
        raise KeylessError(f"SEC company facts returned CIK {document.get('cik')} for {expected_cik}")
    facts: dict[str, XbrlFact] = {}
    for taxonomy, concepts in (document.get("facts") or {}).items():
        for concept, body_ in concepts.items():
            for unit, rows in (body_.get("units") or {}).items():
                for row in rows:
                    try:
                        filed = date.fromisoformat(row["filed"])
                        value = Decimal(str(row["val"]))
                        fact = XbrlFact(
                            cik=expected_cik, taxonomy=taxonomy, concept=concept, unit=unit, value=value,
                            period_start=date.fromisoformat(row["start"]) if row.get("start") else None,
                            period_end=date.fromisoformat(row["end"]),
                            fiscal_year=int(row["fy"]) if row.get("fy") is not None else None,
                            fiscal_period=row.get("fp"), form=str(row.get("form", "")),
                            accession=str(row["accn"]), filed=filed, frame=row.get("frame"),
                            knowledge_time=filing_knowledge_time(filed),
                        )
                    except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
                        raise KeylessError(f"malformed XBRL fact {taxonomy}:{concept}: {exc}") from exc
                    if not fact.value.is_finite():
                        raise KeylessError(f"non-finite XBRL value in {concept}")
                    facts[fact.fact_id] = fact  # the same fact can appear under several frames
    return tuple(sorted(facts.values(), key=lambda f: (f.concept, f.period_end, f.filed, f.accession)))


class XbrlFactStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS xbrl_facts (
                fact_id VARCHAR PRIMARY KEY,
                cik BIGINT NOT NULL,
                taxonomy VARCHAR NOT NULL,
                concept VARCHAR NOT NULL,
                unit VARCHAR NOT NULL,
                value VARCHAR NOT NULL,
                period_start DATE,
                period_end DATE NOT NULL,
                fiscal_year INTEGER,
                fiscal_period VARCHAR,
                form VARCHAR NOT NULL,
                accession VARCHAR NOT NULL,
                filed DATE NOT NULL,
                frame VARCHAR,
                knowledge_time TIMESTAMPTZ NOT NULL,
                source_artifact_id VARCHAR
            )
            """
        )

    def add(self, facts: tuple[XbrlFact, ...], *, source_artifact_id: str | None) -> int:
        existing = {row[0] for row in self._con.execute(
            "SELECT fact_id FROM xbrl_facts WHERE cik = ?", [facts[0].cik] if facts else [0]
        ).fetchall()}
        rows = [
            [f.fact_id, f.cik, f.taxonomy, f.concept, f.unit, str(f.value), f.period_start, f.period_end,
             f.fiscal_year, f.fiscal_period, f.form, f.accession, f.filed, f.frame, f.knowledge_time,
             source_artifact_id]
            for f in facts if f.fact_id not in existing
        ]
        if rows:
            bulk_insert(self._con, "xbrl_facts", rows)
        return len(rows)

    def as_of(self, *, cik: int, concept: str, known_at: datetime, unit: str = "USD") -> tuple[tuple, ...]:
        """Latest known value per reporting period as of ``known_at``.

        Returns ``(period_start, period_end, value, form, accession, filed)`` rows.
        """

        return tuple(
            (start, end, Decimal(value), form, accession, filed)
            for start, end, value, form, accession, filed in self._con.execute(
                """
                SELECT period_start, period_end, value, form, accession, filed FROM (
                    SELECT *, row_number() OVER (
                        PARTITION BY period_start, period_end ORDER BY knowledge_time DESC, accession DESC
                    ) AS rank
                    FROM xbrl_facts
                    WHERE cik = ? AND concept = ? AND unit = ? AND knowledge_time <= ?
                ) WHERE rank = 1 ORDER BY period_end, period_start NULLS FIRST
                """,
                [cik, concept, unit, known_at],
            ).fetchall()
        )

    def close(self) -> None:
        self._con.close()


# ------------------------------------------------------ rates, FX and macro


@dataclass(frozen=True)
class PublicObservation:
    source: str
    series_id: str
    observation_date: date
    value: Decimal
    unit: str
    knowledge_time: datetime
    knowledge_policy: KnowledgeTimePolicy
    captured_at: datetime

    @property
    def observation_id(self) -> str:
        return _content_id("public-observation", {
            "source": self.source, "series": self.series_id, "date": self.observation_date.isoformat(),
            "value": str(self.value), "unit": self.unit, "knowledge_time": self.knowledge_time.isoformat(),
            "policy": self.knowledge_policy.value,
        })


def _knowledge(day: date, captured_at: datetime, policy: KnowledgeTimePolicy, publish: time, zone: ZoneInfo) -> datetime:
    if policy is KnowledgeTimePolicy.CAPTURE_TIME:
        return captured_at
    scheduled = datetime.combine(day, publish, tzinfo=zone).astimezone(timezone.utc)
    # Never later than the capture itself: having the value proves it was published.
    return min(scheduled, captured_at)


class TreasuryYieldCurveAdapter(_Client):
    SOURCE = "us-treasury"

    def fetch(self, *, year: int, policy: KnowledgeTimePolicy = KnowledgeTimePolicy.CAPTURE_TIME,
              fetched_at: datetime | None = None) -> tuple[PublicObservation, ...]:
        if not 1990 <= year <= 2100:
            raise KeylessError("Treasury par yield curve data starts in 1990")
        body, captured, _ = self.get(TREASURY_URL.format(year=year), fetched_at=fetched_at)
        return parse_treasury_csv(body, captured_at=captured, policy=policy)


def parse_treasury_csv(body: bytes, *, captured_at: datetime, policy: KnowledgeTimePolicy) -> tuple[PublicObservation, ...]:
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
    if not reader.fieldnames or reader.fieldnames[0] != "Date":
        raise KeylessError("unexpected Treasury CSV header")
    output = []
    for row in reader:
        try:
            day = datetime.strptime(row["Date"], "%m/%d/%Y").date()
        except ValueError as exc:
            raise KeylessError(f"invalid Treasury date {row['Date']!r}") from exc
        for tenor, raw in row.items():
            if tenor == "Date" or raw in (None, "", "N/A"):
                continue
            output.append(PublicObservation(
                source=TreasuryYieldCurveAdapter.SOURCE, series_id="UST_PAR_" + _tenor_code(tenor),
                observation_date=day, value=_decimal(raw, tenor), unit="percent",
                knowledge_time=_knowledge(day, captured_at, policy, time(18, 0), NEW_YORK),
                knowledge_policy=policy, captured_at=captured_at,
            ))
    if not output:
        raise KeylessError("Treasury CSV contained no observations")
    return tuple(output)


def _tenor_code(label: str) -> str:
    match = re.fullmatch(r"\s*([\d.]+)\s*(Mo|Month|Yr|Year)s?\s*", label)
    if not match:
        raise KeylessError(f"unknown Treasury tenor {label!r}")
    number = match.group(1).replace(".", "_")
    return f"{number}{'M' if match.group(2).startswith('Mo') else 'Y'}"


class ECBReferenceRateAdapter(_Client):
    SOURCE = "ecb"

    def fetch(self, *, currency: str, start: date, policy: KnowledgeTimePolicy = KnowledgeTimePolicy.CAPTURE_TIME,
              fetched_at: datetime | None = None) -> tuple[PublicObservation, ...]:
        if not re.fullmatch(r"[A-Z]{3}", currency) or currency == "EUR":
            raise KeylessError("currency must be a three-letter code other than EUR")
        url = ECB_URL.format(currency=currency, query=urlencode({"startPeriod": start.isoformat(), "format": "csvdata"}))
        body, captured, _ = self.get(url, fetched_at=fetched_at)
        return parse_ecb_csv(body, currency=currency, captured_at=captured, policy=policy)


def parse_ecb_csv(body: bytes, *, currency: str, captured_at: datetime, policy: KnowledgeTimePolicy) -> tuple[PublicObservation, ...]:
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
    if not reader.fieldnames or "TIME_PERIOD" not in reader.fieldnames or "OBS_VALUE" not in reader.fieldnames:
        raise KeylessError("unexpected ECB CSV header")
    output = []
    for row in reader:
        if row.get("CURRENCY") not in (None, currency) or not row["OBS_VALUE"]:
            continue
        day = date.fromisoformat(row["TIME_PERIOD"])
        output.append(PublicObservation(
            source=ECBReferenceRateAdapter.SOURCE, series_id=f"EUR{currency}", observation_date=day,
            value=_decimal(row["OBS_VALUE"], "OBS_VALUE"), unit=f"{currency} per EUR",
            knowledge_time=_knowledge(day, captured_at, policy, time(16, 0), FRANKFURT),
            knowledge_policy=policy, captured_at=captured_at,
        ))
    return tuple(output)


class FredCsvAdapter(_Client):
    """Latest FRED values without a key. Always CAPTURE_TIME (values are revised)."""

    SOURCE = "fred-csv"

    def fetch(self, *, series_id: str, fetched_at: datetime | None = None) -> tuple[PublicObservation, ...]:
        if not re.fullmatch(r"[A-Z0-9_]{1,40}", series_id):
            raise KeylessError("FRED series IDs are upper-case alphanumerics")
        body, captured, _ = self.get(FRED_CSV_URL.format(series=series_id), fetched_at=fetched_at)
        return parse_fred_csv(body, series_id=series_id, captured_at=captured)


def parse_fred_csv(body: bytes, *, series_id: str, captured_at: datetime) -> tuple[PublicObservation, ...]:
    reader = csv.reader(io.StringIO(body.decode("utf-8-sig")))
    header = next(reader, None)
    if not header or len(header) != 2 or header[1] != series_id:
        raise KeylessError(f"unexpected FRED CSV header {header!r}")
    output = []
    for day_text, raw in reader:
        if raw in ("", "."):
            continue
        output.append(PublicObservation(
            source=FredCsvAdapter.SOURCE, series_id=series_id, observation_date=date.fromisoformat(day_text),
            value=_decimal(raw, series_id), unit="as published", knowledge_time=captured_at,
            knowledge_policy=KnowledgeTimePolicy.CAPTURE_TIME, captured_at=captured_at,
        ))
    return tuple(output)


FRENCH_BASE = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/"
FRENCH_DATASETS = {
    # dataset id -> (zip file, series prefix)
    "FF5_DAILY": ("F-F_Research_Data_5_Factors_2x3_daily_CSV.zip", "FF5_"),
    "MOM_DAILY": ("F-F_Momentum_Factor_daily_CSV.zip", "FF_"),
}


class FamaFrenchAdapter(_Client):
    """Kenneth R. French Data Library factor returns (keyless).

    The library is rebuilt from each new CRSP release and history can be
    revised, so observations are CAPTURE_TIME and the CRSP vintage is
    recorded in the unit. A later vintage that changes a value is stored as
    a revision.
    """

    SOURCE = "ken-french"

    def fetch(self, *, dataset: str, fetched_at: datetime | None = None) -> tuple[PublicObservation, ...]:
        if dataset not in FRENCH_DATASETS:
            raise KeylessError(f"unknown French dataset {dataset!r}")
        filename, prefix = FRENCH_DATASETS[dataset]
        body, captured, _ = self.get(FRENCH_BASE + filename, fetched_at=fetched_at)
        return parse_french_zip(body, prefix=prefix, captured_at=captured)


def parse_french_zip(body: bytes, *, prefix: str, captured_at: datetime) -> tuple[PublicObservation, ...]:
    import zipfile

    try:
        archive = zipfile.ZipFile(io.BytesIO(body))
        names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1 or archive.getinfo(names[0]).file_size > MAX_BODY_BYTES:
            raise KeylessError("French data zip must contain exactly one reasonable CSV")
        text = archive.read(names[0]).decode("latin-1")
    except zipfile.BadZipFile as exc:
        raise KeylessError("invalid French data zip") from exc
    vintage_match = re.search(r"(\d{6}) CRSP database", text)
    unit = "percent daily return" + (f"; CRSP {vintage_match.group(1)} vintage" if vintage_match else "")
    header: list[str] | None = None
    output = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.split(",")]
        if header is None:
            if len(cells) > 1 and cells[0] == "" and all(cells[1:]):
                header = cells
            continue
        if not re.fullmatch(r"\d{8}", cells[0]):
            if output:
                break  # the daily section ended (annual tables and notes follow in some files)
            continue
        if len(cells) != len(header):
            raise KeylessError(f"French data row has {len(cells)} cells, header has {len(header)}")
        day = datetime.strptime(cells[0], "%Y%m%d").date()
        for name, raw in zip(header[1:], cells[1:]):
            if raw in ("", "-99.99", "-999"):
                continue  # the library's missing-value markers
            series = prefix + re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")
            output.append(PublicObservation(
                source=FamaFrenchAdapter.SOURCE, series_id=series, observation_date=day,
                value=_decimal(raw, series), unit=unit, knowledge_time=captured_at,
                knowledge_policy=KnowledgeTimePolicy.CAPTURE_TIME, captured_at=captured_at,
            ))
    if header is None or not output:
        raise KeylessError("French data file contained no daily observations")
    return tuple(output)


class PublicObservationStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS public_observations (
                observation_id VARCHAR PRIMARY KEY,
                source VARCHAR NOT NULL,
                series_id VARCHAR NOT NULL,
                observation_date DATE NOT NULL,
                value VARCHAR NOT NULL,
                unit VARCHAR NOT NULL,
                knowledge_time TIMESTAMPTZ NOT NULL,
                knowledge_policy VARCHAR NOT NULL,
                captured_at TIMESTAMPTZ NOT NULL
            )
            """
        )

    def add(self, observations: tuple[PublicObservation, ...]) -> dict[str, int]:
        """Stores new values; an identical value already known is not re-stored."""

        counts = {"inserted": 0, "unchanged": 0, "revisions": 0}
        # One query per series loads every stored version; comparison happens in memory.
        history: dict[tuple[str, str, date], list[tuple[datetime, Decimal]]] = {}
        known_ids: set[str] = set()
        for source, series_id in sorted({(o.source, o.series_id) for o in observations}):
            for oid, day, value, known in self._con.execute(
                "SELECT observation_id, observation_date, value, knowledge_time FROM public_observations "
                "WHERE source = ? AND series_id = ?",
                [source, series_id],
            ).fetchall():
                known_ids.add(oid)
                history.setdefault((source, series_id, day), []).append((known, Decimal(value)))
        rows = []
        for obs in observations:
            versions = [v for v in history.get((obs.source, obs.series_id, obs.observation_date), [])
                        if v[0] <= obs.knowledge_time]
            latest = max(versions, key=lambda v: v[0])[1] if versions else None
            if (latest is not None and latest == obs.value) or obs.observation_id in known_ids:
                counts["unchanged"] += 1
                continue
            rows.append([obs.observation_id, obs.source, obs.series_id, obs.observation_date, str(obs.value),
                         obs.unit, obs.knowledge_time, obs.knowledge_policy.value, obs.captured_at])
            known_ids.add(obs.observation_id)
            history.setdefault((obs.source, obs.series_id, obs.observation_date), []).append((obs.knowledge_time, obs.value))
            counts["revisions" if latest is not None else "inserted"] += 1
        if rows:
            bulk_insert(self._con, "public_observations", rows)
        return counts

    def series(self, *, source: str, series_id: str, known_at: datetime) -> tuple[tuple[date, Decimal], ...]:
        rows = self._con.execute(
            """
            SELECT observation_date, value FROM (
                SELECT *, row_number() OVER (PARTITION BY observation_date ORDER BY knowledge_time DESC) AS rank
                FROM public_observations WHERE source = ? AND series_id = ? AND knowledge_time <= ?
            ) WHERE rank = 1 ORDER BY observation_date
            """,
            [source, series_id, known_at],
        ).fetchall()
        return tuple((day, Decimal(value)) for day, value in rows)

    def close(self) -> None:
        self._con.close()


def bulk_insert(con, table: str, rows: list[list], *, chunk: int = 500) -> None:
    """Parameterized multi-row INSERT, in one transaction.

    DuckDB's ``executemany`` is roughly 30x slower for large batches.
    """

    if not rows:
        return
    width = len(rows[0])
    if not re.fullmatch(r"[a-z_]+", table) or any(len(r) != width for r in rows):
        raise KeylessError("bulk_insert needs a plain table name and rows of equal width")
    placeholder = "(" + ", ".join(["?"] * width) + ")"
    con.execute("BEGIN TRANSACTION")
    try:
        for start in range(0, len(rows), chunk):
            part = rows[start:start + chunk]
            con.execute(f"INSERT INTO {table} VALUES " + ", ".join([placeholder] * len(part)),
                        [value for row in part for value in row])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def _decimal(raw: str, label: str) -> Decimal:
    try:
        value = Decimal(raw.strip())
    except (InvalidOperation, AttributeError) as exc:
        raise KeylessError(f"invalid number {raw!r} in {label}") from exc
    if not value.is_finite():
        raise KeylessError(f"non-finite number in {label}")
    return value


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
