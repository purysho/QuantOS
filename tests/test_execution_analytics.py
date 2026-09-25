import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from dataclasses import replace

from quantos.execution_analytics import (
    TransactionCostAnalyzer,
    TransactionCostReportStore,
    transaction_cost_report_identity,
)
from quantos.execution_contracts import (
    ExecutionAssetClass,
    ExecutionInstrument,
    ExecutionOrderType,
    ExecutionSide,
    ExecutionSimulationPolicy,
    ExecutionSimulationRunManifestBuilder,
    HistoricalReplayDatasetBuilder,
    SimulationOrderIntentBuilder,
    SimulationOrderLedger,
    TimeInForce,
    TopOfBookQuote,
    TradePrint,
)
from quantos.execution_reference import QuantOSReferenceFillEngine
from quantos.pricing_risk_contracts import Currency

UTC = timezone.utc
AT = datetime(2026, 9, 25, 12, tzinfo=UTC)
T0 = AT + timedelta(seconds=1)
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


def quote(offset, seq, bid, ask, size="1000"):
    at = T0 + offset * MS
    return TopOfBookQuote(
        execution_instrument_id=A.execution_instrument_id,
        event_time=at,
        knowledge_time=at,
        sequence=seq,
        bid_price=Decimal(bid),
        bid_quantity=Decimal(size),
        ask_price=Decimal(ask),
        ask_quantity=Decimal(size),
        source_fact_ids=(f"q:{seq}",),
    )


def trade(offset, seq, price, qty):
    at = T0 + offset * MS
    return TradePrint(
        execution_instrument_id=A.execution_instrument_id,
        event_time=at,
        knowledge_time=at,
        sequence=seq,
        price=Decimal(price),
        quantity=Decimal(qty),
        source_fact_ids=(f"t:{seq}",),
    )


def policy(**overrides):
    values = {
        "market_latency_ms": 0,
        "order_latency_ms": 0,
        "commission_bps": Decimal("0"),
        "slippage_bps": Decimal("0"),
        "market_impact_bps": Decimal("0"),
        "maximum_participation_rate": Decimal("1"),
        "allow_partial_fills": True,
        "rationale": "TCA fixture.",
        "evidence_references": ("policy:tca",),
    }
    values.update(overrides)
    return ExecutionSimulationPolicy(**values)


def simulate(events, p, **order):
    data = HistoricalReplayDatasetBuilder().build(
        start_time=AT, end_time=AT + timedelta(seconds=10), events=events
    )
    r = ExecutionSimulationRunManifestBuilder().build(
        research_run_manifest_id="research-run-manifest:" + "r" * 64,
        portfolio_solution_id="portfolio-solution:" + "p" * 64,
        replay_dataset=data,
        simulation_policy=p,
        engine_name="QUANTOS_REFERENCE",
        engine_version="12.2",
        code_revision="git:stage12.8",
        created_at=AT,
        evidence_references=("run:tca",),
    )
    values = {
        "side": ExecutionSide.BUY,
        "order_type": ExecutionOrderType.MARKET,
        "quantity": Decimal("100"),
        "time_in_force": TimeInForce.GTC,
        "submitted_at": T0,
        "source_target_id": "target:A",
    }
    values.update(order)
    intent = SimulationOrderIntentBuilder().build(run=r, instrument=A, **values)
    with tempfile.TemporaryDirectory() as tmp:
        ledger = SimulationOrderLedger(Path(tmp) / "l.duckdb")
        try:
            result = QuantOSReferenceFillEngine().simulate(
                run=r, dataset=data, policy=p, instrument=A, intent=intent, ledger=ledger
            )
            fills = ledger.fills(intent.intent_id)
        finally:
            ledger.close()
    return data, intent, result, fills


def analyze(data, p, intent, result, fills):
    return TransactionCostAnalyzer().analyze(
        dataset=data, policy=p, instrument=A, intent=intent, result=result, fills=fills
    )


