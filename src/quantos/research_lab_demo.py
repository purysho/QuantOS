from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from .backtest_economics import (
    BacktestPolicy,
    BenchmarkKind,
    BenchmarkPeriodReturn,
    CostModel,
    PortfolioConstructionPolicy,
    PortfolioMode,
    RebalancePeriod,
    SecurityPeriodReturn,
)
from .calibration import (
    ForecastRecord,
    OutcomeRecord,
    ScenarioProbability,
)
from .factor_contracts import (
    CrossSectionTransform,
    FactorComponent,
    FactorSpecification,
    FeatureObservation,
)
from .model_registry import (
    ModelLifecycleStage,
    ModelRegistry,
)
from .overfitting_diagnostics import (
    MultipleTestingPolicy,
    VariantReturnSeries,
)
from .paper_monitoring import (
    MonitoringConditionCheck,
    PaperBenchmarkObservation,
    PaperHealthState,
    PaperMonitoringLedger,
    PaperMonitoringPolicy,
    PostmortemReason,
)
from .performance_analytics import PerformancePolicy
from .prospective_review import ProspectiveReviewStore
from .readiness import ProspectiveShadowPermit
from .research_case import ResearchCase, ResearchCaseType
from .research_lab import ResearchLabService
from .research_universe import (
    AssetClass,
    ListingObservation,
    ListingStatus,
    MarketEligibilityObservation,
    SecurityType,
    UniversePolicy,
)
from .validation import (
    ResearchExperimentSpecification,
    ValidationSample,
    WalkForwardPolicy,
)


UTC = timezone.utc
BASE = datetime(2024, 1, 2, 16, tzinfo=UTC)


@dataclass(frozen=True)
class GoldenResearchLabResult:
    universe_id: str
    factor_id: str
    validation_plan_id: str
    backtest_id: str
    performance_analysis_id: str
    multiple_testing_audit_id: str
    manifest_id: str
    registry_stage: str
    paper_health_before_close: str
    postmortem_id: str
    comparison_id: str
    revision_seed_id: str
    final_paper_health: str
    capital_authority: str


