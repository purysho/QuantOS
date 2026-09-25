import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from quantos.portfolio_comparison import (
    PortfolioComparisonDossier,
    PortfolioFoldOutcome,
    PortfolioMethod,
    PortfolioMethodEvaluation,
    portfolio_comparison_dossier_identity,
)
from quantos.portfolio_construction import PortfolioWeight
from quantos.portfolio_paper_authorization import (
    PortfolioPaperAuthorization,
    PortfolioPaperKillCondition,
    portfolio_paper_authorization_identity,
)
from quantos.portfolio_paper_monitoring import (
    PortfolioPaperAuthorizationState,
    PortfolioPaperEnforcementEvent,
    PortfolioPaperShadowObservation,
    portfolio_paper_enforcement_event_identity,
    portfolio_paper_shadow_observation_identity,
)
from quantos.portfolio_paper_review import (
    PortfolioPaperBenchmarkObservation,
    PortfolioPaperIterationDisposition,
    PortfolioPaperPostmortemEngine,
    PortfolioPaperReviewEngine,
    PortfolioPaperReviewPolicy,
    PortfolioPaperReviewState,
    PortfolioPaperReviewStore,
)

UTC = timezone.utc
AT = datetime(2026, 9, 25, 12, tzinfo=UTC)
MODEL_ID = "research-model:" + "m" * 64
MANIFEST_ID = "research-run-manifest:" + "r" * 64
AUTH_ID_PREFIX = "portfolio-paper-authorization:"
COMPARISON_POLICY_ID = "portfolio-comparison-policy:" + "p" * 64
CONSTRAINT_ID = "portfolio-constraint-policy:" + "c" * 64
SOLUTION_ID = "portfolio-solution:" + "s" * 64


def method_evaluation(method):
    outcomes = tuple(
        PortfolioFoldOutcome(
            fold_id=f"fold:{index}",
            method=method,
            solution_id=f"solution:{method.value}:{index}",
            gross_return=Decimal("0.0103"),
            implementation_cost_rate=Decimal("0.0003"),
            net_return=Decimal("0.01"),
            one_way_turnover=Decimal("0.25"),
            effective_number_of_assets=Decimal("2"),
            maximum_absolute_weight=Decimal("0.5"),
        )
        for index in range(3)
    )
    return PortfolioMethodEvaluation(
        method=method,
        solution_ids=tuple(item.solution_id for item in outcomes),
        fold_outcomes=outcomes,
        cumulative_net_return=Decimal("0.030301"),
        realized_period_volatility=Decimal("0"),
        maximum_drawdown=Decimal("0"),
        expected_shortfall_return=Decimal("0.01"),
        total_implementation_cost_rate=Decimal("0.0009"),
        average_one_way_turnover=Decimal("0.25"),
        average_effective_number_of_assets=Decimal("2"),
        maximum_absolute_weight=Decimal("0.5"),
        market_relative_wealth_return=Decimal("0.015"),
    )


def comparison():
    draft = PortfolioComparisonDossier(
        dossier_id="placeholder",
        model_id=MODEL_ID,
        manifest_id=MANIFEST_ID,
        constraint_policy_id=CONSTRAINT_ID,
        comparison_policy_id=COMPARISON_POLICY_ID,
        fold_ids=("fold:0", "fold:1", "fold:2"),
        benchmark_id="benchmark:market-cap",
        evaluations=tuple(
            method_evaluation(method)
            for method in (
                PortfolioMethod.EQUAL_WEIGHT,
                PortfolioMethod.INVERSE_VOLATILITY,
                PortfolioMethod.MINIMUM_VARIANCE,
            )
        ),
        selection_authority="NONE",
        capital_authority="NONE",
        caveat="Common OOS comparison.",
    )
    return replace(
        draft,
        dossier_id=portfolio_comparison_dossier_identity(draft),
    )


