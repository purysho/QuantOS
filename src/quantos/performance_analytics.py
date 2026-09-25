from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from .backtest_economics import (
    BacktestResult,
    BenchmarkKind,
    RebalancePeriod,
)


class MetricStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    INSUFFICIENT_OBSERVATIONS = "INSUFFICIENT_OBSERVATIONS"
    ZERO_VARIANCE = "ZERO_VARIANCE"
    PARTIAL = "PARTIAL"


@dataclass(frozen=True)
class PerformancePolicy:
    periods_per_year: int
    cvar_confidence: Decimal
    minimum_periods_for_risk_metrics: int
    minimum_ic_cross_section: int
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.periods_per_year < 1:
            raise ValueError("periods_per_year must be positive")
        if (
            not self.cvar_confidence.is_finite()
            or self.cvar_confidence <= 0
            or self.cvar_confidence >= 1
        ):
            raise ValueError("cvar_confidence must be between 0 and 1")
        if self.minimum_periods_for_risk_metrics < 2:
            raise ValueError(
                "minimum_periods_for_risk_metrics must be at least 2"
            )
        if self.minimum_ic_cross_section < 3:
            raise ValueError("minimum_ic_cross_section must be at least 3")
        if not self.rationale.strip():
            raise ValueError("performance policy rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("performance policy requires evidence references")

    @property
    def policy_id(self) -> str:
        return _content_id(
            "performance-policy",
            {
                "periods_per_year": self.periods_per_year,
                "cvar_confidence": str(self.cvar_confidence),
                "minimum_periods_for_risk_metrics": (
                    self.minimum_periods_for_risk_metrics
                ),
                "minimum_ic_cross_section": self.minimum_ic_cross_section,
                "rationale": self.rationale,
                "evidence_references": list(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class RiskAdjustedMetric:
    status: MetricStatus
    value: Decimal | None
    observations: int
    reason: str | None = None


@dataclass(frozen=True)
class BenchmarkAnalytics:
    kind: BenchmarkKind
    final_wealth: Decimal
    relative_wealth_return: Decimal
    tracking_error: RiskAdjustedMetric
    information_ratio: RiskAdjustedMetric


@dataclass(frozen=True)
class ICPeriod:
    factor_run_id: str
    value: Decimal | None
    cross_section_size: int
    status: MetricStatus
    reason: str | None


@dataclass(frozen=True)
class PerformanceAnalysis:
    analysis_id: str
    backtest_id: str
    policy_id: str
    period_count: int
    cumulative_gross_return: Decimal
    cumulative_net_return: Decimal
    annualized_net_return: Decimal
    annualized_net_volatility: RiskAdjustedMetric
    sharpe_ratio: RiskAdjustedMetric
    maximum_drawdown: Decimal
    expected_shortfall_return: RiskAdjustedMetric
    total_transaction_cost_rate: Decimal
    total_borrow_cost_rate: Decimal
    terminal_wealth_cost_drag: Decimal
    average_one_way_turnover: Decimal
    benchmark_analytics: tuple[BenchmarkAnalytics, ...]
    ic_periods: tuple[ICPeriod, ...]
    mean_information_coefficient: RiskAdjustedMetric


class PerformanceAnalyticsEngine:
    """Auditable diagnostics over a frozen economic backtest."""

    def analyze(
        self,
        *,
        backtest: BacktestResult,
        policy: PerformancePolicy,
        source_periods: tuple[RebalancePeriod, ...] = (),
    ) -> PerformanceAnalysis:
        if not backtest.periods:
            raise ValueError("performance analysis requires backtest periods")
        net_returns = tuple(item.net_return for item in backtest.periods)
        gross_returns = tuple(item.gross_return for item in backtest.periods)
        period_count = len(net_returns)

        annualized_net_return = _annualize_wealth(
            backtest.final_net_wealth,
            periods=period_count,
            periods_per_year=policy.periods_per_year,
        )
        volatility = _annualized_volatility(
            net_returns,
            policy=policy,
        )
        cash_returns = tuple(
            _benchmark_return(item, BenchmarkKind.CASH)
            for item in backtest.periods
        )
        excess = tuple(
            value - cash
            for value, cash in zip(net_returns, cash_returns)
        )
        sharpe = _annualized_ratio(excess, policy=policy)

        max_drawdown = _maximum_drawdown(
            tuple(item.net_wealth for item in backtest.periods)
        )
        expected_shortfall = _expected_shortfall(
            net_returns,
            policy=policy,
        )

        benchmark_analytics = tuple(
            self._benchmark_analytics(
                backtest=backtest,
                kind=kind,
                policy=policy,
            )
            for kind, _ in backtest.final_benchmark_wealth
        )

        ic_periods = self._information_coefficients(
            backtest=backtest,
            source_periods=source_periods,
            minimum_cross_section=policy.minimum_ic_cross_section,
        )
        available_ic = tuple(
            item.value
            for item in ic_periods
            if item.value is not None
            and item.status is MetricStatus.AVAILABLE
        )
        if not source_periods:
            mean_ic = RiskAdjustedMetric(
                status=MetricStatus.INSUFFICIENT_OBSERVATIONS,
                value=None,
                observations=0,
                reason="Source rebalance periods were not supplied for IC.",
            )
        elif not available_ic:
            mean_ic = RiskAdjustedMetric(
                status=MetricStatus.INSUFFICIENT_OBSERVATIONS,
                value=None,
                observations=0,
                reason="No holding period had a complete eligible IC cross-section.",
            )
        else:
            partial = any(
                item.status is not MetricStatus.AVAILABLE
                for item in ic_periods
            )
            mean_ic = RiskAdjustedMetric(
                status=(
                    MetricStatus.PARTIAL
                    if partial
                    else MetricStatus.AVAILABLE
                ),
                value=_mean(tuple(available_ic)),
                observations=len(available_ic),
                reason=(
                    "Some periods were omitted from IC aggregation."
                    if partial
                    else None
                ),
            )

        total_transaction_cost = sum(
            (item.transaction_cost for item in backtest.periods),
            Decimal("0"),
        )
        total_borrow_cost = sum(
            (item.borrow_cost for item in backtest.periods),
            Decimal("0"),
        )
        average_turnover = _mean(
            tuple(
                item.one_way_turnover_ratio
                for item in backtest.periods
            )
        )

        payload = {
            "backtest_id": backtest.backtest_id,
            "policy_id": policy.policy_id,
            "source_factor_run_ids": [
                item.factor_run.run_id for item in source_periods
            ],
            "period_count": period_count,
            "cumulative_gross_return": str(
                backtest.final_gross_wealth - Decimal("1")
            ),
            "cumulative_net_return": str(
                backtest.final_net_wealth - Decimal("1")
            ),
            "annualized_net_return": str(annualized_net_return),
            "volatility": _metric_payload(volatility),
            "sharpe": _metric_payload(sharpe),
            "maximum_drawdown": str(max_drawdown),
            "expected_shortfall": _metric_payload(expected_shortfall),
            "total_transaction_cost_rate": str(total_transaction_cost),
            "total_borrow_cost_rate": str(total_borrow_cost),
            "terminal_wealth_cost_drag": str(
                backtest.final_gross_wealth - backtest.final_net_wealth
            ),
            "average_one_way_turnover": str(average_turnover),
            "benchmarks": [
                {
                    "kind": item.kind.value,
                    "final_wealth": str(item.final_wealth),
                    "relative_wealth_return": str(
                        item.relative_wealth_return
                    ),
                    "tracking_error": _metric_payload(
                        item.tracking_error
                    ),
                    "information_ratio": _metric_payload(
                        item.information_ratio
                    ),
                }
                for item in benchmark_analytics
            ],
            "ic_periods": [
                {
                    "factor_run_id": item.factor_run_id,
                    "value": (
                        str(item.value) if item.value is not None else None
                    ),
                    "cross_section_size": item.cross_section_size,
                    "status": item.status.value,
                    "reason": item.reason,
                }
                for item in ic_periods
            ],
            "mean_information_coefficient": _metric_payload(mean_ic),
        }
        return PerformanceAnalysis(
            analysis_id=_content_id("performance-analysis", payload),
            backtest_id=backtest.backtest_id,
            policy_id=policy.policy_id,
            period_count=period_count,
            cumulative_gross_return=(
                backtest.final_gross_wealth - Decimal("1")
            ),
            cumulative_net_return=(
                backtest.final_net_wealth - Decimal("1")
            ),
            annualized_net_return=annualized_net_return,
            annualized_net_volatility=volatility,
            sharpe_ratio=sharpe,
            maximum_drawdown=max_drawdown,
            expected_shortfall_return=expected_shortfall,
            total_transaction_cost_rate=total_transaction_cost,
            total_borrow_cost_rate=total_borrow_cost,
            terminal_wealth_cost_drag=(
                backtest.final_gross_wealth - backtest.final_net_wealth
            ),
            average_one_way_turnover=average_turnover,
            benchmark_analytics=benchmark_analytics,
            ic_periods=ic_periods,
            mean_information_coefficient=mean_ic,
        )

    @staticmethod
    def _benchmark_analytics(
        *,
        backtest: BacktestResult,
        kind: BenchmarkKind,
        policy: PerformancePolicy,
    ) -> BenchmarkAnalytics:
        net_returns = tuple(item.net_return for item in backtest.periods)
        benchmark_returns = tuple(
            _benchmark_return(item, kind)
            for item in backtest.periods
        )
        active = tuple(
            portfolio - benchmark
            for portfolio, benchmark in zip(
                net_returns,
                benchmark_returns,
            )
        )
        tracking_error = _annualized_volatility(
            active,
            policy=policy,
        )
        information_ratio = _annualized_ratio(
            active,
            policy=policy,
        )
        final_wealth = dict(backtest.final_benchmark_wealth)[kind]
        relative = backtest.final_net_wealth / final_wealth - Decimal("1")
        return BenchmarkAnalytics(
            kind=kind,
            final_wealth=final_wealth,
            relative_wealth_return=relative,
            tracking_error=tracking_error,
            information_ratio=information_ratio,
        )

    @staticmethod
    def _information_coefficients(
        *,
        backtest: BacktestResult,
        source_periods: tuple[RebalancePeriod, ...],
        minimum_cross_section: int,
    ) -> tuple[ICPeriod, ...]:
        if not source_periods:
            return ()
        by_run = {
            item.factor_run.run_id: item for item in source_periods
        }
        if len(by_run) != len(source_periods):
            raise ValueError("duplicate source factor-run IDs")
        output: list[ICPeriod] = []
        for result_period in backtest.periods:
            source = by_run.get(result_period.factor_run_id)
            if source is None:
                raise ValueError(
                    "source periods do not cover every backtest factor run"
                )
            returns = {
                item.security_id: item.total_return
                for item in source.security_returns
            }
            score_pairs = tuple(
                (item.security_id, item.score)
                for item in source.factor_run.scores
                if item.security_id in returns
            )
            required = len(source.factor_run.scores)
            if len(score_pairs) != required:
                output.append(
                    ICPeriod(
                        factor_run_id=result_period.factor_run_id,
                        value=None,
                        cross_section_size=len(score_pairs),
                        status=MetricStatus.PARTIAL,
                        reason=(
                            "Not every scored security has a holding-period return."
                        ),
                    )
                )
                continue
            if len(score_pairs) < minimum_cross_section:
                output.append(
                    ICPeriod(
                        factor_run_id=result_period.factor_run_id,
                        value=None,
                        cross_section_size=len(score_pairs),
                        status=MetricStatus.INSUFFICIENT_OBSERVATIONS,
                        reason="Cross-section below IC minimum.",
                    )
                )
                continue
            scores = tuple(value for _, value in score_pairs)
            realized = tuple(
                returns[security_id]
                for security_id, _ in score_pairs
            )
            correlation = _spearman(scores, realized)
            if correlation is None:
                output.append(
                    ICPeriod(
                        factor_run_id=result_period.factor_run_id,
                        value=None,
                        cross_section_size=len(score_pairs),
                        status=MetricStatus.ZERO_VARIANCE,
                        reason="Scores or realized returns have zero rank variance.",
                    )
                )
            else:
                output.append(
                    ICPeriod(
                        factor_run_id=result_period.factor_run_id,
                        value=correlation,
                        cross_section_size=len(score_pairs),
                        status=MetricStatus.AVAILABLE,
                        reason=None,
                    )
                )
        return tuple(output)


def _benchmark_return(period, kind: BenchmarkKind) -> Decimal:
    matches = [
        value
        for benchmark_kind, value in period.benchmark_returns
        if benchmark_kind is kind
    ]
    if len(matches) != 1:
        raise ValueError(
            f"backtest period does not contain exactly one {kind.value} return"
        )
    return matches[0]


def _annualize_wealth(
    wealth: Decimal,
    *,
    periods: int,
    periods_per_year: int,
) -> Decimal:
    if wealth <= 0:
        raise ValueError("wealth must be positive for annualization")
    exponent = float(Decimal(periods_per_year) / Decimal(periods))
    return Decimal(str(math.pow(float(wealth), exponent) - 1.0))


def _annualized_volatility(
    values: tuple[Decimal, ...],
    *,
    policy: PerformancePolicy,
) -> RiskAdjustedMetric:
    if len(values) < policy.minimum_periods_for_risk_metrics:
        return RiskAdjustedMetric(
            MetricStatus.INSUFFICIENT_OBSERVATIONS,
            None,
            len(values),
            "Too few periods for volatility.",
        )
    std = _sample_std(values)
    return RiskAdjustedMetric(
        MetricStatus.AVAILABLE,
        std * Decimal(policy.periods_per_year).sqrt(),
        len(values),
        None,
    )


def _annualized_ratio(
    values: tuple[Decimal, ...],
    *,
    policy: PerformancePolicy,
) -> RiskAdjustedMetric:
    if len(values) < policy.minimum_periods_for_risk_metrics:
        return RiskAdjustedMetric(
            MetricStatus.INSUFFICIENT_OBSERVATIONS,
            None,
            len(values),
            "Too few periods for risk-adjusted ratio.",
        )
    std = _sample_std(values)
    if std == 0:
        return RiskAdjustedMetric(
            MetricStatus.ZERO_VARIANCE,
            None,
            len(values),
            "Return variance is zero.",
        )
    ratio = (
        _mean(values)
        / std
        * Decimal(policy.periods_per_year).sqrt()
    )
    return RiskAdjustedMetric(
        MetricStatus.AVAILABLE,
        ratio,
        len(values),
        None,
    )


def _expected_shortfall(
    values: tuple[Decimal, ...],
    *,
    policy: PerformancePolicy,
) -> RiskAdjustedMetric:
    if len(values) < policy.minimum_periods_for_risk_metrics:
        return RiskAdjustedMetric(
            MetricStatus.INSUFFICIENT_OBSERVATIONS,
            None,
            len(values),
            "Too few periods for expected shortfall.",
        )
    tail_probability = Decimal("1") - policy.cvar_confidence
    tail_count = max(
        1,
        math.ceil(float(Decimal(len(values)) * tail_probability)),
    )
    worst = tuple(sorted(values)[:tail_count])
    return RiskAdjustedMetric(
        MetricStatus.AVAILABLE,
        _mean(worst),
        len(values),
        None,
    )


def _maximum_drawdown(wealth_path: tuple[Decimal, ...]) -> Decimal:
    peak = Decimal("1")
    maximum = Decimal("0")
    for wealth in wealth_path:
        if wealth > peak:
            peak = wealth
        drawdown = wealth / peak - Decimal("1")
        if drawdown < maximum:
            maximum = drawdown
    return maximum


def _sample_std(values: tuple[Decimal, ...]) -> Decimal:
    if len(values) < 2:
        raise ValueError("sample standard deviation requires two values")
    mean = _mean(values)
    variance = sum(
        ((item - mean) ** 2 for item in values),
        Decimal("0"),
    ) / Decimal(len(values) - 1)
    return variance.sqrt()


def _mean(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        raise ValueError("mean requires values")
    return sum(values, Decimal("0")) / Decimal(len(values))


def _average_ranks(values: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
    indexed = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    ranks = [Decimal("0")] * len(values)
    cursor = 0
    while cursor < len(indexed):
        end = cursor
        value = indexed[cursor][1]
        while end + 1 < len(indexed) and indexed[end + 1][1] == value:
            end += 1
        rank = (
            Decimal(cursor + 1) + Decimal(end + 1)
        ) / Decimal("2")
        for position in range(cursor, end + 1):
            ranks[indexed[position][0]] = rank
        cursor = end + 1
    return tuple(ranks)


def _spearman(
    left: tuple[Decimal, ...],
    right: tuple[Decimal, ...],
) -> Decimal | None:
    if len(left) != len(right) or len(left) < 2:
        raise ValueError("Spearman inputs must have equal length >= 2")
    x = _average_ranks(left)
    y = _average_ranks(right)
    x_mean = _mean(x)
    y_mean = _mean(y)
    numerator = sum(
        (
            (x_value - x_mean) * (y_value - y_mean)
            for x_value, y_value in zip(x, y)
        ),
        Decimal("0"),
    )
    x_ss = sum(
        ((value - x_mean) ** 2 for value in x),
        Decimal("0"),
    )
    y_ss = sum(
        ((value - y_mean) ** 2 for value in y),
        Decimal("0"),
    )
    if x_ss == 0 or y_ss == 0:
        return None
    return numerator / (x_ss * y_ss).sqrt()


def _metric_payload(metric: RiskAdjustedMetric) -> dict[str, object]:
    return {
        "status": metric.status.value,
        "value": str(metric.value) if metric.value is not None else None,
        "observations": metric.observations,
        "reason": metric.reason,
    }


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
