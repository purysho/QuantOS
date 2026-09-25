import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from quantos.execution_contracts import (
    ExecutionAssetClass,
    ExecutionInstrument,
    ExecutionOrderType,
    ExecutionSide,
    ExecutionSimulationPolicy,
    ExecutionSimulationRunManifestBuilder,
    HistoricalReplayDatasetBuilder,
    SimulationOrderIntentBuilder,
    TimeInForce,
    TopOfBookQuote,
    TradePrint,
)
from quantos.execution_data_quality import (
    ReplayIssueKind,
    ReplayQualityEngine,
    ReplayQualityPolicy,
    ReplayQualityReportStore,
    ReplayQualityState,
    replay_quality_report_identity,
)
from quantos.pricing_risk_contracts import Currency

UTC = timezone.utc
AT = datetime(2026, 9, 25, 12, tzinfo=UTC)
MS = timedelta(milliseconds=1)

A = ExecutionInstrument(
    canonical_instrument_id="SEC:A",
    venue_id="XTEST",
    venue_symbol="A",
    asset_class=ExecutionAssetClass.EQUITY,
    quote_currency=Currency.USD,
    price_increment=Decimal("0.01"),
    quantity_increment=Decimal("1"),
    minimum_quantity=Decimal("1"),
    contract_multiplier=Decimal("1"),
)


def quote(offset_ms, sequence, bid="99.99", ask="100.01", size="1000", lag_ms=0):
    known = AT + timedelta(seconds=1) + offset_ms * MS
    return TopOfBookQuote(
        execution_instrument_id=A.execution_instrument_id,
        event_time=known - lag_ms * MS,
        knowledge_time=known,
        sequence=sequence,
        bid_price=Decimal(bid),
        bid_quantity=Decimal(size),
        ask_price=Decimal(ask),
        ask_quantity=Decimal(size),
        source_fact_ids=(f"quote:{sequence}:{offset_ms}",),
    )


def trade(offset_ms, sequence, price):
    known = AT + timedelta(seconds=1) + offset_ms * MS
    return TradePrint(
        execution_instrument_id=A.execution_instrument_id,
        event_time=known,
        knowledge_time=known,
        sequence=sequence,
        price=Decimal(price),
        quantity=Decimal("10"),
        source_fact_ids=(f"trade:{sequence}",),
    )


def dataset(*events):
    return HistoricalReplayDatasetBuilder().build(
        start_time=AT,
        end_time=AT + timedelta(seconds=10),
        events=events,
    )


def policy(**overrides):
    values = {
        "minimum_quotes_per_instrument": 2,
        "maximum_quote_gap_ms": 1000,
        "maximum_spread_bps": Decimal("10"),
        "maximum_mid_jump_bps": Decimal("50"),
        "minimum_displayed_quantity": Decimal("100"),
        "maximum_knowledge_lag_ms": 50,
        "maximum_quote_age_at_submission_ms": 500,
        "rationale": "Liquid large-cap replay standard.",
        "evidence_references": ("replay-quality:policy",),
    }
    values.update(overrides)
    return ReplayQualityPolicy(**values)


def intent(data, submitted_offset_ms):
    p = ExecutionSimulationPolicy(
        market_latency_ms=0,
        order_latency_ms=0,
        commission_bps=Decimal("0"),
        slippage_bps=Decimal("0"),
        market_impact_bps=Decimal("0"),
        maximum_participation_rate=Decimal("1"),
        allow_partial_fills=False,
        rationale="quality fixture",
        evidence_references=("policy:q",),
    )
    r = ExecutionSimulationRunManifestBuilder().build(
        research_run_manifest_id="research-run-manifest:" + "r" * 64,
        portfolio_solution_id="portfolio-solution:" + "p" * 64,
        replay_dataset=data,
        simulation_policy=p,
        engine_name="QUANTOS_REFERENCE",
        engine_version="12.2",
        code_revision="git:stage12.7",
        created_at=AT,
        evidence_references=("run:q",),
    )
    return SimulationOrderIntentBuilder().build(
        run=r,
        instrument=A,
        side=ExecutionSide.BUY,
        order_type=ExecutionOrderType.MARKET,
        quantity=Decimal("10"),
        time_in_force=TimeInForce.GTC,
        submitted_at=AT + timedelta(seconds=1) + submitted_offset_ms * MS,
        source_target_id="target:A",
    )


