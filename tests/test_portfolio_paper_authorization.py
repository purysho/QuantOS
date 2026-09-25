import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from quantos.model_registry import (
    ModelLifecycleStage,
    ModelRegistryState,
    ResearchRunManifest,
)
from quantos.portfolio_comparison import (
    PortfolioComparisonDossier,
    PortfolioFoldOutcome,
    PortfolioMethod,
    PortfolioMethodEvaluation,
    portfolio_comparison_dossier_identity,
)
from quantos.portfolio_construction import (
    BaselineAllocator,
    PortfolioSolution,
    PortfolioWeight,
    portfolio_solution_identity,
)
from quantos.portfolio_decision import (
    MethodAssessment,
    MethodAssessmentState,
    PortfolioDecisionDisposition,
    PortfolioResearchDecision,
    portfolio_research_decision_identity,
)
from quantos.portfolio_paper_authorization import (
    MANDATORY_KILL_CONDITIONS,
    PortfolioPaperAuthorizationEngine,
    PortfolioPaperAuthorizationStore,
    PortfolioPaperExecutionAssumptions,
    PortfolioPaperMonitoringPolicy,
)
from quantos.portfolio_robustness import (
    ConstraintFragility,
    MethodWeightStability,
    PortfolioRobustnessDossier,
    PortfolioRobustnessState,
    portfolio_robustness_dossier_identity,
)
from quantos.readiness import ProspectiveShadowPermit

UTC = timezone.utc
AT = datetime(2026, 9, 25, 12, tzinfo=UTC)
MODEL_ID = "research-model:" + "m" * 64
MANIFEST_ID = "research-run-manifest:" + "r" * 64
CASE_ID = "research-case:" + "c" * 64
DOSSIER = "case-dossier:" + "d" * 64
PERMIT_ID = "shadow-permit:" + "p" * 64
CONSTRAINT_ID = "portfolio-constraint-policy:" + "q" * 64


def manifest():
    return ResearchRunManifest(
        manifest_id=MANIFEST_ID,
        model_id=MODEL_ID,
        experiment_id="research-experiment:" + "e" * 64,
        research_case_id=CASE_ID,
        case_dossier_fingerprint=DOSSIER,
        factor_id="factor-spec:" + "f" * 64,
        universe_policy_id="universe-policy:" + "u" * 64,
        validation_plan_id="walk-forward-plan:" + "v" * 64,
        backtest_id="economic-backtest:" + "b" * 64,
        backtest_policy_id="backtest-policy:" + "k" * 64,
        performance_analysis_id="performance-analysis:" + "a" * 64,
        performance_policy_id="performance-policy:" + "n" * 64,
        multiple_testing_audit_id="multiple-testing-audit:" + "t" * 64,
        overfitting_policy_id="multiple-testing-policy:" + "o" * 64,
        selected_variant_id="selected",
        selected_variant_series_id="variant-return-series:" + "s" * 64,
        shadow_permit_id=PERMIT_ID,
        benchmark_kinds=("CASH", "EQUAL_WEIGHT", "INVERSE_VOL", "MARKET_CAP"),
        dataset_fingerprints=("dataset:prices",),
        code_revision="git:stage10.8",
        evidence_references=("manifest:evidence",),
        eligible_stage=ModelLifecycleStage.PAPER,
    )


def registry(stage=ModelLifecycleStage.PAPER):
    return ModelRegistryState(
        model_id=MODEL_ID,
        manifest_id=MANIFEST_ID,
        stage=stage,
        updated_at=AT,
        updated_by="registry-reviewer",
    )


def permit():
    return ProspectiveShadowPermit(
        permit_id=PERMIT_ID,
        case_id=CASE_ID,
        case_dossier_fingerprint=DOSSIER,
        scenario_set_id="scenario-set:" + "s" * 64,
        issued_at=AT - timedelta(hours=1),
        issued_by="case-review-panel",
        purpose="PROSPECTIVE_SHADOW_ONLY",
    )


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
        model_id=MODEL_ID,
        manifest_id=MANIFEST_ID,
        constraint_policy_id=CONSTRAINT_ID,
        comparison_policy_id="portfolio-comparison-policy:" + "x" * 64,
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
        caveat="Common OOS comparison.",
    )
    return replace(
        draft,
        dossier_id=portfolio_comparison_dossier_identity(draft),
    )


