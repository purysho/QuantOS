from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum

from .research_universe import InvestableUniverse


class CrossSectionTransform(str, Enum):
    RAW = "RAW"
    PERCENTILE_RANK = "PERCENTILE_RANK"


@dataclass(frozen=True)
class FactorComponent:
    feature_name: str
    lookback_periods: int
    minimum_lag_seconds: int
    maximum_staleness_seconds: int
    weight: Decimal
    transform: CrossSectionTransform

    def __post_init__(self) -> None:
        if not self.feature_name.strip():
            raise ValueError("factor component feature_name is required")
        if self.lookback_periods < 1:
            raise ValueError("lookback_periods must be >= 1")
        if self.minimum_lag_seconds < 0:
            raise ValueError("minimum_lag_seconds must be non-negative")
        if self.maximum_staleness_seconds < 0:
            raise ValueError("maximum_staleness_seconds must be non-negative")
        if not self.weight.is_finite() or self.weight == 0:
            raise ValueError("factor component weight must be finite and non-zero")


@dataclass(frozen=True)
class FactorSpecification:
    name: str
    version: str
    universe_policy_id: str
    components: tuple[FactorComponent, ...]
    rationale: str
    evidence_references: tuple[str, ...]
    pinned_universe_id: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.version.strip():
            raise ValueError("factor name and version are required")
        if not self.universe_policy_id.startswith("universe-policy:"):
            raise ValueError("factor must bind to a universe-policy ID")
        if (
            self.pinned_universe_id is not None
            and not self.pinned_universe_id.startswith("investable-universe:")
        ):
            raise ValueError("pinned_universe_id must be an investable-universe ID")
        if not self.components:
            raise ValueError("factor requires at least one component")
        names = [item.feature_name for item in self.components]
        if len(names) != len(set(names)):
            raise ValueError("factor feature names must be unique")
        total_abs_weight = sum(
            (abs(item.weight) for item in self.components),
            Decimal("0"),
        )
        if total_abs_weight != Decimal("1"):
            raise ValueError("sum of absolute factor weights must equal 1")
        if not self.rationale.strip():
            raise ValueError("factor rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("factor specification requires evidence references")

    @property
    def factor_id(self) -> str:
        return _content_id(
            "factor-spec",
            {
                "name": self.name,
                "version": self.version,
                "universe_policy_id": self.universe_policy_id,
                "pinned_universe_id": self.pinned_universe_id,
                "components": [
                    {
                        "feature_name": item.feature_name,
                        "lookback_periods": item.lookback_periods,
                        "minimum_lag_seconds": item.minimum_lag_seconds,
                        "maximum_staleness_seconds": (
                            item.maximum_staleness_seconds
                        ),
                        "weight": str(item.weight),
                        "transform": item.transform.value,
                    }
                    for item in self.components
                ],
                "rationale": self.rationale,
                "evidence_references": list(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class FeatureObservation:
    security_id: str
    feature_name: str
    feature_end_time: datetime
    knowledge_time: datetime
    lookback_periods: int
    value: Decimal
    source_fact_ids: tuple[str, ...]
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.security_id.strip() or not self.feature_name.strip():
            raise ValueError("feature security_id and feature_name are required")
        if (
            self.feature_end_time.tzinfo is None
            or self.knowledge_time.tzinfo is None
        ):
            raise ValueError("feature timestamps must be timezone-aware")
        if self.knowledge_time < self.feature_end_time:
            raise ValueError("feature knowledge_time cannot precede feature_end_time")
        if self.lookback_periods < 1:
            raise ValueError("feature lookback_periods must be >= 1")
        if not self.value.is_finite():
            raise ValueError("feature value must be finite")
        if not self.source_fact_ids or not all(
            item.strip() for item in self.source_fact_ids
        ):
            raise ValueError("feature requires source fact IDs")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("feature requires evidence references")

    @property
    def observation_id(self) -> str:
        return _content_id(
            "feature-observation",
            {
                "security_id": self.security_id,
                "feature_name": self.feature_name,
                "feature_end_time": self.feature_end_time.isoformat(),
                "knowledge_time": self.knowledge_time.isoformat(),
                "lookback_periods": self.lookback_periods,
                "value": str(self.value),
                "source_fact_ids": list(self.source_fact_ids),
                "evidence_references": list(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class ComponentValue:
    feature_name: str
    observation_id: str
    raw_value: Decimal
    transformed_value: Decimal
    weight: Decimal
    contribution: Decimal


@dataclass(frozen=True)
class FactorScore:
    security_id: str
    score: Decimal
    components: tuple[ComponentValue, ...]


@dataclass(frozen=True)
class FactorExclusion:
    security_id: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class FactorRun:
    run_id: str
    factor_id: str
    universe_id: str
    decision_time: datetime
    scores: tuple[FactorScore, ...]
    exclusions: tuple[FactorExclusion, ...]


class FactorEngine:
    """Cross-sectional factor scorer with strict point-in-time feature gates."""

    def run(
        self,
        *,
        specification: FactorSpecification,
        universe: InvestableUniverse,
        decision_time: datetime,
        features: tuple[FeatureObservation, ...],
    ) -> FactorRun:
        if decision_time.tzinfo is None:
            raise ValueError("factor decision_time must be timezone-aware")
        if specification.universe_policy_id != universe.policy_id:
            raise ValueError("factor specification is bound to another universe policy")
        if (
            specification.pinned_universe_id is not None
            and specification.pinned_universe_id != universe.universe_id
        ):
            raise ValueError("factor specification is pinned to another universe")
        if universe.as_of > decision_time:
            raise ValueError("factor decision_time cannot precede universe as_of")
        security_ids = universe.included_security_ids
        if not security_ids:
            raise ValueError("factor universe contains no included securities")
        if len({item.observation_id for item in features}) != len(features):
            raise ValueError("duplicate feature observations are not allowed")

        selected: dict[tuple[str, str], FeatureObservation] = {}
        missing: dict[str, list[str]] = {security_id: [] for security_id in security_ids}
        components_by_name = {
            item.feature_name: item for item in specification.components
        }

        for security_id in security_ids:
            for component in specification.components:
                observation = self._select_feature(
                    security_id=security_id,
                    component=component,
                    decision_time=decision_time,
                    features=features,
                )
                if observation is None:
                    missing[security_id].append(
                        f"MISSING_FEATURE:{component.feature_name}"
                    )
                else:
                    selected[(security_id, component.feature_name)] = observation

        eligible = tuple(
            security_id
            for security_id in security_ids
            if not missing[security_id]
        )
        transformed: dict[tuple[str, str], Decimal] = {}
        for component in specification.components:
            values = tuple(
                (
                    security_id,
                    selected[(security_id, component.feature_name)].value,
                )
                for security_id in eligible
            )
            if component.transform is CrossSectionTransform.RAW:
                for security_id, value in values:
                    transformed[(security_id, component.feature_name)] = value
            else:
                ranks = _percentile_ranks(values)
                for security_id, value in ranks.items():
                    transformed[(security_id, component.feature_name)] = value

        scores: list[FactorScore] = []
        for security_id in eligible:
            component_values: list[ComponentValue] = []
            score = Decimal("0")
            for component in specification.components:
                observation = selected[
                    (security_id, component.feature_name)
                ]
                transformed_value = transformed[
                    (security_id, component.feature_name)
                ]
                contribution = transformed_value * component.weight
                component_values.append(
                    ComponentValue(
                        feature_name=component.feature_name,
                        observation_id=observation.observation_id,
                        raw_value=observation.value,
                        transformed_value=transformed_value,
                        weight=component.weight,
                        contribution=contribution,
                    )
                )
                score += contribution
            scores.append(
                FactorScore(
                    security_id=security_id,
                    score=score,
                    components=tuple(component_values),
                )
            )

        exclusions = tuple(
            FactorExclusion(
                security_id=security_id,
                reasons=tuple(missing[security_id]),
            )
            for security_id in security_ids
            if missing[security_id]
        )
        scores_tuple = tuple(sorted(scores, key=lambda item: item.security_id))
        payload = {
            "factor_id": specification.factor_id,
            "universe_id": universe.universe_id,
            "decision_time": decision_time.isoformat(),
            "scores": [
                {
                    "security_id": item.security_id,
                    "score": str(item.score),
                    "components": [
                        {
                            "feature_name": component.feature_name,
                            "observation_id": component.observation_id,
                            "raw_value": str(component.raw_value),
                            "transformed_value": str(
                                component.transformed_value
                            ),
                            "weight": str(component.weight),
                            "contribution": str(component.contribution),
                        }
                        for component in item.components
                    ],
                }
                for item in scores_tuple
            ],
            "exclusions": [
                {
                    "security_id": item.security_id,
                    "reasons": list(item.reasons),
                }
                for item in exclusions
            ],
        }
        return FactorRun(
            run_id=_content_id("factor-run", payload),
            factor_id=specification.factor_id,
            universe_id=universe.universe_id,
            decision_time=decision_time,
            scores=scores_tuple,
            exclusions=exclusions,
        )

    @staticmethod
    def _select_feature(
        *,
        security_id: str,
        component: FactorComponent,
        decision_time: datetime,
        features: tuple[FeatureObservation, ...],
    ) -> FeatureObservation | None:
        cutoff = decision_time - timedelta(
            seconds=component.minimum_lag_seconds
        )
        candidates = tuple(
            item
            for item in features
            if item.security_id == security_id
            and item.feature_name == component.feature_name
            and item.lookback_periods == component.lookback_periods
            and item.feature_end_time <= cutoff
            and item.knowledge_time <= cutoff
            and (
                decision_time - item.feature_end_time
            ).total_seconds()
            <= component.maximum_staleness_seconds
        )
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda item: (
                item.feature_end_time,
                item.knowledge_time,
                item.observation_id,
            ),
        )


def _percentile_ranks(
    values: tuple[tuple[str, Decimal], ...],
) -> dict[str, Decimal]:
    if not values:
        return {}
    if len(values) == 1:
        return {values[0][0]: Decimal("0.5")}
    ordered = sorted(values, key=lambda item: (item[1], item[0]))
    output: dict[str, Decimal] = {}
    index = 0
    denominator = Decimal(len(ordered) - 1)
    while index < len(ordered):
        value = ordered[index][1]
        end = index
        while end + 1 < len(ordered) and ordered[end + 1][1] == value:
            end += 1
        average_rank = (
            Decimal(index) + Decimal(end)
        ) / Decimal("2")
        percentile = average_rank / denominator
        for position in range(index, end + 1):
            output[ordered[position][0]] = percentile
        index = end + 1
    return output


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()