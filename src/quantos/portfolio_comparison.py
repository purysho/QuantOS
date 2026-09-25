from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

import duckdb

from .portfolio_construction import (
    BaselineAllocator,
    PortfolioSolution,
    PortfolioWeight,
    portfolio_solution_identity,
)
from .portfolio_hierarchical import (
    HierarchicalAllocator,
    HierarchicalPortfolioSolution,
    hierarchical_portfolio_solution_identity,
)
from .portfolio_optimization import (
    OptimizedPortfolioSolution,
    optimized_portfolio_solution_identity,
)


class PortfolioMethod(str, Enum):
    EQUAL_WEIGHT = "EQUAL_WEIGHT"
    INVERSE_VOLATILITY = "INVERSE_VOLATILITY"
    MINIMUM_VARIANCE = "MINIMUM_VARIANCE"
    HRP = "HRP"
    HERC = "HERC"


@dataclass(frozen=True)
class OutOfSampleSecurityReturn:
    security_id: str
    period_start: datetime
    period_end: datetime
    total_return: Decimal
    source_fact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.security_id.strip():
            raise ValueError("OOS security return requires security_id")
        if self.period_start.tzinfo is None or self.period_end.tzinfo is None:
            raise ValueError("OOS return timestamps must be timezone-aware")
        if self.period_end <= self.period_start:
            raise ValueError("OOS period_end must follow period_start")
        if not self.total_return.is_finite() or self.total_return <= Decimal("-1"):
            raise ValueError("OOS total_return must be finite and greater than -1")
        if not self.source_fact_ids or not all(
            item.strip() for item in self.source_fact_ids
        ):
            raise ValueError("OOS security return requires source fact IDs")

    @property
    def return_id(self) -> str:
        return _content_id(
            "portfolio-oos-return",
            {
                "security_id": self.security_id,
                "period_start": self.period_start.isoformat(),
                "period_end": self.period_end.isoformat(),
                "total_return": str(self.total_return),
                "source_fact_ids": list(self.source_fact_ids),
            },
        )


@dataclass(frozen=True)
class OutOfSampleBenchmarkReturn:
    benchmark_id: str
    period_start: datetime
    period_end: datetime
    total_return: Decimal
    source_fact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.benchmark_id.strip():
            raise ValueError("OOS benchmark_id is required")
        if self.period_start.tzinfo is None or self.period_end.tzinfo is None:
            raise ValueError("OOS benchmark timestamps must be timezone-aware")
        if self.period_end <= self.period_start:
            raise ValueError("OOS benchmark period_end must follow period_start")
        if not self.total_return.is_finite() or self.total_return <= Decimal("-1"):
            raise ValueError(
                "OOS benchmark return must be finite and greater than -1"
            )
        if not self.source_fact_ids or not all(
            item.strip() for item in self.source_fact_ids
        ):
            raise ValueError("OOS benchmark return requires source fact IDs")

    @property
    def return_id(self) -> str:
        return _content_id(
            "portfolio-oos-benchmark-return",
            {
                "benchmark_id": self.benchmark_id,
                "period_start": self.period_start.isoformat(),
                "period_end": self.period_end.isoformat(),
                "total_return": str(self.total_return),
                "source_fact_ids": list(self.source_fact_ids),
            },
        )


