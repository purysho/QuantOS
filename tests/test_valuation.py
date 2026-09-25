import unittest
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from quantos.financial_statements import AccountingValidationError
from quantos.fundamental_schedules import ProjectionPeriod
from quantos.valuation import (
    DCFEngine,
    DCFInputs,
    DiscountPoint,
    EquityBridge,
    MarketPriceReference,
    PeriodTaxAssumption,
    ReverseDCFStatus,
    ValuationAssumption,
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
            raise AccountingValidationError("invalid model run")


def model_run(valid=True):
    return StubRun(
        model_run_id="model-run:test",
        valid=valid,
        projections=(
            StubProjection(
                projection_id="projection:2027",
                period=ProjectionPeriod(
                    date(2027, 1, 1),
                    date(2027, 12, 31),
                    2027,
                    "FY",
                ),
                income_statement=StubStatement(
                    {"operating_income": Decimal("100")}
                ),
                schedule_values={
                    "depreciation": Decimal("10"),
                    "capex": Decimal("20"),
                    "change_in_nwc": Decimal("5"),
                },
            ),
            StubProjection(
                projection_id="projection:2028",
                period=ProjectionPeriod(
                    date(2028, 1, 1),
                    date(2028, 12, 31),
                    2028,
                    "FY",
                ),
                income_statement=StubStatement(
                    {"operating_income": Decimal("110")}
                ),
                schedule_values={
                    "depreciation": Decimal("11"),
                    "capex": Decimal("21"),
                    "change_in_nwc": Decimal("6"),
                },
            ),
        ),
    )


def inputs(growth="0.03", wacc="0.10"):
    return DCFInputs(
        wacc=ValuationAssumption(
            name="wacc",
            value=Decimal(wacc),
            rationale="Reviewed cost-of-capital assumption.",
            evidence_references=("claim:wacc",),
        ),
        terminal_growth=ValuationAssumption(
            name="terminal_growth",
            value=Decimal(growth),
            rationale="Reviewed long-run nominal growth assumption.",
            evidence_references=("claim:terminal-growth",),
        ),
        taxes=(
            PeriodTaxAssumption(
                projection_id="projection:2027",
                tax_rate=Decimal("0.25"),
                rationale="Unlevered cash-tax assumption.",
                evidence_references=("claim:tax",),
            ),
            PeriodTaxAssumption(
                projection_id="projection:2028",
                tax_rate=Decimal("0.25"),
                rationale="Unlevered cash-tax assumption.",
                evidence_references=("claim:tax",),
            ),
        ),
        discount_points=(
            DiscountPoint("projection:2027", 1),
            DiscountPoint("projection:2028", 2),
        ),
        equity_bridge=EquityBridge(
            cash=Decimal("30"),
            non_operating_investments=Decimal("5"),
            debt=Decimal("100"),
            preferred_equity=Decimal("0"),
            noncontrolling_interest=Decimal("0"),
            diluted_shares=Decimal("50"),
            as_of=datetime(2026, 12, 31, tzinfo=UTC),
            evidence_references=("artifact:balance-sheet",),
        ),
    )


class DCFTests(unittest.TestCase):
    def test_fcff_is_derived_from_exact_model_projection(self):
        result = DCFEngine().value(
            model_run=model_run(),
            inputs=inputs(),
        )
        self.assertEqual(result.model_run_id, "model-run:test")
        self.assertEqual(result.periods[0].fcff, Decimal("60.00"))
        self.assertEqual(result.periods[1].fcff, Decimal("66.50"))
        self.assertGreater(result.enterprise_value, result.equity_value)
        self.assertGreater(result.value_per_diluted_share, 0)
        self.assertGreater(
            result.terminal_value_share_of_enterprise_value,
            Decimal("0"),
        )

    def test_invalid_model_run_is_rejected_before_valuation(self):
        with self.assertRaises(AccountingValidationError):
            DCFEngine().value(
                model_run=model_run(valid=False),
                inputs=inputs(),
            )

    def test_terminal_growth_must_be_below_wacc(self):
        with self.assertRaises(ValueError):
            inputs(growth="0.10", wacc="0.10")

    def test_tax_and_discount_coverage_must_match_model_exactly(self):
        base = inputs()
        incomplete = DCFInputs(
            wacc=base.wacc,
            terminal_growth=base.terminal_growth,
            taxes=base.taxes[:1],
            discount_points=base.discount_points,
            equity_bridge=base.equity_bridge,
        )
        with self.assertRaises(ValueError):
            DCFEngine().value(
                model_run=model_run(),
                inputs=incomplete,
            )

    def test_changed_wacc_changes_config_and_valuation_identity(self):
        engine = DCFEngine()
        first = engine.value(model_run=model_run(), inputs=inputs(wacc="0.10"))
        second = engine.value(model_run=model_run(), inputs=inputs(wacc="0.11"))
        self.assertNotEqual(first.config_id, second.config_id)
        self.assertNotEqual(first.valuation_id, second.valuation_id)
        self.assertNotEqual(first.enterprise_value, second.enterprise_value)

    def test_reverse_dcf_solves_market_implied_terminal_growth(self):
        reverse = DCFEngine().reverse_terminal_growth(
            model_run=model_run(),
            inputs=inputs(),
            market=MarketPriceReference(
                price_per_share=Decimal("15"),
                diluted_shares=Decimal("50"),
                as_of=datetime(2026, 12, 31, tzinfo=UTC),
                evidence_references=("artifact:market-price",),
            ),
        )
        self.assertEqual(reverse.status, ReverseDCFStatus.SOLVED)
        self.assertIsNotNone(reverse.implied_terminal_growth)
        assert reverse.implied_terminal_growth is not None
        self.assertLess(
            reverse.implied_terminal_growth,
            inputs().wacc.value,
        )

    def test_reverse_dcf_reports_market_below_explicit_value_without_fake_growth(self):
        reverse = DCFEngine().reverse_terminal_growth(
            model_run=model_run(),
            inputs=inputs(),
            market=MarketPriceReference(
                price_per_share=Decimal("0"),
                diluted_shares=Decimal("50"),
                as_of=datetime(2026, 12, 31, tzinfo=UTC),
                evidence_references=("artifact:market-price",),
            ),
        )
        self.assertEqual(
            reverse.status,
            ReverseDCFStatus.MARKET_EV_BELOW_EXPLICIT_PV,
        )
        self.assertIsNone(reverse.implied_terminal_growth)


if __name__ == "__main__":
    unittest.main()
