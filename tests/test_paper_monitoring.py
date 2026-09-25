import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from quantos.backtest_economics import BenchmarkKind
from quantos.model_registry import (
    ModelLifecycleStage,
    ModelRegistryState,
    ResearchRunManifest,
)
from quantos.paper_monitoring import (
    MonitoringConditionCheck,
    PaperBenchmarkObservation,
    PaperHealthState,
    PaperMonitoringLedger,
    PaperMonitoringPolicy,
    PostmortemReason,
)
from quantos.readiness import ProspectiveShadowPermit
from quantos.research_case import ResearchCase, ResearchCaseType

UTC = timezone.utc
AT = datetime(2026, 9, 25, 12, tzinfo=UTC)
CASE_ID = "research-case:" + "c" * 64
PERMIT_ID = "shadow-permit:" + "p" * 64
DOSSIER = "case-dossier:" + "d" * 64
MANIFEST_ID = "research-run-manifest:" + "m" * 64
MODEL_ID = "research-model:" + "r" * 64


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
        backtest_policy_id="backtest-policy:" + "q" * 64,
        performance_analysis_id="performance-analysis:" + "a" * 64,
        performance_policy_id="performance-policy:" + "x" * 64,
        multiple_testing_audit_id="multiple-testing-audit:" + "t" * 64,
        overfitting_policy_id="multiple-testing-policy:" + "o" * 64,
        selected_variant_id="selected",
        selected_variant_series_id="variant-return-series:" + "s" * 64,
        shadow_permit_id=PERMIT_ID,
        benchmark_kinds=(
            "CASH",
            "EQUAL_WEIGHT",
            "INVERSE_VOL",
            "MARKET_CAP",
        ),
        dataset_fingerprints=("dataset:prices",),
        code_revision="git:abc",
        evidence_references=("run:evidence",),
        eligible_stage=ModelLifecycleStage.PAPER,
    )


def registry_state(stage=ModelLifecycleStage.PAPER, manifest_id=MANIFEST_ID):
    return ModelRegistryState(
        model_id=MODEL_ID,
        manifest_id=manifest_id,
        stage=stage,
        updated_at=AT,
        updated_by="researcher",
    )


def permit(issued_at=AT):
    return ProspectiveShadowPermit(
        permit_id=PERMIT_ID,
        case_id=CASE_ID,
        case_dossier_fingerprint=DOSSIER,
        scenario_set_id="scenario-set:" + "s" * 64,
        issued_at=issued_at,
        issued_by="reviewer",
        purpose="PROSPECTIVE_SHADOW_ONLY",
    )


def research_case():
    return ResearchCase(
        case_id=CASE_ID,
        case_type=ResearchCaseType.SYSTEMATIC,
        subject_ids=("universe:us-equities",),
        universe="US equities",
        thesis="Frozen systematic hypothesis.",
        mechanism="Quality and value may carry a return premium.",
        horizon="12 months",
        as_of=AT - timedelta(days=1),
        supporting_claim_ids=("claim:1",),
        limiting_claim_ids=(),
        contradicting_claim_ids=(),
        alternative_explanations=("Risk compensation",),
        falsifiers=("Prospective signal degradation",),
        monitoring_conditions=(
            "factor IC remains directionally consistent",
            "implementation costs remain within plan",
        ),
        assumptions=(),
        author="researcher",
        created_at=AT - timedelta(hours=1),
        supersedes_case_id=None,
    )


def policy(**overrides):
    values = {
        "minimum_observations": 2,
        "maximum_drawdown_abs": Decimal("0.20"),
        "maximum_average_one_way_turnover": Decimal("0.50"),
        "maximum_average_implementation_cost_rate": Decimal("0.01"),
        "minimum_market_cap_relative_wealth_return": Decimal("-0.20"),
        "rationale": "Prospective shadow monitoring thresholds.",
        "evidence_references": ("policy:paper",),
    }
    values.update(overrides)
    return PaperMonitoringPolicy(**values)


