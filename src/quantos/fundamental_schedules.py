from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum

from .financial_statements import (
    AccountingValidationError,
    FinancialStatement,
    FinancialStatementValidator,
    StatementType,
)
from .models import EpistemicState


class ProjectionStatementType(str, Enum):
    BALANCE_SHEET = "BALANCE_SHEET"
    INCOME_STATEMENT = "INCOME_STATEMENT"
    CASH_FLOW = "CASH_FLOW"


@dataclass(frozen=True)
class ProjectionPeriod:
    start_date: date
    end_date: date
    fiscal_year: int
    fiscal_period: str

    def __post_init__(self) -> None:
        if self.start_date > self.end_date:
            raise ValueError("projection start cannot follow end")
        if not self.fiscal_period.strip():
            raise ValueError("fiscal_period is required")


@dataclass(frozen=True)
class ModelAssumption:
    name: str
    value: Decimal
    rationale: str
    evidence_references: tuple[str, ...]
    epistemic_state: EpistemicState = EpistemicState.ESTIMATED

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("assumption name is required")
        if not self.value.is_finite():
            raise ValueError("assumption value must be finite")
        if not self.rationale.strip():
            raise ValueError("assumption rationale is required")
        if not self.evidence_references or not all(
            reference.strip() for reference in self.evidence_references
        ):
            raise ValueError("assumption requires evidence references")
        if self.epistemic_state not in {
            EpistemicState.ESTIMATED,
            EpistemicState.INFERRED,
            EpistemicState.SPECULATIVE,
        }:
            raise ValueError(
                "model assumptions must be ESTIMATED, INFERRED or SPECULATIVE"
            )


@dataclass(frozen=True)
class AssumptionSet:
    assumption_set_id: str
    assumptions: tuple[ModelAssumption, ...]

    def __post_init__(self) -> None:
        names = [item.name for item in self.assumptions]
        if len(names) != len(set(names)):
            raise ValueError("duplicate assumption names are not allowed")
        expected = make_assumption_set_id(self.assumptions)
        if self.assumption_set_id != expected:
            raise ValueError("assumption_set_id does not match contents")

    def value(self, name: str) -> Decimal:
        for item in self.assumptions:
            if item.name == name:
                return item.value
        raise KeyError(name)


@dataclass(frozen=True)
class ProjectedLineItem:
    key: str
    value: Decimal
    epistemic_state: EpistemicState
    derived_from: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("projected line-item key is required")
        if not self.value.is_finite():
            raise ValueError("projected line-item value must be finite")
        if not self.derived_from:
            raise ValueError("projected line item requires derivation references")
        if self.epistemic_state not in {
            EpistemicState.DERIVED,
            EpistemicState.ESTIMATED,
            EpistemicState.INFERRED,
        }:
            raise ValueError("invalid projected line-item epistemic state")


@dataclass(frozen=True)
class ProjectedStatement:
    statement_type: ProjectionStatementType
    items: tuple[ProjectedLineItem, ...]

    def values(self) -> dict[str, Decimal]:
        return {item.key: item.value for item in self.items}


@dataclass(frozen=True)
class ProjectionIssue:
    code: str
    message: str
    difference: Decimal | None = None


@dataclass(frozen=True)
class ProjectionRun:
    projection_id: str
    source_statement_ids: tuple[str, ...]
    assumption_set_id: str
    period: ProjectionPeriod
    income_statement: ProjectedStatement
    balance_sheet: ProjectedStatement
    cash_flow: ProjectedStatement
    schedule_values: dict[str, Decimal]
    issues: tuple[ProjectionIssue, ...]

    @property
    def is_valid(self) -> bool:
        return not self.issues

    def require_valid(self) -> None:
        if self.issues:
            raise AccountingValidationError(
                "; ".join(f"{issue.code}: {issue.message}" for issue in self.issues)
            )


