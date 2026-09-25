import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from quantos.comparables import (
    ComparableCompanyEngine,
    CompsTargetFinancials,
    CompsTargetProfile,
    CompsValuationPolicy,
    DistributionStatus,
    ImpliedValuationStatus,
    MultipleKind,
    PeerSelectionPolicy,
    PeerSelector,
    PeerSnapshot,
)
from quantos.valuation import EquityBridge
from quantos.valuation_methodology import (
    CashFlowVisibility,
    CompanyArchetype,
    ValuationMethod,
    ValuationMethodologyEngine,
    ValuationMethodologyGate,
    ValuationProfile,
)

UTC = timezone.utc
AS_OF = datetime(2026, 9, 25, 16, tzinfo=UTC)


def peer(entity_id, revenue, *, industry="Software", geography="US",
         tags=("subscription",), distressed=False,
         knowledge_time=AS_OF - timedelta(hours=1), ev=None):
    revenue = Decimal(str(revenue))
    return PeerSnapshot(
        entity_id=entity_id,
        knowledge_time=knowledge_time,
        market_as_of=knowledge_time,
        industry=industry,
        geography=geography,
        business_tags=tags,
        distressed=distressed,
        revenue=revenue,
        ebitda=revenue * Decimal("0.20"),
        ebit=revenue * Decimal("0.15"),
        net_income=revenue * Decimal("0.10"),
        book_equity=revenue * Decimal("0.50"),
        free_cash_flow=revenue * Decimal("0.08"),
        enterprise_value=Decimal(str(ev)) if ev is not None else revenue * Decimal("4"),
        equity_value=revenue * Decimal("3.5"),
        evidence_references=(f"artifact:{entity_id}",),
    )


def target_profile():
    return CompsTargetProfile(
        entity_id="TARGET", as_of=AS_OF, industry="Software", geography="US",
        business_tags=("subscription",), revenue=Decimal("100"),
        evidence_references=("claim:target-profile",),
    )


def selection_policy():
    return PeerSelectionPolicy(
        require_same_industry=True,
        allowed_geographies=("US", "UK"),
        required_business_tags=("subscription",),
        minimum_revenue_ratio=Decimal("0.50"),
        maximum_revenue_ratio=Decimal("2.00"),
        exclude_distressed=True,
        minimum_included_peers=3,
        rationale="Comparable operating model, geography and size range.",
        evidence_references=("review:peer-policy",),
    )


def candidates():
    return (
        peer("P1", "80", ev="280"),
        peer("P2", "100", ev="400"),
        peer("P3", "120", ev="540"),
        peer("P4", "150", ev="750"),
    )


def valuation_policy():
    return CompsValuationPolicy(
        minimum_peers_per_multiple=3,
        percentiles=(Decimal("0.25"), Decimal("0.50"), Decimal("0.75")),
        rationale="Show interquartile valuation ranges.",
        evidence_references=("review:comps-policy",),
    )


def target_financials(target):
    return CompsTargetFinancials(
        target_id=target.target_id,
        entity_id=target.entity_id,
        as_of=target.as_of,
        revenue=Decimal("100"),
        ebitda=Decimal("20"),
        ebit=Decimal("15"),
        net_income=Decimal("10"),
        book_equity=Decimal("50"),
        free_cash_flow=Decimal("8"),
        equity_bridge=EquityBridge(
            cash=Decimal("20"),
            non_operating_investments=Decimal("5"),
            debt=Decimal("40"),
            preferred_equity=Decimal("0"),
            noncontrolling_interest=Decimal("0"),
            diluted_shares=Decimal("10"),
            as_of=target.as_of,
            evidence_references=("artifact:target-bridge",),
        ),
        evidence_references=("artifact:target-financials",),
    )


def method_gate(selection_id):
    assessment = ValuationMethodologyEngine().assess(
        ValuationProfile(
            entity_id="TARGET", as_of=AS_OF,
            archetype=CompanyArchetype.GENERAL_OPERATING,
            cash_flow_visibility=CashFlowVisibility.HIGH,
            positive_fcff=True, positive_fcfe=True, material_dividend=False,
            regulatory_capital_central=False, has_segment_disclosure=False,
            segment_economics_divergent=False, peer_set_available=False,
            going_concern_uncertainty=False, distressed=False,
            stable_leverage_capacity=True,
            evidence_references=("claim:valuation-profile",),
        )
    )
    permit = ValuationMethodologyGate().issue(
        assessment=assessment,
        method=ValuationMethod.TRADING_COMPS,
        condition_evidence={"PEER_SET_VALIDATED": (selection_id,)},
    )
    return assessment, permit


