from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum


class InterestRateType(str, Enum):
    FIXED = "FIXED"
    FLOATING = "FLOATING"


@dataclass(frozen=True)
class DebtTranche:
    tranche_id: str
    beginning_balance: Decimal
    rate_type: InterestRateType
    maturity_date: date
    scheduled_repayment: Decimal
    new_borrowing: Decimal = Decimal("0")
    fixed_rate: Decimal | None = None
    spread: Decimal | None = None
    evidence_references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.tranche_id.strip():
            raise ValueError("tranche_id is required")
        for name in ("beginning_balance", "scheduled_repayment", "new_borrowing"):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if not self.evidence_references or not all(
            reference.strip() for reference in self.evidence_references
        ):
            raise ValueError("debt tranche requires evidence references")
        if self.rate_type is InterestRateType.FIXED:
            if self.fixed_rate is None:
                raise ValueError("fixed-rate tranche requires fixed_rate")
            if self.spread is not None:
                raise ValueError("fixed-rate tranche must not set spread")
            self._validate_rate(self.fixed_rate, "fixed_rate")
        else:
            if self.spread is None:
                raise ValueError("floating-rate tranche requires spread")
            if self.fixed_rate is not None:
                raise ValueError("floating-rate tranche must not set fixed_rate")
            self._validate_rate(self.spread, "spread")

    @staticmethod
    def _validate_rate(value: Decimal, name: str) -> None:
        if not value.is_finite() or value < 0 or value > 1:
            raise ValueError(f"{name} must be between 0 and 1")


@dataclass(frozen=True)
class DebtTrancheResult:
    tranche_id: str
    effective_rate: Decimal
    beginning_balance: Decimal
    new_borrowing: Decimal
    mandatory_repayment: Decimal
    ending_balance_before_sweep: Decimal
    interest_expense: Decimal
    matured_in_period: bool


@dataclass(frozen=True)
class DebtScheduleResult:
    schedule_id: str
    period_start: date
    period_end: date
    tranches: tuple[DebtTrancheResult, ...]
    total_beginning_debt: Decimal
    total_new_borrowing: Decimal
    total_mandatory_repayment: Decimal
    total_ending_debt_before_sweep: Decimal
    total_interest_expense: Decimal


class DebtScheduleEngine:
    def project(
        self,
        *,
        tranches: tuple[DebtTranche, ...],
        period_start: date,
        period_end: date,
        floating_base_rate: Decimal,
    ) -> DebtScheduleResult:
        if period_start > period_end:
            raise ValueError("debt schedule period start cannot follow end")
        if not tranches:
            raise ValueError("debt schedule requires at least one tranche")
        if len({item.tranche_id for item in tranches}) != len(tranches):
            raise ValueError("duplicate debt tranche IDs are not allowed")
        if (
            not floating_base_rate.is_finite()
            or floating_base_rate < 0
            or floating_base_rate > 1
        ):
            raise ValueError("floating_base_rate must be between 0 and 1")

        results: list[DebtTrancheResult] = []
        for tranche in tranches:
            available = tranche.beginning_balance + tranche.new_borrowing
            matured = tranche.maturity_date <= period_end
            mandatory = (
                available
                if matured
                else min(tranche.scheduled_repayment, available)
            )
            ending = available - mandatory
            rate = (
                tranche.fixed_rate
                if tranche.rate_type is InterestRateType.FIXED
                else floating_base_rate + tranche.spread
            )
            assert rate is not None
            if rate > 1:
                raise ValueError("effective interest rate cannot exceed 1")
            average_balance = (tranche.beginning_balance + ending) / Decimal("2")
            interest = average_balance * rate
            results.append(
                DebtTrancheResult(
                    tranche_id=tranche.tranche_id,
                    effective_rate=rate,
                    beginning_balance=tranche.beginning_balance,
                    new_borrowing=tranche.new_borrowing,
                    mandatory_repayment=mandatory,
                    ending_balance_before_sweep=ending,
                    interest_expense=interest,
                    matured_in_period=matured,
                )
            )

        result_tuple = tuple(results)
        payload = {
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "floating_base_rate": str(floating_base_rate),
            "tranches": [
                {
                    "tranche_id": item.tranche_id,
                    "effective_rate": str(item.effective_rate),
                    "beginning_balance": str(item.beginning_balance),
                    "new_borrowing": str(item.new_borrowing),
                    "mandatory_repayment": str(item.mandatory_repayment),
                    "ending_balance_before_sweep": str(
                        item.ending_balance_before_sweep
                    ),
                    "interest_expense": str(item.interest_expense),
                    "matured_in_period": item.matured_in_period,
                }
                for item in result_tuple
            ],
        }
        return DebtScheduleResult(
            schedule_id=_content_id("debt-schedule", payload),
            period_start=period_start,
            period_end=period_end,
            tranches=result_tuple,
            total_beginning_debt=sum(
                (item.beginning_balance for item in result_tuple), Decimal("0")
            ),
            total_new_borrowing=sum(
                (item.new_borrowing for item in result_tuple), Decimal("0")
            ),
            total_mandatory_repayment=sum(
                (item.mandatory_repayment for item in result_tuple), Decimal("0")
            ),
            total_ending_debt_before_sweep=sum(
                (item.ending_balance_before_sweep for item in result_tuple),
                Decimal("0"),
            ),
            total_interest_expense=sum(
                (item.interest_expense for item in result_tuple), Decimal("0")
            ),
        )


