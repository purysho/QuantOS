import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from quantos.corporate_actions import (
    AdjustmentMode,
    CorporateActionEngine,
    CorporateActionEvent,
    CorporateActionKind,
    CorporateActionLedger,
    PositionLot,
    ReturnIssueKind,
    corporate_action_identity,
)

UTC = timezone.utc
K = datetime(2024, 1, 2, tzinfo=UTC)
NOW = datetime(2026, 9, 25, tzinfo=UTC)
D = Decimal


def action(key, kind, ex, known=K, **kw):
    return CorporateActionEvent(
        record_key=key,
        security_id="S1",
        kind=kind,
        announced_at=known,
        knowledge_time=known,
        ex_date=ex,
        evidence_references=("8-K:evidence",),
        **kw,
    )


SPLIT = action("split", CorporateActionKind.STOCK_SPLIT, date(2024, 3, 4), ratio_new=D("2"), ratio_old=D("1"))
DIV = action("div", CorporateActionKind.CASH_DIVIDEND, date(2024, 2, 5), cash_amount=D("1.00"), currency="USD")
PRICES = {
    date(2024, 2, 1): D("100"),
    date(2024, 2, 2): D("100"),
    date(2024, 2, 5): D("99.5"),
    date(2024, 3, 1): D("110"),
    date(2024, 3, 4): D("56"),
}


class CorporateActionEventTests(unittest.TestCase):
    def test_kind_specific_validation(self):
        with self.assertRaises(ValueError):
            action("x", CorporateActionKind.CASH_DIVIDEND, date(2024, 2, 5), ratio_new=D("1"), ratio_old=D("1"))
        with self.assertRaises(ValueError):
            action("x", CorporateActionKind.STOCK_SPLIT, date(2024, 2, 5), ratio_new=D("1"), ratio_old=D("1"))
        with self.assertRaises(ValueError):
            action("x", CorporateActionKind.SPINOFF, date(2024, 2, 5), ratio_new=D("1"), ratio_old=D("4"))
        with self.assertRaises(ValueError):
            action("x", CorporateActionKind.CASH_MERGER, date(2024, 2, 5))
        with self.assertRaises(ValueError):
            action("x", CorporateActionKind.CASH_DIVIDEND, date(2024, 2, 5), cash_amount=D("1"))
        self.assertEqual(
            action("x", CorporateActionKind.STOCK_DIVIDEND, date(2024, 2, 5), ratio_new=D("1"), ratio_old=D("10")).share_multiplier,
            D("1.1"),
        )


class LedgerTests(unittest.TestCase):
    def test_bitemporal_supersession_and_retraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = CorporateActionLedger(Path(tmp) / "ca.duckdb")
            self.assertTrue(ledger.add(DIV))
            self.assertFalse(ledger.add(DIV))
            corrected = action("div", CorporateActionKind.CASH_DIVIDEND, date(2024, 2, 5), known=datetime(2024, 1, 20, tzinfo=UTC), cash_amount=D("1.10"), currency="USD")
            ledger.add(corrected)
            self.assertEqual(ledger.actions(security_id="S1", known_at=datetime(2024, 1, 10, tzinfo=UTC))[0].cash_amount, D("1.00"))
            self.assertEqual(ledger.actions(security_id="S1", known_at=NOW)[0].cash_amount, D("1.10"))
            self.assertEqual(ledger.actions(security_id="S1", known_at=datetime(2023, 1, 1, tzinfo=UTC)), ())
            retraction = action("div", CorporateActionKind.CASH_DIVIDEND, date(2024, 2, 5), known=datetime(2024, 2, 1, tzinfo=UTC), cash_amount=D("1.10"), currency="USD", retracted=True)
            ledger.add(retraction)
            self.assertEqual(ledger.actions(security_id="S1", known_at=NOW), ())
            with self.assertRaisesRegex(ValueError, "share a knowledge time"):
                ledger.add(action("div", CorporateActionKind.CASH_DIVIDEND, date(2024, 2, 6), cash_amount=D("2"), currency="USD"))
            ledger.close()


