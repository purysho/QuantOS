import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from quantos.research_universe import (
    AssetClass,
    CorporateActionObservation,
    CorporateActionType,
    ListingObservation,
    ListingStatus,
    MarketEligibilityObservation,
    SecurityType,
    UniverseBuilder,
    UniversePolicy,
    UniverseStore,
)

UTC = timezone.utc
AS_OF = datetime(2021, 6, 1, 20, tzinfo=UTC)


def listing(
    security_id="SEC:1",
    *,
    ticker="AAA",
    effective_from=date(2020, 1, 1),
    effective_to=None,
    knowledge_time=AS_OF - timedelta(days=1),
    status=ListingStatus.ACTIVE,
):
    return ListingObservation(
        security_id=security_id,
        issuer_id=f"ISSUER:{security_id}",
        listing_id=f"LISTING:{security_id}",
        ticker=ticker,
        exchange="NYSE",
        country="US",
        currency="USD",
        asset_class=AssetClass.EQUITY,
        security_type=SecurityType.COMMON_STOCK,
        is_primary=True,
        effective_from=effective_from,
        effective_to=effective_to,
        status=status,
        knowledge_time=knowledge_time,
        evidence_references=(f"artifact:listing:{security_id}:{ticker}",),
    )


def market(
    security_id="SEC:1",
    *,
    market_time=AS_OF - timedelta(hours=1),
    knowledge_time=None,
    price="20",
    addv="5000000",
    market_cap="1000000000",
    history=500,
    suspended=False,
):
    return MarketEligibilityObservation(
        security_id=security_id,
        market_time=market_time,
        knowledge_time=knowledge_time or market_time + timedelta(minutes=1),
        close_price=Decimal(price),
        average_daily_dollar_volume=Decimal(addv),
        market_cap=Decimal(market_cap),
        trading_days_history=history,
        suspended=suspended,
        evidence_references=(f"artifact:market:{security_id}",),
    )


def policy(**overrides):
    values = {
        "allowed_asset_classes": (AssetClass.EQUITY,),
        "allowed_security_types": (SecurityType.COMMON_STOCK,),
        "allowed_exchanges": ("NYSE", "NASDAQ"),
        "allowed_countries": ("US",),
        "allowed_currencies": ("USD",),
        "primary_listing_only": True,
        "minimum_price": Decimal("5"),
        "minimum_average_daily_dollar_volume": Decimal("1000000"),
        "minimum_market_cap": Decimal("500000000"),
        "minimum_trading_days_history": 252,
        "maximum_market_age_days": 3,
        "exclude_suspended": True,
        "exclude_pending_actions": (
            CorporateActionType.MERGER,
            CorporateActionType.ACQUISITION,
            CorporateActionType.DELISTING,
            CorporateActionType.BANKRUPTCY,
        ),
        "rationale": "Liquid US common-equity research universe.",
        "evidence_references": ("research-policy:universe",),
    }
    values.update(overrides)
    return UniversePolicy(**values)


