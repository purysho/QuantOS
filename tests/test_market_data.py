import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

from quantos.artifacts import SourceArtifactStore
from quantos.corporate_actions import CorporateActionEvent, CorporateActionKind
from quantos.exchange_calendar import XNYSRuleCalendarBuilder
from quantos.market_data import (
    BarStore,
    MarketDataError,
    PolygonDailyAdapter,
    QualityIssueKind,
    TiingoEodAdapter,
    cross_check_provider_actions,
    quality_report,
    reconcile_providers,
    to_session_closes,
)
from quantos.security import SecretProvider, SecretUnavailable

CAL = XNYSRuleCalendarBuilder().build(start=date(2024, 1, 2), end=date(2024, 1, 31))
DAYS = [s.session_date for s in CAL.sessions_between(date(2024, 1, 2), date(2024, 1, 10))]
AFTER = datetime(2024, 1, 11, 2, tzinfo=timezone.utc)  # after the Jan 10 close
SECRETS = SecretProvider(environ={"QUANTOS_SECRET_TIINGO_API_KEY": "tiingo-test-token-123", "QUANTOS_SECRET_POLYGON_API_KEY": "polygon-test-token-456"})


def tiingo_rows(closes, *, div=None, split=None):
    rows = []
    for day, close in zip(DAYS, closes):
        rows.append(
            {
                "date": f"{day.isoformat()}T00:00:00.000Z",
                "open": close, "high": close + 1, "low": close - 1, "close": close,
                "volume": 1000, "adjClose": close,
                "divCash": (div or {}).get(day, 0.0), "splitFactor": (split or {}).get(day, 1.0),
            }
        )
    return json.dumps(rows).encode()


def polygon_body(closes, *, adjusted=False):
    results = []
    for day, close in zip(DAYS, closes):
        opened = datetime(day.year, day.month, day.day, 5, tzinfo=timezone.utc)  # midnight New York
        results.append({"t": int(opened.timestamp() * 1000), "o": close, "h": close + 1, "l": close - 1, "c": close, "v": 900})
    return json.dumps({"status": "OK", "adjusted": adjusted, "results": results}).encode()


class RecordingTransport:
    def __init__(self, body):
        self.body = body
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, headers))
        return self.body, "application/json"


CLOSES = [100.0, 101.0, 102.5, 101.25, 103.0, 104.0, 104.5]


class AdapterTests(unittest.TestCase):
    def test_tiingo_capture_keeps_token_out_of_url_and_archives_raw_bytes(self):
        transport = RecordingTransport(tiingo_rows(CLOSES))
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = SourceArtifactStore(Path(tmp) / "a", Path(tmp) / "a.duckdb")
            capture = TiingoEodAdapter(secrets=SECRETS, transport=transport).capture(
                symbol="SPY", security_id="SEC-SPY", start=DAYS[0], end=DAYS[-1],
                artifacts=artifacts, fetched_at=AFTER,
            )
        url, headers = transport.calls[0]
        self.assertNotIn("tiingo-test-token-123", url)
        self.assertEqual(headers["Authorization"], "Token tiingo-test-token-123")
        self.assertIn("/spy/prices", url)
        self.assertIsNotNone(capture.raw_artifact_id)
        self.assertEqual([b.session_date for b in capture.bars], DAYS)
        self.assertEqual(capture.bars[2].close, D("102.5"))
        self.assertTrue(all(b.knowledge_time == AFTER for b in capture.bars))

    def test_polygon_requires_unadjusted_and_maps_to_new_york_session(self):
        transport = RecordingTransport(polygon_body(CLOSES))
        capture = PolygonDailyAdapter(secrets=SECRETS, transport=transport).capture(
            symbol="spy", security_id="SEC-SPY", start=DAYS[0], end=DAYS[-1], fetched_at=AFTER,
        )
        url, headers = transport.calls[0]
        self.assertIn("adjusted=false", url)
        self.assertNotIn("polygon-test-token-456", url)
        self.assertEqual(headers["Authorization"], "Bearer polygon-test-token-456")
        self.assertEqual([b.session_date for b in capture.bars], DAYS)
        with self.assertRaisesRegex(MarketDataError, "adjusted"):
            PolygonDailyAdapter(secrets=SECRETS, transport=RecordingTransport(polygon_body(CLOSES, adjusted=True))).capture(
                symbol="SPY", security_id="SEC-SPY", start=DAYS[0], end=DAYS[-1], fetched_at=AFTER,
            )

    def test_missing_key_fails_closed_before_any_request(self):
        transport = RecordingTransport(tiingo_rows(CLOSES))
        with self.assertRaises(SecretUnavailable):
            TiingoEodAdapter(secrets=SecretProvider(environ={}), transport=transport).capture(
                symbol="SPY", security_id="SEC-SPY", start=DAYS[0], end=DAYS[-1],
            )
        self.assertEqual(transport.calls, [])

    def test_malformed_and_out_of_range_responses_are_refused(self):
        bad = json.loads(tiingo_rows(CLOSES))
        del bad[0]["close"]
        cases = {
            "missing field": json.dumps(bad).encode(),
            "duplicate": json.dumps(json.loads(tiingo_rows(CLOSES)) * 2).encode(),
            "outside": tiingo_rows(CLOSES),
        }
        for name, body in cases.items():
            end = DAYS[3] if name == "outside" else DAYS[-1]
            with self.subTest(name), self.assertRaises(MarketDataError):
                TiingoEodAdapter(secrets=SECRETS, transport=RecordingTransport(body)).capture(
                    symbol="SPY", security_id="SEC-SPY", start=DAYS[0], end=end, fetched_at=AFTER,
                )


