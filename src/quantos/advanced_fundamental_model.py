from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from .advanced_schedules import (
    CashSweepEngine,
    CashSweepPolicy,
    CashSweepResult,
    DebtScheduleEngine,
    DebtScheduleResult,
    DebtTranche,
    ShareScheduleEngine,
    ShareScheduleResult,
    TaxScheduleEngine,
    TaxScheduleResult,
)
from .financial_statements import (
    AccountingValidationError,
    FinancialStatement,
    FinancialStatementValidator,
    StatementType,
)
from .fundamental_schedules import (
    ModelAssumption,
    ProjectedLineItem,
    ProjectedStatement,
    ProjectionPeriod,
    ProjectionStatementType,
)
from .models import EpistemicState


OPERATING_ASSUMPTIONS = (
    "revenue_growth",
    "gross_margin",
    "operating_expense_ratio",
    "capex_percent_revenue",
    "depreciation_percent_beginning_ppe",
    "ar_days",
    "inventory_days",
    "ap_days",
    "dividend_payout_ratio",
)


@dataclass(frozen=True)
class OperatingAssumptionSet:
    assumption_set_id: str
    assumptions: tuple[ModelAssumption, ...]

    def __post_init__(self) -> None:
        names = [item.name for item in self.assumptions]
        if len(names) != len(set(names)):
            raise ValueError("duplicate operating assumption names")
        expected = make_operating_assumption_set_id(self.assumptions)
        if self.assumption_set_id != expected:
            raise ValueError("operating assumption-set identity mismatch")

    def value(self, name: str) -> Decimal:
        for item in self.assumptions:
            if item.name == name:
                return item.value
        raise KeyError(name)


def make_operating_assumption_set_id(
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
        for item in sorted(assumptions, key=lambda item: item.name)
    ]
    material = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "operating-assumptions:" + hashlib.sha256(material).hexdigest()


def build_operating_assumption_set(
    assumptions: tuple[ModelAssumption, ...],
) -> OperatingAssumptionSet:
    return OperatingAssumptionSet(
        assumption_set_id=make_operating_assumption_set_id(assumptions),
        assumptions=assumptions,
    )


@dataclass(frozen=True)
class TaxPeriodInputs:
    opening_nol: Decimal
    statutory_tax_rate: Decimal
    nol_utilization_limit: Decimal
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.opening_nol < 0 or not self.opening_nol.is_finite():
            raise ValueError("opening_nol must be finite and non-negative")
        for name in ("statutory_tax_rate", "nol_utilization_limit"):
            value = getattr(self, name)
            if not value.is_finite() or not Decimal("0") <= value <= Decimal("1"):
                raise ValueError(f"{name} must be between 0 and 1")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("tax inputs require evidence references")


@dataclass(frozen=True)
class SharePeriodInputs:
    beginning_basic_shares: Decimal
    issued_shares: Decimal
    issue_price: Decimal
    repurchased_shares: Decimal
    repurchase_price: Decimal
    options_outstanding: Decimal
    option_strike: Decimal
    average_market_price: Decimal
    restricted_units: Decimal
    evidence_references: tuple[str, ...]


@dataclass(frozen=True)
class AdvancedProjectionPlanPeriod:
    period: ProjectionPeriod
    operating_assumptions: OperatingAssumptionSet
    debt_tranches: tuple[DebtTranche, ...]
    floating_base_rate: Decimal
    tax: TaxPeriodInputs
    cash_sweep_policy: CashSweepPolicy
    shares: SharePeriodInputs


@dataclass(frozen=True)
class AdvancedProjectionRun:
    projection_id: str
    source_statement_ids: tuple[str, ...]
    parent_projection_id: str | None
    period: ProjectionPeriod
    operating_assumption_set_id: str
    income_statement: ProjectedStatement
    balance_sheet: ProjectedStatement
    cash_flow: ProjectedStatement
    debt_schedule: DebtScheduleResult
    tax_schedule: TaxScheduleResult
    cash_sweep: CashSweepResult
    share_schedule: ShareScheduleResult
    schedule_values: dict[str, Decimal]
    ending_debt_by_tranche: dict[str, Decimal]
    issues: tuple[str, ...]

    @property
    def is_valid(self) -> bool:
        return not self.issues

    def require_valid(self) -> None:
        if self.issues:
            raise AccountingValidationError("; ".join(self.issues))


