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

from .portfolio_comparison import (
    PortfolioComparisonDossier,
    PortfolioMethod,
    portfolio_comparison_dossier_identity,
)
from .portfolio_paper_authorization import (
    PortfolioPaperAuthorization,
    portfolio_paper_authorization_identity,
)
from .portfolio_paper_monitoring import (
    PortfolioPaperAuthorizationState,
    PortfolioPaperEnforcementEvent,
    PortfolioPaperShadowObservation,
    portfolio_paper_enforcement_event_identity,
    portfolio_paper_shadow_observation_identity,
)


class PortfolioPaperReviewState(str, Enum):
    OPEN = "OPEN"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    WITHIN_POLICY = "WITHIN_POLICY"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class PortfolioPaperIterationDisposition(str, Enum):
    ITERATE_RESEARCH = "ITERATE_RESEARCH"
    CLOSE_RESEARCH_LINE = "CLOSE_RESEARCH_LINE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


@dataclass(frozen=True)
class PortfolioPaperBenchmarkObservation:
    shadow_observation_id: str
    benchmark_id: str
    period_start: datetime
    period_end: datetime
    total_return: Decimal
    source_fact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.shadow_observation_id.strip():
            raise ValueError("shadow_observation_id is required")
        if not self.benchmark_id.strip():
            raise ValueError("benchmark_id is required")
        if self.period_start.tzinfo is None or self.period_end.tzinfo is None:
            raise ValueError("benchmark timestamps must be timezone-aware")
        if self.period_end <= self.period_start:
            raise ValueError("benchmark period_end must follow period_start")
        if not self.total_return.is_finite() or self.total_return <= Decimal("-1"):
            raise ValueError(
                "benchmark total_return must be finite and greater than -1"
            )
        if not self.source_fact_ids or not all(
            item.strip() for item in self.source_fact_ids
        ):
            raise ValueError(
                "benchmark observation requires source fact IDs"
            )
        if len(self.source_fact_ids) != len(set(self.source_fact_ids)):
            raise ValueError(
                "benchmark source fact IDs must be unique"
            )

    @property
    def observation_id(self) -> str:
        return _content_id(
            "portfolio-paper-benchmark-observation",
            {
                "shadow_observation_id": self.shadow_observation_id,
                "benchmark_id": self.benchmark_id,
                "period_start": self.period_start.isoformat(),
                "period_end": self.period_end.isoformat(),
                "total_return": str(self.total_return),
                "source_fact_ids": sorted(self.source_fact_ids),
            },
        )


