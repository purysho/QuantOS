from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from decimal import Decimal
from importlib.metadata import version as package_version
from pathlib import Path

import cvxpy as cp
import duckdb
import numpy as np
from skfolio import RiskMeasure
from skfolio.moments import BaseCovariance
from skfolio.optimization import MeanRisk, ObjectiveFunction
from skfolio.prior import EmpiricalPrior
from sklearn.utils.validation import validate_data

from .covariance import CovarianceArtifact
from .portfolio_construction import (
    BaselineAllocator,
    PortfolioConstraintPolicy,
    PortfolioDataset,
    PortfolioSolution,
    PortfolioWeight,
    calculate_one_way_turnover,
    validate_portfolio_constraints,
    validate_previous_weights,
    portfolio_solution_identity,
)


class _FrozenCovariance(BaseCovariance):
    """skfolio covariance estimator that returns one immutable artifact matrix."""

    def __init__(
        self,
        covariance_matrix: tuple[tuple[float, ...], ...],
    ) -> None:
        super().__init__(nearest=False)
        self.covariance_matrix = covariance_matrix

    def fit(self, X, y=None):
        X = validate_data(self, X)
        covariance = np.asarray(self.covariance_matrix, dtype=float)
        if covariance.shape != (X.shape[1], X.shape[1]):
            raise ValueError("frozen covariance shape differs from optimizer input")
        if not np.isfinite(covariance).all():
            raise ValueError("frozen covariance contains non-finite values")
        self._set_covariance(covariance)
        return self


FrozenCovarianceEstimator = _FrozenCovariance