@dataclass(frozen=True)
class TaxScheduleResult:
    schedule_id: str
    pretax_income: Decimal
    opening_nol: Decimal
    nol_generated: Decimal
    nol_utilized: Decimal
    ending_nol: Decimal
    taxable_income: Decimal
    current_tax_expense: Decimal


class TaxScheduleEngine:
    def project(
        self,
        *,
        pretax_income: Decimal,
        opening_nol: Decimal,
        statutory_tax_rate: Decimal,
        nol_utilization_limit: Decimal,
        evidence_references: tuple[str, ...],
    ) -> TaxScheduleResult:
        for name, value in (
            ("pretax_income", pretax_income),
            ("opening_nol", opening_nol),
            ("statutory_tax_rate", statutory_tax_rate),
            ("nol_utilization_limit", nol_utilization_limit),
        ):
            if not value.is_finite():
                raise ValueError(f"{name} must be finite")
        if opening_nol < 0:
            raise ValueError("opening_nol must be non-negative")
        if not Decimal("0") <= statutory_tax_rate <= Decimal("1"):
            raise ValueError("statutory_tax_rate must be between 0 and 1")
        if not Decimal("0") <= nol_utilization_limit <= Decimal("1"):
            raise ValueError("nol_utilization_limit must be between 0 and 1")
        if not evidence_references or not all(
            reference.strip() for reference in evidence_references
        ):
            raise ValueError("tax schedule requires evidence references")

        if pretax_income <= 0:
            taxable_before_nol = Decimal("0")
            nol_generated = -pretax_income
            nol_utilized = Decimal("0")
        else:
            taxable_before_nol = pretax_income
            nol_generated = Decimal("0")
            max_usable = taxable_before_nol * nol_utilization_limit
            nol_utilized = min(opening_nol, max_usable)

        taxable_income = taxable_before_nol - nol_utilized
        ending_nol = opening_nol - nol_utilized + nol_generated
        current_tax = taxable_income * statutory_tax_rate
        payload = {
            "pretax_income": str(pretax_income),
            "opening_nol": str(opening_nol),
            "statutory_tax_rate": str(statutory_tax_rate),
            "nol_utilization_limit": str(nol_utilization_limit),
            "evidence_references": list(evidence_references),
            "nol_generated": str(nol_generated),
            "nol_utilized": str(nol_utilized),
            "ending_nol": str(ending_nol),
            "taxable_income": str(taxable_income),
            "current_tax_expense": str(current_tax),
        }
        return TaxScheduleResult(
            schedule_id=_content_id("tax-schedule", payload),
            pretax_income=pretax_income,
            opening_nol=opening_nol,
            nol_generated=nol_generated,
            nol_utilized=nol_utilized,
            ending_nol=ending_nol,
            taxable_income=taxable_income,
            current_tax_expense=current_tax,
        )


@dataclass(frozen=True)
class CashSweepPolicy:
    minimum_cash: Decimal
    sweep_percent: Decimal
    tranche_priority: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.minimum_cash.is_finite() or self.minimum_cash < 0:
            raise ValueError("minimum_cash must be finite and non-negative")
        if (
            not self.sweep_percent.is_finite()
            or not Decimal("0") <= self.sweep_percent <= Decimal("1")
        ):
            raise ValueError("sweep_percent must be between 0 and 1")
        if len(self.tranche_priority) != len(set(self.tranche_priority)):
            raise ValueError("cash-sweep priority cannot repeat tranche IDs")


@dataclass(frozen=True)
class SweepAllocation:
    tranche_id: str
    sweep_repayment: Decimal
    ending_balance_after_sweep: Decimal


@dataclass(frozen=True)
class CashSweepResult:
    schedule_id: str
    pre_sweep_cash: Decimal
    minimum_cash: Decimal
    available_excess_cash: Decimal
    requested_sweep: Decimal
    applied_sweep: Decimal
    ending_cash: Decimal
    allocations: tuple[SweepAllocation, ...]


