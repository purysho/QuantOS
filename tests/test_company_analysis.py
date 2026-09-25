import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

from quantos.company_analysis import (
    AnalysisError,
    CompanyAnalysisStore,
    _Fact,
    analyze_company,
    render,
    store_statements,
)
from quantos.financial_statements import FinancialStatementStore, StatementType
from quantos.keyless import filing_knowledge_time

ART = "sha256:" + "a" * 64
CIK = 320193


def fact(concept, value, end, *, start=None, accession="0001-24-1", filed=date(2024, 11, 1), unit="USD", form="10-K"):
    return _Fact(concept, unit, D(str(value)), start, end, form, accession, filed, filing_knowledge_time(filed), ART)


def year(end: date, accession: str, filed: date, scale: int = 1, *, extra=()):
    start = end.replace(year=end.year - 1) + timedelta(days=1)
    rows = [
        # income statement
        ("RevenueFromContractWithCustomerExcludingAssessedTax", 1000 * scale, start),
        ("CostOfGoodsAndServicesSold", 600 * scale, start),
        ("GrossProfit", 400 * scale, start),
        ("OperatingExpenses", 150 * scale, start),
        ("OperatingIncomeLoss", 250 * scale, start),
        ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest", 240 * scale, start),
        ("IncomeTaxExpenseBenefit", 40 * scale, start),
        ("NetIncomeLoss", 200 * scale, start),
        # cash flow
        ("NetCashProvidedByUsedInOperatingActivities", 300 * scale, start),
        ("NetCashProvidedByUsedInInvestingActivities", -100 * scale, start),
        ("NetCashProvidedByUsedInFinancingActivities", -150 * scale, start),
        ("PaymentsToAcquirePropertyPlantAndEquipment", 80 * scale, start),
        ("EffectOfExchangeRateOnCashAndCashEquivalents", -10 * scale, start),
        ("CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect", 40 * scale, start),
        # balance sheet
        ("Assets", 5000 * scale, None),
        ("AssetsCurrent", 1500 * scale, None),
        ("LiabilitiesCurrent", 1000 * scale, None),
        ("Liabilities", 3000 * scale, None),
        ("StockholdersEquity", 2000 * scale, None),
        ("CashAndCashEquivalentsAtCarryingValue", 540 * scale, None),
        ("CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", 540 * scale, None),
        ("LongTermDebtNoncurrent", 800 * scale, None),
        *extra,
    ]
    facts = [fact(c, v, end, start=s, accession=accession, filed=filed) for c, v, s in rows]
    # opening cash balance (prior year end) and a prior-year comparative
    facts.append(fact("CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", 500 * scale, start - timedelta(days=1),
                      accession=accession, filed=filed))
    facts.append(fact("EarningsPerShareDiluted", 2.5 * scale, end, start=start, accession=accession, filed=filed, unit="USD/shares"))
    return facts


