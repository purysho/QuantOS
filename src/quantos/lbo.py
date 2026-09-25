from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Protocol

from .advanced_schedules import InterestRateType
from .fundamental_schedules import ProjectionPeriod
from .valuation_methodology import (
    MethodPermit,
    ValuationMethod,
    ValuationMethodologyAssessment,
    ValuationMethodologyGate,
)


class LBOModelRun(Protocol):
    model_run_id: str
    projections: tuple[object, ...]

    def require_valid(self) -> None: ...


class LBOStatus(str, Enum):
    SOLVED = "SOLVED"
    NEGATIVE_EXIT_EQUITY = "NEGATIVE_EXIT_EQUITY"


@dataclass(frozen=True)
class LBOTransactionAssumptions:
    as_of: datetime
    entry_enterprise_value: Decimal
    target_cash: Decimal
    target_debt: Decimal
    target_non_operating_investments: Decimal
    transaction_fees: Decimal
    financing_fees: Decimal
    cash_used_at_close: Decimal
    minimum_cash: Decimal
    opening_nol: Decimal
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ValueError("LBO transaction as_of must be timezone-aware")
        for name in (
            "entry_enterprise_value",
            "target_cash",
            "target_debt",
            "target_non_operating_investments",
            "transaction_fees",
            "financing_fees",
            "cash_used_at_close",
            "minimum_cash",
            "opening_nol",
        ):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.entry_enterprise_value <= 0:
            raise ValueError("entry enterprise value must be positive")
        available_close_cash = max(
            self.target_cash - self.minimum_cash,
            Decimal("0"),
        )
        if self.cash_used_at_close > available_close_cash:
            raise ValueError(
                "cash used at close would breach the minimum-cash requirement"
            )
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("LBO transaction assumptions require evidence")


@dataclass(frozen=True)
class LBODebtTerms:
    tranche_id: str
    initial_draw: Decimal
    rate_type: InterestRateType
    annual_amortization_rate: Decimal
    maturity_period: int
    fixed_rate: Decimal | None
    spread: Decimal | None
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.tranche_id.strip():
            raise ValueError("LBO debt tranche_id is required")
        if not self.initial_draw.is_finite() or self.initial_draw < 0:
            raise ValueError("initial_draw must be finite and non-negative")
        if (
            not self.annual_amortization_rate.is_finite()
            or self.annual_amortization_rate < 0
            or self.annual_amortization_rate > 1
        ):
            raise ValueError("annual_amortization_rate must be between 0 and 1")
        if self.maturity_period < 1:
            raise ValueError("maturity_period must be >= 1")
        if self.rate_type is InterestRateType.FIXED:
            if self.fixed_rate is None or self.spread is not None:
                raise ValueError("fixed tranche requires fixed_rate only")
            self._rate(self.fixed_rate, "fixed_rate")
        else:
            if self.spread is None or self.fixed_rate is not None:
                raise ValueError("floating tranche requires spread only")
            self._rate(self.spread, "spread")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("LBO debt terms require evidence references")

    @staticmethod
    def _rate(value: Decimal, name: str) -> None:
        if not value.is_finite() or value < 0 or value > 1:
            raise ValueError(f"{name} must be between 0 and 1")


@dataclass(frozen=True)
class LBOPeriodAssumption:
    projection_id: str
    floating_base_rate: Decimal
    cash_tax_rate: Decimal
    nol_utilization_limit: Decimal
    sweep_percent: Decimal
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.projection_id.strip():
            raise ValueError("LBO period projection_id is required")
        for name in (
            "floating_base_rate",
            "cash_tax_rate",
            "nol_utilization_limit",
            "sweep_percent",
        ):
            value = getattr(self, name)
            if (
                not value.is_finite()
                or value < 0
                or value > 1
            ):
                raise ValueError(f"{name} must be between 0 and 1")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("LBO period assumption requires evidence")


@dataclass(frozen=True)
class LBOExitAssumptions:
    exit_multiple: Decimal
    non_operating_investments: Decimal
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.exit_multiple.is_finite() or self.exit_multiple <= 0:
            raise ValueError("exit multiple must be finite and positive")
        if (
            not self.non_operating_investments.is_finite()
            or self.non_operating_investments < 0
        ):
            raise ValueError(
                "exit non-operating investments must be non-negative"
            )
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("LBO exit assumptions require evidence")


