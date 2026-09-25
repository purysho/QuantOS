"""Stage 21 — company analysis from keyless SEC XBRL facts.

``quantos analyze TICKER`` turns the point-in-time XBRL facts from Stage 19
into standardized annual statements, using the Stage 7 model (balance sheet,
income statement, cash flow) with full filing provenance. It runs the Stage 7
accounting-identity validators and derives a metrics table for the terminal.

Rules:

* **As originally filed.** Each fiscal year is built from the 10-K that first
  reported it (its primary period). Later restatements remain visible as
  versions in ``xbrl_facts`` and never overwrite what was known.
* **Point in time.** Only facts known at ``known_at`` are used.
* **Explicit mapping.** Each standardized line maps to an ordered list of US
  GAAP concepts; the concept actually used is the line's source locator. A
  line computed from other lines is marked ``derived:`` in its locator.
* **Fail visible.** Missing lines and identity breaks (for example net
  income ≠ pretax income − tax when there are equity-method or discontinued
  items) are reported as validation issues, never forced to tie.
* **No automatic valuation.** A DCF or other valuation needs reviewed,
  evidence-backed assumptions (Stage 8). This module only prepares the
  statements that valuation work starts from.
"""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb

from .financial_statements import (
    FilingContext,
    FinancialLineItem,
    FinancialStatement,
    FinancialStatementStore,
    FinancialStatementValidator,
    PeriodKind,
    StatementPeriod,
    StatementType,
    build_statement,
)