@dataclass(frozen=True)
class PortfolioComparisonFold:
    fold_number: int
    period_start: datetime
    period_end: datetime
    baselines: tuple[PortfolioSolution, ...]
    optimized: OptimizedPortfolioSolution
    security_returns: tuple[OutOfSampleSecurityReturn, ...]
    market_benchmark: OutOfSampleBenchmarkReturn
    hierarchical: tuple[HierarchicalPortfolioSolution, ...] = ()

    def __post_init__(self) -> None:
        if self.fold_number < 0:
            raise ValueError("portfolio comparison fold_number cannot be negative")
        if self.period_start.tzinfo is None or self.period_end.tzinfo is None:
            raise ValueError("portfolio comparison fold timestamps must be aware")
        if self.period_end <= self.period_start:
            raise ValueError("portfolio comparison period_end must follow start")
        for item in self.security_returns:
            if (
                item.period_start != self.period_start
                or item.period_end != self.period_end
            ):
                raise ValueError(
                    "every OOS security return must match comparison fold interval"
                )
        if (
            self.market_benchmark.period_start != self.period_start
            or self.market_benchmark.period_end != self.period_end
        ):
            raise ValueError(
                "OOS benchmark return must match comparison fold interval"
            )

    @property
    def fold_id(self) -> str:
        return _content_id(
            "portfolio-comparison-fold",
            {
                "fold_number": self.fold_number,
                "period_start": self.period_start.isoformat(),
                "period_end": self.period_end.isoformat(),
                "baseline_solution_ids": sorted(
                    item.solution_id for item in self.baselines
                ),
                "optimized_solution_id": self.optimized.solution_id,
                "hierarchical_solution_ids": sorted(
                    item.solution_id for item in self.hierarchical
                ),
                "security_return_ids": sorted(
                    item.return_id for item in self.security_returns
                ),
                "benchmark_return_id": self.market_benchmark.return_id,
            },
        )


