import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

from quantos.keyless import (
    KEYLESS_HEALTH_URLS,
    KeylessError,
    KnowledgeTimePolicy,
    PublicObservationStore,
    SECCompanyFactsAdapter,
    SECTickerDirectoryAdapter,
    XbrlFactStore,
    filing_knowledge_time,
    parse_company_facts,
    parse_ecb_csv,
    parse_fred_csv,
    parse_ticker_directory,
    parse_treasury_csv,
    seed_security_master,
)
from quantos.security import DEFAULT_EGRESS_ALLOWLIST
from quantos.security_master import ListingRecord, SecurityMaster
from urllib.parse import urlparse

UTC = timezone.utc
CAPTURED = datetime(2026, 9, 25, 12, tzinfo=UTC)
AGENT = "First Current Quant OS test research@example.com"

TICKERS = json.dumps({
    "fields": ["cik", "name", "ticker", "exchange"],
    "data": [
        [320193, "Apple Inc.", "AAPL", "Nasdaq"],
        [1067983, "BERKSHIRE HATHAWAY INC", "BRK-B", "NYSE"],
        [884394, "SPDR S&P 500 ETF TRUST", "SPY", "NYSE"],
        [999999, "Obscure Holdings", "OBSC", "OTC"],
        [888888, "Nowhere Corp", "NWHR", ""],
    ],
}).encode()


def facts_document(cik=320193, extra_rows=()):
    rows = [
        {"end": "2024-09-28", "val": 364980000000, "accn": "0000320193-24-000123", "fy": 2024, "fp": "FY",
         "form": "10-K", "filed": "2024-11-01", "frame": "CY2024Q3I"},
        # restated in a later filing: a new version, known later
        {"end": "2024-09-28", "val": 365000000000, "accn": "0000320193-25-000010", "fy": 2025, "fp": "Q1",
         "form": "10-Q", "filed": "2025-01-31"},
        *extra_rows,
    ]
    return json.dumps({"cik": cik, "entityName": "Apple Inc.", "facts": {
        "us-gaap": {"Assets": {"label": "Assets", "units": {"USD": rows}}},
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            {"end": "2024-10-18", "val": 15115823000, "accn": "0000320193-24-000123", "fy": 2024, "fp": "FY",
             "form": "10-K", "filed": "2024-11-01"}]}}},
    }}).encode()


class FakeTransport:
    def __init__(self, bodies):
        self.bodies = bodies
        self.calls = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, headers))
        for fragment, body in self.bodies.items():
            if fragment in url:
                return body, "application/json"
        raise AssertionError(f"unexpected URL {url}")