def authorization():
    comp = comparison()
    draft = PortfolioPaperAuthorization(
        authorization_id="placeholder",
        model_id=MODEL_ID,
        manifest_id=MANIFEST_ID,
        research_case_id="research-case:" + "q" * 64,
        decision_id="portfolio-research-decision:" + "d" * 64,
        comparison_dossier_id=comp.dossier_id,
        robustness_dossier_id="portfolio-robustness-dossier:" + "b" * 64,
        permit_id="shadow-permit:" + "h" * 64,
        selected_method=PortfolioMethod.EQUAL_WEIGHT,
        selected_solution_id=SOLUTION_ID,
        selected_dataset_id="portfolio-dataset:" + "x" * 64,
        constraint_policy_id=CONSTRAINT_ID,
        execution_assumptions_id=(
            "portfolio-paper-execution-assumptions:" + "e" * 64
        ),
        monitoring_policy_id=(
            "portfolio-paper-monitoring-policy:" + "n" * 64
        ),
        authorized_at=AT,
        expires_at=AT + timedelta(days=30),
        portfolio_reviewer="paper-reviewer",
        independent_risk_reviewer="risk-reviewer",
        rationale="Shadow review authorization.",
        evidence_references=("authorization:evidence",),
        lifecycle_stage="PAPER",
        paper_authority="SHADOW_ONLY",
        order_authority="NONE",
        capital_authority="NONE",
        purpose="PROSPECTIVE_PORTFOLIO_SHADOW_ONLY",
        caveat="Shadow only.",
    )
    return replace(
        draft,
        authorization_id=portfolio_paper_authorization_identity(draft),
    )


def shadow_observation(index, net=Decimal("0.01")):
    start = AT + timedelta(days=1 + index * 2)
    cumulative = (
        (Decimal("1.01") ** Decimal(index + 1))
        - Decimal("1")
    )
    draft = PortfolioPaperShadowObservation(
        observation_id="placeholder",
        authorization_id=authorization().authorization_id,
        model_id=MODEL_ID,
        manifest_id=MANIFEST_ID,
        selected_solution_id=SOLUTION_ID,
        period_start=start,
        period_end=start + timedelta(days=1),
        observed_at=start + timedelta(days=1, minutes=1),
        gross_return=net + Decimal("0.0003"),
        net_return=net,
        implementation_cost_rate=Decimal("0.0003"),
        expected_implementation_cost_rate=Decimal("0.0003"),
        implementation_cost_assumption_variance=Decimal("0"),
        one_way_turnover=Decimal("0.25"),
        weights=(
            PortfolioWeight("SEC:A", Decimal("0.5")),
            PortfolioWeight("SEC:B", Decimal("0.5")),
        ),
        net_exposure=Decimal("1"),
        gross_exposure=Decimal("1"),
        solution_drift_turnover=Decimal("0"),
        cumulative_net_return=cumulative,
        running_maximum_drawdown=Decimal("0"),
        running_average_implementation_cost_rate=Decimal("0.0003"),
        source_fact_ids=(
            f"source:{index}:A",
            f"source:{index}:B",
        ),
        triggered_kill_conditions=(),
        authorization_state_after_record=(
            PortfolioPaperAuthorizationState.ACTIVE
        ),
        paper_authority="SHADOW_ONLY",
        order_authority="NONE",
        capital_authority="NONE",
    )
    return replace(
        draft,
        observation_id=portfolio_paper_shadow_observation_identity(
            draft
        ),
    )


def observations(count=3):
    return tuple(shadow_observation(index) for index in range(count))


def benchmarks(shadow=None):
    shadow = shadow or observations()
    return tuple(
        PortfolioPaperBenchmarkObservation(
            shadow_observation_id=item.observation_id,
            benchmark_id="benchmark:market-cap",
            period_start=item.period_start,
            period_end=item.period_end,
            total_return=Decimal("0.005"),
            source_fact_ids=(
                f"benchmark:{index}",
            ),
        )
        for index, item in enumerate(shadow)
    )