class AdjustmentTests(unittest.TestCase):
    def test_split_only_factors(self):
        series = CorporateActionEngine().adjustment_factors(
            security_id="S1", actions=(DIV, SPLIT), known_at=NOW, mode=AdjustmentMode.SPLIT_ONLY
        )
        self.assertEqual(len(series.factors), 1)
        self.assertEqual(series.cumulative_factor(date(2024, 3, 1)), D("0.5"))
        self.assertEqual(series.cumulative_factor(date(2024, 3, 4)), D("1"))
        adjusted = series.adjust(PRICES)
        self.assertEqual(adjusted[date(2024, 3, 1)], D("55.0"))

    def test_total_return_factors_use_prior_close(self):
        series = CorporateActionEngine().adjustment_factors(
            security_id="S1", actions=(DIV, SPLIT), known_at=NOW, mode=AdjustmentMode.TOTAL_RETURN, prices=PRICES
        )
        self.assertEqual(series.cumulative_factor(date(2024, 2, 2)), D("0.99") * D("0.5"))

    def test_total_return_factor_without_prior_close_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "close before"):
            CorporateActionEngine().adjustment_factors(
                security_id="S1", actions=(DIV,), known_at=NOW, mode=AdjustmentMode.TOTAL_RETURN, prices={}
            )

    def test_unknown_or_foreign_actions_are_refused(self):
        with self.assertRaisesRegex(ValueError, "not yet known"):
            CorporateActionEngine().adjustment_factors(
                security_id="S1", actions=(DIV,), known_at=datetime(2023, 1, 1, tzinfo=UTC), mode=AdjustmentMode.SPLIT_ONLY
            )


class TotalReturnTests(unittest.TestCase):
    def test_dividend_and_split_returns(self):
        series = CorporateActionEngine().total_returns(
            security_id="S1", actions=(DIV, SPLIT), known_at=NOW, prices=PRICES
        )
        returns = {o.end: o.total_return for o in series.observations}
        self.assertEqual(returns[date(2024, 2, 5)], D("0.005"))
        self.assertEqual(returns[date(2024, 3, 4)], D("112") / D("110") - 1)
        self.assertTrue(series.complete)

    def test_cash_merger_ends_series_and_flags_later_prices(self):
        merger = action("merge", CorporateActionKind.CASH_MERGER, date(2024, 2, 5), cash_amount=D("120"), currency="USD")
        prices = {date(2024, 2, 2): D("100"), date(2024, 2, 5): D("100"), date(2024, 2, 6): D("101")}
        series = CorporateActionEngine().total_returns(
            security_id="S1", actions=(merger,), known_at=NOW, prices=prices
        )
        self.assertEqual(series.observations[-1].total_return, D("0.2"))
        self.assertTrue(series.observations[-1].terminal)
        self.assertEqual([i.kind for i in series.issues], [ReturnIssueKind.PRICE_AFTER_TERMINATION])

    def test_delisting_without_proceeds_is_visible(self):
        delist = action("delist", CorporateActionKind.DELISTING, date(2024, 2, 5))
        series = CorporateActionEngine().total_returns(
            security_id="S1", actions=(delist,), known_at=NOW, prices={date(2024, 2, 2): D("5"), date(2024, 2, 5): D("5")}
        )
        self.assertFalse(series.complete)
        self.assertEqual(series.issues[0].kind, ReturnIssueKind.MISSING_DELISTING_PROCEEDS)
        self.assertEqual(series.observations, ())

    def test_stock_merger_and_spinoff_need_counterparty_prices(self):
        spin = action("spin", CorporateActionKind.SPINOFF, date(2024, 2, 5), ratio_new=D("1"), ratio_old=D("2"), counterparty_security_id="CHILD")
        prices = {date(2024, 2, 2): D("100"), date(2024, 2, 5): D("90")}
        engine = CorporateActionEngine()
        missing = engine.total_returns(security_id="S1", actions=(spin,), known_at=NOW, prices=prices)
        self.assertEqual(missing.issues[0].kind, ReturnIssueKind.MISSING_COUNTERPARTY_PRICE)
        priced = engine.total_returns(
            security_id="S1", actions=(spin,), known_at=NOW, prices=prices,
            counterparty_prices={("CHILD", date(2024, 2, 5)): D("20")},
        )
        self.assertEqual(priced.observations[0].total_return, D("0"))
        merger = action("stock-merge", CorporateActionKind.STOCK_MERGER, date(2024, 2, 5), ratio_new=D("3"), ratio_old=D("2"), counterparty_security_id="ACQ", cash_amount=D("5"), currency="USD")
        result = engine.total_returns(
            security_id="S1", actions=(merger,), known_at=NOW, prices=prices,
            counterparty_prices={("ACQ", date(2024, 2, 5)): D("70")},
        )
        self.assertEqual(result.observations[0].total_return, D("0.1"))

    def test_split_and_distribution_in_one_window_is_ambiguous(self):
        prices = {date(2024, 2, 1): D("100"), date(2024, 3, 4): D("50")}
        series = CorporateActionEngine().total_returns(
            security_id="S1", actions=(DIV, SPLIT), known_at=NOW, prices=prices
        )
        self.assertEqual(series.issues[0].kind, ReturnIssueKind.AMBIGUOUS_SAME_WINDOW_ACTIONS)