class TickerDirectoryTests(unittest.TestCase):
    def test_parse_and_share_class_lookup(self):
        directory = SECTickerDirectoryAdapter(user_agent=AGENT, transport=FakeTransport({"company_tickers": TICKERS})).fetch(fetched_at=CAPTURED)
        self.assertEqual(directory.lookup("aapl").cik, 320193)
        self.assertEqual(directory.lookup("BRK.B").ticker, "BRK-B")
        self.assertEqual(directory.lookup("BRK/B").ticker, "BRK-B")
        self.assertIsNone(directory.lookup("ZZZZ"))

    def test_contact_email_is_mandatory(self):
        with self.assertRaises(KeylessError):
            SECTickerDirectoryAdapter(user_agent="anonymous")

    def test_bad_directory_fails_closed(self):
        for body in (b"{}", b"not json", json.dumps({"fields": ["cik", "name", "ticker", "exchange"], "data": []}).encode()):
            with self.subTest(body=body[:10]), self.assertRaises(KeylessError):
                parse_ticker_directory(body)

    def test_seed_is_point_in_time_idempotent_and_flags_assumptions(self):
        transport = FakeTransport({"company_tickers": TICKERS})
        directory = SECTickerDirectoryAdapter(user_agent=AGENT, transport=transport).fetch(fetched_at=CAPTURED)
        with tempfile.TemporaryDirectory() as tmp:
            master = SecurityMaster(Path(tmp) / "m.duckdb")
            try:
                result = seed_security_master(master=master, directory=directory, tickers=("AAPL", "BRK.B", "NWHR", "ZZZZ"))
                self.assertEqual(result.security_ids["AAPL"], "SEC:0000320193:AAPL")
                self.assertIn("ZZZZ", result.unresolved)
                self.assertTrue(any("NWHR" in u for u in result.unresolved), "no exchange -> no listing")
                self.assertEqual(
                    master.resolve_ticker(ticker="AAPL", venue_mic="XNAS", on=date(2026, 9, 25), known_at=CAPTURED),
                    "SEC:0000320193:AAPL",
                )
                # not known before the snapshot, and not valid before its date
                self.assertEqual(master.records(known_at=CAPTURED - timedelta(seconds=1)), ())
                self.assertIsNone(master.resolve_ticker(ticker="AAPL", venue_mic=None, on=date(2026, 9, 24), known_at=CAPTURED))
                security = next(r for r in master.records(known_at=CAPTURED) if getattr(r, "security_id", None) == "SEC:0000320193:AAPL" and r.record_key.startswith("security:"))
                self.assertIn("assumption:security-kind-unverified", security.evidence_references)
                # a later identical snapshot adds nothing
                later = SECTickerDirectoryAdapter(user_agent=AGENT, transport=transport).fetch(fetched_at=CAPTURED + timedelta(days=1))
                again = seed_security_master(master=master, directory=later, tickers=("AAPL", "BRK.B"))
                self.assertEqual(again.added, 0)
                self.assertTrue(master.integrity_report(known_at=CAPTURED + timedelta(days=1)).clean)
            finally:
                master.close()


class CompanyFactsTests(unittest.TestCase):
    def test_facts_parse_with_filing_day_knowledge_time_and_dedupe(self):
        facts = parse_company_facts(facts_document(), expected_cik=320193)
        self.assertEqual(len(facts), 3)
        assets = [f for f in facts if f.concept == "Assets"]
        self.assertEqual(assets[0].knowledge_time, datetime(2024, 11, 2, 3, 59, 59, 999999, tzinfo=UTC))
        self.assertEqual(filing_knowledge_time(date(2025, 1, 31)).astimezone(timezone.utc).date(), date(2025, 2, 1))

    def test_as_of_returns_what_was_known_then(self):
        facts = parse_company_facts(facts_document(), expected_cik=320193)
        with tempfile.TemporaryDirectory() as tmp:
            store = XbrlFactStore(Path(tmp) / "f.duckdb")
            try:
                self.assertEqual(store.add(facts, source_artifact_id="a"), 3)
                self.assertEqual(store.add(facts, source_artifact_id="a"), 0)
                before = store.as_of(cik=320193, concept="Assets", known_at=datetime(2024, 12, 1, tzinfo=UTC))
                after = store.as_of(cik=320193, concept="Assets", known_at=datetime(2025, 6, 1, tzinfo=UTC))
                self.assertEqual(before[-1][2], D("364980000000"))
                self.assertEqual(after[-1][2], D("365000000000"))
                self.assertEqual(store.as_of(cik=320193, concept="Assets", known_at=datetime(2024, 11, 1, tzinfo=UTC)), ())
            finally:
                store.close()

    def test_wrong_cik_and_malformed_rows_are_refused(self):
        with self.assertRaises(KeylessError):
            parse_company_facts(facts_document(cik=1), expected_cik=320193)
        with self.assertRaises(KeylessError):
            parse_company_facts(facts_document(extra_rows=({"end": "2024-01-01", "val": 1, "accn": "x"},)), expected_cik=320193)

    def test_adapter_uses_padded_cik_url(self):
        transport = FakeTransport({"CIK0000320193": facts_document()})
        facts, _ = SECCompanyFactsAdapter(user_agent=AGENT, transport=transport).fetch(320193)
        self.assertEqual(len(facts), 3)
        self.assertEqual(transport.calls[0][1]["User-Agent"], AGENT)


TREASURY = b'Date,"1 Mo","1.5 Month","10 Yr","30 Yr"\n09/24/2026,4.01,4.10,4.55,\n09/23/2026,4.00,4.09,4.52,4.90\n'
ECB = b"KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE\nEXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-09-23,1.1350\nEXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-09-24,1.1367\n"
FRED = b"observation_date,DGS10\n2026-09-22,5.10\n2026-09-23,\n2026-09-24,5.11\n"


