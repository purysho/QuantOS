import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from quantos.advanced_fundamental_model import (
    AdvancedFundamentalModelEngine,
    AdvancedProjectionPlanPeriod,
    SharePeriodInputs,
    TaxPeriodInputs,
    build_operating_assumption_set,
)
from quantos.advanced_schedules import (
    CashSweepPolicy,
    DebtTranche,
    InterestRateType,
)
from quantos.financial_statements import (
    FilingContext,
    FinancialLineItem,
    PeriodKind,
    StatementPeriod,
    StatementType,
    build_statement,
)
from quantos.fundamental_schedules import ModelAssumption, ProjectionPeriod


UTC = timezone.utc
ARTIFACT = "sha256:" + "d" * 64


def line(key, value):
    return FinancialLineItem(
        key=key,
        value=Decimal(value),
        source_artifact_ids=(ARTIFACT,),
        source_locator=f"XBRL:{key}",
    )


def source_statements():
    filing = FilingContext(
        accession_id="0000000000-26-000004",
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
            PeriodKind.INSTANT, None, date(2025, 12, 31), 2025, "FY"
        ),
        filing=filing,
        items=(
            line("cash_and_cash_equivalents", "20"),
            line("accounts_receivable", "15"),
            line("inventory", "10"),
            line("property_plant_equipment", "40"),
            line("other_assets", "15"),
            line("total_assets", "100"),
            line("accounts_payable", "12"),
            line("total_debt", "30"),
            line("other_liabilities", "18"),
            line("total_liabilities", "60"),
            line("retained_earnings", "25"),
            line("other_equity", "15"),
            line("total_equity", "40"),
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
            line("revenue", "100"),
            line("cost_of_revenue", "60"),
            line("gross_profit", "40"),
            line("operating_expenses", "20"),
            line("operating_income", "20"),
            line("pretax_income", "17"),
            line("income_tax_expense", "3"),
            line("net_income", "14"),
        ),
    )
    cf = build_statement(
        entity_id=bs.entity_id,
        statement_type=StatementType.CASH_FLOW,
        currency="USD",
        period=inc.period,
        filing=filing,
        items=(
            line("beginning_cash", "10"),
            line("cash_from_operating_activities", "20"),
            line("cash_from_investing_activities", "-8"),
            line("cash_from_financing_activities", "-2"),
            line("net_change_in_cash", "10"),
            line("ending_cash", "20"),
        ),
    )
    return (bs, inc, cf)


def operating(growth="0.10"):
    values = {
        "revenue_growth": growth,
        "gross_margin": "0.42",
        "operating_expense_ratio": "0.20",
        "capex_percent_revenue": "0.08",
        "depreciation_percent_beginning_ppe": "0.10",
        "ar_days": "50",
        "inventory_days": "60",
        "ap_days": "45",
        "dividend_payout_ratio": "0.20",
    }
    return build_operating_assumption_set(
        tuple(
            ModelAssumption(
                name=name,
                value=Decimal(value),
                rationale=f"Reviewed basis for {name}",
                evidence_references=("claim:assumption",),
            )
            for name, value in values.items()
        )
    )


def shares(beginning):
    return SharePeriodInputs(
        beginning_basic_shares=Decimal(beginning),
        issued_shares=Decimal("1"),
        issue_price=Decimal("10"),
        repurchased_shares=Decimal("0"),
        repurchase_price=Decimal("10"),
        options_outstanding=Decimal("5"),
        option_strike=Decimal("8"),
        average_market_price=Decimal("20"),
        restricted_units=Decimal("1"),
        evidence_references=("artifact:equity-note",),
    )


def tranche(balance, *, new="0", maturity=date(2030, 12, 31)):
    return DebtTranche(
        tranche_id="term-a",
        beginning_balance=Decimal(balance),
        rate_type=InterestRateType.FIXED,
        maturity_date=maturity,
        scheduled_repayment=Decimal("2"),
        new_borrowing=Decimal(new),
        fixed_rate=Decimal("0.05"),
        evidence_references=("artifact:debt-note",),
    )