def capture(provider, closes, *, fetched_at=AFTER, **kw):
    if provider == "tiingo":
        adapter = TiingoEodAdapter(secrets=SECRETS, transport=RecordingTransport(tiingo_rows(closes, **kw)))
    else:
        adapter = PolygonDailyAdapter(secrets=SECRETS, transport=RecordingTransport(polygon_body(closes)))
    return adapter.capture(symbol="SPY", security_id="SEC-SPY", start=DAYS[0], end=DAYS[-1], fetched_at=fetched_at)


class StoreAndQualityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = BarStore(Path(self.tmp.name) / "bars.duckdb")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_revisions_are_preserved_and_queried_as_of_knowledge_time(self):
        self.assertEqual(self.store.add(capture("tiingo", CLOSES))["inserted"], 7)
        later = AFTER + timedelta(days=1)
        self.assertEqual(self.store.add(capture("tiingo", CLOSES, fetched_at=later))["unchanged"], 7)
        revised = CLOSES[:3] + [101.5] + CLOSES[4:]
        counts = self.store.add(capture("tiingo", revised, fetched_at=later + timedelta(days=1)))
        self.assertEqual(counts, {"inserted": 0, "unchanged": 6, "revisions": 1})
        before = self.store.bars(provider="tiingo", security_id="SEC-SPY", known_at=later)
        after = self.store.bars(provider="tiingo", security_id="SEC-SPY", known_at=later + timedelta(days=2))
        self.assertEqual(before[3].close, D("101.25"))
        self.assertEqual(after[3].close, D("101.5"))
        self.assertEqual(self.store.bars(provider="tiingo", security_id="SEC-SPY", known_at=AFTER - timedelta(seconds=1)), ())
        report = quality_report(store=self.store, calendar=CAL, provider="tiingo", security_id="SEC-SPY",
                                first_session=DAYS[0], last_session=DAYS[-1], known_at=later + timedelta(days=2))
        self.assertEqual([i.kind for i in report.issues], [QualityIssueKind.REVISED])

    def test_clean_history_then_gap_stale_and_intraday_are_reported(self):
        self.store.add(capture("tiingo", CLOSES))
        report = quality_report(store=self.store, calendar=CAL, provider="tiingo", security_id="SEC-SPY",
                                first_session=DAYS[0], last_session=date(2024, 1, 12), known_at=AFTER)
        self.assertTrue(report.clean, report.issues)
        self.assertTrue(report.report_id.startswith("market-data-quality:"))
        # Two days later nothing new arrived: Jan 11 missing, stale.
        later = datetime(2024, 1, 12, 23, tzinfo=timezone.utc)
        report = quality_report(store=self.store, calendar=CAL, provider="tiingo", security_id="SEC-SPY",
                                first_session=DAYS[0], last_session=date(2024, 1, 12), known_at=later)
        kinds = {i.kind for i in report.issues}
        self.assertEqual(kinds, {QualityIssueKind.MISSING_SESSION, QualityIssueKind.STALE})

    def test_intraday_capture_is_flagged_and_never_becomes_a_close(self):
        intraday = datetime(2024, 1, 10, 18, tzinfo=timezone.utc)  # 13:00 New York
        cap = capture("tiingo", CLOSES, fetched_at=intraday)
        self.store.add(cap)
        report = quality_report(store=self.store, calendar=CAL, provider="tiingo", security_id="SEC-SPY",
                                first_session=DAYS[0], last_session=DAYS[-1], known_at=intraday)
        self.assertIn(QualityIssueKind.INTRADAY_CAPTURE, {i.kind for i in report.issues})
        closes = to_session_closes(bars=cap.bars, calendar=CAL)
        self.assertEqual([c.session_date for c in closes], DAYS[:-1])
        self.assertEqual(closes[0].source_fact_ids, (cap.bars[0].bar_id, cap.capture_id))

    def test_ohlc_violation_detected(self):
        rows = json.loads(tiingo_rows(CLOSES))
        rows[1]["high"] = rows[1]["close"] - 5
        adapter = TiingoEodAdapter(secrets=SECRETS, transport=RecordingTransport(json.dumps(rows).encode()))
        self.store.add(adapter.capture(symbol="SPY", security_id="SEC-SPY", start=DAYS[0], end=DAYS[-1], fetched_at=AFTER))
        report = quality_report(store=self.store, calendar=CAL, provider="tiingo", security_id="SEC-SPY",
                                first_session=DAYS[0], last_session=DAYS[-1], known_at=AFTER)
        self.assertEqual([(i.kind, i.session_date) for i in report.issues], [(QualityIssueKind.OHLC_VIOLATION, DAYS[1])])


