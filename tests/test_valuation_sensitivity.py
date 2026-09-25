import unittest
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from quantos.fundamental_schedules import ProjectionPeriod
from quantos.valuation import (
    DCFEngine,
    DCFInputs,
    DiscountPoint,
    EquityBridge,
    PeriodTaxAssumption,
    ValuationAssumption,
)
from quantos.valuation_methodology import (
    CashFlowVisibility,
    CompanyArchetype,
    ValuationMethod,
    ValuationMethodologyEngine,
    ValuationMethodologyGate,
    ValuationProfile,
)
from quantos.valuation_sensitivity import (
    DCFSensitivityEngine,
    SensitivityCellStatus,
    SensitivityPolicy,
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

    def require_valid(self):
        return None


def run():
    return StubRun(
        model_run_id="model-run:sensitivity",
        projections=(
            StubProjection(
                "projection:2027",
                ProjectionPeriod(
                    date(2027, 1, 1),
                    date(2027, 12, 31),
                    2027,
                    "FY",
                ),
                StubStatement({"operating_income": Decimal("100")}),
                {
                    "depreciation": Decimal("10"),
                    "capex": Decimal("20"),
                    "change_in_nwc": Decimal("5"),
                },
            ),
            StubProjection(
                "projection:2028",
                ProjectionPeriod(
                    date(2028, 1, 1),
                    date(2028, 12, 31),
                    2028,
                    "FY",
                ),
                StubStatement({"operating_income": Decimal("110")}),
                {
                    "depreciation": Decimal("11"),
                    "capex": Decimal("21"),
                    "change_in_nwc": Decimal("6"),
                },
            ),
        ),
    )


def inputs():
    return DCFInputs(
        wacc=ValuationAssumption(
            "wacc",
            Decimal("0.10"),
            "Reviewed WACC.",
            ("claim:wacc",),
        ),
        terminal_growth=ValuationAssumption(
            "terminal_growth",
            Decimal("0.03"),
            "Reviewed terminal growth.",
            ("claim:g",),
        ),
        taxes=(
            PeriodTaxAssumption(
                "projection:2027",
                Decimal("0.25"),
                "Reviewed cash tax.",
                ("claim:tax",),
            ),
            PeriodTaxAssumption(
                "projection:2028",
                Decimal("0.25"),
                "Reviewed cash tax.",
                ("claim:tax",),
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
            evidence_references=("artifact:bridge",),
        ),
    )


def method_gate():
    assessment = ValuationMethodologyEngine().assess(
        ValuationProfile(
            entity_id="CIK:test",
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
            evidence_references=("claim:profile",),
        )
    )
    permit = ValuationMethodologyGate().issue(
        assessment=assessment,
        method=ValuationMethod.FCFF_DCF,
    )
    return assessment, permit


def policy(**overrides):
    values = {
        "terminal_share_watch": Decimal("0.50"),
        "terminal_share_critical": Decimal("0.80"),
        "minimum_wacc_growth_spread": Decimal("0.02"),
        "per_share_span_ratio_watch": Decimal("0.50"),
        "rationale": "Explicit review thresholds for DCF diagnostic visibility.",
        "evidence_references": ("review:valuation-policy",),
    }
    values.update(overrides)
    return SensitivityPolicy(**values)


class DCFSensitivityTests(unittest.TestCase):
    def test_grid_is_bound_to_base_valuation_and_method_permit(self):
        assessment, permit = method_gate()
        base = DCFEngine().value(
            model_run=run(),
            inputs=inputs(),
            methodology_assessment=assessment,
            method_permit=permit,
        )
        grid = DCFSensitivityEngine().build(
            model_run=run(),
            base_inputs=inputs(),
            methodology_assessment=assessment,
            method_permit=permit,
            wacc_values=(Decimal("0.08"), Decimal("0.10"), Decimal("0.12")),
            terminal_growth_values=(
                Decimal("0.02"),
                Decimal("0.03"),
                Decimal("0.09"),
            ),
            policy=policy(),
        )
        self.assertEqual(grid.base_valuation_id, base.valuation_id)
        self.assertEqual(grid.method_permit_id, permit.permit_id)
        self.assertEqual(len(grid.cells), 9)

    def test_invalid_perpetuity_combinations_remain_visible(self):
        assessment, permit = method_gate()
        grid = DCFSensitivityEngine().build(
            model_run=run(),
            base_inputs=inputs(),
            methodology_assessment=assessment,
            method_permit=permit,
            wacc_values=(Decimal("0.08"), Decimal("0.10")),
            terminal_growth_values=(Decimal("0.03"), Decimal("0.09")),
            policy=policy(),
        )
        invalid = tuple(
            item
            for item in grid.cells
            if item.status is SensitivityCellStatus.INVALID_PERPETUITY_DOMAIN
        )
        self.assertEqual(len(invalid), 1)
        self.assertIsNone(invalid[0].value_per_diluted_share)
        self.assertIn(
            "INVALID_PERPETUITY_CELLS",
            {flag.code for flag in grid.flags},
        )

    def test_base_cell_matches_direct_dcf(self):
        assessment, permit = method_gate()
        base = DCFEngine().value(
            model_run=run(),
            inputs=inputs(),
            methodology_assessment=assessment,
            method_permit=permit,
        )
        grid = DCFSensitivityEngine().build(
            model_run=run(),
            base_inputs=inputs(),
            methodology_assessment=assessment,
            method_permit=permit,
            wacc_values=(Decimal("0.10"),),
            terminal_growth_values=(Decimal("0.03"),),
            policy=policy(),
        )
        self.assertEqual(
            grid.cells[0].value_per_diluted_share,
            base.value_per_diluted_share,
        )

    def test_thresholds_are_policy_bound_not_hidden_constants(self):
        assessment, permit = method_gate()
        strict = policy(
            terminal_share_watch=Decimal("0.01"),
            terminal_share_critical=Decimal("0.02"),
            per_share_span_ratio_watch=Decimal("0.01"),
        )
        grid = DCFSensitivityEngine().build(
            model_run=run(),
            base_inputs=inputs(),
            methodology_assessment=assessment,
            method_permit=permit,
            wacc_values=(Decimal("0.08"), Decimal("0.12")),
            terminal_growth_values=(Decimal("0.01"), Decimal("0.04")),
            policy=strict,
        )
        codes = {flag.code for flag in grid.flags}
        self.assertIn("TERMINAL_VALUE_CRITICAL", codes)
        self.assertIn("WIDE_PER_SHARE_SENSITIVITY", codes)

    def test_duplicate_sensitivity_axis_fails_closed(self):
        assessment, permit = method_gate()
        with self.assertRaises(ValueError):
            DCFSensitivityEngine().build(
                model_run=run(),
                base_inputs=inputs(),
                methodology_assessment=assessment,
                method_permit=permit,
                wacc_values=(Decimal("0.10"), Decimal("0.10")),
                terminal_growth_values=(Decimal("0.03"),),
                policy=policy(),
            )


if __name__ == "__main__":
    unittest.main()
