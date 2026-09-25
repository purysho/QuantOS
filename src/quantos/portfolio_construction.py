from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from importlib.metadata import version as package_version
from pathlib import Path

import duckdb
import numpy as np
from skfolio.optimization import EqualWeighted, InverseVolatility

from .model_registry import ModelLifecycleStage, ResearchRunManifest


class BaselineAllocator(str, Enum):
    EQUAL_WEIGHT = "EQUAL_WEIGHT"
    INVERSE_VOLATILITY = "INVERSE_VOLATILITY"


@dataclass(frozen=True)
class PortfolioReturnObservation:
    security_id: str
    period_start: datetime
    period_end: datetime
    knowledge_time: datetime
    total_return: Decimal
    source_fact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.security_id.strip():
            raise ValueError("portfolio return observation requires security_id")
        for name in ("period_start", "period_end", "knowledge_time"):
            if getattr(self, name).tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.period_end <= self.period_start:
            raise ValueError("period_end must follow period_start")
        if self.knowledge_time < self.period_end:
            raise ValueError("return cannot be known before period_end")
        if not self.total_return.is_finite() or self.total_return <= Decimal("-1"):
            raise ValueError("portfolio return must be finite and greater than -1")
        if not self.source_fact_ids or not all(
            item.strip() for item in self.source_fact_ids
        ):
            raise ValueError("portfolio return observation requires source fact IDs")

    @property
    def observation_id(self) -> str:
        return _content_id(
            "portfolio-return-observation",
            {
                "security_id": self.security_id,
                "period_start": self.period_start.isoformat(),
                "period_end": self.period_end.isoformat(),
                "knowledge_time": self.knowledge_time.isoformat(),
                "total_return": str(self.total_return),
                "source_fact_ids": list(self.source_fact_ids),
            },
        )


@dataclass(frozen=True)
class PortfolioDataset:
    dataset_id: str
    model_id: str
    manifest_id: str
    decision_time: datetime
    security_ids: tuple[str, ...]
    period_ends: tuple[datetime, ...]
    returns: tuple[tuple[Decimal, ...], ...]
    observation_ids: tuple[str, ...]

    @property
    def observations(self) -> int:
        return len(self.period_ends)

    @property
    def assets(self) -> int:
        return len(self.security_ids)


class PortfolioDatasetBuilder:
    """Build a synchronous point-in-time return matrix for portfolio construction."""

    def build(
        self,
        *,
        manifest: ResearchRunManifest,
        decision_time: datetime,
        observations: tuple[PortfolioReturnObservation, ...],
        minimum_periods: int,
    ) -> PortfolioDataset:
        if manifest.eligible_stage not in {
            ModelLifecycleStage.BACKTESTED,
            ModelLifecycleStage.PAPER,
        }:
            raise ValueError(
                "portfolio construction requires BACKTESTED or PAPER-eligible manifest"
            )
        if decision_time.tzinfo is None:
            raise ValueError("portfolio decision_time must be timezone-aware")
        if minimum_periods < 2:
            raise ValueError("minimum_periods must be at least 2")
        if not observations:
            raise ValueError("portfolio dataset requires return observations")
        if len({item.observation_id for item in observations}) != len(observations):
            raise ValueError("duplicate portfolio return observations")
        if any(
            item.period_end > decision_time
            or item.knowledge_time > decision_time
            for item in observations
        ):
            raise ValueError(
                "portfolio dataset contains returns unavailable at decision_time"
            )

        security_ids = tuple(
            sorted({item.security_id for item in observations})
        )
        if len(security_ids) < 2:
            raise ValueError("portfolio dataset requires at least two securities")

        by_security: dict[
            str,
            dict[datetime, PortfolioReturnObservation],
        ] = {security_id: {} for security_id in security_ids}
        for item in observations:
            security_rows = by_security[item.security_id]
            if item.period_end in security_rows:
                raise ValueError(
                    "multiple return observations for one security/period"
                )
            security_rows[item.period_end] = item

        canonical_periods = tuple(
            sorted(by_security[security_ids[0]])
        )
        if len(canonical_periods) < minimum_periods:
            raise ValueError("portfolio dataset has too few return periods")
        for security_id in security_ids[1:]:
            periods = tuple(sorted(by_security[security_id]))
            if periods != canonical_periods:
                raise ValueError(
                    "portfolio construction requires synchronous return histories"
                )

        matrix = tuple(
            tuple(
                by_security[security_id][period_end].total_return
                for security_id in security_ids
            )
            for period_end in canonical_periods
        )
        observation_ids = tuple(
            by_security[security_id][period_end].observation_id
            for period_end in canonical_periods
            for security_id in security_ids
        )
        payload = {
            "model_id": manifest.model_id,
            "manifest_id": manifest.manifest_id,
            "decision_time": decision_time.isoformat(),
            "security_ids": list(security_ids),
            "period_ends": [
                item.isoformat() for item in canonical_periods
            ],
            "returns": [
                [str(value) for value in row]
                for row in matrix
            ],
            "observation_ids": list(observation_ids),
        }
        return PortfolioDataset(
            dataset_id=_content_id("portfolio-dataset", payload),
            model_id=manifest.model_id,
            manifest_id=manifest.manifest_id,
            decision_time=decision_time,
            security_ids=security_ids,
            period_ends=canonical_periods,
            returns=matrix,
            observation_ids=observation_ids,
        )


