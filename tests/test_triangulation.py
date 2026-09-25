import unittest
from datetime import datetime, timezone
from decimal import Decimal

from quantos.comparables import (
    ComparableValuationResult,
    ImpliedValuationPoint,
    ImpliedValuationRange,
    ImpliedValuationStatus,
    MultipleKind,
)
from quantos.lbo import (
    LBOResult,
    LBOSourcesAndUses,
    LBOStatus,
)
from quantos.sotp import SOTPResult
from quantos.triangulation import (
    TriangulationAdapter,
    TriangulationPolicy,
    TriangulationStatus,
    ValuationFamily,
    ValuationTriangulationEngine,
    build_observation,
)
from quantos.valuation import DCFResult
from quantos.valuation_sensitivity import DCFSensitivityGrid

UTC = timezone.utc
AS_OF = datetime(2026, 9, 25, tzinfo=UTC)
PERMIT = "valuation-method-permit:" + "a" * 64


def obs(family, label, low, central, high):
    return build_observation(
        family=family,
        label=label,
        reference_id=f"valuation:{label}",
        method_permit_id=PERMIT,
        as_of=AS_OF,
        low=Decimal(low),
        central=Decimal(central),
        high=Decimal(high),
        evidence_references=(f"evidence:{label}",),
    )


def policy(**overrides):
    values = {
        "minimum_method_families": 2,
        "wide_central_dispersion_ratio": Decimal("0.30"),
        "rationale": "Surface cross-method disagreement without averaging it away.",
        "evidence_references": ("review:triangulation-policy",),
    }
    values.update(overrides)
    return TriangulationPolicy(**values)


