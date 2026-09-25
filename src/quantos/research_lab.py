from __future__ import annotations

from .backtest_economics import (
    BacktestEngine,
    BacktestPolicy,
    BacktestResult,
    RebalancePeriod,
)
from .covariance import (
    CovarianceArtifact,
    CovarianceEstimationPolicy,
    SkfolioCovarianceEstimator,
)
from .factor_contracts import (
    FactorEngine,
    FactorRun,
    FactorSpecification,
    FeatureObservation,
)
from .model_registry import (
    ResearchRunManifest,
    ResearchRunManifestBuilder,
)
from .overfitting_diagnostics import (
    MultipleTestingAudit,
    MultipleTestingEngine,
    MultipleTestingPolicy,
    VariantReturnSeries,
)
from .performance_analytics import (
    PerformanceAnalysis,
    PerformanceAnalyticsEngine,
    PerformancePolicy,
)
from .portfolio_comparison import (
    PortfolioComparisonDossier,
    PortfolioComparisonEngine,
    PortfolioComparisonFold,
    PortfolioComparisonPolicy,
)
from .portfolio_construction import (
    BaselineAllocator,
    PortfolioConstraintPolicy,
    PortfolioDataset,
    PortfolioDatasetBuilder,
    PortfolioReturnObservation,
    PortfolioSolution,
    PortfolioWeight,
    SkfolioBaselineAllocator,
)
from .portfolio_optimization import (
    MinimumVariancePolicy,
    OptimizedPortfolioSolution,
    SkfolioMinimumVarianceOptimizer,
)
from .prospective_review import (
    ProspectiveBehaviorExpectation,
    ProspectiveComparison,
    ProspectiveReviewEngine,
    ResearchRevisionSeed,
)
from .readiness import ProspectiveShadowPermit
from .research_case import ResearchCase
from .research_universe import (
    CorporateActionObservation,
    InvestableUniverse,
    ListingObservation,
    MarketEligibilityObservation,
    UniverseBuilder,
    UniversePolicy,
)
from .validation import (
    ResearchExperimentSpecification,
    ValidationSample,
    WalkForwardPolicy,
    WalkForwardValidationEngine,
    WalkForwardValidationPlan,
)
from .calibration import ForecastRecord, OutcomeRecord
from .paper_monitoring import PaperPortfolioObservation, PaperPostmortem


