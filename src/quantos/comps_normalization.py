from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from .comparables import (
    CompsTargetFinancials,
    CompsTargetProfile,
    CompsValuationPolicy,
    DistributionStatus,
    ImpliedValuationPoint,
    ImpliedValuationRange,
    ImpliedValuationStatus,
    MultipleDistribution,
    MultipleKind,
    PeerMultiple,
    PeerSelectionResult,
    PeerSnapshot,
    PercentileValue,
)
from .valuation_methodology import (
    MethodPermit,
    ValuationMethod,
    ValuationMethodologyAssessment,
    ValuationMethodologyGate,
)


class NormalizationMetric(str, Enum):
    REVENUE = "revenue"
    EBITDA = "ebitda"
    EBIT = "ebit"
    NET_INCOME = "net_income"
    BOOK_EQUITY = "book_equity"
    FREE_CASH_FLOW = "free_cash_flow"
    ENTERPRISE_VALUE = "enterprise_value"
    EQUITY_VALUE = "equity_value"


class AdjustmentKind(str, Enum):
    NON_RECURRING = "NON_RECURRING"
    ACCOUNTING_POLICY = "ACCOUNTING_POLICY"
    PRO_FORMA = "PRO_FORMA"
    OTHER = "OTHER"


@dataclass(frozen=True)
class NormalizationAdjustment:
    raw_snapshot_id: str
    metric: NormalizationMetric
    amount: Decimal
    kind: AdjustmentKind
    knowledge_time: datetime
    rationale: str
    evidence_references: tuple[str, ...]
    prepared_by: str

    def __post_init__(self) -> None:
        if not self.raw_snapshot_id.startswith("peer-snapshot:"):
            raise ValueError("normalization adjustment requires raw peer snapshot ID")
        if not self.amount.is_finite():
            raise ValueError("normalization adjustment amount must be finite")
        if self.knowledge_time.tzinfo is None:
            raise ValueError("adjustment knowledge_time must be timezone-aware")
        if not self.rationale.strip() or not self.prepared_by.strip():
            raise ValueError("adjustment rationale and preparer are required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("normalization adjustment requires evidence references")

    @property
    def adjustment_id(self) -> str:
        return _content_id("peer-normalization-adjustment", {
            "raw_snapshot_id": self.raw_snapshot_id,
            "metric": self.metric.value,
            "amount": str(self.amount),
            "kind": self.kind.value,
            "knowledge_time": self.knowledge_time.isoformat(),
            "rationale": self.rationale,
            "evidence_references": list(self.evidence_references),
            "prepared_by": self.prepared_by,
        })


@dataclass(frozen=True)
class NormalizedPeer:
    raw_snapshot: PeerSnapshot
    adjustment_ids: tuple[str, ...]
    revenue: Decimal
    ebitda: Decimal
    ebit: Decimal
    net_income: Decimal
    book_equity: Decimal
    free_cash_flow: Decimal
    enterprise_value: Decimal
    equity_value: Decimal

    @property
    def normalized_id(self) -> str:
        return _content_id("normalized-peer", {
            "raw_snapshot_id": self.raw_snapshot.snapshot_id,
            "adjustment_ids": list(self.adjustment_ids),
            "revenue": str(self.revenue),
            "ebitda": str(self.ebitda),
            "ebit": str(self.ebit),
            "net_income": str(self.net_income),
            "book_equity": str(self.book_equity),
            "free_cash_flow": str(self.free_cash_flow),
            "enterprise_value": str(self.enterprise_value),
            "equity_value": str(self.equity_value),
        })


@dataclass(frozen=True)
class PeerNormalizationDataset:
    dataset_id: str
    selection_id: str
    as_of: datetime
    reviewer: str
    reviewed_at: datetime
    review_notes: str
    peers: tuple[NormalizedPeer, ...]


class PeerNormalizationEngine:
    """Creates a reviewed normalized peer dataset without mutating raw facts."""

    def build(
        self,
        *,
        selection: PeerSelectionResult,
        adjustments: tuple[NormalizationAdjustment, ...],
        reviewer: str,
        reviewed_at: datetime,
        review_notes: str,
    ) -> PeerNormalizationDataset:
        if not selection.is_sufficient:
            raise ValueError("cannot normalize an insufficient peer selection")
        if not reviewer.strip() or not review_notes.strip():
            raise ValueError("normalization reviewer and review notes are required")
        if reviewed_at.tzinfo is None:
            raise ValueError("reviewed_at must be timezone-aware")
        if reviewed_at > selection.as_of:
            raise ValueError(
                "normalization review cannot occur after the valuation as_of"
            )
        if len({item.adjustment_id for item in adjustments}) != len(adjustments):
            raise ValueError("duplicate normalization adjustments are not allowed")

        raw_by_id = {
            item.snapshot_id: item for item in selection.included_snapshots
        }
        grouped: dict[str, list[NormalizationAdjustment]] = {
            key: [] for key in raw_by_id
        }
        for adjustment in adjustments:
            if adjustment.raw_snapshot_id not in raw_by_id:
                raise ValueError(
                    "normalization adjustment references peer outside included selection"
                )
            if adjustment.knowledge_time > selection.as_of:
                raise ValueError("normalization adjustment uses future-known information")
            if adjustment.knowledge_time > reviewed_at:
                raise ValueError("normalization review predates adjustment knowledge")
            if adjustment.prepared_by.strip() == reviewer.strip():
                raise ValueError("normalization preparer and reviewer must differ")
            grouped[adjustment.raw_snapshot_id].append(adjustment)

        peers = tuple(
            self._normalize(
                raw=raw,
                adjustments=tuple(grouped[raw.snapshot_id]),
            )
            for raw in sorted(
                selection.included_snapshots,
                key=lambda item: item.snapshot_id,
            )
        )
        payload = {
            "selection_id": selection.selection_id,
            "as_of": selection.as_of.isoformat(),
            "reviewer": reviewer.strip(),
            "reviewed_at": reviewed_at.isoformat(),
            "review_notes": review_notes.strip(),
            "peers": [
                {
                    "normalized_id": peer.normalized_id,
                    "raw_snapshot_id": peer.raw_snapshot.snapshot_id,
                    "adjustment_ids": list(peer.adjustment_ids),
                }
                for peer in peers
            ],
        }
        return PeerNormalizationDataset(
            dataset_id=_content_id("peer-normalization-dataset", payload),
            selection_id=selection.selection_id,
            as_of=selection.as_of,
            reviewer=reviewer.strip(),
            reviewed_at=reviewed_at,
            review_notes=review_notes.strip(),
            peers=peers,
        )

    @staticmethod
    def _normalize(
        *,
        raw: PeerSnapshot,
        adjustments: tuple[NormalizationAdjustment, ...],
    ) -> NormalizedPeer:
        values = {
            "revenue": raw.revenue,
            "ebitda": raw.ebitda,
            "ebit": raw.ebit,
            "net_income": raw.net_income,
            "book_equity": raw.book_equity,
            "free_cash_flow": raw.free_cash_flow,
            "enterprise_value": raw.enterprise_value,
            "equity_value": raw.equity_value,
        }
        ordered = tuple(sorted(adjustments, key=lambda item: item.adjustment_id))
        for adjustment in ordered:
            values[adjustment.metric.value] += adjustment.amount
        if values["equity_value"] <= 0:
            raise ValueError("normalization produced non-positive equity value")
        if any(not value.is_finite() for value in values.values()):
            raise ValueError("normalization produced non-finite metric")
        return NormalizedPeer(
            raw_snapshot=raw,
            adjustment_ids=tuple(item.adjustment_id for item in ordered),
            **values,
        )


@dataclass(frozen=True)
class SectorMetricContract:
    industry: str
    allowed_multiple_kinds: tuple[MultipleKind, ...]
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.industry.strip():
            raise ValueError("sector metric contract requires industry")
        if not self.allowed_multiple_kinds:
            raise ValueError("sector metric contract requires at least one metric")
        if len(self.allowed_multiple_kinds) != len(set(self.allowed_multiple_kinds)):
            raise ValueError("sector metric contract cannot repeat metrics")
        if not self.rationale.strip():
            raise ValueError("sector metric contract rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("sector metric contract requires evidence references")

    @property
    def contract_id(self) -> str:
        return _content_id("sector-metric-contract", {
            "industry": self.industry,
            "allowed_multiple_kinds": [
                item.value for item in self.allowed_multiple_kinds
            ],
            "rationale": self.rationale,
            "evidence_references": list(self.evidence_references),
        })


@dataclass(frozen=True)
class NormalizedComparableValuationResult:
    valuation_id: str
    selection_id: str
    normalization_dataset_id: str
    metric_contract_id: str
    method_permit_id: str
    policy_id: str
    distributions: tuple[MultipleDistribution, ...]
    implied_ranges: tuple[ImpliedValuationRange, ...]


class NormalizedComparableCompanyEngine:
    def value(
        self,
        *,
        selection: PeerSelectionResult,
        normalization: PeerNormalizationDataset,
        target_profile: CompsTargetProfile,
        target: CompsTargetFinancials,
        metric_contract: SectorMetricContract,
        policy: CompsValuationPolicy,
        methodology_assessment: ValuationMethodologyAssessment,
        method_permit: MethodPermit,
    ) -> NormalizedComparableValuationResult:
        ValuationMethodologyGate.validate(
            assessment=methodology_assessment,
            permit=method_permit,
            required_method=ValuationMethod.TRADING_COMPS,
        )
        if not selection.is_sufficient:
            raise ValueError("peer selection is insufficient")
        if normalization.selection_id != selection.selection_id:
            raise ValueError("normalization dataset belongs to another selection")
        if target_profile.target_id != target.target_id:
            raise ValueError("target profile and target financials differ")
        if selection.target_id != target.target_id:
            raise ValueError("peer selection belongs to another target")
        if target.as_of != selection.as_of or normalization.as_of != selection.as_of:
            raise ValueError("comps as_of timestamps do not match")
        if metric_contract.industry.casefold() != target_profile.industry.casefold():
            raise ValueError("sector metric contract does not match target industry")

        expected_raw = {
            item.snapshot_id for item in selection.included_snapshots
        }
        normalized_raw = {
            item.raw_snapshot.snapshot_id for item in normalization.peers
        }
        if normalized_raw != expected_raw:
            raise ValueError("normalization dataset does not cover exact peer selection")

        distributions = tuple(
            self._distribution(
                kind=kind,
                peers=normalization.peers,
                policy=policy,
            )
            for kind in metric_contract.allowed_multiple_kinds
        )
        ranges = tuple(
            self._implied_range(distribution=item, target=target)
            for item in distributions
        )
        payload = {
            "selection_id": selection.selection_id,
            "normalization_dataset_id": normalization.dataset_id,
            "metric_contract_id": metric_contract.contract_id,
            "method_permit_id": method_permit.permit_id,
            "policy_id": policy.policy_id,
            "target_id": target.target_id,
            "distributions": [
                {
                    "kind": item.kind.value,
                    "status": item.status.value,
                    "peer_values": [
                        {
                            "normalized_peer_id": peer.peer_snapshot_id,
                            "value": str(peer.value),
                        }
                        for peer in item.peer_values
                    ],
                    "percentiles": [
                        {
                            "percentile": str(point.percentile),
                            "value": str(point.value),
                        }
                        for point in item.percentiles
                    ],
                }
                for item in distributions
            ],
        }
        return NormalizedComparableValuationResult(
            valuation_id=_content_id("normalized-trading-comps", payload),
            selection_id=selection.selection_id,
            normalization_dataset_id=normalization.dataset_id,
            metric_contract_id=metric_contract.contract_id,
            method_permit_id=method_permit.permit_id,
            policy_id=policy.policy_id,
            distributions=distributions,
            implied_ranges=ranges,
        )

    def _distribution(
        self,
        *,
        kind: MultipleKind,
        peers: tuple[NormalizedPeer, ...],
        policy: CompsValuationPolicy,
    ) -> MultipleDistribution:
        records: list[PeerMultiple] = []
        for peer in peers:
            value = self._multiple(peer, kind)
            if value is None:
                continue
            records.append(PeerMultiple(
                peer_entity_id=peer.raw_snapshot.entity_id,
                peer_snapshot_id=peer.normalized_id,
                kind=kind,
                value=value,
            ))
        records.sort(key=lambda item: (item.value, item.peer_snapshot_id))
        if len(records) < policy.minimum_peers_per_multiple:
            return MultipleDistribution(
                kind=kind,
                status=DistributionStatus.INSUFFICIENT_PEERS,
                peer_count=len(records),
                peer_values=tuple(records),
                minimum=None,
                percentiles=(),
                maximum=None,
            )
        values = tuple(item.value for item in records)
        return MultipleDistribution(
            kind=kind,
            status=DistributionStatus.AVAILABLE,
            peer_count=len(records),
            peer_values=tuple(records),
            minimum=values[0],
            percentiles=tuple(
                PercentileValue(item, _percentile(values, item))
                for item in policy.percentiles
            ),
            maximum=values[-1],
        )

    @staticmethod
    def _multiple(
        peer: NormalizedPeer,
        kind: MultipleKind,
    ) -> Decimal | None:
        if kind is MultipleKind.EV_REVENUE:
            return peer.enterprise_value / peer.revenue if peer.revenue > 0 else None
        if kind is MultipleKind.EV_EBITDA:
            return peer.enterprise_value / peer.ebitda if peer.ebitda > 0 else None
        if kind is MultipleKind.EV_EBIT:
            return peer.enterprise_value / peer.ebit if peer.ebit > 0 else None
        if kind is MultipleKind.PE:
            return peer.equity_value / peer.net_income if peer.net_income > 0 else None
        if kind is MultipleKind.PB:
            return peer.equity_value / peer.book_equity if peer.book_equity > 0 else None
        if kind is MultipleKind.FCF_YIELD:
            return peer.free_cash_flow / peer.equity_value
        raise AssertionError(kind)

    @staticmethod
    def _implied_range(
        *,
        distribution: MultipleDistribution,
        target: CompsTargetFinancials,
    ) -> ImpliedValuationRange:
        if distribution.status is DistributionStatus.INSUFFICIENT_PEERS:
            return ImpliedValuationRange(
                distribution.kind,
                ImpliedValuationStatus.INSUFFICIENT_PEERS,
                (),
                "Not enough normalized peers for this metric.",
            )
        denominator = {
            MultipleKind.EV_REVENUE: target.revenue,
            MultipleKind.EV_EBITDA: target.ebitda,
            MultipleKind.EV_EBIT: target.ebit,
            MultipleKind.PE: target.net_income,
            MultipleKind.PB: target.book_equity,
            MultipleKind.FCF_YIELD: target.free_cash_flow,
        }[distribution.kind]
        if denominator <= 0:
            return ImpliedValuationRange(
                distribution.kind,
                ImpliedValuationStatus.TARGET_NOT_APPLICABLE,
                (),
                "Target denominator is non-positive for this metric.",
            )
        bridge = target.equity_bridge
        points: list[ImpliedValuationPoint] = []
        for item in distribution.percentiles:
            if distribution.kind in {
                MultipleKind.EV_REVENUE,
                MultipleKind.EV_EBITDA,
                MultipleKind.EV_EBIT,
            }:
                enterprise = denominator * item.value
                equity = (
                    enterprise + bridge.cash + bridge.non_operating_investments
                    - bridge.debt - bridge.preferred_equity
                    - bridge.noncontrolling_interest
                )
            elif distribution.kind in {MultipleKind.PE, MultipleKind.PB}:
                enterprise = None
                equity = denominator * item.value
            else:
                if item.value <= 0:
                    return ImpliedValuationRange(
                        distribution.kind,
                        ImpliedValuationStatus.TARGET_NOT_APPLICABLE,
                        (),
                        "Non-positive FCF yield cannot support reciprocal valuation.",
                    )
                enterprise = None
                equity = denominator / item.value
            points.append(ImpliedValuationPoint(
                percentile=item.percentile,
                multiple_or_yield=item.value,
                enterprise_value=enterprise,
                equity_value=equity,
                value_per_diluted_share=equity / bridge.diluted_shares,
            ))
        return ImpliedValuationRange(
            distribution.kind,
            ImpliedValuationStatus.AVAILABLE,
            tuple(points),
            None,
        )


def _percentile(values: tuple[Decimal, ...], percentile: Decimal) -> Decimal:
    if not values:
        raise ValueError("cannot calculate percentile of empty values")
    if len(values) == 1:
        return values[0]
    position = Decimal(len(values) - 1) * percentile
    lower = int(position)
    upper = lower if position == Decimal(lower) else lower + 1
    if lower == upper:
        return values[lower]
    fraction = position - Decimal(lower)
    return values[lower] + (values[upper] - values[lower]) * fraction


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
