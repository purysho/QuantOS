from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class CompanyArchetype(str, Enum):
    GENERAL_OPERATING = "GENERAL_OPERATING"
    BANK = "BANK"
    INSURER = "INSURER"
    REIT = "REIT"
    PRE_REVENUE_BIOTECH = "PRE_REVENUE_BIOTECH"
    CONGLOMERATE = "CONGLOMERATE"
    NATURAL_RESOURCE = "NATURAL_RESOURCE"


class CashFlowVisibility(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ValuationMethod(str, Enum):
    FCFF_DCF = "FCFF_DCF"
    FCFE_DCF = "FCFE_DCF"
    DIVIDEND_DISCOUNT = "DIVIDEND_DISCOUNT"
    RESIDUAL_INCOME = "RESIDUAL_INCOME"
    TRADING_COMPS = "TRADING_COMPS"
    TRANSACTION_COMPS = "TRANSACTION_COMPS"
    SOTP = "SOTP"
    PROBABILITY_WEIGHTED_DCF = "PROBABILITY_WEIGHTED_DCF"
    NAV = "NAV"
    LIQUIDATION_VALUE = "LIQUIDATION_VALUE"
    REPLACEMENT_VALUE = "REPLACEMENT_VALUE"
    LBO = "LBO"


class MethodStatus(str, Enum):
    APPROPRIATE = "APPROPRIATE"
    CONDITIONAL = "CONDITIONAL"
    INAPPROPRIATE = "INAPPROPRIATE"


@dataclass(frozen=True)
class ValuationProfile:
    entity_id: str
    as_of: datetime
    archetype: CompanyArchetype
    cash_flow_visibility: CashFlowVisibility
    positive_fcff: bool
    positive_fcfe: bool
    material_dividend: bool
    regulatory_capital_central: bool
    has_segment_disclosure: bool
    segment_economics_divergent: bool
    peer_set_available: bool
    going_concern_uncertainty: bool
    distressed: bool
    stable_leverage_capacity: bool
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.entity_id.strip():
            raise ValueError("entity_id is required")
        if self.as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("valuation profile requires evidence references")


@dataclass(frozen=True)
class MethodCondition:
    code: str
    description: str


@dataclass(frozen=True)
class MethodAssessment:
    method: ValuationMethod
    status: MethodStatus
    reasons: tuple[str, ...]
    conditions: tuple[MethodCondition, ...] = ()


@dataclass(frozen=True)
class ValuationMethodologyAssessment:
    profile_fingerprint: str
    assessment_fingerprint: str
    assessments: tuple[MethodAssessment, ...]

    def for_method(self, method: ValuationMethod) -> MethodAssessment:
        for item in self.assessments:
            if item.method is method:
                return item
        raise KeyError(method)


@dataclass(frozen=True)
class MethodPermit:
    permit_id: str
    assessment_fingerprint: str
    method: ValuationMethod
    condition_evidence: tuple[tuple[str, tuple[str, ...]], ...]

    def __post_init__(self) -> None:
        expected = make_method_permit_id(
            assessment_fingerprint=self.assessment_fingerprint,
            method=self.method,
            condition_evidence=self.condition_evidence,
        )
        if self.permit_id != expected:
            raise ValueError("method permit identity does not match contents")


def profile_fingerprint(profile: ValuationProfile) -> str:
    payload = {
        "entity_id": profile.entity_id,
        "as_of": profile.as_of.isoformat(),
        "archetype": profile.archetype.value,
        "cash_flow_visibility": profile.cash_flow_visibility.value,
        "positive_fcff": profile.positive_fcff,
        "positive_fcfe": profile.positive_fcfe,
        "material_dividend": profile.material_dividend,
        "regulatory_capital_central": profile.regulatory_capital_central,
        "has_segment_disclosure": profile.has_segment_disclosure,
        "segment_economics_divergent": profile.segment_economics_divergent,
        "peer_set_available": profile.peer_set_available,
        "going_concern_uncertainty": profile.going_concern_uncertainty,
        "distressed": profile.distressed,
        "stable_leverage_capacity": profile.stable_leverage_capacity,
        "evidence_references": list(profile.evidence_references),
    }
    return _content_id("valuation-profile", payload)


def make_assessment_fingerprint(
    *,
    profile_fp: str,
    assessments: tuple[MethodAssessment, ...],
) -> str:
    payload = {
        "profile_fingerprint": profile_fp,
        "assessments": [
            {
                "method": item.method.value,
                "status": item.status.value,
                "reasons": list(item.reasons),
                "conditions": [
                    {"code": condition.code, "description": condition.description}
                    for condition in item.conditions
                ],
            }
            for item in assessments
        ],
    }
    return _content_id("valuation-methodology", payload)


def make_method_permit_id(
    *,
    assessment_fingerprint: str,
    method: ValuationMethod,
    condition_evidence: tuple[tuple[str, tuple[str, ...]], ...],
) -> str:
    payload = {
        "assessment_fingerprint": assessment_fingerprint,
        "method": method.value,
        "condition_evidence": [
            {"code": code, "references": list(refs)}
            for code, refs in condition_evidence
        ],
    }
    return _content_id("valuation-method-permit", payload)


class ValuationMethodologyEngine:
    """Method suitability with explicit reasons instead of a numeric score."""

    def assess(self, profile: ValuationProfile) -> ValuationMethodologyAssessment:
        profile_fp = profile_fingerprint(profile)
        assessments = tuple(
            self._assess_method(profile, method) for method in ValuationMethod
        )
        return ValuationMethodologyAssessment(
            profile_fingerprint=profile_fp,
            assessment_fingerprint=make_assessment_fingerprint(
                profile_fp=profile_fp,
                assessments=assessments,
            ),
            assessments=assessments,
        )

    def _assess_method(
        self,
        profile: ValuationProfile,
        method: ValuationMethod,
    ) -> MethodAssessment:
        if method is ValuationMethod.FCFF_DCF:
            return self._fcff(profile)
        if method is ValuationMethod.FCFE_DCF:
            return self._fcfe(profile)
        if method is ValuationMethod.DIVIDEND_DISCOUNT:
            return self._dividend(profile)
        if method is ValuationMethod.RESIDUAL_INCOME:
            return self._residual_income(profile)
        if method is ValuationMethod.TRADING_COMPS:
            return self._trading_comps(profile)
        if method is ValuationMethod.TRANSACTION_COMPS:
            return MethodAssessment(
                method=method,
                status=MethodStatus.CONDITIONAL,
                reasons=(
                    "Transaction comparability depends on control premium, cycle, financing and deal context.",
                ),
                conditions=(
                    MethodCondition(
                        "TRANSACTION_SET_VALIDATED",
                        "Validate transaction comparability and normalize control and synergy effects.",
                    ),
                ),
            )
        if method is ValuationMethod.SOTP:
            return self._sotp(profile)
        if method is ValuationMethod.PROBABILITY_WEIGHTED_DCF:
            return self._probability_weighted(profile)
        if method is ValuationMethod.NAV:
            return self._nav(profile)
        if method is ValuationMethod.LIQUIDATION_VALUE:
            return self._liquidation(profile)
        if method is ValuationMethod.REPLACEMENT_VALUE:
            return MethodAssessment(
                method=method,
                status=MethodStatus.CONDITIONAL,
                reasons=(
                    "Replacement value is useful only where asset replacement economics constrain value.",
                ),
                conditions=(
                    MethodCondition(
                        "REPLACEMENT_ECONOMICS_SUPPORTED",
                        "Document why replacement economics are decision-relevant.",
                    ),
                ),
            )
        if method is ValuationMethod.LBO:
            return self._lbo(profile)
        raise AssertionError(method)

    @staticmethod
    def _fcff(profile: ValuationProfile) -> MethodAssessment:
        if profile.archetype in {
            CompanyArchetype.BANK,
            CompanyArchetype.INSURER,
        } or profile.regulatory_capital_central:
            return MethodAssessment(
                method=ValuationMethod.FCFF_DCF,
                status=MethodStatus.INAPPROPRIATE,
                reasons=(
                    "Enterprise-value FCFF can misclassify funding that is integral to regulated financial operations.",
                ),
            )
        if profile.archetype is CompanyArchetype.PRE_REVENUE_BIOTECH:
            return MethodAssessment(
                method=ValuationMethod.FCFF_DCF,
                status=MethodStatus.CONDITIONAL,
                reasons=(
                    "A single deterministic FCFF path can conceal binary development and approval outcomes.",
                ),
                conditions=(
                    MethodCondition(
                        "BINARY_OUTCOMES_MODELED",
                        "Model material development and approval outcomes explicitly.",
                    ),
                ),
            )
        conditions: list[MethodCondition] = []
        if profile.cash_flow_visibility is CashFlowVisibility.LOW:
            conditions.append(
                MethodCondition(
                    "FORECAST_VISIBILITY_JUSTIFIED",
                    "Support why explicit forecast cash flows are decision-useful despite low visibility.",
                )
            )
        if profile.distressed or profile.going_concern_uncertainty:
            conditions.append(
                MethodCondition(
                    "GOING_CONCERN_CASE_SUPPORTED",
                    "Support the going-concern path and cross-check liquidation or restructuring value.",
                )
            )
        if not profile.positive_fcff:
            conditions.append(
                MethodCondition(
                    "NEGATIVE_FCFF_PATH_SUPPORTED",
                    "Explain the path from negative FCFF to sustainable terminal economics.",
                )
            )
        return MethodAssessment(
            method=ValuationMethod.FCFF_DCF,
            status=MethodStatus.CONDITIONAL if conditions else MethodStatus.APPROPRIATE,
            reasons=(
                "FCFF is structurally compatible with a non-financial operating business.",
            ),
            conditions=tuple(conditions),
        )

    @staticmethod
    def _fcfe(profile: ValuationProfile) -> MethodAssessment:
        if profile.archetype in {
            CompanyArchetype.BANK,
            CompanyArchetype.INSURER,
        } or profile.regulatory_capital_central:
            conditions = ()
            if not profile.positive_fcfe:
                conditions = (
                    MethodCondition(
                        "DISTRIBUTABLE_EQUITY_CASH_FLOW_SUPPORTED",
                        "Support the path to distributable equity cash flow under capital constraints.",
                    ),
                )
            return MethodAssessment(
                method=ValuationMethod.FCFE_DCF,
                status=MethodStatus.CONDITIONAL if conditions else MethodStatus.APPROPRIATE,
                reasons=(
                    "Equity cash flow aligns with businesses where regulatory capital and funding are operating constraints.",
                ),
                conditions=conditions,
            )
        return MethodAssessment(
            method=ValuationMethod.FCFE_DCF,
            status=MethodStatus.CONDITIONAL,
            reasons=("FCFE requires an explicit sustainable financing policy.",),
            conditions=(
                MethodCondition(
                    "FINANCING_POLICY_SUPPORTED",
                    "Document debt issuance, repayment and target leverage policy.",
                ),
            ),
        )

    @staticmethod
    def _dividend(profile: ValuationProfile) -> MethodAssessment:
        if not profile.material_dividend:
            return MethodAssessment(
                method=ValuationMethod.DIVIDEND_DISCOUNT,
                status=MethodStatus.INAPPROPRIATE,
                reasons=(
                    "Current distributions are not a material representation of equity cash generation.",
                ),
            )
        return MethodAssessment(
            method=ValuationMethod.DIVIDEND_DISCOUNT,
            status=(
                MethodStatus.APPROPRIATE
                if profile.regulatory_capital_central
                else MethodStatus.CONDITIONAL
            ),
            reasons=(
                "Observed distributions can anchor equity value when payout policy is economically meaningful.",
            ),
            conditions=(
                ()
                if profile.regulatory_capital_central
                else (
                    MethodCondition(
                        "PAYOUT_POLICY_STABLE",
                        "Support that payout policy represents sustainable distributable cash.",
                    ),
                )
            ),
        )

    @staticmethod
    def _residual_income(profile: ValuationProfile) -> MethodAssessment:
        if profile.archetype in {
            CompanyArchetype.BANK,
            CompanyArchetype.INSURER,
        }:
            return MethodAssessment(
                method=ValuationMethod.RESIDUAL_INCOME,
                status=MethodStatus.APPROPRIATE,
                reasons=(
                    "Book equity and return on equity are central anchors for regulated financial institutions.",
                ),
            )
        return MethodAssessment(
            method=ValuationMethod.RESIDUAL_INCOME,
            status=MethodStatus.CONDITIONAL,
            reasons=(
                "Residual income requires accounting book values to be economically informative.",
            ),
            conditions=(
                MethodCondition(
                    "BOOK_VALUE_QUALITY_REVIEWED",
                    "Review write-offs, intangibles and accounting distortions.",
                ),
            ),
        )

    @staticmethod
    def _trading_comps(profile: ValuationProfile) -> MethodAssessment:
        if profile.peer_set_available:
            return MethodAssessment(
                method=ValuationMethod.TRADING_COMPS,
                status=MethodStatus.APPROPRIATE,
                reasons=(
                    "A candidate peer set exists, subject to reproducible peer-selection and normalization controls.",
                ),
            )
        return MethodAssessment(
            method=ValuationMethod.TRADING_COMPS,
            status=MethodStatus.CONDITIONAL,
            reasons=("No validated peer set is currently available.",),
            conditions=(
                MethodCondition(
                    "PEER_SET_VALIDATED",
                    "Build and document a reproducible economically comparable peer set.",
                ),
            ),
        )

    @staticmethod
    def _sotp(profile: ValuationProfile) -> MethodAssessment:
        if (
            profile.archetype is CompanyArchetype.CONGLOMERATE
            and profile.has_segment_disclosure
            and profile.segment_economics_divergent
        ):
            return MethodAssessment(
                method=ValuationMethod.SOTP,
                status=MethodStatus.APPROPRIATE,
                reasons=(
                    "Distinct disclosed segments have different economics and justify method-by-segment valuation.",
                ),
            )
        if profile.has_segment_disclosure and profile.segment_economics_divergent:
            return MethodAssessment(
                method=ValuationMethod.SOTP,
                status=MethodStatus.CONDITIONAL,
                reasons=(
                    "Segment economics differ, but separability and corporate allocations require review.",
                ),
                conditions=(
                    MethodCondition(
                        "SEGMENT_SEPARABILITY_SUPPORTED",
                        "Support segment economics, intercompany items and corporate-cost allocation.",
                    ),
                ),
            )
        return MethodAssessment(
            method=ValuationMethod.SOTP,
            status=MethodStatus.INAPPROPRIATE,
            reasons=(
                "The profile does not establish separable divergent segment economics.",
            ),
        )

    @staticmethod
    def _probability_weighted(profile: ValuationProfile) -> MethodAssessment:
        if profile.archetype is CompanyArchetype.PRE_REVENUE_BIOTECH:
            return MethodAssessment(
                method=ValuationMethod.PROBABILITY_WEIGHTED_DCF,
                status=MethodStatus.APPROPRIATE,
                reasons=(
                    "Material binary development outcomes should be represented explicitly.",
                ),
            )
        return MethodAssessment(
            method=ValuationMethod.PROBABILITY_WEIGHTED_DCF,
            status=MethodStatus.CONDITIONAL,
            reasons=(
                "Probability-weighted valuation is useful when discrete outcome states are material and evidence-backed.",
            ),
            conditions=(
                MethodCondition(
                    "DISCRETE_OUTCOMES_SUPPORTED",
                    "Identify material discrete outcomes and evidence for their probabilities.",
                ),
            ),
        )

    @staticmethod
    def _nav(profile: ValuationProfile) -> MethodAssessment:
        if profile.archetype in {
            CompanyArchetype.REIT,
            CompanyArchetype.NATURAL_RESOURCE,
        }:
            return MethodAssessment(
                method=ValuationMethod.NAV,
                status=MethodStatus.APPROPRIATE,
                reasons=(
                    "Asset-level value is a central economic anchor for this business archetype.",
                ),
            )
        return MethodAssessment(
            method=ValuationMethod.NAV,
            status=MethodStatus.CONDITIONAL,
            reasons=(
                "NAV is informative only where separable asset values constrain enterprise value.",
            ),
            conditions=(
                MethodCondition(
                    "ASSET_VALUES_DECISION_RELEVANT",
                    "Document why asset-level values provide a meaningful valuation anchor.",
                ),
            ),
        )

    @staticmethod
    def _liquidation(profile: ValuationProfile) -> MethodAssessment:
        if profile.distressed or profile.going_concern_uncertainty:
            return MethodAssessment(
                method=ValuationMethod.LIQUIDATION_VALUE,
                status=MethodStatus.APPROPRIATE,
                reasons=(
                    "Downside value must explicitly consider non-going-concern outcomes.",
                ),
            )
        return MethodAssessment(
            method=ValuationMethod.LIQUIDATION_VALUE,
            status=MethodStatus.CONDITIONAL,
            reasons=(
                "Liquidation value is primarily a downside analysis for a solvent going concern.",
            ),
            conditions=(
                MethodCondition(
                    "LIQUIDATION_USE_CASE_DEFINED",
                    "Document the downside scenario in which liquidation value is decision-relevant.",
                ),
            ),
        )

    @staticmethod
    def _lbo(profile: ValuationProfile) -> MethodAssessment:
        if (
            profile.stable_leverage_capacity
            and profile.cash_flow_visibility is not CashFlowVisibility.LOW
            and not profile.regulatory_capital_central
            and not profile.distressed
        ):
            return MethodAssessment(
                method=ValuationMethod.LBO,
                status=MethodStatus.CONDITIONAL,
                reasons=(
                    "Cash flow and leverage capacity may support sponsor-return analysis.",
                ),
                conditions=(
                    MethodCondition(
                        "LBO_TERMS_SUPPORTED",
                        "Provide evidence-backed entry, financing, cash-sweep and exit assumptions.",
                    ),
                ),
            )
        return MethodAssessment(
            method=ValuationMethod.LBO,
            status=MethodStatus.INAPPROPRIATE,
            reasons=(
                "The profile does not establish stable leverage capacity and sufficiently visible cash flow.",
            ),
        )


class ValuationMethodologyGate:
    def issue(
        self,
        *,
        assessment: ValuationMethodologyAssessment,
        method: ValuationMethod,
        condition_evidence: dict[str, tuple[str, ...]] | None = None,
    ) -> MethodPermit:
        method_assessment = assessment.for_method(method)
        if method_assessment.status is MethodStatus.INAPPROPRIATE:
            raise ValueError(
                f"{method.value} is inappropriate for the current valuation profile"
            )
        supplied = condition_evidence or {}
        required_codes = {item.code for item in method_assessment.conditions}
        if set(supplied) - required_codes:
            raise ValueError("condition evidence contains unknown condition codes")
        for condition in method_assessment.conditions:
            refs = supplied.get(condition.code, ())
            if not refs or not all(item.strip() for item in refs):
                raise ValueError(
                    f"missing evidence for valuation condition: {condition.code}"
                )
        normalized = tuple(
            sorted(
                (
                    code,
                    tuple(sorted(reference.strip() for reference in refs)),
                )
                for code, refs in supplied.items()
            )
        )
        return MethodPermit(
            permit_id=make_method_permit_id(
                assessment_fingerprint=assessment.assessment_fingerprint,
                method=method,
                condition_evidence=normalized,
            ),
            assessment_fingerprint=assessment.assessment_fingerprint,
            method=method,
            condition_evidence=normalized,
        )

    @staticmethod
    def validate(
        *,
        assessment: ValuationMethodologyAssessment,
        permit: MethodPermit,
        required_method: ValuationMethod,
    ) -> None:
        if permit.method is not required_method:
            raise ValueError(
                f"valuation permit is for {permit.method.value}, not {required_method.value}"
            )
        if permit.assessment_fingerprint != assessment.assessment_fingerprint:
            raise ValueError("valuation method permit is stale for this assessment")
        current = assessment.for_method(required_method)
        if current.status is MethodStatus.INAPPROPRIATE:
            raise ValueError("current methodology assessment blocks this method")
        required_codes = {item.code for item in current.conditions}
        supplied = {code: refs for code, refs in permit.condition_evidence}
        if set(supplied) != required_codes:
            raise ValueError("valuation method permit conditions are incomplete or stale")
        if any(not refs for refs in supplied.values()):
            raise ValueError("valuation method permit contains empty evidence")


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