def robustness():
    comp = comparison()
    stability = tuple(
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
        model_id=MODEL_ID,
        manifest_id=MANIFEST_ID,
        comparison_dossier_id=comp.dossier_id,
        constraint_policy_id=CONSTRAINT_ID,
        robustness_policy_id="portfolio-robustness-policy:" + "z" * 64,
        state=PortfolioRobustnessState.WITHIN_POLICY,
        method_weight_stability=stability,
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
        caveat="Robustness diagnostics.",
    )
    return replace(
        draft,
        dossier_id=portfolio_robustness_dossier_identity(draft),
    )


def decision():
    comp = comparison()
    robust = robustness()
    assessments = tuple(
        MethodAssessment(
            method=method,
            state=(
                MethodAssessmentState.SELECTED
                if method is PortfolioMethod.EQUAL_WEIGHT
                else MethodAssessmentState.NOT_SELECTED
            ),
            rationale=(
                "Selected for separate PAPER review."
                if method is PortfolioMethod.EQUAL_WEIGHT
                else "Not selected after common OOS review."
            ),
            evidence_references=(f"assessment:{method.value}",),
        )
        for method in (
            PortfolioMethod.EQUAL_WEIGHT,
            PortfolioMethod.INVERSE_VOLATILITY,
            PortfolioMethod.MINIMUM_VARIANCE,
        )
    )
    draft = PortfolioResearchDecision(
        decision_id="placeholder",
        model_id=MODEL_ID,
        manifest_id=MANIFEST_ID,
        comparison_dossier_id=comp.dossier_id,
        robustness_dossier_id=robust.dossier_id,
        reviewed_at=AT + timedelta(hours=1),
        reviewer="portfolio-research-reviewer",
        independent_challenger="independent-challenger",
        disposition=PortfolioDecisionDisposition.RECOMMEND_FOR_PAPER_REVIEW,
        selected_method=PortfolioMethod.EQUAL_WEIGHT,
        method_assessments=assessments,
        tradeoffs=("Simplicity versus model sensitivity.",),
        challenger_objections=("Monitor drift prospectively.",),
        unresolved_objections=(),
        rationale="Recommend EqualWeight for separate shadow PAPER review.",
        evidence_references=("decision:evidence",),
        method_selection_origin="HUMAN_REVIEW",
        paper_authority="NONE",
        capital_authority="NONE",
        caveat="Research decision only.",
    )
    return replace(
        draft,
        decision_id=portfolio_research_decision_identity(draft),
    )


def selected_solution(allocator=BaselineAllocator.EQUAL_WEIGHT):
    weights = (
        PortfolioWeight("SEC:A", Decimal("0.5")),
        PortfolioWeight("SEC:B", Decimal("0.5")),
    )
    draft = PortfolioSolution(
        solution_id="placeholder",
        model_id=MODEL_ID,
        manifest_id=MANIFEST_ID,
        dataset_id="portfolio-dataset:" + "g" * 64,
        decision_time=AT + timedelta(minutes=30),
        allocator=allocator,
        engine_name="skfolio",
        engine_version="1.x",
        constraint_policy_id=CONSTRAINT_ID,
        weights=weights,
        net_exposure=Decimal("1.0"),
        gross_exposure=Decimal("1.0"),
        one_way_turnover=Decimal("1.0"),
        previous_weights=(),
        capital_authority="NONE",
    )
    return replace(
        draft,
        solution_id=portfolio_solution_identity(draft),
    )


def execution():
    return PortfolioPaperExecutionAssumptions(
        commission_bps=Decimal("1"),
        half_spread_bps=Decimal("1"),
        slippage_bps=Decimal("2"),
        market_impact_bps=Decimal("2"),
        annual_borrow_bps=Decimal("0"),
        rationale="Conservative shadow implementation assumptions.",
        evidence_references=("execution:evidence",),
    )


