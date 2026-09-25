import unittest
from datetime import datetime, timezone

from quantos.valuation_methodology import (
    CashFlowVisibility,
    CompanyArchetype,
    MethodStatus,
    ValuationMethod,
    ValuationMethodologyEngine,
    ValuationMethodologyGate,
    ValuationProfile,
)


UTC = timezone.utc


def profile(**overrides):
    values = {
        "entity_id": "CIK:0000000000",
        "as_of": datetime(2026, 9, 25, tzinfo=UTC),
        "archetype": CompanyArchetype.GENERAL_OPERATING,
        "cash_flow_visibility": CashFlowVisibility.HIGH,
        "positive_fcff": True,
        "positive_fcfe": True,
        "material_dividend": False,
        "regulatory_capital_central": False,
        "has_segment_disclosure": False,
        "segment_economics_divergent": False,
        "peer_set_available": True,
        "going_concern_uncertainty": False,
        "distressed": False,
        "stable_leverage_capacity": True,
        "evidence_references": ("claim:business-profile",),
    }
    values.update(overrides)
    return ValuationProfile(**values)


class ValuationMethodologyTests(unittest.TestCase):
    def test_general_operating_company_can_receive_fcff_permit(self):
        assessment = ValuationMethodologyEngine().assess(profile())
        self.assertEqual(
            assessment.for_method(ValuationMethod.FCFF_DCF).status,
            MethodStatus.APPROPRIATE,
        )
        permit = ValuationMethodologyGate().issue(
            assessment=assessment,
            method=ValuationMethod.FCFF_DCF,
        )
        ValuationMethodologyGate.validate(
            assessment=assessment,
            permit=permit,
            required_method=ValuationMethod.FCFF_DCF,
        )

    def test_bank_blocks_fcff_and_supports_equity_methods(self):
        assessment = ValuationMethodologyEngine().assess(
            profile(
                archetype=CompanyArchetype.BANK,
                regulatory_capital_central=True,
            )
        )
        self.assertEqual(
            assessment.for_method(ValuationMethod.FCFF_DCF).status,
            MethodStatus.INAPPROPRIATE,
        )
        self.assertEqual(
            assessment.for_method(ValuationMethod.RESIDUAL_INCOME).status,
            MethodStatus.APPROPRIATE,
        )
        with self.assertRaises(ValueError):
            ValuationMethodologyGate().issue(
                assessment=assessment,
                method=ValuationMethod.FCFF_DCF,
            )

    def test_biotech_plain_fcff_requires_binary_outcome_evidence(self):
        assessment = ValuationMethodologyEngine().assess(
            profile(
                archetype=CompanyArchetype.PRE_REVENUE_BIOTECH,
                positive_fcff=False,
            )
        )
        with self.assertRaises(ValueError):
            ValuationMethodologyGate().issue(
                assessment=assessment,
                method=ValuationMethod.FCFF_DCF,
            )
        permit = ValuationMethodologyGate().issue(
            assessment=assessment,
            method=ValuationMethod.FCFF_DCF,
            condition_evidence={
                "BINARY_OUTCOMES_MODELED": ("scenario-set:123",),
            },
        )
        self.assertTrue(permit.permit_id.startswith("valuation-method-permit:"))

    def test_conglomerate_with_divergent_segments_supports_sotp(self):
        assessment = ValuationMethodologyEngine().assess(
            profile(
                archetype=CompanyArchetype.CONGLOMERATE,
                has_segment_disclosure=True,
                segment_economics_divergent=True,
            )
        )
        self.assertEqual(
            assessment.for_method(ValuationMethod.SOTP).status,
            MethodStatus.APPROPRIATE,
        )

    def test_low_visibility_fcff_requires_condition_evidence(self):
        assessment = ValuationMethodologyEngine().assess(
            profile(cash_flow_visibility=CashFlowVisibility.LOW)
        )
        with self.assertRaises(ValueError):
            ValuationMethodologyGate().issue(
                assessment=assessment,
                method=ValuationMethod.FCFF_DCF,
            )
        permit = ValuationMethodologyGate().issue(
            assessment=assessment,
            method=ValuationMethod.FCFF_DCF,
            condition_evidence={
                "FORECAST_VISIBILITY_JUSTIFIED": ("review:forecast",),
            },
        )
        ValuationMethodologyGate.validate(
            assessment=assessment,
            permit=permit,
            required_method=ValuationMethod.FCFF_DCF,
        )

    def test_distress_makes_liquidation_explicit_and_fcff_conditional(self):
        assessment = ValuationMethodologyEngine().assess(
            profile(distressed=True, going_concern_uncertainty=True)
        )
        self.assertEqual(
            assessment.for_method(ValuationMethod.LIQUIDATION_VALUE).status,
            MethodStatus.APPROPRIATE,
        )
        self.assertEqual(
            assessment.for_method(ValuationMethod.FCFF_DCF).status,
            MethodStatus.CONDITIONAL,
        )


if __name__ == "__main__":
    unittest.main()