class ComparableCompanyTests(unittest.TestCase):
    def test_selection_preserves_exclusion_reasons(self):
        target = target_profile()
        result = PeerSelector().select(
            target=target,
            candidates=(
                peer("P1", "80"),
                peer("P2", "100", industry="Hardware"),
                peer("P3", "100", distressed=True),
                peer("P4", "100", knowledge_time=AS_OF + timedelta(minutes=1)),
                peer("P5", "400"),
            ),
            policy=selection_policy(),
        )
        by_entity = {item.entity_id: item for item in result.decisions}
        self.assertTrue(by_entity["P1"].included)
        self.assertIn("INDUSTRY_MISMATCH", by_entity["P2"].exclusion_reasons)
        self.assertIn("DISTRESSED_EXCLUDED", by_entity["P3"].exclusion_reasons)
        self.assertIn("KNOWLEDGE_AFTER_AS_OF", by_entity["P4"].exclusion_reasons)
        self.assertIn("REVENUE_ABOVE_POLICY_RANGE", by_entity["P5"].exclusion_reasons)

    def test_selection_identity_changes_with_candidate_snapshot(self):
        target = target_profile()
        first = PeerSelector().select(
            target=target, candidates=candidates(), policy=selection_policy()
        )
        changed = list(candidates())
        changed[0] = peer("P1", "81", ev="280")
        second = PeerSelector().select(
            target=target, candidates=tuple(changed), policy=selection_policy()
        )
        self.assertNotEqual(first.selection_id, second.selection_id)

    def test_comps_requires_permit_bound_to_selection(self):
        target = target_profile()
        selection = PeerSelector().select(
            target=target, candidates=candidates(), policy=selection_policy()
        )
        assessment, permit = method_gate(selection.selection_id)
        result = ComparableCompanyEngine().value(
            selection=selection,
            target=target_financials(target),
            policy=valuation_policy(),
            methodology_assessment=assessment,
            method_permit=permit,
        )
        self.assertEqual(result.method_permit_id, permit.permit_id)

    def test_distribution_uses_all_peers_and_multiple_percentiles(self):
        target = target_profile()
        selection = PeerSelector().select(
            target=target, candidates=candidates(), policy=selection_policy()
        )
        assessment, permit = method_gate(selection.selection_id)
        result = ComparableCompanyEngine().value(
            selection=selection,
            target=target_financials(target),
            policy=valuation_policy(),
            methodology_assessment=assessment,
            method_permit=permit,
        )
        dist = next(x for x in result.distributions if x.kind is MultipleKind.EV_REVENUE)
        self.assertEqual(dist.status, DistributionStatus.AVAILABLE)
        self.assertEqual(dist.peer_count, 4)
        self.assertEqual(len(dist.percentiles), 3)

    def test_implied_valuation_is_a_range(self):
        target = target_profile()
        selection = PeerSelector().select(
            target=target, candidates=candidates(), policy=selection_policy()
        )
        assessment, permit = method_gate(selection.selection_id)
        result = ComparableCompanyEngine().value(
            selection=selection,
            target=target_financials(target),
            policy=valuation_policy(),
            methodology_assessment=assessment,
            method_permit=permit,
        )
        rng = next(x for x in result.implied_ranges if x.kind is MultipleKind.EV_EBITDA)
        self.assertEqual(rng.status, ImpliedValuationStatus.AVAILABLE)
        self.assertEqual(len(rng.points), 3)
        self.assertLess(rng.points[0].value_per_diluted_share, rng.points[-1].value_per_diluted_share)

    def test_future_known_peer_is_excluded(self):
        target = target_profile()
        selection = PeerSelector().select(
            target=target,
            candidates=candidates() + (
                peer("FUTURE", "100", knowledge_time=AS_OF + timedelta(days=1)),
            ),
            policy=selection_policy(),
        )
        self.assertNotIn("FUTURE", selection.included_peer_ids)

    def test_non_positive_target_denominator_is_not_applicable(self):
        target = target_profile()
        selection = PeerSelector().select(
            target=target, candidates=candidates(), policy=selection_policy()
        )
        assessment, permit = method_gate(selection.selection_id)
        base = target_financials(target)
        changed = CompsTargetFinancials(
            target_id=base.target_id, entity_id=base.entity_id, as_of=base.as_of,
            revenue=base.revenue, ebitda=Decimal("-1"), ebit=base.ebit,
            net_income=base.net_income, book_equity=base.book_equity,
            free_cash_flow=base.free_cash_flow, equity_bridge=base.equity_bridge,
            evidence_references=base.evidence_references,
        )
        result = ComparableCompanyEngine().value(
            selection=selection, target=changed, policy=valuation_policy(),
            methodology_assessment=assessment, method_permit=permit,
        )
        rng = next(x for x in result.implied_ranges if x.kind is MultipleKind.EV_EBITDA)
        self.assertEqual(rng.status, ImpliedValuationStatus.TARGET_NOT_APPLICABLE)


if __name__ == "__main__":
    unittest.main()
