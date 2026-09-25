from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from .comparables import (
    ComparableValuationResult,
    ImpliedValuationStatus,
)
from .comps_normalization import NormalizedComparableValuationResult
from .lbo import LBOResult
from .sotp import SOTPResult
from .valuation import DCFResult
from .valuation_sensitivity import DCFSensitivityGrid


class ValuationFamily(str, Enum):
    DCF = "DCF"
    TRADING_COMPS = "TRADING_COMPS"
    SOTP = "SOTP"


class TriangulationStatus(str, Enum):
    INSUFFICIENT_METHOD_FAMILIES = "INSUFFICIENT_METHOD_FAMILIES"
    COMMON_OVERLAP = "COMMON_OVERLAP"
    PARTIAL_OVERLAP = "PARTIAL_OVERLAP"
    DISJOINT = "DISJOINT"


@dataclass(frozen=True)
class ValuationObservation:
    observation_id: str
    family: ValuationFamily
    label: str
    reference_id: str
    method_permit_id: str
    as_of: datetime
    low_per_share: Decimal
    central_per_share: Decimal
    high_per_share: Decimal
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.label.strip() or not self.reference_id.strip():
            raise ValueError("valuation observation label/reference are required")
        if not self.method_permit_id.startswith("valuation-method-permit:"):
            raise ValueError("valuation observation requires a method permit ID")
        if self.as_of.tzinfo is None:
            raise ValueError("valuation observation as_of must be timezone-aware")
        for name in ("low_per_share", "central_per_share", "high_per_share"):
            if not getattr(self, name).is_finite():
                raise ValueError(f"{name} must be finite")
        if not (
            self.low_per_share
            <= self.central_per_share
            <= self.high_per_share
        ):
            raise ValueError(
                "valuation observation must satisfy low <= central <= high"
            )
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("valuation observation requires evidence references")
        expected = make_observation_id(
            family=self.family,
            label=self.label,
            reference_id=self.reference_id,
            method_permit_id=self.method_permit_id,
            as_of=self.as_of,
            low=self.low_per_share,
            central=self.central_per_share,
            high=self.high_per_share,
            evidence_references=self.evidence_references,
        )
        if self.observation_id != expected:
            raise ValueError("valuation observation identity mismatch")


@dataclass(frozen=True)
class LBOSponsorCrossCheck:
    cross_check_id: str
    valuation_id: str
    model_run_id: str
    method_permit_id: str
    status: str
    sponsor_moic: Decimal | None
    sponsor_irr: Decimal | None
    evidence_references: tuple[str, ...]