@dataclass(frozen=True)
class PortfolioPaperReviewPolicy:
    minimum_observations: int
    maximum_geometric_mean_return_calibration_error: Decimal
    maximum_mean_absolute_cost_assumption_error: Decimal
    maximum_average_solution_drift_turnover: Decimal
    minimum_benchmark_relative_wealth_return: Decimal
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.minimum_observations < 2:
            raise ValueError("PAPER review requires at least two observations")
        for name in (
            "maximum_geometric_mean_return_calibration_error",
            "maximum_mean_absolute_cost_assumption_error",
            "maximum_average_solution_drift_turnover",
        ):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(
                    f"{name} must be finite and non-negative"
                )
        if self.maximum_average_solution_drift_turnover > 1:
            raise ValueError(
                "maximum_average_solution_drift_turnover cannot exceed 1"
            )
        if not self.minimum_benchmark_relative_wealth_return.is_finite():
            raise ValueError(
                "minimum_benchmark_relative_wealth_return must be finite"
            )
        if not self.rationale.strip():
            raise ValueError("PAPER review policy rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError(
                "PAPER review policy requires evidence references"
            )

    @property
    def policy_id(self) -> str:
        return _content_id(
            "portfolio-paper-review-policy",
            {
                "minimum_observations": self.minimum_observations,
                "maximum_geometric_mean_return_calibration_error": str(
                    self.maximum_geometric_mean_return_calibration_error
                ),
                "maximum_mean_absolute_cost_assumption_error": str(
                    self.maximum_mean_absolute_cost_assumption_error
                ),
                "maximum_average_solution_drift_turnover": str(
                    self.maximum_average_solution_drift_turnover
                ),
                "minimum_benchmark_relative_wealth_return": str(
                    self.minimum_benchmark_relative_wealth_return
                ),
                "rationale": self.rationale,
                "evidence_references": list(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class PortfolioPaperReviewDossier:
    dossier_id: str
    authorization_id: str
    model_id: str
    manifest_id: str
    comparison_dossier_id: str
    selected_method: PortfolioMethod
    review_policy_id: str
    state: PortfolioPaperReviewState
    authorization_final_state: PortfolioPaperAuthorizationState
    observation_ids: tuple[str, ...]
    benchmark_observation_ids: tuple[str, ...]
    enforcement_event_ids: tuple[str, ...]
    observations: int
    shadow_cumulative_net_return: Decimal | None
    shadow_geometric_mean_return: Decimal | None
    shadow_realized_period_volatility: Decimal | None
    shadow_maximum_drawdown: Decimal | None
    shadow_average_one_way_turnover: Decimal | None
    shadow_average_solution_drift_turnover: Decimal | None
    shadow_average_implementation_cost_rate: Decimal | None
    mean_absolute_cost_assumption_error: Decimal | None
    benchmark_cumulative_return: Decimal | None
    benchmark_relative_wealth_return: Decimal | None
    oos_cumulative_net_return: Decimal
    oos_geometric_mean_return: Decimal
    oos_realized_period_volatility: Decimal
    oos_maximum_drawdown: Decimal
    oos_average_one_way_turnover: Decimal
    geometric_mean_return_calibration_error: Decimal | None
    realized_volatility_ratio_to_oos: Decimal | None
    average_turnover_delta_from_oos: Decimal | None
    kill_event_count: int
    kill_conditions: tuple[str, ...]
    reasons: tuple[str, ...]
    promotion_authority: str
    order_authority: str
    capital_authority: str
    caveat: str


class PortfolioPaperReviewEngine:
    CAVEAT = (
        "The PAPER review dossier measures one completed or interrupted "
        "shadow-only authorization against its frozen OOS reference. It does not "
        "promote a model, authorize live trading, or establish future performance."
    )

    def evaluate(
        self,
        *,
        authorization: PortfolioPaperAuthorization,
        comparison: PortfolioComparisonDossier,
        observations: tuple[PortfolioPaperShadowObservation, ...],
        benchmarks: tuple[PortfolioPaperBenchmarkObservation, ...],
        events: tuple[PortfolioPaperEnforcementEvent, ...],
        policy: PortfolioPaperReviewPolicy,
    ) -> PortfolioPaperReviewDossier:
        self._validate_inputs(
            authorization=authorization,
            comparison=comparison,
            observations=observations,
            benchmarks=benchmarks,
            events=events,
        )

        selected = next(
            item
            for item in comparison.evaluations
            if item.method is authorization.selected_method
        )
        final_state = (
            events[-1].new_state
            if events
            else PortfolioPaperAuthorizationState.ACTIVE
        )
        observation_count = len(observations)
        kill_events = tuple(
            item for item in events if item.kill_conditions
        )
        kill_conditions = tuple(
            sorted(
                {
                    condition.value
                    for event in kill_events
                    for condition in event.kill_conditions
                }
            )
        )

        oos_periods = len(comparison.fold_ids)
        if oos_periods < 1:
            raise ValueError("selected OOS evaluation contains no folds")
        oos_gmean = _geometric_mean_from_cumulative(
            selected.cumulative_net_return,
            oos_periods,
        )

        shadow_cumulative = None
        shadow_gmean = None
        shadow_vol = None
        shadow_drawdown = None
        shadow_turnover = None
        shadow_drift = None
        shadow_cost = None
        cost_error = None
        benchmark_cumulative = None
        benchmark_relative = None
        calibration_error = None
        vol_ratio = None
        turnover_delta = None

        if observations:
            returns = tuple(item.net_return for item in observations)
            costs = tuple(
                item.implementation_cost_rate for item in observations
            )
            drifts = tuple(
                item.solution_drift_turnover for item in observations
            )
            turnovers = tuple(
                item.one_way_turnover for item in observations
            )
            shadow_cumulative, shadow_drawdown = _wealth_metrics(returns)
            shadow_gmean = _geometric_mean_from_cumulative(
                shadow_cumulative,
                observation_count,
            )
            shadow_vol = (
                _sample_std(returns)
                if observation_count >= 2
                else Decimal("0")
            )
            shadow_turnover = _mean(turnovers)
            shadow_drift = _mean(drifts)
            shadow_cost = _mean(costs)
            cost_error = _mean(
                tuple(
                    abs(item.implementation_cost_assumption_variance)
                    for item in observations
                )
            )
            benchmark_returns = tuple(
                item.total_return for item in benchmarks
            )
            benchmark_cumulative, _ = _wealth_metrics(
                benchmark_returns
            )
            shadow_wealth = Decimal("1") + shadow_cumulative
            benchmark_wealth = Decimal("1") + benchmark_cumulative
            benchmark_relative = (
                shadow_wealth / benchmark_wealth - Decimal("1")
            )
            calibration_error = abs(shadow_gmean - oos_gmean)
            if selected.realized_period_volatility > 0:
                vol_ratio = (
                    shadow_vol / selected.realized_period_volatility
                )
            turnover_delta = (
                shadow_turnover - selected.average_one_way_turnover
            )

        reasons: list[str] = []
        if final_state is PortfolioPaperAuthorizationState.ACTIVE:
            state = PortfolioPaperReviewState.OPEN
            reasons.append(
                "shadow authorization is still active and has not reached a "
                "terminal state"
            )
        elif observation_count < policy.minimum_observations:
            state = PortfolioPaperReviewState.INSUFFICIENT_EVIDENCE
            reasons.append(
                "minimum prospective observation count not reached"
            )
        else:
            assert calibration_error is not None
            assert cost_error is not None
            assert shadow_drift is not None
            assert benchmark_relative is not None
            if kill_events:
                reasons.append(
                    "one or more mandatory PAPER kill conditions were triggered"
                )
            if (
                calibration_error
                > policy.maximum_geometric_mean_return_calibration_error
            ):
                reasons.append(
                    "shadow return calibration error exceeded review policy"
                )
            if (
                cost_error
                > policy.maximum_mean_absolute_cost_assumption_error
            ):
                reasons.append(
                    "implementation-cost model error exceeded review policy"
                )
            if (
                shadow_drift
                > policy.maximum_average_solution_drift_turnover
            ):
                reasons.append(
                    "average solution drift exceeded review policy"
                )
            if (
                benchmark_relative
                < policy.minimum_benchmark_relative_wealth_return
            ):
                reasons.append(
                    "benchmark-relative shadow wealth fell below review policy"
                )
            state = (
                PortfolioPaperReviewState.REVIEW_REQUIRED
                if reasons
                else PortfolioPaperReviewState.WITHIN_POLICY
            )
            if not reasons:
                reasons.append(
                    "completed shadow evidence remains within frozen review policy"
                )

        payload = {
            "authorization_id": authorization.authorization_id,
            "model_id": authorization.model_id,
            "manifest_id": authorization.manifest_id,
            "comparison_dossier_id": comparison.dossier_id,
            "selected_method": authorization.selected_method.value,
            "review_policy_id": policy.policy_id,
            "state": state.value,
            "authorization_final_state": final_state.value,
            "observation_ids": [
                item.observation_id for item in observations
            ],
            "benchmark_observation_ids": [
                item.observation_id for item in benchmarks
            ],
            "enforcement_event_ids": [
                item.event_id for item in events
            ],
            "observations": observation_count,
            "shadow_cumulative_net_return": _optional_str(shadow_cumulative),
            "shadow_geometric_mean_return": _optional_str(shadow_gmean),
            "shadow_realized_period_volatility": _optional_str(shadow_vol),
            "shadow_maximum_drawdown": _optional_str(shadow_drawdown),
            "shadow_average_one_way_turnover": _optional_str(
                shadow_turnover
            ),
            "shadow_average_solution_drift_turnover": _optional_str(
                shadow_drift
            ),
            "shadow_average_implementation_cost_rate": _optional_str(
                shadow_cost
            ),
            "mean_absolute_cost_assumption_error": _optional_str(
                cost_error
            ),
            "benchmark_cumulative_return": _optional_str(
                benchmark_cumulative
            ),
            "benchmark_relative_wealth_return": _optional_str(
                benchmark_relative
            ),
            "oos_cumulative_net_return": str(
                selected.cumulative_net_return
            ),
            "oos_geometric_mean_return": str(oos_gmean),
            "oos_realized_period_volatility": str(
                selected.realized_period_volatility
            ),
            "oos_maximum_drawdown": str(selected.maximum_drawdown),
            "oos_average_one_way_turnover": str(
                selected.average_one_way_turnover
            ),
            "geometric_mean_return_calibration_error": _optional_str(
                calibration_error
            ),
            "realized_volatility_ratio_to_oos": _optional_str(vol_ratio),
            "average_turnover_delta_from_oos": _optional_str(
                turnover_delta
            ),
            "kill_event_count": len(kill_events),
            "kill_conditions": list(kill_conditions),
            "reasons": list(reasons),
            "promotion_authority": "NONE",
            "order_authority": "NONE",
            "capital_authority": "NONE",
        }
        return PortfolioPaperReviewDossier(
            dossier_id=_content_id(
                "portfolio-paper-review-dossier",
                payload,
            ),
            authorization_id=authorization.authorization_id,
            model_id=authorization.model_id,
            manifest_id=authorization.manifest_id,
            comparison_dossier_id=comparison.dossier_id,
            selected_method=authorization.selected_method,
            review_policy_id=policy.policy_id,
            state=state,
            authorization_final_state=final_state,
            observation_ids=tuple(
                item.observation_id for item in observations
            ),
            benchmark_observation_ids=tuple(
                item.observation_id for item in benchmarks
            ),
            enforcement_event_ids=tuple(
                item.event_id for item in events
            ),
            observations=observation_count,
            shadow_cumulative_net_return=shadow_cumulative,
            shadow_geometric_mean_return=shadow_gmean,
            shadow_realized_period_volatility=shadow_vol,
            shadow_maximum_drawdown=shadow_drawdown,
            shadow_average_one_way_turnover=shadow_turnover,
            shadow_average_solution_drift_turnover=shadow_drift,
            shadow_average_implementation_cost_rate=shadow_cost,
            mean_absolute_cost_assumption_error=cost_error,
            benchmark_cumulative_return=benchmark_cumulative,
            benchmark_relative_wealth_return=benchmark_relative,
            oos_cumulative_net_return=selected.cumulative_net_return,
            oos_geometric_mean_return=oos_gmean,
            oos_realized_period_volatility=(
                selected.realized_period_volatility
            ),
            oos_maximum_drawdown=selected.maximum_drawdown,
            oos_average_one_way_turnover=(
                selected.average_one_way_turnover
            ),
            geometric_mean_return_calibration_error=calibration_error,
            realized_volatility_ratio_to_oos=vol_ratio,
            average_turnover_delta_from_oos=turnover_delta,
            kill_event_count=len(kill_events),
            kill_conditions=kill_conditions,
            reasons=tuple(reasons),
            promotion_authority="NONE",
            order_authority="NONE",
            capital_authority="NONE",
            caveat=self.CAVEAT,
        )

    @staticmethod
    def _validate_inputs(
        *,
        authorization: PortfolioPaperAuthorization,
        comparison: PortfolioComparisonDossier,
        observations: tuple[PortfolioPaperShadowObservation, ...],
        benchmarks: tuple[PortfolioPaperBenchmarkObservation, ...],
        events: tuple[PortfolioPaperEnforcementEvent, ...],
    ) -> None:
        if (
            authorization.authorization_id
            != portfolio_paper_authorization_identity(authorization)
        ):
            raise ValueError(
                "portfolio PAPER authorization identity mismatch"
            )
        if comparison.dossier_id != portfolio_comparison_dossier_identity(
            comparison
        ):
            raise ValueError("portfolio comparison dossier identity mismatch")
        if authorization.comparison_dossier_id != comparison.dossier_id:
            raise ValueError(
                "authorization belongs to another OOS comparison"
            )
        if (
            comparison.model_id != authorization.model_id
            or comparison.manifest_id != authorization.manifest_id
        ):
            raise ValueError(
                "comparison and authorization Research Run differ"
            )
        if not any(
            item.method is authorization.selected_method
            for item in comparison.evaluations
        ):
            raise ValueError(
                "authorized selected method is absent from OOS comparison"
            )
        for evaluation in comparison.evaluations:
            if tuple(
                item.fold_id for item in evaluation.fold_outcomes
            ) != comparison.fold_ids:
                raise ValueError(
                    "OOS evaluation fold outcomes differ from dossier fold IDs"
                )
            if tuple(
                item.solution_id for item in evaluation.fold_outcomes
            ) != evaluation.solution_ids:
                raise ValueError(
                    "OOS evaluation solution IDs differ from fold outcomes"
                )
            if any(
                item.method is not evaluation.method
                for item in evaluation.fold_outcomes
            ):
                raise ValueError(
                    "OOS fold outcome method differs from evaluation method"
                )

        ordered_observations = tuple(
            sorted(observations, key=lambda item: item.period_start)
        )
        if ordered_observations != observations:
            raise ValueError(
                "shadow observations must be chronological"
            )
        for index, item in enumerate(observations):
            if (
                item.observation_id
                != portfolio_paper_shadow_observation_identity(item)
            ):
                raise ValueError(
                    "shadow observation identity mismatch"
                )
            if (
                item.authorization_id != authorization.authorization_id
                or item.model_id != authorization.model_id
                or item.manifest_id != authorization.manifest_id
                or item.selected_solution_id
                != authorization.selected_solution_id
            ):
                raise ValueError(
                    "shadow observation belongs to another authorization"
                )
            if index and item.period_start < observations[index - 1].period_end:
                raise ValueError(
                    "shadow observation periods cannot overlap"
                )

        if len(benchmarks) != len(observations):
            raise ValueError(
                "every shadow observation requires one benchmark observation"
            )
        benchmark_by_shadow = {
            item.shadow_observation_id: item
            for item in benchmarks
        }
        if len(benchmark_by_shadow) != len(benchmarks):
            raise ValueError(
                "duplicate benchmark observation for shadow observation"
            )
        for observation in observations:
            benchmark = benchmark_by_shadow.get(
                observation.observation_id
            )
            if benchmark is None:
                raise ValueError(
                    "missing benchmark for shadow observation"
                )
            if benchmark.benchmark_id != comparison.benchmark_id:
                raise ValueError(
                    "shadow benchmark differs from OOS benchmark"
                )
            if (
                benchmark.period_start != observation.period_start
                or benchmark.period_end != observation.period_end
            ):
                raise ValueError(
                    "benchmark interval differs from shadow observation"
                )

        ordered_events = tuple(
            sorted(events, key=lambda item: item.ordinal)
        )
        if ordered_events != events:
            raise ValueError("enforcement events must be ordinal order")
        for index, event in enumerate(events, start=1):
            if (
                event.event_id
                != portfolio_paper_enforcement_event_identity(event)
            ):
                raise ValueError("enforcement event identity mismatch")
            if event.authorization_id != authorization.authorization_id:
                raise ValueError(
                    "enforcement event belongs to another authorization"
                )
            if event.ordinal != index:
                raise ValueError(
                    "enforcement event ordinals must be contiguous"
                )
            if index > 1 and (
                event.prior_state != events[index - 2].new_state
            ):
                raise ValueError(
                    "enforcement event state chain is broken"
                )


def portfolio_paper_review_dossier_identity(
    dossier: PortfolioPaperReviewDossier,
) -> str:
    payload = {
        "authorization_id": dossier.authorization_id,
        "model_id": dossier.model_id,
        "manifest_id": dossier.manifest_id,
        "comparison_dossier_id": dossier.comparison_dossier_id,
        "selected_method": dossier.selected_method.value,
        "review_policy_id": dossier.review_policy_id,
        "state": dossier.state.value,
        "authorization_final_state": dossier.authorization_final_state.value,
        "observation_ids": list(dossier.observation_ids),
        "benchmark_observation_ids": list(
            dossier.benchmark_observation_ids
        ),
        "enforcement_event_ids": list(dossier.enforcement_event_ids),
        "observations": dossier.observations,
        "shadow_cumulative_net_return": _optional_str(
            dossier.shadow_cumulative_net_return
        ),
        "shadow_geometric_mean_return": _optional_str(
            dossier.shadow_geometric_mean_return
        ),
        "shadow_realized_period_volatility": _optional_str(
            dossier.shadow_realized_period_volatility
        ),
        "shadow_maximum_drawdown": _optional_str(
            dossier.shadow_maximum_drawdown
        ),
        "shadow_average_one_way_turnover": _optional_str(
            dossier.shadow_average_one_way_turnover
        ),
        "shadow_average_solution_drift_turnover": _optional_str(
            dossier.shadow_average_solution_drift_turnover
        ),
        "shadow_average_implementation_cost_rate": _optional_str(
            dossier.shadow_average_implementation_cost_rate
        ),
        "mean_absolute_cost_assumption_error": _optional_str(
            dossier.mean_absolute_cost_assumption_error
        ),
        "benchmark_cumulative_return": _optional_str(
            dossier.benchmark_cumulative_return
        ),
        "benchmark_relative_wealth_return": _optional_str(
            dossier.benchmark_relative_wealth_return
        ),
        "oos_cumulative_net_return": str(
            dossier.oos_cumulative_net_return
        ),
        "oos_geometric_mean_return": str(
            dossier.oos_geometric_mean_return
        ),
        "oos_realized_period_volatility": str(
            dossier.oos_realized_period_volatility
        ),
        "oos_maximum_drawdown": str(dossier.oos_maximum_drawdown),
        "oos_average_one_way_turnover": str(
            dossier.oos_average_one_way_turnover
        ),
        "geometric_mean_return_calibration_error": _optional_str(
            dossier.geometric_mean_return_calibration_error
        ),
        "realized_volatility_ratio_to_oos": _optional_str(
            dossier.realized_volatility_ratio_to_oos
        ),
        "average_turnover_delta_from_oos": _optional_str(
            dossier.average_turnover_delta_from_oos
        ),
        "kill_event_count": dossier.kill_event_count,
        "kill_conditions": list(dossier.kill_conditions),
        "reasons": list(dossier.reasons),
        "promotion_authority": dossier.promotion_authority,
        "order_authority": dossier.order_authority,
        "capital_authority": dossier.capital_authority,
    }
    return _content_id("portfolio-paper-review-dossier", payload)


@dataclass(frozen=True)
class PortfolioPaperPostmortem:
    postmortem_id: str
    review_dossier_id: str
    authorization_id: str
    model_id: str
    manifest_id: str
    closed_at: datetime
    reviewer: str
    independent_challenger: str
    disposition: PortfolioPaperIterationDisposition
    lessons: tuple[str, ...]
    limitations: tuple[str, ...]
    rationale: str
    evidence_references: tuple[str, ...]
    next_stage_recommendation: str | None
    research_iteration_authority: str
    promotion_authority: str
    order_authority: str
    capital_authority: str
    caveat: str


class PortfolioPaperPostmortemEngine:
    CAVEAT = (
        "The postmortem can recommend another research iteration or close the "
        "research line. It cannot promote a model to APPROVED or LIVE and cannot "
        "authorize orders or capital."
    )

    def close(
        self,
        *,
        review: PortfolioPaperReviewDossier,
        closed_at: datetime,
        reviewer: str,
        independent_challenger: str,
        disposition: PortfolioPaperIterationDisposition,
        lessons: tuple[str, ...],
        limitations: tuple[str, ...],
        rationale: str,
        evidence_references: tuple[str, ...],
    ) -> PortfolioPaperPostmortem:
        if (
            review.dossier_id
            != portfolio_paper_review_dossier_identity(review)
        ):
            raise ValueError("PAPER review dossier identity mismatch")
        if review.state is PortfolioPaperReviewState.OPEN:
            raise ValueError(
                "active shadow authorization cannot receive final postmortem"
            )
        if closed_at.tzinfo is None:
            raise ValueError("postmortem closed_at must be timezone-aware")
        if not reviewer.strip() or not independent_challenger.strip():
            raise ValueError(
                "postmortem reviewer and challenger are required"
            )
        if reviewer.strip() == independent_challenger.strip():
            raise ValueError(
                "postmortem reviewer and challenger must differ"
            )
        if not lessons or not all(item.strip() for item in lessons):
            raise ValueError("postmortem requires explicit lessons")
        if not limitations or not all(item.strip() for item in limitations):
            raise ValueError("postmortem requires explicit limitations")
        if not rationale.strip():
            raise ValueError("postmortem rationale is required")
        if not evidence_references or not all(
            item.strip() for item in evidence_references
        ):
            raise ValueError("postmortem requires evidence references")

        if review.state is PortfolioPaperReviewState.INSUFFICIENT_EVIDENCE:
            if (
                disposition
                is not PortfolioPaperIterationDisposition.INSUFFICIENT_EVIDENCE
            ):
                raise ValueError(
                    "insufficient PAPER evidence cannot support a final iterate/close decision"
                )
        elif (
            disposition
            is PortfolioPaperIterationDisposition.INSUFFICIENT_EVIDENCE
        ):
            raise ValueError(
                "sufficient PAPER evidence cannot be labeled insufficient"
            )

        next_stage = (
            "RESEARCH"
            if disposition
            is PortfolioPaperIterationDisposition.ITERATE_RESEARCH
            else None
        )
        payload = {
            "review_dossier_id": review.dossier_id,
            "authorization_id": review.authorization_id,
            "model_id": review.model_id,
            "manifest_id": review.manifest_id,
            "closed_at": closed_at.isoformat(),
            "reviewer": reviewer.strip(),
            "independent_challenger": independent_challenger.strip(),
            "disposition": disposition.value,
            "lessons": list(lessons),
            "limitations": list(limitations),
            "rationale": rationale.strip(),
            "evidence_references": list(evidence_references),
            "next_stage_recommendation": next_stage,
            "research_iteration_authority": "RECOMMENDATION_ONLY",
            "promotion_authority": "NONE",
            "order_authority": "NONE",
            "capital_authority": "NONE",
        }
        return PortfolioPaperPostmortem(
            postmortem_id=_content_id(
                "portfolio-paper-postmortem",
                payload,
            ),
            review_dossier_id=review.dossier_id,
            authorization_id=review.authorization_id,
            model_id=review.model_id,
            manifest_id=review.manifest_id,
            closed_at=closed_at,
            reviewer=reviewer.strip(),
            independent_challenger=independent_challenger.strip(),
            disposition=disposition,
            lessons=lessons,
            limitations=limitations,
            rationale=rationale.strip(),
            evidence_references=evidence_references,
            next_stage_recommendation=next_stage,
            research_iteration_authority="RECOMMENDATION_ONLY",
            promotion_authority="NONE",
            order_authority="NONE",
            capital_authority="NONE",
            caveat=self.CAVEAT,
        )


def portfolio_paper_postmortem_identity(
    postmortem: PortfolioPaperPostmortem,
) -> str:
    payload = {
        "review_dossier_id": postmortem.review_dossier_id,
        "authorization_id": postmortem.authorization_id,
        "model_id": postmortem.model_id,
        "manifest_id": postmortem.manifest_id,
        "closed_at": postmortem.closed_at.isoformat(),
        "reviewer": postmortem.reviewer,
        "independent_challenger": postmortem.independent_challenger,
        "disposition": postmortem.disposition.value,
        "lessons": list(postmortem.lessons),
        "limitations": list(postmortem.limitations),
        "rationale": postmortem.rationale,
        "evidence_references": list(postmortem.evidence_references),
        "next_stage_recommendation": postmortem.next_stage_recommendation,
        "research_iteration_authority": postmortem.research_iteration_authority,
        "promotion_authority": postmortem.promotion_authority,
        "order_authority": postmortem.order_authority,
        "capital_authority": postmortem.capital_authority,
    }
    return _content_id("portfolio-paper-postmortem", payload)


class PortfolioPaperReviewStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_paper_review_dossiers (
                dossier_id VARCHAR PRIMARY KEY,
                authorization_id VARCHAR NOT NULL,
                model_id VARCHAR NOT NULL,
                manifest_id VARCHAR NOT NULL,
                state VARCHAR NOT NULL,
                selected_method VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_paper_postmortems (
                postmortem_id VARCHAR PRIMARY KEY,
                review_dossier_id VARCHAR NOT NULL UNIQUE,
                authorization_id VARCHAR NOT NULL,
                model_id VARCHAR NOT NULL,
                manifest_id VARCHAR NOT NULL,
                disposition VARCHAR NOT NULL,
                closed_at TIMESTAMPTZ NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add_review(self, dossier: PortfolioPaperReviewDossier) -> bool:
        if dossier.dossier_id != portfolio_paper_review_dossier_identity(
            dossier
        ):
            raise ValueError("PAPER review dossier identity mismatch")
        payload = json.dumps(
            _review_json(dossier),
            sort_keys=True,
            separators=(",", ":"),
        )
        existing = self._con.execute(
            """
            SELECT payload_json
            FROM portfolio_paper_review_dossiers
            WHERE dossier_id = ?
            """,
            [dossier.dossier_id],
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != payload:
                raise ValueError("PAPER review dossier identity conflict")
            return False
        self._con.execute(
            """
            INSERT INTO portfolio_paper_review_dossiers
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                dossier.dossier_id,
                dossier.authorization_id,
                dossier.model_id,
                dossier.manifest_id,
                dossier.state.value,
                dossier.selected_method.value,
                payload,
            ],
        )
        return True

    def add_postmortem(self, postmortem: PortfolioPaperPostmortem) -> bool:
        if (
            postmortem.postmortem_id
            != portfolio_paper_postmortem_identity(postmortem)
        ):
            raise ValueError("PAPER postmortem identity mismatch")
        payload = json.dumps(
            {
                "postmortem_id": postmortem.postmortem_id,
                "review_dossier_id": postmortem.review_dossier_id,
                "authorization_id": postmortem.authorization_id,
                "model_id": postmortem.model_id,
                "manifest_id": postmortem.manifest_id,
                "closed_at": postmortem.closed_at.isoformat(),
                "reviewer": postmortem.reviewer,
                "independent_challenger": postmortem.independent_challenger,
                "disposition": postmortem.disposition.value,
                "lessons": list(postmortem.lessons),
                "limitations": list(postmortem.limitations),
                "rationale": postmortem.rationale,
                "evidence_references": list(
                    postmortem.evidence_references
                ),
                "next_stage_recommendation": (
                    postmortem.next_stage_recommendation
                ),
                "research_iteration_authority": (
                    postmortem.research_iteration_authority
                ),
                "promotion_authority": postmortem.promotion_authority,
                "order_authority": postmortem.order_authority,
                "capital_authority": postmortem.capital_authority,
                "caveat": postmortem.caveat,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        existing = self._con.execute(
            """
            SELECT payload_json
            FROM portfolio_paper_postmortems
            WHERE postmortem_id = ?
            """,
            [postmortem.postmortem_id],
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != payload:
                raise ValueError("PAPER postmortem identity conflict")
            return False
        if self._con.execute(
            """
            SELECT 1
            FROM portfolio_paper_postmortems
            WHERE review_dossier_id = ?
            """,
            [postmortem.review_dossier_id],
        ).fetchone() is not None:
            raise ValueError(
                "PAPER review dossier already has a postmortem"
            )
        self._con.execute(
            """
            INSERT INTO portfolio_paper_postmortems
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                postmortem.postmortem_id,
                postmortem.review_dossier_id,
                postmortem.authorization_id,
                postmortem.model_id,
                postmortem.manifest_id,
                postmortem.disposition.value,
                postmortem.closed_at,
                payload,
            ],
        )
        return True

    def close(self) -> None:
        self._con.close()


def _geometric_mean_from_cumulative(
    cumulative_return: Decimal,
    periods: int,
) -> Decimal:
    if periods < 1:
        raise ValueError("geometric mean requires at least one period")
    wealth = Decimal("1") + cumulative_return
    if wealth <= 0:
        raise ValueError("geometric mean requires positive wealth")
    value = math.pow(float(wealth), 1.0 / periods) - 1.0
    return Decimal(str(value))


def _wealth_metrics(
    returns: tuple[Decimal, ...],
) -> tuple[Decimal, Decimal]:
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
    return wealth - Decimal("1"), maximum_drawdown


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


def _optional_str(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _review_json(
    dossier: PortfolioPaperReviewDossier,
) -> dict[str, object]:
    return {
        "dossier_id": dossier.dossier_id,
        "authorization_id": dossier.authorization_id,
        "model_id": dossier.model_id,
        "manifest_id": dossier.manifest_id,
        "comparison_dossier_id": dossier.comparison_dossier_id,
        "selected_method": dossier.selected_method.value,
        "review_policy_id": dossier.review_policy_id,
        "state": dossier.state.value,
        "authorization_final_state": (
            dossier.authorization_final_state.value
        ),
        "observation_ids": list(dossier.observation_ids),
        "benchmark_observation_ids": list(
            dossier.benchmark_observation_ids
        ),
        "enforcement_event_ids": list(
            dossier.enforcement_event_ids
        ),
        "observations": dossier.observations,
        "shadow_cumulative_net_return": _optional_str(
            dossier.shadow_cumulative_net_return
        ),
        "shadow_geometric_mean_return": _optional_str(
            dossier.shadow_geometric_mean_return
        ),
        "shadow_realized_period_volatility": _optional_str(
            dossier.shadow_realized_period_volatility
        ),
        "shadow_maximum_drawdown": _optional_str(
            dossier.shadow_maximum_drawdown
        ),
        "shadow_average_one_way_turnover": _optional_str(
            dossier.shadow_average_one_way_turnover
        ),
        "shadow_average_solution_drift_turnover": _optional_str(
            dossier.shadow_average_solution_drift_turnover
        ),
        "shadow_average_implementation_cost_rate": _optional_str(
            dossier.shadow_average_implementation_cost_rate
        ),
        "mean_absolute_cost_assumption_error": _optional_str(
            dossier.mean_absolute_cost_assumption_error
        ),
        "benchmark_cumulative_return": _optional_str(
            dossier.benchmark_cumulative_return
        ),
        "benchmark_relative_wealth_return": _optional_str(
            dossier.benchmark_relative_wealth_return
        ),
        "oos_cumulative_net_return": str(
            dossier.oos_cumulative_net_return
        ),
        "oos_geometric_mean_return": str(
            dossier.oos_geometric_mean_return
        ),
        "oos_realized_period_volatility": str(
            dossier.oos_realized_period_volatility
        ),
        "oos_maximum_drawdown": str(
            dossier.oos_maximum_drawdown
        ),
        "oos_average_one_way_turnover": str(
            dossier.oos_average_one_way_turnover
        ),
        "geometric_mean_return_calibration_error": _optional_str(
            dossier.geometric_mean_return_calibration_error
        ),
        "realized_volatility_ratio_to_oos": _optional_str(
            dossier.realized_volatility_ratio_to_oos
        ),
        "average_turnover_delta_from_oos": _optional_str(
            dossier.average_turnover_delta_from_oos
        ),
        "kill_event_count": dossier.kill_event_count,
        "kill_conditions": list(dossier.kill_conditions),
        "reasons": list(dossier.reasons),
        "promotion_authority": dossier.promotion_authority,
        "order_authority": dossier.order_authority,
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