@dataclass(frozen=True)
class AdvancedFundamentalModelRun:
    model_run_id: str
    base_statement_ids: tuple[str, ...]
    projections: tuple[AdvancedProjectionRun, ...]

    def require_valid(self) -> None:
        if not self.projections:
            raise AccountingValidationError("advanced model has no projections")
        for projection in self.projections:
            projection.require_valid()


class AdvancedFundamentalModelEngine:
    DAYS = Decimal("365")

    def __init__(self, *, tolerance: Decimal = Decimal("0.01")) -> None:
        self.tolerance = tolerance
        self.statement_validator = FinancialStatementValidator(tolerance=tolerance)
        self.debt_engine = DebtScheduleEngine()
        self.tax_engine = TaxScheduleEngine()
        self.sweep_engine = CashSweepEngine()
        self.share_engine = ShareScheduleEngine()

    def project(
        self,
        *,
        source_statements: tuple[FinancialStatement, ...],
        plan: tuple[AdvancedProjectionPlanPeriod, ...],
    ) -> AdvancedFundamentalModelRun:
        report = self.statement_validator.validate_three_statement_set(source_statements)
        report.require_valid()
        if not plan:
            raise ValueError("advanced projection plan requires at least one period")
        self._validate_periods(plan)

        by_type = {statement.statement_type: statement for statement in source_statements}
        historical_bs = {
            item.key: item.value
            for item in by_type[StatementType.BALANCE_SHEET].items
        }
        historical_inc = {
            item.key: item.value
            for item in by_type[StatementType.INCOME_STATEMENT].items
        }
        self._require_historical_inputs(historical_bs, historical_inc)

        source_ids = tuple(sorted(statement.statement_id for statement in source_statements))
        projections: list[AdvancedProjectionRun] = []
        prior_bs = historical_bs
        prior_inc = historical_inc
        prior_debt: dict[str, Decimal] | None = None
        prior_nol: Decimal | None = None
        prior_shares: Decimal | None = None

        for index, step in enumerate(plan):
            self._validate_operating_assumptions(step.operating_assumptions)
            debt_openings = {
                item.tranche_id: item.beginning_balance
                for item in step.debt_tranches
            }
            if index == 0:
                if abs(
                    sum(debt_openings.values(), Decimal("0"))
                    - prior_bs["total_debt"]
                ) > self.tolerance:
                    raise AccountingValidationError(
                        "opening debt tranches do not reconcile to historical total_debt"
                    )
            else:
                assert prior_debt is not None
                self._validate_debt_rollforward(prior_debt, debt_openings)
                assert prior_nol is not None
                if abs(step.tax.opening_nol - prior_nol) > self.tolerance:
                    raise AccountingValidationError(
                        "opening NOL does not equal prior ending NOL"
                    )
                assert prior_shares is not None
                if abs(
                    step.shares.beginning_basic_shares - prior_shares
                ) > self.tolerance:
                    raise AccountingValidationError(
                        "opening basic shares do not equal prior ending basic shares"
                    )

            projection = self._project_period(
                source_ids=source_ids,
                parent_projection_id=(
                    projections[-1].projection_id if projections else None
                ),
                opening_bs=prior_bs,
                opening_inc=prior_inc,
                step=step,
            )
            projection.require_valid()
            projections.append(projection)
            prior_bs = projection.balance_sheet.values()
            prior_inc = projection.income_statement.values()
            prior_debt = dict(projection.ending_debt_by_tranche)
            prior_nol = projection.tax_schedule.ending_nol
            prior_shares = projection.share_schedule.ending_basic_shares

        projection_tuple = tuple(projections)
        return AdvancedFundamentalModelRun(
            model_run_id=make_advanced_model_run_id(
                base_statement_ids=source_ids,
                projections=projection_tuple,
            ),
            base_statement_ids=source_ids,
            projections=projection_tuple,
        )

    def _project_period(
        self,
        *,
        source_ids: tuple[str, ...],
        parent_projection_id: str | None,
        opening_bs: dict[str, Decimal],
        opening_inc: dict[str, Decimal],
        step: AdvancedProjectionPlanPeriod,
    ) -> AdvancedProjectionRun:
        a = step.operating_assumptions.value
        revenue = opening_inc["revenue"] * (Decimal("1") + a("revenue_growth"))
        cost_of_revenue = revenue * (Decimal("1") - a("gross_margin"))
        gross_profit = revenue - cost_of_revenue
        operating_expenses = revenue * a("operating_expense_ratio")
        operating_income = gross_profit - operating_expenses

        capex = revenue * a("capex_percent_revenue")
        depreciation = (
            opening_bs["property_plant_equipment"]
            * a("depreciation_percent_beginning_ppe")
        )
        ending_ppe = opening_bs["property_plant_equipment"] + capex - depreciation

        ending_ar = revenue * a("ar_days") / self.DAYS
        ending_inventory = cost_of_revenue * a("inventory_days") / self.DAYS
        ending_ap = cost_of_revenue * a("ap_days") / self.DAYS
        opening_nwc = (
            opening_bs["accounts_receivable"]
            + opening_bs["inventory"]
            - opening_bs["accounts_payable"]
        )
        ending_nwc = ending_ar + ending_inventory - ending_ap
        change_in_nwc = ending_nwc - opening_nwc

        debt = self.debt_engine.project(
            tranches=step.debt_tranches,
            period_start=step.period.start_date,
            period_end=step.period.end_date,
            floating_base_rate=step.floating_base_rate,
        )
        pretax_income = operating_income - debt.total_interest_expense
        tax = self.tax_engine.project(
            pretax_income=pretax_income,
            opening_nol=step.tax.opening_nol,
            statutory_tax_rate=step.tax.statutory_tax_rate,
            nol_utilization_limit=step.tax.nol_utilization_limit,
            evidence_references=step.tax.evidence_references,
        )
        net_income = pretax_income - tax.current_tax_expense
        dividends = max(net_income, Decimal("0")) * a("dividend_payout_ratio")

        shares = self.share_engine.project(
            beginning_basic_shares=step.shares.beginning_basic_shares,
            issued_shares=step.shares.issued_shares,
            issue_price=step.shares.issue_price,
            repurchased_shares=step.shares.repurchased_shares,
            repurchase_price=step.shares.repurchase_price,
            options_outstanding=step.shares.options_outstanding,
            option_strike=step.shares.option_strike,
            average_market_price=step.shares.average_market_price,
            restricted_units=step.shares.restricted_units,
            evidence_references=step.shares.evidence_references,
        )

        ending_retained_earnings = (
            opening_bs["retained_earnings"] + net_income - dividends
        )
        ending_other_equity = (
            opening_bs["other_equity"]
            + shares.net_equity_financing_cash_flow
        )

        cash_from_operating = net_income + depreciation - change_in_nwc
        cash_from_investing = -capex
        cff_before_sweep = (
            debt.total_new_borrowing
            - debt.total_mandatory_repayment
            - dividends
            + shares.net_equity_financing_cash_flow
        )
        pre_sweep_cash = (
            opening_bs["cash_and_cash_equivalents"]
            + cash_from_operating
            + cash_from_investing
            + cff_before_sweep
        )
        sweep = self.sweep_engine.apply(
            pre_sweep_cash=pre_sweep_cash,
            debt_schedule=debt,
            policy=step.cash_sweep_policy,
        )
        ending_debt_by_tranche = {
            item.tranche_id: item.ending_balance_after_sweep
            for item in sweep.allocations
        }
        ending_debt = sum(
            ending_debt_by_tranche.values(),
            Decimal("0"),
        )
        cash_from_financing = cff_before_sweep - sweep.applied_sweep
        net_change_in_cash = (
            cash_from_operating + cash_from_investing + cash_from_financing
        )
        ending_cash = opening_bs["cash_and_cash_equivalents"] + net_change_in_cash

        if abs(ending_cash - sweep.ending_cash) > self.tolerance:
            raise AccountingValidationError(
                "cash-sweep ending cash does not reconcile to statement cash"
            )

        total_assets = (
            ending_cash
            + ending_ar
            + ending_inventory
            + ending_ppe
            + opening_bs["other_assets"]
        )
        total_liabilities = (
            ending_ap + ending_debt + opening_bs["other_liabilities"]
        )
        total_equity = ending_retained_earnings + ending_other_equity

        refs = [
            *source_ids,
            step.operating_assumptions.assumption_set_id,
            debt.schedule_id,
            tax.schedule_id,
            sweep.schedule_id,
            shares.schedule_id,
        ]
        if parent_projection_id is not None:
            refs.append(parent_projection_id)
        derived_refs = tuple(refs)

        def estimate(key: str, value: Decimal, *drivers: str) -> ProjectedLineItem:
            return ProjectedLineItem(
                key=key,
                value=value,
                epistemic_state=EpistemicState.ESTIMATED,
                derived_from=tuple((*derived_refs, *drivers)),
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
                estimate("operating_income", operating_income, "operating_schedule"),
                estimate(
                    "interest_expense",
                    debt.total_interest_expense,
                    debt.schedule_id,
                ),
                estimate("pretax_income", pretax_income, "debt_schedule"),
                estimate(
                    "income_tax_expense",
                    tax.current_tax_expense,
                    tax.schedule_id,
                ),
                estimate("net_income", net_income, "tax_schedule"),
            ),
        )
        balance_sheet = ProjectedStatement(
            statement_type=ProjectionStatementType.BALANCE_SHEET,
            items=(
                estimate("cash_and_cash_equivalents", ending_cash, sweep.schedule_id),
                estimate("accounts_receivable", ending_ar, "ar_days"),
                estimate("inventory", ending_inventory, "inventory_days"),
                estimate(
                    "property_plant_equipment",
                    ending_ppe,
                    "ppe_schedule",
                ),
                estimate("other_assets", opening_bs["other_assets"], "carry_forward"),
                estimate("total_assets", total_assets, "balance_sheet_sum"),
                estimate("accounts_payable", ending_ap, "ap_days"),
                estimate("total_debt", ending_debt, debt.schedule_id, sweep.schedule_id),
                estimate(
                    "other_liabilities",
                    opening_bs["other_liabilities"],
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
                    "retained_earnings_rollforward",
                ),
                estimate("other_equity", ending_other_equity, shares.schedule_id),
                estimate("total_equity", total_equity, "balance_sheet_sum"),
                estimate("ending_nol", tax.ending_nol, tax.schedule_id),
                estimate(
                    "ending_basic_shares",
                    shares.ending_basic_shares,
                    shares.schedule_id,
                ),
                estimate("diluted_shares", shares.diluted_shares, shares.schedule_id),
            ),
        )
        cash_flow = ProjectedStatement(
            statement_type=ProjectionStatementType.CASH_FLOW,
            items=(
                estimate(
                    "beginning_cash",
                    opening_bs["cash_and_cash_equivalents"],
                    "opening_balance_sheet",
                ),
                estimate("net_income", net_income, "income_statement"),
                estimate("depreciation", depreciation, "ppe_schedule"),
                estimate(
                    "change_in_net_working_capital",
                    change_in_nwc,
                    "working_capital_schedule",
                ),
                estimate(
                    "cash_from_operating_activities",
                    cash_from_operating,
                    "operating_cash_flow",
                ),
                estimate("capital_expenditures", capex, "ppe_schedule"),
                estimate(
                    "cash_from_investing_activities",
                    cash_from_investing,
                    "investing_cash_flow",
                ),
                estimate(
                    "new_borrowing",
                    debt.total_new_borrowing,
                    debt.schedule_id,
                ),
                estimate(
                    "mandatory_debt_repayment",
                    debt.total_mandatory_repayment,
                    debt.schedule_id,
                ),
                estimate(
                    "cash_sweep_debt_repayment",
                    sweep.applied_sweep,
                    sweep.schedule_id,
                ),
                estimate("dividends", dividends, "dividend_payout_ratio"),
                estimate(
                    "net_equity_financing_cash_flow",
                    shares.net_equity_financing_cash_flow,
                    shares.schedule_id,
                ),
                estimate(
                    "cash_from_financing_activities",
                    cash_from_financing,
                    "financing_cash_flow",
                ),
                estimate(
                    "net_change_in_cash",
                    net_change_in_cash,
                    "cash_rollforward",
                ),
                estimate("ending_cash", ending_cash, sweep.schedule_id),
            ),
        )

        schedule_values = {
            "revenue": revenue,
            "operating_income": operating_income,
            "interest_expense": debt.total_interest_expense,
            "pretax_income": pretax_income,
            "current_tax_expense": tax.current_tax_expense,
            "net_income": net_income,
            "ending_nol": tax.ending_nol,
            "capex": capex,
            "depreciation": depreciation,
            "change_in_nwc": change_in_nwc,
            "mandatory_debt_repayment": debt.total_mandatory_repayment,
            "cash_sweep_debt_repayment": sweep.applied_sweep,
            "ending_debt": ending_debt,
            "ending_cash": ending_cash,
            "ending_basic_shares": shares.ending_basic_shares,
            "diluted_shares": shares.diluted_shares,
        }
        issues = self._validate_projection(
            income_statement=income_statement,
            balance_sheet=balance_sheet,
            cash_flow=cash_flow,
        )
        projection_id = make_advanced_projection_id(
            source_statement_ids=source_ids,
            parent_projection_id=parent_projection_id,
            period=step.period,
            operating_assumption_set_id=step.operating_assumptions.assumption_set_id,
            debt_schedule_id=debt.schedule_id,
            tax_schedule_id=tax.schedule_id,
            cash_sweep_id=sweep.schedule_id,
            share_schedule_id=shares.schedule_id,
            schedule_values=schedule_values,
        )
        return AdvancedProjectionRun(
            projection_id=projection_id,
            source_statement_ids=source_ids,
            parent_projection_id=parent_projection_id,
            period=step.period,
            operating_assumption_set_id=step.operating_assumptions.assumption_set_id,
            income_statement=income_statement,
            balance_sheet=balance_sheet,
            cash_flow=cash_flow,
            debt_schedule=debt,
            tax_schedule=tax,
            cash_sweep=sweep,
            share_schedule=shares,
            schedule_values=schedule_values,
            ending_debt_by_tranche=ending_debt_by_tranche,
            issues=issues,
        )

    def _validate_operating_assumptions(
        self,
        assumptions: OperatingAssumptionSet,
    ) -> None:
        names = {item.name for item in assumptions.assumptions}
        required = set(OPERATING_ASSUMPTIONS)
        missing = sorted(required - names)
        extra = sorted(names - required)
        if missing:
            raise ValueError(
                "missing operating assumptions: " + ", ".join(missing)
            )
        if extra:
            raise ValueError(
                "unknown operating assumptions: " + ", ".join(extra)
            )
        for name in (
            "gross_margin",
            "operating_expense_ratio",
            "capex_percent_revenue",
            "depreciation_percent_beginning_ppe",
            "dividend_payout_ratio",
        ):
            if not Decimal("0") <= assumptions.value(name) <= Decimal("1"):
                raise ValueError(f"{name} must be between 0 and 1")
        if assumptions.value("revenue_growth") <= Decimal("-1"):
            raise ValueError("revenue_growth must be greater than -1")
        for name in ("ar_days", "inventory_days", "ap_days"):
            if assumptions.value(name) < 0:
                raise ValueError(f"{name} must be non-negative")

    @staticmethod
    def _validate_periods(
        plan: tuple[AdvancedProjectionPlanPeriod, ...],
    ) -> None:
        for index, item in enumerate(plan):
            if index:
                prior = plan[index - 1].period
                if item.period.start_date != prior.end_date + timedelta(days=1):
                    raise ValueError("advanced projection periods must be contiguous")
                if item.period.fiscal_year <= prior.fiscal_year:
                    raise ValueError("advanced projection fiscal years must increase")

    def _require_historical_inputs(
        self,
        bs: dict[str, Decimal],
        inc: dict[str, Decimal],
    ) -> None:
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
        missing = [key for key in required_bs if key not in bs]
        missing += [key for key in required_inc if key not in inc]
        if missing:
            raise AccountingValidationError(
                "historical statements missing advanced-model inputs: "
                + ", ".join(missing)
            )
        assets = (
            bs["cash_and_cash_equivalents"]
            + bs["accounts_receivable"]
            + bs["inventory"]
            + bs["property_plant_equipment"]
            + bs["other_assets"]
        )
        liabilities = (
            bs["accounts_payable"] + bs["total_debt"] + bs["other_liabilities"]
        )
        equity = bs["retained_earnings"] + bs["other_equity"]
        if (
            abs(assets - bs["total_assets"]) > self.tolerance
            or abs(liabilities - bs["total_liabilities"]) > self.tolerance
            or abs(equity - bs["total_equity"]) > self.tolerance
        ):
            raise AccountingValidationError(
                "historical component mapping does not reconcile to reported totals"
            )

    def _validate_debt_rollforward(
        self,
        previous: dict[str, Decimal],
        current: dict[str, Decimal],
    ) -> None:
        for tranche_id, prior_ending in previous.items():
            if prior_ending > self.tolerance and tranche_id not in current:
                raise AccountingValidationError(
                    f"outstanding debt tranche missing from next period: {tranche_id}"
                )
            if tranche_id in current and abs(
                current[tranche_id] - prior_ending
            ) > self.tolerance:
                raise AccountingValidationError(
                    f"opening balance mismatch for debt tranche: {tranche_id}"
                )
        for tranche_id, opening in current.items():
            if tranche_id not in previous and abs(opening) > self.tolerance:
                raise AccountingValidationError(
                    f"new debt tranche must begin at zero before borrowing: {tranche_id}"
                )

    def _validate_projection(
        self,
        *,
        income_statement: ProjectedStatement,
        balance_sheet: ProjectedStatement,
        cash_flow: ProjectedStatement,
    ) -> tuple[str, ...]:
        inc = income_statement.values()
        bs = balance_sheet.values()
        cf = cash_flow.values()
        checks = (
            (
                "gross profit",
                inc["gross_profit"],
                inc["revenue"] - inc["cost_of_revenue"],
            ),
            (
                "operating income",
                inc["operating_income"],
                inc["gross_profit"] - inc["operating_expenses"],
            ),
            (
                "net income",
                inc["net_income"],
                inc["pretax_income"] - inc["income_tax_expense"],
            ),
            (
                "balance sheet",
                bs["total_assets"],
                bs["total_liabilities"] + bs["total_equity"],
            ),
            (
                "cash components",
                cf["net_change_in_cash"],
                cf["cash_from_operating_activities"]
                + cf["cash_from_investing_activities"]
                + cf["cash_from_financing_activities"],
            ),
            (
                "cash roll-forward",
                cf["ending_cash"],
                cf["beginning_cash"] + cf["net_change_in_cash"],
            ),
            (
                "cross-statement cash",
                bs["cash_and_cash_equivalents"],
                cf["ending_cash"],
            ),
        )
        return tuple(
            f"{label} does not tie; difference={left - right}"
            for label, left, right in checks
            if abs(left - right) > self.tolerance
        )