@dataclass(frozen=True)
class PortfolioComparisonPolicy:
    minimum_folds: int
    implementation_cost_bps_per_traded_notional: Decimal
    expected_shortfall_confidence: Decimal
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.minimum_folds < 2:
            raise ValueError("portfolio comparison requires at least two folds")
        if (
            not self.implementation_cost_bps_per_traded_notional.is_finite()
            or self.implementation_cost_bps_per_traded_notional < 0
        ):
            raise ValueError(
                "implementation cost bps must be finite and non-negative"
            )
        if (
            not self.expected_shortfall_confidence.is_finite()
            or self.expected_shortfall_confidence <= 0
            or self.expected_shortfall_confidence >= 1
        ):
            raise ValueError(
                "expected_shortfall_confidence must be between 0 and 1"
            )
        if not self.rationale.strip():
            raise ValueError("portfolio comparison policy rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError(
                "portfolio comparison policy requires evidence references"
            )

    @property
    def policy_id(self) -> str:
        return _content_id(
            "portfolio-comparison-policy",
            {
                "minimum_folds": self.minimum_folds,
                "implementation_cost_bps_per_traded_notional": str(
                    self.implementation_cost_bps_per_traded_notional
                ),
                "expected_shortfall_confidence": str(
                    self.expected_shortfall_confidence
                ),
                "rationale": self.rationale,
                "evidence_references": list(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class PortfolioFoldOutcome:
    fold_id: str
    method: PortfolioMethod
    solution_id: str
    gross_return: Decimal
    implementation_cost_rate: Decimal
    net_return: Decimal
    one_way_turnover: Decimal
    effective_number_of_assets: Decimal
    maximum_absolute_weight: Decimal


@dataclass(frozen=True)
class PortfolioMethodEvaluation:
    method: PortfolioMethod
    solution_ids: tuple[str, ...]
    fold_outcomes: tuple[PortfolioFoldOutcome, ...]
    cumulative_net_return: Decimal
    realized_period_volatility: Decimal
    maximum_drawdown: Decimal
    expected_shortfall_return: Decimal
    total_implementation_cost_rate: Decimal
    average_one_way_turnover: Decimal
    average_effective_number_of_assets: Decimal
    maximum_absolute_weight: Decimal
    market_relative_wealth_return: Decimal


@dataclass(frozen=True)
class PortfolioComparisonDossier:
    dossier_id: str
    model_id: str
    manifest_id: str
    constraint_policy_id: str
    comparison_policy_id: str
    fold_ids: tuple[str, ...]
    benchmark_id: str
    evaluations: tuple[PortfolioMethodEvaluation, ...]
    selection_authority: str
    capital_authority: str
    caveat: str


class PortfolioComparisonEngine:
    """Common-OOS portfolio comparison without an automatic winner."""

    CAVEAT = (
        "The dossier compares frozen portfolio methods on identical out-of-sample "
        "periods. It does not select a winner, authorize capital, or establish that "
        "past out-of-sample behavior will persist."
    )

    def evaluate(
        self,
        *,
        folds: tuple[PortfolioComparisonFold, ...],
        policy: PortfolioComparisonPolicy,
    ) -> PortfolioComparisonDossier:
        if len(folds) < policy.minimum_folds:
            raise ValueError("too few OOS folds for portfolio comparison")
        if len({item.fold_id for item in folds}) != len(folds):
            raise ValueError("duplicate portfolio comparison folds")
        ordered = tuple(sorted(folds, key=lambda item: item.period_start))
        if ordered != folds:
            raise ValueError("portfolio comparison folds must be chronological")
        for index, fold in enumerate(folds):
            if index and fold.period_start < folds[index - 1].period_end:
                raise ValueError("portfolio comparison OOS folds cannot overlap")

        contexts = tuple(self._validate_fold(item) for item in folds)
        model_ids = {item["model_id"] for item in contexts}
        manifest_ids = {item["manifest_id"] for item in contexts}
        constraint_ids = {item["constraint_policy_id"] for item in contexts}
        benchmark_ids = {item.market_benchmark.benchmark_id for item in folds}
        if len(model_ids) != 1 or len(manifest_ids) != 1:
            raise ValueError(
                "all portfolio comparison folds must belong to one Research Run"
            )
        if len(constraint_ids) != 1:
            raise ValueError(
                "all portfolio comparison folds must use one constraint policy"
            )
        if len(benchmark_ids) != 1:
            raise ValueError("all portfolio comparison folds require one benchmark")

        method_sets = tuple(
            frozenset(context["solutions"]) for context in contexts
        )
        if any(item != method_sets[0] for item in method_sets[1:]):
            raise ValueError(
                "every OOS fold must contain the same portfolio methods"
            )
        active_methods = tuple(
            sorted(method_sets[0], key=lambda item: item.value)
        )
        outcomes: dict[PortfolioMethod, list[PortfolioFoldOutcome]] = {
            method: [] for method in active_methods
        }
        benchmark_wealth = Decimal("1")
        for fold, context in zip(folds, contexts):
            returns = {
                item.security_id: item.total_return
                for item in fold.security_returns
            }
            benchmark_wealth *= Decimal("1") + fold.market_benchmark.total_return
            for method, solution in context["solutions"].items():
                weights = solution.weights
                gross_return = sum(
                    (
                        item.weight * returns[item.security_id]
                        for item in weights
                    ),
                    Decimal("0"),
                )
                traded_notional = _security_traded_notional(
                    target=weights,
                    previous=solution.previous_weights,
                )
                implementation_cost = (
                    traded_notional
                    * policy.implementation_cost_bps_per_traded_notional
                    / Decimal("10000")
                )
                net_return = gross_return - implementation_cost
                if Decimal("1") + net_return <= 0:
                    raise ValueError(
                        "portfolio comparison net wealth would become non-positive"
                    )
                hhi = sum(
                    (item.weight * item.weight for item in weights),
                    Decimal("0"),
                )
                if hhi <= 0:
                    raise ValueError("portfolio concentration is undefined")
                effective_assets = Decimal("1") / hhi
                max_weight = max(abs(item.weight) for item in weights)
                outcomes[method].append(
                    PortfolioFoldOutcome(
                        fold_id=fold.fold_id,
                        method=method,
                        solution_id=solution.solution_id,
                        gross_return=gross_return,
                        implementation_cost_rate=implementation_cost,
                        net_return=net_return,
                        one_way_turnover=solution.one_way_turnover,
                        effective_number_of_assets=effective_assets,
                        maximum_absolute_weight=max_weight,
                    )
                )

        evaluations = tuple(
            self._summarize(
                method=method,
                outcomes=tuple(outcomes[method]),
                benchmark_wealth=benchmark_wealth,
                confidence=policy.expected_shortfall_confidence,
            )
            for method in active_methods
        )
        fold_ids = tuple(item.fold_id for item in folds)
        payload = {
            "model_id": next(iter(model_ids)),
            "manifest_id": next(iter(manifest_ids)),
            "constraint_policy_id": next(iter(constraint_ids)),
            "comparison_policy_id": policy.policy_id,
            "fold_ids": list(fold_ids),
            "benchmark_id": next(iter(benchmark_ids)),
            "evaluation_summaries": [
                {
                    "method": item.method.value,
                    "solution_ids": list(item.solution_ids),
                    "cumulative_net_return": str(item.cumulative_net_return),
                    "realized_period_volatility": str(
                        item.realized_period_volatility
                    ),
                    "maximum_drawdown": str(item.maximum_drawdown),
                    "expected_shortfall_return": str(
                        item.expected_shortfall_return
                    ),
                    "total_implementation_cost_rate": str(
                        item.total_implementation_cost_rate
                    ),
                    "average_one_way_turnover": str(
                        item.average_one_way_turnover
                    ),
                    "average_effective_number_of_assets": str(
                        item.average_effective_number_of_assets
                    ),
                    "maximum_absolute_weight": str(
                        item.maximum_absolute_weight
                    ),
                    "market_relative_wealth_return": str(
                        item.market_relative_wealth_return
                    ),
                }
                for item in evaluations
            ],
            "selection_authority": "NONE",
            "capital_authority": "NONE",
        }
        return PortfolioComparisonDossier(
            dossier_id=_content_id("portfolio-comparison-dossier", payload),
            model_id=next(iter(model_ids)),
            manifest_id=next(iter(manifest_ids)),
            constraint_policy_id=next(iter(constraint_ids)),
            comparison_policy_id=policy.policy_id,
            fold_ids=fold_ids,
            benchmark_id=next(iter(benchmark_ids)),
            evaluations=evaluations,
            selection_authority="NONE",
            capital_authority="NONE",
            caveat=self.CAVEAT,
        )

    @staticmethod
    def _validate_fold(
        fold: PortfolioComparisonFold,
    ) -> dict[str, object]:
        if len(fold.baselines) != 2:
            raise ValueError("each OOS fold requires exactly two baselines")
        if any(
            item.solution_id != portfolio_solution_identity(item)
            for item in fold.baselines
        ):
            raise ValueError("baseline solution identity does not match content")
        if (
            fold.optimized.solution_id
            != optimized_portfolio_solution_identity(fold.optimized)
        ):
            raise ValueError("optimized solution identity does not match content")

        baseline_by_method: dict[PortfolioMethod, PortfolioSolution] = {}
        for item in fold.baselines:
            method = (
                PortfolioMethod.EQUAL_WEIGHT
                if item.allocator is BaselineAllocator.EQUAL_WEIGHT
                else PortfolioMethod.INVERSE_VOLATILITY
            )
            if method in baseline_by_method:
                raise ValueError("duplicate baseline method in OOS fold")
            baseline_by_method[method] = item
        if set(baseline_by_method) != {
            PortfolioMethod.EQUAL_WEIGHT,
            PortfolioMethod.INVERSE_VOLATILITY,
        }:
            raise ValueError(
                "each OOS fold requires EqualWeight and InverseVolatility"
            )
        if set(fold.optimized.baseline_solution_ids) != {
            item.solution_id for item in fold.baselines
        }:
            raise ValueError(
                "optimized solution does not cite the exact fold baselines"
            )

        hierarchical_by_method: dict[
            PortfolioMethod,
            HierarchicalPortfolioSolution,
        ] = {}
        if fold.hierarchical:
            if len(fold.hierarchical) != 2:
                raise ValueError(
                    "hierarchical comparison requires exactly HRP and HERC"
                )
            for item in fold.hierarchical:
                if (
                    item.solution_id
                    != hierarchical_portfolio_solution_identity(item)
                ):
                    raise ValueError(
                        "hierarchical solution identity does not match content"
                    )
                method = (
                    PortfolioMethod.HRP
                    if item.allocator is HierarchicalAllocator.HRP
                    else PortfolioMethod.HERC
                )
                if method in hierarchical_by_method:
                    raise ValueError(
                        "duplicate hierarchical method in OOS fold"
                    )
                hierarchical_by_method[method] = item
            if set(hierarchical_by_method) != {
                PortfolioMethod.HRP,
                PortfolioMethod.HERC,
            }:
                raise ValueError(
                    "hierarchical comparison requires HRP and HERC"
                )

        solutions = {
            **baseline_by_method,
            PortfolioMethod.MINIMUM_VARIANCE: fold.optimized,
            **hierarchical_by_method,
        }
        model_ids = {item.model_id for item in solutions.values()}
        manifest_ids = {item.manifest_id for item in solutions.values()}
        dataset_ids = {item.dataset_id for item in solutions.values()}
        constraint_ids = {
            item.constraint_policy_id for item in solutions.values()
        }
        decision_times = {item.decision_time for item in solutions.values()}
        if (
            len(model_ids) != 1
            or len(manifest_ids) != 1
            or len(dataset_ids) != 1
            or len(constraint_ids) != 1
            or len(decision_times) != 1
        ):
            raise ValueError(
                "all methods in an OOS fold must share model, manifest, "
                "training dataset, constraints, and decision time"
            )
        decision_time = next(iter(decision_times))
        if decision_time > fold.period_start:
            raise ValueError("portfolio solution decision occurs after OOS start")

        if len({item.return_id for item in fold.security_returns}) != len(
            fold.security_returns
        ):
            raise ValueError("duplicate OOS security return observations")
        returns_by_security = {
            item.security_id: item for item in fold.security_returns
        }
        security_sets = [
            {item.security_id for item in solution.weights}
            for solution in solutions.values()
        ]
        if any(security_set != security_sets[0] for security_set in security_sets):
            raise ValueError("candidate methods use different security sets")
        if set(returns_by_security) != security_sets[0]:
            raise ValueError(
                "OOS returns must cover exactly the common candidate security set"
            )

        tolerance = Decimal("1e-9")
        for solution in solutions.values():
            if solution.capital_authority != "NONE":
                raise ValueError("comparison candidate has capital authority")
            net = sum(
                (item.weight for item in solution.weights),
                Decimal("0"),
            )
            gross = sum(
                (abs(item.weight) for item in solution.weights),
                Decimal("0"),
            )
            if abs(net - Decimal("1")) > tolerance:
                raise ValueError("Stage 10.4 comparison requires full investment")
            if abs(gross - Decimal("1")) > tolerance:
                raise ValueError("Stage 10.4 comparison requires long-only weights")
            if any(item.weight < -tolerance for item in solution.weights):
                raise ValueError("Stage 10.4 comparison requires long-only weights")

        return {
            "model_id": next(iter(model_ids)),
            "manifest_id": next(iter(manifest_ids)),
            "constraint_policy_id": next(iter(constraint_ids)),
            "solutions": solutions,
        }

    @staticmethod
    def _summarize(
        *,
        method: PortfolioMethod,
        outcomes: tuple[PortfolioFoldOutcome, ...],
        benchmark_wealth: Decimal,
        confidence: Decimal,
    ) -> PortfolioMethodEvaluation:
        returns = tuple(item.net_return for item in outcomes)
        wealth = Decimal("1")
        peak = Decimal("1")
        maximum_drawdown = Decimal("0")
        for value in returns:
            wealth *= Decimal("1") + value
            if wealth > peak:
                peak = wealth
            drawdown = wealth / peak - Decimal("1")
            if drawdown < maximum_drawdown:
                maximum_drawdown = drawdown

        realized_volatility = _sample_std(returns)
        tail_count = max(
            1,
            math.ceil(
                float(
                    Decimal(len(returns))
                    * (Decimal("1") - confidence)
                )
            ),
        )
        expected_shortfall = _mean(tuple(sorted(returns)[:tail_count]))
        return PortfolioMethodEvaluation(
            method=method,
            solution_ids=tuple(item.solution_id for item in outcomes),
            fold_outcomes=outcomes,
            cumulative_net_return=wealth - Decimal("1"),
            realized_period_volatility=realized_volatility,
            maximum_drawdown=maximum_drawdown,
            expected_shortfall_return=expected_shortfall,
            total_implementation_cost_rate=sum(
                (item.implementation_cost_rate for item in outcomes),
                Decimal("0"),
            ),
            average_one_way_turnover=_mean(
                tuple(item.one_way_turnover for item in outcomes)
            ),
            average_effective_number_of_assets=_mean(
                tuple(item.effective_number_of_assets for item in outcomes)
            ),
            maximum_absolute_weight=max(
                item.maximum_absolute_weight for item in outcomes
            ),
            market_relative_wealth_return=(
                wealth / benchmark_wealth - Decimal("1")
            ),
        )


class PortfolioComparisonDossierStore:
    """Immutable persistence for OOS portfolio comparison dossiers."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_comparison_dossiers (
                dossier_id VARCHAR PRIMARY KEY,
                model_id VARCHAR NOT NULL,
                manifest_id VARCHAR NOT NULL,
                constraint_policy_id VARCHAR NOT NULL,
                comparison_policy_id VARCHAR NOT NULL,
                benchmark_id VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, dossier: PortfolioComparisonDossier) -> bool:
        payload = json.dumps(
            _jsonable_dossier(dossier),
            sort_keys=True,
            separators=(",", ":"),
        )
        row = self._con.execute(
            """
            SELECT payload_json
            FROM portfolio_comparison_dossiers
            WHERE dossier_id = ?
            """,
            [dossier.dossier_id],
        ).fetchone()
        if row is not None:
            if str(row[0]) != payload:
                raise ValueError("portfolio comparison dossier identity conflict")
            return False
        self._con.execute(
            """
            INSERT INTO portfolio_comparison_dossiers
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                dossier.dossier_id,
                dossier.model_id,
                dossier.manifest_id,
                dossier.constraint_policy_id,
                dossier.comparison_policy_id,
                dossier.benchmark_id,
                payload,
            ],
        )
        return True

    def close(self) -> None:
        self._con.close()


def _security_traded_notional(
    *,
    target: tuple[PortfolioWeight, ...],
    previous: tuple[PortfolioWeight, ...],
) -> Decimal:
    target_map = {item.security_id: item.weight for item in target}
    previous_map = {item.security_id: item.weight for item in previous}
    return sum(
        (
            abs(
                target_map.get(security_id, Decimal("0"))
                - previous_map.get(security_id, Decimal("0"))
            )
            for security_id in set(target_map) | set(previous_map)
        ),
        Decimal("0"),
    )


def _mean(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        raise ValueError("mean requires values")
    return sum(values, Decimal("0")) / Decimal(len(values))


def _sample_std(values: tuple[Decimal, ...]) -> Decimal:
    if len(values) < 2:
        raise ValueError("sample standard deviation requires two values")
    mean = _mean(values)
    variance = sum(
        ((item - mean) ** 2 for item in values),
        Decimal("0"),
    ) / Decimal(len(values) - 1)
    return variance.sqrt()


def _jsonable_dossier(
    dossier: PortfolioComparisonDossier,
) -> dict[str, object]:
    return {
        "dossier_id": dossier.dossier_id,
        "model_id": dossier.model_id,
        "manifest_id": dossier.manifest_id,
        "constraint_policy_id": dossier.constraint_policy_id,
        "comparison_policy_id": dossier.comparison_policy_id,
        "fold_ids": list(dossier.fold_ids),
        "benchmark_id": dossier.benchmark_id,
        "evaluations": [
            {
                "method": item.method.value,
                "solution_ids": list(item.solution_ids),
                "fold_outcomes": [
                    {
                        "fold_id": fold.fold_id,
                        "method": fold.method.value,
                        "solution_id": fold.solution_id,
                        "gross_return": str(fold.gross_return),
                        "implementation_cost_rate": str(
                            fold.implementation_cost_rate
                        ),
                        "net_return": str(fold.net_return),
                        "one_way_turnover": str(fold.one_way_turnover),
                        "effective_number_of_assets": str(
                            fold.effective_number_of_assets
                        ),
                        "maximum_absolute_weight": str(
                            fold.maximum_absolute_weight
                        ),
                    }
                    for fold in item.fold_outcomes
                ],
                "cumulative_net_return": str(item.cumulative_net_return),
                "realized_period_volatility": str(
                    item.realized_period_volatility
                ),
                "maximum_drawdown": str(item.maximum_drawdown),
                "expected_shortfall_return": str(
                    item.expected_shortfall_return
                ),
                "total_implementation_cost_rate": str(
                    item.total_implementation_cost_rate
                ),
                "average_one_way_turnover": str(
                    item.average_one_way_turnover
                ),
                "average_effective_number_of_assets": str(
                    item.average_effective_number_of_assets
                ),
                "maximum_absolute_weight": str(
                    item.maximum_absolute_weight
                ),
                "market_relative_wealth_return": str(
                    item.market_relative_wealth_return
                ),
            }
            for item in dossier.evaluations
        ],
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