def monitoring():
    return PortfolioPaperMonitoringPolicy(
        minimum_observations=5,
        maximum_drawdown_abs=Decimal("0.15"),
        maximum_one_way_turnover=Decimal("1"),
        maximum_average_implementation_cost_rate=Decimal("0.01"),
        maximum_absolute_weight=Decimal("0.60"),
        maximum_gross_exposure=Decimal("1"),
        maximum_solution_drift_turnover=Decimal("0.25"),
        kill_conditions=tuple(
            sorted(
                MANDATORY_KILL_CONDITIONS,
                key=lambda item: item.value,
            )
        ),
        rationale="Shadow PAPER kill and monitoring thresholds.",
        evidence_references=("monitoring:evidence",),
    )


def authorize(**overrides):
    values = {
        "manifest": manifest(),
        "registry_state": registry(),
        "permit": permit(),
        "comparison": comparison(),
        "robustness": robustness(),
        "decision": decision(),
        "selected_solution": selected_solution(),
        "execution_assumptions": execution(),
        "monitoring_policy": monitoring(),
        "authorized_at": AT + timedelta(hours=2),
        "expires_at": AT + timedelta(days=30),
        "portfolio_reviewer": "paper-portfolio-reviewer",
        "independent_risk_reviewer": "paper-risk-reviewer",
        "rationale": "Authorize shadow-only prospective portfolio evaluation.",
        "evidence_references": ("paper-authorization:evidence",),
    }
    values.update(overrides)
    return PortfolioPaperAuthorizationEngine().authorize(**values)


class PortfolioPaperAuthorizationTests(unittest.TestCase):
    def test_valid_authorization_is_shadow_only_and_finite_lived(self):
        item = authorize()
        self.assertEqual(item.selected_method, PortfolioMethod.EQUAL_WEIGHT)
        self.assertEqual(item.lifecycle_stage, "PAPER")
        self.assertEqual(item.paper_authority, "SHADOW_ONLY")
        self.assertEqual(item.order_authority, "NONE")
        self.assertEqual(item.capital_authority, "NONE")
        self.assertEqual(
            item.purpose,
            "PROSPECTIVE_PORTFOLIO_SHADOW_ONLY",
        )
        self.assertGreater(item.expires_at, item.authorized_at)

    def test_backtested_registry_state_cannot_authorize_portfolio_paper(self):
        with self.assertRaises(ValueError):
            authorize(
                registry_state=registry(ModelLifecycleStage.BACKTESTED)
            )

    def test_wrong_shadow_permit_fails_closed(self):
        wrong = replace(
            permit(),
            permit_id="shadow-permit:" + "w" * 64,
        )
        with self.assertRaises(ValueError):
            authorize(permit=wrong)

    def test_solution_method_must_match_human_selection(self):
        with self.assertRaises(ValueError):
            authorize(
                selected_solution=selected_solution(
                    BaselineAllocator.INVERSE_VOLATILITY
                )
            )

    def test_selected_solution_must_already_fit_paper_thresholds(self):
        restrictive = replace(
            monitoring(),
            maximum_absolute_weight=Decimal("0.40"),
        )
        with self.assertRaises(ValueError):
            authorize(monitoring_policy=restrictive)

    def test_full_mandatory_kill_condition_set_is_required(self):
        with self.assertRaises(ValueError):
            PortfolioPaperMonitoringPolicy(
                minimum_observations=5,
                maximum_drawdown_abs=Decimal("0.15"),
                maximum_one_way_turnover=Decimal("1"),
                maximum_average_implementation_cost_rate=Decimal("0.01"),
                maximum_absolute_weight=Decimal("0.60"),
                maximum_gross_exposure=Decimal("1"),
                maximum_solution_drift_turnover=Decimal("0.25"),
                kill_conditions=tuple(
                    sorted(
                        list(MANDATORY_KILL_CONDITIONS)[:-1],
                        key=lambda item: item.value,
                    )
                ),
                rationale="Incomplete kill set.",
                evidence_references=("monitoring:evidence",),
            )

    def test_authorization_store_is_idempotent(self):
        item = authorize()
        with tempfile.TemporaryDirectory() as tmp:
            store = PortfolioPaperAuthorizationStore(
                Path(tmp) / "portfolio-paper.duckdb"
            )
            self.assertTrue(store.add(item))
            self.assertFalse(store.add(item))
            store.close()


if __name__ == "__main__":
    unittest.main()
