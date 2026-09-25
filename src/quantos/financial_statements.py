from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

import duckdb


class StatementType(str, Enum):
    BALANCE_SHEET = "BALANCE_SHEET"
    INCOME_STATEMENT = "INCOME_STATEMENT"
    CASH_FLOW = "CASH_FLOW"


class PeriodKind(str, Enum):
    INSTANT = "INSTANT"
    DURATION = "DURATION"


class ValidationSeverity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"


class AccountingValidationError(ValueError):
    pass


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class StatementPeriod:
    period_kind: PeriodKind
    start_date: date | None
    end_date: date
    fiscal_year: int
    fiscal_period: str

    def __post_init__(self) -> None:
        if self.fiscal_year < 1900 or self.fiscal_year > 2200:
            raise ValueError("fiscal_year is outside supported bounds")
        if not self.fiscal_period.strip():
            raise ValueError("fiscal_period is required")
        if self.period_kind is PeriodKind.INSTANT:
            if self.start_date is not None:
                raise ValueError("instant periods must not have start_date")
        else:
            if self.start_date is None:
                raise ValueError("duration periods require start_date")
            if self.start_date > self.end_date:
                raise ValueError("period start cannot follow period end")


@dataclass(frozen=True)
class FilingContext:
    accession_id: str
    form_type: str
    filed_date: date
    accepted_at: datetime
    knowledge_time: datetime
    source_artifact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.accession_id.strip():
            raise ValueError("accession_id is required")
        if not self.form_type.strip():
            raise ValueError("form_type is required")
        if self.accepted_at.tzinfo is None or self.knowledge_time.tzinfo is None:
            raise ValueError("accepted_at and knowledge_time must be timezone-aware")
        if self.knowledge_time < self.accepted_at:
            raise ValueError("knowledge_time cannot precede accepted_at")
        if self.filed_date > self.accepted_at.date():
            raise ValueError("filed_date cannot follow accepted_at calendar date")
        if not self.source_artifact_ids:
            raise ValueError("filing requires at least one source artifact")
        for artifact_id in self.source_artifact_ids:
            if not _SHA256.fullmatch(artifact_id):
                raise ValueError("filing source artifacts must be SHA-256 artifact IDs")


@dataclass(frozen=True)
class FinancialLineItem:
    key: str
    value: Decimal
    source_artifact_ids: tuple[str, ...]
    source_locator: str
    claim_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("line-item key is required")
        if not self.value.is_finite():
            raise ValueError("line-item value must be finite")
        if not self.source_locator.strip():
            raise ValueError("line-item source locator is required")
        if not self.source_artifact_ids:
            raise ValueError("line-item provenance requires source artifacts")
        for artifact_id in self.source_artifact_ids:
            if not _SHA256.fullmatch(artifact_id):
                raise ValueError(
                    "line-item source artifacts must be SHA-256 artifact IDs"
                )
        if any(not claim_id.startswith("claim:") for claim_id in self.claim_ids):
            raise ValueError("line-item claim IDs must use claim: identities")


