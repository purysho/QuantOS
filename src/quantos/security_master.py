"""Stage 13.1 — bitemporal Security Master.

Hierarchy: Company -> Security -> Listing, plus identifier assignments
(ISIN, FIGI, CUSIP, SEDOL, CIK, vendor IDs) attached to a security or a
company. The stable ``security_id`` is the permanent identity; tickers are
time-bounded listing attributes and can be reused by unrelated securities.

Every record is bitemporal:

* ``valid_from`` / ``valid_to`` (dates, ``valid_to`` exclusive) say when the
  fact was true in the world;
* ``knowledge_time`` says when QuantOS learned it.

A record is identified by a logical ``record_key``. A later record with the
same key supersedes an earlier one from its knowledge time onward, and a
``retracted`` record withdraws the key. Queries always pass both the date
of interest and the knowledge cut-off, so a correction learned later cannot
leak into an earlier research state.

Resolution fails closed: an ambiguous ticker or identifier raises instead
of guessing, and an unknown one returns ``None``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import Enum
from pathlib import Path

import duckdb


class SecurityKind(str, Enum):
    COMMON_STOCK = "COMMON_STOCK"
    PREFERRED = "PREFERRED"
    ADR = "ADR"
    ETF = "ETF"
    REIT = "REIT"
    FUND = "FUND"
    BOND = "BOND"


class IdentifierScheme(str, Enum):
    ISIN = "ISIN"
    FIGI = "FIGI"
    CUSIP = "CUSIP"
    SEDOL = "SEDOL"
    CIK = "CIK"
    VENDOR = "VENDOR"


# Schemes that identify an issuer rather than one security.
COMPANY_SCHEMES = frozenset({IdentifierScheme.CIK})


class MasterRecordKind(str, Enum):
    COMPANY = "COMPANY"
    SECURITY = "SECURITY"
    LISTING = "LISTING"
    IDENTIFIER = "IDENTIFIER"


class MasterIssueKind(str, Enum):
    TICKER_COLLISION = "TICKER_COLLISION"
    IDENTIFIER_COLLISION = "IDENTIFIER_COLLISION"
    MULTIPLE_PRIMARY_LISTINGS = "MULTIPLE_PRIMARY_LISTINGS"
    DANGLING_REFERENCE = "DANGLING_REFERENCE"
    SECURITY_OUTSIDE_ISSUER_LIFE = "SECURITY_OUTSIDE_ISSUER_LIFE"
    LISTING_OUTSIDE_SECURITY_LIFE = "LISTING_OUTSIDE_SECURITY_LIFE"
    INVALID_CHECK_DIGIT = "INVALID_CHECK_DIGIT"


@dataclass(frozen=True, kw_only=True)
class _Bitemporal:
    record_key: str
    valid_from: date
    valid_to: date | None
    knowledge_time: datetime
    evidence_references: tuple[str, ...]
    retracted: bool = False

    def _validate_bitemporal(self) -> None:
        if not self.record_key.strip():
            raise ValueError("record_key is required")
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must follow valid_from")
        if self.knowledge_time.tzinfo is None:
            raise ValueError("knowledge_time must be timezone-aware")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("master record requires evidence references")

    def valid_on(self, day: date) -> bool:
        return self.valid_from <= day and (
            self.valid_to is None or day < self.valid_to
        )

    def overlaps(self, other: "_Bitemporal") -> bool:
        return (other.valid_to is None or self.valid_from < other.valid_to) and (
            self.valid_to is None or other.valid_from < self.valid_to
        )


@dataclass(frozen=True, kw_only=True)
class CompanyRecord(_Bitemporal):
    company_id: str
    legal_name: str
    country: str

    def __post_init__(self) -> None:
        self._validate_bitemporal()
        for name in ("company_id", "legal_name", "country"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} is required")


@dataclass(frozen=True, kw_only=True)
class SecurityRecord(_Bitemporal):
    security_id: str
    company_id: str
    kind: SecurityKind
    share_class: str
    description: str

    def __post_init__(self) -> None:
        self._validate_bitemporal()
        for name in ("security_id", "company_id", "description"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} is required")


@dataclass(frozen=True, kw_only=True)
class ListingRecord(_Bitemporal):
    listing_id: str
    security_id: str
    venue_mic: str
    ticker: str
    currency: str
    is_primary: bool

    def __post_init__(self) -> None:
        self._validate_bitemporal()
        for name in ("listing_id", "security_id", "venue_mic", "ticker"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
        if len(self.currency) != 3 or not self.currency.isupper():
            raise ValueError("currency must be a three-letter upper-case code")
        if self.ticker != self.ticker.strip().upper():
            raise ValueError("ticker must be normalized upper-case")


@dataclass(frozen=True, kw_only=True)
class IdentifierRecord(_Bitemporal):
    scheme: IdentifierScheme
    value: str
    security_id: str | None
    company_id: str | None
    vendor: str | None = None

    def __post_init__(self) -> None:
        self._validate_bitemporal()
        if not self.value.strip() or self.value != self.value.strip():
            raise ValueError("identifier value must be non-empty and trimmed")
        if (self.security_id is None) == (self.company_id is None):
            raise ValueError(
                "identifier must attach to exactly one of security or company"
            )
        if self.scheme in COMPANY_SCHEMES and self.company_id is None:
            raise ValueError(f"{self.scheme.value} identifies a company")
        if self.scheme not in COMPANY_SCHEMES and self.security_id is None:
            raise ValueError(f"{self.scheme.value} identifies a security")
        if (self.scheme is IdentifierScheme.VENDOR) != bool(
            self.vendor and self.vendor.strip()
        ):
            raise ValueError("vendor name is required only for VENDOR ids")


MasterRecord = CompanyRecord | SecurityRecord | ListingRecord | IdentifierRecord


@dataclass(frozen=True)
class MasterIssue:
    kind: MasterIssueKind
    record_keys: tuple[str, ...]
    detail: str


@dataclass(frozen=True)
class MasterIntegrityReport:
    report_id: str
    knowledge_cutoff: datetime
    record_count: int
    issues: tuple[MasterIssue, ...]

    @property
    def clean(self) -> bool:
        return not self.issues


class AmbiguousResolution(ValueError):
    """Raised when a lookup matches more than one security."""


class SecurityMaster:
    """Append-only bitemporal Security Master backed by DuckDB."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS security_master_records (
                record_id VARCHAR PRIMARY KEY,
                kind VARCHAR NOT NULL,
                record_key VARCHAR NOT NULL,
                knowledge_time TIMESTAMPTZ NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, record: MasterRecord) -> bool:
        payload = master_record_payload(record)
        record_id = master_record_identity(record)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        existing = self._con.execute(
            "SELECT payload_json FROM security_master_records WHERE record_id = ?",
            [record_id],
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != encoded:
                raise ValueError("security master record identity conflict")
            return False
        clash = self._con.execute(
            """
            SELECT kind FROM security_master_records
            WHERE record_key = ? AND knowledge_time = ?
            """,
            [record.record_key, record.knowledge_time],
        ).fetchone()
        if clash is not None:
            raise ValueError(
                "two versions of one record_key share a knowledge time"
            )
        other_kind = self._con.execute(
            "SELECT kind FROM security_master_records WHERE record_key = ? LIMIT 1",
            [record.record_key],
        ).fetchone()
        if other_kind is not None and other_kind[0] != payload["record_kind"]:
            raise ValueError("record_key already names another record kind")
        self._con.execute(
            "INSERT INTO security_master_records VALUES (?, ?, ?, ?, ?)",
            [
                record_id,
                payload["record_kind"],
                record.record_key,
                record.knowledge_time,
                encoded,
            ],
        )
        return True

    def records(self, *, known_at: datetime) -> tuple[MasterRecord, ...]:
        """Latest non-retracted version of every key known at ``known_at``."""

        if known_at.tzinfo is None:
            raise ValueError("known_at must be timezone-aware")
        rows = self._con.execute(
            """
            SELECT payload_json FROM security_master_records
            WHERE knowledge_time <= ?
            ORDER BY record_key, knowledge_time
            """,
            [known_at],
        ).fetchall()
        latest: dict[str, MasterRecord] = {}
        for (payload,) in rows:
            record = master_record_from_payload(json.loads(payload))
            latest[record.record_key] = record
        return tuple(
            record
            for _, record in sorted(latest.items())
            if not record.retracted
        )

    def resolve_ticker(
        self,
        *,
        ticker: str,
        venue_mic: str | None,
        on: date,
        known_at: datetime,
    ) -> str | None:
        matches = {
            record.security_id
            for record in self.records(known_at=known_at)
            if isinstance(record, ListingRecord)
            and record.ticker == ticker.strip().upper()
            and (venue_mic is None or record.venue_mic == venue_mic)
            and record.valid_on(on)
        }
        return _single(matches, f"ticker {ticker} on {on}")

    def resolve_identifier(
        self,
        *,
        scheme: IdentifierScheme,
        value: str,
        on: date,
        known_at: datetime,
        vendor: str | None = None,
    ) -> str | None:
        """Security id (or company id for company schemes) for an identifier."""

        matches = {
            record.security_id or record.company_id
            for record in self.records(known_at=known_at)
            if isinstance(record, IdentifierRecord)
            and record.scheme is scheme
            and record.value == value
            and record.vendor == vendor
            and record.valid_on(on)
        }
        return _single(matches, f"{scheme.value} {value} on {on}")

    def listings(
        self,
        *,
        security_id: str,
        known_at: datetime,
    ) -> tuple[ListingRecord, ...]:
        """Full known listing history of a security, oldest first."""

        return tuple(
            sorted(
                (
                    record
                    for record in self.records(known_at=known_at)
                    if isinstance(record, ListingRecord)
                    and record.security_id == security_id
                ),
                key=lambda item: (item.valid_from, item.listing_id),
            )
        )

    def primary_listing(
        self,
        *,
        security_id: str,
        on: date,
        known_at: datetime,
    ) -> ListingRecord | None:
        primaries = [
            record
            for record in self.listings(security_id=security_id, known_at=known_at)
            if record.is_primary and record.valid_on(on)
        ]
        if len(primaries) > 1:
            raise AmbiguousResolution(
                f"security {security_id} has several primary listings on {on}"
            )
        return primaries[0] if primaries else None

    def integrity_report(self, *, known_at: datetime) -> MasterIntegrityReport:
        records = self.records(known_at=known_at)
        companies = [r for r in records if isinstance(r, CompanyRecord)]
        securities = [r for r in records if isinstance(r, SecurityRecord)]
        listings = [r for r in records if isinstance(r, ListingRecord)]
        identifiers = [r for r in records if isinstance(r, IdentifierRecord)]
        issues: list[MasterIssue] = []

        def pairs(items):
            for index, left in enumerate(items):
                for right in items[index + 1:]:
                    yield left, right

        for left, right in pairs(listings):
            if (
                left.ticker == right.ticker
                and left.venue_mic == right.venue_mic
                and left.security_id != right.security_id
                and left.overlaps(right)
            ):
                issues.append(
                    MasterIssue(
                        MasterIssueKind.TICKER_COLLISION,
                        (left.record_key, right.record_key),
                        f"{left.ticker} on {left.venue_mic} maps to two "
                        "securities at once",
                    )
                )
            if (
                left.security_id == right.security_id
                and left.is_primary
                and right.is_primary
                and left.overlaps(right)
            ):
                issues.append(
                    MasterIssue(
                        MasterIssueKind.MULTIPLE_PRIMARY_LISTINGS,
                        (left.record_key, right.record_key),
                        f"{left.security_id} has overlapping primary listings",
                    )
                )
        for left, right in pairs(identifiers):
            if (
                left.scheme is right.scheme
                and left.value == right.value
                and left.vendor == right.vendor
                and (left.security_id, left.company_id)
                != (right.security_id, right.company_id)
                and left.overlaps(right)
            ):
                issues.append(
                    MasterIssue(
                        MasterIssueKind.IDENTIFIER_COLLISION,
                        (left.record_key, right.record_key),
                        f"{left.scheme.value} {left.value} assigned twice",
                    )
                )
        for record in identifiers:
            if not identifier_check_digit_valid(record.scheme, record.value):
                issues.append(
                    MasterIssue(
                        MasterIssueKind.INVALID_CHECK_DIGIT,
                        (record.record_key,),
                        f"{record.scheme.value} {record.value} fails its check digit",
                    )
                )

        company_by_id: dict[str, list[CompanyRecord]] = {}
        for company in companies:
            company_by_id.setdefault(company.company_id, []).append(company)
        security_by_id: dict[str, list[SecurityRecord]] = {}
        for security in securities:
            security_by_id.setdefault(security.security_id, []).append(security)

        for security in securities:
            parents = company_by_id.get(security.company_id)
            if not parents:
                issues.append(
                    MasterIssue(
                        MasterIssueKind.DANGLING_REFERENCE,
                        (security.record_key,),
                        f"security references unknown company {security.company_id}",
                    )
                )
            elif not _covered(security, parents):
                issues.append(
                    MasterIssue(
                        MasterIssueKind.SECURITY_OUTSIDE_ISSUER_LIFE,
                        (security.record_key,),
                        f"{security.security_id} valid outside its issuer's life",
                    )
                )
        for listing in listings:
            parents = security_by_id.get(listing.security_id)
            if not parents:
                issues.append(
                    MasterIssue(
                        MasterIssueKind.DANGLING_REFERENCE,
                        (listing.record_key,),
                        f"listing references unknown security {listing.security_id}",
                    )
                )
            elif not _covered(listing, parents):
                issues.append(
                    MasterIssue(
                        MasterIssueKind.LISTING_OUTSIDE_SECURITY_LIFE,
                        (listing.record_key,),
                        f"{listing.listing_id} valid outside its security's life",
                    )
                )
        for identifier in identifiers:
            known = (
                identifier.security_id in security_by_id
                if identifier.security_id is not None
                else identifier.company_id in company_by_id
            )
            if not known:
                issues.append(
                    MasterIssue(
                        MasterIssueKind.DANGLING_REFERENCE,
                        (identifier.record_key,),
                        f"identifier {identifier.value} references an unknown entity",
                    )
                )

        ordered = tuple(
            sorted(issues, key=lambda item: (item.kind.value, item.record_keys))
        )
        report = MasterIntegrityReport(
            report_id="",
            knowledge_cutoff=known_at,
            record_count=len(records),
            issues=ordered,
        )
        return replace(
            report,
            report_id=_content_id(
                "security-master-integrity",
                {
                    "knowledge_cutoff": known_at.isoformat(),
                    "record_ids": sorted(master_record_identity(r) for r in records),
                    "issues": [
                        {
                            "kind": item.kind.value,
                            "record_keys": list(item.record_keys),
                            "detail": item.detail,
                        }
                        for item in ordered
                    ],
                },
            ),
        )

    def close(self) -> None:
        self._con.close()


def identifier_check_digit_valid(scheme: IdentifierScheme, value: str) -> bool:
    """Structural check-digit validation where the scheme defines one."""

    if scheme is IdentifierScheme.ISIN:
        return _isin_valid(value)
    if scheme is IdentifierScheme.CUSIP:
        return _cusip_valid(value)
    if scheme is IdentifierScheme.SEDOL:
        return _sedol_valid(value)
    if scheme is IdentifierScheme.FIGI:
        return _figi_valid(value)
    if scheme is IdentifierScheme.CIK:
        return value.isdigit() and 1 <= len(value) <= 10
    return True


def master_record_payload(record: MasterRecord) -> dict[str, object]:
    kind = {
        CompanyRecord: MasterRecordKind.COMPANY,
        SecurityRecord: MasterRecordKind.SECURITY,
        ListingRecord: MasterRecordKind.LISTING,
        IdentifierRecord: MasterRecordKind.IDENTIFIER,
    }[type(record)]
    payload: dict[str, object] = {"record_kind": kind.value}
    for name in record.__dataclass_fields__:
        value = getattr(record, name)
        if isinstance(value, Enum):
            value = value.value
        elif isinstance(value, (date, datetime)):
            value = value.isoformat()
        elif isinstance(value, tuple):
            value = list(value)
        payload[name] = value
    return payload


def master_record_identity(record: MasterRecord) -> str:
    return _content_id("security-master-record", master_record_payload(record))


def master_record_from_payload(payload: dict[str, object]) -> MasterRecord:
    data = dict(payload)
    kind = MasterRecordKind(data.pop("record_kind"))
    data["valid_from"] = date.fromisoformat(data["valid_from"])
    data["valid_to"] = (
        date.fromisoformat(data["valid_to"]) if data["valid_to"] is not None else None
    )
    data["knowledge_time"] = datetime.fromisoformat(data["knowledge_time"])
    data["evidence_references"] = tuple(data["evidence_references"])
    if kind is MasterRecordKind.COMPANY:
        return CompanyRecord(**data)
    if kind is MasterRecordKind.SECURITY:
        data["kind"] = SecurityKind(data["kind"])
        return SecurityRecord(**data)
    if kind is MasterRecordKind.LISTING:
        return ListingRecord(**data)
    data["scheme"] = IdentifierScheme(data["scheme"])
    return IdentifierRecord(**data)


def _single(matches: set, description: str) -> str | None:
    if len(matches) > 1:
        raise AmbiguousResolution(f"{description} matches {len(matches)} entities")
    return next(iter(matches)) if matches else None


def _covered(child: _Bitemporal, parents: list[_Bitemporal]) -> bool:
    """Whether the child's validity lies inside one parent's validity."""

    for parent in parents:
        starts = parent.valid_from <= child.valid_from
        ends = parent.valid_to is None or (
            child.valid_to is not None and child.valid_to <= parent.valid_to
        )
        if starts and ends:
            return True
    return False


def _luhn_digits(digits: list[int]) -> int:
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return (10 - total % 10) % 10


def _isin_valid(value: str) -> bool:
    if len(value) != 12 or not value[:2].isalpha() or not value.isalnum():
        return False
    if not value[-1].isdigit() or value != value.upper():
        return False
    expanded = "".join(str(int(ch, 36)) for ch in value[:-1])
    return _luhn_digits([int(ch) for ch in expanded]) == int(value[-1])


def _cusip_valid(value: str) -> bool:
    if len(value) != 9 or not value[-1].isdigit() or value != value.upper():
        return False
    total = 0
    for index, ch in enumerate(value[:8]):
        if ch.isdigit():
            number = int(ch)
        elif ch.isalpha():
            number = ord(ch) - ord("A") + 10
        elif ch in "*@#":
            number = {"*": 36, "@": 37, "#": 38}[ch]
        else:
            return False
        if index % 2 == 1:
            number *= 2
        total += number // 10 + number % 10
    return (10 - total % 10) % 10 == int(value[-1])


def _sedol_valid(value: str) -> bool:
    if len(value) != 7 or not value[-1].isdigit() or value != value.upper():
        return False
    weights = (1, 3, 1, 7, 3, 9)
    total = 0
    for weight, ch in zip(weights, value[:6]):
        if ch.isdigit():
            number = int(ch)
        elif ch.isalpha() and ch not in "AEIOU":
            number = ord(ch) - ord("A") + 10
        else:
            return False
        total += weight * number
    return (10 - total % 10) % 10 == int(value[-1])


def _figi_valid(value: str) -> bool:
    if len(value) != 12 or value != value.upper() or not value.isalnum():
        return False
    if value[2] != "G" or value[:2] in {"BS", "BM", "GG", "GB", "GH", "KY", "VG"}:
        return False
    if any(ch in "AEIOU" for ch in value[:11]):
        return False
    total = 0
    for index, ch in enumerate(value[:11]):
        number = int(ch) if ch.isdigit() else ord(ch) - ord("A") + 10
        if index % 2 == 1:
            number *= 2
        total += sum(int(d) for d in str(number))
    return (10 - total % 10) % 10 == int(value[-1])


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