@dataclass(frozen=True)
class TriangulationPolicy:
    minimum_method_families: int
    wide_central_dispersion_ratio: Decimal
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.minimum_method_families < 2:
            raise ValueError("triangulation requires at least two method families")
        if (
            not self.wide_central_dispersion_ratio.is_finite()
            or self.wide_central_dispersion_ratio < 0
        ):
            raise ValueError(
                "wide_central_dispersion_ratio must be finite and non-negative"
            )
        if not self.rationale.strip():
            raise ValueError("triangulation policy rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("triangulation policy requires evidence references")

    @property
    def policy_id(self) -> str:
        return _content_id(
            "valuation-triangulation-policy",
            {
                "minimum_method_families": self.minimum_method_families,
                "wide_central_dispersion_ratio": str(
                    self.wide_central_dispersion_ratio
                ),
                "rationale": self.rationale,
                "evidence_references": list(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class FamilySummary:
    family: ValuationFamily
    observation_ids: tuple[str, ...]
    low_per_share: Decimal
    central_per_share: Decimal
    high_per_share: Decimal


@dataclass(frozen=True)
class TriangulationFlag:
    code: str
    detail: str


@dataclass(frozen=True)
class ValuationTriangulation:
    triangulation_id: str
    as_of: datetime
    policy_id: str
    status: TriangulationStatus
    observations: tuple[ValuationObservation, ...]
    family_summaries: tuple[FamilySummary, ...]
    common_overlap_low: Decimal | None
    common_overlap_high: Decimal | None
    union_low: Decimal
    union_high: Decimal
    central_dispersion_ratio: Decimal | None
    lbo_cross_checks: tuple[LBOSponsorCrossCheck, ...]
    flags: tuple[TriangulationFlag, ...]
    caveat: str


def make_observation_id(
    *,
    family: ValuationFamily,
    label: str,
    reference_id: str,
    method_permit_id: str,
    as_of: datetime,
    low: Decimal,
    central: Decimal,
    high: Decimal,
    evidence_references: tuple[str, ...],
) -> str:
    return _content_id(
        "valuation-observation",
        {
            "family": family.value,
            "label": label,
            "reference_id": reference_id,
            "method_permit_id": method_permit_id,
            "as_of": as_of.isoformat(),
            "low": str(low),
            "central": str(central),
            "high": str(high),
            "evidence_references": list(evidence_references),
        },
    )


def build_observation(
    *,
    family: ValuationFamily,
    label: str,
    reference_id: str,
    method_permit_id: str,
    as_of: datetime,
    low: Decimal,
    central: Decimal,
    high: Decimal,
    evidence_references: tuple[str, ...],
) -> ValuationObservation:
    return ValuationObservation(
        observation_id=make_observation_id(
            family=family,
            label=label,
            reference_id=reference_id,
            method_permit_id=method_permit_id,
            as_of=as_of,
            low=low,
            central=central,
            high=high,
            evidence_references=evidence_references,
        ),
        family=family,
        label=label,
        reference_id=reference_id,
        method_permit_id=method_permit_id,
        as_of=as_of,
        low_per_share=low,
        central_per_share=central,
        high_per_share=high,
        evidence_references=evidence_references,
    )


class TriangulationAdapter:
    @staticmethod
    def from_dcf(
        *,
        dcf: DCFResult,
        sensitivity: DCFSensitivityGrid,
        as_of: datetime,
        evidence_references: tuple[str, ...],
    ) -> ValuationObservation:
        if sensitivity.base_valuation_id != dcf.valuation_id:
            raise ValueError("DCF sensitivity grid belongs to another valuation")
        if sensitivity.model_run_id != dcf.model_run_id:
            raise ValueError("DCF sensitivity grid model-run mismatch")
        if sensitivity.method_permit_id != dcf.method_permit_id:
            raise ValueError("DCF sensitivity grid method-permit mismatch")
        if (
            sensitivity.min_valid_per_share is None
            or sensitivity.max_valid_per_share is None
        ):
            raise ValueError("DCF sensitivity has no valid per-share range")
        return build_observation(
            family=ValuationFamily.DCF,
            label="FCFF DCF sensitivity envelope",
            reference_id=sensitivity.grid_id,
            method_permit_id=dcf.method_permit_id,
            as_of=as_of,
            low=sensitivity.min_valid_per_share,
            central=dcf.value_per_diluted_share,
            high=sensitivity.max_valid_per_share,
            evidence_references=evidence_references,
        )

    @staticmethod
    def from_comps(
        *,
        result: ComparableValuationResult | NormalizedComparableValuationResult,
        as_of: datetime,
        evidence_references: tuple[str, ...],
    ) -> tuple[ValuationObservation, ...]:
        observations: list[ValuationObservation] = []
        for item in result.implied_ranges:
            if item.status is not ImpliedValuationStatus.AVAILABLE:
                continue
            median = next(
                (
                    point
                    for point in item.points
                    if point.percentile == Decimal("0.50")
                ),
                None,
            )
            if median is None:
                raise ValueError(
                    "comps triangulation requires an explicit 50th percentile"
                )
            values = tuple(
                point.value_per_diluted_share for point in item.points
            )
            observations.append(
                build_observation(
                    family=ValuationFamily.TRADING_COMPS,
                    label=f"Trading comps {item.kind.value}",
                    reference_id=result.valuation_id,
                    method_permit_id=result.method_permit_id,
                    as_of=as_of,
                    low=min(values),
                    central=median.value_per_diluted_share,
                    high=max(values),
                    evidence_references=evidence_references,
                )
            )
        if not observations:
            raise ValueError("comps result has no triangulatable valuation ranges")
        return tuple(observations)

    @staticmethod
    def from_sotp(
        *,
        result: SOTPResult,
        evidence_references: tuple[str, ...],
    ) -> ValuationObservation:
        return build_observation(
            family=ValuationFamily.SOTP,
            label="Sum-of-the-parts",
            reference_id=result.valuation_id,
            method_permit_id=result.company_method_permit_id,
            as_of=result.as_of,
            low=result.value_per_diluted_share,
            central=result.value_per_diluted_share,
            high=result.value_per_diluted_share,
            evidence_references=evidence_references,
        )

    @staticmethod
    def from_lbo(
        *,
        result: LBOResult,
        evidence_references: tuple[str, ...],
    ) -> LBOSponsorCrossCheck:
        if not evidence_references or not all(
            item.strip() for item in evidence_references
        ):
            raise ValueError("LBO cross-check requires evidence references")
        payload = {
            "valuation_id": result.valuation_id,
            "model_run_id": result.model_run_id,
            "method_permit_id": result.method_permit_id,
            "status": result.status.value,
            "sponsor_moic": (
                str(result.sponsor_moic)
                if result.sponsor_moic is not None
                else None
            ),
            "sponsor_irr": (
                str(result.sponsor_irr)
                if result.sponsor_irr is not None
                else None
            ),
            "evidence_references": list(evidence_references),
        }
        return LBOSponsorCrossCheck(
            cross_check_id=_content_id("lbo-sponsor-cross-check", payload),
            valuation_id=result.valuation_id,
            model_run_id=result.model_run_id,
            method_permit_id=result.method_permit_id,
            status=result.status.value,
            sponsor_moic=result.sponsor_moic,
            sponsor_irr=result.sponsor_irr,
            evidence_references=evidence_references,
        )


class ValuationTriangulationEngine:
    CAVEAT = (
        "Triangulation preserves method disagreement. It does not calculate a "
        "weighted fair value, expected return, recommendation, or capital action."
    )

    def build(
        self,
        *,
        observations: tuple[ValuationObservation, ...],
        policy: TriangulationPolicy,
        lbo_cross_checks: tuple[LBOSponsorCrossCheck, ...] = (),
    ) -> ValuationTriangulation:
        if not observations:
            raise ValueError("triangulation requires valuation observations")
        if len({item.observation_id for item in observations}) != len(observations):
            raise ValueError("duplicate valuation observations are not allowed")
        as_of_values = {item.as_of for item in observations}
        if len(as_of_values) != 1:
            raise ValueError("triangulation observations must share one as_of")
        as_of = next(iter(as_of_values))

        families = self._summaries(observations)
        if len(families) < policy.minimum_method_families:
            status = TriangulationStatus.INSUFFICIENT_METHOD_FAMILIES
            overlap_low = None
            overlap_high = None
        else:
            overlap_low = max(item.low_per_share for item in families)
            overlap_high = min(item.high_per_share for item in families)
            if overlap_low <= overlap_high:
                status = TriangulationStatus.COMMON_OVERLAP
            elif self._has_cross_family_pair_overlap(families):
                status = TriangulationStatus.PARTIAL_OVERLAP
                overlap_low = None
                overlap_high = None
            else:
                status = TriangulationStatus.DISJOINT
                overlap_low = None
                overlap_high = None

        union_low = min(item.low_per_share for item in families)
        union_high = max(item.high_per_share for item in families)
        central_values = tuple(
            sorted(item.central_per_share for item in families)
        )
        central_reference = _median(central_values)
        if central_reference == 0:
            dispersion = None
        else:
            dispersion = (
                max(central_values) - min(central_values)
            ) / abs(central_reference)

        flags: list[TriangulationFlag] = []
        if status is TriangulationStatus.INSUFFICIENT_METHOD_FAMILIES:
            flags.append(
                TriangulationFlag(
                    "INSUFFICIENT_METHOD_FAMILIES",
                    (
                        f"{len(families)} independent method family/families are "
                        f"present; policy requires {policy.minimum_method_families}."
                    ),
                )
            )
        elif status is TriangulationStatus.DISJOINT:
            flags.append(
                TriangulationFlag(
                    "NO_CROSS_METHOD_OVERLAP",
                    "The family valuation envelopes do not overlap.",
                )
            )
        elif status is TriangulationStatus.PARTIAL_OVERLAP:
            flags.append(
                TriangulationFlag(
                    "PARTIAL_CROSS_METHOD_OVERLAP",
                    "Some method-family envelopes overlap, but there is no common overlap.",
                )
            )
        if dispersion is None:
            flags.append(
                TriangulationFlag(
                    "CENTRAL_REFERENCE_ZERO",
                    "Relative central-value dispersion cannot be computed around a zero median.",
                )
            )
        elif dispersion >= policy.wide_central_dispersion_ratio:
            flags.append(
                TriangulationFlag(
                    "WIDE_CROSS_METHOD_DISPERSION",
                    (
                        f"Family central-value span/median magnitude is {dispersion}, "
                        "at or above the policy threshold "
                        f"{policy.wide_central_dispersion_ratio}."
                    ),
                )
            )
        if lbo_cross_checks:
            flags.append(
                TriangulationFlag(
                    "LBO_SPONSOR_RETURN_CROSS_CHECK_PRESENT",
                    (
                        f"{len(lbo_cross_checks)} LBO sponsor-return cross-check(s) "
                        "are preserved separately and are not averaged into per-share value."
                    ),
                )
            )

        payload = {
            "as_of": as_of.isoformat(),
            "policy_id": policy.policy_id,
            "status": status.value,
            "observation_ids": sorted(
                item.observation_id for item in observations
            ),
            "family_summaries": [
                {
                    "family": item.family.value,
                    "observation_ids": list(item.observation_ids),
                    "low": str(item.low_per_share),
                    "central": str(item.central_per_share),
                    "high": str(item.high_per_share),
                }
                for item in families
            ],
            "common_overlap_low": (
                str(overlap_low) if overlap_low is not None else None
            ),
            "common_overlap_high": (
                str(overlap_high) if overlap_high is not None else None
            ),
            "union_low": str(union_low),
            "union_high": str(union_high),
            "central_dispersion_ratio": (
                str(dispersion) if dispersion is not None else None
            ),
            "lbo_cross_check_ids": sorted(
                item.cross_check_id for item in lbo_cross_checks
            ),
        }
        return ValuationTriangulation(
            triangulation_id=_content_id(
                "valuation-triangulation",
                payload,
            ),
            as_of=as_of,
            policy_id=policy.policy_id,
            status=status,
            observations=tuple(
                sorted(observations, key=lambda item: item.observation_id)
            ),
            family_summaries=families,
            common_overlap_low=overlap_low,
            common_overlap_high=overlap_high,
            union_low=union_low,
            union_high=union_high,
            central_dispersion_ratio=dispersion,
            lbo_cross_checks=tuple(
                sorted(
                    lbo_cross_checks,
                    key=lambda item: item.cross_check_id,
                )
            ),
            flags=tuple(flags),
            caveat=self.CAVEAT,
        )

    @staticmethod
    def _summaries(
        observations: tuple[ValuationObservation, ...],
    ) -> tuple[FamilySummary, ...]:
        grouped: dict[ValuationFamily, list[ValuationObservation]] = {}
        for item in observations:
            grouped.setdefault(item.family, []).append(item)
        summaries: list[FamilySummary] = []
        for family in sorted(grouped, key=lambda item: item.value):
            items = grouped[family]
            centrals = tuple(
                sorted(item.central_per_share for item in items)
            )
            summaries.append(
                FamilySummary(
                    family=family,
                    observation_ids=tuple(
                        sorted(item.observation_id for item in items)
                    ),
                    low_per_share=min(
                        item.low_per_share for item in items
                    ),
                    central_per_share=_median(centrals),
                    high_per_share=max(
                        item.high_per_share for item in items
                    ),
                )
            )
        return tuple(summaries)

    @staticmethod
    def _has_cross_family_pair_overlap(
        families: tuple[FamilySummary, ...],
    ) -> bool:
        for index, left in enumerate(families):
            for right in families[index + 1:]:
                if (
                    max(left.low_per_share, right.low_per_share)
                    <= min(left.high_per_share, right.high_per_share)
                ):
                    return True
        return False


def _median(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        raise ValueError("median requires values")
    size = len(values)
    middle = size // 2
    if size % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / Decimal("2")


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