class ResearchLabService:
    """Single application surface over the immutable Research Lab engines."""

    def __init__(self) -> None:
        self._universes = UniverseBuilder()
        self._factors = FactorEngine()
        self._validation = WalkForwardValidationEngine()
        self._backtests = BacktestEngine()
        self._performance = PerformanceAnalyticsEngine()
        self._overfitting = MultipleTestingEngine()
        self._manifests = ResearchRunManifestBuilder()
        self._prospective = ProspectiveReviewEngine()
        self._portfolio_datasets = PortfolioDatasetBuilder()
        self._baseline_allocator = SkfolioBaselineAllocator()
        self._covariance = SkfolioCovarianceEstimator()
        self._minimum_variance = SkfolioMinimumVarianceOptimizer()
        self._portfolio_comparison = PortfolioComparisonEngine()

    def build_universe(
        self,
        *,
        as_of,
        listings: tuple[ListingObservation, ...],
        market: tuple[MarketEligibilityObservation, ...],
        corporate_actions: tuple[CorporateActionObservation, ...],
        policy: UniversePolicy,
    ) -> InvestableUniverse:
        return self._universes.build(
            as_of=as_of,
            listings=listings,
            market=market,
            corporate_actions=corporate_actions,
            policy=policy,
        )

    def score_factor(
        self,
        *,
        specification: FactorSpecification,
        universe: InvestableUniverse,
        decision_time,
        features: tuple[FeatureObservation, ...],
    ) -> FactorRun:
        return self._factors.run(
            specification=specification,
            universe=universe,
            decision_time=decision_time,
            features=features,
        )

    def build_validation(
        self,
        *,
        experiment: ResearchExperimentSpecification,
        policy: WalkForwardPolicy,
        samples: tuple[ValidationSample, ...],
    ) -> WalkForwardValidationPlan:
        return self._validation.build(
            experiment=experiment,
            policy=policy,
            samples=samples,
        )

    def run_backtest(
        self,
        *,
        factor_id: str,
        validation_plan_id: str,
        policy: BacktestPolicy,
        periods: tuple[RebalancePeriod, ...],
    ) -> BacktestResult:
        return self._backtests.run(
            factor_id=factor_id,
            validation_plan_id=validation_plan_id,
            policy=policy,
            periods=periods,
        )

    def analyze_performance(
        self,
        *,
        backtest: BacktestResult,
        policy: PerformancePolicy,
        source_periods: tuple[RebalancePeriod, ...] = (),
    ) -> PerformanceAnalysis:
        return self._performance.analyze(
            backtest=backtest,
            policy=policy,
            source_periods=source_periods,
        )

    def audit_multiple_testing(
        self,
        *,
        experiment: ResearchExperimentSpecification,
        variants: tuple[VariantReturnSeries, ...],
        selected_variant_id: str,
        policy: MultipleTestingPolicy,
    ) -> MultipleTestingAudit:
        return self._overfitting.audit(
            experiment=experiment,
            variants=variants,
            selected_variant_id=selected_variant_id,
            policy=policy,
        )

    def build_manifest(
        self,
        *,
        experiment: ResearchExperimentSpecification,
        factor: FactorSpecification,
        dataset_fingerprints: tuple[str, ...],
        code_revision: str,
        evidence_references: tuple[str, ...],
        validation_plan: WalkForwardValidationPlan | None = None,
        backtest: BacktestResult | None = None,
        performance: PerformanceAnalysis | None = None,
        multiple_testing: MultipleTestingAudit | None = None,
        selected_variant: VariantReturnSeries | None = None,
        shadow_permit: ProspectiveShadowPermit | None = None,
    ) -> ResearchRunManifest:
        return self._manifests.build(
            experiment=experiment,
            factor=factor,
            dataset_fingerprints=dataset_fingerprints,
            code_revision=code_revision,
            evidence_references=evidence_references,
            validation_plan=validation_plan,
            backtest=backtest,
            performance=performance,
            multiple_testing=multiple_testing,
            selected_variant=selected_variant,
            shadow_permit=shadow_permit,
        )

    def build_portfolio_dataset(
        self,
        *,
        manifest: ResearchRunManifest,
        decision_time,
        observations: tuple[PortfolioReturnObservation, ...],
        minimum_periods: int,
    ) -> PortfolioDataset:
        return self._portfolio_datasets.build(
            manifest=manifest,
            decision_time=decision_time,
            observations=observations,
            minimum_periods=minimum_periods,
        )

    def allocate_baseline_portfolio(
        self,
        *,
        dataset: PortfolioDataset,
        allocator: BaselineAllocator,
        constraints: PortfolioConstraintPolicy,
        previous_weights: tuple[PortfolioWeight, ...] = (),
    ) -> PortfolioSolution:
        return self._baseline_allocator.allocate(
            dataset=dataset,
            allocator=allocator,
            constraints=constraints,
            previous_weights=previous_weights,
        )

    def estimate_covariance(
        self,
        *,
        dataset: PortfolioDataset,
        policy: CovarianceEstimationPolicy,
    ) -> CovarianceArtifact:
        return self._covariance.estimate(
            dataset=dataset,
            policy=policy,
        )

    def optimize_minimum_variance(
        self,
        *,
        dataset: PortfolioDataset,
        covariance: CovarianceArtifact,
        constraints: PortfolioConstraintPolicy,
        policy: MinimumVariancePolicy,
        baselines: tuple[PortfolioSolution, ...],
        previous_weights: tuple[PortfolioWeight, ...] = (),
    ) -> OptimizedPortfolioSolution:
        return self._minimum_variance.optimize(
            dataset=dataset,
            covariance=covariance,
            constraints=constraints,
            policy=policy,
            baselines=baselines,
            previous_weights=previous_weights,
        )

    def compare_portfolio_methods(
        self,
        *,
        folds: tuple[PortfolioComparisonFold, ...],
        policy: PortfolioComparisonPolicy,
    ) -> PortfolioComparisonDossier:
        return self._portfolio_comparison.evaluate(
            folds=folds,
            policy=policy,
        )

    def freeze_expectation(
        self,
        *,
        manifest: ResearchRunManifest,
        permit: ProspectiveShadowPermit,
        forecast: ForecastRecord,
        cumulative_net_return_lower,
        cumulative_net_return_upper,
        market_relative_return_lower,
        market_relative_return_upper,
        maximum_average_implementation_cost_rate,
        maximum_average_one_way_turnover,
        rationale: str,
        evidence_references: tuple[str, ...],
    ) -> ProspectiveBehaviorExpectation:
        return self._prospective.freeze_expectation(
            manifest=manifest,
            permit=permit,
            forecast=forecast,
            cumulative_net_return_lower=cumulative_net_return_lower,
            cumulative_net_return_upper=cumulative_net_return_upper,
            market_relative_return_lower=market_relative_return_lower,
            market_relative_return_upper=market_relative_return_upper,
            maximum_average_implementation_cost_rate=(
                maximum_average_implementation_cost_rate
            ),
            maximum_average_one_way_turnover=(
                maximum_average_one_way_turnover
            ),
            rationale=rationale,
            evidence_references=evidence_references,
        )

    def compare_prospective(
        self,
        *,
        manifest: ResearchRunManifest,
        expectation: ProspectiveBehaviorExpectation,
        forecast: ForecastRecord,
        outcome: OutcomeRecord,
        observations: tuple[PaperPortfolioObservation, ...],
        postmortem: PaperPostmortem,
        reviewed_at,
        reviewer: str,
        lessons: tuple[str, ...],
        evidence_references: tuple[str, ...],
    ) -> ProspectiveComparison:
        return self._prospective.compare(
            manifest=manifest,
            expectation=expectation,
            forecast=forecast,
            outcome=outcome,
            observations=observations,
            postmortem=postmortem,
            reviewed_at=reviewed_at,
            reviewer=reviewer,
            lessons=lessons,
            evidence_references=evidence_references,
        )

    def make_revision_seed(
        self,
        *,
        prior_case: ResearchCase,
        comparison: ProspectiveComparison,
        created_at,
        author: str,
        evidence_references: tuple[str, ...],
    ) -> ResearchRevisionSeed:
        return self._prospective.make_revision_seed(
            prior_case=prior_case,
            comparison=comparison,
            created_at=created_at,
            author=author,
            evidence_references=evidence_references,
        )