def benchmarks(value="0.005"):
    return tuple(
        PaperBenchmarkObservation(
            kind=kind,
            total_return=(
                Decimal("0")
                if kind is BenchmarkKind.CASH
                else Decimal(value)
            ),
            source_fact_ids=(f"benchmark:{kind.value}",),
        )
        for kind in (
            BenchmarkKind.CASH,
            BenchmarkKind.MARKET_CAP,
            BenchmarkKind.EQUAL_WEIGHT,
            BenchmarkKind.INVERSE_VOL,
        )
    )


def checks(breach=None):
    return tuple(
        MonitoringConditionCheck(
            condition=condition,
            breached=(condition == breach),
            evidence_references=(f"monitor:{index}",),
        )
        for index, condition in enumerate(
            research_case().monitoring_conditions
        )
    )


def record(
    ledger,
    *,
    start,
    gross="0.02",
    transaction="0.001",
    borrow="0",
    turnover="0.2",
    breach=None,
):
    gross_d = Decimal(gross)
    trans_d = Decimal(transaction)
    borrow_d = Decimal(borrow)
    end = start + timedelta(days=1)
    return ledger.record(
        manifest=manifest(),
        registry_state=registry_state(),
        permit=permit(),
        research_case=research_case(),
        period_start=start,
        period_end=end,
        observed_at=end + timedelta(minutes=1),
        gross_return=gross_d,
        net_return=gross_d - trans_d - borrow_d,
        transaction_cost_rate=trans_d,
        borrow_cost_rate=borrow_d,
        one_way_turnover_ratio=Decimal(turnover),
        benchmarks=benchmarks(),
        condition_checks=checks(breach),
        evidence_references=("observation:evidence",),
    )