class CashSweepEngine:
    def apply(
        self,
        *,
        pre_sweep_cash: Decimal,
        debt_schedule: DebtScheduleResult,
        policy: CashSweepPolicy,
    ) -> CashSweepResult:
        if not pre_sweep_cash.is_finite():
            raise ValueError("pre_sweep_cash must be finite")
        known = {item.tranche_id for item in debt_schedule.tranches}
        unknown = [item for item in policy.tranche_priority if item not in known]
        if unknown:
            raise ValueError(
                "cash-sweep priority references unknown tranches: "
                + ", ".join(unknown)
            )
        remaining_by_id = {
            item.tranche_id: item.ending_balance_before_sweep
            for item in debt_schedule.tranches
        }
        excess = max(pre_sweep_cash - policy.minimum_cash, Decimal("0"))
        requested = excess * policy.sweep_percent
        capacity = sum(remaining_by_id.values(), Decimal("0"))
        remaining_sweep = min(requested, capacity)
        applied = Decimal("0")
        allocations: list[SweepAllocation] = []

        priority = policy.tranche_priority + tuple(
            item.tranche_id
            for item in debt_schedule.tranches
            if item.tranche_id not in policy.tranche_priority
        )
        for tranche_id in priority:
            balance = remaining_by_id[tranche_id]
            payment = min(balance, remaining_sweep)
            ending = balance - payment
            allocations.append(
                SweepAllocation(
                    tranche_id=tranche_id,
                    sweep_repayment=payment,
                    ending_balance_after_sweep=ending,
                )
            )
            applied += payment
            remaining_sweep -= payment

        ending_cash = pre_sweep_cash - applied
        if ending_cash < policy.minimum_cash:
            raise ValueError("cash sweep breached minimum-cash policy")
        payload = {
            "pre_sweep_cash": str(pre_sweep_cash),
            "debt_schedule_id": debt_schedule.schedule_id,
            "minimum_cash": str(policy.minimum_cash),
            "sweep_percent": str(policy.sweep_percent),
            "tranche_priority": list(policy.tranche_priority),
            "applied_sweep": str(applied),
            "ending_cash": str(ending_cash),
            "allocations": [
                {
                    "tranche_id": item.tranche_id,
                    "sweep_repayment": str(item.sweep_repayment),
                    "ending_balance_after_sweep": str(
                        item.ending_balance_after_sweep
                    ),
                }
                for item in allocations
            ],
        }
        return CashSweepResult(
            schedule_id=_content_id("cash-sweep", payload),
            pre_sweep_cash=pre_sweep_cash,
            minimum_cash=policy.minimum_cash,
            available_excess_cash=excess,
            requested_sweep=requested,
            applied_sweep=applied,
            ending_cash=ending_cash,
            allocations=tuple(allocations),
        )


@dataclass(frozen=True)
class ShareScheduleResult:
    schedule_id: str
    beginning_basic_shares: Decimal
    issued_shares: Decimal
    repurchased_shares: Decimal
    ending_basic_shares: Decimal
    incremental_option_shares: Decimal
    restricted_units: Decimal
    diluted_shares: Decimal
    issuance_cash_inflow: Decimal
    repurchase_cash_outflow: Decimal
    net_equity_financing_cash_flow: Decimal


class ShareScheduleEngine:
    def project(
        self,
        *,
        beginning_basic_shares: Decimal,
        issued_shares: Decimal,
        issue_price: Decimal,
        repurchased_shares: Decimal,
        repurchase_price: Decimal,
        options_outstanding: Decimal,
        option_strike: Decimal,
        average_market_price: Decimal,
        restricted_units: Decimal,
        evidence_references: tuple[str, ...],
    ) -> ShareScheduleResult:
        values = {
            "beginning_basic_shares": beginning_basic_shares,
            "issued_shares": issued_shares,
            "issue_price": issue_price,
            "repurchased_shares": repurchased_shares,
            "repurchase_price": repurchase_price,
            "options_outstanding": options_outstanding,
            "option_strike": option_strike,
            "average_market_price": average_market_price,
            "restricted_units": restricted_units,
        }
        for name, value in values.items():
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if average_market_price <= 0:
            raise ValueError("average_market_price must be positive")
        if repurchased_shares > beginning_basic_shares + issued_shares:
            raise ValueError("share repurchase exceeds shares available")
        if not evidence_references or not all(
            reference.strip() for reference in evidence_references
        ):
            raise ValueError("share schedule requires evidence references")

        ending_basic = beginning_basic_shares + issued_shares - repurchased_shares
        if average_market_price > option_strike:
            incremental_options = options_outstanding * (
                Decimal("1") - option_strike / average_market_price
            )
        else:
            incremental_options = Decimal("0")
        diluted = ending_basic + incremental_options + restricted_units
        issuance_cash = issued_shares * issue_price
        repurchase_cash = repurchased_shares * repurchase_price
        net_financing = issuance_cash - repurchase_cash
        payload = {
            **{key: str(value) for key, value in values.items()},
            "evidence_references": list(evidence_references),
            "ending_basic_shares": str(ending_basic),
            "incremental_option_shares": str(incremental_options),
            "diluted_shares": str(diluted),
            "issuance_cash_inflow": str(issuance_cash),
            "repurchase_cash_outflow": str(repurchase_cash),
            "net_equity_financing_cash_flow": str(net_financing),
        }
        return ShareScheduleResult(
            schedule_id=_content_id("share-schedule", payload),
            beginning_basic_shares=beginning_basic_shares,
            issued_shares=issued_shares,
            repurchased_shares=repurchased_shares,
            ending_basic_shares=ending_basic,
            incremental_option_shares=incremental_options,
            restricted_units=restricted_units,
            diluted_shares=diluted,
            issuance_cash_inflow=issuance_cash,
            repurchase_cash_outflow=repurchase_cash,
            net_equity_financing_cash_flow=net_financing,
        )


def _content_id(prefix: str, payload: dict[str, object]) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
