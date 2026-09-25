from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from .factor_contracts import FactorRun, FactorScore


class PortfolioMode(str, Enum):
    LONG_ONLY = "LONG_ONLY"
    LONG_SHORT = "LONG_SHORT"


class BenchmarkKind(str, Enum):
    CASH = "CASH"
    MARKET_CAP = "MARKET_CAP"
    EQUAL_WEIGHT = "EQUAL_WEIGHT"
    INVERSE_VOL = "INVERSE_VOL"
    CUSTOM = "CUSTOM"


@dataclass(frozen=True)
class PortfolioConstructionPolicy:
    mode: PortfolioMode
    long_count: int
    short_count: int
    long_gross: Decimal
    short_gross: Decimal
    maximum_absolute_weight: Decimal
    allow_boundary_tie_break: bool
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.long_count < 1:
            raise ValueError("long_count must be positive")
        if self.short_count < 0:
            raise ValueError("short_count cannot be negative")
        for name in (
            "long_gross",
            "short_gross",
            "maximum_absolute_weight",
        ):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.maximum_absolute_weight <= 0:
            raise ValueError("maximum_absolute_weight must be positive")
        if self.mode is PortfolioMode.LONG_ONLY:
            if self.short_count != 0 or self.short_gross != 0:
                raise ValueError("long-only policy cannot include short exposure")
            if self.long_gross > 1:
                raise ValueError("long-only gross exposure cannot exceed 1")
        else:
            if self.short_count < 1 or self.short_gross <= 0:
                raise ValueError("long-short policy requires short positions")
        long_weight = self.long_gross / Decimal(self.long_count)
        if long_weight > self.maximum_absolute_weight:
            raise ValueError("long position weight exceeds policy maximum")
        if self.short_count:
            short_weight = self.short_gross / Decimal(self.short_count)
            if short_weight > self.maximum_absolute_weight:
                raise ValueError("short position weight exceeds policy maximum")
        if not self.rationale.strip():
            raise ValueError("portfolio policy rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("portfolio policy requires evidence references")

    @property
    def policy_id(self) -> str:
        return _content_id(
            "portfolio-construction-policy",
            {
                "mode": self.mode.value,
                "long_count": self.long_count,
                "short_count": self.short_count,
                "long_gross": str(self.long_gross),
                "short_gross": str(self.short_gross),
                "maximum_absolute_weight": str(
                    self.maximum_absolute_weight
                ),
                "allow_boundary_tie_break": self.allow_boundary_tie_break,
                "rationale": self.rationale,
                "evidence_references": list(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class CostModel:
    commission_bps: Decimal
    half_spread_bps: Decimal
    slippage_bps: Decimal
    market_impact_bps: Decimal
    annual_borrow_bps: Decimal
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "commission_bps",
            "half_spread_bps",
            "slippage_bps",
            "market_impact_bps",
            "annual_borrow_bps",
        ):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if not self.rationale.strip():
            raise ValueError("cost model rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("cost model requires evidence references")

    @property
    def transaction_bps(self) -> Decimal:
        return (
            self.commission_bps
            + self.half_spread_bps
            + self.slippage_bps
            + self.market_impact_bps
        )

    @property
    def cost_model_id(self) -> str:
        return _content_id(
            "backtest-cost-model",
            {
                "commission_bps": str(self.commission_bps),
                "half_spread_bps": str(self.half_spread_bps),
                "slippage_bps": str(self.slippage_bps),
                "market_impact_bps": str(self.market_impact_bps),
                "annual_borrow_bps": str(self.annual_borrow_bps),
                "rationale": self.rationale,
                "evidence_references": list(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class BacktestPolicy:
    portfolio: PortfolioConstructionPolicy
    costs: CostModel
    required_benchmarks: tuple[BenchmarkKind, ...]
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.required_benchmarks:
            raise ValueError("backtest requires benchmark baselines")
        if len(self.required_benchmarks) != len(set(self.required_benchmarks)):
            raise ValueError("required benchmarks cannot contain duplicates")
        mandatory = {
            BenchmarkKind.CASH,
            BenchmarkKind.MARKET_CAP,
            BenchmarkKind.EQUAL_WEIGHT,
            BenchmarkKind.INVERSE_VOL,
        }
        if not mandatory.issubset(set(self.required_benchmarks)):
            raise ValueError(
                "backtest baselines must include cash, market-cap, "
                "equal-weight, and inverse-vol"
            )
        if not self.rationale.strip():
            raise ValueError("backtest policy rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("backtest policy requires evidence references")

    @property
    def policy_id(self) -> str:
        return _content_id(
            "backtest-policy",
            {
                "portfolio_policy_id": self.portfolio.policy_id,
                "cost_model_id": self.costs.cost_model_id,
                "required_benchmarks": [
                    item.value for item in self.required_benchmarks
                ],
                "rationale": self.rationale,
                "evidence_references": list(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class SecurityPeriodReturn:
    security_id: str
    start_time: datetime
    end_time: datetime
    total_return: Decimal
    delisted: bool
    source_fact_ids: tuple[str, ...]
    corporate_action_fact_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.security_id.strip():
            raise ValueError("security return requires security_id")
        if self.start_time.tzinfo is None or self.end_time.tzinfo is None:
            raise ValueError("security return timestamps must be timezone-aware")
        if self.end_time <= self.start_time:
            raise ValueError("security return end_time must follow start_time")
        if not self.total_return.is_finite() or self.total_return < Decimal("-1"):
            raise ValueError("security total_return must be finite and >= -1")
        if not self.source_fact_ids or not all(
            item.strip() for item in self.source_fact_ids
        ):
            raise ValueError("security return requires source fact IDs")
        if self.delisted and not self.corporate_action_fact_ids:
            raise ValueError(
                "delisted security return requires corporate-action fact IDs"
            )

    @property
    def return_id(self) -> str:
        return _content_id(
            "security-period-return",
            {
                "security_id": self.security_id,
                "start_time": self.start_time.isoformat(),
                "end_time": self.end_time.isoformat(),
                "total_return": str(self.total_return),
                "delisted": self.delisted,
                "source_fact_ids": list(self.source_fact_ids),
                "corporate_action_fact_ids": list(
                    self.corporate_action_fact_ids
                ),
            },
        )


@dataclass(frozen=True)
class BenchmarkPeriodReturn:
    kind: BenchmarkKind
    benchmark_id: str
    start_time: datetime
    end_time: datetime
    total_return: Decimal
    source_fact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.benchmark_id.strip():
            raise ValueError("benchmark_id is required")
        if self.start_time.tzinfo is None or self.end_time.tzinfo is None:
            raise ValueError("benchmark timestamps must be timezone-aware")
        if self.end_time <= self.start_time:
            raise ValueError("benchmark end_time must follow start_time")
        if not self.total_return.is_finite() or self.total_return < Decimal("-1"):
            raise ValueError("benchmark total_return must be finite and >= -1")
        if not self.source_fact_ids or not all(
            item.strip() for item in self.source_fact_ids
        ):
            raise ValueError("benchmark return requires source fact IDs")

    @property
    def return_id(self) -> str:
        return _content_id(
            "benchmark-period-return",
            {
                "kind": self.kind.value,
                "benchmark_id": self.benchmark_id,
                "start_time": self.start_time.isoformat(),
                "end_time": self.end_time.isoformat(),
                "total_return": str(self.total_return),
                "source_fact_ids": list(self.source_fact_ids),
            },
        )


@dataclass(frozen=True)
class RebalancePeriod:
    factor_run: FactorRun
    holding_start: datetime
    holding_end: datetime
    security_returns: tuple[SecurityPeriodReturn, ...]
    benchmark_returns: tuple[BenchmarkPeriodReturn, ...]

    def __post_init__(self) -> None:
        if self.holding_start.tzinfo is None or self.holding_end.tzinfo is None:
            raise ValueError("holding timestamps must be timezone-aware")
        if self.holding_start < self.factor_run.decision_time:
            raise ValueError("holding period cannot start before factor decision")
        if self.holding_end <= self.holding_start:
            raise ValueError("holding_end must follow holding_start")
        if len({item.return_id for item in self.security_returns}) != len(
            self.security_returns
        ):
            raise ValueError("duplicate security return observations")
        security_ids = [item.security_id for item in self.security_returns]
        if len(security_ids) != len(set(security_ids)):
            raise ValueError("multiple security returns for one holding period")
        if len({item.kind for item in self.benchmark_returns}) != len(
            self.benchmark_returns
        ):
            raise ValueError("multiple returns for one benchmark kind")
        for item in self.security_returns:
            if (
                item.start_time != self.holding_start
                or item.end_time != self.holding_end
            ):
                raise ValueError(
                    "security return interval must equal holding interval"
                )
        for item in self.benchmark_returns:
            if (
                item.start_time != self.holding_start
                or item.end_time != self.holding_end
            ):
                raise ValueError(
                    "benchmark return interval must equal holding interval"
                )


@dataclass(frozen=True)
class TargetPosition:
    security_id: str
    score: Decimal
    weight: Decimal


@dataclass(frozen=True)
class BacktestPeriodResult:
    factor_run_id: str
    decision_time: datetime
    holding_start: datetime
    holding_end: datetime
    pre_trade_weights: tuple[tuple[str, Decimal], ...]
    target_positions: tuple[TargetPosition, ...]
    cash_weight: Decimal
    traded_notional_ratio: Decimal
    one_way_turnover_ratio: Decimal
    gross_return: Decimal
    transaction_cost: Decimal
    borrow_cost: Decimal
    net_return: Decimal
    gross_wealth: Decimal
    net_wealth: Decimal
    benchmark_returns: tuple[tuple[BenchmarkKind, Decimal], ...]
    benchmark_wealth: tuple[tuple[BenchmarkKind, Decimal], ...]
    return_ids: tuple[str, ...]
    benchmark_return_ids: tuple[str, ...]


@dataclass(frozen=True)
class BacktestResult:
    backtest_id: str
    factor_id: str
    validation_plan_id: str
    policy_id: str
    periods: tuple[BacktestPeriodResult, ...]
    final_gross_wealth: Decimal
    final_net_wealth: Decimal
    final_benchmark_wealth: tuple[tuple[BenchmarkKind, Decimal], ...]


class BacktestEngine:
    """Deterministic score-to-portfolio simulation with explicit economics."""

    def run(
        self,
        *,
        factor_id: str,
        validation_plan_id: str,
        policy: BacktestPolicy,
        periods: tuple[RebalancePeriod, ...],
    ) -> BacktestResult:
        if not factor_id.startswith("factor-spec:"):
            raise ValueError("backtest requires factor-spec ID")
        if not validation_plan_id.startswith("walk-forward-plan:"):
            raise ValueError("backtest requires walk-forward validation plan ID")
        if not periods:
            raise ValueError("backtest requires rebalance periods")
        ordered = tuple(sorted(periods, key=lambda item: item.holding_start))
        if ordered != periods:
            raise ValueError("rebalance periods must be supplied chronologically")
        for index, period in enumerate(periods):
            if period.factor_run.factor_id != factor_id:
                raise ValueError("factor run does not match backtest factor")
            if index and period.holding_start < periods[index - 1].holding_end:
                raise ValueError("backtest holding periods cannot overlap")
            actual_benchmarks = {item.kind for item in period.benchmark_returns}
            required = set(policy.required_benchmarks)
            if actual_benchmarks != required:
                raise ValueError(
                    "each holding period must contain exactly the required benchmarks"
                )

        gross_wealth = Decimal("1")
        net_wealth = Decimal("1")
        benchmark_wealth = {
            item: Decimal("1") for item in policy.required_benchmarks
        }
        pre_trade_weights: dict[str, Decimal] = {}
        pre_trade_cash = Decimal("1")
        results: list[BacktestPeriodResult] = []

        for period in periods:
            targets = self._construct(
                scores=period.factor_run.scores,
                policy=policy.portfolio,
            )
            target_weights = {
                item.security_id: item.weight for item in targets
            }
            cash_weight = Decimal("1") - sum(
                target_weights.values(),
                Decimal("0"),
            )
            held_ids = set(target_weights)
            returns_by_id = {
                item.security_id: item for item in period.security_returns
            }
            missing = sorted(held_ids - set(returns_by_id))
            if missing:
                raise ValueError(
                    "missing holding-period returns for: " + ", ".join(missing)
                )

            traded_notional = sum(
                (
                    abs(
                        target_weights.get(security_id, Decimal("0"))
                        - pre_trade_weights.get(security_id, Decimal("0"))
                    )
                    for security_id in (
                        set(target_weights) | set(pre_trade_weights)
                    )
                ),
                Decimal("0"),
            )
            one_way_turnover = (
                traded_notional + abs(cash_weight - pre_trade_cash)
            ) / Decimal("2")
            transaction_cost = (
                traded_notional
                * policy.costs.transaction_bps
                / Decimal("10000")
            )
            days = Decimal(
                (period.holding_end - period.holding_start).total_seconds()
            ) / Decimal("86400")
            short_gross = sum(
                (
                    abs(weight)
                    for weight in target_weights.values()
                    if weight < 0
                ),
                Decimal("0"),
            )
            borrow_cost = (
                short_gross
                * policy.costs.annual_borrow_bps
                / Decimal("10000")
                * days
                / Decimal("365")
            )
            gross_return = sum(
                (
                    weight * returns_by_id[security_id].total_return
                    for security_id, weight in target_weights.items()
                ),
                Decimal("0"),
            )
            net_return = gross_return - transaction_cost - borrow_cost
            if Decimal("1") + net_return <= 0:
                raise ValueError("net backtest wealth would become non-positive")
            gross_wealth *= Decimal("1") + gross_return
            net_wealth *= Decimal("1") + net_return

            period_benchmark_returns: list[tuple[BenchmarkKind, Decimal]] = []
            for benchmark in sorted(
                period.benchmark_returns,
                key=lambda item: item.kind.value,
            ):
                benchmark_wealth[benchmark.kind] *= (
                    Decimal("1") + benchmark.total_return
                )
                period_benchmark_returns.append(
                    (benchmark.kind, benchmark.total_return)
                )

            pre_snapshot = tuple(
                sorted(pre_trade_weights.items(), key=lambda item: item[0])
            )
            benchmark_wealth_snapshot = tuple(
                sorted(
                    benchmark_wealth.items(),
                    key=lambda item: item[0].value,
                )
            )
            results.append(
                BacktestPeriodResult(
                    factor_run_id=period.factor_run.run_id,
                    decision_time=period.factor_run.decision_time,
                    holding_start=period.holding_start,
                    holding_end=period.holding_end,
                    pre_trade_weights=pre_snapshot,
                    target_positions=targets,
                    cash_weight=cash_weight,
                    traded_notional_ratio=traded_notional,
                    one_way_turnover_ratio=one_way_turnover,
                    gross_return=gross_return,
                    transaction_cost=transaction_cost,
                    borrow_cost=borrow_cost,
                    net_return=net_return,
                    gross_wealth=gross_wealth,
                    net_wealth=net_wealth,
                    benchmark_returns=tuple(period_benchmark_returns),
                    benchmark_wealth=benchmark_wealth_snapshot,
                    return_ids=tuple(
                        sorted(
                            returns_by_id[security_id].return_id
                            for security_id in held_ids
                        )
                    ),
                    benchmark_return_ids=tuple(
                        sorted(
                            item.return_id
                            for item in period.benchmark_returns
                        )
                    ),
                )
            )

            equity_factor = Decimal("1") + gross_return
            post_weights: dict[str, Decimal] = {}
            for security_id, weight in target_weights.items():
                post_weights[security_id] = (
                    weight
                    * (Decimal("1") + returns_by_id[security_id].total_return)
                    / equity_factor
                )
            pre_trade_weights = post_weights
            pre_trade_cash = cash_weight / equity_factor

        result_periods = tuple(results)
        payload = {
            "factor_id": factor_id,
            "validation_plan_id": validation_plan_id,
            "policy_id": policy.policy_id,
            "periods": [
                {
                    "factor_run_id": item.factor_run_id,
                    "holding_start": item.holding_start.isoformat(),
                    "holding_end": item.holding_end.isoformat(),
                    "target_positions": [
                        {
                            "security_id": position.security_id,
                            "score": str(position.score),
                            "weight": str(position.weight),
                        }
                        for position in item.target_positions
                    ],
                    "traded_notional_ratio": str(
                        item.traded_notional_ratio
                    ),
                    "gross_return": str(item.gross_return),
                    "transaction_cost": str(item.transaction_cost),
                    "borrow_cost": str(item.borrow_cost),
                    "net_return": str(item.net_return),
                    "return_ids": list(item.return_ids),
                    "benchmark_return_ids": list(
                        item.benchmark_return_ids
                    ),
                }
                for item in result_periods
            ],
        }
        return BacktestResult(
            backtest_id=_content_id("economic-backtest", payload),
            factor_id=factor_id,
            validation_plan_id=validation_plan_id,
            policy_id=policy.policy_id,
            periods=result_periods,
            final_gross_wealth=gross_wealth,
            final_net_wealth=net_wealth,
            final_benchmark_wealth=tuple(
                sorted(
                    benchmark_wealth.items(),
                    key=lambda item: item[0].value,
                )
            ),
        )

    @staticmethod
    def _construct(
        *,
        scores: tuple[FactorScore, ...],
        policy: PortfolioConstructionPolicy,
    ) -> tuple[TargetPosition, ...]:
        required = policy.long_count + policy.short_count
        if len(scores) < required:
            raise ValueError("not enough scored securities for portfolio policy")
        ordered = sorted(
            scores,
            key=lambda item: (-item.score, item.security_id),
        )
        if (
            not policy.allow_boundary_tie_break
            and policy.long_count < len(ordered)
            and ordered[policy.long_count - 1].score
            == ordered[policy.long_count].score
        ):
            raise ValueError("long selection boundary cuts through a score tie")
        longs = ordered[: policy.long_count]

        shorts: list[FactorScore] = []
        if policy.short_count:
            ascending = sorted(
                scores,
                key=lambda item: (item.score, item.security_id),
            )
            if (
                not policy.allow_boundary_tie_break
                and policy.short_count < len(ascending)
                and ascending[policy.short_count - 1].score
                == ascending[policy.short_count].score
            ):
                raise ValueError("short selection boundary cuts through a score tie")
            shorts = ascending[: policy.short_count]
            if {item.security_id for item in longs} & {
                item.security_id for item in shorts
            }:
                raise ValueError("long and short selections overlap")

        long_weight = policy.long_gross / Decimal(policy.long_count)
        positions = [
            TargetPosition(item.security_id, item.score, long_weight)
            for item in longs
        ]
        if shorts:
            short_weight = -policy.short_gross / Decimal(policy.short_count)
            positions.extend(
                TargetPosition(item.security_id, item.score, short_weight)
                for item in shorts
            )
        return tuple(sorted(positions, key=lambda item: item.security_id))


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