class TriangulationTests(unittest.TestCase):
    def test_multiple_comps_metrics_count_as_one_method_family(self):
        result = ValuationTriangulationEngine().build(
            observations=(
                obs(
                    ValuationFamily.TRADING_COMPS,
                    "EV_EBITDA",
                    "8",
                    "10",
                    "12",
                ),
                obs(
                    ValuationFamily.TRADING_COMPS,
                    "EV_REVENUE",
                    "9",
                    "11",
                    "13",
                ),
            ),
            policy=policy(),
        )
        self.assertEqual(
            result.status,
            TriangulationStatus.INSUFFICIENT_METHOD_FAMILIES,
        )
        self.assertEqual(len(result.family_summaries), 1)

    def test_common_overlap_is_structural_not_weighted_fair_value(self):
        result = ValuationTriangulationEngine().build(
            observations=(
                obs(ValuationFamily.DCF, "DCF", "9", "11", "14"),
                obs(
                    ValuationFamily.TRADING_COMPS,
                    "Comps",
                    "10",
                    "12",
                    "15",
                ),
                obs(ValuationFamily.SOTP, "SOTP", "12", "12", "12"),
            ),
            policy=policy(),
        )
        self.assertEqual(result.status, TriangulationStatus.COMMON_OVERLAP)
        self.assertEqual(result.common_overlap_low, Decimal("12"))
        self.assertEqual(result.common_overlap_high, Decimal("12"))
        self.assertNotIn("fair value", result.__dict__)
        self.assertIn("does not calculate", result.caveat)

    def test_disjoint_methods_remain_disjoint(self):
        result = ValuationTriangulationEngine().build(
            observations=(
                obs(ValuationFamily.DCF, "DCF", "5", "6", "7"),
                obs(
                    ValuationFamily.TRADING_COMPS,
                    "Comps",
                    "10",
                    "11",
                    "12",
                ),
            ),
            policy=policy(),
        )
        self.assertEqual(result.status, TriangulationStatus.DISJOINT)
        self.assertIn(
            "NO_CROSS_METHOD_OVERLAP",
            {flag.code for flag in result.flags},
        )

    def test_partial_overlap_preserves_no_common_range(self):
        result = ValuationTriangulationEngine().build(
            observations=(
                obs(ValuationFamily.DCF, "DCF", "8", "10", "12"),
                obs(
                    ValuationFamily.TRADING_COMPS,
                    "Comps",
                    "11",
                    "13",
                    "15",
                ),
                obs(ValuationFamily.SOTP, "SOTP", "14", "14", "14"),
            ),
            policy=policy(),
        )
        self.assertEqual(
            result.status,
            TriangulationStatus.PARTIAL_OVERLAP,
        )
        self.assertIsNone(result.common_overlap_low)
        self.assertIsNone(result.common_overlap_high)

    def test_wide_dispersion_threshold_is_policy_bound(self):
        result = ValuationTriangulationEngine().build(
            observations=(
                obs(ValuationFamily.DCF, "DCF", "8", "10", "12"),
                obs(
                    ValuationFamily.TRADING_COMPS,
                    "Comps",
                    "18",
                    "20",
                    "22",
                ),
            ),
            policy=policy(wide_central_dispersion_ratio=Decimal("0.20")),
        )
        self.assertIn(
            "WIDE_CROSS_METHOD_DISPERSION",
            {flag.code for flag in result.flags},
        )

    def test_mismatched_as_of_fails_closed(self):
        first = obs(ValuationFamily.DCF, "DCF", "8", "10", "12")
        second = build_observation(
            family=ValuationFamily.SOTP,
            label="SOTP",
            reference_id="sotp:test",
            method_permit_id=PERMIT,
            as_of=datetime(2026, 9, 24, tzinfo=UTC),
            low=Decimal("10"),
            central=Decimal("10"),
            high=Decimal("10"),
            evidence_references=("evidence:sotp",),
        )
        with self.assertRaises(ValueError):
            ValuationTriangulationEngine().build(
                observations=(first, second),
                policy=policy(),
            )

    def test_dcf_adapter_requires_exact_sensitivity_lineage(self):
        dcf = DCFResult(
            valuation_id="dcf:test",
            model_run_id="model-run:test",
            config_id="dcf-config:test",
            method_permit_id=PERMIT,
            periods=(),
            terminal_value=Decimal("0"),
            present_value_terminal=Decimal("0"),
            present_value_explicit_fcff=Decimal("0"),
            enterprise_value=Decimal("100"),
            equity_value=Decimal("100"),
            value_per_diluted_share=Decimal("10"),
            terminal_value_share_of_enterprise_value=Decimal("0.5"),
        )
        grid = DCFSensitivityGrid(
            grid_id="dcf-sensitivity-grid:test",
            base_valuation_id="dcf:other",
            model_run_id=dcf.model_run_id,
            method_permit_id=PERMIT,
            policy_id="policy:test",
            wacc_values=(Decimal("0.10"),),
            terminal_growth_values=(Decimal("0.03"),),
            cells=(),
            min_valid_per_share=Decimal("8"),
            max_valid_per_share=Decimal("12"),
            per_share_span_ratio_to_base=Decimal("0.4"),
            flags=(),
        )
        with self.assertRaises(ValueError):
            TriangulationAdapter.from_dcf(
                dcf=dcf,
                sensitivity=grid,
                as_of=AS_OF,
                evidence_references=("evidence:dcf",),
            )

    def test_comps_adapter_preserves_each_metric_but_one_family(self):
        result = ComparableValuationResult(
            valuation_id="trading-comps:test",
            selection_id="peer-selection:test",
            method_permit_id=PERMIT,
            policy_id="comps-policy:test",
            distributions=(),
            implied_ranges=(
                ImpliedValuationRange(
                    kind=MultipleKind.EV_EBITDA,
                    status=ImpliedValuationStatus.AVAILABLE,
                    points=(
                        ImpliedValuationPoint(
                            Decimal("0.25"), Decimal("5"), Decimal("90"),
                            Decimal("80"), Decimal("8"),
                        ),
                        ImpliedValuationPoint(
                            Decimal("0.50"), Decimal("6"), Decimal("110"),
                            Decimal("100"), Decimal("10"),
                        ),
                        ImpliedValuationPoint(
                            Decimal("0.75"), Decimal("7"), Decimal("130"),
                            Decimal("120"), Decimal("12"),
                        ),
                    ),
                    reason=None,
                ),
                ImpliedValuationRange(
                    kind=MultipleKind.EV_REVENUE,
                    status=ImpliedValuationStatus.AVAILABLE,
                    points=(
                        ImpliedValuationPoint(
                            Decimal("0.25"), Decimal("2"), Decimal("95"),
                            Decimal("85"), Decimal("8.5"),
                        ),
                        ImpliedValuationPoint(
                            Decimal("0.50"), Decimal("2.5"), Decimal("115"),
                            Decimal("105"), Decimal("10.5"),
                        ),
                        ImpliedValuationPoint(
                            Decimal("0.75"), Decimal("3"), Decimal("135"),
                            Decimal("125"), Decimal("12.5"),
                        ),
                    ),
                    reason=None,
                ),
            ),
        )
        observations = TriangulationAdapter.from_comps(
            result=result,
            as_of=AS_OF,
            evidence_references=("evidence:comps",),
        )
        self.assertEqual(len(observations), 2)
        self.assertEqual(
            {item.family for item in observations},
            {ValuationFamily.TRADING_COMPS},
        )

    def test_sotp_adapter_is_a_point_observation(self):
        result = SOTPResult(
            valuation_id="sotp:test",
            company_method_permit_id=PERMIT,
            as_of=AS_OF,
            contributions=(),
            gross_attributable_segment_equity=Decimal("100"),
            corporate_net_adjustment=Decimal("0"),
            equity_value=Decimal("100"),
            diluted_shares=Decimal("10"),
            value_per_diluted_share=Decimal("10"),
        )
        observation = TriangulationAdapter.from_sotp(
            result=result,
            evidence_references=("evidence:sotp",),
        )
        self.assertEqual(observation.low_per_share, Decimal("10"))
        self.assertEqual(observation.high_per_share, Decimal("10"))

    def test_lbo_cross_check_remains_outside_per_share_family_count(self):
        lbo = LBOResult(
            valuation_id="lbo:test",
            model_run_id="model-run:lbo",
            method_permit_id=PERMIT,
            status=LBOStatus.SOLVED,
            sources_and_uses=LBOSourcesAndUses(
                equity_purchase_price=Decimal("400"),
                debt_refinancing=Decimal("100"),
                transaction_fees=Decimal("10"),
                financing_fees=Decimal("5"),
                total_uses=Decimal("515"),
                sponsor_debt=Decimal("280"),
                target_cash_used=Decimal("20"),
                sponsor_equity=Decimal("215"),
                total_sources=Decimal("515"),
            ),
            periods=(),
            exit_ebitda=Decimal("100"),
            exit_multiple=Decimal("7"),
            exit_enterprise_value=Decimal("700"),
            exit_net_debt=Decimal("100"),
            sponsor_exit_equity_value=Decimal("600"),
            sponsor_moic=Decimal("2.79"),
            sponsor_irr=Decimal("0.41"),
        )
        cross = TriangulationAdapter.from_lbo(
            result=lbo,
            evidence_references=("evidence:lbo",),
        )
        result = ValuationTriangulationEngine().build(
            observations=(
                obs(ValuationFamily.DCF, "DCF", "9", "10", "11"),
                obs(ValuationFamily.SOTP, "SOTP", "9.5", "10", "10.5"),
            ),
            policy=policy(),
            lbo_cross_checks=(cross,),
        )
        self.assertEqual(len(result.family_summaries), 2)
        self.assertIn(
            "LBO_SPONSOR_RETURN_CROSS_CHECK_PRESENT",
            {flag.code for flag in result.flags},
        )


if __name__ == "__main__":
    unittest.main()