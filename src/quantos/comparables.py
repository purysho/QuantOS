from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from .valuation import EquityBridge
from .valuation_methodology import (
    MethodPermit,
    ValuationMethod,
    ValuationMethodologyAssessment,
    ValuationMethodologyGate,
)


class MultipleKind(str, Enum):
    EV_REVENUE = "EV_REVENUE"
    EV_EBITDA = "EV_EBITDA"
    EV_EBIT = "EV_EBIT"
    PE = "PE"
    PB = "PB"
    FCF_YIELD = "FCF_YIELD"


class DistributionStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    INSUFFICIENT_PEERS = "INSUFFICIENT_PEERS"


class ImpliedValuationStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    INSUFFICIENT_PEERS = "INSUFFICIENT_PEERS"
    TARGET_NOT_APPLICABLE = "TARGET_NOT_APPLICABLE"


@dataclass(frozen=True)
class PeerSnapshot:
    entity_id: str
    knowledge_time: datetime
    market_as_of: datetime
    industry: str
    geography: str
    business_tags: tuple[str, ...]
    distressed: bool
    revenue: Decimal
    ebitda: Decimal
    ebit: Decimal
    net_income: Decimal
    book_equity: Decimal
    free_cash_flow: Decimal
    enterprise_value: Decimal
    equity_value: Decimal
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.entity_id.strip():
            raise ValueError("peer entity_id is required")
        if self.knowledge_time.tzinfo is None or self.market_as_of.tzinfo is None:
            raise ValueError("peer timestamps must be timezone-aware")
        if not self.industry.strip() or not self.geography.strip():
            raise ValueError("peer industry and geography are required")
        if self.equity_value <= 0:
            raise ValueError("peer equity_value must be positive")
        for name in (
            "revenue", "ebitda", "ebit", "net_income", "book_equity",
            "free_cash_flow", "enterprise_value", "equity_value",
        ):
            if not getattr(self, name).is_finite():
                raise ValueError(f"peer {name} must be finite")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("peer snapshot requires evidence references")

    @property
    def snapshot_id(self) -> str:
        return _content_id("peer-snapshot", {
            "entity_id": self.entity_id,
            "knowledge_time": self.knowledge_time.isoformat(),
            "market_as_of": self.market_as_of.isoformat(),
            "industry": self.industry,
            "geography": self.geography,
            "business_tags": list(self.business_tags),
            "distressed": self.distressed,
            "revenue": str(self.revenue),
            "ebitda": str(self.ebitda),
            "ebit": str(self.ebit),
            "net_income": str(self.net_income),
            "book_equity": str(self.book_equity),
            "free_cash_flow": str(self.free_cash_flow),
            "enterprise_value": str(self.enterprise_value),
            "equity_value": str(self.equity_value),
            "evidence_references": list(self.evidence_references),
        })


@dataclass(frozen=True)
class CompsTargetProfile:
    entity_id: str
    as_of: datetime
    industry: str
    geography: str
    business_tags: tuple[str, ...]
    revenue: Decimal
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.entity_id.strip():
            raise ValueError("target entity_id is required")
        if self.as_of.tzinfo is None:
            raise ValueError("target as_of must be timezone-aware")
        if not self.industry.strip() or not self.geography.strip():
            raise ValueError("target industry and geography are required")
        if not self.revenue.is_finite() or self.revenue <= 0:
            raise ValueError("target revenue must be finite and positive")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("target profile requires evidence references")

    @property
    def target_id(self) -> str:
        return _content_id("comps-target", {
            "entity_id": self.entity_id,
            "as_of": self.as_of.isoformat(),
            "industry": self.industry,
            "geography": self.geography,
            "business_tags": list(self.business_tags),
            "revenue": str(self.revenue),
            "evidence_references": list(self.evidence_references),
        })