@dataclass(frozen=True)
class LBOSourcesAndUses:
    equity_purchase_price: Decimal
    debt_refinancing: Decimal
    transaction_fees: Decimal
    financing_fees: Decimal
    total_uses: Decimal
    sponsor_debt: Decimal
    target_cash_used: Decimal
    sponsor_equity: Decimal
    total_sources: Decimal


@dataclass(frozen=True)
class LBODebtPeriod:
    tranche_id: str
    beginning_balance: Decimal
    mandatory_repayment: Decimal
    cash_sweep_repayment: Decimal
    ending_balance: Decimal
    effective_rate: Decimal
    interest_expense: Decimal


@dataclass(frozen=True)
class LBOOperatingPeriod:
    projection_id: str
    fiscal_year: int
    ebitda: Decimal
    operating_income: Decimal
    depreciation: Decimal
    capex: Decimal
    change_in_nwc: Decimal
    interest_expense: Decimal
    pretax_income: Decimal
    cash_tax: Decimal
    nol_utilized: Decimal
    ending_nol: Decimal
    cash_available_before_debt_repayment: Decimal
    mandatory_debt_repayment: Decimal
    cash_sweep_repayment: Decimal
    ending_cash: Decimal
    ending_debt: Decimal
    debt_periods: tuple[LBODebtPeriod, ...]


@dataclass(frozen=True)
class LBOResult:
    valuation_id: str
    model_run_id: str
    method_permit_id: str
    status: LBOStatus
    sources_and_uses: LBOSourcesAndUses
    periods: tuple[LBOOperatingPeriod, ...]
    exit_ebitda: Decimal
    exit_multiple: Decimal
    exit_enterprise_value: Decimal
    exit_net_debt: Decimal
    sponsor_exit_equity_value: Decimal
    sponsor_moic: Decimal | None
    sponsor_irr: Decimal | None


