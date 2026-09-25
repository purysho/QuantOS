import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from quantos.comparables import (
    CompsTargetFinancials,
    CompsTargetProfile,
    CompsValuationPolicy,
    MultipleKind,
    PeerSelectionPolicy,
    PeerSelector,
    PeerSnapshot,
)
from quantos.comps_normalization import (
    AdjustmentKind,
    NormalizationAdjustment,
    NormalizationMetric,
    NormalizedComparableCompanyEngine,
    PeerNormalizationEngine,
    SectorMetricContract,
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


def peer(entity, revenue):
    revenue = Decimal(str(revenue))
    return PeerSnapshot(
        entity_id=entity,
        knowledge_time=AS_OF - timedelta(hours=2),
        market_as_of=AS_OF - timedelta(hours=2),
        industry="Software",
        geography="US",
        business_tags=("subscription",),
        distressed=False,
        revenue=revenue,
        ebitda=revenue * Decimal("0.20"),
        ebit=revenue * Decimal("0.15"),
        net_income=revenue * Decimal("0.10"),
        book_equity=revenue * Decimal("0.50"),
        free_cash_flow=revenue * Decimal("0.08"),
        enterprise_value=revenue * Decimal("4"),
        equity_value=revenue * Decimal("3.5"),
        evidence_references=(f"artifact:{entity}",),
    )


def target_profile():
    return CompsTargetProfile(
        entity_id="TARGET",
        as_of=AS_OF,
        industry="Software",
        geography="US",
        business_tags=("subscription",),
        revenue=Decimal("100"),
        evidence_references=("claim:target",),
    )


def selection():
    return PeerSelector().select(
        target=target_profile(),
        candidates=(peer("P1", 80), peer("P2", 100), peer("P3", 120)),
        policy=PeerSelectionPolicy(
            require_same_industry=True,
            allowed_geographies=("US",),
            required_business_tags=("subscription",),
            minimum_revenue_ratio=Decimal("0.5"),
            maximum_revenue_ratio=Decimal("2"),
            exclude_distressed=True,
            minimum_included_peers=3,
            rationale="Reviewed peer policy.",
            evidence_references=("review:peers",),
        ),
    )


def target_financials():
    profile = target_profile()
    return CompsTargetFinancials(
        target_id=profile.target_id,
        entity_id=profile.entity_id,
        as_of=profile.as_of,
        revenue=Decimal("100"),
        ebitda=Decimal("20"),
        ebit=Decimal("15"),
        net_income=Decimal("10"),
        book_equity=Decimal("50"),
        free_cash_flow=Decimal("8"),
        equity_bridge=EquityBridge(
            cash=Decimal("20"),
            non_operating_investments=Decimal("0"),
            debt=Decimal("40"),
            preferred_equity=Decimal("0"),
            noncontrolling_interest=Decimal("0"),
            diluted_shares=Decimal("10"),
            as_of=AS_OF,
            evidence_references=("artifact:bridge",),
        ),
        evidence_references=("artifact:target-financials",),
    )


def policy():
    return CompsValuationPolicy(
        minimum_peers_per_multiple=3,
        percentiles=(Decimal("0.25"), Decimal("0.50"), Decimal("0.75")),
        rationale="Reviewed distribution policy.",
        evidence_references=("review:distribution",),
    )


def permit(selection_id):
    assessment = ValuationMethodologyEngine().assess(
        ValuationProfile(
            entity_id="TARGET",
            as_of=AS_OF,
            archetype=CompanyArchetype.GENERAL_OPERATING,
            cash_flow_visibility=CashFlowVisibility.HIGH,
            positive_fcff=True,
            positive_fcfe=True,
            material_dividend=False,
            regulatory_capital_central=False,
            has_segment_disclosure=False,
            segment_economics_divergent=False,
            peer_set_available=False,
            going_concern_uncertainty=False,
            distressed=False,
            stable_leverage_capacity=True,
            evidence_references=("claim:valuation-profile",),
        )
    )
    method_permit = ValuationMethodologyGate().issue(
        assessment=assessment,
        method=ValuationMethod.TRADING_COMPS,
        condition_evidence={"PEER_SET_VALIDATED": (selection_id,)},
    )
    return assessment, method_permit


def contract(*kinds):
    return SectorMetricContract(
        industry="Software",
        allowed_multiple_kinds=kinds,
        rationale="Metrics appropriate to reviewed software peer set.",
        evidence_references=("review:sector-contract",),
    )


class CompsNormalizationTests(unittest.TestCase):
    def test_preparer_cannot_review_own_adjustment(self):
        selected = selection()
        raw = selected.included_snapshots[0]
        adjustment = NormalizationAdjustment(
            raw_snapshot_id=raw.snapshot_id,
            metric=NormalizationMetric.EBITDA,
            amount=Decimal("2"),
            kind=AdjustmentKind.NON_RECURRING,
            knowledge_time=AS_OF - timedelta(hours=1),
            rationale="Remove one-time restructuring cost.",
            evidence_references=("artifact:adjustment",),
            prepared_by="analyst-a",
        )
        with self.assertRaises(ValueError):
            PeerNormalizationEngine().build(
                selection=selected,
                adjustments=(adjustment,),
                reviewer="analyst-a",
                reviewed_at=AS_OF,
                review_notes="Reviewed.",
            )

    def test_future_known_adjustment_fails_closed(self):
        selected = selection()
        raw = selected.included_snapshots[0]
        adjustment = NormalizationAdjustment(
            raw_snapshot_id=raw.snapshot_id,
            metric=NormalizationMetric.EBITDA,
            amount=Decimal("2"),
            kind=AdjustmentKind.NON_RECURRING,
            knowledge_time=AS_OF + timedelta(minutes=1),
            rationale="Future-known adjustment.",
            evidence_references=("artifact:future",),
            prepared_by="analyst-a",
        )
        with self.assertRaises(ValueError):
            PeerNormalizationEngine().build(
                selection=selected,
                adjustments=(adjustment,),
                reviewer="reviewer-b",
                reviewed_at=AS_OF,
                review_notes="Reviewed.",
            )

    def test_normalization_preserves_raw_snapshot_and_changes_dataset_identity(self):
        selected = selection()
        raw = selected.included_snapshots[0]
        base = PeerNormalizationEngine().build(
            selection=selected,
            adjustments=(),
            reviewer="reviewer-b",
            reviewed_at=AS_OF,
            review_notes="No adjustments.",
        )
        adjustment = NormalizationAdjustment(
            raw_snapshot_id=raw.snapshot_id,
            metric=NormalizationMetric.EBITDA,
            amount=Decimal("4"),
            kind=AdjustmentKind.NON_RECURRING,
            knowledge_time=AS_OF - timedelta(hours=1),
            rationale="Reviewed one-time item.",
            evidence_references=("artifact:adjustment",),
            prepared_by="analyst-a",
        )
        changed = PeerNormalizationEngine().build(
            selection=selected,
            adjustments=(adjustment,),
            reviewer="reviewer-b",
            reviewed_at=AS_OF,
            review_notes="Adjustment accepted.",
        )
        self.assertNotEqual(base.dataset_id, changed.dataset_id)
        changed_peer = next(
            item
            for item in changed.peers
            if item.raw_snapshot.snapshot_id == raw.snapshot_id
        )
        self.assertEqual(changed_peer.ebitda, raw.ebitda + Decimal("4"))
        self.assertEqual(raw.ebitda, Decimal("16.00"))

    def test_sector_contract_limits_metrics_used(self):
        selected = selection()
        normalized = PeerNormalizationEngine().build(
            selection=selected,
            adjustments=(),
            reviewer="reviewer-b",
            reviewed_at=AS_OF,
            review_notes="No adjustments required.",
        )
        assessment, method_permit = permit(selected.selection_id)
        result = NormalizedComparableCompanyEngine().value(
            selection=selected,
            normalization=normalized,
            target_profile=target_profile(),
            target=target_financials(),
            metric_contract=contract(
                MultipleKind.EV_REVENUE,
                MultipleKind.EV_EBITDA,
            ),
            policy=policy(),
            methodology_assessment=assessment,
            method_permit=method_permit,
        )
        self.assertEqual(
            tuple(item.kind for item in result.distributions),
            (MultipleKind.EV_REVENUE, MultipleKind.EV_EBITDA),
        )

    def test_wrong_industry_metric_contract_fails_closed(self):
        selected = selection()
        normalized = PeerNormalizationEngine().build(
            selection=selected,
            adjustments=(),
            reviewer="reviewer-b",
            reviewed_at=AS_OF,
            review_notes="Reviewed.",
        )
        assessment, method_permit = permit(selected.selection_id)
        wrong = SectorMetricContract(
            industry="Banking",
            allowed_multiple_kinds=(MultipleKind.PE, MultipleKind.PB),
            rationale="Bank metrics.",
            evidence_references=("review:bank-contract",),
        )
        with self.assertRaises(ValueError):
            NormalizedComparableCompanyEngine().value(
                selection=selected,
                normalization=normalized,
                target_profile=target_profile(),
                target=target_financials(),
                metric_contract=wrong,
                policy=policy(),
                methodology_assessment=assessment,
                method_permit=method_permit,
            )

    def test_normalization_changes_multiple_distribution(self):
        selected = selection()
        raw = selected.included_snapshots[0]
        base = PeerNormalizationEngine().build(
            selection=selected,
            adjustments=(),
            reviewer="reviewer-b",
            reviewed_at=AS_OF,
            review_notes="Base.",
        )
        adjustment = NormalizationAdjustment(
            raw_snapshot_id=raw.snapshot_id,
            metric=NormalizationMetric.EBITDA,
            amount=Decimal("8"),
            kind=AdjustmentKind.ACCOUNTING_POLICY,
            knowledge_time=AS_OF - timedelta(hours=1),
            rationale="Normalize capitalization policy.",
            evidence_references=("artifact:policy-note",),
            prepared_by="analyst-a",
        )
        changed = PeerNormalizationEngine().build(
            selection=selected,
            adjustments=(adjustment,),
            reviewer="reviewer-b",
            reviewed_at=AS_OF,
            review_notes="Reviewed adjustment.",
        )
        assessment, method_permit = permit(selected.selection_id)
        kwargs = dict(
            selection=selected,
            target_profile=target_profile(),
            target=target_financials(),
            metric_contract=contract(MultipleKind.EV_EBITDA),
            policy=policy(),
            methodology_assessment=assessment,
            method_permit=method_permit,
        )
        first = NormalizedComparableCompanyEngine().value(
            normalization=base, **kwargs
        )
        second = NormalizedComparableCompanyEngine().value(
            normalization=changed, **kwargs
        )
        self.assertNotEqual(first.valuation_id, second.valuation_id)
        self.assertNotEqual(
            first.distributions[0].percentiles,
            second.distributions[0].percentiles,
        )


if __name__ == "__main__":
    unittest.main()
