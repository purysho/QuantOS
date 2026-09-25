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
from quantos.portfolio_comparison import PortfolioMethod
from quantos.portfolio_construction import (
    BaselineAllocator,
    PortfolioSolution,
    PortfolioWeight,
    portfolio_solution_identity,
)
from quantos.portfolio_paper_authorization import (
    MANDATORY_KILL_CONDITIONS,
    PortfolioPaperAuthorization,
    PortfolioPaperExecutionAssumptions,
    PortfolioPaperKillCondition,
    PortfolioPaperMonitoringPolicy,
    portfolio_paper_authorization_identity,
)
from quantos.portfolio_paper_monitoring import (
    PortfolioPaperAuthorizationState,
    PortfolioPaperShadowLedger,
)

UTC = timezone.utc
AT = datetime(2026, 9, 25, 12, tzinfo=UTC)
MODEL_ID = "research-model:" + "m" * 64
MANIFEST_ID = "research-run-manifest:" + "r" * 64
CASE_ID = "research-case:" + "c" * 64
PERMIT_ID = "shadow-permit:" + "p" * 64
CONSTRAINT_ID = "portfolio-constraint-policy:" + "q" * 64
DATASET_ID = "portfolio-dataset:" + "d" * 64


def manifest():
    return ResearchRunManifest(
        manifest_id=MANIFEST_ID,
        model_id=MODEL_ID,
        experiment_id="research-experiment:" + "e" * 64,
        research_case_id=CASE_ID,
        case_dossier_fingerprint="case-dossier:" + "f" * 64,
        factor_id="factor-spec:" + "g" * 64,
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
        code_revision="git:stage10.9",
        evidence_references=("manifest:evidence",),
        eligible_stage=ModelLifecycleStage.PAPER,
    )


def registry(stage=ModelLifecycleStage.PAPER):
    return ModelRegistryState(
        model_id=MODEL_ID,
        manifest_id=MANIFEST_ID,
        stage=stage,
        updated_at=AT - timedelta(minutes=30),
        updated_by="registry-reviewer",
    )


def solution():
    draft = PortfolioSolution(
        solution_id="placeholder",
        model_id=MODEL_ID,
        manifest_id=MANIFEST_ID,
        dataset_id=DATASET_ID,
        decision_time=AT - timedelta(hours=1),
        allocator=BaselineAllocator.EQUAL_WEIGHT,
        engine_name="skfolio",
        engine_version="1.x",
        constraint_policy_id=CONSTRAINT_ID,
        weights=(
            PortfolioWeight("SEC:A", Decimal("0.5")),
            PortfolioWeight("SEC:B", Decimal("0.5")),
        ),
        net_exposure=Decimal("1"),
        gross_exposure=Decimal("1"),
        one_way_turnover=Decimal("1"),
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
        rationale="Frozen shadow cost assumptions.",
        evidence_references=("execution:evidence",),
    )


def monitoring(**overrides):
    values = {
        "minimum_observations": 3,
        "maximum_drawdown_abs": Decimal("0.15"),
        "maximum_one_way_turnover": Decimal("1"),
        "maximum_average_implementation_cost_rate": Decimal("0.001"),
        "maximum_absolute_weight": Decimal("0.70"),
        "maximum_gross_exposure": Decimal("1"),
        "maximum_solution_drift_turnover": Decimal("0.25"),
        "kill_conditions": tuple(
            sorted(
                MANDATORY_KILL_CONDITIONS,
                key=lambda item: item.value,
            )
        ),
        "rationale": "Stage 10.9 enforcement thresholds.",
        "evidence_references": ("monitoring:evidence",),
    }
    values.update(overrides)
    return PortfolioPaperMonitoringPolicy(**values)