class LBOEngine:
    """Sponsor-return LBO analysis pinned to a validated operating model run."""

    def value(
        self,
        *,
        model_run: LBOModelRun,
        methodology_assessment: ValuationMethodologyAssessment,
        method_permit: MethodPermit,
        transaction: LBOTransactionAssumptions,
        debt_terms: tuple[LBODebtTerms, ...],
        period_assumptions: tuple[LBOPeriodAssumption, ...],
        sweep_priority: tuple[str, ...],
        exit_assumptions: LBOExitAssumptions,
    ) -> LBOResult:
        ValuationMethodologyGate.validate(
            assessment=methodology_assessment,
            permit=method_permit,
            required_method=ValuationMethod.LBO,
        )
        model_run.require_valid()
        projections = tuple(model_run.projections)
        if not projections:
            raise ValueError("LBO requires at least one projected period")
        if not debt_terms:
            raise ValueError("LBO requires at least one sponsor debt tranche")
        if len({item.tranche_id for item in debt_terms}) != len(debt_terms):
            raise ValueError("LBO debt tranche IDs must be unique")
        if set(sweep_priority) != {item.tranche_id for item in debt_terms}:
            raise ValueError(
                "LBO sweep priority must contain every debt tranche exactly once"
            )
        projection_ids = tuple(
            str(getattr(item, "projection_id")) for item in projections
        )
        assumptions_by_id = {
            item.projection_id: item for item in period_assumptions
        }
        if len(assumptions_by_id) != len(period_assumptions):
            raise ValueError("duplicate LBO period assumptions")
        if set(assumptions_by_id) != set(projection_ids):
            raise ValueError(
                "LBO period assumptions must exactly cover model projections"
            )

        sources_uses = self._sources_and_uses(
            transaction=transaction,
            debt_terms=debt_terms,
        )
        debt_state = {
            item.tranche_id: item.initial_draw for item in debt_terms
        }
        opening_cash = transaction.minimum_cash
        opening_nol = transaction.opening_nol
        results: list[LBOOperatingPeriod] = []

        for period_index, projection in enumerate(projections, start=1):
            projection_id = str(getattr(projection, "projection_id"))
            period = getattr(projection, "period")
            if not isinstance(period, ProjectionPeriod):
                raise ValueError("LBO projection period has unexpected type")
            income = getattr(projection, "income_statement").values()
            schedule = dict(getattr(projection, "schedule_values"))
            if "operating_income" not in income:
                raise ValueError("LBO projection missing operating_income")
            for key in ("depreciation", "capex", "change_in_nwc"):
                if key not in schedule:
                    raise ValueError(f"LBO projection missing schedule: {key}")

            assumption = assumptions_by_id[projection_id]
            operating_income = Decimal(income["operating_income"])
            depreciation = Decimal(schedule["depreciation"])
            capex = Decimal(schedule["capex"])
            change_in_nwc = Decimal(schedule["change_in_nwc"])
            ebitda = operating_income + depreciation

            tranche_pre_sweep: dict[str, tuple[Decimal, Decimal, Decimal, Decimal]] = {}
            total_interest = Decimal("0")
            total_mandatory = Decimal("0")
            terms_by_id = {item.tranche_id: item for item in debt_terms}
            for terms in debt_terms:
                beginning = debt_state[terms.tranche_id]
                if period_index >= terms.maturity_period:
                    mandatory = beginning
                else:
                    scheduled = (
                        terms.initial_draw * terms.annual_amortization_rate
                    )
                    mandatory = min(beginning, scheduled)
                pre_sweep = beginning - mandatory
                rate = (
                    terms.fixed_rate
                    if terms.rate_type is InterestRateType.FIXED
                    else assumption.floating_base_rate + terms.spread
                )
                assert rate is not None
                if rate > 1:
                    raise ValueError("effective LBO interest rate exceeds 100%")
                average_balance = (
                    beginning + pre_sweep
                ) / Decimal("2")
                interest = average_balance * rate
                tranche_pre_sweep[terms.tranche_id] = (
                    beginning,
                    mandatory,
                    pre_sweep,
                    interest,
                )
                total_interest += interest
                total_mandatory += mandatory

            pretax_income = operating_income - total_interest
            if pretax_income <= 0:
                nol_generated = -pretax_income
                nol_utilized = Decimal("0")
                taxable_income = Decimal("0")
            else:
                max_use = (
                    pretax_income * assumption.nol_utilization_limit
                )
                nol_utilized = min(opening_nol, max_use)
                taxable_income = pretax_income - nol_utilized
                nol_generated = Decimal("0")
            cash_tax = taxable_income * assumption.cash_tax_rate
            ending_nol = opening_nol - nol_utilized + nol_generated

            cash_available = (
                operating_income
                - cash_tax
                + depreciation
                - capex
                - change_in_nwc
                - total_interest
            )
            pre_sweep_cash = (
                opening_cash + cash_available - total_mandatory
            )
            if pre_sweep_cash < transaction.minimum_cash:
                raise ValueError(
                    "LBO liquidity shortfall: mandatory debt service breaches minimum cash"
                )
            requested_sweep = (
                pre_sweep_cash - transaction.minimum_cash
            ) * assumption.sweep_percent

            remaining_sweep = requested_sweep
            debt_periods: list[LBODebtPeriod] = []
            ending_state: dict[str, Decimal] = {}
            sweep_by_id: dict[str, Decimal] = {}
            for tranche_id in sweep_priority:
                beginning, mandatory, pre_sweep, interest = (
                    tranche_pre_sweep[tranche_id]
                )
                sweep = min(pre_sweep, remaining_sweep)
                ending = pre_sweep - sweep
                remaining_sweep -= sweep
                sweep_by_id[tranche_id] = sweep
                ending_state[tranche_id] = ending
                terms = terms_by_id[tranche_id]
                effective_rate = (
                    terms.fixed_rate
                    if terms.rate_type is InterestRateType.FIXED
                    else assumption.floating_base_rate + terms.spread
                )
                assert effective_rate is not None
                debt_periods.append(
                    LBODebtPeriod(
                        tranche_id=tranche_id,
                        beginning_balance=beginning,
                        mandatory_repayment=mandatory,
                        cash_sweep_repayment=sweep,
                        ending_balance=ending,
                        effective_rate=effective_rate,
                        interest_expense=interest,
                    )
                )

            total_sweep = sum(sweep_by_id.values(), Decimal("0"))
            ending_cash = pre_sweep_cash - total_sweep
            if ending_cash < transaction.minimum_cash:
                raise ValueError("LBO cash sweep breached minimum cash")
            ending_debt = sum(ending_state.values(), Decimal("0"))

            results.append(
                LBOOperatingPeriod(
                    projection_id=projection_id,
                    fiscal_year=period.fiscal_year,
                    ebitda=ebitda,
                    operating_income=operating_income,
                    depreciation=depreciation,
                    capex=capex,
                    change_in_nwc=change_in_nwc,
                    interest_expense=total_interest,
                    pretax_income=pretax_income,
                    cash_tax=cash_tax,
                    nol_utilized=nol_utilized,
                    ending_nol=ending_nol,
                    cash_available_before_debt_repayment=cash_available,
                    mandatory_debt_repayment=total_mandatory,
                    cash_sweep_repayment=total_sweep,
                    ending_cash=ending_cash,
                    ending_debt=ending_debt,
                    debt_periods=tuple(debt_periods),
                )
            )
            debt_state = ending_state
            opening_cash = ending_cash
            opening_nol = ending_nol

        periods = tuple(results)
        last = periods[-1]
        exit_ev = last.ebitda * exit_assumptions.exit_multiple
        exit_net_debt = last.ending_debt - last.ending_cash
        exit_equity = (
            exit_ev
            - last.ending_debt
            + last.ending_cash
            + exit_assumptions.non_operating_investments
        )

        if exit_equity <= 0:
            status = LBOStatus.NEGATIVE_EXIT_EQUITY
            moic = None
            irr = None
        else:
            status = LBOStatus.SOLVED
            moic = exit_equity / sources_uses.sponsor_equity
            years = Decimal(len(periods))
            irr = Decimal(str(float(moic) ** (1.0 / float(years)) - 1.0))

        payload = {
            "model_run_id": model_run.model_run_id,
            "method_permit_id": method_permit.permit_id,
            "transaction": {
                "as_of": transaction.as_of.isoformat(),
                "entry_enterprise_value": str(
                    transaction.entry_enterprise_value
                ),
                "target_cash": str(transaction.target_cash),
                "target_debt": str(transaction.target_debt),
                "target_non_operating_investments": str(
                    transaction.target_non_operating_investments
                ),
                "transaction_fees": str(transaction.transaction_fees),
                "financing_fees": str(transaction.financing_fees),
                "cash_used_at_close": str(transaction.cash_used_at_close),
                "minimum_cash": str(transaction.minimum_cash),
                "opening_nol": str(transaction.opening_nol),
                "evidence_references": list(
                    transaction.evidence_references
                ),
            },
            "debt_terms": [
                {
                    "tranche_id": item.tranche_id,
                    "initial_draw": str(item.initial_draw),
                    "rate_type": item.rate_type.value,
                    "annual_amortization_rate": str(
                        item.annual_amortization_rate
                    ),
                    "maturity_period": item.maturity_period,
                    "fixed_rate": (
                        str(item.fixed_rate)
                        if item.fixed_rate is not None
                        else None
                    ),
                    "spread": (
                        str(item.spread)
                        if item.spread is not None
                        else None
                    ),
                    "evidence_references": list(
                        item.evidence_references
                    ),
                }
                for item in debt_terms
            ],
            "period_assumptions": [
                {
                    "projection_id": item.projection_id,
                    "floating_base_rate": str(item.floating_base_rate),
                    "cash_tax_rate": str(item.cash_tax_rate),
                    "nol_utilization_limit": str(
                        item.nol_utilization_limit
                    ),
                    "sweep_percent": str(item.sweep_percent),
                    "evidence_references": list(
                        item.evidence_references
                    ),
                }
                for item in period_assumptions
            ],
            "sweep_priority": list(sweep_priority),
            "exit": {
                "exit_multiple": str(exit_assumptions.exit_multiple),
                "non_operating_investments": str(
                    exit_assumptions.non_operating_investments
                ),
                "evidence_references": list(
                    exit_assumptions.evidence_references
                ),
            },
            "sources_and_uses": {
                "equity_purchase_price": str(
                    sources_uses.equity_purchase_price
                ),
                "debt_refinancing": str(
                    sources_uses.debt_refinancing
                ),
                "total_uses": str(sources_uses.total_uses),
                "sponsor_debt": str(sources_uses.sponsor_debt),
                "target_cash_used": str(
                    sources_uses.target_cash_used
                ),
                "sponsor_equity": str(sources_uses.sponsor_equity),
            },
            "periods": [
                {
                    "projection_id": item.projection_id,
                    "ending_cash": str(item.ending_cash),
                    "ending_debt": str(item.ending_debt),
                    "ending_nol": str(item.ending_nol),
                    "cash_sweep_repayment": str(
                        item.cash_sweep_repayment
                    ),
                }
                for item in periods
            ],
            "status": status.value,
            "exit_enterprise_value": str(exit_ev),
            "exit_equity": str(exit_equity),
            "moic": str(moic) if moic is not None else None,
            "irr": str(irr) if irr is not None else None,
        }
        return LBOResult(
            valuation_id=_content_id("lbo", payload),
            model_run_id=model_run.model_run_id,
            method_permit_id=method_permit.permit_id,
            status=status,
            sources_and_uses=sources_uses,
            periods=periods,
            exit_ebitda=last.ebitda,
            exit_multiple=exit_assumptions.exit_multiple,
            exit_enterprise_value=exit_ev,
            exit_net_debt=exit_net_debt,
            sponsor_exit_equity_value=exit_equity,
            sponsor_moic=moic,
            sponsor_irr=irr,
        )

    @staticmethod
    def _sources_and_uses(
        *,
        transaction: LBOTransactionAssumptions,
        debt_terms: tuple[LBODebtTerms, ...],
    ) -> LBOSourcesAndUses:
        equity_purchase = (
            transaction.entry_enterprise_value
            - transaction.target_debt
            + transaction.target_cash
            + transaction.target_non_operating_investments
        )
        if equity_purchase < 0:
            raise ValueError("entry assumptions imply negative equity purchase price")
        uses = (
            equity_purchase
            + transaction.target_debt
            + transaction.transaction_fees
            + transaction.financing_fees
        )
        sponsor_debt = sum(
            (item.initial_draw for item in debt_terms),
            Decimal("0"),
        )
        sponsor_equity = (
            uses - sponsor_debt - transaction.cash_used_at_close
        )
        if sponsor_equity <= 0:
            raise ValueError(
                "LBO sources imply non-positive sponsor equity contribution"
            )
        sources = (
            sponsor_debt
            + transaction.cash_used_at_close
            + sponsor_equity
        )
        if sources != uses:
            raise ValueError("LBO sources and uses do not balance")
        return LBOSourcesAndUses(
            equity_purchase_price=equity_purchase,
            debt_refinancing=transaction.target_debt,
            transaction_fees=transaction.transaction_fees,
            financing_fees=transaction.financing_fees,
            total_uses=uses,
            sponsor_debt=sponsor_debt,
            target_cash_used=transaction.cash_used_at_close,
            sponsor_equity=sponsor_equity,
            total_sources=sources,
        )