def run_golden_research_lab(root: Path) -> GoldenResearchLabResult:
    root.mkdir(parents=True, exist_ok=True)
    lab = ResearchLabService()
    case_id = "research-case:" + "c" * 64

    universe_policy = UniversePolicy(
        allowed_asset_classes=(AssetClass.EQUITY,),
        allowed_security_types=(SecurityType.COMMON_STOCK,),
        allowed_exchanges=("XNAS",),
        allowed_countries=("US",),
        allowed_currencies=("USD",),
        primary_listing_only=True,
        minimum_price=Decimal("5"),
        minimum_average_daily_dollar_volume=Decimal("1000000"),
        minimum_market_cap=Decimal("100000000"),
        minimum_trading_days_history=200,
        maximum_market_age_days=3,
        exclude_suspended=True,
        exclude_pending_actions=(),
        rationale="Synthetic liquid US-equity universe for golden workflow.",
        evidence_references=("golden:universe-policy",),
    )
    securities = ("SEC:A", "SEC:B", "SEC:C", "SEC:D")
    listings = tuple(
        ListingObservation(
            security_id=security_id,
            issuer_id=f"ISS:{security_id[-1]}",
            listing_id=f"LIST:{security_id[-1]}",
            ticker=security_id[-1],
            exchange="XNAS",
            country="US",
            currency="USD",
            asset_class=AssetClass.EQUITY,
            security_type=SecurityType.COMMON_STOCK,
            is_primary=True,
            effective_from=date(2020, 1, 1),
            effective_to=None,
            status=ListingStatus.ACTIVE,
            knowledge_time=BASE - timedelta(days=5),
            evidence_references=(f"golden:listing:{security_id}",),
        )
        for security_id in securities
    )
    market = tuple(
        MarketEligibilityObservation(
            security_id=security_id,
            market_time=BASE - timedelta(days=1),
            knowledge_time=BASE - timedelta(hours=23),
            close_price=Decimal("100"),
            average_daily_dollar_volume=Decimal("5000000"),
            market_cap=Decimal("1000000000"),
            trading_days_history=1000,
            suspended=False,
            evidence_references=(f"golden:market:{security_id}",),
        )
        for security_id in securities
    )
    universe = lab.build_universe(
        as_of=BASE,
        listings=listings,
        market=market,
        corporate_actions=(),
        policy=universe_policy,
    )

    factor = FactorSpecification(
        name="golden-quality",
        version="1",
        universe_policy_id=universe_policy.policy_id,
        components=(
            FactorComponent(
                feature_name="quality",
                lookback_periods=4,
                minimum_lag_seconds=3600,
                maximum_staleness_seconds=86400 * 10,
                weight=Decimal("1"),
                transform=CrossSectionTransform.PERCENTILE_RANK,
            ),
        ),
        rationale="Synthetic monotonic quality factor.",
        evidence_references=("golden:factor",),
    )

    decision_times = tuple(
        BASE + timedelta(days=2 * index)
        for index in range(6)
    )
    features: list[FeatureObservation] = []
    values = {
        "SEC:A": Decimal("4"),
        "SEC:B": Decimal("3"),
        "SEC:C": Decimal("2"),
        "SEC:D": Decimal("1"),
    }
    for index, decision in enumerate(decision_times):
        feature_end = decision - timedelta(hours=2)
        knowledge = decision - timedelta(hours=1, minutes=30)
        for security_id in securities:
            features.append(
                FeatureObservation(
                    security_id=security_id,
                    feature_name="quality",
                    feature_end_time=feature_end,
                    knowledge_time=knowledge,
                    lookback_periods=4,
                    value=values[security_id] + Decimal(index) / Decimal("100"),
                    source_fact_ids=(f"golden:feature:{index}:{security_id}",),
                    evidence_references=(f"golden:feature-evidence:{index}:{security_id}",),
                )
            )
    factor_runs = tuple(
        lab.score_factor(
            specification=factor,
            universe=universe,
            decision_time=decision,
            features=tuple(features),
        )
        for decision in decision_times
    )

    experiment = ResearchExperimentSpecification(
        name="golden-factor-research",
        version="1",
        factor_id=factor.factor_id,
        benchmark_id="benchmark:market-cap",
        hypothesis_reference="hypothesis:golden-quality",
        variants_tested=2,
        primary_metric="sharpe",
        rationale="Golden end-to-end research workflow.",
        evidence_references=("golden:experiment",),
        research_case_id=case_id,
    )
    validation_samples: list[ValidationSample] = []
    realized_by_security = {
        "SEC:A": Decimal("0.03"),
        "SEC:B": Decimal("0.02"),
        "SEC:C": Decimal("0.00"),
        "SEC:D": Decimal("-0.01"),
    }
    for run in factor_runs:
        for security_id in securities:
            validation_samples.append(
                ValidationSample(
                    factor_id=factor.factor_id,
                    factor_run_id=run.run_id,
                    universe_id=universe.universe_id,
                    security_id=security_id,
                    decision_time=run.decision_time,
                    label_start_time=run.decision_time + timedelta(minutes=1),
                    label_end_time=run.decision_time + timedelta(hours=12),
                    outcome_known_at=run.decision_time + timedelta(hours=13),
                    realized_return=realized_by_security[security_id],
                    source_fact_ids=(f"golden:return:{run.run_id}:{security_id}",),
                    evidence_references=("golden:validation-return",),
                )
            )
    validation_plan = lab.build_validation(
        experiment=experiment,
        policy=WalkForwardPolicy(
            minimum_train_decision_times=2,
            test_decision_times_per_fold=1,
            step_decision_times=1,
            purge_seconds=0,
            embargo_seconds=0,
            minimum_train_samples=8,
            minimum_test_samples=4,
            expanding_window=True,
            rolling_train_decision_times=None,
            rationale="Golden expanding walk-forward plan.",
            evidence_references=("golden:validation-policy",),
        ),
        samples=tuple(validation_samples),
    )

    portfolio_policy = PortfolioConstructionPolicy(
        mode=PortfolioMode.LONG_ONLY,
        long_count=2,
        short_count=0,
        long_gross=Decimal("1"),
        short_gross=Decimal("0"),
        maximum_absolute_weight=Decimal("0.5"),
        allow_boundary_tie_break=False,
        rationale="Golden top-two equal-weight portfolio.",
        evidence_references=("golden:portfolio-policy",),
    )
    cost_model = CostModel(
        commission_bps=Decimal("1"),
        half_spread_bps=Decimal("1"),
        slippage_bps=Decimal("1"),
        market_impact_bps=Decimal("1"),
        annual_borrow_bps=Decimal("0"),
        rationale="Golden explicit implementation costs.",
        evidence_references=("golden:cost-model",),
    )
    backtest_policy = BacktestPolicy(
        portfolio=portfolio_policy,
        costs=cost_model,
        required_benchmarks=(
            BenchmarkKind.CASH,
            BenchmarkKind.MARKET_CAP,
            BenchmarkKind.EQUAL_WEIGHT,
            BenchmarkKind.INVERSE_VOL,
        ),
        rationale="Golden mandatory-baseline policy.",
        evidence_references=("golden:backtest-policy",),
    )
    period_returns = (
        {"SEC:A": "0.04", "SEC:B": "0.02", "SEC:C": "0.00", "SEC:D": "-0.01"},
        {"SEC:A": "-0.02", "SEC:B": "0.01", "SEC:C": "0.02", "SEC:D": "0.00"},
        {"SEC:A": "0.03", "SEC:B": "0.015", "SEC:C": "-0.01", "SEC:D": "0.00"},
        {"SEC:A": "0.01", "SEC:B": "-0.005", "SEC:C": "0.005", "SEC:D": "-0.01"},
    )
    rebalance_periods: list[RebalancePeriod] = []
    for index, returns in enumerate(period_returns):
        run = factor_runs[index + 2]
        start = run.decision_time + timedelta(minutes=1)
        end = start + timedelta(days=1)
        rebalance_periods.append(
            RebalancePeriod(
                factor_run=run,
                holding_start=start,
                holding_end=end,
                security_returns=tuple(
                    SecurityPeriodReturn(
                        security_id=security_id,
                        start_time=start,
                        end_time=end,
                        total_return=Decimal(value),
                        delisted=False,
                        source_fact_ids=(f"golden:security-return:{index}:{security_id}",),
                    )
                    for security_id, value in returns.items()
                ),
                benchmark_returns=tuple(
                    BenchmarkPeriodReturn(
                        kind=kind,
                        benchmark_id=f"benchmark:{kind.value}",
                        start_time=start,
                        end_time=end,
                        total_return=(
                            Decimal("0")
                            if kind is BenchmarkKind.CASH
                            else Decimal("0.005")
                        ),
                        source_fact_ids=(f"golden:benchmark:{index}:{kind.value}",),
                    )
                    for kind in (
                        BenchmarkKind.CASH,
                        BenchmarkKind.MARKET_CAP,
                        BenchmarkKind.EQUAL_WEIGHT,
                        BenchmarkKind.INVERSE_VOL,
                    )
                ),
            )
        )
    backtest = lab.run_backtest(
        factor_id=factor.factor_id,
        validation_plan_id=validation_plan.plan_id,
        policy=backtest_policy,
        periods=tuple(rebalance_periods),
    )
    performance = lab.analyze_performance(
        backtest=backtest,
        policy=PerformancePolicy(
            periods_per_year=252,
            cvar_confidence=Decimal("0.95"),
            minimum_periods_for_risk_metrics=2,
            minimum_ic_cross_section=3,
            rationale="Golden performance diagnostics.",
            evidence_references=("golden:performance-policy",),
        ),
        source_periods=tuple(rebalance_periods),
    )

    selected_variant = VariantReturnSeries(
        experiment_id=experiment.experiment_id,
        variant_id="selected",
        backtest_id=backtest.backtest_id,
        net_period_returns=tuple(
            item.net_return for item in backtest.periods
        ),
        evidence_references=("golden:selected-backtest",),
    )
    alternate_variant = VariantReturnSeries(
        experiment_id=experiment.experiment_id,
        variant_id="alternate",
        backtest_id="economic-backtest:" + "a" * 64,
        net_period_returns=(
            Decimal("0.006"),
            Decimal("-0.004"),
            Decimal("0.008"),
            Decimal("-0.002"),
        ),
        evidence_references=("golden:alternate-backtest",),
    )
    multiple_testing = lab.audit_multiple_testing(
        experiment=experiment,
        variants=(selected_variant, alternate_variant),
        selected_variant_id="selected",
        policy=MultipleTestingPolicy(
            cscv_slices=4,
            require_unique_is_winner=False,
            rationale="Golden complete two-variant search audit.",
            evidence_references=("golden:dsr", "golden:pbo"),
        ),
    )

    permit = ProspectiveShadowPermit(
        permit_id="shadow-permit:" + "p" * 64,
        case_id=case_id,
        case_dossier_fingerprint="case-dossier:" + "d" * 64,
        scenario_set_id="scenario-set:" + "s" * 64,
        issued_at=decision_times[-1] + timedelta(days=2),
        issued_by="golden-review-panel",
        purpose="PROSPECTIVE_SHADOW_ONLY",
    )
    manifest = lab.build_manifest(
        experiment=experiment,
        factor=factor,
        dataset_fingerprints=("dataset:golden-prices", "dataset:golden-features"),
        code_revision="golden:stage-9.10",
        evidence_references=("golden:manifest",),
        validation_plan=validation_plan,
        backtest=backtest,
        performance=performance,
        multiple_testing=multiple_testing,
        selected_variant=selected_variant,
        shadow_permit=permit,
    )

    registry = ModelRegistry(root / "model-registry.duckdb")
    paper = PaperMonitoringLedger(root / "paper-monitoring.duckdb")
    review_store = ProspectiveReviewStore(root / "prospective-review.duckdb")
    try:
        state = registry.register(
            manifest=manifest,
            registered_at=permit.issued_at,
            registered_by="golden-researcher",
        )
        for stage in (
            ModelLifecycleStage.VALIDATED,
            ModelLifecycleStage.BACKTESTED,
            ModelLifecycleStage.PAPER,
        ):
            state = registry.transition(
                manifest=manifest,
                to_stage=stage,
                transitioned_at=permit.issued_at,
                transitioned_by="golden-researcher",
                reason=f"Golden gate {stage.value}.",
                evidence_references=(f"golden:gate:{stage.value}",),
            )

        research_case = ResearchCase(
            case_id=case_id,
            case_type=ResearchCaseType.SYSTEMATIC,
            subject_ids=("universe:golden-us-equities",),
            universe="Synthetic US equities",
            thesis="Quality-ranked securities may outperform the market baseline.",
            mechanism="Synthetic quality ranking.",
            horizon="8 days",
            as_of=permit.issued_at,
            supporting_claim_ids=("claim:golden",),
            limiting_claim_ids=(),
            contradicting_claim_ids=(),
            alternative_explanations=("Synthetic construction",),
            falsifiers=("Prospective underperformance",),
            monitoring_conditions=("quality ranking remains directionally consistent",),
            assumptions=("Synthetic golden fixture only",),
            author="golden-researcher",
            created_at=permit.issued_at,
            supersedes_case_id=None,
        )
        forecast = ForecastRecord(
            forecast_id="forecast:" + "f" * 64,
            permit_id=permit.permit_id,
            case_id=case_id,
            case_dossier_fingerprint=permit.case_dossier_fingerprint,
            scenario_set_id=permit.scenario_set_id,
            forecaster="golden-researcher",
            forecast_at=permit.issued_at + timedelta(hours=1),
            horizon_end=permit.issued_at + timedelta(days=8),
            probabilities=(
                ScenarioProbability("bear", "Bear", 0.2),
                ScenarioProbability("base", "Base", 0.5),
                ScenarioProbability("bull", "Bull", 0.3),
            ),
        )
        expectation = lab.freeze_expectation(
            manifest=manifest,
            permit=permit,
            forecast=forecast,
            cumulative_net_return_lower=Decimal("-0.10"),
            cumulative_net_return_upper=Decimal("0.10"),
            market_relative_return_lower=Decimal("-0.10"),
            market_relative_return_upper=Decimal("0.10"),
            maximum_average_implementation_cost_rate=Decimal("0.01"),
            maximum_average_one_way_turnover=Decimal("0.50"),
            rationale="Golden prospective behavior envelope.",
            evidence_references=("golden:expectation",),
        )
        review_store.add_expectation(expectation)

        paper_policy = PaperMonitoringPolicy(
            minimum_observations=2,
            maximum_drawdown_abs=Decimal("0.20"),
            maximum_average_one_way_turnover=Decimal("0.50"),
            maximum_average_implementation_cost_rate=Decimal("0.01"),
            minimum_market_cap_relative_wealth_return=Decimal("-0.20"),
            rationale="Golden PAPER monitoring.",
            evidence_references=("golden:paper-policy",),
        )
        for index, gross in enumerate((Decimal("0.02"), Decimal("0.01"))):
            start = forecast.forecast_at + timedelta(days=1 + index * 2)
            paper.record(
                manifest=manifest,
                registry_state=state,
                permit=permit,
                research_case=research_case,
                period_start=start,
                period_end=start + timedelta(days=1),
                observed_at=start + timedelta(days=1, minutes=1),
                gross_return=gross,
                net_return=gross - Decimal("0.001"),
                transaction_cost_rate=Decimal("0.001"),
                borrow_cost_rate=Decimal("0"),
                one_way_turnover_ratio=Decimal("0.20"),
                benchmarks=tuple(
                    PaperBenchmarkObservation(
                        kind=kind,
                        total_return=(
                            Decimal("0")
                            if kind is BenchmarkKind.CASH
                            else Decimal("0.005")
                        ),
                        source_fact_ids=(f"golden:paper-benchmark:{index}:{kind.value}",),
                    )
                    for kind in (
                        BenchmarkKind.CASH,
                        BenchmarkKind.MARKET_CAP,
                        BenchmarkKind.EQUAL_WEIGHT,
                        BenchmarkKind.INVERSE_VOL,
                    )
                ),
                condition_checks=(
                    MonitoringConditionCheck(
                        condition=research_case.monitoring_conditions[0],
                        breached=False,
                        evidence_references=(f"golden:monitor:{index}",),
                    ),
                ),
                evidence_references=(f"golden:paper-observation:{index}",),
            )
        pre_close_health = paper.health(
            manifest=manifest,
            registry_state=state,
            policy=paper_policy,
        )
        postmortem = paper.close_with_postmortem(
            manifest=manifest,
            registry_state=state,
            policy=paper_policy,
            closed_at=forecast.horizon_end,
            reviewer="golden-independent-reviewer",
            reason=PostmortemReason.COMPLETED_EVALUATION,
            notes="Golden prospective PAPER horizon completed.",
            evidence_references=("golden:postmortem",),
        )
        final_health = paper.health(
            manifest=manifest,
            registry_state=state,
            policy=paper_policy,
        )

        outcome = OutcomeRecord(
            outcome_id="forecast-outcome:" + "o" * 64,
            forecast_id=forecast.forecast_id,
            realized_scenario_id="base",
            observed_at=forecast.horizon_end + timedelta(hours=1),
            adjudicator="golden-outcome-reviewer",
            notes="Golden base scenario realized.",
            evidence_references=("golden:outcome",),
        )
        observations = paper.observations(manifest.manifest_id)
        comparison = lab.compare_prospective(
            manifest=manifest,
            expectation=expectation,
            forecast=forecast,
            outcome=outcome,
            observations=observations,
            postmortem=postmortem,
            reviewed_at=forecast.horizon_end + timedelta(hours=2),
            reviewer="golden-reviewer",
            lessons=("Preserve point-in-time lineage and explicit costs in the next case.",),
            evidence_references=("golden:comparison",),
        )
        review_store.add_comparison(comparison)
        seed = lab.make_revision_seed(
            prior_case=research_case,
            comparison=comparison,
            created_at=forecast.horizon_end + timedelta(hours=3),
            author="golden-researcher",
            evidence_references=("golden:revision-seed",),
        )
        review_store.add_revision_seed(seed)

        return GoldenResearchLabResult(
            universe_id=universe.universe_id,
            factor_id=factor.factor_id,
            validation_plan_id=validation_plan.plan_id,
            backtest_id=backtest.backtest_id,
            performance_analysis_id=performance.analysis_id,
            multiple_testing_audit_id=multiple_testing.audit_id,
            manifest_id=manifest.manifest_id,
            registry_stage=state.stage.value,
            paper_health_before_close=pre_close_health.state.value,
            postmortem_id=postmortem.postmortem_id,
            comparison_id=comparison.comparison_id,
            revision_seed_id=seed.seed_id,
            final_paper_health=final_health.state.value,
            capital_authority="NONE",
        )
    finally:
        review_store.close()
        paper.close()
        registry.close()
