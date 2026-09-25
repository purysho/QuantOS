import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from quantos.corporate_actions import AdjustmentMode, CorporateActionEvent, CorporateActionKind
from quantos.exchange_calendar import XNYSRuleCalendarBuilder
from quantos.portfolio_construction import PortfolioDatasetBuilder
from quantos.research_price_panel import (
    PanelIssueKind,
    ResearchReturnPanelBuilder,
    SessionCloseObservation,
    research_return_panel_identity,
)
from quantos.security_master import CompanyRecord, SecurityKind, SecurityMaster, SecurityRecord

from tests.test_portfolio_construction import manifest

UTC = timezone.utc
D = Decimal
K0 = datetime(2024, 1, 1, tzinfo=UTC)
DECISION = datetime(2024, 7, 12, 23, tzinfo=UTC)
CAL = XNYSRuleCalendarBuilder().build(start=date(2024, 1, 2), end=date(2024, 12, 31))
SESSIONS = [s.session_date for s in CAL.sessions_between(date(2024, 7, 1), date(2024, 7, 12))]


def close(security, day, price, known_delay=timedelta(minutes=5), tag="v1"):
    session = CAL.session(day)
    return SessionCloseObservation(
        security_id=security,
        session_date=day,
        close=D(price),
        knowledge_time=session.close_utc + known_delay,
        source_fact_ids=(f"close:{security}:{day}:{tag}",),
    )


def action(key, security, kind, ex, **kw):
    return CorporateActionEvent(
        record_key=key,
        security_id=security,
        kind=kind,
        announced_at=K0,
        knowledge_time=K0,
        ex_date=ex,
        evidence_references=("8-K",),
        **kw,
    )


