from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from .valuation_methodology import (
    MethodPermit,
    ValuationMethod,
    ValuationMethodologyAssessment,
    ValuationMethodologyGate,
)


class SegmentValueBasis(str, Enum):
    ENTERPRISE_VALUE = "ENTERPRISE_VALUE"
    EQUITY_VALUE = "EQUITY_VALUE"


@dataclass(frozen=True)
class SegmentBridge:
    cash: Decimal
    non_operating_investments: Decimal
    debt: Decimal
    preferred_equity: Decimal
    noncontrolling_interest: Decimal
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "cash",
            "non_operating_investments",
            "debt",
            "preferred_equity",
            "noncontrolling_interest",
        ):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(f"segment bridge {name} must be finite and non-negative")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("segment bridge requires evidence references")


@dataclass(frozen=True)
class SegmentValuation:
    segment_id: str
    segment_name: str
    as_of: datetime
    method: ValuationMethod
    methodology_assessment: ValuationMethodologyAssessment
    method_permit: MethodPermit
    valuation_reference_id: str
    basis: SegmentValueBasis
    value: Decimal
    ownership: Decimal
    bridge: SegmentBridge | None
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.segment_id.strip() or not self.segment_name.strip():
            raise ValueError("segment identity and name are required")
        if self.as_of.tzinfo is None:
            raise ValueError("segment as_of must be timezone-aware")
        if self.method is ValuationMethod.SOTP:
            raise ValueError("nested SOTP segment valuation is not supported")
        if not self.valuation_reference_id.strip():
            raise ValueError("segment valuation reference is required")
        if not self.value.is_finite():
            raise ValueError("segment valuation value must be finite")
        if (
            not self.ownership.is_finite()
            or self.ownership <= 0
            or self.ownership > 1
        ):
            raise ValueError("segment ownership must be in (0, 1]")
        if self.basis is SegmentValueBasis.ENTERPRISE_VALUE:
            if self.bridge is None:
                raise ValueError("enterprise-value segment requires a segment bridge")
        elif self.bridge is not None:
            raise ValueError("equity-value segment must not include an enterprise bridge")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("segment valuation requires evidence references")


@dataclass(frozen=True)
class CorporateItems:
    as_of: datetime
    cash: Decimal
    non_operating_investments: Decimal
    debt: Decimal
    preferred_equity: Decimal
    noncontrolling_interest: Decimal
    other_assets: Decimal
    other_liabilities: Decimal
    corporate_cost_value: Decimal
    intercompany_elimination: Decimal
    diluted_shares: Decimal
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ValueError("corporate items as_of must be timezone-aware")
        for name in (
            "cash",
            "non_operating_investments",
            "debt",
            "preferred_equity",
            "noncontrolling_interest",
            "other_assets",
            "other_liabilities",
            "corporate_cost_value",
            "intercompany_elimination",
            "diluted_shares",
        ):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(f"corporate {name} must be finite and non-negative")
        if self.diluted_shares <= 0:
            raise ValueError("corporate diluted_shares must be positive")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("corporate items require evidence references")


@dataclass(frozen=True)
class GroupReconciliationReference:
    as_of: datetime
    reported_cash: Decimal
    reported_debt: Decimal
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ValueError("group reconciliation as_of must be timezone-aware")
        for name in ("reported_cash", "reported_debt"):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("group reconciliation requires evidence references")


@dataclass(frozen=True)
class SegmentContribution:
    segment_id: str
    segment_name: str
    method: ValuationMethod
    method_permit_id: str
    valuation_reference_id: str
    basis: SegmentValueBasis
    raw_value: Decimal
    equity_value_before_ownership: Decimal
    ownership: Decimal
    attributable_equity_value: Decimal


@dataclass(frozen=True)
class SOTPResult:
    valuation_id: str
    company_method_permit_id: str
    as_of: datetime
    contributions: tuple[SegmentContribution, ...]
    gross_attributable_segment_equity: Decimal
    corporate_net_adjustment: Decimal
    equity_value: Decimal
    diluted_shares: Decimal
    value_per_diluted_share: Decimal