def first_plan():
    return AdvancedProjectionPlanPeriod(
        period=ProjectionPeriod(
            date(2026, 1, 1), date(2026, 12, 31), 2026, "FY"
        ),
        operating_assumptions=operating(),
        debt_tranches=(tranche("30"),),
        floating_base_rate=Decimal("0.04"),
        tax=TaxPeriodInputs(
            opening_nol=Decimal("10"),
            statutory_tax_rate=Decimal("0.25"),
            nol_utilization_limit=Decimal("0.80"),
            evidence_references=("claim:tax",),
        ),
        cash_sweep_policy=CashSweepPolicy(
            minimum_cash=Decimal("10"),
            sweep_percent=Decimal("0.50"),
            tranche_priority=("term-a",),
        ),
        shares=shares("100"),
    )


class AdvancedFundamentalModelTests(unittest.TestCase):
    def test_first_period_reconciles_tranche_debt_to_historical_debt(self):
        step = first_plan()
        bad = AdvancedProjectionPlanPeriod(
            period=step.period,
            operating_assumptions=step.operating_assumptions,
            debt_tranches=(tranche("29"),),
            floating_base_rate=step.floating_base_rate,
            tax=step.tax,
            cash_sweep_policy=step.cash_sweep_policy,
            shares=step.shares,
        )
        with self.assertRaises(ValueError):
            AdvancedFundamentalModelEngine().project(
                source_statements=source_statements(),
                plan=(bad,),
            )

    def test_advanced_projection_integrates_all_schedule_ids_and_balances(self):
        run = AdvancedFundamentalModelEngine().project(
            source_statements=source_statements(),
            plan=(first_plan(),),
        )
        projection = run.projections[0]
        self.assertTrue(projection.is_valid)
        projection.require_valid()
        refs = set(projection.balance_sheet.items[0].derived_from)
        self.assertIn(projection.debt_schedule.schedule_id, refs)
        self.assertIn(projection.tax_schedule.schedule_id, refs)
        self.assertIn(projection.cash_sweep.schedule_id, refs)
        self.assertIn(projection.share_schedule.schedule_id, refs)
        bs = projection.balance_sheet.values()
        self.assertEqual(
            bs["total_assets"],
            bs["total_liabilities"] + bs["total_equity"],
        )

    def test_nol_reduces_tax_and_rolls_forward(self):
        first = first_plan()
        single = AdvancedFundamentalModelEngine().project(
            source_statements=source_statements(),
            plan=(first,),
        ).projections[0]
        self.assertGreater(single.tax_schedule.nol_utilized, 0)
        second = AdvancedProjectionPlanPeriod(
            period=ProjectionPeriod(
                date(2027, 1, 1), date(2027, 12, 31), 2027, "FY"
            ),
            operating_assumptions=operating("0.08"),
            debt_tranches=(
                tranche(
                    str(single.ending_debt_by_tranche["term-a"])
                ),
            ),
            floating_base_rate=Decimal("0.04"),
            tax=TaxPeriodInputs(
                opening_nol=single.tax_schedule.ending_nol,
                statutory_tax_rate=Decimal("0.25"),
                nol_utilization_limit=Decimal("0.80"),
                evidence_references=("claim:tax",),
            ),
            cash_sweep_policy=first.cash_sweep_policy,
            shares=shares(str(single.share_schedule.ending_basic_shares)),
        )
        run = AdvancedFundamentalModelEngine().project(
            source_statements=source_statements(),
            plan=(first, second),
        )
        self.assertEqual(
            run.projections[1].tax_schedule.opening_nol,
            run.projections[0].tax_schedule.ending_nol,
        )

    def test_cash_sweep_reduces_debt_and_preserves_minimum_cash(self):
        projection = AdvancedFundamentalModelEngine().project(
            source_statements=source_statements(),
            plan=(first_plan(),),
        ).projections[0]
        self.assertGreaterEqual(
            projection.cash_sweep.ending_cash,
            projection.cash_sweep.minimum_cash,
        )
        self.assertEqual(
            projection.balance_sheet.values()["total_debt"],
            sum(projection.ending_debt_by_tranche.values(), Decimal("0")),
        )

    def test_share_schedule_feeds_cash_equity_and_dilution(self):
        projection = AdvancedFundamentalModelEngine().project(
            source_statements=source_statements(),
            plan=(first_plan(),),
        ).projections[0]
        bs = projection.balance_sheet.values()
        cf = projection.cash_flow.values()
        self.assertGreater(bs["diluted_shares"], bs["ending_basic_shares"])
        self.assertEqual(
            cf["net_equity_financing_cash_flow"],
            projection.share_schedule.net_equity_financing_cash_flow,
        )

    def test_next_period_debt_opening_must_match_post_sweep_balance(self):
        first = first_plan()
        first_run = AdvancedFundamentalModelEngine().project(
            source_statements=source_statements(),
            plan=(first,),
        ).projections[0]
        bad_second = AdvancedProjectionPlanPeriod(
            period=ProjectionPeriod(
                date(2027, 1, 1), date(2027, 12, 31), 2027, "FY"
            ),
            operating_assumptions=operating("0.08"),
            debt_tranches=(tranche("999"),),
            floating_base_rate=Decimal("0.04"),
            tax=TaxPeriodInputs(
                opening_nol=first_run.tax_schedule.ending_nol,
                statutory_tax_rate=Decimal("0.25"),
                nol_utilization_limit=Decimal("0.80"),
                evidence_references=("claim:tax",),
            ),
            cash_sweep_policy=first.cash_sweep_policy,
            shares=shares(str(first_run.share_schedule.ending_basic_shares)),
        )
        with self.assertRaises(ValueError):
            AdvancedFundamentalModelEngine().project(
                source_statements=source_statements(),
                plan=(first, bad_second),
            )

    def test_unfunded_maturity_fails_minimum_cash_gate(self):
        base = first_plan()
        matured = AdvancedProjectionPlanPeriod(
            period=base.period,
            operating_assumptions=base.operating_assumptions,
            debt_tranches=(
                tranche("30", maturity=date(2026, 6, 30)),
            ),
            floating_base_rate=base.floating_base_rate,
            tax=base.tax,
            cash_sweep_policy=base.cash_sweep_policy,
            shares=base.shares,
        )
        with self.assertRaises(ValueError):
            AdvancedFundamentalModelEngine().project(
                source_statements=source_statements(),
                plan=(matured,),
            )

    def test_funded_maturity_changes_projection_identity(self):
        base = first_plan()
        standard = AdvancedFundamentalModelEngine().project(
            source_statements=source_statements(),
            plan=(base,),
        )
        replacement_facility = DebtTranche(
            tranche_id="rcf",
            beginning_balance=Decimal("0"),
            rate_type=InterestRateType.FLOATING,
            maturity_date=date(2030, 12, 31),
            scheduled_repayment=Decimal("0"),
            new_borrowing=Decimal("20"),
            spread=Decimal("0.02"),
            evidence_references=("artifact:replacement-facility",),
        )
        matured = AdvancedProjectionPlanPeriod(
            period=base.period,
            operating_assumptions=base.operating_assumptions,
            debt_tranches=(
                tranche("30", maturity=date(2026, 6, 30)),
                replacement_facility,
            ),
            floating_base_rate=base.floating_base_rate,
            tax=base.tax,
            cash_sweep_policy=CashSweepPolicy(
                minimum_cash=base.cash_sweep_policy.minimum_cash,
                sweep_percent=base.cash_sweep_policy.sweep_percent,
                tranche_priority=("rcf", "term-a"),
            ),
            shares=base.shares,
        )
        changed = AdvancedFundamentalModelEngine().project(
            source_statements=source_statements(),
            plan=(matured,),
        )
        changed.projections[0].require_valid()
        self.assertNotEqual(
            standard.projections[0].projection_id,
            changed.projections[0].projection_id,
        )
        self.assertNotEqual(standard.model_run_id, changed.model_run_id)


if __name__ == "__main__":
    unittest.main()