PRICES = {
    "S1": ["100", "101", "102", "51.5", "52", "52.5", "53", "53.5", "54"],
    "S2": ["50", "50.5", "49.5", "50", "51", "51.5", "52", "52", "52.5"],
}
ACTIONS = (
    action("split", "S1", CorporateActionKind.STOCK_SPLIT, SESSIONS[3], ratio_new=D("2"), ratio_old=D("1")),
    action("div", "S2", CorporateActionKind.CASH_DIVIDEND, SESSIONS[4], cash_amount=D("0.5"), currency="USD"),
)


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.master = SecurityMaster(Path(self.tmp.name) / "m.duckdb")
        self.master.add(CompanyRecord(record_key="c", company_id="C", legal_name="C", country="US",
                                      valid_from=date(2000, 1, 1), valid_to=None, knowledge_time=K0, evidence_references=("e",)))
        for sid in ("S1", "S2", "S3"):
            self.master.add(SecurityRecord(record_key=f"s:{sid}", security_id=sid, company_id="C", kind=SecurityKind.COMMON_STOCK,
                                           share_class="A", description=sid, valid_from=date(2000, 1, 1), valid_to=None,
                                           knowledge_time=K0, evidence_references=("e",)))

    def tearDown(self):
        self.master.close()
        self.tmp.cleanup()

    def closes(self, skip=()):
        return tuple(
            close(sid, day, price)
            for sid, series in PRICES.items()
            for day, price in zip(SESSIONS, series)
            if (sid, day) not in skip
        )

    def build(self, closes=None, actions=ACTIONS, securities=("S1", "S2"), decision=DECISION):
        return ResearchReturnPanelBuilder().build(
            security_ids=securities,
            closes=closes if closes is not None else self.closes(),
            actions=actions,
            calendar=CAL,
            first_session=SESSIONS[0],
            last_session=SESSIONS[-1],
            decision_time=decision,
            master=self.master,
        )

    def test_total_returns_include_split_and_dividend_and_feed_portfolio_dataset(self):
        panel = self.build()
        self.assertEqual(panel.complete_security_ids, ("S1", "S2"))
        self.assertEqual(panel.panel_id, research_return_panel_identity(panel))
        s1 = {o.period_end: o.total_return for o in panel.observations if o.security_id == "S1"}
        split_day = CAL.session(SESSIONS[3]).close_utc
        self.assertEqual(s1[split_day], D("51.5") * 2 / D("102") - 1)
        s2 = {o.period_end: o.total_return for o in panel.observations if o.security_id == "S2"}
        self.assertEqual(s2[CAL.session(SESSIONS[4]).close_utc], (D("51") + D("0.5")) / D("50") - 1)
        adjusted = {a.security_id: dict(a.closes) for a in panel.adjusted_closes}
        self.assertEqual(adjusted["S1"][SESSIONS[0]], D("50.0"))
        self.assertEqual(panel.adjusted_closes[0].mode, AdjustmentMode.TOTAL_RETURN)
        dataset = PortfolioDatasetBuilder().build(
            manifest=manifest(),
            decision_time=DECISION,
            observations=panel.portfolio_observations(("S1", "S2")),
            minimum_periods=4,
        )
        self.assertEqual(dataset.observations, len(SESSIONS) - 1)
        for o in panel.observations:
            self.assertGreaterEqual(o.knowledge_time, o.period_end)

    def test_missing_close_makes_security_incomplete_without_fill(self):
        panel = self.build(closes=self.closes(skip={("S2", SESSIONS[5])}))
        self.assertEqual(panel.incomplete_security_ids, ("S2",))
        self.assertIn(PanelIssueKind.MISSING_SESSION_CLOSE, {i.kind for i in panel.issues})
        with self.assertRaisesRegex(ValueError, "INCOMPLETE"):
            panel.portfolio_observations(("S1", "S2"))

    def test_latest_known_revision_wins_and_future_revisions_are_ignored(self):
        revised = close("S2", SESSIONS[1], "50.25", known_delay=timedelta(hours=2), tag="v2")
        future = close("S2", SESSIONS[2], "10", known_delay=timedelta(days=30), tag="v3")
        panel = self.build(closes=self.closes() + (revised, future))
        s2 = {o.period_end: o.total_return for o in panel.observations if o.security_id == "S2"}
        self.assertEqual(s2[CAL.session(SESSIONS[1]).close_utc], D("50.25") / D("50") - 1)
        self.assertEqual(s2[CAL.session(SESSIONS[2]).close_utc], D("49.5") / D("50.25") - 1)

    def test_close_known_before_session_close_is_rejected(self):
        early = close("S1", SESSIONS[2], "999", known_delay=timedelta(hours=-1), tag="early")
        panel = self.build(closes=self.closes() + (early,))
        self.assertIn(PanelIssueKind.CLOSE_KNOWN_BEFORE_SESSION_CLOSE, {i.kind for i in panel.issues})

    def test_unknown_security_and_unresolved_delisting(self):
        delist = action("delist", "S1", CorporateActionKind.DELISTING, SESSIONS[6])
        closes = tuple(c for c in self.closes() if not (c.security_id == "S1" and c.session_date >= SESSIONS[6]))
        panel = self.build(closes=closes, actions=ACTIONS + (delist,), securities=("S1", "S2", "S9"))
        kinds = {(i.security_id, i.kind) for i in panel.issues}
        self.assertIn(("S9", PanelIssueKind.UNKNOWN_SECURITY), kinds)
        self.assertIn(("S1", PanelIssueKind.CORPORATE_ACTION_UNRESOLVED), kinds)
        self.assertEqual(panel.complete_security_ids, ("S2",))

    def test_cash_merger_terminal_return_without_final_close(self):
        merger = action("merge", "S1", CorporateActionKind.CASH_MERGER, SESSIONS[6], cash_amount=D("60"), currency="USD")
        closes = tuple(c for c in self.closes() if not (c.security_id == "S1" and c.session_date >= SESSIONS[6]))
        panel = self.build(closes=closes, actions=ACTIONS + (merger,), securities=("S1",))
        last = [o for o in panel.observations if o.security_id == "S1"][-1]
        self.assertEqual(last.total_return, D("60") / D("52.5") - 1)
        self.assertEqual(last.period_end, CAL.session(SESSIONS[6]).close_utc)

    def test_bounds_must_be_sessions_known_by_decision_time(self):
        with self.assertRaises(ValueError):
            self.build(decision=CAL.session(SESSIONS[-1]).close_utc - timedelta(minutes=1))


if __name__ == "__main__":
    unittest.main()
