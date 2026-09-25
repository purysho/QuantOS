import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from quantos.security_master import (
    AmbiguousResolution,
    CompanyRecord,
    IdentifierRecord,
    IdentifierScheme,
    ListingRecord,
    MasterIssueKind,
    SecurityKind,
    SecurityMaster,
    SecurityRecord,
    identifier_check_digit_valid,
)

UTC = timezone.utc
K0 = datetime(2019, 12, 1, tzinfo=UTC)
K_CHANGE = datetime(2022, 6, 1, tzinfo=UTC)
K_REUSE = datetime(2022, 12, 15, tzinfo=UTC)
NOW = datetime(2026, 9, 25, tzinfo=UTC)
EV = ("filing:evidence",)


def company(cid, known=K0, **kw):
    return CompanyRecord(
        record_key=f"company:{cid}",
        company_id=cid,
        legal_name=f"{cid} Holdings",
        country="US",
        valid_from=date(2000, 1, 1),
        valid_to=None,
        knowledge_time=known,
        evidence_references=EV,
        **kw,
    )


def security(sid, cid, known=K0, valid_from=date(2000, 1, 1), valid_to=None):
    return SecurityRecord(
        record_key=f"security:{sid}",
        security_id=sid,
        company_id=cid,
        kind=SecurityKind.COMMON_STOCK,
        share_class="A",
        description=f"{sid} common",
        valid_from=valid_from,
        valid_to=valid_to,
        knowledge_time=known,
        evidence_references=EV,
    )


def listing(key, sid, ticker, start, end, known, primary=True, venue="XNAS", **kw):
    return ListingRecord(
        record_key=f"listing:{key}",
        listing_id=key,
        security_id=sid,
        venue_mic=venue,
        ticker=ticker,
        currency="USD",
        is_primary=primary,
        valid_from=start,
        valid_to=end,
        knowledge_time=known,
        evidence_references=EV,
        **kw,
    )


def identifier(key, scheme, value, sid=None, cid=None, known=K0, start=date(2000, 1, 1), end=None):
    return IdentifierRecord(
        record_key=f"identifier:{key}",
        scheme=scheme,
        value=value,
        security_id=sid,
        company_id=cid,
        valid_from=start,
        valid_to=end,
        knowledge_time=known,
        evidence_references=EV,
    )


class SecurityMasterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.master = SecurityMaster(Path(self.tmp.name) / "master.duckdb")
        for record in (
            company("C1"),
            company("C2"),
            security("S1", "C1"),
            security("S2", "C2"),
            # Original belief: OLD trades indefinitely.
            listing("L1", "S1", "OLD", date(2020, 1, 1), None, K0),
            # Correction learned 2022-06-01: OLD ends at the symbol change.
            listing("L1", "S1", "OLD", date(2020, 1, 1), date(2022, 6, 9), K_CHANGE),
            listing("L2", "S1", "NEW", date(2022, 6, 9), None, K_CHANGE),
            # An unrelated security reuses the OLD symbol.
            listing("L3", "S2", "OLD", date(2023, 1, 1), None, K_REUSE),
            identifier("isin-S1", IdentifierScheme.ISIN, "US0378331005", sid="S1"),
            identifier("cik-C1", IdentifierScheme.CIK, "320193", cid="C1"),
        ):
            self.master.add(record)

    def tearDown(self):
        self.master.close()
        self.tmp.cleanup()

    def test_ticker_resolution_follows_symbol_change_and_reuse(self):
        resolve = self.master.resolve_ticker
        self.assertEqual(resolve(ticker="OLD", venue_mic="XNAS", on=date(2021, 5, 3), known_at=NOW), "S1")
        self.assertEqual(resolve(ticker="NEW", venue_mic="XNAS", on=date(2022, 7, 1), known_at=NOW), "S1")
        self.assertIsNone(resolve(ticker="NEW", venue_mic="XNAS", on=date(2022, 6, 8), known_at=NOW))
        self.assertIsNone(resolve(ticker="OLD", venue_mic="XNAS", on=date(2022, 10, 1), known_at=NOW))
        self.assertEqual(resolve(ticker="old", venue_mic=None, on=date(2023, 6, 1), known_at=NOW), "S2")

    def test_corrections_do_not_leak_backward_in_knowledge_time(self):
        before_change_known = datetime(2022, 5, 1, tzinfo=UTC)
        self.assertEqual(
            self.master.resolve_ticker(ticker="OLD", venue_mic="XNAS", on=date(2022, 10, 1), known_at=before_change_known),
            "S1",
        )
        self.assertIsNone(
            self.master.resolve_ticker(ticker="NEW", venue_mic="XNAS", on=date(2022, 10, 1), known_at=before_change_known)
        )

    def test_listing_history_and_primary_listing(self):
        history = self.master.listings(security_id="S1", known_at=NOW)
        self.assertEqual([item.ticker for item in history], ["OLD", "NEW"])
        primary = self.master.primary_listing(security_id="S1", on=date(2024, 1, 2), known_at=NOW)
        self.assertEqual(primary.ticker, "NEW")

    def test_identifier_resolution(self):
        self.assertEqual(
            self.master.resolve_identifier(scheme=IdentifierScheme.ISIN, value="US0378331005", on=date(2024, 1, 2), known_at=NOW),
            "S1",
        )
        self.assertEqual(
            self.master.resolve_identifier(scheme=IdentifierScheme.CIK, value="320193", on=date(2024, 1, 2), known_at=NOW),
            "C1",
        )
        self.assertIsNone(
            self.master.resolve_identifier(scheme=IdentifierScheme.ISIN, value="US0378331005", on=date(2024, 1, 2), known_at=datetime(2019, 1, 1, tzinfo=UTC))
        )

    def test_clean_master_has_no_integrity_issues(self):
        report = self.master.integrity_report(known_at=NOW)
        self.assertTrue(report.clean, report.issues)
        self.assertTrue(report.report_id.startswith("security-master-integrity:"))

    def test_ticker_collision_fails_resolution_and_is_reported(self):
        self.master.add(listing("L4", "S2", "NEW", date(2024, 1, 1), None, NOW, primary=False))
        with self.assertRaises(AmbiguousResolution):
            self.master.resolve_ticker(ticker="NEW", venue_mic="XNAS", on=date(2024, 6, 1), known_at=NOW)
        kinds = {item.kind for item in self.master.integrity_report(known_at=NOW).issues}
        self.assertIn(MasterIssueKind.TICKER_COLLISION, kinds)

    def test_retraction_withdraws_a_record(self):
        self.master.add(listing("L3", "S2", "OLD", date(2023, 1, 1), None, NOW, retracted=True))
        self.assertIsNone(
            self.master.resolve_ticker(ticker="OLD", venue_mic="XNAS", on=date(2023, 6, 1), known_at=NOW)
        )
        self.assertEqual(
            self.master.resolve_ticker(ticker="OLD", venue_mic="XNAS", on=date(2023, 6, 1), known_at=K_REUSE),
            "S2",
        )

    def test_integrity_issues_are_reported(self):
        later = datetime(2026, 9, 26, tzinfo=UTC)
        for record in (
            identifier("isin-dup", IdentifierScheme.ISIN, "US0378331005", sid="S2", known=later),
            identifier("isin-bad", IdentifierScheme.ISIN, "US0378331006", sid="S2", known=later),
            listing("L5", "S9", "GHOST", date(2024, 1, 1), None, later),
            listing("L6", "S1", "DUAL", date(2024, 1, 1), None, later, venue="XNYS"),
            security("S3", "C1", known=later, valid_from=date(2021, 1, 1), valid_to=date(2022, 1, 1)),
            listing("L7", "S3", "SHORT", date(2021, 6, 1), date(2023, 1, 1), later),
        ):
            self.master.add(record)
        kinds = {item.kind for item in self.master.integrity_report(known_at=later).issues}
        self.assertEqual(
            kinds,
            {
                MasterIssueKind.IDENTIFIER_COLLISION,
                MasterIssueKind.INVALID_CHECK_DIGIT,
                MasterIssueKind.DANGLING_REFERENCE,
                MasterIssueKind.MULTIPLE_PRIMARY_LISTINGS,
                MasterIssueKind.LISTING_OUTSIDE_SECURITY_LIFE,
            },
        )
        with self.assertRaises(AmbiguousResolution):
            self.master.resolve_identifier(scheme=IdentifierScheme.ISIN, value="US0378331005", on=date(2024, 1, 2), known_at=later)

    def test_store_guards(self):
        self.assertFalse(self.master.add(company("C1")))
        with self.assertRaisesRegex(ValueError, "share a knowledge time"):
            self.master.add(listing("L2", "S1", "NEWER", date(2022, 6, 9), None, K_CHANGE))
        with self.assertRaisesRegex(ValueError, "another record kind"):
            self.master.add(
                CompanyRecord(
                    record_key="listing:L1",
                    company_id="C9",
                    legal_name="x",
                    country="US",
                    valid_from=date(2000, 1, 1),
                    valid_to=None,
                    knowledge_time=NOW,
                    evidence_references=EV,
                )
            )

    def test_record_validation(self):
        with self.assertRaises(ValueError):
            identifier("bad", IdentifierScheme.CIK, "320193", sid="S1")
        with self.assertRaises(ValueError):
            identifier("bad", IdentifierScheme.ISIN, "US0378331005", sid="S1", cid="C1")
        with self.assertRaises(ValueError):
            listing("bad", "S1", "lower", date(2020, 1, 1), None, NOW)
        with self.assertRaises(ValueError):
            listing("bad", "S1", "X", date(2020, 1, 1), date(2020, 1, 1), NOW)

    def test_check_digits(self):
        valid = {
            IdentifierScheme.ISIN: "US0378331005",
            IdentifierScheme.CUSIP: "037833100",
            IdentifierScheme.SEDOL: "2046251",
            IdentifierScheme.FIGI: "BBG000B9XRY4",
        }
        for scheme, value in valid.items():
            self.assertTrue(identifier_check_digit_valid(scheme, value), scheme)
            broken = value[:-1] + str((int(value[-1]) + 1) % 10)
            self.assertFalse(identifier_check_digit_valid(scheme, broken), scheme)


if __name__ == "__main__":
    unittest.main()
