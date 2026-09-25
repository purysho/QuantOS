import unittest
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from quantos.advanced_schedules import InterestRateType
from quantos.fundamental_schedules import ProjectionPeriod
from quantos.lbo import (
    LBOCaseResult,
    LBOComparisonEngine,
    LBODebtTerms,
    LBOEngine,
    LBOExitAssumptions,
    LBOPeriodAssumption,
    LBOStatus,
    LBOTransactionAssumptions,
)
from quantos.valuation_methodology import (
    CashFlowVisibility,
    CompanyArchetype,
    ValuationMethod,
    ValuationMethodologyEngine,
    ValuationMethodologyGate,
    ValuationProfile,
)

UTC = timezone.utc


@dataclass(frozen=True)
class StubStatement:
    payload: dict[str, Decimal]

    def values(self):
        return dict(self.payload)


@dataclass(frozen=True)
class StubProjection:
    projection_id: str
    period: ProjectionPeriod
    income_statement: StubStatement
    schedule_values: dict[str, Decimal]


@dataclass(frozen=True)
class StubRun:
    model_run_id: str
    projections: tuple[StubProjection, ...]
    valid: bool = True

    def require_valid(self):
        if not self.valid:
            raise ValueError("invalid model run")


def model_run(
    *,
    model_id="model-run:base",
    operating_income=("60", "70", "80"),
):
    projections = []
    for i, value in enumerate(operating_income, start=1):
        year = 2026 + i
        projections.append(
            StubProjection(
                projection_id=f"projection:{year}",
                period=ProjectionPeriod(
                    date(year, 1, 1),
                    date(year, 12, 31),
                    year,
                    "FY",
                ),
                income_statement=StubStatement(
                    {"operating_income": Decimal(value)}
                ),
                schedule_values={
                    "depreciation": Decimal("10"),
                    "capex": Decimal("15"),
                    "change_in_nwc": Decimal("5"),
                },
            )
        )
    return StubRun(model_id, tuple(projections))


def method_gate():
    assessment = ValuationMethodologyEngine().assess(
        ValuationProfile(
            entity_id="TARGET",
            as_of=datetime(2026, 12, 31, tzinfo=UTC),
            archetype=CompanyArchetype.GENERAL_OPERATING,
            cash_flow_visibility=CashFlowVisibility.HIGH,
            positive_fcff=True,
            positive_fcfe=True,
            material_dividend=False,
            regulatory_capital_central=False,
            has_segment_disclosure=False,
            segment_economics_divergent=False,
            peer_set_available=True,
            going_concern_uncertainty=False,
            distressed=False,
            stable_leverage_capacity=True,
            evidence_references=("claim:lbo-profile",),
        )
    )
    permit = ValuationMethodologyGate().issue(
        assessment=assessment,
        method=ValuationMethod.LBO,
        condition_evidence={
            "LBO_TERMS_SUPPORTED": ("review:lbo-terms",),
        },
    )
    return assessment, permit


def transaction(**overrides):
    values = {
        "as_of": datetime(2026, 12, 31, tzinfo=UTC),
        "entry_enterprise_value": Decimal("500"),
        "target_cash": Decimal("30"),
        "target_debt": Decimal("100"),
        "target_non_operating_investments": Decimal("0"),
        "transaction_fees": Decimal("10"),
        "financing_fees": Decimal("5"),
        "cash_used_at_close": Decimal("20"),
        "minimum_cash": Decimal("10"),
        "opening_nol": Decimal("0"),
        "evidence_references": ("review:entry",),
    }
    values.update(overrides)
    return LBOTransactionAssumptions(**values)


def debt():
    return (
        LBODebtTerms(
            tranche_id="term-a",
            initial_draw=Decimal("180"),
            rate_type=InterestRateType.FIXED,
            annual_amortization_rate=Decimal("0.05"),
            maturity_period=5,
            fixed_rate=Decimal("0.06"),
            spread=None,
            evidence_references=("term-sheet:a",),
        ),
        LBODebtTerms(
            tranche_id="term-b",
            initial_draw=Decimal("100"),
            rate_type=InterestRateType.FLOATING,
            annual_amortization_rate=Decimal("0"),
            maturity_period=5,
            fixed_rate=None,
            spread=Decimal("0.03"),
            evidence_references=("term-sheet:b",),
        ),
    )


def periods(run):
    return tuple(
        LBOPeriodAssumption(
            projection_id=item.projection_id,
            floating_base_rate=Decimal("0.04"),
            cash_tax_rate=Decimal("0.25"),
            nol_utilization_limit=Decimal("0.80"),
            sweep_percent=Decimal("0.75"),
            evidence_references=("review:lbo-period",),
        )
        for item in run.projections
    )


def exit_assumptions(multiple="7"):
    return LBOExitAssumptions(
        exit_multiple=Decimal(multiple),
        non_operating_investments=Decimal("0"),
        evidence_references=("review:exit",),
    )


def value(run=None, **kwargs):
    run = run or model_run()
    assessment, permit = method_gate()
    params = dict(
        model_run=run,
        methodology_assessment=assessment,
        method_permit=permit,
        transaction=transaction(),
        debt_terms=debt(),
        period_assumptions=periods(run),
        sweep_priority=("term-a", "term-b"),
        exit_assumptions=exit_assumptions(),
    )
    params.update(kwargs)
    return LBOEngine().value(**params)


