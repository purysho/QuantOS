import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from quantos.portfolio_comparison import (
    PortfolioComparisonDossier,
    PortfolioFoldOutcome,
    PortfolioMethod,
    PortfolioMethodEvaluation,
    portfolio_comparison_dossier_identity,
)
from quantos.portfolio_decision import (
    MethodAssessment,
    MethodAssessmentState,
    PortfolioDecisionDisposition,
    PortfolioResearchDecisionEngine,
    PortfolioResearchDecisionStore,
)
from quantos.portfolio_robustness import (
    ConstraintFragility,
    MethodWeightStability,
    PortfolioRobustnessDossier,
    PortfolioRobustnessState,
    portfolio_robustness_dossier_identity,
)

UTC = timezone.utc
AT = datetime(2026, 9, 25, 12, tzinfo=UTC)


def evaluation(method):
    outcomes = (
        PortfolioFoldOutcome(
            fold_id="fold:1",
            method=method,
            solution_id=f"solution:{method.value}:1",
            gross_return=Decimal("0.01"),
            implementation_cost_rate=Decimal("0.001"),
            net_return=Decimal("0.009"),
            one_way_turnover=Decimal("0.20"),
            effective_number_of_assets=Decimal("3"),
            maximum_absolute_weight=Decimal("0.40"),
        ),
        PortfolioFoldOutcome(
            fold_id="fold:2",
            method=method,
            solution_id=f"solution:{method.value}:2",
            gross_return=Decimal("0.02"),
            implementation_cost_rate=Decimal("0.001"),
            net_return=Decimal("0.019"),
            one_way_turnover=Decimal("0.15"),
            effective_number_of_assets=Decimal("3.2"),
            maximum_absolute_weight=Decimal("0.38"),
        ),
    )
    return PortfolioMethodEvaluation(
        method=method,
        solution_ids=tuple(item.solution_id for item in outcomes),
        fold_outcomes=outcomes,
        cumulative_net_return=Decimal("0.028171"),
        realized_period_volatility=Decimal("0.007"),
        maximum_drawdown=Decimal("0"),
        expected_shortfall_return=Decimal("0.009"),
        total_implementation_cost_rate=Decimal("0.002"),
        average_one_way_turnover=Decimal("0.175"),
        average_effective_number_of_assets=Decimal("3.1"),
        maximum_absolute_weight=Decimal("0.40"),
        market_relative_wealth_return=Decimal("0.01"),
    )


def comparison():
    draft = PortfolioComparisonDossier(
        dossier_id="placeholder",
        model_id="research-model:" + "m" * 64,
        manifest_id="research-run-manifest:" + "r" * 64,
        constraint_policy_id="portfolio-constraint-policy:" + "c" * 64,
        comparison_policy_id="portfolio-comparison-policy:" + "p" * 64,
        fold_ids=("fold:1", "fold:2"),
        benchmark_id="benchmark:market-cap",
        evaluations=tuple(
            evaluation(method)
            for method in (
                PortfolioMethod.EQUAL_WEIGHT,
                PortfolioMethod.INVERSE_VOLATILITY,
                PortfolioMethod.MINIMUM_VARIANCE,
            )
        ),
        selection_authority="NONE",
        capital_authority="NONE",
        caveat="OOS comparison caveat.",
    )
    return replace(
        draft,
        dossier_id=portfolio_comparison_dossier_identity(draft),
    )


def robustness(state=PortfolioRobustnessState.WITHIN_POLICY):
    comp = comparison()
    weight_stability = tuple(
        MethodWeightStability(
            method=item.method,
            adjacent_turnovers=(Decimal("0.10"),),
            average_adjacent_turnover=Decimal("0.10"),
            maximum_adjacent_turnover=Decimal("0.10"),
        )
        for item in comp.evaluations
    )
    draft = PortfolioRobustnessDossier(
        dossier_id="placeholder",
        model_id=comp.model_id,
        manifest_id=comp.manifest_id,
        comparison_dossier_id=comp.dossier_id,
        constraint_policy_id=comp.constraint_policy_id,
        robustness_policy_id="portfolio-robustness-policy:" + "b" * 64,
        state=state,
        method_weight_stability=weight_stability,
        cluster_stability=(),
        covariance_sensitivity=(),
        constraint_fragility=ConstraintFragility(
            minimum_upper_weight_headroom=Decimal("0.20"),
            minimum_turnover_headroom=None,
            optimal_inaccurate_count=0,
        ),
        reasons=(
            "portfolio construction stability remains within frozen policy",
        ),
        selection_authority="NONE",
        capital_authority="NONE",
        caveat="Robustness caveat.",
    )
    return replace(
        draft,
        dossier_id=portfolio_robustness_dossier_identity(draft),
    )


def assessments(selected=PortfolioMethod.MINIMUM_VARIANCE):
    return tuple(
        MethodAssessment(
            method=method,
            state=(
                MethodAssessmentState.SELECTED
                if method is selected
                else MethodAssessmentState.NOT_SELECTED
            ),
            rationale=(
                "Chosen for continued PAPER review."
                if method is selected
                else "Retained as a comparison baseline, not selected."
            ),
            evidence_references=(f"assessment:{method.value}",),
        )
        for method in (
            PortfolioMethod.EQUAL_WEIGHT,
            PortfolioMethod.INVERSE_VOLATILITY,
            PortfolioMethod.MINIMUM_VARIANCE,
        )
    )


