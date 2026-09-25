from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from importlib.metadata import version as package_version
from pathlib import Path

import duckdb
import numpy as np
from skfolio import RiskMeasure
from skfolio.cluster import HierarchicalClustering, LinkageMethod
from skfolio.distance import PearsonDistance
from skfolio.optimization import (
    HierarchicalEqualRiskContribution,
    HierarchicalRiskParity,
)
from skfolio.prior import EmpiricalPrior

from .covariance import CovarianceArtifact
from .portfolio_construction import (
    PortfolioConstraintPolicy,
    PortfolioDataset,
    PortfolioWeight,
    calculate_one_way_turnover,
    validate_portfolio_constraints,
    validate_previous_weights,
)
from .portfolio_optimization import FrozenCovarianceEstimator


class HierarchicalAllocator(str, Enum):
    HRP = "HRP"
    HERC = "HERC"


@dataclass(frozen=True)
class HierarchicalAllocationPolicy:
    linkage_method: str
    pearson_absolute: bool
    pearson_power: Decimal
    max_clusters: int | None
    herc_solver: str
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.linkage_method != "WARD":
            raise ValueError("Stage 10.5 supports only explicit WARD linkage")
        if not self.pearson_power.is_finite() or self.pearson_power <= 0:
            raise ValueError("pearson_power must be finite and positive")
        if self.max_clusters is not None and self.max_clusters < 2:
            raise ValueError("max_clusters must be at least 2 when supplied")
        if not self.herc_solver.strip():
            raise ValueError("HERC solver is required")
        if not self.rationale.strip():
            raise ValueError("hierarchical allocation rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError(
                "hierarchical allocation policy requires evidence references"
            )

    @property
    def policy_id(self) -> str:
        return _content_id(
            "hierarchical-allocation-policy",
            {
                "linkage_method": self.linkage_method,
                "pearson_absolute": self.pearson_absolute,
                "pearson_power": str(self.pearson_power),
                "max_clusters": self.max_clusters,
                "herc_solver": self.herc_solver,
                "risk_measure": "VARIANCE",
                "rationale": self.rationale,
                "evidence_references": list(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class HierarchicalPortfolioSolution:
    solution_id: str
    model_id: str
    manifest_id: str
    dataset_id: str
    covariance_artifact_id: str
    decision_time: object
    allocator: HierarchicalAllocator
    risk_measure: str
    engine_name: str
    engine_version: str
    hierarchical_policy_id: str
    constraint_policy_id: str
    weights: tuple[PortfolioWeight, ...]
    previous_weights: tuple[PortfolioWeight, ...]
    net_exposure: Decimal
    gross_exposure: Decimal
    one_way_turnover: Decimal
    portfolio_variance: Decimal
    covariance_verified: bool
    cluster_count: int
    cluster_labels: tuple[int, ...]
    distance_matrix_fingerprint: str
    linkage_matrix_fingerprint: str
    capital_authority: str


class SkfolioHierarchicalAllocator:
    ENGINE_NAME = "skfolio"
    COVARIANCE_TOLERANCE = 1e-12

    def __init__(self) -> None:
        self.engine_version = package_version("skfolio")

    def allocate(
        self,
        *,
        dataset: PortfolioDataset,
        covariance: CovarianceArtifact,
        constraints: PortfolioConstraintPolicy,
        policy: HierarchicalAllocationPolicy,
        allocator: HierarchicalAllocator,
        previous_weights: tuple[PortfolioWeight, ...] = (),
    ) -> HierarchicalPortfolioSolution:
        self._validate_inputs(
            dataset=dataset,
            covariance=covariance,
            constraints=constraints,
        )
        previous = validate_previous_weights(
            dataset=dataset,
            previous_weights=previous_weights,
        )
        if (
            constraints.maximum_one_way_turnover is not None
            and not previous_weights
        ):
            raise ValueError(
                "turnover-limited hierarchical allocation requires previous weights"
            )

        returns = np.asarray(
            [[float(value) for value in row] for row in dataset.returns],
            dtype=float,
        )
        covariance_matrix = np.asarray(
            [[float(value) for value in row] for row in covariance.covariance],
            dtype=float,
        )
        previous_array = np.asarray(
            [
                float(
                    next(
                        (
                            item.weight
                            for item in previous
                            if item.security_id == security_id
                        ),
                        Decimal("0"),
                    )
                )
                for security_id in dataset.security_ids
            ],
            dtype=float,
        )

        frozen_covariance = tuple(
            tuple(float(value) for value in row)
            for row in covariance.covariance
        )
        prior = EmpiricalPrior(
            covariance_estimator=FrozenCovarianceEstimator(
                covariance_matrix=frozen_covariance
            )
        )
        distance = PearsonDistance(
            absolute=policy.pearson_absolute,
            power=float(policy.pearson_power),
        )
        clustering = HierarchicalClustering(
            max_clusters=policy.max_clusters,
            linkage_method=LinkageMethod.WARD,
        )

        common = dict(
            risk_measure=RiskMeasure.VARIANCE,
            prior_estimator=prior,
            distance_estimator=distance,
            hierarchical_clustering_estimator=clustering,
            min_weights=float(constraints.minimum_weight),
            max_weights=float(constraints.maximum_weight),
            previous_weights=(
                previous_array if previous_weights else None
            ),
            raise_on_failure=True,
        )
        if allocator is HierarchicalAllocator.HRP:
            model = HierarchicalRiskParity(**common)
        elif allocator is HierarchicalAllocator.HERC:
            model = HierarchicalEqualRiskContribution(
                **common,
                solver=policy.herc_solver,
            )
        else:
            raise ValueError(
                f"unsupported hierarchical allocator: {allocator.value}"
            )

        try:
            model.fit(returns)
        except Exception as exc:
            raise ValueError(
                f"{allocator.value} allocation failed: {exc}"
            ) from exc

        raw = np.asarray(model.weights_, dtype=float).reshape(-1)
        if raw.shape[0] != dataset.assets or not np.isfinite(raw).all():
            raise ValueError(
                "hierarchical allocator returned invalid weights"
            )

        fitted_covariance = np.asarray(
            model.prior_estimator_.return_distribution_.covariance,
            dtype=float,
        )
        covariance_verified = bool(
            fitted_covariance.shape == covariance_matrix.shape
            and np.allclose(
                fitted_covariance,
                covariance_matrix,
                atol=self.COVARIANCE_TOLERANCE,
                rtol=0.0,
            )
        )
        if not covariance_verified:
            raise ValueError(
                "hierarchical allocator did not consume frozen covariance"
            )

        distance_matrix = np.asarray(
            model.distance_estimator_.distance_,
            dtype=float,
        )
        linkage_matrix = np.asarray(
            model.hierarchical_clustering_estimator_.linkage_matrix_,
            dtype=float,
        )
        labels = np.asarray(
            model.hierarchical_clustering_estimator_.labels_,
            dtype=int,
        ).reshape(-1)
        if distance_matrix.shape != (dataset.assets, dataset.assets):
            raise ValueError("hierarchical distance matrix shape mismatch")
        if labels.shape[0] != dataset.assets:
            raise ValueError("hierarchical cluster labels shape mismatch")
        if not np.isfinite(distance_matrix).all():
            raise ValueError("hierarchical distance matrix is non-finite")
        if not np.isfinite(linkage_matrix).all():
            raise ValueError("hierarchical linkage matrix is non-finite")

        weights = tuple(
            PortfolioWeight(
                security_id=security_id,
                weight=Decimal(str(float(weight))),
            )
            for security_id, weight in zip(dataset.security_ids, raw)
        )
        net = sum((item.weight for item in weights), Decimal("0"))
        gross = sum((abs(item.weight) for item in weights), Decimal("0"))
        turnover = calculate_one_way_turnover(
            target=weights,
            previous=previous,
        )
        validate_portfolio_constraints(
            weights=weights,
            net=net,
            gross=gross,
            turnover=turnover,
            constraints=constraints,
        )

        variance_float = float(raw @ covariance_matrix @ raw)
        if not math.isfinite(variance_float) or variance_float < -1e-12:
            raise ValueError("hierarchical portfolio variance is invalid")
        variance = Decimal(str(max(variance_float, 0.0)))
        cluster_labels = tuple(int(item) for item in labels.tolist())
        cluster_count = len(set(cluster_labels))
        if cluster_count < 1 or cluster_count > dataset.assets:
            raise ValueError("hierarchical cluster count is invalid")

        payload = {
            "model_id": dataset.model_id,
            "manifest_id": dataset.manifest_id,
            "dataset_id": dataset.dataset_id,
            "covariance_artifact_id": covariance.artifact_id,
            "decision_time": dataset.decision_time.isoformat(),
            "allocator": allocator.value,
            "risk_measure": "VARIANCE",
            "engine_name": self.ENGINE_NAME,
            "engine_version": self.engine_version,
            "hierarchical_policy_id": policy.policy_id,
            "constraint_policy_id": constraints.policy_id,
            "weights": [
                {
                    "security_id": item.security_id,
                    "weight": str(item.weight),
                }
                for item in weights
            ],
            "previous_weights": [
                {
                    "security_id": item.security_id,
                    "weight": str(item.weight),
                }
                for item in previous
            ],
            "net_exposure": str(net),
            "gross_exposure": str(gross),
            "one_way_turnover": str(turnover),
            "portfolio_variance": str(variance),
            "covariance_verified": covariance_verified,
            "cluster_count": cluster_count,
            "cluster_labels": list(cluster_labels),
            "distance_matrix_fingerprint": _matrix_fingerprint(
                "pearson-distance",
                distance_matrix,
            ),
            "linkage_matrix_fingerprint": _matrix_fingerprint(
                "hierarchical-linkage",
                linkage_matrix,
            ),
            "capital_authority": "NONE",
        }
        return HierarchicalPortfolioSolution(
            solution_id=_content_id(
                "hierarchical-portfolio-solution",
                payload,
            ),
            model_id=dataset.model_id,
            manifest_id=dataset.manifest_id,
            dataset_id=dataset.dataset_id,
            covariance_artifact_id=covariance.artifact_id,
            decision_time=dataset.decision_time,
            allocator=allocator,
            risk_measure="VARIANCE",
            engine_name=self.ENGINE_NAME,
            engine_version=self.engine_version,
            hierarchical_policy_id=policy.policy_id,
            constraint_policy_id=constraints.policy_id,
            weights=weights,
            previous_weights=previous,
            net_exposure=net,
            gross_exposure=gross,
            one_way_turnover=turnover,
            portfolio_variance=variance,
            covariance_verified=covariance_verified,
            cluster_count=cluster_count,
            cluster_labels=cluster_labels,
            distance_matrix_fingerprint=payload[
                "distance_matrix_fingerprint"
            ],
            linkage_matrix_fingerprint=payload[
                "linkage_matrix_fingerprint"
            ],
            capital_authority="NONE",
        )

    @staticmethod
    def _validate_inputs(
        *,
        dataset: PortfolioDataset,
        covariance: CovarianceArtifact,
        constraints: PortfolioConstraintPolicy,
    ) -> None:
        if covariance.dataset_id != dataset.dataset_id:
            raise ValueError("covariance artifact belongs to another dataset")
        if covariance.model_id != dataset.model_id:
            raise ValueError("covariance artifact belongs to another model")
        if covariance.manifest_id != dataset.manifest_id:
            raise ValueError("covariance artifact belongs to another manifest")
        if covariance.security_ids != dataset.security_ids:
            raise ValueError("covariance security order differs from dataset")
        if not covariance.positive_semidefinite:
            raise ValueError(
                "hierarchical allocation requires PSD covariance"
            )
        if not constraints.long_only or not constraints.fully_invested:
            raise ValueError(
                "Stage 10.5 hierarchical allocation requires "
                "fully-invested long-only constraints"
            )
        if constraints.maximum_weight > 1:
            raise ValueError(
                "hierarchical allocation maximum weight cannot exceed 1"
            )


def hierarchical_portfolio_solution_identity(
    solution: HierarchicalPortfolioSolution,
) -> str:
    payload = {
        "model_id": solution.model_id,
        "manifest_id": solution.manifest_id,
        "dataset_id": solution.dataset_id,
        "covariance_artifact_id": solution.covariance_artifact_id,
        "decision_time": solution.decision_time.isoformat(),
        "allocator": solution.allocator.value,
        "risk_measure": solution.risk_measure,
        "engine_name": solution.engine_name,
        "engine_version": solution.engine_version,
        "hierarchical_policy_id": solution.hierarchical_policy_id,
        "constraint_policy_id": solution.constraint_policy_id,
        "weights": [
            {
                "security_id": item.security_id,
                "weight": str(item.weight),
            }
            for item in solution.weights
        ],
        "previous_weights": [
            {
                "security_id": item.security_id,
                "weight": str(item.weight),
            }
            for item in solution.previous_weights
        ],
        "net_exposure": str(solution.net_exposure),
        "gross_exposure": str(solution.gross_exposure),
        "one_way_turnover": str(solution.one_way_turnover),
        "portfolio_variance": str(solution.portfolio_variance),
        "covariance_verified": solution.covariance_verified,
        "cluster_count": solution.cluster_count,
        "cluster_labels": list(solution.cluster_labels),
        "distance_matrix_fingerprint": solution.distance_matrix_fingerprint,
        "linkage_matrix_fingerprint": solution.linkage_matrix_fingerprint,
        "capital_authority": solution.capital_authority,
    }
    return _content_id("hierarchical-portfolio-solution", payload)


class HierarchicalPortfolioStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS hierarchical_portfolio_solutions (
                solution_id VARCHAR PRIMARY KEY,
                model_id VARCHAR NOT NULL,
                manifest_id VARCHAR NOT NULL,
                dataset_id VARCHAR NOT NULL,
                covariance_artifact_id VARCHAR NOT NULL,
                decision_time TIMESTAMPTZ NOT NULL,
                allocator VARCHAR NOT NULL,
                hierarchical_policy_id VARCHAR NOT NULL,
                constraint_policy_id VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, solution: HierarchicalPortfolioSolution) -> bool:
        if (
            solution.solution_id
            != hierarchical_portfolio_solution_identity(solution)
        ):
            raise ValueError(
                "hierarchical portfolio content does not match solution_id"
            )
        payload = json.dumps(
            {
                key: (
                    value.isoformat()
                    if hasattr(value, "isoformat")
                    else value.value
                    if isinstance(value, Enum)
                    else [
                        {
                            "security_id": item.security_id,
                            "weight": str(item.weight),
                        }
                        for item in value
                    ]
                    if key in {"weights", "previous_weights"}
                    else list(value)
                    if isinstance(value, tuple)
                    else str(value)
                    if isinstance(value, Decimal)
                    else value
                )
                for key, value in solution.__dict__.items()
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        row = self._con.execute(
            """
            SELECT payload_json
            FROM hierarchical_portfolio_solutions
            WHERE solution_id = ?
            """,
            [solution.solution_id],
        ).fetchone()
        if row is not None:
            if str(row[0]) != payload:
                raise ValueError(
                    "hierarchical portfolio solution identity conflict"
                )
            return False
        self._con.execute(
            """
            INSERT INTO hierarchical_portfolio_solutions
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                solution.solution_id,
                solution.model_id,
                solution.manifest_id,
                solution.dataset_id,
                solution.covariance_artifact_id,
                solution.decision_time,
                solution.allocator.value,
                solution.hierarchical_policy_id,
                solution.constraint_policy_id,
                payload,
            ],
        )
        return True

    def close(self) -> None:
        self._con.close()


def _matrix_fingerprint(prefix: str, matrix: np.ndarray) -> str:
    payload = [
        [repr(float(value)) for value in row]
        for row in np.asarray(matrix, dtype=float)
    ]
    return _content_id(prefix, payload)


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
