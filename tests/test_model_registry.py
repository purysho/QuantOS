import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from quantos.backtest_economics import (
    BacktestResult,
    BenchmarkKind,
)
from quantos.factor_contracts import (
    CrossSectionTransform,
    FactorComponent,
    FactorSpecification,
)
from quantos.model_registry import (
    ModelLifecycleStage,
    ModelRegistry,
    ResearchRunManifestBuilder,
)
from quantos.overfitting_diagnostics import (
    DeflatedSharpeResult,
    MultipleTestingAudit,
    PBOResult,
    VariantReturnSeries,
)
from quantos.performance_analytics import (
    MetricStatus,
    PerformanceAnalysis,
    RiskAdjustedMetric,
)
from quantos.readiness import ProspectiveShadowPermit
from quantos.validation import (
    ResearchExperimentSpecification,
    WalkForwardValidationPlan,
)

UTC = timezone.utc
AT = datetime(2026, 9, 25, 12, tzinfo=UTC)
CASE = "research-case:" + "c" * 64


def factor(version="1"):
    return FactorSpecification(
        name="quality-value",
        version=version,
        universe_policy_id="universe-policy:" + "u" * 64,
        components=(
            FactorComponent(
                feature_name="quality",
                lookback_periods=4,
                minimum_lag_seconds=3600,
                maximum_staleness_seconds=86400 * 120,
                weight=Decimal("1"),
                transform=CrossSectionTransform.PERCENTILE_RANK,
            ),
        ),
        rationale="Reviewed factor.",
        evidence_references=("paper:qv",),
    )


def experiment(f):
    return ResearchExperimentSpecification(
        name="qv-study",
        version="1",
        factor_id=f.factor_id,
        benchmark_id="benchmark:market",
        hypothesis_reference="hypothesis:qv",
        variants_tested=2,
        primary_metric="sharpe",
        rationale="Frozen experiment.",
        evidence_references=("plan:qv",),
        research_case_id=CASE,
    )


def validation(exp):
    return WalkForwardValidationPlan(
        plan_id="walk-forward-plan:" + "v" * 64,
        experiment_id=exp.experiment_id,
        factor_id=exp.factor_id,
        policy_id="walk-forward-policy:" + "p" * 64,
        sample_set_id="validation-sample-set:" + "s" * 64,
        folds=(),
    )


def backtest(exp, plan, suffix="a"):
    return BacktestResult(
        backtest_id="economic-backtest:" + suffix * 64,
        factor_id=exp.factor_id,
        validation_plan_id=plan.plan_id,
        policy_id="backtest-policy:" + "b" * 64,
        periods=(),
        final_gross_wealth=Decimal("1.2"),
        final_net_wealth=Decimal("1.1"),
        final_benchmark_wealth=(
            (BenchmarkKind.CASH, Decimal("1")),
            (BenchmarkKind.MARKET_CAP, Decimal("1.05")),
            (BenchmarkKind.EQUAL_WEIGHT, Decimal("1.04")),
            (BenchmarkKind.INVERSE_VOL, Decimal("1.03")),
        ),
    )


def performance(bt):
    metric = RiskAdjustedMetric(
        status=MetricStatus.AVAILABLE,
        value=Decimal("1"),
        observations=20,
        reason=None,
    )
    return PerformanceAnalysis(
        analysis_id="performance-analysis:" + "a" * 64,
        backtest_id=bt.backtest_id,
        policy_id="performance-policy:" + "p" * 64,
        period_count=20,
        cumulative_gross_return=Decimal("0.2"),
        cumulative_net_return=Decimal("0.1"),
        annualized_net_return=Decimal("0.1"),
        annualized_net_volatility=metric,
        sharpe_ratio=metric,
        maximum_drawdown=Decimal("-0.1"),
        expected_shortfall_return=metric,
        total_transaction_cost_rate=Decimal("0.01"),
        total_borrow_cost_rate=Decimal("0"),
        terminal_wealth_cost_drag=Decimal("0.1"),
        average_one_way_turnover=Decimal("0.2"),
        benchmark_analytics=(),
        ic_periods=(),
        mean_information_coefficient=metric,
    )


def variant(exp, bt, variant_id="selected"):
    return VariantReturnSeries(
        experiment_id=exp.experiment_id,
        variant_id=variant_id,
        backtest_id=bt.backtest_id,
        net_period_returns=(
            Decimal("0.01"),
            Decimal("0.02"),
            Decimal("-0.01"),
            Decimal("0.03"),
        ),
        evidence_references=("backtest:selected",),
    )