REQUIRED_ASSUMPTIONS = (
    "revenue_growth",
    "gross_margin",
    "operating_expense_ratio",
    "capex_percent_revenue",
    "depreciation_percent_beginning_ppe",
    "ar_days",
    "inventory_days",
    "ap_days",
    "new_borrowing",
    "debt_repayment",
    "interest_rate",
    "tax_rate",
    "dividend_payout_ratio",
)


def make_assumption_set_id(
    assumptions: tuple[ModelAssumption, ...],
) -> str:
    payload = [
        {
            "name": item.name,
            "value": str(item.value),
            "rationale": item.rationale,
            "evidence_references": list(item.evidence_references),
            "epistemic_state": item.epistemic_state.value,
        }
        for item in sorted(assumptions, key=lambda value: value.name)
    ]
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "assumptions:" + hashlib.sha256(material).hexdigest()


def build_assumption_set(
    assumptions: tuple[ModelAssumption, ...],
) -> AssumptionSet:
    return AssumptionSet(
        assumption_set_id=make_assumption_set_id(assumptions),
        assumptions=assumptions,
    )


class ThreeStatementProjectionEngine:
    """One-period linked projection with explicit model-estimate semantics."""

    DAYS = Decimal("365")

    def __init__(self, *, tolerance: Decimal = Decimal("0.01")) -> None:
        self.validator = FinancialStatementValidator(tolerance=tolerance)
        self.tolerance = tolerance

    def project(
        self,
        *,
        source_statements: tuple[FinancialStatement, ...],
        assumptions: AssumptionSet,
        period: ProjectionPeriod,
    ) -> ProjectionRun:
        base_report = self.validator.validate_three_statement_set(source_statements)
        base_report.require_valid()
        self._validate_assumptions(assumptions)

        by_type = {statement.statement_type: statement for statement in source_statements}
        balance = by_type[StatementType.BALANCE_SHEET]
        income = by_type[StatementType.INCOME_STATEMENT]

        if period.start_date <= balance.period.end_date:
            raise ValueError(
                "projection period must begin after the historical statement period"
            )

        bs = {item.key: item.value for item in balance.items}
        inc = {item.key: item.value for item in income.items}
        required_bs = (
            "cash_and_cash_equivalents",
            "accounts_receivable",
            "inventory",
            "property_plant_equipment",
            "other_assets",
            "total_assets",
            "accounts_payable",
            "total_debt",
            "other_liabilities",
            "total_liabilities",
            "retained_earnings",
            "other_equity",
            "total_equity",
        )
        required_inc = ("revenue", "cost_of_revenue")
        self._require_values(bs, required_bs, "balance sheet")
        self._require_values(inc, required_inc, "income statement")

        opening_assets = (
            bs["cash_and_cash_equivalents"]
            + bs["accounts_receivable"]
            + bs["inventory"]
            + bs["property_plant_equipment"]
            + bs["other_assets"]
        )
        opening_liabilities = (
            bs["accounts_payable"] + bs["total_debt"] + bs["other_liabilities"]
        )
        opening_equity = bs["retained_earnings"] + bs["other_equity"]
        if abs(opening_assets - bs["total_assets"]) > self.tolerance:
            raise AccountingValidationError(
                "historical balance-sheet asset components do not reconcile to total_assets"
            )
        if (
            abs(opening_liabilities - bs["total_liabilities"]) > self.tolerance
            or abs(opening_equity - bs["total_equity"]) > self.tolerance
        ):
            raise AccountingValidationError(
                "historical liability/equity components do not reconcile to reported totals"
            )

        a = assumptions.value
        revenue = inc["revenue"] * (Decimal("1") + a("revenue_growth"))
        cost_of_revenue = revenue * (Decimal("1") - a("gross_margin"))
        gross_profit = revenue - cost_of_revenue
        operating_expenses = revenue * a("operating_expense_ratio")
        operating_income = gross_profit - operating_expenses

        capex = revenue * a("capex_percent_revenue")
        depreciation = (
            bs["property_plant_equipment"]
            * a("depreciation_percent_beginning_ppe")
        )
        ending_ppe = bs["property_plant_equipment"] + capex - depreciation

        ending_ar = revenue * a("ar_days") / self.DAYS
        ending_inventory = cost_of_revenue * a("inventory_days") / self.DAYS
        ending_ap = cost_of_revenue * a("ap_days") / self.DAYS
        opening_nwc = (
            bs["accounts_receivable"] + bs["inventory"] - bs["accounts_payable"]
        )
        ending_nwc = ending_ar + ending_inventory - ending_ap
        change_in_nwc = ending_nwc - opening_nwc

        ending_debt = (
            bs["total_debt"] + a("new_borrowing") - a("debt_repayment")
        )
        if ending_debt < 0:
            raise ValueError("debt repayment exceeds available debt plus borrowing")
        average_debt = (bs["total_debt"] + ending_debt) / Decimal("2")
        interest_expense = average_debt * a("interest_rate")

        pretax_income = operating_income - interest_expense
        income_tax_expense = pretax_income * a("tax_rate")
        net_income = pretax_income - income_tax_expense
        dividends = max(net_income, Decimal("0")) * a("dividend_payout_ratio")
        ending_retained_earnings = (
            bs["retained_earnings"] + net_income - dividends
        )

        cash_from_operating = net_income + depreciation - change_in_nwc
        cash_from_investing = -capex
        cash_from_financing = (
            a("new_borrowing") - a("debt_repayment") - dividends
        )
        net_change_in_cash = (
            cash_from_operating + cash_from_investing + cash_from_financing
        )
        ending_cash = bs["cash_and_cash_equivalents"] + net_change_in_cash

        total_assets = (
            ending_cash
            + ending_ar
            + ending_inventory
            + ending_ppe
            + bs["other_assets"]
        )
        total_liabilities = ending_ap + ending_debt + bs["other_liabilities"]
        total_equity = ending_retained_earnings + bs["other_equity"]

        source_ids = tuple(sorted(statement.statement_id for statement in source_statements))
        assumption_ref = assumptions.assumption_set_id

        def estimate(key: str, value: Decimal, *drivers: str) -> ProjectedLineItem:
            return ProjectedLineItem(
                key=key,
                value=value,
                epistemic_state=EpistemicState.ESTIMATED,
                derived_from=tuple((*source_ids, assumption_ref, *drivers)),
            )

        income_statement = ProjectedStatement(
            statement_type=ProjectionStatementType.INCOME_STATEMENT,
            items=(
                estimate("revenue", revenue, "revenue_growth"),
                estimate("cost_of_revenue", cost_of_revenue, "gross_margin"),
                estimate("gross_profit", gross_profit, "gross_margin"),
                estimate(
                    "operating_expenses",
                    operating_expenses,
                    "operating_expense_ratio",
                ),
                estimate("operating_income", operating_income, "gross_margin"),
                estimate("interest_expense", interest_expense, "interest_rate"),
                estimate("pretax_income", pretax_income, "interest_rate"),
                estimate("income_tax_expense", income_tax_expense, "tax_rate"),
                estimate("net_income", net_income, "tax_rate"),
            ),
        )
        balance_sheet = ProjectedStatement(
            statement_type=ProjectionStatementType.BALANCE_SHEET,
            items=(
                estimate("cash_and_cash_equivalents", ending_cash, "cash_schedule"),
                estimate("accounts_receivable", ending_ar, "ar_days"),
                estimate("inventory", ending_inventory, "inventory_days"),
                estimate(
                    "property_plant_equipment",
                    ending_ppe,
                    "capex_percent_revenue",
                    "depreciation_percent_beginning_ppe",
                ),
                estimate("other_assets", bs["other_assets"], "carry_forward"),
                estimate("total_assets", total_assets, "balance_sheet_sum"),
                estimate("accounts_payable", ending_ap, "ap_days"),
                estimate(
                    "total_debt",
                    ending_debt,
                    "new_borrowing",
                    "debt_repayment",
                ),
                estimate(
                    "other_liabilities",
                    bs["other_liabilities"],
                    "carry_forward",
                ),
                estimate(
                    "total_liabilities",
                    total_liabilities,
                    "balance_sheet_sum",
                ),
                estimate(
                    "retained_earnings",
                    ending_retained_earnings,
                    "dividend_payout_ratio",
                ),
                estimate("other_equity", bs["other_equity"], "carry_forward"),
                estimate("total_equity", total_equity, "balance_sheet_sum"),
            ),
        )
        cash_flow = ProjectedStatement(
            statement_type=ProjectionStatementType.CASH_FLOW,
            items=(
                estimate(
                    "beginning_cash",
                    bs["cash_and_cash_equivalents"],
                    "historical_balance_sheet",
                ),
                estimate("net_income", net_income, "income_statement"),
                estimate(
                    "depreciation",
                    depreciation,
                    "depreciation_percent_beginning_ppe",
                ),
                estimate(
                    "change_in_net_working_capital",
                    change_in_nwc,
                    "working_capital_schedule",
                ),
                estimate(
                    "cash_from_operating_activities",
                    cash_from_operating,
                    "working_capital_schedule",
                ),
                estimate("capital_expenditures", capex, "capex_percent_revenue"),
                estimate(
                    "cash_from_investing_activities",
                    cash_from_investing,
                    "capex_percent_revenue",
                ),
                estimate("new_borrowing", a("new_borrowing"), "new_borrowing"),
                estimate("debt_repayment", a("debt_repayment"), "debt_repayment"),
                estimate("dividends", dividends, "dividend_payout_ratio"),
                estimate(
                    "cash_from_financing_activities",
                    cash_from_financing,
                    "financing_schedule",
                ),
                estimate("net_change_in_cash", net_change_in_cash, "cash_schedule"),
                estimate("ending_cash", ending_cash, "cash_schedule"),
            ),
        )

        schedule_values = {
            "revenue": revenue,
            "cost_of_revenue": cost_of_revenue,
            "capex": capex,
            "depreciation": depreciation,
            "ending_ppe": ending_ppe,
            "opening_nwc": opening_nwc,
            "ending_nwc": ending_nwc,
            "change_in_nwc": change_in_nwc,
            "ending_debt": ending_debt,
            "average_debt": average_debt,
            "interest_expense": interest_expense,
            "pretax_income": pretax_income,
            "income_tax_expense": income_tax_expense,
            "net_income": net_income,
            "dividends": dividends,
            "ending_retained_earnings": ending_retained_earnings,
            "cash_from_operating_activities": cash_from_operating,
            "cash_from_investing_activities": cash_from_investing,
            "cash_from_financing_activities": cash_from_financing,
            "net_change_in_cash": net_change_in_cash,
            "ending_cash": ending_cash,
        }
        issues = self._validate_projection(
            income_statement=income_statement,
            balance_sheet=balance_sheet,
            cash_flow=cash_flow,
        )
        projection_id = make_projection_id(
            source_statement_ids=source_ids,
            assumption_set_id=assumptions.assumption_set_id,
            period=period,
            schedule_values=schedule_values,
        )
        return ProjectionRun(
            projection_id=projection_id,
            source_statement_ids=source_ids,
            assumption_set_id=assumptions.assumption_set_id,
            period=period,
            income_statement=income_statement,
            balance_sheet=balance_sheet,
            cash_flow=cash_flow,
            schedule_values=schedule_values,
            issues=issues,
        )

    def _validate_assumptions(self, assumptions: AssumptionSet) -> None:
        names = {item.name for item in assumptions.assumptions}
        missing = [name for name in REQUIRED_ASSUMPTIONS if name not in names]
        extra = [name for name in names if name not in REQUIRED_ASSUMPTIONS]
        if missing:
            raise ValueError(
                "missing required projection assumptions: " + ", ".join(missing)
            )
        if extra:
            raise ValueError(
                "unknown projection assumptions: " + ", ".join(sorted(extra))
            )
        bounded = (
            "gross_margin",
            "operating_expense_ratio",
            "capex_percent_revenue",
            "depreciation_percent_beginning_ppe",
            "interest_rate",
            "tax_rate",
            "dividend_payout_ratio",
        )
        for name in bounded:
            value = assumptions.value(name)
            if not Decimal("0") <= value <= Decimal("1"):
                raise ValueError(f"{name} must be between 0 and 1")
        if assumptions.value("revenue_growth") <= Decimal("-1"):
            raise ValueError("revenue_growth must be greater than -1")
        for name in (
            "ar_days",
            "inventory_days",
            "ap_days",
            "new_borrowing",
            "debt_repayment",
        ):
            if assumptions.value(name) < 0:
                raise ValueError(f"{name} must be non-negative")

    @staticmethod
    def _require_values(
        values: dict[str, Decimal],
        keys: tuple[str, ...],
        label: str,
    ) -> None:
        missing = [key for key in keys if key not in values]
        if missing:
            raise AccountingValidationError(
                f"{label} missing projection inputs: " + ", ".join(missing)
            )

    def _validate_projection(
        self,
        *,
        income_statement: ProjectedStatement,
        balance_sheet: ProjectedStatement,
        cash_flow: ProjectedStatement,
    ) -> tuple[ProjectionIssue, ...]:
        inc = income_statement.values()
        bs = balance_sheet.values()
        cf = cash_flow.values()
        issues: list[ProjectionIssue] = []

        checks = (
            (
                "PROJECTED_GROSS_PROFIT_DOES_NOT_TIE",
                inc["gross_profit"],
                inc["revenue"] - inc["cost_of_revenue"],
                "projected gross profit does not tie",
            ),
            (
                "PROJECTED_OPERATING_INCOME_DOES_NOT_TIE",
                inc["operating_income"],
                inc["gross_profit"] - inc["operating_expenses"],
                "projected operating income does not tie",
            ),
            (
                "PROJECTED_NET_INCOME_DOES_NOT_TIE",
                inc["net_income"],
                inc["pretax_income"] - inc["income_tax_expense"],
                "projected net income does not tie",
            ),
            (
                "PROJECTED_BALANCE_SHEET_DOES_NOT_BALANCE",
                bs["total_assets"],
                bs["total_liabilities"] + bs["total_equity"],
                "projected balance sheet does not balance",
            ),
            (
                "PROJECTED_CASH_COMPONENTS_DO_NOT_TIE",
                cf["net_change_in_cash"],
                cf["cash_from_operating_activities"]
                + cf["cash_from_investing_activities"]
                + cf["cash_from_financing_activities"],
                "projected cash-flow components do not tie",
            ),
            (
                "PROJECTED_CASH_ROLL_FORWARD_DOES_NOT_TIE",
                cf["ending_cash"],
                cf["beginning_cash"] + cf["net_change_in_cash"],
                "projected cash roll-forward does not tie",
            ),
            (
                "PROJECTED_CASH_CROSS_STATEMENT_MISMATCH",
                bs["cash_and_cash_equivalents"],
                cf["ending_cash"],
                "projected balance-sheet cash and cash-flow ending cash differ",
            ),
        )
        for code, left, right, message in checks:
            difference = left - right
            if abs(difference) > self.tolerance:
                issues.append(
                    ProjectionIssue(
                        code=code,
                        message=message,
                        difference=difference,
                    )
                )
        return tuple(issues)


def make_projection_id(
    *,
    source_statement_ids: tuple[str, ...],
    assumption_set_id: str,
    period: ProjectionPeriod,
    schedule_values: dict[str, Decimal],
) -> str:
    payload = {
        "source_statement_ids": sorted(source_statement_ids),
        "assumption_set_id": assumption_set_id,
        "period": {
            "start_date": period.start_date.isoformat(),
            "end_date": period.end_date.isoformat(),
            "fiscal_year": period.fiscal_year,
            "fiscal_period": period.fiscal_period,
        },
        "schedule_values": {
            key: str(value)
            for key, value in sorted(schedule_values.items())
        },
    }
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "projection:" + hashlib.sha256(material).hexdigest()