class LBOTests(unittest.TestCase):
    def test_sources_and_uses_balance_and_sponsor_equity_is_residual(self):
        result = value()
        su = result.sources_and_uses
        self.assertEqual(su.total_uses, su.total_sources)
        self.assertEqual(su.sponsor_debt, Decimal("280"))
        self.assertEqual(su.sponsor_equity, Decimal("245"))
        self.assertEqual(result.status, LBOStatus.SOLVED)

    def test_debt_paydown_rolls_period_to_period(self):
        result = value()
        self.assertEqual(len(result.periods), 3)
        self.assertLess(
            result.periods[-1].ending_debt,
            result.periods[0].ending_debt,
        )
        first_by_id = {
            item.tranche_id: item for item in result.periods[0].debt_periods
        }
        second_by_id = {
            item.tranche_id: item for item in result.periods[1].debt_periods
        }
        self.assertEqual(
            second_by_id["term-a"].beginning_balance,
            first_by_id["term-a"].ending_balance,
        )

    def test_interest_uses_pre_sweep_timing_convention(self):
        result = value()
        first = {
            item.tranche_id: item for item in result.periods[0].debt_periods
        }["term-a"]
        scheduled = Decimal("180") * Decimal("0.05")
        pre_sweep = Decimal("180") - scheduled
        expected_interest = (
            (Decimal("180") + pre_sweep) / Decimal("2")
        ) * Decimal("0.06")
        self.assertEqual(first.interest_expense, expected_interest)

    def test_mandatory_debt_service_cannot_breach_minimum_cash(self):
        run = model_run(operating_income=("5", "5", "5"))
        harsh = (
            LBODebtTerms(
                tranche_id="term-a",
                initial_draw=Decimal("280"),
                rate_type=InterestRateType.FIXED,
                annual_amortization_rate=Decimal("0.50"),
                maturity_period=5,
                fixed_rate=Decimal("0.10"),
                spread=None,
                evidence_references=("term-sheet:harsh",),
            ),
        )
        assessment, permit = method_gate()
        with self.assertRaises(ValueError):
            LBOEngine().value(
                model_run=run,
                methodology_assessment=assessment,
                method_permit=permit,
                transaction=transaction(),
                debt_terms=harsh,
                period_assumptions=periods(run),
                sweep_priority=("term-a",),
                exit_assumptions=exit_assumptions(),
            )

    def test_exit_returns_are_explicit(self):
        result = value()
        self.assertEqual(
            result.exit_enterprise_value,
            result.exit_ebitda * Decimal("7"),
        )
        self.assertIsNotNone(result.sponsor_moic)
        self.assertIsNotNone(result.sponsor_irr)
        assert result.sponsor_moic is not None
        self.assertGreater(result.sponsor_moic, Decimal("1"))

    def test_wrong_method_permit_fails_closed(self):
        run = model_run()
        assessment, _ = method_gate()
        dcf_assessment = ValuationMethodologyEngine().assess(
            ValuationProfile(
                entity_id="TARGET",
                as_of=datetime(2026, 12, 31, tzinfo=UTC),
                archetype=CompanyArchetype.GENERAL_OPERATING,
                cash_flow_visibility=CashFlowVisibility.HIGH,
                positive_fcff=True,
                positive_fcfe=True,
                material_dividend=False,
                regulatory_capital_central=False,
                has_segment_disclosure=False,
                segment_economics_divergent=False,
                peer_set_available=True,
                going_concern_uncertainty=False,
                distressed=False,
                stable_leverage_capacity=True,
                evidence_references=("claim:dcf",),
            )
        )
        dcf_permit = ValuationMethodologyGate().issue(
            assessment=dcf_assessment,
            method=ValuationMethod.FCFF_DCF,
        )
        with self.assertRaises(ValueError):
            LBOEngine().value(
                model_run=run,
                methodology_assessment=assessment,
                method_permit=dcf_permit,
                transaction=transaction(),
                debt_terms=debt(),
                period_assumptions=periods(run),
                sweep_priority=("term-a", "term-b"),
                exit_assumptions=exit_assumptions(),
            )

    def test_operating_downside_is_separate_model_run_not_hidden_haircut(self):
        base = value(
            run=model_run(
                model_id="model-run:base",
                operating_income=("60", "70", "80"),
            )
        )
        downside_run = model_run(
            model_id="model-run:downside",
            operating_income=("45", "50", "55"),
        )
        downside = value(run=downside_run)
        comparison = LBOComparisonEngine.compare(
            (
                LBOCaseResult("Base", base),
                LBOCaseResult("Downside", downside),
            )
        )
        self.assertNotEqual(
            comparison.cases[0].result.model_run_id,
            comparison.cases[1].result.model_run_id,
        )
        self.assertNotEqual(
            comparison.cases[0].result.valuation_id,
            comparison.cases[1].result.valuation_id,
        )

    def test_exit_multiple_change_changes_valuation_identity(self):
        base = value()
        changed = value(exit_assumptions=exit_assumptions("6"))
        self.assertNotEqual(base.valuation_id, changed.valuation_id)
        self.assertLess(
            changed.sponsor_exit_equity_value,
            base.sponsor_exit_equity_value,
        )


if __name__ == "__main__":
    unittest.main()
