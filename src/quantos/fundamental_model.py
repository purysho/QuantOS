from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import duckdb

from .financial_statements import FinancialStatement
from .fundamental_schedules import (
    AssumptionSet,
    ProjectedLineItem,
    ProjectedStatement,
    ProjectionPeriod,
    ProjectionRun,
    ProjectionStatementType,
    ThreeStatementProjectionEngine,
    make_projection_id,
)
from .models import EpistemicState


@dataclass(frozen=True)
class ProjectionPlanPeriod:
    period: ProjectionPeriod
    assumptions: AssumptionSet


@dataclass(frozen=True)
class MultiPeriodModelRun:
    model_run_id: str
    base_statement_ids: tuple[str, ...]
    projections: tuple[ProjectionRun, ...]

    def __post_init__(self) -> None:
        if not self.projections:
            raise ValueError("multi-period model requires at least one projection")
        expected = make_model_run_id(
            base_statement_ids=self.base_statement_ids,
            projections=self.projections,
        )
        if self.model_run_id != expected:
            raise ValueError("model_run_id does not match model contents")

    def require_valid(self) -> None:
        for projection in self.projections:
            projection.require_valid()


@dataclass(frozen=True)
class MetricDelta:
    fiscal_year: int
    metric: str
    left: Decimal
    right: Decimal
    delta: Decimal


@dataclass(frozen=True)
class ModelRunComparison:
    left_run_id: str
    right_run_id: str
    deltas: tuple[MetricDelta, ...]


def make_model_run_id(
    *,
    base_statement_ids: tuple[str, ...],
    projections: tuple[ProjectionRun, ...],
) -> str:
    payload = {
        "base_statement_ids": sorted(base_statement_ids),
        "projection_ids": [projection.projection_id for projection in projections],
    }
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "model-run:" + hashlib.sha256(material).hexdigest()