class PortfolioResearchDecisionTests(unittest.TestCase):
    def test_human_gate_can_recommend_one_method_for_paper_review(self):
        decision = PortfolioResearchDecisionEngine().decide(
            comparison=comparison(),
            robustness=robustness(),
            reviewed_at=AT,
            reviewer="portfolio-reviewer",
            independent_challenger="independent-risk-reviewer",
            disposition=(
                PortfolioDecisionDisposition.RECOMMEND_FOR_PAPER_REVIEW
            ),
            method_assessments=assessments(),
            tradeoffs=(
                "Minimum variance is more model-sensitive than equal weight.",
                "Equal weight is simpler but ignores covariance structure.",
            ),
            challenger_objections=(
                "Monitor covariance-estimator sensitivity in PAPER.",
            ),
            unresolved_objections=(),
            rationale="Continue one method to a separate PAPER authorization review.",
            evidence_references=("decision:evidence",),
        )
        self.assertEqual(
            decision.selected_method,
            PortfolioMethod.MINIMUM_VARIANCE,
        )
        self.assertEqual(decision.method_selection_origin, "HUMAN_REVIEW")
        self.assertEqual(decision.paper_authority, "NONE")
        self.assertEqual(decision.capital_authority, "NONE")

    def test_review_required_robustness_blocks_paper_review_recommendation(self):
        with self.assertRaises(ValueError):
            PortfolioResearchDecisionEngine().decide(
                comparison=comparison(),
                robustness=robustness(
                    PortfolioRobustnessState.REVIEW_REQUIRED
                ),
                reviewed_at=AT,
                reviewer="portfolio-reviewer",
                independent_challenger="independent-risk-reviewer",
                disposition=(
                    PortfolioDecisionDisposition.RECOMMEND_FOR_PAPER_REVIEW
                ),
                method_assessments=assessments(),
                tradeoffs=("Explicit tradeoff.",),
                challenger_objections=(),
                unresolved_objections=(),
                rationale="Should fail.",
                evidence_references=("decision:evidence",),
            )

    def test_defer_requires_unresolved_challenger_objection_and_no_selection(self):
        no_selection = tuple(
            MethodAssessment(
                method=item.method,
                state=MethodAssessmentState.NOT_SELECTED,
                rationale="Awaiting further evidence.",
                evidence_references=item.evidence_references,
            )
            for item in assessments()
        )
        decision = PortfolioResearchDecisionEngine().decide(
            comparison=comparison(),
            robustness=robustness(),
            reviewed_at=AT,
            reviewer="portfolio-reviewer",
            independent_challenger="independent-risk-reviewer",
            disposition=PortfolioDecisionDisposition.DEFER,
            method_assessments=no_selection,
            tradeoffs=("More stability evidence is needed.",),
            challenger_objections=("Need a longer OOS history.",),
            unresolved_objections=("Need a longer OOS history.",),
            rationale="Defer pending more prospective evidence.",
            evidence_references=("decision:evidence",),
        )
        self.assertIsNone(decision.selected_method)
        self.assertEqual(
            decision.disposition,
            PortfolioDecisionDisposition.DEFER,
        )

    def test_every_evaluated_method_must_have_explicit_assessment(self):
        with self.assertRaises(ValueError):
            PortfolioResearchDecisionEngine().decide(
                comparison=comparison(),
                robustness=robustness(),
                reviewed_at=AT,
                reviewer="portfolio-reviewer",
                independent_challenger="independent-risk-reviewer",
                disposition=(
                    PortfolioDecisionDisposition.RECOMMEND_FOR_PAPER_REVIEW
                ),
                method_assessments=assessments()[:-1],
                tradeoffs=("Tradeoff.",),
                challenger_objections=(),
                unresolved_objections=(),
                rationale="Incomplete assessment should fail.",
                evidence_references=("decision:evidence",),
            )

    def test_same_person_cannot_be_reviewer_and_challenger(self):
        with self.assertRaises(ValueError):
            PortfolioResearchDecisionEngine().decide(
                comparison=comparison(),
                robustness=robustness(),
                reviewed_at=AT,
                reviewer="same-person",
                independent_challenger="same-person",
                disposition=PortfolioDecisionDisposition.REJECT,
                method_assessments=tuple(
                    MethodAssessment(
                        method=item.method,
                        state=MethodAssessmentState.NOT_SELECTED,
                        rationale="Rejected.",
                        evidence_references=item.evidence_references,
                    )
                    for item in assessments()
                ),
                tradeoffs=("Tradeoff.",),
                challenger_objections=(),
                unresolved_objections=(),
                rationale="Reject.",
                evidence_references=("decision:evidence",),
            )

    def test_store_is_idempotent(self):
        decision = PortfolioResearchDecisionEngine().decide(
            comparison=comparison(),
            robustness=robustness(),
            reviewed_at=AT,
            reviewer="portfolio-reviewer",
            independent_challenger="independent-risk-reviewer",
            disposition=(
                PortfolioDecisionDisposition.RECOMMEND_FOR_PAPER_REVIEW
            ),
            method_assessments=assessments(),
            tradeoffs=("Explicit tradeoff.",),
            challenger_objections=(),
            unresolved_objections=(),
            rationale="Continue to separate PAPER review.",
            evidence_references=("decision:evidence",),
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = PortfolioResearchDecisionStore(
                Path(tmp) / "portfolio-decision.duckdb"
            )
            self.assertTrue(store.add(decision))
            self.assertFalse(store.add(decision))
            store.close()


if __name__ == "__main__":
    unittest.main()
