import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from quantos.financial_statements import (
    FilingContext,
    FinancialLineItem,
    PeriodKind,
    StatementPeriod,
    StatementType,
    build_statement,
)
from quantos.fundamental_schedules import (
    ModelAssumption,
    ProjectionPeriod,
    ThreeStatementProjectionEngine,
    build_assumption_set,
)
from quantos.models import EpistemicState


UTC = timezone.utc
ARTIFACT = "sha256:" + "b" * 64


def filing():
    return FilingContext(
        accession_id="0000000000-26-000002",
        form_type="10-K",
        filed_date=date(2026, 2, 15),
        accepted_at=datetime(2026, 2, 15, 16, tzinfo=UTC),
        knowledge_time=datetime(2026, 2, 15, 16, 1, tzinfo=UTC),
        source_artifact_ids=(ARTIFACT,),
    )


def item(key, value):
    return FinancialLineItem(
        key=key,
        value=Decimal(value),
        source_artifact_ids=(ARTIFACT,),
        source_locator=f"XBRL:{key}",
    )


def source_statements():
    bs = build_statement(
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
            item("cash_and_cash_equivalents", "20"),
            item("accounts_receivable", "15"),
            item("inventory", "10"),
            item("property_plant_equipment", "40"),
            item("other_assets", "15"),
            item("total_assets", "100"),
            item("accounts_payable", "12"),
            item("total_debt", "30"),
            item("other_liabilities", "18"),
            item("total_liabilities", "60"),
            item("retained_earnings", "25"),
            item("other_equity", "15"),
            item("total_equity", "40"),
        ),
    )
    income = build_statement(
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
            item("cost_of_revenue", "60"),
            item("gross_profit", "40"),
            item("operating_expenses", "20"),
            item("operating_income", "20"),
            item("pretax_income", "17"),
            item("income_tax_expense", "3"),
            item("net_income", "14"),
        ),
    )
    cash = build_statement(
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
            item("beginning_cash", "10"),
            item("cash_from_operating_activities", "20"),
            item("cash_from_investing_activities", "-8"),
            item("cash_from_financing_activities", "-2"),
            item("net_change_in_cash", "10"),
            item("ending_cash", "20"),
        ),
    )
    return (bs, income, cash)


def assumptions(**overrides):
    values = {
        "revenue_growth": "0.10",
        "gross_margin": "0.42",
        "operating_expense_ratio": "0.20",
        "capex_percent_revenue": "0.08",
        "depreciation_percent_beginning_ppe": "0.10",
        "ar_days": "50",
        "inventory_days": "60",
        "ap_days": "45",
        "new_borrowing": "5",
        "debt_repayment": "3",
        "interest_rate": "0.06",
        "tax_rate": "0.21",
        "dividend_payout_ratio": "0.20",
    }
    values.update(overrides)
    items = tuple(
        ModelAssumption(
            name=name,
            value=Decimal(value),
            rationale=f"Reviewed modeling basis for {name}.",
            evidence_references=("claim:test",),
            epistemic_state=EpistemicState.ESTIMATED,
        )
        for name, value in values.items()
    )
    return build_assumption_set(items)


PERIOD = ProjectionPeriod(
    start_date=date(2026, 1, 1),
    end_date=date(2026, 12, 31),
    fiscal_year=2026,
    fiscal_period="FY",
)


class FundamentalScheduleTests(unittest.TestCase):
    def test_assumption_requires_rationale_and_evidence(self):
        with self.assertRaises(ValueError):
            ModelAssumption(
                name="growth",
                value=Decimal("0.1"),
                rationale="",
                evidence_references=(),
            )

    def test_projection_requires_valid_historical_three_statement_set(self):
        bs, income, cash = source_statements()
        broken_bs = build_statement(
            entity_id=bs.entity_id,
            statement_type=bs.statement_type,
            currency=bs.currency,
            period=bs.period,
            filing=bs.filing,
            items=tuple(
                item("total_assets", "101") if line.key == "total_assets" else line
                for line in bs.items
            ),
        )
        with self.assertRaises(ValueError):
            ThreeStatementProjectionEngine().project(
                source_statements=(broken_bs, income, cash),
                assumptions=assumptions(),
                period=PERIOD,
            )

    def test_projection_is_deterministic_and_balances(self):
        engine = ThreeStatementProjectionEngine()
        first = engine.project(
            source_statements=source_statements(),
            assumptions=assumptions(),
            period=PERIOD,
        )
        second = engine.project(
            source_statements=source_statements(),
            assumptions=assumptions(),
            period=PERIOD,
        )
        self.assertEqual(first.projection_id, second.projection_id)
        self.assertTrue(first.is_valid)
        first.require_valid()
        bs = first.balance_sheet.values()
        cf = first.cash_flow.values()
        self.assertEqual(
            bs["total_assets"],
            bs["total_liabilities"] + bs["total_equity"],
        )
        self.assertEqual(bs["cash_and_cash_equivalents"], cf["ending_cash"])

    def test_projection_outputs_are_explicitly_estimated(self):
        run = ThreeStatementProjectionEngine().project(
            source_statements=source_statements(),
            assumptions=assumptions(),
            period=PERIOD,
        )
        all_items = (
            run.income_statement.items
            + run.balance_sheet.items
            + run.cash_flow.items
        )
        self.assertTrue(
            all(
                item.epistemic_state is EpistemicState.ESTIMATED
                for item in all_items
            )
        )
        self.assertTrue(all(item.derived_from for item in all_items))

    def test_changed_assumption_changes_projection_identity(self):
        engine = ThreeStatementProjectionEngine()
        base = engine.project(
            source_statements=source_statements(),
            assumptions=assumptions(),
            period=PERIOD,
        )
        changed = engine.project(
            source_statements=source_statements(),
            assumptions=assumptions(revenue_growth="0.12"),
            period=PERIOD,
        )
        self.assertNotEqual(base.projection_id, changed.projection_id)
        self.assertNotEqual(
            base.income_statement.values()["revenue"],
            changed.income_statement.values()["revenue"],
        )

    def test_debt_schedule_cannot_repay_more_than_available(self):
        with self.assertRaises(ValueError):
            ThreeStatementProjectionEngine().project(
                source_statements=source_statements(),
                assumptions=assumptions(
                    new_borrowing="0",
                    debt_repayment="31",
                ),
                period=PERIOD,
            )

    def test_unknown_assumption_fails_closed(self):
        base = list(assumptions().assumptions)
        base.append(
            ModelAssumption(
                name="magic_alpha",
                value=Decimal("1"),
                rationale="Should not be silently accepted.",
                evidence_references=("claim:test",),
            )
        )
        with self.assertRaises(ValueError):
            ThreeStatementProjectionEngine().project(
                source_statements=source_statements(),
                assumptions=build_assumption_set(tuple(base)),
                period=PERIOD,
            )


if __name__ == "__main__":
    unittest.main()
