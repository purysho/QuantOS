import unittest
from datetime import date
from decimal import Decimal

from quantos.advanced_schedules import (
    CashSweepEngine,
    CashSweepPolicy,
    DebtScheduleEngine,
    DebtTranche,
    InterestRateType,
    ShareScheduleEngine,
    TaxScheduleEngine,
)


class AdvancedScheduleTests(unittest.TestCase):
    def test_fixed_and_floating_debt_interest_are_explicit(self):
        result = DebtScheduleEngine().project(
            tranches=(
                DebtTranche(
                    tranche_id="term-a",
                    beginning_balance=Decimal("100"),
                    rate_type=InterestRateType.FIXED,
                    maturity_date=date(2030, 12, 31),
                    scheduled_repayment=Decimal("10"),
                    fixed_rate=Decimal("0.05"),
                    evidence_references=("artifact:debt-agreement",),
                ),
                DebtTranche(
                    tranche_id="rcf",
                    beginning_balance=Decimal("50"),
                    rate_type=InterestRateType.FLOATING,
                    maturity_date=date(2031, 12, 31),
                    scheduled_repayment=Decimal("0"),
                    spread=Decimal("0.02"),
                    evidence_references=("artifact:credit-facility",),
                ),
            ),
            period_start=date(2026, 1, 1),
            period_end=date(2026, 12, 31),
            floating_base_rate=Decimal("0.04"),
        )
        by_id = {item.tranche_id: item for item in result.tranches}
        self.assertEqual(by_id["term-a"].effective_rate, Decimal("0.05"))
        self.assertEqual(by_id["rcf"].effective_rate, Decimal("0.06"))
        self.assertGreater(result.total_interest_expense, 0)

    def test_maturity_forces_full_repayment(self):
        result = DebtScheduleEngine().project(
            tranches=(
                DebtTranche(
                    tranche_id="maturing",
                    beginning_balance=Decimal("25"),
                    rate_type=InterestRateType.FIXED,
                    maturity_date=date(2026, 6, 30),
                    scheduled_repayment=Decimal("1"),
                    fixed_rate=Decimal("0.05"),
                    evidence_references=("artifact:terms",),
                ),
            ),
            period_start=date(2026, 1, 1),
            period_end=date(2026, 12, 31),
            floating_base_rate=Decimal("0.04"),
        )
        tranche = result.tranches[0]
        self.assertTrue(tranche.matured_in_period)
        self.assertEqual(tranche.mandatory_repayment, Decimal("25"))
        self.assertEqual(tranche.ending_balance_before_sweep, Decimal("0"))

    def test_cash_sweep_preserves_minimum_cash_and_priority(self):
        debt = DebtScheduleEngine().project(
            tranches=(
                DebtTranche(
                    tranche_id="senior",
                    beginning_balance=Decimal("30"),
                    rate_type=InterestRateType.FIXED,
                    maturity_date=date(2030, 12, 31),
                    scheduled_repayment=Decimal("0"),
                    fixed_rate=Decimal("0.05"),
                    evidence_references=("artifact:senior",),
                ),
                DebtTranche(
                    tranche_id="junior",
                    beginning_balance=Decimal("40"),
                    rate_type=InterestRateType.FIXED,
                    maturity_date=date(2031, 12, 31),
                    scheduled_repayment=Decimal("0"),
                    fixed_rate=Decimal("0.07"),
                    evidence_references=("artifact:junior",),
                ),
            ),
            period_start=date(2026, 1, 1),
            period_end=date(2026, 12, 31),
            floating_base_rate=Decimal("0.04"),
        )
        sweep = CashSweepEngine().apply(
            pre_sweep_cash=Decimal("60"),
            debt_schedule=debt,
            policy=CashSweepPolicy(
                minimum_cash=Decimal("20"),
                sweep_percent=Decimal("0.75"),
                tranche_priority=("senior", "junior"),
            ),
        )
        self.assertEqual(sweep.available_excess_cash, Decimal("40"))
        self.assertEqual(sweep.applied_sweep, Decimal("30.00"))
        self.assertEqual(sweep.ending_cash, Decimal("30.00"))
        self.assertEqual(
            sweep.allocations[0].ending_balance_after_sweep,
            Decimal("0.00"),
        )

    def test_nol_shields_taxable_income_with_utilization_limit(self):
        result = TaxScheduleEngine().project(
            pretax_income=Decimal("100"),
            opening_nol=Decimal("100"),
            statutory_tax_rate=Decimal("0.25"),
            nol_utilization_limit=Decimal("0.80"),
            evidence_references=("claim:tax-rule",),
        )
        self.assertEqual(result.nol_utilized, Decimal("80.00"))
        self.assertEqual(result.taxable_income, Decimal("20.00"))
        self.assertEqual(result.current_tax_expense, Decimal("5.0000"))
        self.assertEqual(result.ending_nol, Decimal("20.00"))

    def test_loss_generates_nol_without_negative_current_tax(self):
        result = TaxScheduleEngine().project(
            pretax_income=Decimal("-30"),
            opening_nol=Decimal("10"),
            statutory_tax_rate=Decimal("0.25"),
            nol_utilization_limit=Decimal("0.80"),
            evidence_references=("claim:tax-rule",),
        )
        self.assertEqual(result.nol_generated, Decimal("30"))
        self.assertEqual(result.ending_nol, Decimal("40"))
        self.assertEqual(result.current_tax_expense, Decimal("0.00"))

    def test_treasury_stock_method_and_equity_cash_flows(self):
        result = ShareScheduleEngine().project(
            beginning_basic_shares=Decimal("100"),
            issued_shares=Decimal("5"),
            issue_price=Decimal("12"),
            repurchased_shares=Decimal("2"),
            repurchase_price=Decimal("15"),
            options_outstanding=Decimal("10"),
            option_strike=Decimal("8"),
            average_market_price=Decimal("20"),
            restricted_units=Decimal("3"),
            evidence_references=("artifact:equity-note",),
        )
        self.assertEqual(result.ending_basic_shares, Decimal("103"))
        self.assertEqual(result.incremental_option_shares, Decimal("6.0"))
        self.assertEqual(result.diluted_shares, Decimal("112.0"))
        self.assertEqual(result.issuance_cash_inflow, Decimal("60"))
        self.assertEqual(result.repurchase_cash_outflow, Decimal("30"))
        self.assertEqual(
            result.net_equity_financing_cash_flow,
            Decimal("30"),
        )

    def test_share_repurchase_cannot_exceed_available_shares(self):
        with self.assertRaises(ValueError):
            ShareScheduleEngine().project(
                beginning_basic_shares=Decimal("100"),
                issued_shares=Decimal("0"),
                issue_price=Decimal("10"),
                repurchased_shares=Decimal("101"),
                repurchase_price=Decimal("10"),
                options_outstanding=Decimal("0"),
                option_strike=Decimal("0"),
                average_market_price=Decimal("10"),
                restricted_units=Decimal("0"),
                evidence_references=("artifact:equity-note",),
            )


if __name__ == "__main__":
    unittest.main()