@dataclass(frozen=True)
class MinimumVariancePolicy:
    solver: str
    l2_regularization: Decimal
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.solver.strip():
            raise ValueError("minimum-variance policy solver is required")
        if (
            not self.l2_regularization.is_finite()
            or self.l2_regularization < 0
        ):
            raise ValueError("l2_regularization must be finite and non-negative")
        if not self.rationale.strip():
            raise ValueError("minimum-variance policy rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError(
                "minimum-variance policy requires evidence references"
            )

    @property
    def policy_id(self) -> str:
        return _content_id(
            "minimum-variance-policy",
            {
                "objective": "MINIMIZE_RISK",
                "risk_measure": "VARIANCE",
                "solver": self.solver,
                "l2_regularization": str(self.l2_regularization),
                "rationale": self.rationale,
                "evidence_references": list(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class OptimizedPortfolioSolution:
    solution_id: str
    model_id: str
    manifest_id: str
    dataset_id: str
    covariance_artifact_id: str
    decision_time: object
    objective: str
    risk_measure: str
    engine_name: str
    engine_version: str
    solver: str
    solver_status: str
    solver_objective_value: Decimal
    optimization_policy_id: str
    constraint_policy_id: str
    baseline_solution_ids: tuple[str, ...]
    weights: tuple[PortfolioWeight, ...]
    previous_weights: tuple[PortfolioWeight, ...]
    net_exposure: Decimal
    gross_exposure: Decimal
    one_way_turnover: Decimal
    portfolio_variance: Decimal
    covariance_verified: bool
    capital_authority: str


class SkfolioMinimumVarianceOptimizer:
    """Minimum-variance optimizer consuming an already-frozen covariance artifact."""

    ENGINE_NAME = "skfolio"
    COVARIANCE_TOLERANCE = 1e-12

    def __init__(self) -> None:
        self.engine_version = package_version("skfolio")

    def optimize(
        self,
        *,
        dataset: PortfolioDataset,
        covariance: CovarianceArtifact,
        constraints: PortfolioConstraintPolicy,
        policy: MinimumVariancePolicy,
        baselines: tuple[PortfolioSolution, ...],
        previous_weights: tuple[PortfolioWeight, ...] = (),
    ) -> OptimizedPortfolioSolution:
        self._validate_inputs(
            dataset=dataset,
            covariance=covariance,
            constraints=constraints,
            baselines=baselines,
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
                "turnover-constrained optimization requires previous weights"
            )

        returns = np.asarray(
            [
                [float(value) for value in row]
                for row in dataset.returns
            ],
            dtype=float,
        )
        covariance_matrix = np.asarray(
            [
                [float(value) for value in row]
                for row in covariance.covariance
            ],
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

        add_constraints = None
        if constraints.maximum_one_way_turnover is not None:
            turnover_limit = float(
                constraints.maximum_one_way_turnover
            )
            previous_cash = 1.0 - float(previous_array.sum())

            def add_constraints(w):
                target_cash = 1.0 - cp.sum(w)
                return (
                    0.5
                    * (
                        cp.norm1(w - previous_array)
                        + cp.abs(target_cash - previous_cash)
                    )
                    <= turnover_limit
                )

        frozen_covariance = tuple(
            tuple(float(value) for value in row)
            for row in covariance.covariance
        )
        model = MeanRisk(
            objective_function=ObjectiveFunction.MINIMIZE_RISK,
            risk_measure=RiskMeasure.VARIANCE,
            prior_estimator=EmpiricalPrior(
                covariance_estimator=_FrozenCovariance(
                    covariance_matrix=frozen_covariance
                )
            ),
            min_weights=float(constraints.minimum_weight),
            max_weights=float(constraints.maximum_weight),
            budget=1.0,
            previous_weights=(
                previous_array if previous_weights else None
            ),
            l2_coef=float(policy.l2_regularization),
            solver=policy.solver,
            save_problem=True,
            add_constraints=add_constraints,
            raise_on_failure=True,
        )
        try:
            model.fit(returns)
        except Exception as exc:
            raise ValueError(
                f"minimum-variance optimization failed: {exc}"
            ) from exc

        raw = np.asarray(model.weights_, dtype=float).reshape(-1)
        if raw.shape[0] != dataset.assets:
            raise ValueError("optimizer returned unexpected number of weights")
        if not np.isfinite(raw).all():
            raise ValueError("optimizer returned non-finite weights")

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
                "optimizer did not consume the exact frozen covariance artifact"
            )

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
        if not math.isfinite(variance_float):
            raise ValueError("optimized portfolio variance is non-finite")
        if variance_float < -1e-12:
            raise ValueError("optimized portfolio variance is negative")
        variance = Decimal(str(max(variance_float, 0.0)))

        problem = getattr(model, "problem_", None)
        if problem is None:
            raise ValueError("optimizer did not retain solver problem diagnostics")
        solver_status = str(problem.status)
        if solver_status not in {"optimal", "optimal_inaccurate"}:
            raise ValueError(
                f"optimizer solver status is not successful: {solver_status}"
            )
        objective_value = getattr(problem, "value", None)
        if objective_value is None or not math.isfinite(float(objective_value)):
            raise ValueError("optimizer objective value is unavailable")
        objective_decimal = Decimal(str(float(objective_value)))

        baseline_ids = tuple(
            sorted(item.solution_id for item in baselines)
        )
        payload = {
            "model_id": dataset.model_id,
            "manifest_id": dataset.manifest_id,
            "dataset_id": dataset.dataset_id,
            "covariance_artifact_id": covariance.artifact_id,
            "decision_time": dataset.decision_time.isoformat(),
            "objective": "MINIMIZE_RISK",
            "risk_measure": "VARIANCE",
            "engine_name": self.ENGINE_NAME,
            "engine_version": self.engine_version,
            "solver": policy.solver,
            "solver_status": solver_status,
            "solver_objective_value": str(objective_decimal),
            "optimization_policy_id": policy.policy_id,
            "constraint_policy_id": constraints.policy_id,
            "baseline_solution_ids": list(baseline_ids),
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
            "capital_authority": "NONE",
        }
        return OptimizedPortfolioSolution(
            solution_id=_content_id(
                "optimized-portfolio-solution",
                payload,
            ),
            model_id=dataset.model_id,
            manifest_id=dataset.manifest_id,
            dataset_id=dataset.dataset_id,
            covariance_artifact_id=covariance.artifact_id,
            decision_time=dataset.decision_time,
            objective="MINIMIZE_RISK",
            risk_measure="VARIANCE",
            engine_name=self.ENGINE_NAME,
            engine_version=self.engine_version,
            solver=policy.solver,
            solver_status=solver_status,
            solver_objective_value=objective_decimal,
            optimization_policy_id=policy.policy_id,
            constraint_policy_id=constraints.policy_id,
            baseline_solution_ids=baseline_ids,
            weights=weights,
            previous_weights=previous,
            net_exposure=net,
            gross_exposure=gross,
            one_way_turnover=turnover,
            portfolio_variance=variance,
            covariance_verified=covariance_verified,
            capital_authority="NONE",
        )

    @staticmethod
    def _validate_inputs(
        *,
        dataset: PortfolioDataset,
        covariance: CovarianceArtifact,
        constraints: PortfolioConstraintPolicy,
        baselines: tuple[PortfolioSolution, ...],
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
            raise ValueError("optimizer requires positive-semidefinite covariance")
        if not constraints.long_only or not constraints.fully_invested:
            raise ValueError(
                "Stage 10.3 minimum-variance optimizer supports only "
                "fully-invested long-only policies"
            )

        if len(baselines) != 2:
            raise ValueError(
                "minimum-variance optimization requires exactly two baselines"
            )
        baseline_kinds = {item.allocator for item in baselines}
        if baseline_kinds != {
            BaselineAllocator.EQUAL_WEIGHT,
            BaselineAllocator.INVERSE_VOLATILITY,
        }:
            raise ValueError(
                "mandatory baselines are EqualWeight and InverseVolatility"
            )
        if len({item.solution_id for item in baselines}) != 2:
            raise ValueError("baseline solutions must be distinct")
        for baseline in baselines:
            if baseline.solution_id != portfolio_solution_identity(baseline):
                raise ValueError("baseline solution identity does not match content")
            if baseline.dataset_id != dataset.dataset_id:
                raise ValueError("baseline belongs to another portfolio dataset")
            if baseline.manifest_id != dataset.manifest_id:
                raise ValueError("baseline belongs to another Research Run manifest")
            if baseline.constraint_policy_id != constraints.policy_id:
                raise ValueError("baseline uses another constraint policy")
            if baseline.capital_authority != "NONE":
                raise ValueError("baseline unexpectedly carries capital authority")


def optimized_portfolio_solution_identity(
    solution: OptimizedPortfolioSolution,
) -> str:
    payload = {
        "model_id": solution.model_id,
        "manifest_id": solution.manifest_id,
        "dataset_id": solution.dataset_id,
        "covariance_artifact_id": solution.covariance_artifact_id,
        "decision_time": solution.decision_time.isoformat(),
        "objective": solution.objective,
        "risk_measure": solution.risk_measure,
        "engine_name": solution.engine_name,
        "engine_version": solution.engine_version,
        "solver": solution.solver,
        "solver_status": solution.solver_status,
        "solver_objective_value": str(solution.solver_objective_value),
        "optimization_policy_id": solution.optimization_policy_id,
        "constraint_policy_id": solution.constraint_policy_id,
        "baseline_solution_ids": list(solution.baseline_solution_ids),
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
        "capital_authority": solution.capital_authority,
    }
    return _content_id("optimized-portfolio-solution", payload)


class OptimizedPortfolioStore:
    """Immutable idempotent persistence for constrained optimizer outputs."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS optimized_portfolio_solutions (
                solution_id VARCHAR PRIMARY KEY,
                model_id VARCHAR NOT NULL,
                manifest_id VARCHAR NOT NULL,
                dataset_id VARCHAR NOT NULL,
                covariance_artifact_id VARCHAR NOT NULL,
                decision_time TIMESTAMPTZ NOT NULL,
                optimization_policy_id VARCHAR NOT NULL,
                constraint_policy_id VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, solution: OptimizedPortfolioSolution) -> bool:
        if (
            solution.solution_id
            != optimized_portfolio_solution_identity(solution)
        ):
            raise ValueError(
                "optimized portfolio solution content does not match solution_id"
            )
        payload = json.dumps(
            {
                key: (
                    value.isoformat()
                    if hasattr(value, "isoformat")
                    else value.value
                    if hasattr(value, "value")
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
            FROM optimized_portfolio_solutions
            WHERE solution_id = ?
            """,
            [solution.solution_id],
        ).fetchone()
        if row is not None:
            if str(row[0]) != payload:
                raise ValueError("optimized portfolio solution identity conflict")
            return False
        self._con.execute(
            """
            INSERT INTO optimized_portfolio_solutions
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                solution.solution_id,
                solution.model_id,
                solution.manifest_id,
                solution.dataset_id,
                solution.covariance_artifact_id,
                solution.decision_time,
                solution.optimization_policy_id,
                solution.constraint_policy_id,
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