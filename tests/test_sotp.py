import unittest
from datetime import datetime, timezone
from decimal import Decimal

from quantos.sotp import (
    CorporateItems,
    GroupReconciliationReference,
    SOTPEngine,
    SegmentBridge,
    SegmentValuation,
    SegmentValueBasis,
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
AS_OF = datetime(2026, 9, 25, tzinfo=UTC)


def assessment(
    *,
    archetype=CompanyArchetype.GENERAL_OPERATING,
    segments=False,
    regulatory=False,
):
    return ValuationMethodologyEngine().assess(
        ValuationProfile(
            entity_id=f"entity:{archetype.value}:{segments}",
            as_of=AS_OF,
            archetype=archetype,
            cash_flow_visibility=CashFlowVisibility.HIGH,
            positive_fcff=True,
            positive_fcfe=True,
            material_dividend=False,
            regulatory_capital_central=regulatory,
            has_segment_disclosure=segments,
            segment_economics_divergent=segments,
            peer_set_available=True,
            going_concern_uncertainty=False,
            distressed=False,
            stable_leverage_capacity=True,
            evidence_references=("claim:profile",),
        )
    )


def permit_for(assessment_obj, method, evidence=None):
    return ValuationMethodologyGate().issue(
        assessment=assessment_obj,
        method=method,
        condition_evidence=evidence,
    )


def segment_operating():
    a = assessment()
    p = permit_for(a, ValuationMethod.FCFF_DCF)
    return SegmentValuation(
        segment_id="industrial",
        segment_name="Industrial",
        as_of=AS_OF,
        method=ValuationMethod.FCFF_DCF,
        methodology_assessment=a,
        method_permit=p,
        valuation_reference_id="dcf:industrial",
        basis=SegmentValueBasis.ENTERPRISE_VALUE,
        value=Decimal("500"),
        ownership=Decimal("1"),
        bridge=SegmentBridge(
            cash=Decimal("20"),
            non_operating_investments=Decimal("0"),
            debt=Decimal("100"),
            preferred_equity=Decimal("0"),
            noncontrolling_interest=Decimal("0"),
            evidence_references=("artifact:industrial-bridge",),
        ),
        evidence_references=("valuation:industrial",),
    )


def segment_bank():
    a = assessment(
        archetype=CompanyArchetype.BANK,
        regulatory=True,
    )
    p = permit_for(a, ValuationMethod.RESIDUAL_INCOME)
    return SegmentValuation(
        segment_id="bank",
        segment_name="Bank",
        as_of=AS_OF,
        method=ValuationMethod.RESIDUAL_INCOME,
        methodology_assessment=a,
        method_permit=p,
        valuation_reference_id="residual-income:bank",
        basis=SegmentValueBasis.EQUITY_VALUE,
        value=Decimal("300"),
        ownership=Decimal("0.80"),
        bridge=None,
        evidence_references=("valuation:bank",),
    )


def company_gate():
    a = assessment(
        archetype=CompanyArchetype.CONGLOMERATE,
        segments=True,
    )
    p = permit_for(a, ValuationMethod.SOTP)
    return a, p


def corporate(**overrides):
    values = {
        "as_of": AS_OF,
        "cash": Decimal("10"),
        "non_operating_investments": Decimal("20"),
        "debt": Decimal("50"),
        "preferred_equity": Decimal("0"),
        "noncontrolling_interest": Decimal("0"),
        "other_assets": Decimal("5"),
        "other_liabilities": Decimal("10"),
        "corporate_cost_value": Decimal("30"),
        "intercompany_elimination": Decimal("5"),
        "diluted_shares": Decimal("100"),
        "evidence_references": ("artifact:corporate",),
    }
    values.update(overrides)
    return CorporateItems(**values)


def reconciliation(**overrides):
    values = {
        "as_of": AS_OF,
        "reported_cash": Decimal("30"),
        "reported_debt": Decimal("150"),
        "evidence_references": ("artifact:group-balance-sheet",),
    }
    values.update(overrides)
    return GroupReconciliationReference(**values)


class SOTPTests(unittest.TestCase):
    def test_mixed_segment_methods_aggregate_to_equity(self):
        company_assessment, company_permit = company_gate()
        result = SOTPEngine().value(
            company_assessment=company_assessment,
            company_method_permit=company_permit,
            segments=(segment_operating(), segment_bank()),
            corporate=corporate(),
            reconciliation=reconciliation(),
        )
        self.assertEqual(
            result.gross_attributable_segment_equity,
            Decimal("660.00"),
        )
        self.assertEqual(result.corporate_net_adjustment, Decimal("-60"))
        self.assertEqual(result.equity_value, Decimal("600.00"))
        self.assertEqual(result.value_per_diluted_share, Decimal("6.00"))

    def test_company_requires_sotp_permit(self):
        company_assessment, _ = company_gate()
        wrong_permit = permit_for(
            assessment(),
            ValuationMethod.FCFF_DCF,
        )
        with self.assertRaises(ValueError):
            SOTPEngine().value(
                company_assessment=company_assessment,
                company_method_permit=wrong_permit,
                segments=(segment_operating(), segment_bank()),
                corporate=corporate(),
                reconciliation=reconciliation(),
            )

    def test_segment_method_permit_must_match_segment_method(self):
        good = segment_operating()
        bank_assessment = assessment(
            archetype=CompanyArchetype.BANK,
            regulatory=True,
        )
        wrong_permit = permit_for(
            bank_assessment,
            ValuationMethod.RESIDUAL_INCOME,
        )
        broken = SegmentValuation(
            segment_id=good.segment_id,
            segment_name=good.segment_name,
            as_of=good.as_of,
            method=good.method,
            methodology_assessment=good.methodology_assessment,
            method_permit=wrong_permit,
            valuation_reference_id=good.valuation_reference_id,
            basis=good.basis,
            value=good.value,
            ownership=good.ownership,
            bridge=good.bridge,
            evidence_references=good.evidence_references,
        )
        company_assessment, company_permit = company_gate()
        with self.assertRaises(ValueError):
            SOTPEngine().value(
                company_assessment=company_assessment,
                company_method_permit=company_permit,
                segments=(broken, segment_bank()),
                corporate=corporate(),
                reconciliation=reconciliation(),
            )

    def test_cash_allocation_must_reconcile_to_group(self):
        company_assessment, company_permit = company_gate()
        with self.assertRaises(ValueError):
            SOTPEngine().value(
                company_assessment=company_assessment,
                company_method_permit=company_permit,
                segments=(segment_operating(), segment_bank()),
                corporate=corporate(),
                reconciliation=reconciliation(reported_cash=Decimal("31")),
            )

    def test_debt_allocation_must_reconcile_to_group(self):
        company_assessment, company_permit = company_gate()
        with self.assertRaises(ValueError):
            SOTPEngine().value(
                company_assessment=company_assessment,
                company_method_permit=company_permit,
                segments=(segment_operating(), segment_bank()),
                corporate=corporate(),
                reconciliation=reconciliation(reported_debt=Decimal("149")),
            )

    def test_intercompany_elimination_changes_identity_and_value(self):
        company_assessment, company_permit = company_gate()
        engine = SOTPEngine()
        first = engine.value(
            company_assessment=company_assessment,
            company_method_permit=company_permit,
            segments=(segment_operating(), segment_bank()),
            corporate=corporate(intercompany_elimination=Decimal("5")),
            reconciliation=reconciliation(),
        )
        second = engine.value(
            company_assessment=company_assessment,
            company_method_permit=company_permit,
            segments=(segment_operating(), segment_bank()),
            corporate=corporate(intercompany_elimination=Decimal("10")),
            reconciliation=reconciliation(),
        )
        self.assertNotEqual(first.valuation_id, second.valuation_id)
        self.assertEqual(first.equity_value - second.equity_value, Decimal("5"))

    def test_segment_as_of_mismatch_fails_closed(self):
        good = segment_bank()
        shifted = SegmentValuation(
            segment_id=good.segment_id,
            segment_name=good.segment_name,
            as_of=datetime(2026, 9, 24, tzinfo=UTC),
            method=good.method,
            methodology_assessment=good.methodology_assessment,
            method_permit=good.method_permit,
            valuation_reference_id=good.valuation_reference_id,
            basis=good.basis,
            value=good.value,
            ownership=good.ownership,
            bridge=good.bridge,
            evidence_references=good.evidence_references,
        )
        company_assessment, company_permit = company_gate()
        with self.assertRaises(ValueError):
            SOTPEngine().value(
                company_assessment=company_assessment,
                company_method_permit=company_permit,
                segments=(segment_operating(), shifted),
                corporate=corporate(),
                reconciliation=reconciliation(),
            )


if __name__ == "__main__":
    unittest.main()