def make_advanced_projection_id(
    *,
    source_statement_ids: tuple[str, ...],
    parent_projection_id: str | None,
    period: ProjectionPeriod,
    operating_assumption_set_id: str,
    debt_schedule_id: str,
    tax_schedule_id: str,
    cash_sweep_id: str,
    share_schedule_id: str,
    schedule_values: dict[str, Decimal],
) -> str:
    payload = {
        "source_statement_ids": sorted(source_statement_ids),
        "parent_projection_id": parent_projection_id,
        "period": {
            "start_date": period.start_date.isoformat(),
            "end_date": period.end_date.isoformat(),
            "fiscal_year": period.fiscal_year,
            "fiscal_period": period.fiscal_period,
        },
        "operating_assumption_set_id": operating_assumption_set_id,
        "debt_schedule_id": debt_schedule_id,
        "tax_schedule_id": tax_schedule_id,
        "cash_sweep_id": cash_sweep_id,
        "share_schedule_id": share_schedule_id,
        "schedule_values": {
            key: str(value) for key, value in sorted(schedule_values.items())
        },
    }
    material = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "advanced-projection:" + hashlib.sha256(material).hexdigest()


def make_advanced_model_run_id(
    *,
    base_statement_ids: tuple[str, ...],
    projections: tuple[AdvancedProjectionRun, ...],
) -> str:
    payload = {
        "base_statement_ids": sorted(base_statement_ids),
        "projection_ids": [item.projection_id for item in projections],
    }
    material = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "advanced-model-run:" + hashlib.sha256(material).hexdigest()
