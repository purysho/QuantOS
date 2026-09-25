from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path

import duckdb

from .covariance import CovarianceArtifact
from .portfolio_comparison import (
    PortfolioComparisonDossier,
    PortfolioComparisonEngine,
    PortfolioComparisonFold,
    PortfolioMethod,
)
from .portfolio_construction import (
    PortfolioConstraintPolicy,
    PortfolioWeight,
)
from .portfolio_hierarchical import HierarchicalPortfolioSolution
from .portfolio_optimization import (
    OptimizedPortfolioSolution,
    optimized_portfolio_solution_identity,
)


class PortfolioRobustnessState(str, Enum):
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    WITHIN_POLICY = "WITHIN_POLICY"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


@dataclass(frozen=True)
class PortfolioRobustnessPolicy:
    minimum_folds: int
    maximum_adjacent_weight_turnover: Decimal
    minimum_cluster_pair_agreement: Decimal
    minimum_covariance_sensitivity_observations: int
    maximum_covariance_sensitivity_turnover: Decimal
    minimum_upper_weight_headroom: Decimal
    minimum_turnover_headroom: Decimal
    maximum_covariance_condition_number: Decimal | None
    allow_optimal_inaccurate: bool
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.minimum_folds < 2:
            raise ValueError("robustness policy requires at least two folds")
        if self.minimum_covariance_sensitivity_observations < 0:
            raise ValueError(
                "minimum covariance sensitivity observations cannot be negative"
            )
        for name in (
            "maximum_adjacent_weight_turnover",
            "minimum_cluster_pair_agreement",
            "maximum_covariance_sensitivity_turnover",
            "minimum_upper_weight_headroom",
            "minimum_turnover_headroom",
        ):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.maximum_adjacent_weight_turnover > 1:
            raise ValueError(
                "maximum_adjacent_weight_turnover cannot exceed 1"
            )
        if self.minimum_cluster_pair_agreement > 1:
            raise ValueError(
                "minimum_cluster_pair_agreement cannot exceed 1"
            )
        if self.maximum_covariance_sensitivity_turnover > 1:
            raise ValueError(
                "maximum_covariance_sensitivity_turnover cannot exceed 1"
            )
        if self.maximum_covariance_condition_number is not None:
            if (
                not self.maximum_covariance_condition_number.is_finite()
                or self.maximum_covariance_condition_number <= 0
            ):
                raise ValueError(
                    "maximum covariance condition number must be positive"
                )
        if not self.rationale.strip():
            raise ValueError("robustness policy rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("robustness policy requires evidence references")

    @property
    def policy_id(self) -> str:
        return _content_id(
            "portfolio-robustness-policy",
            {
                "minimum_folds": self.minimum_folds,
                "maximum_adjacent_weight_turnover": str(
                    self.maximum_adjacent_weight_turnover
                ),
                "minimum_cluster_pair_agreement": str(
                    self.minimum_cluster_pair_agreement
                ),
                "minimum_covariance_sensitivity_observations": (
                    self.minimum_covariance_sensitivity_observations
                ),
                "maximum_covariance_sensitivity_turnover": str(
                    self.maximum_covariance_sensitivity_turnover
                ),
                "minimum_upper_weight_headroom": str(
                    self.minimum_upper_weight_headroom
                ),
                "minimum_turnover_headroom": str(
                    self.minimum_turnover_headroom
                ),
                "maximum_covariance_condition_number": (
                    str(self.maximum_covariance_condition_number)
                    if self.maximum_covariance_condition_number is not None
                    else None
                ),
                "allow_optimal_inaccurate": self.allow_optimal_inaccurate,
                "rationale": self.rationale,
                "evidence_references": list(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class CovarianceSensitivityObservation:
    observation_id: str
    dataset_id: str
    reference_covariance_id: str
    alternate_covariance_id: str
    reference_estimator: str
    alternate_estimator: str
    reference_solution_id: str
    alternate_solution_id: str
    weight_turnover_distance: Decimal
    maximum_condition_number: Decimal


@dataclass(frozen=True)
class MethodWeightStability:
    method: PortfolioMethod
    adjacent_turnovers: tuple[Decimal, ...]
    average_adjacent_turnover: Decimal
    maximum_adjacent_turnover: Decimal


@dataclass(frozen=True)
class HierarchicalClusterStability:
    method: PortfolioMethod
    adjacent_pair_agreements: tuple[Decimal, ...]
    minimum_pair_agreement: Decimal
    average_pair_agreement: Decimal


@dataclass(frozen=True)
class ConstraintFragility:
    minimum_upper_weight_headroom: Decimal
    minimum_turnover_headroom: Decimal | None
    optimal_inaccurate_count: int


@dataclass(frozen=True)
class PortfolioRobustnessDossier:
    dossier_id: str
    model_id: str
    manifest_id: str
    comparison_dossier_id: str
    constraint_policy_id: str
    robustness_policy_id: str
    state: PortfolioRobustnessState
    method_weight_stability: tuple[MethodWeightStability, ...]
    cluster_stability: tuple[HierarchicalClusterStability, ...]
    covariance_sensitivity: tuple[CovarianceSensitivityObservation, ...]
    constraint_fragility: ConstraintFragility
    reasons: tuple[str, ...]
    selection_authority: str
    capital_authority: str
    caveat: str


class PortfolioRobustnessEngine:
    CAVEAT = (
        "Robustness diagnostics test stability and fragility of frozen portfolio "
        "construction artifacts. They do not select a method, predict future "
        "performance, or authorize capital."
    )

    def covariance_sensitivity(
        self,
        *,
        reference_covariance: CovarianceArtifact,
        alternate_covariance: CovarianceArtifact,
        reference_solution: OptimizedPortfolioSolution,
        alternate_solution: OptimizedPortfolioSolution,
    ) -> CovarianceSensitivityObservation:
        for solution in (reference_solution, alternate_solution):
            if (
                solution.solution_id
                != optimized_portfolio_solution_identity(solution)
            ):
                raise ValueError(
                    "covariance sensitivity solution identity mismatch"
                )
        if reference_covariance.artifact_id == alternate_covariance.artifact_id:
            raise ValueError(
                "covariance sensitivity requires distinct covariance artifacts"
            )
        if reference_covariance.estimator is alternate_covariance.estimator:
            raise ValueError(
                "covariance sensitivity requires distinct estimator families"
            )
        dataset_ids = {
            reference_covariance.dataset_id,
            alternate_covariance.dataset_id,
            reference_solution.dataset_id,
            alternate_solution.dataset_id,
        }
        if len(dataset_ids) != 1:
            raise ValueError(
                "covariance sensitivity inputs must share one dataset"
            )
        if (
            reference_solution.covariance_artifact_id
            != reference_covariance.artifact_id
            or alternate_solution.covariance_artifact_id
            != alternate_covariance.artifact_id
        ):
            raise ValueError(
                "optimizer solutions do not bind supplied covariance artifacts"
            )
        if (
            reference_solution.model_id != alternate_solution.model_id
            or reference_solution.manifest_id
            != alternate_solution.manifest_id
            or reference_solution.constraint_policy_id
            != alternate_solution.constraint_policy_id
            or reference_solution.optimization_policy_id
            != alternate_solution.optimization_policy_id
            or reference_solution.decision_time
            != alternate_solution.decision_time
        ):
            raise ValueError(
                "covariance sensitivity solutions differ beyond covariance"
            )

        distance = _weight_turnover_distance(
            reference_solution.weights,
            alternate_solution.weights,
        )
        condition = max(
            reference_covariance.condition_number,
            alternate_covariance.condition_number,
        )
        payload = {
            "dataset_id": reference_covariance.dataset_id,
            "reference_covariance_id": reference_covariance.artifact_id,
            "alternate_covariance_id": alternate_covariance.artifact_id,
            "reference_estimator": reference_covariance.estimator.value,
            "alternate_estimator": alternate_covariance.estimator.value,
            "reference_solution_id": reference_solution.solution_id,
            "alternate_solution_id": alternate_solution.solution_id,
            "weight_turnover_distance": str(distance),
            "maximum_condition_number": str(condition),
        }
        return CovarianceSensitivityObservation(
            observation_id=_content_id(
                "covariance-sensitivity-observation",
                payload,
            ),
            dataset_id=reference_covariance.dataset_id,
            reference_covariance_id=reference_covariance.artifact_id,
            alternate_covariance_id=alternate_covariance.artifact_id,
            reference_estimator=reference_covariance.estimator.value,
            alternate_estimator=alternate_covariance.estimator.value,
            reference_solution_id=reference_solution.solution_id,
            alternate_solution_id=alternate_solution.solution_id,
            weight_turnover_distance=distance,
            maximum_condition_number=condition,
        )

    def assess(
        self,
        *,
        comparison: PortfolioComparisonDossier,
        folds: tuple[PortfolioComparisonFold, ...],
        constraints: PortfolioConstraintPolicy,
        policy: PortfolioRobustnessPolicy,
        covariance_sensitivity: tuple[
            CovarianceSensitivityObservation, ...
        ] = (),
    ) -> PortfolioRobustnessDossier:
        if comparison.selection_authority != "NONE":
            raise ValueError("comparison unexpectedly carries selection authority")
        if comparison.capital_authority != "NONE":
            raise ValueError("comparison unexpectedly carries capital authority")
        if comparison.constraint_policy_id != constraints.policy_id:
            raise ValueError("robustness constraints differ from comparison")
        if comparison.fold_ids != tuple(item.fold_id for item in folds):
            raise ValueError("robustness folds differ from comparison dossier")
        if len(folds) < policy.minimum_folds:
            return self._insufficient(
                comparison=comparison,
                constraints=constraints,
                policy=policy,
                reason="minimum robustness fold count not reached",
            )

        contexts = tuple(
            PortfolioComparisonEngine._validate_fold(item)
            for item in folds
        )
        if any(
            item["model_id"] != comparison.model_id
            or item["manifest_id"] != comparison.manifest_id
            for item in contexts
        ):
            raise ValueError(
                "robustness fold lineage differs from comparison dossier"
            )
        method_sets = tuple(
            frozenset(item["solutions"]) for item in contexts
        )
        if any(item != method_sets[0] for item in method_sets[1:]):
            raise ValueError(
                "robustness folds must contain the same portfolio methods"
            )
        active_methods = tuple(
            sorted(method_sets[0], key=lambda item: item.value)
        )

        weight_stability = tuple(
            self._weight_stability(
                method=method,
                solutions=tuple(
                    item["solutions"][method] for item in contexts
                ),
            )
            for method in active_methods
        )
        cluster_stability = tuple(
            self._cluster_stability(
                method=method,
                solutions=tuple(
                    item["solutions"][method] for item in contexts
                ),
            )
            for method in active_methods
            if method in {PortfolioMethod.HRP, PortfolioMethod.HERC}
        )
        fragility = self._constraint_fragility(
            contexts=contexts,
            constraints=constraints,
        )

        reasons: list[str] = []
        for item in weight_stability:
            if (
                item.maximum_adjacent_turnover
                > policy.maximum_adjacent_weight_turnover
            ):
                reasons.append(
                    f"{item.method.value} adjacent-fold weight instability "
                    "exceeded policy"
                )
        for item in cluster_stability:
            if (
                item.minimum_pair_agreement
                < policy.minimum_cluster_pair_agreement
            ):
                reasons.append(
                    f"{item.method.value} clustering stability fell below policy"
                )
        for item in covariance_sensitivity:
            if (
                item.weight_turnover_distance
                > policy.maximum_covariance_sensitivity_turnover
            ):
                reasons.append(
                    "covariance-estimator sensitivity exceeded policy"
                )
            if (
                policy.maximum_covariance_condition_number is not None
                and item.maximum_condition_number
                > policy.maximum_covariance_condition_number
            ):
                reasons.append(
                    "covariance condition number exceeded robustness policy"
                )
        if (
            fragility.minimum_upper_weight_headroom
            < policy.minimum_upper_weight_headroom
        ):
            reasons.append(
                "one or more candidate weights are too close to the upper bound"
            )
        if (
            fragility.minimum_turnover_headroom is not None
            and fragility.minimum_turnover_headroom
            < policy.minimum_turnover_headroom
        ):
            reasons.append(
                "one or more candidates are too close to the turnover limit"
            )
        if (
            fragility.optimal_inaccurate_count
            and not policy.allow_optimal_inaccurate
        ):
            reasons.append(
                "minimum-variance solver reported optimal_inaccurate"
            )

        insufficient_reasons: list[str] = []
        if (
            len(covariance_sensitivity)
            < policy.minimum_covariance_sensitivity_observations
        ):
            insufficient_reasons.append(
                "minimum covariance-sensitivity observation count not reached"
            )
        if reasons:
            state = PortfolioRobustnessState.REVIEW_REQUIRED
            final_reasons = tuple(sorted(set(reasons + insufficient_reasons)))
        elif insufficient_reasons:
            state = PortfolioRobustnessState.INSUFFICIENT_EVIDENCE
            final_reasons = tuple(insufficient_reasons)
        else:
            state = PortfolioRobustnessState.WITHIN_POLICY
            final_reasons = (
                "portfolio construction stability remains within frozen policy",
            )

        payload = {
            "model_id": comparison.model_id,
            "manifest_id": comparison.manifest_id,
            "comparison_dossier_id": comparison.dossier_id,
            "constraint_policy_id": constraints.policy_id,
            "robustness_policy_id": policy.policy_id,
            "state": state.value,
            "method_weight_stability": [
                {
                    "method": item.method.value,
                    "adjacent_turnovers": [
                        str(value) for value in item.adjacent_turnovers
                    ],
                    "average_adjacent_turnover": str(
                        item.average_adjacent_turnover
                    ),
                    "maximum_adjacent_turnover": str(
                        item.maximum_adjacent_turnover
                    ),
                }
                for item in weight_stability
            ],
            "cluster_stability": [
                {
                    "method": item.method.value,
                    "adjacent_pair_agreements": [
                        str(value)
                        for value in item.adjacent_pair_agreements
                    ],
                    "minimum_pair_agreement": str(
                        item.minimum_pair_agreement
                    ),
                    "average_pair_agreement": str(
                        item.average_pair_agreement
                    ),
                }
                for item in cluster_stability
            ],
            "covariance_sensitivity_ids": [
                item.observation_id for item in covariance_sensitivity
            ],
            "constraint_fragility": {
                "minimum_upper_weight_headroom": str(
                    fragility.minimum_upper_weight_headroom
                ),
                "minimum_turnover_headroom": (
                    str(fragility.minimum_turnover_headroom)
                    if fragility.minimum_turnover_headroom is not None
                    else None
                ),
                "optimal_inaccurate_count": fragility.optimal_inaccurate_count,
            },
            "reasons": list(final_reasons),
            "selection_authority": "NONE",
            "capital_authority": "NONE",
        }
        return PortfolioRobustnessDossier(
            dossier_id=_content_id("portfolio-robustness-dossier", payload),
            model_id=comparison.model_id,
            manifest_id=comparison.manifest_id,
            comparison_dossier_id=comparison.dossier_id,
            constraint_policy_id=constraints.policy_id,
            robustness_policy_id=policy.policy_id,
            state=state,
            method_weight_stability=weight_stability,
            cluster_stability=cluster_stability,
            covariance_sensitivity=covariance_sensitivity,
            constraint_fragility=fragility,
            reasons=final_reasons,
            selection_authority="NONE",
            capital_authority="NONE",
            caveat=self.CAVEAT,
        )

    @staticmethod
    def _weight_stability(
        *,
        method: PortfolioMethod,
        solutions,
    ) -> MethodWeightStability:
        adjacent = tuple(
            _weight_turnover_distance(left.weights, right.weights)
            for left, right in zip(solutions, solutions[1:])
        )
        if not adjacent:
            raise ValueError("weight stability requires adjacent solutions")
        return MethodWeightStability(
            method=method,
            adjacent_turnovers=adjacent,
            average_adjacent_turnover=_mean(adjacent),
            maximum_adjacent_turnover=max(adjacent),
        )

    @staticmethod
    def _cluster_stability(
        *,
        method: PortfolioMethod,
        solutions,
    ) -> HierarchicalClusterStability:
        if not all(
            isinstance(item, HierarchicalPortfolioSolution)
            for item in solutions
        ):
            raise ValueError(
                "cluster stability requires hierarchical solutions"
            )
        agreements = tuple(
            _cluster_pair_agreement(left, right)
            for left, right in zip(solutions, solutions[1:])
        )
        if not agreements:
            raise ValueError("cluster stability requires adjacent solutions")
        return HierarchicalClusterStability(
            method=method,
            adjacent_pair_agreements=agreements,
            minimum_pair_agreement=min(agreements),
            average_pair_agreement=_mean(agreements),
        )

    @staticmethod
    def _constraint_fragility(
        *,
        contexts,
        constraints: PortfolioConstraintPolicy,
    ) -> ConstraintFragility:
        upper_headrooms: list[Decimal] = []
        turnover_headrooms: list[Decimal] = []
        inaccurate = 0
        for context in contexts:
            for solution in context["solutions"].values():
                max_weight = max(
                    item.weight for item in solution.weights
                )
                upper_headrooms.append(
                    constraints.maximum_weight - max_weight
                )
                if constraints.maximum_one_way_turnover is not None:
                    turnover_headrooms.append(
                        constraints.maximum_one_way_turnover
                        - solution.one_way_turnover
                    )
                if (
                    isinstance(solution, OptimizedPortfolioSolution)
                    and solution.solver_status == "optimal_inaccurate"
                ):
                    inaccurate += 1
        return ConstraintFragility(
            minimum_upper_weight_headroom=min(upper_headrooms),
            minimum_turnover_headroom=(
                min(turnover_headrooms)
                if turnover_headrooms
                else None
            ),
            optimal_inaccurate_count=inaccurate,
        )

    def _insufficient(
        self,
        *,
        comparison: PortfolioComparisonDossier,
        constraints: PortfolioConstraintPolicy,
        policy: PortfolioRobustnessPolicy,
        reason: str,
    ) -> PortfolioRobustnessDossier:
        empty_fragility = ConstraintFragility(
            minimum_upper_weight_headroom=Decimal("0"),
            minimum_turnover_headroom=None,
            optimal_inaccurate_count=0,
        )
        payload = {
            "model_id": comparison.model_id,
            "manifest_id": comparison.manifest_id,
            "comparison_dossier_id": comparison.dossier_id,
            "constraint_policy_id": constraints.policy_id,
            "robustness_policy_id": policy.policy_id,
            "state": PortfolioRobustnessState.INSUFFICIENT_EVIDENCE.value,
            "reason": reason,
            "selection_authority": "NONE",
            "capital_authority": "NONE",
        }
        return PortfolioRobustnessDossier(
            dossier_id=_content_id("portfolio-robustness-dossier", payload),
            model_id=comparison.model_id,
            manifest_id=comparison.manifest_id,
            comparison_dossier_id=comparison.dossier_id,
            constraint_policy_id=constraints.policy_id,
            robustness_policy_id=policy.policy_id,
            state=PortfolioRobustnessState.INSUFFICIENT_EVIDENCE,
            method_weight_stability=(),
            cluster_stability=(),
            covariance_sensitivity=(),
            constraint_fragility=empty_fragility,
            reasons=(reason,),
            selection_authority="NONE",
            capital_authority="NONE",
            caveat=self.CAVEAT,
        )


class PortfolioRobustnessStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_robustness_dossiers (
                dossier_id VARCHAR PRIMARY KEY,
                model_id VARCHAR NOT NULL,
                manifest_id VARCHAR NOT NULL,
                comparison_dossier_id VARCHAR NOT NULL,
                constraint_policy_id VARCHAR NOT NULL,
                robustness_policy_id VARCHAR NOT NULL,
                state VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, dossier: PortfolioRobustnessDossier) -> bool:
        payload = json.dumps(
            _jsonable(dossier),
            sort_keys=True,
            separators=(",", ":"),
        )
        row = self._con.execute(
            """
            SELECT payload_json
            FROM portfolio_robustness_dossiers
            WHERE dossier_id = ?
            """,
            [dossier.dossier_id],
        ).fetchone()
        if row is not None:
            if str(row[0]) != payload:
                raise ValueError("portfolio robustness dossier identity conflict")
            return False
        self._con.execute(
            """
            INSERT INTO portfolio_robustness_dossiers
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                dossier.dossier_id,
                dossier.model_id,
                dossier.manifest_id,
                dossier.comparison_dossier_id,
                dossier.constraint_policy_id,
                dossier.robustness_policy_id,
                dossier.state.value,
                payload,
            ],
        )
        return True

    def close(self) -> None:
        self._con.close()


def _weight_turnover_distance(
    left: tuple[PortfolioWeight, ...],
    right: tuple[PortfolioWeight, ...],
) -> Decimal:
    left_map = {item.security_id: item.weight for item in left}
    right_map = {item.security_id: item.weight for item in right}
    return (
        sum(
            (
                abs(
                    left_map.get(security_id, Decimal("0"))
                    - right_map.get(security_id, Decimal("0"))
                )
                for security_id in set(left_map) | set(right_map)
            ),
            Decimal("0"),
        )
        / Decimal("2")
    )


def _cluster_pair_agreement(
    left: HierarchicalPortfolioSolution,
    right: HierarchicalPortfolioSolution,
) -> Decimal:
    left_labels = dict(
        zip(
            (item.security_id for item in left.weights),
            left.cluster_labels,
        )
    )
    right_labels = dict(
        zip(
            (item.security_id for item in right.weights),
            right.cluster_labels,
        )
    )
    common = tuple(sorted(set(left_labels) & set(right_labels)))
    if len(common) < 2:
        raise ValueError(
            "cluster stability requires at least two common securities"
        )
    agreements = 0
    comparisons = 0
    for index, first in enumerate(common):
        for second in common[index + 1 :]:
            left_same = left_labels[first] == left_labels[second]
            right_same = right_labels[first] == right_labels[second]
            agreements += int(left_same == right_same)
            comparisons += 1
    return Decimal(agreements) / Decimal(comparisons)


def _mean(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        raise ValueError("mean requires values")
    return sum(values, Decimal("0")) / Decimal(len(values))


def _jsonable(dossier: PortfolioRobustnessDossier) -> dict[str, object]:
    return {
        "dossier_id": dossier.dossier_id,
        "model_id": dossier.model_id,
        "manifest_id": dossier.manifest_id,
        "comparison_dossier_id": dossier.comparison_dossier_id,
        "constraint_policy_id": dossier.constraint_policy_id,
        "robustness_policy_id": dossier.robustness_policy_id,
        "state": dossier.state.value,
        "method_weight_stability": [
            {
                "method": item.method.value,
                "adjacent_turnovers": [
                    str(value) for value in item.adjacent_turnovers
                ],
                "average_adjacent_turnover": str(
                    item.average_adjacent_turnover
                ),
                "maximum_adjacent_turnover": str(
                    item.maximum_adjacent_turnover
                ),
            }
            for item in dossier.method_weight_stability
        ],
        "cluster_stability": [
            {
                "method": item.method.value,
                "adjacent_pair_agreements": [
                    str(value) for value in item.adjacent_pair_agreements
                ],
                "minimum_pair_agreement": str(
                    item.minimum_pair_agreement
                ),
                "average_pair_agreement": str(
                    item.average_pair_agreement
                ),
            }
            for item in dossier.cluster_stability
        ],
        "covariance_sensitivity": [
            {
                key: (
                    str(value)
                    if isinstance(value, Decimal)
                    else value
                )
                for key, value in item.__dict__.items()
            }
            for item in dossier.covariance_sensitivity
        ],
        "constraint_fragility": {
            "minimum_upper_weight_headroom": str(
                dossier.constraint_fragility.minimum_upper_weight_headroom
            ),
            "minimum_turnover_headroom": (
                str(dossier.constraint_fragility.minimum_turnover_headroom)
                if dossier.constraint_fragility.minimum_turnover_headroom
                is not None
                else None
            ),
            "optimal_inaccurate_count": (
                dossier.constraint_fragility.optimal_inaccurate_count
            ),
        },
        "reasons": list(dossier.reasons),
        "selection_authority": dossier.selection_authority,
        "capital_authority": dossier.capital_authority,
        "caveat": dossier.caveat,
    }


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