def terminal_event(
    *,
    new_state=PortfolioPaperAuthorizationState.CLOSED,
    kill_conditions=(),
    reason="Prospective shadow evaluation completed.",
):
    auth = authorization()
    draft = PortfolioPaperEnforcementEvent(
        event_id="placeholder",
        ordinal=1,
        authorization_id=auth.authorization_id,
        occurred_at=AT + timedelta(days=8),
        prior_state=PortfolioPaperAuthorizationState.ACTIVE,
        new_state=new_state,
        kill_conditions=kill_conditions,
        reason=reason,
        evidence_references=("event:evidence",),
    )
    return replace(
        draft,
        event_id=portfolio_paper_enforcement_event_identity(draft),
    )


def policy(**overrides):
    values = {
        "minimum_observations": 3,
        "maximum_geometric_mean_return_calibration_error": Decimal("0.001"),
        "maximum_mean_absolute_cost_assumption_error": Decimal("0.0005"),
        "maximum_average_solution_drift_turnover": Decimal("0.10"),
        "minimum_benchmark_relative_wealth_return": Decimal("-0.10"),
        "rationale": "Stage 10.10 prospective review thresholds.",
        "evidence_references": ("review-policy:evidence",),
    }
    values.update(overrides)
    return PortfolioPaperReviewPolicy(**values)


def review(
    *,
    shadow=None,
    event=None,
    review_policy=None,
):
    shadow = shadow if shadow is not None else observations()
    event_tuple = () if event is None else (event,)
    return PortfolioPaperReviewEngine().evaluate(
        authorization=authorization(),
        comparison=comparison(),
        observations=shadow,
        benchmarks=benchmarks(shadow),
        events=event_tuple,
        policy=review_policy or policy(),
    )