def kinds(report):
    return {item.kind for item in report.issues}


class ReplayQualityTests(unittest.TestCase):
    def evaluate(self, data, p=None, intents=(), latency=0):
        return ReplayQualityEngine().evaluate(
            dataset=data, policy=p or policy(), intents=intents, market_latency_ms=latency
        )

    def test_clean_tape_is_clean(self):
        data = dataset(quote(0, 1), quote(100, 2, "100.00", "100.02"))
        report = self.evaluate(data, intents=(intent(data, 150),))
        self.assertEqual(report.state, ReplayQualityState.CLEAN, report.issues)
        self.assertEqual(report.issues, ())
        self.assertEqual(report.profiles[0].quote_count, 2)
        self.assertEqual(report.report_id, replay_quality_report_identity(report))
        self.assertEqual(report.capital_authority, "NONE")

    def test_degraded_issues_are_reported_without_repair(self):
        data = dataset(
            quote(0, 1),
            quote(2000, 2, "101.00", "101.50", size="50", lag_ms=80),
            trade(2500, 3, "90.00"),
        )
        report = self.evaluate(data)
        self.assertEqual(report.state, ReplayQualityState.DEGRADED)
        self.assertEqual(
            kinds(report),
            {
                ReplayIssueKind.QUOTE_GAP,
                ReplayIssueKind.WIDE_SPREAD,
                ReplayIssueKind.MID_JUMP,
                ReplayIssueKind.THIN_BOOK,
                ReplayIssueKind.KNOWLEDGE_LAG,
                ReplayIssueKind.TRADE_OUTSIDE_QUOTE,
            },
        )
        self.assertEqual(len(data.events), 3)

    def test_sequence_regression_is_unusable(self):
        report = self.evaluate(dataset(quote(0, 5), quote(100, 2)))
        self.assertEqual(report.state, ReplayQualityState.UNUSABLE)
        self.assertIn(ReplayIssueKind.SEQUENCE_REGRESSION, kinds(report))

    def test_ambiguous_arrival_order_is_degraded(self):
        report = self.evaluate(dataset(quote(0, 1), quote(0, 2, "100.00", "100.02")))
        self.assertIn(ReplayIssueKind.AMBIGUOUS_ARRIVAL_ORDER, kinds(report))

    def test_insufficient_quotes_is_unusable(self):
        report = self.evaluate(dataset(quote(0, 1)))
        self.assertEqual(report.state, ReplayQualityState.UNUSABLE)
        self.assertIn(ReplayIssueKind.INSUFFICIENT_QUOTES, kinds(report))

    def test_order_before_any_book_is_unusable(self):
        data = dataset(quote(100, 1), quote(200, 2))
        report = self.evaluate(data, intents=(intent(data, 50),))
        self.assertEqual(report.state, ReplayQualityState.UNUSABLE)
        self.assertIn(ReplayIssueKind.NO_BOOK_AT_SUBMISSION, kinds(report))

    def test_market_latency_can_hide_the_book_at_submission(self):
        data = dataset(quote(0, 1), quote(100, 2))
        order = intent(data, 10)
        self.assertEqual(self.evaluate(data, intents=(order,)).state, ReplayQualityState.CLEAN)
        delayed = self.evaluate(data, intents=(order,), latency=20)
        self.assertIn(ReplayIssueKind.NO_BOOK_AT_SUBMISSION, kinds(delayed))

    def test_stale_book_at_submission_is_degraded(self):
        data = dataset(quote(0, 1), quote(100, 2))
        report = self.evaluate(data, intents=(intent(data, 900),))
        self.assertEqual(report.state, ReplayQualityState.DEGRADED)
        self.assertIn(ReplayIssueKind.STALE_BOOK_AT_SUBMISSION, kinds(report))

    def test_policy_identity_and_validation(self):
        self.assertNotEqual(policy().policy_id, policy(maximum_quote_gap_ms=999).policy_id)
        with self.assertRaises(ValueError):
            policy(maximum_spread_bps=Decimal("0"))

    def test_store_is_idempotent(self):
        report = self.evaluate(dataset(quote(0, 1), quote(100, 2)))
        with tempfile.TemporaryDirectory() as tmp:
            store = ReplayQualityReportStore(Path(tmp) / "q.duckdb")
            self.assertTrue(store.add(report))
            self.assertFalse(store.add(report))
            store.close()


if __name__ == "__main__":
    unittest.main()
