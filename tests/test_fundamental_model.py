import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from quantos.financial_statements import (
    FilingContext,
    FinancialLineItem,
    PeriodKind,
    StatementPeriod,
    StatementType,
    build_statement,
)
from quantos.fundamental_model import (
    ModelRunStore,
    MultiPeriodProjectionEngine,
    ProjectionPlanPeriod,
)
from quantos.fundamental_schedules import (
    ModelAssumption,
    ProjectionPeriod,
    build_assumption_set,
)


UTC = timezone.utc
ARTIFACT = "sha256:" + "c" * 64


def item(key, value):
    return FinancialLineItem(
        key=key,
        value=Decimal(value),
        source_artifact_ids=(ARTIFACT,),
        source_locator=f"XBRL:{key}",
    )


def source_statements():
    filing = FilingContext(
        accession_id="0000000000-26-000003",
        form_type="10-K",
        filed_date=date(2026, 2, 15),
        accepted_at=datetime(2026, 2, 15, 16, tzinfo=UTC),
        knowledge_time=datetime(2026, 2, 15, 16, 1, tzinfo=UTC),
        source_artifact_ids=(ARTIFACT,),
    )
    bs = build_statement(
        entity_id="CIK:0000000000",
        statement_type=StatementType.BALANCE_SHEET,
        currency="USD",
        period=StatementPeriod(
            PeriodKind.INSTANT,
            None,
            date(2025, 12, 31),
            2025,
            "FY",
        ),
        filing=filing,
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
    inc = build_statement(
        entity_id=bs.entity_id,
        statement_type=StatementType.INCOME_STATEMENT,
        currency="USD",
        period=StatementPeriod(
            PeriodKind.DURATION,
            date(2025, 1, 1),
            date(2025, 12, 31),
            2025,
            "FY",
        ),
        filing=filing,
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
    cf = build_statement(
        entity_id=bs.entity_id,
        statement_type=StatementType.CASH_FLOW,
        currency="USD",
        period=inc.period,
        filing=filing,
        items=(
            item("beginning_cash", "10"),
            item("cash_from_operating_activities", "20"),
            item("cash_from_investing_activities", "-8"),
            item("cash_from_financing_activities", "-2"),
            item("net_change_in_cash", "10"),
            item("ending_cash", "20"),
        ),
    )
    return (bs, inc, cf)


def assumption_set(growth):
    values = {
        "revenue_growth": str(growth),
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
    return build_assumption_set(
        tuple(
            ModelAssumption(
                name=name,
                value=Decimal(value),
                rationale=f"Basis for {name}",
                evidence_references=("claim:test",),
            )
            for name, value in values.items()
        )
    )


def plan(growths=("0.10", "0.08", "0.06")):
    years = (2026, 2027, 2028)
    return tuple(
        ProjectionPlanPeriod(
            period=ProjectionPeriod(
                start_date=date(year, 1, 1),
                end_date=date(year, 12, 31),
                fiscal_year=year,
                fiscal_period="FY",
            ),
            assumptions=assumption_set(growth),
        )
        for year, growth in zip(years, growths, strict=True)
    )


class MultiPeriodFundamentalModelTests(unittest.TestCase):
    def test_multi_period_rollforward_preserves_parent_lineage(self):
        run = MultiPeriodProjectionEngine().project(
            source_statements=source_statements(),
            plan=plan(),
        )
        self.assertEqual(len(run.projections), 3)
        self.assertIsNone(run.projections[0].parent_projection_id)
        self.assertEqual(
            run.projections[1].parent_projection_id,
            run.projections[0].projection_id,
        )
        self.assertEqual(
            run.projections[2].parent_projection_id,
            run.projections[1].projection_id,
        )
        run.require_valid()

    def test_each_year_opens_from_prior_projected_balance_sheet(self):
        run = MultiPeriodProjectionEngine().project(
            source_statements=source_statements(),
            plan=plan(),
        )
        first_cash = run.projections[0].cash_flow.values()["ending_cash"]
        second_begin = run.projections[1].cash_flow.values()["beginning_cash"]
        self.assertEqual(first_cash, second_begin)
        first_debt = run.projections[0].balance_sheet.values()["total_debt"]
        second_debt = run.projections[1].balance_sheet.values()["total_debt"]
        self.assertEqual(second_debt, first_debt + Decimal("2"))

    def test_non_contiguous_plan_fails_closed(self):
        bad = list(plan())
        bad[1] = ProjectionPlanPeriod(
            period=ProjectionPeriod(
                start_date=date(2027, 1, 2),
                end_date=date(2027, 12, 31),
                fiscal_year=2027,
                fiscal_period="FY",
            ),
            assumptions=bad[1].assumptions,
        )
        with self.assertRaises(ValueError):
            MultiPeriodProjectionEngine().project(
                source_statements=source_statements(),
                plan=tuple(bad),
            )

    def test_changed_middle_year_changes_middle_and_later_projection(self):
        engine = MultiPeriodProjectionEngine()
        base = engine.project(
            source_statements=source_statements(),
            plan=plan(),
        )
        changed = engine.project(
            source_statements=source_statements(),
            plan=plan(("0.10", "0.12", "0.06")),
        )
        self.assertEqual(
            base.projections[0].projection_id,
            changed.projections[0].projection_id,
        )
        self.assertNotEqual(
            base.projections[1].projection_id,
            changed.projections[1].projection_id,
        )
        self.assertNotEqual(
            base.projections[2].projection_id,
            changed.projections[2].projection_id,
        )
        self.assertNotEqual(base.model_run_id, changed.model_run_id)

    def test_run_comparison_reports_explicit_metric_deltas(self):
        engine = MultiPeriodProjectionEngine()
        left = engine.project(
            source_statements=source_statements(),
            plan=plan(),
        )
        right = engine.project(
            source_statements=source_statements(),
            plan=plan(("0.11", "0.08", "0.06")),
        )
        comparison = engine.compare(left, right)
        self.assertEqual(len(comparison.deltas), 18)
        revenue_2026 = next(
            item
            for item in comparison.deltas
            if item.fiscal_year == 2026 and item.metric == "revenue"
        )
        self.assertGreater(revenue_2026.delta, 0)

    def test_model_run_manifest_store_is_idempotent(self):
        run = MultiPeriodProjectionEngine().project(
            source_statements=source_statements(),
            plan=plan(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = ModelRunStore(Path(tmp) / "models.duckdb")
            self.assertTrue(store.add(run))
            self.assertFalse(store.add(run))
            manifest = store.get_manifest(run.model_run_id)
            self.assertEqual(manifest["model_run_id"], run.model_run_id)
            self.assertEqual(len(manifest["projections"]), 3)
            self.assertEqual(
                manifest["projections"][1]["parent_projection_id"],
                run.projections[0].projection_id,
            )
            store.close()


if __name__ == "__main__":
    unittest.main()
