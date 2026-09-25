from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Protocol

from .fundamental_schedules import ProjectionPeriod
from .valuation_methodology import (
    MethodPermit,
    ValuationMethod,
    ValuationMethodologyAssessment,
    ValuationMethodologyGate,
)


class ValuationModelRun(Protocol):
    model_run_id: str
    projections: tuple[object, ...]

    def require_valid(self) -> None: ...


@dataclass(frozen=True)
class ValuationAssumption:
    name: str
    value: Decimal
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("valuation assumption name is required")
        if not self.value.is_finite():
            raise ValueError("valuation assumption value must be finite")
        if not self.rationale.strip():
            raise ValueError("valuation assumption rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("valuation assumption requires evidence references")


@dataclass(frozen=True)
class PeriodTaxAssumption:
    projection_id: str
    tax_rate: Decimal
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.projection_id.strip():
            raise ValueError("projection_id is required")
        if (
            not self.tax_rate.is_finite()
            or not Decimal("0") <= self.tax_rate <= Decimal("1")
        ):
            raise ValueError("unlevered tax rate must be between 0 and 1")
        if not self.rationale.strip():
            raise ValueError("tax-assumption rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("tax assumption requires evidence references")


@dataclass(frozen=True)
class DiscountPoint:
    projection_id: str
    years: int

    def __post_init__(self) -> None:
        if not self.projection_id.strip():
            raise ValueError("projection_id is required")
        if self.years < 1:
            raise ValueError("discount years must be >= 1")


@dataclass(frozen=True)
class EquityBridge:
    cash: Decimal
    non_operating_investments: Decimal
    debt: Decimal
    preferred_equity: Decimal
    noncontrolling_interest: Decimal
    diluted_shares: Decimal
    as_of: datetime
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "cash",
            "non_operating_investments",
            "debt",
            "preferred_equity",
            "noncontrolling_interest",
            "diluted_shares",
        ):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.diluted_shares <= 0:
            raise ValueError("diluted_shares must be positive")
        if self.as_of.tzinfo is None:
            raise ValueError("equity-bridge as_of must be timezone-aware")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("equity bridge requires evidence references")


@dataclass(frozen=True)
class DCFInputs:
    wacc: ValuationAssumption
    terminal_growth: ValuationAssumption
    taxes: tuple[PeriodTaxAssumption, ...]
    discount_points: tuple[DiscountPoint, ...]
    equity_bridge: EquityBridge

    def __post_init__(self) -> None:
        if self.wacc.name != "wacc":
            raise ValueError("wacc assumption must be named 'wacc'")
        if self.terminal_growth.name != "terminal_growth":
            raise ValueError(
                "terminal-growth assumption must be named 'terminal_growth'"
            )
        if not Decimal("0") < self.wacc.value < Decimal("1"):
            raise ValueError("WACC must be between 0 and 1")
        if self.terminal_growth.value <= Decimal("-1"):
            raise ValueError("terminal growth must be greater than -1")
        if self.terminal_growth.value >= self.wacc.value:
            raise ValueError("terminal growth must be below WACC")
        tax_ids = [item.projection_id for item in self.taxes]
        timing_ids = [item.projection_id for item in self.discount_points]
        if len(tax_ids) != len(set(tax_ids)):
            raise ValueError("duplicate period tax assumptions")
        if len(timing_ids) != len(set(timing_ids)):
            raise ValueError("duplicate discount points")


@dataclass(frozen=True)
class FCFFPeriod:
    projection_id: str
    fiscal_year: int
    operating_income: Decimal
    unlevered_tax_rate: Decimal
    nopat: Decimal
    depreciation: Decimal
    capex: Decimal
    change_in_nwc: Decimal
    fcff: Decimal
    discount_years: int
    discount_factor: Decimal
    present_value: Decimal


@dataclass(frozen=True)
class DCFResult:
    valuation_id: str
    model_run_id: str
    config_id: str
    method_permit_id: str
    periods: tuple[FCFFPeriod, ...]
    terminal_value: Decimal
    present_value_terminal: Decimal
    present_value_explicit_fcff: Decimal
    enterprise_value: Decimal
    equity_value: Decimal
    value_per_diluted_share: Decimal
    terminal_value_share_of_enterprise_value: Decimal


class ReverseDCFStatus(str, Enum):
    SOLVED = "SOLVED"
    MARKET_EV_BELOW_EXPLICIT_PV = "MARKET_EV_BELOW_EXPLICIT_PV"
    OUTSIDE_PERPETUITY_DOMAIN = "OUTSIDE_PERPETUITY_DOMAIN"


@dataclass(frozen=True)
class MarketPriceReference:
    price_per_share: Decimal
    diluted_shares: Decimal
    as_of: datetime
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.price_per_share.is_finite() or self.price_per_share < 0:
            raise ValueError("market price must be finite and non-negative")
        if not self.diluted_shares.is_finite() or self.diluted_shares <= 0:
            raise ValueError("market diluted shares must be positive")
        if self.as_of.tzinfo is None:
            raise ValueError("market reference as_of must be timezone-aware")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("market reference requires evidence references")


@dataclass(frozen=True)
class ReverseDCFResult:
    reverse_valuation_id: str
    model_run_id: str
    config_id: str
    method_permit_id: str
    status: ReverseDCFStatus
    market_equity_value: Decimal
    market_enterprise_value: Decimal
    present_value_explicit_fcff: Decimal
    required_terminal_value_at_horizon: Decimal | None
    implied_terminal_growth: Decimal | None


class DCFEngine:
    """FCFF DCF pinned to an exact validated fundamental model run."""

    def value(
        self,
        *,
        model_run: ValuationModelRun,
        inputs: DCFInputs,
        methodology_assessment: ValuationMethodologyAssessment,
        method_permit: MethodPermit,
    ) -> DCFResult:
        ValuationMethodologyGate.validate(
            assessment=methodology_assessment,
            permit=method_permit,
            required_method=ValuationMethod.FCFF_DCF,
        )
        model_run.require_valid()
        projections = tuple(model_run.projections)
        if not projections:
            raise ValueError("DCF requires at least one projected period")
        projection_ids = tuple(
            str(getattr(item, "projection_id")) for item in projections
        )
        self._require_exact_projection_coverage(
            projection_ids=projection_ids,
            inputs=inputs,
        )

        tax_by_id = {item.projection_id: item for item in inputs.taxes}
        timing_by_id = {
            item.projection_id: item for item in inputs.discount_points
        }
        wacc = inputs.wacc.value

        periods: list[FCFFPeriod] = []
        for projection in projections:
            projection_id = str(getattr(projection, "projection_id"))
            period = getattr(projection, "period")
            if not isinstance(period, ProjectionPeriod):
                raise ValueError("projection period has unexpected type")
            income = getattr(projection, "income_statement").values()
            schedule = dict(getattr(projection, "schedule_values"))
            for key in ("operating_income",):
                if key not in income:
                    raise ValueError(f"projection missing valuation input: {key}")
            for key in ("depreciation", "capex", "change_in_nwc"):
                if key not in schedule:
                    raise ValueError(f"projection missing valuation schedule: {key}")

            tax = tax_by_id[projection_id]
            timing = timing_by_id[projection_id]
            operating_income = Decimal(income["operating_income"])
            depreciation = Decimal(schedule["depreciation"])
            capex = Decimal(schedule["capex"])
            change_in_nwc = Decimal(schedule["change_in_nwc"])
            nopat = operating_income * (Decimal("1") - tax.tax_rate)
            fcff = nopat + depreciation - capex - change_in_nwc
            discount_factor = (Decimal("1") + wacc) ** timing.years
            pv = fcff / discount_factor
            periods.append(
                FCFFPeriod(
                    projection_id=projection_id,
                    fiscal_year=period.fiscal_year,
                    operating_income=operating_income,
                    unlevered_tax_rate=tax.tax_rate,
                    nopat=nopat,
                    depreciation=depreciation,
                    capex=capex,
                    change_in_nwc=change_in_nwc,
                    fcff=fcff,
                    discount_years=timing.years,
                    discount_factor=discount_factor,
                    present_value=pv,
                )
            )

        ordered = tuple(
            sorted(periods, key=lambda item: (item.discount_years, item.projection_id))
        )
        if [item.discount_years for item in ordered] != sorted(
            item.discount_years for item in ordered
        ):
            raise ValueError("discount points are not monotonic")
        if len({item.discount_years for item in ordered}) != len(ordered):
            raise ValueError("discount years must be unique across projections")

        last = ordered[-1]
        g = inputs.terminal_growth.value
        terminal_value = last.fcff * (Decimal("1") + g) / (wacc - g)
        terminal_discount_factor = (Decimal("1") + wacc) ** last.discount_years
        pv_terminal = terminal_value / terminal_discount_factor
        pv_explicit = sum(
            (item.present_value for item in ordered),
            Decimal("0"),
        )
        enterprise_value = pv_explicit + pv_terminal
        bridge = inputs.equity_bridge
        equity_value = (
            enterprise_value
            + bridge.cash
            + bridge.non_operating_investments
            - bridge.debt
            - bridge.preferred_equity
            - bridge.noncontrolling_interest
        )
        per_share = equity_value / bridge.diluted_shares
        terminal_share = (
            pv_terminal / enterprise_value
            if enterprise_value != 0
            else Decimal("0")
        )
        config_id = make_dcf_config_id(inputs)
        valuation_id = make_valuation_id(
            model_run_id=model_run.model_run_id,
            config_id=config_id,
            method_permit_id=method_permit.permit_id,
            enterprise_value=enterprise_value,
            equity_value=equity_value,
            per_share=per_share,
        )
        return DCFResult(
            valuation_id=valuation_id,
            model_run_id=model_run.model_run_id,
            config_id=config_id,
            method_permit_id=method_permit.permit_id,
            periods=ordered,
            terminal_value=terminal_value,
            present_value_terminal=pv_terminal,
            present_value_explicit_fcff=pv_explicit,
            enterprise_value=enterprise_value,
            equity_value=equity_value,
            value_per_diluted_share=per_share,
            terminal_value_share_of_enterprise_value=terminal_share,
        )

    def reverse_terminal_growth(
        self,
        *,
        model_run: ValuationModelRun,
        inputs: DCFInputs,
        market: MarketPriceReference,
        methodology_assessment: ValuationMethodologyAssessment,
        method_permit: MethodPermit,
    ) -> ReverseDCFResult:
        forward = self.value(
            model_run=model_run,
            inputs=inputs,
            methodology_assessment=methodology_assessment,
            method_permit=method_permit,
        )
        bridge = inputs.equity_bridge
        market_equity = market.price_per_share * market.diluted_shares
        market_ev = (
            market_equity
            - bridge.cash
            - bridge.non_operating_investments
            + bridge.debt
            + bridge.preferred_equity
            + bridge.noncontrolling_interest
        )
        last = forward.periods[-1]
        required_pv_terminal = (
            market_ev - forward.present_value_explicit_fcff
        )
        reverse_id_payload = {
            "model_run_id": model_run.model_run_id,
            "config_id": forward.config_id,
            "method_permit_id": method_permit.permit_id,
            "market_price": str(market.price_per_share),
            "market_shares": str(market.diluted_shares),
            "market_as_of": market.as_of.isoformat(),
            "market_evidence": list(market.evidence_references),
        }

        if required_pv_terminal <= 0:
            return ReverseDCFResult(
                reverse_valuation_id=_content_id(
                    "reverse-dcf", reverse_id_payload
                ),
                model_run_id=model_run.model_run_id,
                config_id=forward.config_id,
                method_permit_id=method_permit.permit_id,
                status=ReverseDCFStatus.MARKET_EV_BELOW_EXPLICIT_PV,
                market_equity_value=market_equity,
                market_enterprise_value=market_ev,
                present_value_explicit_fcff=forward.present_value_explicit_fcff,
                required_terminal_value_at_horizon=None,
                implied_terminal_growth=None,
            )

        wacc = inputs.wacc.value
        terminal_discount_factor = (
            Decimal("1") + wacc
        ) ** last.discount_years
        required_terminal = required_pv_terminal * terminal_discount_factor
        denominator = required_terminal + last.fcff
        if denominator == 0:
            implied = None
        else:
            implied = (
                required_terminal * wacc - last.fcff
            ) / denominator

        status = ReverseDCFStatus.SOLVED
        if implied is None or implied <= Decimal("-1") or implied >= wacc:
            status = ReverseDCFStatus.OUTSIDE_PERPETUITY_DOMAIN
            implied = None

        reverse_id_payload["required_terminal_value"] = str(required_terminal)
        reverse_id_payload["implied_terminal_growth"] = (
            str(implied) if implied is not None else None
        )
        reverse_id_payload["status"] = status.value
        return ReverseDCFResult(
            reverse_valuation_id=_content_id(
                "reverse-dcf", reverse_id_payload
            ),
            model_run_id=model_run.model_run_id,
            config_id=forward.config_id,
            method_permit_id=method_permit.permit_id,
            status=status,
            market_equity_value=market_equity,
            market_enterprise_value=market_ev,
            present_value_explicit_fcff=forward.present_value_explicit_fcff,
            required_terminal_value_at_horizon=required_terminal,
            implied_terminal_growth=implied,
        )

    @staticmethod
    def _require_exact_projection_coverage(
        *,
        projection_ids: tuple[str, ...],
        inputs: DCFInputs,
    ) -> None:
        expected = set(projection_ids)
        tax_ids = {item.projection_id for item in inputs.taxes}
        timing_ids = {item.projection_id for item in inputs.discount_points}
        if tax_ids != expected:
            missing = sorted(expected - tax_ids)
            extra = sorted(tax_ids - expected)
            raise ValueError(
                f"DCF tax coverage mismatch; missing={missing}, extra={extra}"
            )
        if timing_ids != expected:
            missing = sorted(expected - timing_ids)
            extra = sorted(timing_ids - expected)
            raise ValueError(
                f"DCF discount coverage mismatch; missing={missing}, extra={extra}"
            )


def make_dcf_config_id(inputs: DCFInputs) -> str:
    payload = {
        "wacc": _assumption_payload(inputs.wacc),
        "terminal_growth": _assumption_payload(inputs.terminal_growth),
        "taxes": [
            {
                "projection_id": item.projection_id,
                "tax_rate": str(item.tax_rate),
                "rationale": item.rationale,
                "evidence_references": list(item.evidence_references),
            }
            for item in sorted(inputs.taxes, key=lambda item: item.projection_id)
        ],
        "discount_points": [
            {
                "projection_id": item.projection_id,
                "years": item.years,
            }
            for item in sorted(
                inputs.discount_points,
                key=lambda item: item.projection_id,
            )
        ],
        "equity_bridge": {
            "cash": str(inputs.equity_bridge.cash),
            "non_operating_investments": str(
                inputs.equity_bridge.non_operating_investments
            ),
            "debt": str(inputs.equity_bridge.debt),
            "preferred_equity": str(inputs.equity_bridge.preferred_equity),
            "noncontrolling_interest": str(
                inputs.equity_bridge.noncontrolling_interest
            ),
            "diluted_shares": str(inputs.equity_bridge.diluted_shares),
            "as_of": inputs.equity_bridge.as_of.isoformat(),
            "evidence_references": list(
                inputs.equity_bridge.evidence_references
            ),
        },
    }
    return _content_id("dcf-config", payload)


def make_valuation_id(
    *,
    model_run_id: str,
    config_id: str,
    method_permit_id: str,
    enterprise_value: Decimal,
    equity_value: Decimal,
    per_share: Decimal,
) -> str:
    return _content_id(
        "dcf",
        {
            "model_run_id": model_run_id,
            "config_id": config_id,
            "method_permit_id": method_permit_id,
            "enterprise_value": str(enterprise_value),
            "equity_value": str(equity_value),
            "per_share": str(per_share),
        },
    )


def _assumption_payload(item: ValuationAssumption) -> dict[str, object]:
    return {
        "name": item.name,
        "value": str(item.value),
        "rationale": item.rationale,
        "evidence_references": list(item.evidence_references),
    }


def _content_id(prefix: str, payload: dict[str, object]) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()