def audit(exp, selected):
    other = VariantReturnSeries(
        experiment_id=exp.experiment_id,
        variant_id="other",
        backtest_id="economic-backtest:" + "o" * 64,
        net_period_returns=(
            Decimal("0"),
            Decimal("0.01"),
            Decimal("-0.02"),
            Decimal("0.01"),
        ),
        evidence_references=("backtest:other",),
    )
    dsr = DeflatedSharpeResult(
        result_id="deflated-sharpe:" + "d" * 64,
        selected_variant_id=selected.variant_id,
        observations=4,
        trial_count=2,
        observed_sharpe=Decimal("1"),
        sharpe_variance_across_trials=Decimal("0.1"),
        expected_max_sharpe_under_null=Decimal("0.2"),
        return_skewness=Decimal("0"),
        return_kurtosis=Decimal("3"),
        z_score=Decimal("1"),
        probability=Decimal("0.84"),
    )
    pbo = PBOResult(
        result_id="probability-backtest-overfitting:" + "q" * 64,
        slices=4,
        combination_count=6,
        pbo=Decimal("0.2"),
        combinations=(),
    )
    return MultipleTestingAudit(
        audit_id="multiple-testing-audit:" + "m" * 64,
        experiment_id=exp.experiment_id,
        policy_id="multiple-testing-policy:" + "t" * 64,
        variant_series_ids=tuple(
            sorted((selected.series_id, other.series_id))
        ),
        selected_variant_id=selected.variant_id,
        deflated_sharpe=dsr,
        probability_backtest_overfitting=pbo,
        caveat="Research diagnostic only.",
    )


def shadow(exp, suffix="x"):
    return ProspectiveShadowPermit(
        permit_id="shadow-permit:" + suffix * 64,
        case_id=exp.research_case_id,
        case_dossier_fingerprint="case-dossier:" + suffix * 64,
        scenario_set_id="scenario-set:" + suffix * 64,
        issued_at=AT,
        issued_by="reviewer",
        purpose="PROSPECTIVE_SHADOW_ONLY",
    )


def manifest(stage):
    f = factor()
    exp = experiment(f)
    kwargs = dict(
        experiment=exp,
        factor=f,
        dataset_fingerprints=("dataset:prices", "dataset:fundamentals"),
        code_revision="git:abc123",
        evidence_references=("run:evidence",),
    )
    if stage in {
        ModelLifecycleStage.VALIDATED,
        ModelLifecycleStage.BACKTESTED,
        ModelLifecycleStage.PAPER,
    }:
        plan = validation(exp)
        kwargs["validation_plan"] = plan
    if stage in {
        ModelLifecycleStage.BACKTESTED,
        ModelLifecycleStage.PAPER,
    }:
        bt = backtest(exp, plan)
        selected = variant(exp, bt)
        kwargs.update(
            backtest=bt,
            performance=performance(bt),
            multiple_testing=audit(exp, selected),
            selected_variant=selected,
        )
    if stage is ModelLifecycleStage.PAPER:
        kwargs["shadow_permit"] = shadow(exp)
    return ResearchRunManifestBuilder().build(**kwargs)