class ReconciliationTests(unittest.TestCase):
    def test_providers_agree_within_tolerance_and_disagreements_are_listed(self):
        a = capture("tiingo", CLOSES).bars
        b = capture("polygon", CLOSES[:4] + [103.2] + CLOSES[5:]).bars
        report = reconcile_providers(bars_a=a, bars_b=b, tolerance_bps=D("5"))
        self.assertFalse(report.agreed)
        self.assertEqual([d.session_date for d in report.disagreements], [DAYS[4]])
        self.assertTrue(reconcile_providers(bars_a=a, bars_b=b, tolerance_bps=D("50")).agreed)
        with self.assertRaises(MarketDataError):
            reconcile_providers(bars_a=a, bars_b=a, tolerance_bps=D("5"))

    def test_missing_session_on_one_side(self):
        a = capture("tiingo", CLOSES).bars
        b = capture("polygon", CLOSES[:-1]).bars
        report = reconcile_providers(bars_a=a, bars_b=b, tolerance_bps=D("1"))
        self.assertEqual(report.only_in_a, (DAYS[-1],))
        self.assertEqual(report.compared_sessions, 6)

    def test_provider_actions_cross_checked_against_ledger(self):
        bars = capture("tiingo", CLOSES, div={DAYS[2]: 0.5}, split={DAYS[5]: 2.0}).bars
        k0 = datetime(2023, 12, 1, tzinfo=timezone.utc)
        ledger = (
            CorporateActionEvent(record_key="d", security_id="SEC-SPY", kind=CorporateActionKind.CASH_DIVIDEND,
                                 announced_at=k0, knowledge_time=k0, ex_date=DAYS[2], cash_amount=D("0.5"),
                                 currency="USD", evidence_references=("8-K",)),
            CorporateActionEvent(record_key="s", security_id="SEC-SPY", kind=CorporateActionKind.STOCK_SPLIT,
                                 announced_at=k0, knowledge_time=k0, ex_date=DAYS[5], ratio_new=D("2"),
                                 ratio_old=D("1"), evidence_references=("8-K",)),
        )
        self.assertEqual(cross_check_provider_actions(bars=bars, actions=ledger), ())
        findings = cross_check_provider_actions(bars=bars, actions=ledger[:1])
        self.assertEqual([(f.kind, f.session_date) for f in findings], [("SPLIT", DAYS[5])])
        findings = cross_check_provider_actions(bars=capture("tiingo", CLOSES).bars, actions=ledger)
        self.assertEqual(len(findings), 2)


class PanelIntegrationTests(unittest.TestCase):
    def test_provider_bars_feed_the_research_return_panel(self):
        from quantos.research_price_panel import ResearchReturnPanelBuilder
        from quantos.security_master import CompanyRecord, SecurityKind, SecurityMaster, SecurityRecord

        k0 = datetime(2023, 1, 1, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            master = SecurityMaster(Path(tmp) / "m.duckdb")
            try:
                master.add(CompanyRecord(record_key="c", company_id="C", legal_name="C", country="US",
                                         valid_from=date(2000, 1, 1), valid_to=None, knowledge_time=k0,
                                         evidence_references=("e",)))
                master.add(SecurityRecord(record_key="s", security_id="SEC-SPY", company_id="C",
                                          kind=SecurityKind.COMMON_STOCK, share_class="A", description="SPY",
                                          valid_from=date(2000, 1, 1), valid_to=None, knowledge_time=k0,
                                          evidence_references=("e",)))
                bars = capture("tiingo", CLOSES).bars
                panel = ResearchReturnPanelBuilder().build(
                    security_ids=("SEC-SPY",),
                    closes=to_session_closes(bars=bars, calendar=CAL),
                    actions=(),
                    calendar=CAL,
                    first_session=DAYS[0],
                    last_session=DAYS[-1],
                    decision_time=AFTER,
                    master=master,
                )
            finally:
                master.close()
        self.assertEqual(panel.complete_security_ids, ("SEC-SPY",))
        returns = [o.total_return for o in panel.observations]
        self.assertEqual(returns[0], D("101.0") / D("100.0") - 1)
        self.assertEqual(len(returns), len(DAYS) - 1)


if __name__ == "__main__":
    unittest.main()