@dataclass(frozen=True)
class LBOCaseResult:
    case_name: str
    result: LBOResult


@dataclass(frozen=True)
class LBOCaseComparison:
    comparison_id: str
    cases: tuple[LBOCaseResult, ...]


class LBOComparisonEngine:
    """Keeps base/downside cases separate instead of averaging sponsor returns."""

    @staticmethod
    def compare(
        cases: tuple[LBOCaseResult, ...],
    ) -> LBOCaseComparison:
        if len(cases) < 2:
            raise ValueError("LBO comparison requires at least two cases")
        names = [item.case_name for item in cases]
        if any(not name.strip() for name in names):
            raise ValueError("every LBO case requires a name")
        if len(names) != len(set(names)):
            raise ValueError("LBO case names must be unique")
        payload = {
            "cases": [
                {
                    "case_name": item.case_name,
                    "valuation_id": item.result.valuation_id,
                    "model_run_id": item.result.model_run_id,
                    "status": item.result.status.value,
                    "moic": (
                        str(item.result.sponsor_moic)
                        if item.result.sponsor_moic is not None
                        else None
                    ),
                    "irr": (
                        str(item.result.sponsor_irr)
                        if item.result.sponsor_irr is not None
                        else None
                    ),
                }
                for item in cases
            ]
        }
        return LBOCaseComparison(
            comparison_id=_content_id("lbo-case-comparison", payload),
            cases=cases,
        )


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