class ModelRegistryTests(unittest.TestCase):
    def test_manifest_eligibility_advances_only_with_complete_artifact_chain(self):
        self.assertEqual(
            manifest(ModelLifecycleStage.RESEARCH).eligible_stage,
            ModelLifecycleStage.RESEARCH,
        )
        self.assertEqual(
            manifest(ModelLifecycleStage.VALIDATED).eligible_stage,
            ModelLifecycleStage.VALIDATED,
        )
        self.assertEqual(
            manifest(ModelLifecycleStage.BACKTESTED).eligible_stage,
            ModelLifecycleStage.BACKTESTED,
        )
        self.assertEqual(
            manifest(ModelLifecycleStage.PAPER).eligible_stage,
            ModelLifecycleStage.PAPER,
        )

    def test_partial_backtest_chain_fails_closed(self):
        f = factor()
        exp = experiment(f)
        plan = validation(exp)
        bt = backtest(exp, plan)
        with self.assertRaises(ValueError):
            ResearchRunManifestBuilder().build(
                experiment=exp,
                factor=f,
                dataset_fingerprints=("dataset:prices",),
                code_revision="git:abc",
                evidence_references=("run:evidence",),
                validation_plan=plan,
                backtest=bt,
            )

    def test_shadow_permit_must_match_research_case(self):
        f = factor()
        exp = experiment(f)
        plan = validation(exp)
        bt = backtest(exp, plan)
        selected = variant(exp, bt)
        wrong = ProspectiveShadowPermit(
            permit_id="shadow-permit:" + "z" * 64,
            case_id="research-case:" + "z" * 64,
            case_dossier_fingerprint="case-dossier:" + "z" * 64,
            scenario_set_id="scenario-set:" + "z" * 64,
            issued_at=AT,
            issued_by="reviewer",
            purpose="PROSPECTIVE_SHADOW_ONLY",
        )
        with self.assertRaises(ValueError):
            ResearchRunManifestBuilder().build(
                experiment=exp,
                factor=f,
                dataset_fingerprints=("dataset:prices",),
                code_revision="git:abc",
                evidence_references=("run:evidence",),
                validation_plan=plan,
                backtest=bt,
                performance=performance(bt),
                multiple_testing=audit(exp, selected),
                selected_variant=selected,
                shadow_permit=wrong,
            )

    def test_registry_cannot_skip_lifecycle_stages(self):
        final = manifest(ModelLifecycleStage.PAPER)
        with tempfile.TemporaryDirectory() as tmp:
            registry = ModelRegistry(Path(tmp) / "registry.duckdb")
            state = registry.register(
                manifest=final,
                registered_at=AT,
                registered_by="researcher",
            )
            self.assertEqual(state.stage, ModelLifecycleStage.RESEARCH)
            with self.assertRaises(ValueError):
                registry.transition(
                    manifest=final,
                    to_stage=ModelLifecycleStage.BACKTESTED,
                    transitioned_at=AT,
                    transitioned_by="researcher",
                    reason="Skip validation.",
                    evidence_references=("review:no",),
                )
            registry.close()

    def test_registry_reaches_paper_only_through_adjacent_gates(self):
        final = manifest(ModelLifecycleStage.PAPER)
        with tempfile.TemporaryDirectory() as tmp:
            registry = ModelRegistry(Path(tmp) / "registry.duckdb")
            registry.register(
                manifest=final,
                registered_at=AT,
                registered_by="researcher",
            )
            for index, stage in enumerate(
                (
                    ModelLifecycleStage.VALIDATED,
                    ModelLifecycleStage.BACKTESTED,
                    ModelLifecycleStage.PAPER,
                ),
                start=1,
            ):
                state = registry.transition(
                    manifest=final,
                    to_stage=stage,
                    transitioned_at=AT,
                    transitioned_by="researcher",
                    reason=f"Gate {stage.value}.",
                    evidence_references=(f"review:{index}",),
                )
            self.assertEqual(state.stage, ModelLifecycleStage.PAPER)
            self.assertEqual(len(registry.history(final.model_id)), 3)
            registry.close()

    def test_approved_and_live_are_blocked_by_construction(self):
        final = manifest(ModelLifecycleStage.PAPER)
        with tempfile.TemporaryDirectory() as tmp:
            registry = ModelRegistry(Path(tmp) / "registry.duckdb")
            registry.register(
                manifest=final,
                registered_at=AT,
                registered_by="researcher",
            )
            with self.assertRaises(ValueError):
                registry.transition(
                    manifest=final,
                    to_stage=ModelLifecycleStage.LIVE,
                    transitioned_at=AT,
                    transitioned_by="researcher",
                    reason="Not allowed.",
                    evidence_references=("review:no",),
                )
            registry.close()

    def test_manifest_change_resets_prior_paper_state_to_research(self):
        final = manifest(ModelLifecycleStage.PAPER)
        with tempfile.TemporaryDirectory() as tmp:
            registry = ModelRegistry(Path(tmp) / "registry.duckdb")
            registry.register(
                manifest=final,
                registered_at=AT,
                registered_by="researcher",
            )
            for stage in (
                ModelLifecycleStage.VALIDATED,
                ModelLifecycleStage.BACKTESTED,
                ModelLifecycleStage.PAPER,
            ):
                registry.transition(
                    manifest=final,
                    to_stage=stage,
                    transitioned_at=AT,
                    transitioned_by="researcher",
                    reason=f"Gate {stage.value}.",
                    evidence_references=("review:ok",),
                )

            f = factor()
            exp = experiment(f)
            plan = validation(exp)
            bt = backtest(exp, plan, suffix="n")
            selected = variant(exp, bt)
            changed = ResearchRunManifestBuilder().build(
                experiment=exp,
                factor=f,
                dataset_fingerprints=("dataset:prices-v2",),
                code_revision="git:def456",
                evidence_references=("run:new",),
                validation_plan=plan,
                backtest=bt,
                performance=performance(bt),
                multiple_testing=audit(exp, selected),
                selected_variant=selected,
                shadow_permit=shadow(exp, suffix="y"),
            )
            self.assertEqual(changed.model_id, final.model_id)
            self.assertNotEqual(changed.manifest_id, final.manifest_id)
            reset = registry.register(
                manifest=changed,
                registered_at=AT,
                registered_by="researcher",
            )
            self.assertEqual(reset.stage, ModelLifecycleStage.RESEARCH)
            history = registry.history(final.model_id)
            self.assertEqual(
                history[-1].reason,
                "MANIFEST_CHANGED_RESET",
            )
            registry.close()


if __name__ == "__main__":
    unittest.main()