@dataclass(frozen=True)
class PeerSelectionPolicy:
    require_same_industry: bool
    allowed_geographies: tuple[str, ...]
    required_business_tags: tuple[str, ...]
    minimum_revenue_ratio: Decimal
    maximum_revenue_ratio: Decimal
    exclude_distressed: bool
    minimum_included_peers: int
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not self.minimum_revenue_ratio.is_finite()
            or not self.maximum_revenue_ratio.is_finite()
            or self.minimum_revenue_ratio <= 0
            or self.maximum_revenue_ratio <= 0
            or self.minimum_revenue_ratio > self.maximum_revenue_ratio
        ):
            raise ValueError("revenue-ratio bounds must be positive and ordered")
        if self.minimum_included_peers < 2:
            raise ValueError("minimum_included_peers must be at least 2")
        if len(self.allowed_geographies) != len(set(self.allowed_geographies)):
            raise ValueError("allowed geographies cannot contain duplicates")
        if len(self.required_business_tags) != len(set(self.required_business_tags)):
            raise ValueError("required business tags cannot contain duplicates")
        if not self.rationale.strip():
            raise ValueError("peer-selection policy rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("peer-selection policy requires evidence references")

    @property
    def policy_id(self) -> str:
        return _content_id("peer-selection-policy", {
            "require_same_industry": self.require_same_industry,
            "allowed_geographies": list(self.allowed_geographies),
            "required_business_tags": list(self.required_business_tags),
            "minimum_revenue_ratio": str(self.minimum_revenue_ratio),
            "maximum_revenue_ratio": str(self.maximum_revenue_ratio),
            "exclude_distressed": self.exclude_distressed,
            "minimum_included_peers": self.minimum_included_peers,
            "rationale": self.rationale,
            "evidence_references": list(self.evidence_references),
        })


@dataclass(frozen=True)
class PeerSelectionDecision:
    snapshot_id: str
    entity_id: str
    included: bool
    exclusion_reasons: tuple[str, ...]


@dataclass(frozen=True)
class PeerSelectionResult:
    selection_id: str
    target_id: str
    policy_id: str
    as_of: datetime
    minimum_included_peers: int
    decisions: tuple[PeerSelectionDecision, ...]
    included_snapshots: tuple[PeerSnapshot, ...]

    @property
    def included_peer_ids(self) -> tuple[str, ...]:
        return tuple(item.entity_id for item in self.included_snapshots)

    @property
    def is_sufficient(self) -> bool:
        return len(self.included_snapshots) >= self.minimum_included_peers


class PeerSelector:
    def select(
        self,
        *,
        target: CompsTargetProfile,
        candidates: tuple[PeerSnapshot, ...],
        policy: PeerSelectionPolicy,
    ) -> PeerSelectionResult:
        if not candidates:
            raise ValueError("peer candidate universe cannot be empty")
        if len({item.snapshot_id for item in candidates}) != len(candidates):
            raise ValueError("peer candidate universe contains duplicate snapshots")
        if len({item.entity_id for item in candidates}) != len(candidates):
            raise ValueError(
                "peer candidate universe contains multiple snapshots for one entity"
            )
        decisions: list[PeerSelectionDecision] = []
        included: list[PeerSnapshot] = []
        for peer in sorted(candidates, key=lambda item: item.snapshot_id):
            reasons = self._reasons(target=target, peer=peer, policy=policy)
            decisions.append(PeerSelectionDecision(
                snapshot_id=peer.snapshot_id,
                entity_id=peer.entity_id,
                included=not reasons,
                exclusion_reasons=tuple(reasons),
            ))
            if not reasons:
                included.append(peer)
        payload = {
            "target_id": target.target_id,
            "policy_id": policy.policy_id,
            "as_of": target.as_of.isoformat(),
            "candidate_snapshot_ids": [
                item.snapshot_id for item in sorted(candidates, key=lambda item: item.snapshot_id)
            ],
            "decisions": [
                {
                    "snapshot_id": item.snapshot_id,
                    "entity_id": item.entity_id,
                    "included": item.included,
                    "exclusion_reasons": list(item.exclusion_reasons),
                }
                for item in decisions
            ],
        }
        return PeerSelectionResult(
            selection_id=_content_id("peer-selection", payload),
            target_id=target.target_id,
            policy_id=policy.policy_id,
            as_of=target.as_of,
            minimum_included_peers=policy.minimum_included_peers,
            decisions=tuple(decisions),
            included_snapshots=tuple(included),
        )

    @staticmethod
    def _reasons(
        *,
        target: CompsTargetProfile,
        peer: PeerSnapshot,
        policy: PeerSelectionPolicy,
    ) -> list[str]:
        reasons: list[str] = []
        if peer.entity_id == target.entity_id:
            reasons.append("TARGET_ENTITY_EXCLUDED")
        if peer.knowledge_time > target.as_of:
            reasons.append("KNOWLEDGE_AFTER_AS_OF")
        if peer.market_as_of > target.as_of:
            reasons.append("MARKET_DATA_AFTER_AS_OF")
        if policy.require_same_industry and peer.industry.casefold() != target.industry.casefold():
            reasons.append("INDUSTRY_MISMATCH")
        if policy.allowed_geographies and peer.geography not in policy.allowed_geographies:
            reasons.append("GEOGRAPHY_NOT_ALLOWED")
        if any(tag not in set(peer.business_tags) for tag in policy.required_business_tags):
            reasons.append("REQUIRED_BUSINESS_TAG_MISSING")
        if peer.revenue <= 0:
            reasons.append("NON_POSITIVE_REVENUE")
        else:
            ratio = peer.revenue / target.revenue
            if ratio < policy.minimum_revenue_ratio:
                reasons.append("REVENUE_BELOW_POLICY_RANGE")
            if ratio > policy.maximum_revenue_ratio:
                reasons.append("REVENUE_ABOVE_POLICY_RANGE")
        if policy.exclude_distressed and peer.distressed:
            reasons.append("DISTRESSED_EXCLUDED")
        return reasons


@dataclass(frozen=True)
class CompsValuationPolicy:
    minimum_peers_per_multiple: int
    percentiles: tuple[Decimal, ...]
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.minimum_peers_per_multiple < 2:
            raise ValueError("minimum_peers_per_multiple must be at least 2")
        if len(self.percentiles) < 2 or len(self.percentiles) != len(set(self.percentiles)):
            raise ValueError("comps policy requires at least two unique percentiles")
        if tuple(sorted(self.percentiles)) != self.percentiles:
            raise ValueError("comps percentiles must be sorted ascending")
        if any((not item.is_finite()) or item < 0 or item > 1 for item in self.percentiles):
            raise ValueError("comps percentiles must be between 0 and 1")
        if not self.rationale.strip():
            raise ValueError("comps valuation policy rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("comps valuation policy requires evidence references")

    @property
    def policy_id(self) -> str:
        return _content_id("comps-valuation-policy", {
            "minimum_peers_per_multiple": self.minimum_peers_per_multiple,
            "percentiles": [str(item) for item in self.percentiles],
            "rationale": self.rationale,
            "evidence_references": list(self.evidence_references),
        })


@dataclass(frozen=True)
class PeerMultiple:
    peer_entity_id: str
    peer_snapshot_id: str
    kind: MultipleKind
    value: Decimal


@dataclass(frozen=True)
class PercentileValue:
    percentile: Decimal
    value: Decimal


@dataclass(frozen=True)
class MultipleDistribution:
    kind: MultipleKind
    status: DistributionStatus
    peer_count: int
    peer_values: tuple[PeerMultiple, ...]
    minimum: Decimal | None
    percentiles: tuple[PercentileValue, ...]
    maximum: Decimal | None


@dataclass(frozen=True)
class CompsTargetFinancials:
    target_id: str
    entity_id: str
    as_of: datetime
    revenue: Decimal
    ebitda: Decimal
    ebit: Decimal
    net_income: Decimal
    book_equity: Decimal
    free_cash_flow: Decimal
    equity_bridge: EquityBridge
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.target_id.startswith("comps-target:"):
            raise ValueError("target_id must reference a CompsTargetProfile")
        if not self.entity_id.strip() or self.as_of.tzinfo is None:
            raise ValueError("target financial identity/as_of invalid")
        for name in ("revenue", "ebitda", "ebit", "net_income", "book_equity", "free_cash_flow"):
            if not getattr(self, name).is_finite():
                raise ValueError(f"target {name} must be finite")
        if self.equity_bridge.as_of > self.as_of:
            raise ValueError("equity bridge cannot be from after the comps as_of")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("target financials require evidence references")


@dataclass(frozen=True)
class ImpliedValuationPoint:
    percentile: Decimal
    multiple_or_yield: Decimal
    enterprise_value: Decimal | None
    equity_value: Decimal
    value_per_diluted_share: Decimal


@dataclass(frozen=True)
class ImpliedValuationRange:
    kind: MultipleKind
    status: ImpliedValuationStatus
    points: tuple[ImpliedValuationPoint, ...]
    reason: str | None


@dataclass(frozen=True)
class ComparableValuationResult:
    valuation_id: str
    selection_id: str
    method_permit_id: str
    policy_id: str
    distributions: tuple[MultipleDistribution, ...]
    implied_ranges: tuple[ImpliedValuationRange, ...]


class ComparableCompanyEngine:
    def value(
        self,
        *,
        selection: PeerSelectionResult,
        target: CompsTargetFinancials,
        policy: CompsValuationPolicy,
        methodology_assessment: ValuationMethodologyAssessment,
        method_permit: MethodPermit,
    ) -> ComparableValuationResult:
        ValuationMethodologyGate.validate(
            assessment=methodology_assessment,
            permit=method_permit,
            required_method=ValuationMethod.TRADING_COMPS,
        )
        if selection.target_id != target.target_id:
            raise ValueError("peer selection does not belong to target financials")
        if target.as_of != selection.as_of:
            raise ValueError("target financials and peer selection as_of differ")
        if not selection.is_sufficient:
            raise ValueError(
                "peer selection does not satisfy its minimum-included-peers policy"
            )
        if any(peer.knowledge_time > target.as_of for peer in selection.included_snapshots):
            raise ValueError("included peer contains future-known information")
        if any(peer.market_as_of > target.as_of for peer in selection.included_snapshots):
            raise ValueError("included peer contains future market data")

        distributions = tuple(
            self._distribution(kind, selection.included_snapshots, policy)
            for kind in MultipleKind
        )
        ranges = tuple(self._implied_range(item, target) for item in distributions)
        payload = {
            "selection_id": selection.selection_id,
            "method_permit_id": method_permit.permit_id,
            "policy_id": policy.policy_id,
            "target_id": target.target_id,
            "distributions": [