# standardized key -> ordered US GAAP concepts (first reported one wins)
INCOME_CONCEPTS: dict[str, tuple[str, ...]] = {
    "revenue": ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
                "RevenuesNetOfInterestExpense"),
    "cost_of_revenue": ("CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfServices"),
    "gross_profit": ("GrossProfit",),
    "operating_expenses": ("OperatingExpenses", "CostsAndExpenses"),
    "operating_income": ("OperatingIncomeLoss",),
    "interest_expense": ("InterestExpense", "InterestExpenseNonoperating"),
    "pretax_income": ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                      "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"),
    "income_tax_expense": ("IncomeTaxExpenseBenefit",),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "consolidated_net_income": ("ProfitLoss",),
}
PER_SHARE_CONCEPTS: dict[str, tuple[str, str]] = {
    "eps_diluted": ("EarningsPerShareDiluted", "USD/shares"),
    "eps_basic_and_diluted": ("EarningsPerShareBasicAndDiluted", "USD/shares"),
    "diluted_shares": ("WeightedAverageNumberOfDilutedSharesOutstanding", "shares"),
}
BALANCE_CONCEPTS: dict[str, tuple[str, ...]] = {
    "cash_and_cash_equivalents": ("CashAndCashEquivalentsAtCarryingValue",
                             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
    "total_current_assets": ("AssetsCurrent",),
    "total_assets": ("Assets",),
    "total_current_liabilities": ("LiabilitiesCurrent",),
    "total_liabilities": ("Liabilities",),
    "total_equity": ("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "StockholdersEquity"),
    "temporary_equity": ("TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests",
                         "TemporaryEquityCarryingAmountAttributableToParent",
                         "RedeemableNoncontrollingInterestEquityCarryingAmount",
                         "RedeemableNoncontrollingInterestEquityCommonCarryingAmount"),
    "long_term_debt": ("LongTermDebtNoncurrent", "LongTermDebt"),
    "current_debt": ("LongTermDebtCurrent", "DebtCurrent"),
}
CASH_FLOW_CONCEPTS: dict[str, tuple[str, ...]] = {
    "cash_from_operating_activities": ("NetCashProvidedByUsedInOperatingActivities",),
    "cash_from_investing_activities": ("NetCashProvidedByUsedInInvestingActivities",),
    "cash_from_financing_activities": ("NetCashProvidedByUsedInFinancingActivities",),
    "capital_expenditures": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "depreciation_amortization": ("DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
                                  "DepreciationAmortizationAndAccretionNet"),
    "effect_of_exchange_rate_on_cash": (
        "EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsIncludingDisposalGroupAndDiscontinuedOperations",
        "EffectOfExchangeRateOnCashAndCashEquivalents",
    ),
    "net_change_in_cash": (
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseExcludingExchangeRateEffect",
        "CashAndCashEquivalentsPeriodIncreaseDecrease",
    ),
}
CASH_BALANCE_CONCEPTS = ("CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
                         "CashAndCashEquivalentsAtCarryingValue")
ANNUAL_DAYS = range(350, 380)


class AnalysisError(ValueError):
    pass


@dataclass(frozen=True)
class _Fact:
    concept: str
    unit: str
    value: Decimal
    start: date | None
    end: date
    form: str
    accession: str
    filed: date
    knowledge_time: datetime
    artifact_id: str | None


@dataclass(frozen=True)
class AnnualReport:
    fiscal_year_end: date
    accession: str
    form: str
    filed: date
    knowledge_time: datetime
    statements: tuple[FinancialStatement, ...]
    per_share: dict[str, Decimal]
    issues: tuple[tuple[str, str, str], ...]  # (severity, code, message)


@dataclass(frozen=True)
class CompanyAnalysis:
    analysis_id: str
    cik: int
    ticker: str
    known_at: datetime
    reports: tuple[AnnualReport, ...]
    metrics: tuple[dict, ...] = field(default_factory=tuple)


def load_facts(fundamentals_db: str | Path, *, cik: int, known_at: datetime) -> tuple[_Fact, ...]:
    con = duckdb.connect(str(fundamentals_db), read_only=True)
    try:
        rows = con.execute(
            """
            SELECT concept, unit, value, period_start, period_end, form, accession, filed, knowledge_time,
                   source_artifact_id
            FROM xbrl_facts
            WHERE cik = ? AND taxonomy = 'us-gaap' AND knowledge_time <= ?
            """,
            [cik, known_at],
        ).fetchall()
    finally:
        con.close()
    return tuple(_Fact(c, u, Decimal(v), s, e, f, a, fd, k, art) for c, u, v, s, e, f, a, fd, k, art in rows)


def analyze_company(*, cik: int, ticker: str, facts: tuple[_Fact, ...], known_at: datetime,
                    years: int = 10) -> CompanyAnalysis:
    if not facts:
        raise AnalysisError(f"no XBRL facts for CIK {cik} known at {known_at.isoformat()} (run `quantos daily`)")
    annual = [f for f in facts if f.form in ("10-K", "10-K/A", "10-KT")]
    by_accession: dict[str, list[_Fact]] = {}
    for fact in annual:
        by_accession.setdefault(fact.accession, []).append(fact)
    # A filing's primary period is its latest balance-sheet date; the first
    # filing to report a fiscal year-end is the one it is built from.
    primary: dict[date, list[_Fact]] = {}
    for accession, rows in sorted(by_accession.items(), key=lambda item: min(f.filed for f in item[1])):
        instants = [f.end for f in rows if f.start is None and f.concept == "Assets"]
        if not instants:
            continue
        year_end = max(instants)
        primary.setdefault(year_end, rows)
    reports = []
    for year_end in sorted(primary)[-years:]:
        rows = primary[year_end]
        assets = next((f.value for f in rows if f.concept == "Assets" and f.end == year_end and f.start is None), None)
        # Filers round to thousands or millions, so identities tie only to
        # rounding: tolerance is 1 in 100,000 of total assets (at least $1,000).
        tolerance = max(Decimal(1000), abs(assets) / 100000) if assets is not None else Decimal(1000)
        reports.append(_build_year(cik, year_end, rows, FinancialStatementValidator(tolerance=tolerance)))
    if not reports:
        raise AnalysisError(f"CIK {cik} has no 10-K balance sheets in its XBRL facts (funds and trusts file none)")
    metrics = tuple(_metrics(reports))
    analysis_id = "company-analysis:" + hashlib.sha256(json.dumps({
        "cik": cik, "known_at": known_at.isoformat(),
        "statements": [s.statement_id for r in reports for s in r.statements],
    }, sort_keys=True).encode()).hexdigest()
    return CompanyAnalysis(analysis_id, cik, ticker.upper(), known_at, tuple(reports), metrics)


def _build_year(cik: int, year_end: date, rows: list[_Fact], validator) -> AnnualReport:
    filed = min(f.filed for f in rows)
    accession = rows[0].accession
    form = rows[0].form
    knowledge = max(f.knowledge_time for f in rows)
    artifacts = tuple(sorted({f.artifact_id for f in rows if f.artifact_id}))
    if not artifacts:
        raise AnalysisError("XBRL facts have no archived source artifact; re-fetch with `quantos daily`")
    filing = FilingContext(accession_id=accession, form_type=form, filed_date=filed,
                           accepted_at=knowledge, knowledge_time=knowledge, source_artifact_ids=artifacts)
    duration = [f for f in rows if f.start is not None and f.end == year_end
                and (f.end - f.start).days in ANNUAL_DAYS]
    instants = [f for f in rows if f.start is None and f.end == year_end]
    starts = sorted({f.start for f in duration})
    period_start = starts[0] if starts else None
    entity = f"CIK:{cik:010d}"
    issues: list[tuple[str, str, str]] = []
    statements = []

    def items_for(concepts: dict[str, tuple[str, ...]], pool: list[_Fact]) -> dict[str, FinancialLineItem]:
        found = {}
        for key, candidates in concepts.items():
            for concept in candidates:
                matches = [f for f in pool if f.concept == concept and f.unit == "USD"]
                if matches:
                    fact = matches[0]
                    found[key] = FinancialLineItem(
                        key=key, value=fact.value, source_artifact_ids=(fact.artifact_id or artifacts[0],),
                        source_locator=f"us-gaap:{concept} {fact.accession}")
                    break
        return found

    income = items_for(INCOME_CONCEPTS, duration)
    if "gross_profit" not in income and {"revenue", "cost_of_revenue"} <= income.keys():
        income["gross_profit"] = _derived("gross_profit", income["revenue"].value - income["cost_of_revenue"].value,
                                          "revenue - cost_of_revenue", artifacts)
    balance = items_for(BALANCE_CONCEPTS, instants)
    if "total_liabilities" not in balance:
        both = [f for f in instants if f.concept == "LiabilitiesAndStockholdersEquity" and f.unit == "USD"]
        if both and "total_equity" in balance:
            balance["total_liabilities"] = _derived(
                "total_liabilities", both[0].value - balance["total_equity"].value,
                "LiabilitiesAndStockholdersEquity - total_equity", artifacts)
    cash_flow = items_for(CASH_FLOW_CONCEPTS, duration)
    ending = [f for f in instants if f.concept in CASH_BALANCE_CONCEPTS and f.unit == "USD"]
    beginning = [f for f in rows if f.start is None and period_start and f.concept in CASH_BALANCE_CONCEPTS
                 and f.unit == "USD" and 0 <= (period_start - f.end).days <= 1]
    for key, pool in (("ending_cash", ending), ("beginning_cash", beginning)):
        if pool:
            chosen = sorted(pool, key=lambda f: CASH_BALANCE_CONCEPTS.index(f.concept))[0]
            cash_flow[key] = FinancialLineItem(key=key, value=chosen.value,
                                               source_artifact_ids=(chosen.artifact_id or artifacts[0],),
                                               source_locator=f"us-gaap:{chosen.concept} {chosen.accession}")

    fiscal_year = year_end.year
    for statement_type, items, kind in (
        (StatementType.INCOME_STATEMENT, income, PeriodKind.DURATION),
        (StatementType.BALANCE_SHEET, balance, PeriodKind.INSTANT),
        (StatementType.CASH_FLOW, cash_flow, PeriodKind.DURATION),
    ):
        if not items:
            issues.append(("ERROR", "STATEMENT_NOT_REPORTED", f"no {statement_type.value} lines in {accession}"))
            continue
        if kind is PeriodKind.DURATION and period_start is None:
            issues.append(("ERROR", "NO_ANNUAL_PERIOD", f"no 12-month period ending {year_end} in {accession}"))
            continue
        period = StatementPeriod(period_kind=kind, start_date=period_start if kind is PeriodKind.DURATION else None,
                                 end_date=year_end, fiscal_year=fiscal_year, fiscal_period="FY")
        statements.append(build_statement(entity_id=entity, statement_type=statement_type, currency="USD",
                                          period=period, filing=filing, items=tuple(items[k] for k in sorted(items))))
    report = validator.validate_three_statement_set(tuple(statements)) if statements else None
    if report is not None:
        issues.extend((i.severity.value, i.code, i.message) for i in report.issues)
    per_share = {}
    for key, (concept, unit) in PER_SHARE_CONCEPTS.items():
        matches = [f for f in duration if f.concept == concept and f.unit == unit]
        if matches:
            per_share[key] = matches[0].value
    return AnnualReport(year_end, accession, form, filed, knowledge, tuple(statements), per_share,
                        tuple(dict.fromkeys(issues)))


def _derived(key: str, value: Decimal, formula: str, artifacts: tuple[str, ...]) -> FinancialLineItem:
    return FinancialLineItem(key=key, value=value, source_artifact_ids=(artifacts[0],),
                             source_locator=f"derived: {formula}")


def _metrics(reports: list[AnnualReport]):
    previous = None
    for report in reports:
        v: dict[str, Decimal] = {}
        for statement in report.statements:
            v.update({item.key: item.value for item in statement.items})

        def ratio(a: str, b: str, source=v):
            return float(source[a] / source[b]) if a in source and b in source and source[b] != 0 else None

        fcf = (v["cash_from_operating_activities"] - v["capital_expenditures"]
               if {"cash_from_operating_activities", "capital_expenditures"} <= v.keys() else None)
        debt = sum((v.get(k, Decimal(0)) for k in ("long_term_debt", "current_debt")), Decimal(0))
        row = {
            "fiscal_year_end": report.fiscal_year_end,
            "filed": report.filed,
            "accession": report.accession,
            "knowledge_time": report.knowledge_time,
            "revenue": _f(v.get("revenue")),
            "revenue_growth": (float(v["revenue"] / previous["revenue"] - 1)
                               if previous and "revenue" in v and previous.get("revenue") else None),
            "gross_margin": ratio("gross_profit", "revenue"),
            "operating_margin": ratio("operating_income", "revenue"),
            "net_margin": ratio("net_income", "revenue"),
            "net_income": _f(v.get("net_income")),
            "eps_diluted": _f(report.per_share.get("eps_diluted", report.per_share.get("eps_basic_and_diluted"))),
            "free_cash_flow": _f(fcf),
            "fcf_margin": float(fcf / v["revenue"]) if fcf is not None and v.get("revenue") else None,
            "return_on_equity": ratio("net_income", "total_equity"),
            "return_on_assets": ratio("net_income", "total_assets"),
            "debt_to_equity": float(debt / v["total_equity"]) if debt and v.get("total_equity") else None,
            "current_ratio": ratio("total_current_assets", "total_current_liabilities"),
            "total_assets": _f(v.get("total_assets")),
            "validation_errors": sum(1 for s, _, _ in report.issues if s == "ERROR"),
        }
        previous = v
        yield row


def _f(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


class CompanyAnalysisStore:
    """Metrics and validation issues for the terminal (Company / Financials)."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS company_metrics (
                analysis_id VARCHAR NOT NULL, cik BIGINT NOT NULL, ticker VARCHAR NOT NULL,
                known_at TIMESTAMPTZ NOT NULL, fiscal_year_end DATE NOT NULL, filed DATE NOT NULL,
                accession VARCHAR NOT NULL, knowledge_time TIMESTAMPTZ NOT NULL,
                revenue DOUBLE, revenue_growth DOUBLE, gross_margin DOUBLE, operating_margin DOUBLE,
                net_margin DOUBLE, net_income DOUBLE, eps_diluted DOUBLE, free_cash_flow DOUBLE,
                fcf_margin DOUBLE, return_on_equity DOUBLE, return_on_assets DOUBLE, debt_to_equity DOUBLE,
                current_ratio DOUBLE, total_assets DOUBLE, validation_errors INTEGER,
                PRIMARY KEY (analysis_id, fiscal_year_end)
            )
            """
        )
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS statement_validation_issues (
                analysis_id VARCHAR NOT NULL, cik BIGINT NOT NULL, ticker VARCHAR NOT NULL,
                fiscal_year_end DATE NOT NULL, accession VARCHAR NOT NULL,
                severity VARCHAR NOT NULL, code VARCHAR NOT NULL, message VARCHAR NOT NULL
            )
            """
        )

    def add(self, analysis: CompanyAnalysis) -> bool:
        if self._con.execute("SELECT 1 FROM company_metrics WHERE analysis_id = ? LIMIT 1",
                             [analysis.analysis_id]).fetchone():
            return False
        # Only the latest analysis per company is shown; older ones are replaced.
        self._con.execute("DELETE FROM company_metrics WHERE cik = ?", [analysis.cik])
        self._con.execute("DELETE FROM statement_validation_issues WHERE cik = ?", [analysis.cik])
        columns = ["fiscal_year_end", "filed", "accession", "knowledge_time", "revenue", "revenue_growth",
                   "gross_margin", "operating_margin", "net_margin", "net_income", "eps_diluted", "free_cash_flow",
                   "fcf_margin", "return_on_equity", "return_on_assets", "debt_to_equity", "current_ratio",
                   "total_assets", "validation_errors"]
        for row in analysis.metrics:
            self._con.execute(
                f"INSERT INTO company_metrics (analysis_id, cik, ticker, known_at, {', '.join(columns)}) "
                f"VALUES ({', '.join(['?'] * (4 + len(columns)))})",
                [analysis.analysis_id, analysis.cik, analysis.ticker, analysis.known_at, *[row[c] for c in columns]],
            )
        for report in analysis.reports:
            for severity, code, message in report.issues:
                self._con.execute("INSERT INTO statement_validation_issues VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                  [analysis.analysis_id, analysis.cik, analysis.ticker, report.fiscal_year_end,
                                   report.accession, severity, code, message])
        return True

    def close(self) -> None:
        self._con.close()


def store_statements(path: str | Path, analysis: CompanyAnalysis) -> int:
    store = FinancialStatementStore(path)
    try:
        return sum(1 for r in analysis.reports for s in r.statements if store.add(s))
    finally:
        store.close()


def render(analysis: CompanyAnalysis) -> str:
    """Plain-text table: fiscal years as columns (most recent last)."""

    rows = list(analysis.metrics)[-6:]
    lines = [f"{analysis.ticker} (CIK {analysis.cik:010d}) — as originally filed in 10-Ks, known at "
             f"{analysis.known_at.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC", ""]
    header = f"{'':22}" + "".join(f"{r['fiscal_year_end']:%Y-%m-%d}".rjust(13) for r in rows)
    lines.append(header)
    spec = [("revenue", "Revenue ($bn)", 1e-9, "{:,.1f}"), ("revenue_growth", "Revenue growth", 100, "{:+.1f}%"),
            ("gross_margin", "Gross margin", 100, "{:.1f}%"), ("operating_margin", "Operating margin", 100, "{:.1f}%"),
            ("net_margin", "Net margin", 100, "{:.1f}%"), ("eps_diluted", "Diluted EPS ($)", 1, "{:.2f}"),
            ("free_cash_flow", "Free cash flow ($bn)", 1e-9, "{:,.1f}"), ("return_on_equity", "Return on equity", 100, "{:.1f}%"),
            ("debt_to_equity", "Debt / equity", 1, "{:.2f}"), ("current_ratio", "Current ratio", 1, "{:.2f}"),
            ("validation_errors", "Validation errors", 1, "{:.0f}")]
    for key, label, scale, fmt in spec:
        cells = [(fmt.format(r[key] * scale) if r[key] is not None else "—").rjust(13) for r in rows]
        lines.append(f"{label:22}" + "".join(cells))
    errors = [(r.fiscal_year_end, code, msg) for r in analysis.reports[-6:] for sev, code, msg in r.issues if sev == "ERROR"]
    if errors:
        lines += ["", "Validation issues (reported, never forced to tie):"]
        lines += [f"  {d:%Y-%m-%d}  {code}: {msg}" for d, code, msg in errors[:12]]
        if len(errors) > 12:
            lines.append(f"  … {len(errors) - 12} more in the terminal (statement_validation_issues)")
    lines += ["", "Valuation needs reviewed, evidence-backed assumptions; these statements are its starting point."]
    return "\n".join(lines)