class ResearchUniverseTests(unittest.TestCase):
    def test_clean_security_is_included(self):
        result = UniverseBuilder().build(
            as_of=AS_OF,
            listings=(listing(),),
            market=(market(),),
            corporate_actions=(),
            policy=policy(),
        )
        self.assertEqual(result.included_security_ids, ("SEC:1",))
        self.assertEqual(result.decisions[0].ticker, "AAA")

    def test_ticker_change_preserves_stable_security_identity(self):
        old_as_of = datetime(2021, 5, 31, 20, tzinfo=UTC)
        history = (
            listing(
                ticker="AAA",
                effective_from=date(2020, 1, 1),
                effective_to=date(2021, 5, 31),
                knowledge_time=datetime(2021, 5, 1, tzinfo=UTC),
            ),
            listing(
                ticker="BBB",
                effective_from=date(2021, 6, 1),
                knowledge_time=datetime(2021, 5, 15, tzinfo=UTC),
            ),
        )
        old = UniverseBuilder().build(
            as_of=old_as_of,
            listings=history,
            market=(market(market_time=old_as_of - timedelta(hours=1)),),
            corporate_actions=(),
            policy=policy(),
        )
        new = UniverseBuilder().build(
            as_of=AS_OF,
            listings=history,
            market=(market(),),
            corporate_actions=(),
            policy=policy(),
        )
        self.assertEqual(old.included_security_ids, ("SEC:1",))
        self.assertEqual(new.included_security_ids, ("SEC:1",))
        self.assertEqual(old.decisions[0].ticker, "AAA")
        self.assertEqual(new.decisions[0].ticker, "BBB")

    def test_future_known_delisting_does_not_leak_backward(self):
        original = listing(
            knowledge_time=datetime(2021, 5, 1, tzinfo=UTC),
        )
        later_revision = listing(
            effective_to=date(2021, 5, 20),
            status=ListingStatus.DELISTED,
            knowledge_time=datetime(2021, 6, 2, tzinfo=UTC),
        )
        before_knowledge = UniverseBuilder().build(
            as_of=AS_OF,
            listings=(original, later_revision),
            market=(market(),),
            corporate_actions=(),
            policy=policy(),
        )
        self.assertEqual(before_knowledge.included_security_ids, ("SEC:1",))

        after = datetime(2021, 6, 3, 20, tzinfo=UTC)
        after_knowledge = UniverseBuilder().build(
            as_of=after,
            listings=(original, later_revision),
            market=(
                market(
                    market_time=datetime(2021, 6, 1, 19, tzinfo=UTC),
                    knowledge_time=datetime(2021, 6, 1, 19, 1, tzinfo=UTC),
                ),
            ),
            corporate_actions=(),
            policy=policy(maximum_market_age_days=5),
        )
        self.assertNotIn("SEC:1", after_knowledge.included_security_ids)
        self.assertIn(
            "NO_ACTIVE_LISTING",
            after_knowledge.decisions[0].exclusion_reasons,
        )

    def test_future_market_observation_is_not_used(self):
        future = market(
            market_time=AS_OF + timedelta(minutes=1),
            knowledge_time=AS_OF + timedelta(minutes=2),
        )
        result = UniverseBuilder().build(
            as_of=AS_OF,
            listings=(listing(),),
            market=(future,),
            corporate_actions=(),
            policy=policy(),
        )
        self.assertIn(
            "MISSING_MARKET_DATA",
            result.decisions[0].exclusion_reasons,
        )

    def test_price_liquidity_market_cap_and_history_filters_are_explicit(self):
        result = UniverseBuilder().build(
            as_of=AS_OF,
            listings=(listing(),),
            market=(
                market(
                    price="2",
                    addv="100",
                    market_cap="1000",
                    history=20,
                ),
            ),
            corporate_actions=(),
            policy=policy(),
        )
        reasons = set(result.decisions[0].exclusion_reasons)
        self.assertIn("PRICE_BELOW_MINIMUM", reasons)
        self.assertIn("LIQUIDITY_BELOW_MINIMUM", reasons)
        self.assertIn("MARKET_CAP_BELOW_MINIMUM", reasons)
        self.assertIn("INSUFFICIENT_TRADING_HISTORY", reasons)

    def test_pending_merger_is_excluded_only_after_it_is_known(self):
        action = CorporateActionObservation(
            security_id="SEC:1",
            action_type=CorporateActionType.MERGER,
            announced_at=datetime(2021, 5, 20, tzinfo=UTC),
            effective_at=datetime(2021, 7, 1, tzinfo=UTC),
            knowledge_time=datetime(2021, 5, 20, 0, 1, tzinfo=UTC),
            evidence_references=("artifact:merger",),
        )
        result = UniverseBuilder().build(
            as_of=AS_OF,
            listings=(listing(),),
            market=(market(),),
            corporate_actions=(action,),
            policy=policy(),
        )
        self.assertIn(
            "PENDING_ACTION:MERGER",
            result.decisions[0].exclusion_reasons,
        )

        earlier = datetime(2021, 5, 19, 20, tzinfo=UTC)
        earlier_result = UniverseBuilder().build(
            as_of=earlier,
            listings=(listing(knowledge_time=datetime(2021, 5, 1, tzinfo=UTC)),),
            market=(
                market(
                    market_time=earlier - timedelta(hours=1),
                    knowledge_time=earlier - timedelta(minutes=50),
                ),
            ),
            corporate_actions=(action,),
            policy=policy(),
        )
        self.assertEqual(earlier_result.included_security_ids, ("SEC:1",))

    def test_universe_store_is_idempotent(self):
        universe = UniverseBuilder().build(
            as_of=AS_OF,
            listings=(listing(),),
            market=(market(),),
            corporate_actions=(),
            policy=policy(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = UniverseStore(Path(tmp) / "universe.duckdb")
            self.assertTrue(store.add(universe))
            self.assertFalse(store.add(universe))
            manifest = store.get_manifest(universe.universe_id)
            self.assertEqual(manifest["universe_id"], universe.universe_id)
            self.assertEqual(
                manifest["decisions"][0]["security_id"],
                "SEC:1",
            )
            store.close()


if __name__ == "__main__":
    unittest.main()