class PortfolioPaperReviewTests(unittest.TestCase):
    def test_completed_clean_run_is_within_review_policy(self):
        dossier = review(event=terminal_event())
        self.assertEqual(
            dossier.state,
            PortfolioPaperReviewState.WITHIN_POLICY,
        )
        self.assertEqual(
            dossier.authorization_final_state,
            PortfolioPaperAuthorizationState.CLOSED,
        )
        self.assertEqual(dossier.observations, 3)
        self.assertEqual(
            dossier.geometric_mean_return_calibration_error,
            Decimal("0.0"),
        )
        self.assertEqual(
            dossier.mean_absolute_cost_assumption_error,
            Decimal("0"),
        )
        self.assertEqual(dossier.kill_event_count, 0)
        self.assertEqual(dossier.promotion_authority, "NONE")
        self.assertEqual(dossier.order_authority, "NONE")
        self.assertEqual(dossier.capital_authority, "NONE")

    def test_active_authorization_remains_open_and_cannot_be_postmortemed(self):
        dossier = review(event=None)
        self.assertEqual(dossier.state, PortfolioPaperReviewState.OPEN)
        with self.assertRaises(ValueError):
            PortfolioPaperPostmortemEngine().close(
                review=dossier,
                closed_at=AT + timedelta(days=9),
                reviewer="reviewer",
                independent_challenger="challenger",
                disposition=(
                    PortfolioPaperIterationDisposition.ITERATE_RESEARCH
                ),
                lessons=("Need another iteration.",),
                limitations=("Run is still active.",),
                rationale="Should fail while open.",
                evidence_references=("postmortem:evidence",),
            )

    def test_kill_event_forces_review_required(self):
        event = terminal_event(
            new_state=PortfolioPaperAuthorizationState.SUSPENDED,
            kill_conditions=(
                PortfolioPaperKillCondition.TURNOVER_BREACH,
            ),
            reason="Turnover kill condition triggered.",
        )
        dossier = review(event=event)
        self.assertEqual(
            dossier.state,
            PortfolioPaperReviewState.REVIEW_REQUIRED,
        )
        self.assertEqual(dossier.kill_event_count, 1)
        self.assertIn("TURNOVER_BREACH", dossier.kill_conditions)

    def test_insufficient_terminal_run_is_fail_visible(self):
        shadow = observations(2)
        dossier = review(
            shadow=shadow,
            event=terminal_event(),
        )
        self.assertEqual(
            dossier.state,
            PortfolioPaperReviewState.INSUFFICIENT_EVIDENCE,
        )
        with self.assertRaises(ValueError):
            PortfolioPaperPostmortemEngine().close(
                review=dossier,
                closed_at=AT + timedelta(days=9),
                reviewer="reviewer",
                independent_challenger="challenger",
                disposition=(
                    PortfolioPaperIterationDisposition.ITERATE_RESEARCH
                ),
                lessons=("Evidence horizon was too short.",),
                limitations=("Only two observations.",),
                rationale="Cannot conclude.",
                evidence_references=("postmortem:evidence",),
            )

    def test_return_calibration_policy_can_force_review(self):
        shifted = tuple(
            shadow_observation(index, net=Decimal("0.03"))
            for index in range(3)
        )
        dossier = review(
            shadow=shifted,
            event=terminal_event(),
            review_policy=policy(
                maximum_geometric_mean_return_calibration_error=(
                    Decimal("0.005")
                )
            ),
        )
        self.assertEqual(
            dossier.state,
            PortfolioPaperReviewState.REVIEW_REQUIRED,
        )
        self.assertTrue(
            any("calibration" in reason for reason in dossier.reasons)
        )

    def test_tampered_shadow_observation_fails_closed(self):
        shadow = list(observations())
        shadow[0] = replace(
            shadow[0],
            net_return=Decimal("0.50"),
        )
        with self.assertRaises(ValueError):
            PortfolioPaperReviewEngine().evaluate(
                authorization=authorization(),
                comparison=comparison(),
                observations=tuple(shadow),
                benchmarks=benchmarks(observations()),
                events=(terminal_event(),),
                policy=policy(),
            )

    def test_postmortem_can_only_recommend_research_not_promotion(self):
        dossier = review(event=terminal_event())
        postmortem = PortfolioPaperPostmortemEngine().close(
            review=dossier,
            closed_at=AT + timedelta(days=9),
            reviewer="portfolio-postmortem-reviewer",
            independent_challenger="independent-postmortem-reviewer",
            disposition=(
                PortfolioPaperIterationDisposition.ITERATE_RESEARCH
            ),
            lessons=(
                "Prospective behavior stayed close to the OOS reference.",
            ),
            limitations=(
                "Observation horizon remains short and shadow-only.",
            ),
            rationale="One more research iteration is justified.",
            evidence_references=("postmortem:evidence",),
        )
        self.assertEqual(
            postmortem.next_stage_recommendation,
            "RESEARCH",
        )
        self.assertEqual(
            postmortem.research_iteration_authority,
            "RECOMMENDATION_ONLY",
        )
        self.assertEqual(postmortem.promotion_authority, "NONE")
        self.assertEqual(postmortem.order_authority, "NONE")
        self.assertEqual(postmortem.capital_authority, "NONE")

    def test_review_and_postmortem_store_are_idempotent(self):
        dossier = review(event=terminal_event())
        postmortem = PortfolioPaperPostmortemEngine().close(
            review=dossier,
            closed_at=AT + timedelta(days=9),
            reviewer="portfolio-postmortem-reviewer",
            independent_challenger="independent-postmortem-reviewer",
            disposition=(
                PortfolioPaperIterationDisposition.CLOSE_RESEARCH_LINE
            ),
            lessons=("Document the completed shadow result.",),
            limitations=("No live-capital evidence exists.",),
            rationale="Close this research line after the shadow review.",
            evidence_references=("postmortem:evidence",),
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = PortfolioPaperReviewStore(
                Path(tmp) / "paper-review.duckdb"
            )
            self.assertTrue(store.add_review(dossier))
            self.assertFalse(store.add_review(dossier))
            self.assertTrue(store.add_postmortem(postmortem))
            self.assertFalse(store.add_postmortem(postmortem))
            store.close()


if __name__ == "__main__":
    unittest.main()