def authorization(
    *,
    monitoring_policy=None,
    execution_assumptions=None,
    expires_at=None,
):
    monitor = monitoring_policy or monitoring()
    costs = execution_assumptions or execution()
    chosen = solution()
    draft = PortfolioPaperAuthorization(
        authorization_id="placeholder",
        model_id=MODEL_ID,
        manifest_id=MANIFEST_ID,
        research_case_id=CASE_ID,
        decision_id="portfolio-research-decision:" + "x" * 64,
        comparison_dossier_id="portfolio-comparison-dossier:" + "y" * 64,
        robustness_dossier_id="portfolio-robustness-dossier:" + "z" * 64,
        permit_id=PERMIT_ID,
        selected_method=PortfolioMethod.EQUAL_WEIGHT,
        selected_solution_id=chosen.solution_id,
        selected_dataset_id=chosen.dataset_id,
        constraint_policy_id=CONSTRAINT_ID,
        execution_assumptions_id=costs.assumptions_id,
        monitoring_policy_id=monitor.policy_id,
        authorized_at=AT,
        expires_at=expires_at or AT + timedelta(days=30),
        portfolio_reviewer="portfolio-reviewer",
        independent_risk_reviewer="risk-reviewer",
        rationale="Shadow-only prospective authorization.",
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


def healthy_record(
    ledger,
    *,
    auth=None,
    monitor=None,
    costs=None,
    state=None,
    start=None,
    gross=Decimal("0.01"),
    cost=Decimal("0.0003"),
    turnover=Decimal("0.25"),
    weights=None,
    source_fact_ids=("price:A", "price:B"),
):
    monitor = monitor or monitoring()
    costs = costs or execution()
    auth = auth or authorization(
        monitoring_policy=monitor,
        execution_assumptions=costs,
    )
    state = state or registry()
    start = start or AT + timedelta(days=1)
    weights = weights or solution().weights
    return ledger.record(
        authorization=auth,
        manifest=manifest(),
        registry_state=state,
        selected_solution=solution(),
        execution_assumptions=costs,
        monitoring_policy=monitor,
        period_start=start,
        period_end=start + timedelta(days=1),
        observed_at=start + timedelta(days=1, minutes=1),
        gross_return=gross,
        net_return=gross - cost,
        implementation_cost_rate=cost,
        one_way_turnover=turnover,
        weights=weights,
        source_fact_ids=source_fact_ids,
        evidence_references=("observation:evidence",),
    )


class PortfolioPaperShadowLedgerTests(unittest.TestCase):
    def test_healthy_observation_remains_active_and_measures_cost_variance(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = PortfolioPaperShadowLedger(
                Path(tmp) / "portfolio-paper-shadow.duckdb"
            )
            item = healthy_record(ledger)
            self.assertEqual(
                item.authorization_state_after_record,
                PortfolioPaperAuthorizationState.ACTIVE,
            )
            self.assertEqual(
                item.expected_implementation_cost_rate,
                Decimal("0.00030"),
            )
            self.assertEqual(
                item.implementation_cost_assumption_variance,
                Decimal("0.00000"),
            )
            self.assertEqual(
                ledger.state(item.authorization_id),
                PortfolioPaperAuthorizationState.ACTIVE,
            )
            self.assertEqual(item.order_authority, "NONE")
            self.assertEqual(item.capital_authority, "NONE")
            ledger.close()

    def test_turnover_breach_suspends_and_blocks_later_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = PortfolioPaperShadowLedger(
                Path(tmp) / "portfolio-paper-shadow.duckdb"
            )
            monitor = monitoring()
            auth = authorization(monitoring_policy=monitor)
            first = healthy_record(
                ledger,
                auth=auth,
                monitor=monitor,
                turnover=Decimal("1.10"),
                cost=Decimal("0.0003"),
            )
            self.assertEqual(
                first.authorization_state_after_record,
                PortfolioPaperAuthorizationState.SUSPENDED,
            )
            self.assertIn(
                PortfolioPaperKillCondition.TURNOVER_BREACH,
                first.triggered_kill_conditions,
            )
            self.assertEqual(
                ledger.state(auth.authorization_id),
                PortfolioPaperAuthorizationState.SUSPENDED,
            )
            with self.assertRaises(ValueError):
                healthy_record(
                    ledger,
                    auth=auth,
                    monitor=monitor,
                    start=AT + timedelta(days=3),
                )
            ledger.close()

    def test_drawdown_breach_suspends(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = PortfolioPaperShadowLedger(
                Path(tmp) / "portfolio-paper-shadow.duckdb"
            )
            item = healthy_record(
                ledger,
                gross=Decimal("-0.20"),
                cost=Decimal("0"),
            )
            self.assertIn(
                PortfolioPaperKillCondition.MAX_DRAWDOWN_BREACH,
                item.triggered_kill_conditions,
            )
            self.assertEqual(
                item.authorization_state_after_record,
                PortfolioPaperAuthorizationState.SUSPENDED,
            )
            ledger.close()

    def test_implementation_cost_breach_suspends_and_records_variance(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = PortfolioPaperShadowLedger(
                Path(tmp) / "portfolio-paper-shadow.duckdb"
            )
            item = healthy_record(
                ledger,
                cost=Decimal("0.002"),
            )
            self.assertIn(
                PortfolioPaperKillCondition.IMPLEMENTATION_COST_BREACH,
                item.triggered_kill_conditions,
            )
            self.assertGreater(
                item.implementation_cost_assumption_variance,
                Decimal("0"),
            )
            ledger.close()

    def test_missing_lineage_terminates_without_recording_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = PortfolioPaperShadowLedger(
                Path(tmp) / "portfolio-paper-shadow.duckdb"
            )
            auth = authorization()
            with self.assertRaises(ValueError):
                healthy_record(
                    ledger,
                    auth=auth,
                    source_fact_ids=(),
                )
            self.assertEqual(
                ledger.state(auth.authorization_id),
                PortfolioPaperAuthorizationState.TERMINATED,
            )
            events = ledger.events(auth.authorization_id)
            self.assertEqual(len(events), 1)
            self.assertIn(
                PortfolioPaperKillCondition.DATA_LINEAGE_BREAK,
                events[0].kill_conditions,
            )
            self.assertEqual(ledger.observations(auth.authorization_id), ())
            ledger.close()

    def test_registry_change_terminates_authorization(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = PortfolioPaperShadowLedger(
                Path(tmp) / "portfolio-paper-shadow.duckdb"
            )
            auth = authorization()
            with self.assertRaises(ValueError):
                healthy_record(
                    ledger,
                    auth=auth,
                    state=registry(ModelLifecycleStage.BACKTESTED),
                )
            self.assertEqual(
                ledger.state(auth.authorization_id),
                PortfolioPaperAuthorizationState.TERMINATED,
            )
            self.assertIn(
                PortfolioPaperKillCondition.MODEL_OR_MANIFEST_CHANGE,
                ledger.events(auth.authorization_id)[0].kill_conditions,
            )
            ledger.close()

    def test_expired_authorization_is_persisted_and_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = PortfolioPaperShadowLedger(
                Path(tmp) / "portfolio-paper-shadow.duckdb"
            )
            auth = authorization(
                expires_at=AT + timedelta(hours=12)
            )
            with self.assertRaises(ValueError):
                healthy_record(
                    ledger,
                    auth=auth,
                    start=AT + timedelta(days=1),
                )
            self.assertEqual(
                ledger.state(auth.authorization_id),
                PortfolioPaperAuthorizationState.EXPIRED,
            )
            self.assertEqual(
                ledger.events(auth.authorization_id)[0].kill_conditions,
                (),
            )
            ledger.close()

    def test_solution_drift_and_constraint_breach_are_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = PortfolioPaperShadowLedger(
                Path(tmp) / "portfolio-paper-shadow.duckdb"
            )
            item = healthy_record(
                ledger,
                weights=(
                    PortfolioWeight("SEC:A", Decimal("0.80")),
                    PortfolioWeight("SEC:B", Decimal("0.20")),
                ),
            )
            self.assertIn(
                PortfolioPaperKillCondition.SOLUTION_DRIFT_BREACH,
                item.triggered_kill_conditions,
            )
            self.assertIn(
                PortfolioPaperKillCondition.PORTFOLIO_CONSTRAINT_BREACH,
                item.triggered_kill_conditions,
            )
            ledger.close()

    def test_overlapping_periods_fail_without_corrupting_active_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = PortfolioPaperShadowLedger(
                Path(tmp) / "portfolio-paper-shadow.duckdb"
            )
            auth = authorization()
            healthy_record(ledger, auth=auth)
            with self.assertRaises(ValueError):
                healthy_record(
                    ledger,
                    auth=auth,
                    start=AT + timedelta(days=1, hours=12),
                )
            self.assertEqual(
                ledger.state(auth.authorization_id),
                PortfolioPaperAuthorizationState.ACTIVE,
            )
            self.assertEqual(len(ledger.observations(auth.authorization_id)), 1)
            ledger.close()


    def test_clean_completion_closes_and_blocks_future_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = PortfolioPaperShadowLedger(
                Path(tmp) / "portfolio-paper-shadow.duckdb"
            )
            auth = authorization()
            healthy_record(ledger, auth=auth)
            event = ledger.complete(
                authorization=auth,
                completed_at=AT + timedelta(days=3),
                reviewer="completion-reviewer",
                reason="Planned shadow horizon completed.",
                evidence_references=("completion:evidence",),
            )
            self.assertEqual(
                event.new_state,
                PortfolioPaperAuthorizationState.CLOSED,
            )
            self.assertEqual(
                ledger.state(auth.authorization_id),
                PortfolioPaperAuthorizationState.CLOSED,
            )
            with self.assertRaises(ValueError):
                healthy_record(
                    ledger,
                    auth=auth,
                    start=AT + timedelta(days=4),
                )
            ledger.close()


if __name__ == "__main__":
    unittest.main()