class MultiPeriodProjectionEngine:
    """Linked multi-period model with explicit projection-to-projection lineage."""

    def __init__(self, *, tolerance: Decimal = Decimal("0.01")) -> None:
        self.single = ThreeStatementProjectionEngine(tolerance=tolerance)
        self.tolerance = tolerance

    def project(
        self,
        *,
        source_statements: tuple[FinancialStatement, ...],
        plan: tuple[ProjectionPlanPeriod, ...],
    ) -> MultiPeriodModelRun:
        if not plan:
            raise ValueError("projection plan must contain at least one period")
        self._validate_plan(plan)

        first = self.single.project(
            source_statements=source_statements,
            assumptions=plan[0].assumptions,
            period=plan[0].period,
        )
        first.require_valid()
        projections = [first]

        for item in plan[1:]:
            previous = projections[-1]
            projection = self._roll_forward(
                previous=previous,
                assumptions=item.assumptions,
                period=item.period,
            )
            projection.require_valid()
            projections.append(projection)

        base_ids = tuple(
            sorted(statement.statement_id for statement in source_statements)
        )
        final = tuple(projections)
        return MultiPeriodModelRun(
            model_run_id=make_model_run_id(
                base_statement_ids=base_ids,
                projections=final,
            ),
            base_statement_ids=base_ids,
            projections=final,
        )

    @staticmethod
    def _validate_plan(plan: tuple[ProjectionPlanPeriod, ...]) -> None:
        seen_years: set[int] = set()
        for index, item in enumerate(plan):
            if item.period.fiscal_year in seen_years:
                raise ValueError("projection plan repeats a fiscal year")
            seen_years.add(item.period.fiscal_year)
            if index:
                prior = plan[index - 1].period
                expected_start = prior.end_date + timedelta(days=1)
                if item.period.start_date != expected_start:
                    raise ValueError(
                        "projection periods must be contiguous for roll-forward modeling"
                    )
                if item.period.fiscal_year <= prior.fiscal_year:
                    raise ValueError("projection fiscal years must increase")

    def _roll_forward(
        self,
        *,
        previous: ProjectionRun,
        assumptions: AssumptionSet,
        period: ProjectionPeriod,
    ) -> ProjectionRun:
        previous.require_valid()
        self.single.validate_assumptions(assumptions)
        expected_start = previous.period.end_date + timedelta(days=1)
        if period.start_date != expected_start:
            raise ValueError(
                "roll-forward period must start immediately after prior projection"
            )

        inc = previous.income_statement.values()
        bs = previous.balance_sheet.values()
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

        ending_ar = revenue * a("ar_days") / self.single.DAYS
        ending_inventory = cost_of_revenue * a("inventory_days") / self.single.DAYS
        ending_ap = cost_of_revenue * a("ap_days") / self.single.DAYS
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

        source_ids = previous.source_statement_ids
        parent_ref = previous.projection_id
        assumption_ref = assumptions.assumption_set_id

        def estimate(key: str, value: Decimal, *drivers: str) -> ProjectedLineItem:
            return ProjectedLineItem(
                key=key,
                value=value,
                epistemic_state=EpistemicState.ESTIMATED,
                derived_from=tuple(
                    (*source_ids, parent_ref, assumption_ref, *drivers)
                ),
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
                    "prior_projection",
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
        issues = self.single.validate_projection(
            income_statement=income_statement,
            balance_sheet=balance_sheet,
            cash_flow=cash_flow,
        )
        projection_id = make_projection_id(
            source_statement_ids=source_ids,
            assumption_set_id=assumptions.assumption_set_id,
            period=period,
            schedule_values=schedule_values,
            parent_projection_id=previous.projection_id,
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
            parent_projection_id=previous.projection_id,
        )

    @staticmethod
    def compare(
        left: MultiPeriodModelRun,
        right: MultiPeriodModelRun,
    ) -> ModelRunComparison:
        if left.base_statement_ids != right.base_statement_ids:
            raise ValueError("model comparison requires the same historical base")
        if len(left.projections) != len(right.projections):
            raise ValueError("model comparison requires the same projection horizon")
        metrics = (
            ("revenue", "income"),
            ("operating_income", "income"),
            ("net_income", "income"),
            ("cash_and_cash_equivalents", "balance"),
            ("total_debt", "balance"),
            ("total_equity", "balance"),
        )
        deltas: list[MetricDelta] = []
        for left_period, right_period in zip(
            left.projections,
            right.projections,
            strict=True,
        ):
            if left_period.period != right_period.period:
                raise ValueError(
                    "model comparison requires matching projection periods"
                )
            left_income = left_period.income_statement.values()
            right_income = right_period.income_statement.values()
            left_balance = left_period.balance_sheet.values()
            right_balance = right_period.balance_sheet.values()
            for metric, source in metrics:
                if source == "income":
                    left_value = left_income[metric]
                    right_value = right_income[metric]
                else:
                    left_value = left_balance[metric]
                    right_value = right_balance[metric]
                deltas.append(
                    MetricDelta(
                        fiscal_year=left_period.period.fiscal_year,
                        metric=metric,
                        left=left_value,
                        right=right_value,
                        delta=right_value - left_value,
                    )
                )
        return ModelRunComparison(
            left_run_id=left.model_run_id,
            right_run_id=right.model_run_id,
            deltas=tuple(deltas),
        )


class ModelRunStore:
    """Immutable manifest store for reproducible multi-period model runs."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS fundamental_model_runs (
                model_run_id VARCHAR PRIMARY KEY,
                base_statement_ids_json VARCHAR NOT NULL,
                projection_ids_json VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, run: MultiPeriodModelRun) -> bool:
        run.require_valid()
        payload = json.dumps(
            self._manifest(run),
            sort_keys=True,
            separators=(",", ":"),
        )
        existing = self._con.execute(
            "SELECT payload_json FROM fundamental_model_runs WHERE model_run_id = ?",
            [run.model_run_id],
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != payload:
                raise ValueError("model-run identity conflict")
            return False
        self._con.execute(
            """
            INSERT INTO fundamental_model_runs
            VALUES (?, ?, ?, ?)
            """,
            [
                run.model_run_id,
                json.dumps(run.base_statement_ids),
                json.dumps(
                    [projection.projection_id for projection in run.projections]
                ),
                payload,
            ],
        )
        return True

    def get_manifest(self, model_run_id: str) -> dict[str, object] | None:
        row = self._con.execute(
            "SELECT payload_json FROM fundamental_model_runs WHERE model_run_id = ?",
            [model_run_id],
        ).fetchone()
        if row is None:
            return None
        return json.loads(str(row[0]))

    @staticmethod
    def _manifest(run: MultiPeriodModelRun) -> dict[str, object]:
        return {
            "model_run_id": run.model_run_id,
            "base_statement_ids": list(run.base_statement_ids),
            "projections": [
                {
                    "projection_id": projection.projection_id,
                    "parent_projection_id": projection.parent_projection_id,
                    "assumption_set_id": projection.assumption_set_id,
                    "period": {
                        "start_date": projection.period.start_date.isoformat(),
                        "end_date": projection.period.end_date.isoformat(),
                        "fiscal_year": projection.period.fiscal_year,
                        "fiscal_period": projection.period.fiscal_period,
                    },
                    "income_statement": {
                        key: str(value)
                        for key, value in projection.income_statement.values().items()
                    },
                    "balance_sheet": {
                        key: str(value)
                        for key, value in projection.balance_sheet.values().items()
                    },
                    "cash_flow": {
                        key: str(value)
                        for key, value in projection.cash_flow.values().items()
                    },
                    "schedule_values": {
                        key: str(value)
                        for key, value in sorted(projection.schedule_values.items())
                    },
                }
                for projection in run.projections
            ],
        }

    def close(self) -> None:
        self._con.close()