class PositionTests(unittest.TestCase):
    def test_split_dividend_and_cash_merger(self):
        merger = action("merge", CorporateActionKind.CASH_MERGER, date(2024, 6, 3), cash_amount=D("60"), currency="USD")
        outcome = CorporateActionEngine().apply_to_position(
            lot=PositionLot("S1", D("15"), D("1500")), actions=(DIV, SPLIT, merger), known_at=NOW, through=date(2024, 12, 31)
        )
        self.assertEqual(outcome.cash, D("15") + D("30") * D("60"))
        self.assertEqual(outcome.lots, ())
        self.assertTrue(outcome.complete)

    def test_reverse_split_reports_fractional_remainder(self):
        reverse = action("rsplit", CorporateActionKind.STOCK_SPLIT, date(2024, 3, 4), ratio_new=D("1"), ratio_old=D("10"))
        outcome = CorporateActionEngine().apply_to_position(
            lot=PositionLot("S1", D("15"), D("1500")), actions=(reverse,), known_at=NOW, through=date(2024, 12, 31)
        )
        self.assertEqual(outcome.lots[0].quantity, D("1"))
        self.assertEqual(outcome.fractional_remainders, (("S1", D("0.5")),))

    def test_spinoff_requires_evidenced_basis_allocation(self):
        spin = action("spin", CorporateActionKind.SPINOFF, date(2024, 2, 5), ratio_new=D("1"), ratio_old=D("2"), counterparty_security_id="CHILD")
        engine = CorporateActionEngine()
        missing = engine.apply_to_position(
            lot=PositionLot("S1", D("10"), D("1000")), actions=(spin,), known_at=NOW, through=date(2024, 12, 31)
        )
        self.assertFalse(missing.complete)
        allocated = engine.apply_to_position(
            lot=PositionLot("S1", D("10"), D("1000")), actions=(spin,), known_at=NOW, through=date(2024, 12, 31),
            spinoff_basis_fraction={corporate_action_identity(spin): D("0.2")},
        )
        self.assertEqual(
            allocated.lots,
            (PositionLot("S1", D("10"), D("800.0")), PositionLot("CHILD", D("5"), D("200.0"))),
        )

    def test_delisting_without_proceeds_leaves_position_unresolved(self):
        delist = action("delist", CorporateActionKind.DELISTING, date(2024, 2, 5))
        outcome = CorporateActionEngine().apply_to_position(
            lot=PositionLot("S1", D("10"), D("1000")), actions=(delist,), known_at=NOW, through=date(2024, 12, 31)
        )
        self.assertEqual(outcome.unresolved_security_ids, ("S1",))
        self.assertEqual(outcome.lots[0].quantity, D("10"))
        self.assertFalse(outcome.complete)

    def test_actions_after_through_date_are_not_applied(self):
        outcome = CorporateActionEngine().apply_to_position(
            lot=PositionLot("S1", D("10"), D("1000")), actions=(DIV, SPLIT), known_at=NOW, through=date(2024, 2, 29)
        )
        self.assertEqual(outcome.lots[0].quantity, D("10"))
        self.assertEqual(outcome.cash, D("10.00"))


if __name__ == "__main__":
    unittest.main()