class RatesTests(unittest.TestCase):
    def test_treasury_tenors_and_publication_schedule_policy(self):
        capture = parse_treasury_csv(TREASURY, captured_at=CAPTURED, policy=KnowledgeTimePolicy.CAPTURE_TIME)
        self.assertEqual({o.series_id for o in capture}, {"UST_PAR_1M", "UST_PAR_1_5M", "UST_PAR_10Y", "UST_PAR_30Y"})
        self.assertEqual(len(capture), 7)  # one blank cell skipped
        self.assertTrue(all(o.knowledge_time == CAPTURED for o in capture))
        scheduled = parse_treasury_csv(TREASURY, captured_at=CAPTURED, policy=KnowledgeTimePolicy.PUBLICATION_SCHEDULE)
        first = next(o for o in scheduled if o.observation_date == date(2026, 9, 23))
        self.assertEqual(first.knowledge_time, datetime(2026, 9, 23, 22, tzinfo=UTC))  # 18:00 New York (EDT)
        # never later than the capture that proves publication
        same_day = parse_treasury_csv(TREASURY, captured_at=datetime(2026, 9, 24, 20, tzinfo=UTC),
                                      policy=KnowledgeTimePolicy.PUBLICATION_SCHEDULE)
        self.assertTrue(all(o.knowledge_time <= datetime(2026, 9, 24, 20, tzinfo=UTC) for o in same_day))

    def test_ecb_and_fred(self):
        ecb = parse_ecb_csv(ECB, currency="USD", captured_at=CAPTURED, policy=KnowledgeTimePolicy.PUBLICATION_SCHEDULE)
        self.assertEqual(ecb[-1].value, D("1.1367"))
        self.assertEqual(ecb[0].knowledge_time, datetime(2026, 9, 23, 14, tzinfo=UTC))  # 16:00 Frankfurt (CEST)
        fred = parse_fred_csv(FRED, series_id="DGS10", captured_at=CAPTURED)
        self.assertEqual([o.observation_date for o in fred], [date(2026, 9, 22), date(2026, 9, 24)])
        self.assertTrue(all(o.knowledge_policy is KnowledgeTimePolicy.CAPTURE_TIME for o in fred))
        with self.assertRaises(KeylessError):
            parse_fred_csv(FRED, series_id="DGS2", captured_at=CAPTURED)
        with self.assertRaises(KeylessError):
            parse_treasury_csv(b"When,1 Mo\n", captured_at=CAPTURED, policy=KnowledgeTimePolicy.CAPTURE_TIME)

    def test_store_keeps_revisions_and_answers_as_of(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = PublicObservationStore(Path(tmp) / "p.duckdb")
            try:
                first = parse_fred_csv(FRED, series_id="DGS10", captured_at=CAPTURED)
                self.assertEqual(store.add(first)["inserted"], 2)
                self.assertEqual(store.add(parse_fred_csv(FRED, series_id="DGS10", captured_at=CAPTURED + timedelta(days=1)))["unchanged"], 2)
                revised = FRED.replace(b"5.10", b"5.12")
                counts = store.add(parse_fred_csv(revised, series_id="DGS10", captured_at=CAPTURED + timedelta(days=2)))
                self.assertEqual(counts, {"inserted": 0, "unchanged": 1, "revisions": 1})
                old = store.series(source="fred-csv", series_id="DGS10", known_at=CAPTURED + timedelta(days=1))
                new = store.series(source="fred-csv", series_id="DGS10", known_at=CAPTURED + timedelta(days=3))
                self.assertEqual(old[0][1], D("5.10"))
                self.assertEqual(new[0][1], D("5.12"))
            finally:
                store.close()

    def test_every_keyless_host_is_allowlisted(self):
        for url in KEYLESS_HEALTH_URLS.values():
            self.assertIn(urlparse(url).hostname, DEFAULT_EGRESS_ALLOWLIST)


if __name__ == "__main__":
    unittest.main()