@dataclass(frozen=True)
class FinancialStatement:
    statement_id: str
    entity_id: str
    statement_type: StatementType
    currency: str
    period: StatementPeriod
    filing: FilingContext
    items: tuple[FinancialLineItem, ...]

    def __post_init__(self) -> None:
        if not self.entity_id.strip():
            raise ValueError("entity_id is required")
        currency = self.currency.strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            raise ValueError("currency must be a three-letter alphabetic code")
        if not self.items:
            raise ValueError("statement must contain line items")
        keys = [item.key for item in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate standardized line-item keys are not allowed")
        if self.period.end_date > self.filing.filed_date:
            raise ValueError("statement period end cannot follow filing date")
        if self.statement_type is StatementType.BALANCE_SHEET:
            if self.period.period_kind is not PeriodKind.INSTANT:
                raise ValueError("balance sheet requires an instant period")
        elif self.period.period_kind is not PeriodKind.DURATION:
            raise ValueError("income and cash-flow statements require duration periods")

        filing_artifacts = set(self.filing.source_artifact_ids)
        for item in self.items:
            if not set(item.source_artifact_ids).issubset(filing_artifacts):
                raise ValueError(
                    "line-item artifacts must be part of the filing artifact set"
                )

        expected = make_statement_id(
            entity_id=self.entity_id,
            statement_type=self.statement_type,
            currency=currency,
            period=self.period,
            filing=self.filing,
            items=self.items,
        )
        if self.statement_id != expected:
            raise ValueError("statement_id does not match statement contents")


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    severity: ValidationSeverity
    message: str
    difference: Decimal | None = None


@dataclass(frozen=True)
class StatementValidationReport:
    statement_id: str
    issues: tuple[ValidationIssue, ...]

    @property
    def is_valid(self) -> bool:
        return not any(
            issue.severity is ValidationSeverity.ERROR for issue in self.issues
        )

    def require_valid(self) -> None:
        errors = [
            f"{issue.code}: {issue.message}"
            for issue in self.issues
            if issue.severity is ValidationSeverity.ERROR
        ]
        if errors:
            raise AccountingValidationError("; ".join(errors))


@dataclass(frozen=True)
class ThreeStatementValidationReport:
    entity_id: str
    statement_ids: tuple[str, ...]
    issues: tuple[ValidationIssue, ...]

    @property
    def is_valid(self) -> bool:
        return not any(
            issue.severity is ValidationSeverity.ERROR for issue in self.issues
        )

    def require_valid(self) -> None:
        errors = [
            f"{issue.code}: {issue.message}"
            for issue in self.issues
            if issue.severity is ValidationSeverity.ERROR
        ]
        if errors:
            raise AccountingValidationError("; ".join(errors))


def _canonical_period(period: StatementPeriod) -> dict[str, object]:
    return {
        "period_kind": period.period_kind.value,
        "start_date": period.start_date.isoformat() if period.start_date else None,
        "end_date": period.end_date.isoformat(),
        "fiscal_year": period.fiscal_year,
        "fiscal_period": period.fiscal_period,
    }


def _canonical_filing(filing: FilingContext) -> dict[str, object]:
    return {
        "accession_id": filing.accession_id,
        "form_type": filing.form_type,
        "filed_date": filing.filed_date.isoformat(),
        "accepted_at": filing.accepted_at.isoformat(),
        "knowledge_time": filing.knowledge_time.isoformat(),
        "source_artifact_ids": list(filing.source_artifact_ids),
    }


def _canonical_items(
    items: tuple[FinancialLineItem, ...],
) -> list[dict[str, object]]:
    return [
        {
            "key": item.key,
            "value": str(item.value),
            "source_artifact_ids": list(item.source_artifact_ids),
            "source_locator": item.source_locator,
            "claim_ids": list(item.claim_ids),
        }
        for item in sorted(items, key=lambda value: value.key)
    ]


def make_statement_id(
    *,
    entity_id: str,
    statement_type: StatementType,
    currency: str,
    period: StatementPeriod,
    filing: FilingContext,
    items: tuple[FinancialLineItem, ...],
) -> str:
    payload = {
        "entity_id": entity_id,
        "statement_type": statement_type.value,
        "currency": currency.strip().upper(),
        "period": _canonical_period(period),
        "filing": _canonical_filing(filing),
        "items": _canonical_items(items),
    }
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "statement:" + hashlib.sha256(material).hexdigest()


def build_statement(
    *,
    entity_id: str,
    statement_type: StatementType,
    currency: str,
    period: StatementPeriod,
    filing: FilingContext,
    items: tuple[FinancialLineItem, ...],
) -> FinancialStatement:
    statement_id = make_statement_id(
        entity_id=entity_id,
        statement_type=statement_type,
        currency=currency,
        period=period,
        filing=filing,
        items=items,
    )
    return FinancialStatement(
        statement_id=statement_id,
        entity_id=entity_id,
        statement_type=statement_type,
        currency=currency.strip().upper(),
        period=period,
        filing=filing,
        items=items,
    )


class FinancialStatementValidator:
    """Accounting-identity checks used before modeling or valuation."""

    def __init__(self, *, tolerance: Decimal = Decimal("0.01")) -> None:
        if tolerance < 0:
            raise ValueError("tolerance must be non-negative")
        self.tolerance = tolerance

    def validate(
        self,
        statement: FinancialStatement,
    ) -> StatementValidationReport:
        values = {item.key: item.value for item in statement.items}
        issues: list[ValidationIssue] = []

        if statement.statement_type is StatementType.BALANCE_SHEET:
            self._require_keys(
                values,
                ("total_assets", "total_liabilities", "total_equity"),
                issues,
            )
            if all(
                key in values
                for key in ("total_assets", "total_liabilities", "total_equity")
            ):
                difference = values["total_assets"] - (
                    values["total_liabilities"] + values["total_equity"]
                )
                if abs(difference) > self.tolerance:
                    issues.append(
                        ValidationIssue(
                            code="BALANCE_SHEET_DOES_NOT_BALANCE",
                            severity=ValidationSeverity.ERROR,
                            message=(
                                "total_assets must equal total_liabilities + total_equity"
                            ),
                            difference=difference,
                        )
                    )

        elif statement.statement_type is StatementType.CASH_FLOW:
            required = (
                "beginning_cash",
                "cash_from_operating_activities",
                "cash_from_investing_activities",
                "cash_from_financing_activities",
                "net_change_in_cash",
                "ending_cash",
            )
            self._require_keys(values, required, issues)
            if all(key in values for key in required):
                component_change = (
                    values["cash_from_operating_activities"]
                    + values["cash_from_investing_activities"]
                    + values["cash_from_financing_activities"]
                )
                difference = values["net_change_in_cash"] - component_change
                if abs(difference) > self.tolerance:
                    issues.append(
                        ValidationIssue(
                            code="CASH_FLOW_COMPONENTS_DO_NOT_TIE",
                            severity=ValidationSeverity.ERROR,
                            message=(
                                "net_change_in_cash must equal operating + investing + financing cash flow"
                            ),
                            difference=difference,
                        )
                    )
                roll_forward = (
                    values["beginning_cash"] + values["net_change_in_cash"]
                )
                difference = values["ending_cash"] - roll_forward
                if abs(difference) > self.tolerance:
                    issues.append(
                        ValidationIssue(
                            code="CASH_ROLL_FORWARD_DOES_NOT_TIE",
                            severity=ValidationSeverity.ERROR,
                            message=(
                                "ending_cash must equal beginning_cash + net_change_in_cash"
                            ),
                            difference=difference,
                        )
                    )

        else:
            identities = (
                (
                    "GROSS_PROFIT_DOES_NOT_TIE",
                    "gross_profit",
                    ("revenue", "cost_of_revenue"),
                    lambda v: v["revenue"] - v["cost_of_revenue"],
                    "gross_profit must equal revenue - cost_of_revenue",
                ),
                (
                    "OPERATING_INCOME_DOES_NOT_TIE",
                    "operating_income",
                    ("gross_profit", "operating_expenses"),
                    lambda v: v["gross_profit"] - v["operating_expenses"],
                    "operating_income must equal gross_profit - operating_expenses",
                ),
                (
                    "NET_INCOME_DOES_NOT_TIE",
                    "net_income",
                    ("pretax_income", "income_tax_expense"),
                    lambda v: v["pretax_income"] - v["income_tax_expense"],
                    "net_income must equal pretax_income - income_tax_expense",
                ),
            )
            for code, target, components, formula, message in identities:
                if target in values and all(key in values for key in components):
                    difference = values[target] - formula(values)
                    if abs(difference) > self.tolerance:
                        issues.append(
                            ValidationIssue(
                                code=code,
                                severity=ValidationSeverity.ERROR,
                                message=message,
                                difference=difference,
                            )
                        )

        return StatementValidationReport(
            statement_id=statement.statement_id,
            issues=tuple(issues),
        )

    @staticmethod
    def _require_keys(
        values: dict[str, Decimal],
        required: tuple[str, ...],
        issues: list[ValidationIssue],
    ) -> None:
        for key in required:
            if key not in values:
                issues.append(
                    ValidationIssue(
                        code="MISSING_REQUIRED_LINE_ITEM",
                        severity=ValidationSeverity.ERROR,
                        message=f"required standardized line item is missing: {key}",
                    )
                )

    def validate_three_statement_set(
        self,
        statements: tuple[FinancialStatement, ...],
    ) -> ThreeStatementValidationReport:
        by_type: dict[StatementType, FinancialStatement] = {}
        issues: list[ValidationIssue] = []
        for statement in statements:
            if statement.statement_type in by_type:
                issues.append(
                    ValidationIssue(
                        code="DUPLICATE_STATEMENT_TYPE",
                        severity=ValidationSeverity.ERROR,
                        message=(
                            f"multiple {statement.statement_type.value} statements supplied"
                        ),
                    )
                )
            else:
                by_type[statement.statement_type] = statement
            issues.extend(self.validate(statement).issues)

        missing = [
            statement_type
            for statement_type in StatementType
            if statement_type not in by_type
        ]
        for statement_type in missing:
            issues.append(
                ValidationIssue(
                    code="MISSING_STATEMENT",
                    severity=ValidationSeverity.ERROR,
                    message=f"missing {statement_type.value}",
                )
            )

        if by_type:
            entity_ids = {statement.entity_id for statement in by_type.values()}
            currencies = {statement.currency for statement in by_type.values()}
            period_ends = {
                statement.period.end_date for statement in by_type.values()
            }
            accession_ids = {
                statement.filing.accession_id for statement in by_type.values()
            }
            if len(entity_ids) != 1:
                issues.append(
                    ValidationIssue(
                        code="ENTITY_MISMATCH",
                        severity=ValidationSeverity.ERROR,
                        message="three-statement set mixes entities",
                    )
                )
            if len(currencies) != 1:
                issues.append(
                    ValidationIssue(
                        code="CURRENCY_MISMATCH",
                        severity=ValidationSeverity.ERROR,
                        message="three-statement set mixes currencies",
                    )
                )
            if len(period_ends) != 1:
                issues.append(
                    ValidationIssue(
                        code="PERIOD_END_MISMATCH",
                        severity=ValidationSeverity.ERROR,
                        message="three-statement set has inconsistent period ends",
                    )
                )
            if len(accession_ids) != 1:
                issues.append(
                    ValidationIssue(
                        code="FILING_MISMATCH",
                        severity=ValidationSeverity.ERROR,
                        message="three-statement set mixes filing accessions",
                    )
                )

        if (
            StatementType.BALANCE_SHEET in by_type
            and StatementType.CASH_FLOW in by_type
        ):
            balance_values = {
                item.key: item.value
                for item in by_type[StatementType.BALANCE_SHEET].items
            }
            cash_values = {
                item.key: item.value
                for item in by_type[StatementType.CASH_FLOW].items
            }
            if (
                "cash_and_cash_equivalents" in balance_values
                and "ending_cash" in cash_values
            ):
                difference = (
                    balance_values["cash_and_cash_equivalents"]
                    - cash_values["ending_cash"]
                )
                if abs(difference) > self.tolerance:
                    issues.append(
                        ValidationIssue(
                            code="CASH_DOES_NOT_TIE_ACROSS_STATEMENTS",
                            severity=ValidationSeverity.ERROR,
                            message=(
                                "balance-sheet cash must equal cash-flow ending_cash"
                            ),
                            difference=difference,
                        )
                    )
            else:
                issues.append(
                    ValidationIssue(
                        code="CASH_CROSS_CHECK_UNAVAILABLE",
                        severity=ValidationSeverity.ERROR,
                        message=(
                            "cash_and_cash_equivalents and ending_cash are required for cross-statement tie-out"
                        ),
                    )
                )

        entity_id = (
            next(iter(by_type.values())).entity_id if by_type else "UNKNOWN"
        )
        return ThreeStatementValidationReport(
            entity_id=entity_id,
            statement_ids=tuple(
                sorted(statement.statement_id for statement in statements)
            ),
            issues=tuple(issues),
        )


class FinancialStatementStore:
    """Append-only content-addressed store for standardized statements."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS financial_statements (
                statement_id VARCHAR PRIMARY KEY,
                entity_id VARCHAR NOT NULL,
                statement_type VARCHAR NOT NULL,
                period_end DATE NOT NULL,
                knowledge_time TIMESTAMPTZ NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, statement: FinancialStatement) -> bool:
        payload = json.dumps(
            _statement_payload(statement),
            sort_keys=True,
            separators=(",", ":"),
        )
        existing = self._con.execute(
            "SELECT payload_json FROM financial_statements WHERE statement_id = ?",
            [statement.statement_id],
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != payload:
                raise ValueError("statement identity conflict")
            return False
        self._con.execute(
            """
            INSERT INTO financial_statements
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                statement.statement_id,
                statement.entity_id,
                statement.statement_type.value,
                statement.period.end_date,
                statement.filing.knowledge_time,
                payload,
            ],
        )
        return True

    def get(self, statement_id: str) -> FinancialStatement | None:
        row = self._con.execute(
            "SELECT payload_json FROM financial_statements WHERE statement_id = ?",
            [statement_id],
        ).fetchone()
        if row is None:
            return None
        return _statement_from_payload(json.loads(str(row[0])))

    def as_of(
        self,
        *,
        entity_id: str,
        statement_type: StatementType,
        as_of: datetime,
    ) -> tuple[FinancialStatement, ...]:
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        rows = self._con.execute(
            """
            SELECT payload_json
            FROM financial_statements
            WHERE entity_id = ?
              AND statement_type = ?
              AND knowledge_time <= ?
            ORDER BY period_end, knowledge_time, statement_id
            """,
            [entity_id, statement_type.value, as_of],
        ).fetchall()
        return tuple(
            _statement_from_payload(json.loads(str(row[0])))
            for row in rows
        )

    def close(self) -> None:
        self._con.close()