@dataclass(frozen=True)
class PortfolioConstraintPolicy:
    fully_invested: bool
    long_only: bool
    minimum_weight: Decimal
    maximum_weight: Decimal
    maximum_gross_exposure: Decimal
    maximum_one_way_turnover: Decimal | None
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "minimum_weight",
            "maximum_weight",
            "maximum_gross_exposure",
        ):
            value = getattr(self, name)
            if not value.is_finite():
                raise ValueError(f"{name} must be finite")
        if self.maximum_weight <= self.minimum_weight:
            raise ValueError("maximum_weight must exceed minimum_weight")
        if self.long_only and self.minimum_weight < 0:
            raise ValueError("long-only policy cannot allow negative weights")
        if self.maximum_gross_exposure <= 0:
            raise ValueError("maximum_gross_exposure must be positive")
        if self.fully_invested and self.maximum_gross_exposure < 1:
            raise ValueError(
                "fully invested policy requires gross-exposure limit >= 1"
            )
        if self.maximum_one_way_turnover is not None:
            if (
                not self.maximum_one_way_turnover.is_finite()
                or self.maximum_one_way_turnover < 0
            ):
                raise ValueError(
                    "maximum_one_way_turnover must be finite and non-negative"
                )
        if not self.rationale.strip():
            raise ValueError("portfolio constraint rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("portfolio constraint policy requires evidence references")

    @property
    def policy_id(self) -> str:
        return _content_id(
            "portfolio-constraint-policy",
            {
                "fully_invested": self.fully_invested,
                "long_only": self.long_only,
                "minimum_weight": str(self.minimum_weight),
                "maximum_weight": str(self.maximum_weight),
                "maximum_gross_exposure": str(self.maximum_gross_exposure),
                "maximum_one_way_turnover": (
                    str(self.maximum_one_way_turnover)
                    if self.maximum_one_way_turnover is not None
                    else None
                ),
                "rationale": self.rationale,
                "evidence_references": list(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class PortfolioWeight:
    security_id: str
    weight: Decimal


@dataclass(frozen=True)
class PortfolioSolution:
    solution_id: str
    model_id: str
    manifest_id: str
    dataset_id: str
    decision_time: datetime
    allocator: BaselineAllocator
    engine_name: str
    engine_version: str
    constraint_policy_id: str
    weights: tuple[PortfolioWeight, ...]
    net_exposure: Decimal
    gross_exposure: Decimal
    one_way_turnover: Decimal
    previous_weights: tuple[PortfolioWeight, ...]
    capital_authority: str


class SkfolioBaselineAllocator:
    """skfolio-backed baseline allocator with independent constraint validation."""

    ENGINE_NAME = "skfolio"

    def __init__(self) -> None:
        self.engine_version = package_version("skfolio")

    def allocate(
        self,
        *,
        dataset: PortfolioDataset,
        allocator: BaselineAllocator,
        constraints: PortfolioConstraintPolicy,
        previous_weights: tuple[PortfolioWeight, ...] = (),
    ) -> PortfolioSolution:
        if dataset.observations < 2:
            raise ValueError("portfolio allocation requires at least two observations")
        previous = validate_previous_weights(
            dataset=dataset,
            previous_weights=previous_weights,
        )
        if (
            constraints.maximum_one_way_turnover is not None
            and not previous_weights
        ):
            raise ValueError(
                "turnover-constrained allocation requires previous weights"
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
            raise ValueError("portfolio return matrix contains non-finite values")

        if allocator is BaselineAllocator.EQUAL_WEIGHT:
            estimator = EqualWeighted()
        elif allocator is BaselineAllocator.INVERSE_VOLATILITY:
            estimator = InverseVolatility()
        else:
            raise ValueError(f"unsupported baseline allocator: {allocator.value}")

        try:
            estimator.fit(matrix)
        except Exception as exc:
            raise ValueError(
                f"{allocator.value} allocation failed: {exc}"
            ) from exc

        raw = np.asarray(estimator.weights_, dtype=float).reshape(-1)
        if raw.shape[0] != dataset.assets:
            raise ValueError("allocator returned unexpected number of weights")
        if not np.isfinite(raw).all():
            raise ValueError("allocator returned non-finite weights")

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

        payload = {
            "model_id": dataset.model_id,
            "manifest_id": dataset.manifest_id,
            "dataset_id": dataset.dataset_id,
            "decision_time": dataset.decision_time.isoformat(),
            "allocator": allocator.value,
            "engine_name": self.ENGINE_NAME,
            "engine_version": self.engine_version,
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
            "capital_authority": "NONE",
        }
        return PortfolioSolution(
            solution_id=_content_id("portfolio-solution", payload),
            model_id=dataset.model_id,
            manifest_id=dataset.manifest_id,
            dataset_id=dataset.dataset_id,
            decision_time=dataset.decision_time,
            allocator=allocator,
            engine_name=self.ENGINE_NAME,
            engine_version=self.engine_version,
            constraint_policy_id=constraints.policy_id,
            weights=weights,
            net_exposure=net,
            gross_exposure=gross,
            one_way_turnover=turnover,
            previous_weights=previous,
            capital_authority="NONE",
        )



class PortfolioSolutionStore:
    """Immutable idempotent persistence for portfolio-construction outputs."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_solutions (
                solution_id VARCHAR PRIMARY KEY,
                model_id VARCHAR NOT NULL,
                manifest_id VARCHAR NOT NULL,
                dataset_id VARCHAR NOT NULL,
                decision_time TIMESTAMPTZ NOT NULL,
                allocator VARCHAR NOT NULL,
                engine_name VARCHAR NOT NULL,
                engine_version VARCHAR NOT NULL,
                constraint_policy_id VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, solution: PortfolioSolution) -> bool:
        payload = json.dumps(
            {
                "solution_id": solution.solution_id,
                "model_id": solution.model_id,
                "manifest_id": solution.manifest_id,
                "dataset_id": solution.dataset_id,
                "decision_time": solution.decision_time.isoformat(),
                "allocator": solution.allocator.value,
                "engine_name": solution.engine_name,
                "engine_version": solution.engine_version,
                "constraint_policy_id": solution.constraint_policy_id,
                "weights": [
                    {
                        "security_id": item.security_id,
                        "weight": str(item.weight),
                    }
                    for item in solution.weights
                ],
                "net_exposure": str(solution.net_exposure),
                "gross_exposure": str(solution.gross_exposure),
                "one_way_turnover": str(solution.one_way_turnover),
                "previous_weights": [
                    {
                        "security_id": item.security_id,
                        "weight": str(item.weight),
                    }
                    for item in solution.previous_weights
                ],
                "capital_authority": solution.capital_authority,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        existing = self._con.execute(
            """
            SELECT payload_json
            FROM portfolio_solutions
            WHERE solution_id = ?
            """,
            [solution.solution_id],
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != payload:
                raise ValueError("portfolio solution identity conflict")
            return False
        self._con.execute(
            """
            INSERT INTO portfolio_solutions
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                solution.solution_id,
                solution.model_id,
                solution.manifest_id,
                solution.dataset_id,
                solution.decision_time,
                solution.allocator.value,
                solution.engine_name,
                solution.engine_version,
                solution.constraint_policy_id,
                payload,
            ],
        )
        return True

    def close(self) -> None:
        self._con.close()


def validate_previous_weights(
    *,
    dataset: PortfolioDataset,
    previous_weights: tuple[PortfolioWeight, ...],
) -> tuple[PortfolioWeight, ...]:
    if len({item.security_id for item in previous_weights}) != len(
        previous_weights
    ):
        raise ValueError("previous weights contain duplicate securities")
    allowed = set(dataset.security_ids)
    if any(item.security_id not in allowed for item in previous_weights):
        raise ValueError("previous weights contain security outside dataset")
    if any(not item.weight.is_finite() for item in previous_weights):
        raise ValueError("previous weights must be finite")
    return tuple(sorted(previous_weights, key=lambda item: item.security_id))


def validate_portfolio_constraints(
    *,
    weights: tuple[PortfolioWeight, ...],
    net: Decimal,
    gross: Decimal,
    turnover: Decimal,
    constraints: PortfolioConstraintPolicy,
) -> None:
    tolerance = Decimal("1e-10")
    for item in weights:
        if item.weight < constraints.minimum_weight - tolerance:
            raise ValueError(
                f"{item.security_id} weight violates minimum_weight"
            )
        if item.weight > constraints.maximum_weight + tolerance:
            raise ValueError(
                f"{item.security_id} weight violates maximum_weight"
            )
        if constraints.long_only and item.weight < -tolerance:
            raise ValueError(
                "allocator produced short weight under long-only policy"
            )
    if constraints.fully_invested and abs(net - Decimal("1")) > tolerance:
        raise ValueError("fully invested allocation must sum to 1")
    if gross > constraints.maximum_gross_exposure + tolerance:
        raise ValueError("allocation exceeds maximum gross exposure")
    if (
        constraints.maximum_one_way_turnover is not None
        and turnover > constraints.maximum_one_way_turnover + tolerance
    ):
        raise ValueError("allocation exceeds maximum one-way turnover")


def calculate_one_way_turnover(
    *,
    target: tuple[PortfolioWeight, ...],
    previous: tuple[PortfolioWeight, ...],
) -> Decimal:
    target_map = {item.security_id: item.weight for item in target}
    previous_map = {item.security_id: item.weight for item in previous}
    security_ids = set(target_map) | set(previous_map)
    traded = sum(
        (
            abs(
                target_map.get(security_id, Decimal("0"))
                - previous_map.get(security_id, Decimal("0"))
            )
            for security_id in security_ids
        ),
        Decimal("0"),
    )
    target_cash = Decimal("1") - sum(
        target_map.values(),
        Decimal("0"),
    )
    previous_cash = Decimal("1") - sum(
        previous_map.values(),
        Decimal("0"),
    )
    return (
        traded + abs(target_cash - previous_cash)
    ) / Decimal("2")


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()