class PaperMonitoringTests(unittest.TestCase):
    def test_observation_requires_exact_paper_registry_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = PaperMonitoringLedger(Path(tmp) / "paper.duckdb")
            start = AT + timedelta(days=1)
            with self.assertRaises(ValueError):
                ledger.record(
                    manifest=manifest(),
                    registry_state=registry_state(ModelLifecycleStage.BACKTESTED),
                    permit=permit(),
                    research_case=research_case(),
                    period_start=start,
                    period_end=start + timedelta(days=1),
                    observed_at=start + timedelta(days=1, minutes=1),
                    gross_return=Decimal("0.02"),
                    net_return=Decimal("0.019"),
                    transaction_cost_rate=Decimal("0.001"),
                    borrow_cost_rate=Decimal("0"),
                    one_way_turnover_ratio=Decimal("0.2"),
                    benchmarks=benchmarks(),
                    condition_checks=checks(),
                    evidence_references=("obs:evidence",),
                )
            ledger.close()

    def test_observation_cannot_predate_shadow_permit(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = PaperMonitoringLedger(Path(tmp) / "paper.duckdb")
            with self.assertRaises(ValueError):
                record(
                    ledger,
                    start=AT - timedelta(minutes=1),
                )
            ledger.close()

    def test_all_four_benchmarks_are_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = PaperMonitoringLedger(Path(tmp) / "paper.duckdb")
            start = AT + timedelta(days=1)
            with self.assertRaises(ValueError):
                ledger.record(
                    manifest=manifest(),
                    registry_state=registry_state(),
                    permit=permit(),
                    research_case=research_case(),
                    period_start=start,
                    period_end=start + timedelta(days=1),
                    observed_at=start + timedelta(days=1, minutes=1),
                    gross_return=Decimal("0.02"),
                    net_return=Decimal("0.019"),
                    transaction_cost_rate=Decimal("0.001"),
                    borrow_cost_rate=Decimal("0"),
                    one_way_turnover_ratio=Decimal("0.2"),
                    benchmarks=benchmarks()[:-1],
                    condition_checks=checks(),
                    evidence_references=("obs:evidence",),
                )
            ledger.close()

    def test_every_frozen_monitoring_condition_must_be_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = PaperMonitoringLedger(Path(tmp) / "paper.duckdb")
            start = AT + timedelta(days=1)
            with self.assertRaises(ValueError):
                ledger.record(
                    manifest=manifest(),
                    registry_state=registry_state(),
                    permit=permit(),
                    research_case=research_case(),
                    period_start=start,
                    period_end=start + timedelta(days=1),
                    observed_at=start + timedelta(days=1, minutes=1),
                    gross_return=Decimal("0.02"),
                    net_return=Decimal("0.019"),
                    transaction_cost_rate=Decimal("0.001"),
                    borrow_cost_rate=Decimal("0"),
                    one_way_turnover_ratio=Decimal("0.2"),
                    benchmarks=benchmarks(),
                    condition_checks=checks()[:-1],
                    evidence_references=("obs:evidence",),
                )
            ledger.close()

    def test_health_moves_from_insufficient_to_measured(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = PaperMonitoringLedger(Path(tmp) / "paper.duckdb")
            record(ledger, start=AT + timedelta(days=1))
            first = ledger.health(
                manifest=manifest(),
                registry_state=registry_state(),
                policy=policy(),
            )
            self.assertEqual(
                first.state,
                PaperHealthState.INSUFFICIENT_EVIDENCE,
            )
            record(ledger, start=AT + timedelta(days=3))
            second = ledger.health(
                manifest=manifest(),
                registry_state=registry_state(),
                policy=policy(),
            )
            self.assertEqual(second.state, PaperHealthState.MEASURED)
            ledger.close()

    def test_condition_breach_forces_review_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = PaperMonitoringLedger(Path(tmp) / "paper.duckdb")
            condition = research_case().monitoring_conditions[0]
            record(
                ledger,
                start=AT + timedelta(days=1),
                breach=condition,
            )
            health = ledger.health(
                manifest=manifest(),
                registry_state=registry_state(),
                policy=policy(),
            )
            self.assertEqual(
                health.state,
                PaperHealthState.REVIEW_REQUIRED,
            )
            self.assertIn(condition, health.breached_conditions)
            ledger.close()

    def test_drawdown_policy_breach_is_fail_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = PaperMonitoringLedger(Path(tmp) / "paper.duckdb")
            record(
                ledger,
                start=AT + timedelta(days=1),
                gross="-0.30",
                transaction="0",
            )
            health = ledger.health(
                manifest=manifest(),
                registry_state=registry_state(),
                policy=policy(maximum_drawdown_abs=Decimal("0.10")),
            )
            self.assertEqual(
                health.state,
                PaperHealthState.REVIEW_REQUIRED,
            )
            self.assertTrue(
                any("drawdown" in reason for reason in health.reasons)
            )
            ledger.close()

    def test_postmortem_closes_manifest_without_promoting_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = PaperMonitoringLedger(Path(tmp) / "paper.duckdb")
            record(ledger, start=AT + timedelta(days=1))
            state = registry_state()
            postmortem = ledger.close_with_postmortem(
                manifest=manifest(),
                registry_state=state,
                policy=policy(),
                closed_at=AT + timedelta(days=3),
                reviewer="independent-reviewer",
                reason=PostmortemReason.COMPLETED_EVALUATION,
                notes="Prospective evaluation closed for review.",
                evidence_references=("postmortem:evidence",),
            )
            self.assertEqual(
                postmortem.final_health_state,
                PaperHealthState.INSUFFICIENT_EVIDENCE,
            )
            self.assertEqual(state.stage, ModelLifecycleStage.PAPER)
            health = ledger.health(
                manifest=manifest(),
                registry_state=state,
                policy=policy(),
            )
            self.assertEqual(health.state, PaperHealthState.CLOSED)
            with self.assertRaises(ValueError):
                record(ledger, start=AT + timedelta(days=4))
            ledger.close()


if __name__ == "__main__":
    unittest.main()
