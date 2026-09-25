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
from skfolio.moments import EmpiricalCovariance, LedoitWolf

from .portfolio_construction import PortfolioDataset


class CovarianceEstimatorKind(str, Enum):
    EMPIRICAL = "EMPIRICAL"
    LEDOIT_WOLF = "LEDOIT_WOLF"


@dataclass(frozen=True)
class CovarianceEstimationPolicy:
    estimator: CovarianceEstimatorKind
    minimum_observations: int
    nearest: bool
    higham: bool
    higham_max_iteration: int
    empirical_ddof: int
    require_positive_semidefinite: bool
    maximum_condition_number: Decimal | None
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.minimum_observations < 2:
            raise ValueError("minimum_observations must be at least 2")
        if self.higham and not self.nearest:
            raise ValueError("Higham nearest-covariance mode requires nearest=True")
        if self.higham_max_iteration < 1:
            raise ValueError("higham_max_iteration must be positive")
        if self.empirical_ddof < 0:
            raise ValueError("empirical_ddof must be non-negative")
        if self.empirical_ddof >= self.minimum_observations:
            raise ValueError(
                "empirical_ddof must be smaller than minimum_observations"
            )
        if self.maximum_condition_number is not None:
            if (
                not self.maximum_condition_number.is_finite()
                or self.maximum_condition_number <= 0
            ):
                raise ValueError(
                    "maximum_condition_number must be finite and positive"
                )
        if not self.rationale.strip():
            raise ValueError("covariance policy rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("covariance policy requires evidence references")

    @property
    def policy_id(self) -> str:
        return _content_id(
            "covariance-policy",
            {
                "estimator": self.estimator.value,
                "minimum_observations": self.minimum_observations,
                "nearest": self.nearest,
                "higham": self.higham,
                "higham_max_iteration": self.higham_max_iteration,
                "empirical_ddof": self.empirical_ddof,
                "require_positive_semidefinite": (
                    self.require_positive_semidefinite
                ),
                "maximum_condition_number": (
                    str(self.maximum_condition_number)
                    if self.maximum_condition_number is not None
                    else None
                ),
                "rationale": self.rationale,
                "evidence_references": list(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class CovarianceArtifact:
    artifact_id: str
    model_id: str
    manifest_id: str
    dataset_id: str
    decision_time: object
    security_ids: tuple[str, ...]
    estimator: CovarianceEstimatorKind
    engine_name: str
    engine_version: str
    policy_id: str
    covariance: tuple[tuple[Decimal, ...], ...]
    minimum_eigenvalue: Decimal
    maximum_eigenvalue: Decimal
    condition_number: Decimal
    shrinkage: Decimal | None
    symmetric: bool
    positive_semidefinite: bool


class SkfolioCovarianceEstimator:
    """Versioned skfolio covariance adapter with independent matrix diagnostics."""

    ENGINE_NAME = "skfolio"
    SYMMETRY_TOLERANCE = 1e-12
    PSD_TOLERANCE = 1e-12

    def __init__(self) -> None:
        self.engine_version = package_version("skfolio")

    def estimate(
        self,
        *,
        dataset: PortfolioDataset,
        policy: CovarianceEstimationPolicy,
    ) -> CovarianceArtifact:
        if dataset.observations < policy.minimum_observations:
            raise ValueError(
                "portfolio dataset has too few observations for covariance policy"
            )
        matrix = np.asarray(
            [
                [float(value) for value in row]
                for row in dataset.returns
            ],
            dtype=float,
        )
        if matrix.shape != (dataset.observations, dataset.assets):
            raise AssertionError("portfolio dataset matrix shape mismatch")
        if not np.isfinite(matrix).all():
            raise ValueError("covariance input contains non-finite returns")

        if policy.estimator is CovarianceEstimatorKind.EMPIRICAL:
            if policy.empirical_ddof >= dataset.observations:
                raise ValueError(
                    "empirical_ddof must be smaller than dataset observations"
                )
            estimator = EmpiricalCovariance(
                ddof=policy.empirical_ddof,
                nearest=policy.nearest,
                higham=policy.higham,
                higham_max_iteration=policy.higham_max_iteration,
            )
        elif policy.estimator is CovarianceEstimatorKind.LEDOIT_WOLF:
            estimator = LedoitWolf(
                nearest=policy.nearest,
                higham=policy.higham,
                higham_max_iteration=policy.higham_max_iteration,
            )
        else:
            raise ValueError(
                f"unsupported covariance estimator: {policy.estimator.value}"
            )

        try:
            estimator.fit(matrix)
        except Exception as exc:
            raise ValueError(
                f"{policy.estimator.value} covariance estimation failed: {exc}"
            ) from exc

        covariance = np.asarray(estimator.covariance_, dtype=float)
        expected_shape = (dataset.assets, dataset.assets)
        if covariance.shape != expected_shape:
            raise ValueError("covariance estimator returned unexpected matrix shape")
        if not np.isfinite(covariance).all():
            raise ValueError("covariance estimator returned non-finite matrix")

        symmetric = bool(
            np.allclose(
                covariance,
                covariance.T,
                atol=self.SYMMETRY_TOLERANCE,
                rtol=0.0,
            )
        )
        if not symmetric:
            raise ValueError("covariance matrix is not symmetric")

        eigenvalues = np.linalg.eigvalsh(covariance)
        if not np.isfinite(eigenvalues).all():
            raise ValueError("covariance eigenvalues are non-finite")
        minimum_eigenvalue = float(eigenvalues.min())
        maximum_eigenvalue = float(eigenvalues.max())
        positive_semidefinite = (
            minimum_eigenvalue >= -self.PSD_TOLERANCE
        )
        if policy.require_positive_semidefinite and not positive_semidefinite:
            raise ValueError("covariance matrix is not positive semidefinite")

        condition = float(np.linalg.cond(covariance))
        if not math.isfinite(condition):
            raise ValueError("covariance condition number is non-finite")
        condition_decimal = Decimal(str(condition))
        if (
            policy.maximum_condition_number is not None
            and condition_decimal > policy.maximum_condition_number
        ):
            raise ValueError(
                "covariance matrix exceeds maximum condition number"
            )

        shrinkage = None
        if policy.estimator is CovarianceEstimatorKind.LEDOIT_WOLF:
            value = getattr(estimator, "shrinkage_", None)
            if value is None or not math.isfinite(float(value)):
                raise ValueError("Ledoit-Wolf estimator did not expose shrinkage")
            shrinkage = Decimal(str(float(value)))
            if shrinkage < 0 or shrinkage > 1:
                raise ValueError("Ledoit-Wolf shrinkage is outside [0, 1]")

        covariance_decimal = tuple(
            tuple(Decimal(str(float(value))) for value in row)
            for row in covariance
        )
        payload = {
            "model_id": dataset.model_id,
            "manifest_id": dataset.manifest_id,
            "dataset_id": dataset.dataset_id,
            "decision_time": dataset.decision_time.isoformat(),
            "security_ids": list(dataset.security_ids),
            "estimator": policy.estimator.value,
            "engine_name": self.ENGINE_NAME,
            "engine_version": self.engine_version,
            "policy_id": policy.policy_id,
            "covariance": [
                [str(value) for value in row]
                for row in covariance_decimal
            ],
            "minimum_eigenvalue": str(minimum_eigenvalue),
            "maximum_eigenvalue": str(maximum_eigenvalue),
            "condition_number": str(condition_decimal),
            "shrinkage": str(shrinkage) if shrinkage is not None else None,
            "symmetric": symmetric,
            "positive_semidefinite": positive_semidefinite,
        }
        return CovarianceArtifact(
            artifact_id=_content_id("covariance-artifact", payload),
            model_id=dataset.model_id,
            manifest_id=dataset.manifest_id,
            dataset_id=dataset.dataset_id,
            decision_time=dataset.decision_time,
            security_ids=dataset.security_ids,
            estimator=policy.estimator,
            engine_name=self.ENGINE_NAME,
            engine_version=self.engine_version,
            policy_id=policy.policy_id,
            covariance=covariance_decimal,
            minimum_eigenvalue=Decimal(str(minimum_eigenvalue)),
            maximum_eigenvalue=Decimal(str(maximum_eigenvalue)),
            condition_number=condition_decimal,
            shrinkage=shrinkage,
            symmetric=symmetric,
            positive_semidefinite=positive_semidefinite,
        )


class CovarianceArtifactStore:
    """Immutable idempotent persistence for covariance artifacts."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS covariance_artifacts (
                artifact_id VARCHAR PRIMARY KEY,
                model_id VARCHAR NOT NULL,
                manifest_id VARCHAR NOT NULL,
                dataset_id VARCHAR NOT NULL,
                decision_time TIMESTAMPTZ NOT NULL,
                estimator VARCHAR NOT NULL,
                engine_name VARCHAR NOT NULL,
                engine_version VARCHAR NOT NULL,
                policy_id VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, artifact: CovarianceArtifact) -> bool:
        payload = json.dumps(
            {
                "artifact_id": artifact.artifact_id,
                "model_id": artifact.model_id,
                "manifest_id": artifact.manifest_id,
                "dataset_id": artifact.dataset_id,
                "decision_time": artifact.decision_time.isoformat(),
                "security_ids": list(artifact.security_ids),
                "estimator": artifact.estimator.value,
                "engine_name": artifact.engine_name,
                "engine_version": artifact.engine_version,
                "policy_id": artifact.policy_id,
                "covariance": [
                    [str(value) for value in row]
                    for row in artifact.covariance
                ],
                "minimum_eigenvalue": str(artifact.minimum_eigenvalue),
                "maximum_eigenvalue": str(artifact.maximum_eigenvalue),
                "condition_number": str(artifact.condition_number),
                "shrinkage": (
                    str(artifact.shrinkage)
                    if artifact.shrinkage is not None
                    else None
                ),
                "symmetric": artifact.symmetric,
                "positive_semidefinite": artifact.positive_semidefinite,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        existing = self._con.execute(
            """
            SELECT payload_json
            FROM covariance_artifacts
            WHERE artifact_id = ?
            """,
            [artifact.artifact_id],
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != payload:
                raise ValueError("covariance artifact identity conflict")
            return False
        self._con.execute(
            """
            INSERT INTO covariance_artifacts
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                artifact.artifact_id,
                artifact.model_id,
                artifact.manifest_id,
                artifact.dataset_id,
                artifact.decision_time,
                artifact.estimator.value,
                artifact.engine_name,
                artifact.engine_version,
                artifact.policy_id,
                payload,
            ],
        )
        return True

    def close(self) -> None:
        self._con.close()


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