class AnalysisTests(unittest.TestCase):
    def facts(self):
        return tuple(year(date(2023, 9, 30), "0001-23-1", date(2023, 11, 3))
                     + year(date(2024, 9, 28), "0001-24-1", date(2024, 11, 1), scale=2))

    def test_statements_validate_with_fx_effect_and_metrics_are_derived(self):
        analysis = analyze_company(cik=CIK, ticker="aapl", facts=self.facts(), known_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
        self.assertEqual([r.fiscal_year_end for r in analysis.reports], [date(2023, 9, 30), date(2024, 9, 28)])
        for report in analysis.reports:
            self.assertEqual({s.statement_type for s in report.statements}, set(StatementType))
            self.assertEqual([i for i in report.issues if i[0] == "ERROR"], [], report.issues)
        latest = analysis.metrics[-1]
        self.assertAlmostEqual(latest["revenue_growth"], 1.0)
        self.assertAlmostEqual(latest["gross_margin"], 0.4)
        self.assertAlmostEqual(latest["free_cash_flow"], 440.0)
        self.assertAlmostEqual(latest["return_on_equity"], 0.1)
        self.assertEqual(latest["eps_diluted"], 5.0)
        income = next(s for s in analysis.reports[-1].statements if s.statement_type is StatementType.INCOME_STATEMENT)
        locators = {i.key: i.source_locator for i in income.items}
        self.assertTrue(locators["revenue"].startswith("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax 0001-24-1"))
        self.assertIn("AAPL", render(analysis))

    def test_point_in_time_uses_only_known_filings(self):
        analysis = analyze_company(cik=CIK, ticker="AAPL", facts=tuple(f for f in self.facts() if f.filed <= date(2024, 1, 1)),
                                   known_at=datetime(2024, 1, 1, tzinfo=timezone.utc))
        self.assertEqual([r.fiscal_year_end for r in analysis.reports], [date(2023, 9, 30)])

    def test_as_originally_filed_ignores_later_restated_comparatives(self):
        restated = [fact("RevenueFromContractWithCustomerExcludingAssessedTax", 999, date(2023, 9, 30),
                         start=date(2022, 10, 1), accession="0001-24-1", filed=date(2024, 11, 1)),
                    fact("Assets", 4999, date(2023, 9, 30), accession="0001-24-1", filed=date(2024, 11, 1))]
        analysis = analyze_company(cik=CIK, ticker="AAPL", facts=self.facts() + tuple(restated),
                                   known_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(analysis.metrics[0]["revenue"], 1000.0, "FY2023 as first filed, not the restated comparative")

    def test_identity_breaks_are_reported_not_forced(self):
        facts = [f for f in year(date(2024, 9, 28), "0001-24-1", date(2024, 11, 1)) if f.concept != "Liabilities"]
        facts.append(fact("Liabilities", 9000, date(2024, 9, 28), accession="0001-24-1"))  # 5000 != 9000 + 2000
        analysis = analyze_company(cik=CIK, ticker="AAPL", facts=tuple(facts), known_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
        codes = {code for _, code, _ in analysis.reports[0].issues}
        self.assertIn("BALANCE_SHEET_DOES_NOT_BALANCE", codes)
        self.assertEqual(analysis.metrics[0]["validation_errors"], 1)

    def test_temporary_equity_and_noncontrolling_interest_reconcile(self):
        extra = (("RedeemableNoncontrollingInterestEquityCommonCarryingAmount", 100, None),)
        facts = [f for f in year(date(2024, 12, 31), "0002-25-1", date(2025, 2, 24), extra=extra)
                 if f.concept != "StockholdersEquity"]
        start = date(2024, 1, 1)
        facts += [fact("StockholdersEquity", 1900, date(2024, 12, 31), accession="0002-25-1", filed=date(2025, 2, 24)),
                  fact("ProfitLoss", 200, date(2024, 12, 31), start=start, accession="0002-25-1", filed=date(2025, 2, 24))]
        facts = [f for f in facts if f.concept != "NetIncomeLoss"] + [
            fact("NetIncomeLoss", 190, date(2024, 12, 31), start=start, accession="0002-25-1", filed=date(2025, 2, 24))]
        analysis = analyze_company(cik=CIK, ticker="BRK", facts=tuple(facts), known_at=datetime(2025, 6, 1, tzinfo=timezone.utc))
        self.assertEqual([i for i in analysis.reports[0].issues if i[0] == "ERROR"], [])

    def test_no_10k_balance_sheet_fails_closed(self):
        with self.assertRaises(AnalysisError):
            analyze_company(cik=CIK, ticker="SPY", facts=(), known_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
        quarterly = tuple(fact("Assets", 1, date(2024, 6, 29), form="10-Q") for _ in range(1))
        with self.assertRaises(AnalysisError):
            analyze_company(cik=CIK, ticker="X", facts=quarterly, known_at=datetime(2025, 1, 1, tzinfo=timezone.utc))

    def test_stores_are_idempotent_and_replace_older_analyses(self):
        analysis = analyze_company(cik=CIK, ticker="AAPL", facts=self.facts(), known_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
        later = analyze_company(cik=CIK, ticker="AAPL", facts=self.facts(), known_at=datetime(2025, 2, 1, tzinfo=timezone.utc))
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(store_statements(Path(tmp) / "s.duckdb", analysis), 6)
            self.assertEqual(store_statements(Path(tmp) / "s.duckdb", analysis), 0)
            store = CompanyAnalysisStore(Path(tmp) / "a.duckdb")
            try:
                self.assertTrue(store.add(analysis))
                self.assertFalse(store.add(analysis))
                self.assertTrue(store.add(later))
                rows = store._con.execute("SELECT count(*), max(known_at) FROM company_metrics").fetchone()
                self.assertEqual(rows[0], 2)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