class SOTPEngine:
    """Method-by-segment SOTP with explicit group cash/debt reconciliation."""

    def __init__(self, *, tolerance: Decimal = Decimal("0.01")) -> None:
        if tolerance < 0:
            raise ValueError("tolerance must be non-negative")
        self.tolerance = tolerance

    def value(
        self,
        *,
        company_assessment: ValuationMethodologyAssessment,
        company_method_permit: MethodPermit,
        segments: tuple[SegmentValuation, ...],
        corporate: CorporateItems,
        reconciliation: GroupReconciliationReference,
    ) -> SOTPResult:
        ValuationMethodologyGate.validate(
            assessment=company_assessment,
            permit=company_method_permit,
            required_method=ValuationMethod.SOTP,
        )
        if not segments:
            raise ValueError("SOTP requires at least one segment")
        if len({item.segment_id for item in segments}) != len(segments):
            raise ValueError("SOTP segment IDs must be unique")
        if reconciliation.as_of != corporate.as_of:
            raise ValueError("corporate and reconciliation as_of must match")
        if any(item.as_of != corporate.as_of for item in segments):
            raise ValueError("every segment valuation must use the same as_of")

        contributions: list[SegmentContribution] = []
        segment_cash = Decimal("0")
        segment_debt = Decimal("0")
        for segment in sorted(segments, key=lambda item: item.segment_id):
            ValuationMethodologyGate.validate(
                assessment=segment.methodology_assessment,
                permit=segment.method_permit,
                required_method=segment.method,
            )
            if segment.basis is SegmentValueBasis.ENTERPRISE_VALUE:
                assert segment.bridge is not None
                bridge = segment.bridge
                equity_before_ownership = (
                    segment.value
                    + bridge.cash
                    + bridge.non_operating_investments
                    - bridge.debt
                    - bridge.preferred_equity
                    - bridge.noncontrolling_interest
                )
                segment_cash += bridge.cash
                segment_debt += bridge.debt
            else:
                equity_before_ownership = segment.value

            attributable = equity_before_ownership * segment.ownership
            contributions.append(
                SegmentContribution(
                    segment_id=segment.segment_id,
                    segment_name=segment.segment_name,
                    method=segment.method,
                    method_permit_id=segment.method_permit.permit_id,
                    valuation_reference_id=segment.valuation_reference_id,
                    basis=segment.basis,
                    raw_value=segment.value,
                    equity_value_before_ownership=equity_before_ownership,
                    ownership=segment.ownership,
                    attributable_equity_value=attributable,
                )
            )

        allocated_cash = segment_cash + corporate.cash
        allocated_debt = segment_debt + corporate.debt
        if abs(allocated_cash - reconciliation.reported_cash) > self.tolerance:
            raise ValueError("segment plus corporate cash does not reconcile to group cash")
        if abs(allocated_debt - reconciliation.reported_debt) > self.tolerance:
            raise ValueError("segment plus corporate debt does not reconcile to group debt")

        gross = sum(
            (item.attributable_equity_value for item in contributions),
            Decimal("0"),
        )
        corporate_net = (
            corporate.cash
            + corporate.non_operating_investments
            + corporate.other_assets
            - corporate.debt
            - corporate.preferred_equity
            - corporate.noncontrolling_interest
            - corporate.other_liabilities
            - corporate.corporate_cost_value
            - corporate.intercompany_elimination
        )
        equity = gross + corporate_net
        per_share = equity / corporate.diluted_shares

        payload = {
            "company_method_permit_id": company_method_permit.permit_id,
            "as_of": corporate.as_of.isoformat(),
            "contributions": [
                {
                    "segment_id": item.segment_id,
                    "method": item.method.value,
                    "method_permit_id": item.method_permit_id,
                    "valuation_reference_id": item.valuation_reference_id,
                    "basis": item.basis.value,
                    "raw_value": str(item.raw_value),
                    "equity_value_before_ownership": str(
                        item.equity_value_before_ownership
                    ),
                    "ownership": str(item.ownership),
                    "attributable_equity_value": str(
                        item.attributable_equity_value
                    ),
                }
                for item in contributions
            ],
            "corporate": {
                "cash": str(corporate.cash),
                "non_operating_investments": str(
                    corporate.non_operating_investments
                ),
                "debt": str(corporate.debt),
                "preferred_equity": str(corporate.preferred_equity),
                "noncontrolling_interest": str(
                    corporate.noncontrolling_interest
                ),
                "other_assets": str(corporate.other_assets),
                "other_liabilities": str(corporate.other_liabilities),
                "corporate_cost_value": str(corporate.corporate_cost_value),
                "intercompany_elimination": str(
                    corporate.intercompany_elimination
                ),
                "diluted_shares": str(corporate.diluted_shares),
                "evidence_references": list(corporate.evidence_references),
            },
            "reconciliation": {
                "reported_cash": str(reconciliation.reported_cash),
                "reported_debt": str(reconciliation.reported_debt),
                "evidence_references": list(
                    reconciliation.evidence_references
                ),
            },
            "gross_attributable_segment_equity": str(gross),
            "corporate_net_adjustment": str(corporate_net),
            "equity_value": str(equity),
            "value_per_diluted_share": str(per_share),
        }
        return SOTPResult(
            valuation_id=_content_id("sotp", payload),
            company_method_permit_id=company_method_permit.permit_id,
            as_of=corporate.as_of,
            contributions=tuple(contributions),
            gross_attributable_segment_equity=gross,
            corporate_net_adjustment=corporate_net,
            equity_value=equity,
            diluted_shares=corporate.diluted_shares,
            value_per_diluted_share=per_share,
        )


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
