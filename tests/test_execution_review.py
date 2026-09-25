import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from quantos.execution_analytics import TransactionCostAnalyzer
from quantos.execution_contracts import (
    ExecutionSide,
    HistoricalReplayDatasetBuilder,
    SimulationOrderLedger,
    TimeInForce,
)
from quantos.execution_data_quality import ReplayQualityEngine, ReplayQualityPolicy
from quantos.execution_nautilus import (
    MULTI_INSTRUMENT_SCHEDULE_CONTRACT,
    DifferentialState,
    NautilusScheduleDifferentialResult,
    nautilus_schedule_differential_identity,
)
from quantos.execution_review import (
    ExecutionReviewEngine,
    ExecutionReviewPolicy,
    ExecutionReviewRecommendation,
    ExecutionReviewState,
    ExecutionReviewStore,
    execution_review_dossier_identity,
)
from quantos.execution_schedule import ExecutionScheduleBuilder, ExecutionScheduleEngine

from tests.test_execution_schedule import (
    A,
    AT,
    B,
    T1,
    TARGETS,
    dataset,
    order,
    run,
    schedule_policy,
    sim_policy,
    quote,
)


def quality_policy(**overrides):
    values = {
        "minimum_quotes_per_instrument": 1,
        "maximum_quote_gap_ms": 5000,
        "maximum_spread_bps": Decimal("10"),
        "maximum_mid_jump_bps": Decimal("50"),
        "minimum_displayed_quantity": Decimal("10"),
        "maximum_knowledge_lag_ms": 50,
        "maximum_quote_age_at_submission_ms": 500,
        "rationale": "review fixture",
        "evidence_references": ("quality:review",),
    }
    values.update(overrides)
    return ReplayQualityPolicy(**values)


def review_policy(**overrides):
    values = {
        "maximum_implementation_shortfall_bps": Decimal("5"),
        "minimum_fill_ratio": Decimal("0.95"),
        "require_clean_replay": True,
        "require_independent_engine_match": True,
        "rationale": "Liquid large-cap execution standard.",
        "evidence_references": ("execution-review:policy",),
    }
    values.update(overrides)
    return ExecutionReviewPolicy(**values)


def evidence(data=None, qp=None, tif=TimeInForce.GTC):
    data = data or dataset()
    p = sim_policy()
    sp = schedule_policy()
    r = run(data, p)
    intents = (
        order(r, A, ExecutionSide.BUY, "100", "target:A", tif=tif),
        order(r, B, ExecutionSide.SELL, "50", "target:B"),
    )
    schedule = ExecutionScheduleBuilder().build(
        run=r, policy=sp, instruments=(A, B), targets=TARGETS, intents=intents
    )
    with tempfile.TemporaryDirectory() as tmp:
        ledger = SimulationOrderLedger(Path(tmp) / "ledger.duckdb")
        try:
            outcome = ExecutionScheduleEngine().run(
                run=r,
                dataset=data,
                simulation_policy=p,
                schedule_policy=sp,
                schedule=schedule,
                instruments=(A, B),
                intents=intents,
                ledger=ledger,
            )
        finally:
            ledger.close()
    quality = ReplayQualityEngine().evaluate(
        dataset=data, policy=qp or quality_policy(), intents=intents
    )
    by_instrument = {A.execution_instrument_id: A, B.execution_instrument_id: B}
    results = {item.intent_id: item for item in outcome.order_results}
    costs = tuple(
        TransactionCostAnalyzer().analyze(
            dataset=data,
            policy=p,
            instrument=by_instrument[intent.execution_instrument_id],
            intent=intent,
            result=results[intent.intent_id],
            fills=outcome.fills_for(intent.intent_id),
        )
        for intent in intents
    )
    return r, schedule, outcome.result, quality, costs


def engine_differential(r, state=DifferentialState.MATCH):
    result = NautilusScheduleDifferentialResult(
        schedule_differential_id="",
        replay_dataset_id=r.replay_dataset_id,
        simulation_policy_id=r.simulation_policy_id,
        contract_id=MULTI_INSTRUMENT_SCHEDULE_CONTRACT.contract_id,
        order_differential_ids=("d:1", "d:2"),
        execution_instrument_ids=tuple(sorted((A.execution_instrument_id, B.execution_instrument_id))),
        state=state,
        mismatched_differential_ids=() if state is DifferentialState.MATCH else ("d:1",),
        trust_authority="REFERENCE_MATCH_ONLY" if state is DifferentialState.MATCH else "NONE",
        network_authority="NONE",
        external_order_authority="NONE",
        capital_authority="NONE",
    )
    return replace(result, schedule_differential_id=nautilus_schedule_differential_identity(result))