class TransactionCostTests(unittest.TestCase):
    def test_buy_shortfall_decomposes_exactly(self):
        p = policy(order_latency_ms=5, slippage_bps=Decimal("2"), commission_bps=Decimal("1"))
        events = (
            quote(0, 1, "99.99", "100.01"),
            quote(5, 2, "100.03", "100.05"),
            trade(1, 3, "100.06", "300"),
            trade(2, 4, "100.02", "100"),
        )
        data, intent, result, fills = simulate(events, p)
        report = analyze(data, p, intent, result, fills)
        self.assertEqual(report.arrival_mid, Decimal("100.00"))
        self.assertEqual(result.volume_weighted_average_price, Decimal("100.08"))
        self.assertEqual(report.timing_cost, Decimal("4.00"))
        self.assertEqual(report.half_spread_cost, Decimal("1.00"))
        self.assertEqual(report.slippage_impact_cost, Decimal("3.00"))
        self.assertEqual(report.fees, Decimal("1.0008"))
        self.assertEqual(report.opportunity_cost, Decimal("0"))
        self.assertEqual(report.implementation_shortfall, Decimal("9.0008"))
        self.assertEqual(report.implementation_shortfall_bps, Decimal("9.0008"))
        self.assertEqual(report.fill_ratio, Decimal("1"))
        self.assertEqual(report.time_to_first_fill_ms, 5)
        self.assertEqual(report.market_vwap, Decimal("100.05"))
        self.assertEqual(report.slippage_vs_market_vwap_bps.quantize(Decimal("0.0001")), Decimal("2.9985"))
        self.assertEqual(report.report_id, transaction_cost_report_identity(report))
        self.assertEqual(report.capital_authority, "NONE")

    def test_partial_sell_charges_opportunity_cost_at_terminal_mid(self):
        p = policy()
        events = (
            quote(0, 1, "99.99", "100.01", size="60"),
            quote(5, 2, "99.90", "99.92"),
        )
        data, intent, result, fills = simulate(
            events,
            p,
            side=ExecutionSide.SELL,
            order_type=ExecutionOrderType.LIMIT,
            limit_price=Decimal("99.99"),
        )
        report = analyze(data, p, intent, result, fills)
        self.assertEqual(report.filled_quantity, Decimal("60"))
        self.assertEqual(report.terminal_mid, Decimal("99.91"))
        self.assertEqual(report.half_spread_cost, Decimal("0.60"))
        self.assertEqual(report.opportunity_cost, Decimal("3.60"))
        self.assertEqual(report.implementation_shortfall, Decimal("4.20"))
        self.assertIn(
            "market VWAP benchmark unavailable: no trade prints in order window",
            report.diagnostics,
        )
        self.assertIsNone(report.market_vwap)

    def test_arrival_without_book_fails_closed(self):
        p = policy(market_latency_ms=3)
        data, intent, result, fills = simulate((quote(0, 1, "99.99", "100.01"),), p)
        with self.assertRaisesRegex(ValueError, "arrival benchmark"):
            analyze(data, p, intent, result, fills)

    def test_tampered_fill_is_refused(self):
        p = policy()
        data, intent, result, fills = simulate((quote(0, 1, "99.99", "100.01"),), p)
        with self.assertRaises(ValueError):
            analyze(data, p, intent, result, (replace(fills[0], price=Decimal("1")),))

    def test_aggregate_sums_components(self):
        p = policy()
        buy = simulate((quote(0, 1, "99.99", "100.01"),), p)
        sell = simulate((quote(0, 1, "99.99", "100.01"),), p, side=ExecutionSide.SELL)
        reports = (analyze(buy[0], p, *buy[1:]), analyze(sell[0], p, *sell[1:]))
        aggregate = TransactionCostAnalyzer().aggregate(reports)
        self.assertEqual(aggregate.half_spread_cost, Decimal("2.00"))
        self.assertEqual(aggregate.implementation_shortfall, Decimal("2.00"))
        self.assertEqual(aggregate.paper_notional, Decimal("20000.00"))
        self.assertEqual(aggregate.filled_quantity_ratio, Decimal("1"))
        with self.assertRaises(ValueError):
            TransactionCostAnalyzer().aggregate((reports[0], reports[0]))

    def test_store_is_idempotent(self):
        p = policy()
        data, intent, result, fills = simulate((quote(0, 1, "99.99", "100.01"),), p)
        report = analyze(data, p, intent, result, fills)
        with tempfile.TemporaryDirectory() as tmp:
            store = TransactionCostReportStore(Path(tmp) / "tca.duckdb")
            self.assertTrue(store.add(report))
            self.assertFalse(store.add(report))
            store.close()


if __name__ == "__main__":
    unittest.main()