def _statement_payload(statement: FinancialStatement) -> dict[str, object]:
    return {
        "statement_id": statement.statement_id,
        "entity_id": statement.entity_id,
        "statement_type": statement.statement_type.value,
        "currency": statement.currency,
        "period": _canonical_period(statement.period),
        "filing": _canonical_filing(statement.filing),
        "items": _canonical_items(statement.items),
    }


def _statement_from_payload(payload: dict[str, object]) -> FinancialStatement:
    period_raw = dict(payload["period"])  # type: ignore[arg-type]
    filing_raw = dict(payload["filing"])  # type: ignore[arg-type]
    items_raw = list(payload["items"])  # type: ignore[arg-type]
    period = StatementPeriod(
        period_kind=PeriodKind(str(period_raw["period_kind"])),
        start_date=(
            date.fromisoformat(str(period_raw["start_date"]))
            if period_raw["start_date"] is not None
            else None
        ),
        end_date=date.fromisoformat(str(period_raw["end_date"])),
        fiscal_year=int(period_raw["fiscal_year"]),
        fiscal_period=str(period_raw["fiscal_period"]),
    )
    filing = FilingContext(
        accession_id=str(filing_raw["accession_id"]),
        form_type=str(filing_raw["form_type"]),
        filed_date=date.fromisoformat(str(filing_raw["filed_date"])),
        accepted_at=datetime.fromisoformat(str(filing_raw["accepted_at"])),
        knowledge_time=datetime.fromisoformat(str(filing_raw["knowledge_time"])),
        source_artifact_ids=tuple(filing_raw["source_artifact_ids"]),  # type: ignore[arg-type]
    )
    items = tuple(
        FinancialLineItem(
            key=str(item["key"]),
            value=Decimal(str(item["value"])),
            source_artifact_ids=tuple(item["source_artifact_ids"]),
            source_locator=str(item["source_locator"]),
            claim_ids=tuple(item["claim_ids"]),
        )
        for item in items_raw
    )
    return FinancialStatement(
        statement_id=str(payload["statement_id"]),
        entity_id=str(payload["entity_id"]),
        statement_type=StatementType(str(payload["statement_type"])),
        currency=str(payload["currency"]),
        period=period,
        filing=filing,
        items=items,
    )