class ExecutionReviewTests(unittest.TestCase):
    def review(self, bundle, differential="match", policy=None, objections=(), unresolved=(), reviewer="trader", challenger="red-team"):
        r, schedule, result, quality, costs = bundle
        diff = (
            engine_differential(r)
            if differential == "match"
            else engine_differential(r, DifferentialState.MISMATCH)
            if differential == "mismatch"
            else None
        )
        return ExecutionReviewEngine().review(
            run=r,
            schedule=schedule,
            schedule_result=result,
            replay_quality=quality,
            transaction_costs=costs,
            schedule_differential=diff,
            policy=policy or review_policy(),
            reviewed_at=AT + timedelta(days=1),
            reviewer=reviewer,
            independent_challenger=challenger,
            limitations=("historical top-of-book replay only",),
            challenger_objections=objections,
            unresolved_objections=unresolved,
            evidence_references=("execution-review:evidence",),
        )

    def test_clean_evidence_is_within_policy_with_shadow_design_only(self):
        dossier = self.review(evidence())
        self.assertEqual(dossier.state, ExecutionReviewState.WITHIN_POLICY, dossier.reasons)
        self.assertEqual(
            dossier.recommendation,
            ExecutionReviewRecommendation.ELIGIBLE_FOR_SHADOW_EXECUTION_DESIGN_REVIEW,
        )
        for name in ("approval_authority", "network_authority", "order_authority", "live_authority", "capital_authority"):
            self.assertEqual(getattr(dossier, name), "NONE")
        self.assertEqual(dossier.dossier_id, execution_review_dossier_identity(dossier))
        self.assertEqual(dossier.fill_ratio, Decimal("1"))

    def test_missing_required_engine_differential_is_insufficient(self):
        dossier = self.review(evidence(), differential=None)
        self.assertEqual(dossier.state, ExecutionReviewState.INSUFFICIENT_EVIDENCE)
        self.assertEqual(dossier.recommendation, ExecutionReviewRecommendation.RESEARCH_ITERATION)

    def test_engine_mismatch_requires_review(self):
        dossier = self.review(evidence(), differential="mismatch")
        self.assertEqual(dossier.state, ExecutionReviewState.REVIEW_REQUIRED)
        self.assertIn("independent engine differential does not match", dossier.reasons)

    def test_unresolved_objection_cannot_be_outvoted(self):
        dossier = self.review(evidence(), objections=("impact model untested at size",), unresolved=("impact model untested at size",))
        self.assertEqual(dossier.state, ExecutionReviewState.REVIEW_REQUIRED)

    def test_shortfall_threshold_breach_requires_review(self):
        dossier = self.review(evidence(), policy=review_policy(maximum_implementation_shortfall_bps=Decimal("0.5")))
        self.assertEqual(dossier.state, ExecutionReviewState.REVIEW_REQUIRED)
        self.assertIn("implementation shortfall exceeds frozen threshold", dossier.reasons)

    def test_incomplete_schedule_and_low_fill_ratio_require_review(self):
        data = HistoricalReplayDatasetBuilder().build(
            start_time=AT,
            end_time=AT + timedelta(seconds=10),
            events=(quote(A, T1, 1, "99.99", "100.01", "60"), quote(B, T1, 1, "49.99", "50.01")),
        )
        dossier = self.review(evidence(data=data, tif=TimeInForce.IOC), policy=review_policy(maximum_implementation_shortfall_bps=Decimal("100")))
        self.assertEqual(dossier.state, ExecutionReviewState.REVIEW_REQUIRED)
        self.assertIn("schedule left targets incomplete", dossier.reasons)
        self.assertIn("fill ratio below frozen minimum", dossier.reasons)

    def test_unusable_replay_is_insufficient(self):
        dossier = self.review(evidence(qp=quality_policy(minimum_quotes_per_instrument=5)))
        self.assertEqual(dossier.state, ExecutionReviewState.INSUFFICIENT_EVIDENCE)

    def test_degraded_replay_requires_review_when_policy_demands_clean(self):
        bundle = evidence(qp=quality_policy(maximum_spread_bps=Decimal("1")))
        self.assertEqual(self.review(bundle).state, ExecutionReviewState.REVIEW_REQUIRED)
        relaxed = self.review(bundle, policy=review_policy(require_clean_replay=False))
        self.assertEqual(relaxed.state, ExecutionReviewState.WITHIN_POLICY)

    def test_same_reviewer_and_challenger_fail_closed(self):
        with self.assertRaises(ValueError):
            self.review(evidence(), reviewer="same", challenger="same")

    def test_cost_reports_must_cover_exactly_the_schedule(self):
        r, schedule, result, quality, costs = evidence()
        with self.assertRaisesRegex(ValueError, "exactly the schedule"):
            self.review((r, schedule, result, quality, costs[:1]))

    def test_store_is_idempotent(self):
        dossier = self.review(evidence())
        with tempfile.TemporaryDirectory() as tmp:
            store = ExecutionReviewStore(Path(tmp) / "review.duckdb")
            self.assertTrue(store.add(dossier))
            self.assertFalse(store.add(dossier))
            store.close()


if __name__ == "__main__":
    unittest.main()
