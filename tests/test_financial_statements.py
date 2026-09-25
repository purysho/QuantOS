import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from quantos.financial_statements import (
    AccountingValidationError,
    FilingContext,
    FinancialLineItem,
    FinancialStatementStore,
    FinancialStatementValidator,
    PeriodKind,
    StatementPeriod,
    StatementType,
    build_statement,
)


UTC = timezone.utc
ARTIFACT = "sha256:" + "a" * 64


def filing():
    return FilingContext(
        accession_id="0000000000-26-000001",
        form_type="10-K",
        filed_date=date(2026, 2, 15),
        accepted_at=datetime(2026, 2, 15, 16, tzinfo=UTC),
        knowledge_time=datetime(2026, 2, 15, 16, 1, tzinfo=UTC),
        source_artifact_ids=(ARTIFACT,),
    )


def item(key, value, locator=None):
    return FinancialLineItem(
        key=key,
        value=Decimal(value),
        source_artifact_ids=(ARTIFACT,),
        source_locator=locator or f"XBRL:{key}",
    )


def balance_sheet(cash="30", assets="100", liabilities="60", equity="40"):
    return build_statement(
        entity_id="CIK:0000000000",
        statement_type=StatementType.BALANCE_SHEET,
        currency="USD",
        period=StatementPeriod(
            period_kind=PeriodKind.INSTANT,
            start_date=None,
            end_date=date(2025, 12, 31),
            fiscal_year=2025,
            fiscal_period="FY",
        ),
        filing=filing(),
        items=(
            item("cash_and_cash_equivalents", cash),
            item("total_assets", assets),
            item("total_liabilities", liabilities),
            item("total_equity", equity),
        ),
    )


def income_statement():
    return build_statement(
        entity_id="CIK:0000000000",
        statement_type=StatementType.INCOME_STATEMENT,
        currency="USD",
        period=StatementPeriod(
            period_kind=PeriodKind.DURATION,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            fiscal_year=2025,
            fiscal_period="FY",
        ),
        filing=filing(),
        items=(
            item("revenue", "100"),
            item("cost_of_revenue", "40"),
            item("gross_profit", "60"),
            item("operating_expenses", "20"),
            item("operating_income", "40"),
            item("pretax_income", "35"),
            item("income_tax_expense", "7"),
            item("net_income", "28"),
        ),
    )


def cash_flow(ending="30"):
    return build_statement(
        entity_id="CIK:0000000000",
        statement_type=StatementType.CASH_FLOW,
        currency="USD",
        period=StatementPeriod(
            period_kind=PeriodKind.DURATION,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            fiscal_year=2025,
            fiscal_period="FY",
        ),
        filing=filing(),
        items=(
            item("beginning_cash", "20"),
            item("cash_from_operating_activities", "18"),
            item("cash_from_investing_activities", "-5"),
            item("cash_from_financing_activities", "-3"),
            item("net_change_in_cash", "10"),
            item("ending_cash", ending),
        ),
    )


class FinancialStatementTests(unittest.TestCase):
    def test_knowledge_time_cannot_precede_acceptance(self):
        with self.assertRaises(ValueError):
            FilingContext(
                accession_id="x",
                form_type="10-K",
                filed_date=date(2026, 2, 15),
                accepted_at=datetime(2026, 2, 15, 16, tzinfo=UTC),
                knowledge_time=datetime(2026, 2, 15, 15, 59, tzinfo=UTC),
                source_artifact_ids=(ARTIFACT,),
            )

    def test_line_item_requires_exact_provenance(self):
        with self.assertRaises(ValueError):
            FinancialLineItem(
                key="revenue",
                value=Decimal("1"),
                source_artifact_ids=(),
                source_locator="",
            )

    def test_statement_identity_is_deterministic(self):
        first = balance_sheet()
        second = balance_sheet()
        self.assertEqual(first.statement_id, second.statement_id)

    def test_balanced_balance_sheet_passes(self):
        report = FinancialStatementValidator().validate(balance_sheet())
        self.assertTrue(report.is_valid)
        report.require_valid()

    def test_unbalanced_balance_sheet_fails_closed(self):
        report = FinancialStatementValidator().validate(
            balance_sheet(assets="101")
        )
        self.assertFalse(report.is_valid)
        codes = {issue.code for issue in report.issues}
        self.assertIn("BALANCE_SHEET_DOES_NOT_BALANCE", codes)
        with self.assertRaises(AccountingValidationError):
            report.require_valid()

    def test_cash_flow_components_and_rollforward_must_tie(self):
        broken = build_statement(
            entity_id="CIK:0000000000",
            statement_type=StatementType.CASH_FLOW,
            currency="USD",
            period=cash_flow().period,
            filing=filing(),
            items=(
                item("beginning_cash", "20"),
                item("cash_from_operating_activities", "18"),
                item("cash_from_investing_activities", "-5"),
                item("cash_from_financing_activities", "-3"),
                item("net_change_in_cash", "11"),
                item("ending_cash", "30"),
            ),
        )
        report = FinancialStatementValidator().validate(broken)
        codes = {issue.code for issue in report.issues}
        self.assertIn("CASH_FLOW_COMPONENTS_DO_NOT_TIE", codes)
        self.assertIn("CASH_ROLL_FORWARD_DOES_NOT_TIE", codes)

    def test_income_statement_available_identities_are_checked(self):
        good = FinancialStatementValidator().validate(income_statement())
        self.assertTrue(good.is_valid)
        broken = build_statement(
            entity_id="CIK:0000000000",
            statement_type=StatementType.INCOME_STATEMENT,
            currency="USD",
            period=income_statement().period,
            filing=filing(),
            items=(
                item("revenue", "100"),
                item("cost_of_revenue", "40"),
                item("gross_profit", "59"),
            ),
        )
        report = FinancialStatementValidator().validate(broken)
        self.assertIn(
            "GROSS_PROFIT_DOES_NOT_TIE",
            {issue.code for issue in report.issues},
        )

    def test_three_statement_set_checks_cross_statement_cash(self):
        validator = FinancialStatementValidator()
        good = validator.validate_three_statement_set(
            (balance_sheet(), income_statement(), cash_flow())
        )
        self.assertTrue(good.is_valid)
        bad = validator.validate_three_statement_set(
            (balance_sheet(cash="31"), income_statement(), cash_flow())
        )
        self.assertFalse(bad.is_valid)
        self.assertIn(
            "CASH_DOES_NOT_TIE_ACROSS_STATEMENTS",
            {issue.code for issue in bad.issues},
        )

    def test_store_is_idempotent_and_point_in_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FinancialStatementStore(Path(tmp) / "statements.duckdb")
            statement = balance_sheet()
            self.assertTrue(store.add(statement))
            self.assertFalse(store.add(statement))
            before = store.as_of(
                entity_id=statement.entity_id,
                statement_type=statement.statement_type,
                as_of=datetime(2026, 2, 15, 16, 0, 30, tzinfo=UTC),
            )
            after = store.as_of(
                entity_id=statement.entity_id,
                statement_type=statement.statement_type,
                as_of=datetime(2026, 2, 15, 16, 2, tzinfo=UTC),
            )
            self.assertEqual(before, ())
            self.assertEqual(after, (statement,))
            self.assertEqual(store.get(statement.statement_id), statement)
            store.close()


if __name__ == "__main__":
    unittest.main()
