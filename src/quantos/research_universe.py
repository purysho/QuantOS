from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

import duckdb


class ListingStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DELISTED = "DELISTED"


class AssetClass(str, Enum):
    EQUITY = "EQUITY"
    ETF = "ETF"


class SecurityType(str, Enum):
    COMMON_STOCK = "COMMON_STOCK"
    ADR = "ADR"
    REIT = "REIT"
    PREFERRED = "PREFERRED"


class CorporateActionType(str, Enum):
    MERGER = "MERGER"
    ACQUISITION = "ACQUISITION"
    DELISTING = "DELISTING"
    BANKRUPTCY = "BANKRUPTCY"
    SPLIT = "SPLIT"
    SYMBOL_CHANGE = "SYMBOL_CHANGE"
    SPINOFF = "SPINOFF"


TERMINATING_ACTIONS = {
    CorporateActionType.MERGER,
    CorporateActionType.ACQUISITION,
    CorporateActionType.DELISTING,
    CorporateActionType.BANKRUPTCY,
}


@dataclass(frozen=True)
class ListingObservation:
    security_id: str
    issuer_id: str
    listing_id: str
    ticker: str
    exchange: str
    country: str
    currency: str
    asset_class: AssetClass
    security_type: SecurityType
    is_primary: bool
    effective_from: date
    effective_to: date | None
    status: ListingStatus
    knowledge_time: datetime
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "security_id",
            "issuer_id",
            "listing_id",
            "ticker",
            "exchange",
            "country",
            "currency",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
        if len(self.currency.strip()) != 3:
            raise ValueError("currency must be a three-letter code")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("listing effective_to cannot precede effective_from")
        if self.knowledge_time.tzinfo is None:
            raise ValueError("listing knowledge_time must be timezone-aware")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("listing observation requires evidence references")

    @property
    def fact_id(self) -> str:
        return _content_id(
            "listing-fact",
            {
                "security_id": self.security_id,
                "issuer_id": self.issuer_id,
                "listing_id": self.listing_id,
                "ticker": self.ticker,
                "exchange": self.exchange,
                "country": self.country,
                "currency": self.currency,
                "asset_class": self.asset_class.value,
                "security_type": self.security_type.value,
                "is_primary": self.is_primary,
                "effective_from": self.effective_from.isoformat(),
                "effective_to": (
                    self.effective_to.isoformat()
                    if self.effective_to is not None
                    else None
                ),
                "status": self.status.value,
                "knowledge_time": self.knowledge_time.isoformat(),
                "evidence_references": list(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class MarketEligibilityObservation:
    security_id: str
    market_time: datetime
    knowledge_time: datetime
    close_price: Decimal
    average_daily_dollar_volume: Decimal
    market_cap: Decimal
    trading_days_history: int
    suspended: bool
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.security_id.strip():
            raise ValueError("market observation security_id is required")
        if self.market_time.tzinfo is None or self.knowledge_time.tzinfo is None:
            raise ValueError("market observation timestamps must be timezone-aware")
        if self.knowledge_time < self.market_time:
            raise ValueError("market knowledge_time cannot precede market_time")
        for name in (
            "close_price",
            "average_daily_dollar_volume",
            "market_cap",
        ):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.trading_days_history < 0:
            raise ValueError("trading_days_history must be non-negative")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("market observation requires evidence references")

    @property
    def fact_id(self) -> str:
        return _content_id(
            "market-eligibility-fact",
            {
                "security_id": self.security_id,
                "market_time": self.market_time.isoformat(),
                "knowledge_time": self.knowledge_time.isoformat(),
                "close_price": str(self.close_price),
                "average_daily_dollar_volume": str(
                    self.average_daily_dollar_volume
                ),
                "market_cap": str(self.market_cap),
                "trading_days_history": self.trading_days_history,
                "suspended": self.suspended,
                "evidence_references": list(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class CorporateActionObservation:
    security_id: str
    action_type: CorporateActionType
    announced_at: datetime
    effective_at: datetime
    knowledge_time: datetime
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.security_id.strip():
            raise ValueError("corporate action security_id is required")
        for name in ("announced_at", "effective_at", "knowledge_time"):
            if getattr(self, name).tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.effective_at < self.announced_at:
            raise ValueError("corporate action effective_at precedes announcement")
        if self.knowledge_time < self.announced_at:
            raise ValueError("corporate action knowledge_time precedes announcement")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("corporate action requires evidence references")

    @property
    def fact_id(self) -> str:
        return _content_id(
            "corporate-action-fact",
            {
                "security_id": self.security_id,
                "action_type": self.action_type.value,
                "announced_at": self.announced_at.isoformat(),
                "effective_at": self.effective_at.isoformat(),
                "knowledge_time": self.knowledge_time.isoformat(),
                "evidence_references": list(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class UniversePolicy:
    allowed_asset_classes: tuple[AssetClass, ...]
    allowed_security_types: tuple[SecurityType, ...]
    allowed_exchanges: tuple[str, ...]
    allowed_countries: tuple[str, ...]
    allowed_currencies: tuple[str, ...]
    primary_listing_only: bool
    minimum_price: Decimal
    minimum_average_daily_dollar_volume: Decimal
    minimum_market_cap: Decimal
    minimum_trading_days_history: int
    maximum_market_age_days: int
    exclude_suspended: bool
    exclude_pending_actions: tuple[CorporateActionType, ...]
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.allowed_asset_classes or not self.allowed_security_types:
            raise ValueError("universe policy requires asset/security types")
        if len(self.allowed_asset_classes) != len(set(self.allowed_asset_classes)):
            raise ValueError("allowed_asset_classes contains duplicates")
        if len(self.allowed_security_types) != len(set(self.allowed_security_types)):
            raise ValueError("allowed_security_types contains duplicates")
        for values, label in (
            (self.allowed_exchanges, "allowed_exchanges"),
            (self.allowed_countries, "allowed_countries"),
            (self.allowed_currencies, "allowed_currencies"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} contains duplicates")
        for name in (
            "minimum_price",
            "minimum_average_daily_dollar_volume",
            "minimum_market_cap",
        ):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.minimum_trading_days_history < 0:
            raise ValueError("minimum_trading_days_history must be non-negative")
        if self.maximum_market_age_days < 0:
            raise ValueError("maximum_market_age_days must be non-negative")
        if len(self.exclude_pending_actions) != len(
            set(self.exclude_pending_actions)
        ):
            raise ValueError("exclude_pending_actions contains duplicates")
        if not self.rationale.strip():
            raise ValueError("universe policy rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("universe policy requires evidence references")

    @property
    def policy_id(self) -> str:
        return _content_id(
            "universe-policy",
            {
                "allowed_asset_classes": [
                    item.value for item in self.allowed_asset_classes
                ],
                "allowed_security_types": [
                    item.value for item in self.allowed_security_types
                ],
                "allowed_exchanges": list(self.allowed_exchanges),
                "allowed_countries": list(self.allowed_countries),
                "allowed_currencies": list(self.allowed_currencies),
                "primary_listing_only": self.primary_listing_only,
                "minimum_price": str(self.minimum_price),
                "minimum_average_daily_dollar_volume": str(
                    self.minimum_average_daily_dollar_volume
                ),
                "minimum_market_cap": str(self.minimum_market_cap),
                "minimum_trading_days_history": (
                    self.minimum_trading_days_history
                ),
                "maximum_market_age_days": self.maximum_market_age_days,
                "exclude_suspended": self.exclude_suspended,
                "exclude_pending_actions": [
                    item.value for item in self.exclude_pending_actions
                ],
                "rationale": self.rationale,
                "evidence_references": list(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class UniverseDecision:
    security_id: str
    issuer_id: str | None
    listing_id: str | None
    ticker: str | None
    included: bool
    exclusion_reasons: tuple[str, ...]
    fact_ids: tuple[str, ...]


@dataclass(frozen=True)
class InvestableUniverse:
    universe_id: str
    as_of: datetime
    policy_id: str
    decisions: tuple[UniverseDecision, ...]

    @property
    def included_security_ids(self) -> tuple[str, ...]:
        return tuple(
            item.security_id for item in self.decisions if item.included
        )


class UniverseBuilder:
    """Reconstruct an investable universe from only facts knowable as-of time."""

    def build(
        self,
        *,
        as_of: datetime,
        listings: tuple[ListingObservation, ...],
        market: tuple[MarketEligibilityObservation, ...],
        corporate_actions: tuple[CorporateActionObservation, ...],
        policy: UniversePolicy,
    ) -> InvestableUniverse:
        if as_of.tzinfo is None:
            raise ValueError("universe as_of must be timezone-aware")
        if not listings:
            raise ValueError("universe construction requires listing history")

        security_ids = sorted({item.security_id for item in listings})
        decisions = tuple(
            self._decide(
                security_id=security_id,
                as_of=as_of,
                listings=listings,
                market=market,
                corporate_actions=corporate_actions,
                policy=policy,
            )
            for security_id in security_ids
        )
        payload = {
            "as_of": as_of.isoformat(),
            "policy_id": policy.policy_id,
            "decisions": [
                {
                    "security_id": item.security_id,
                    "issuer_id": item.issuer_id,
                    "listing_id": item.listing_id,
                    "ticker": item.ticker,
                    "included": item.included,
                    "exclusion_reasons": list(item.exclusion_reasons),
                    "fact_ids": list(item.fact_ids),
                }
                for item in decisions
            ],
        }
        return InvestableUniverse(
            universe_id=_content_id("investable-universe", payload),
            as_of=as_of,
            policy_id=policy.policy_id,
            decisions=decisions,
        )

    def _decide(
        self,
        *,
        security_id: str,
        as_of: datetime,
        listings: tuple[ListingObservation, ...],
        market: tuple[MarketEligibilityObservation, ...],
        corporate_actions: tuple[CorporateActionObservation, ...],
        policy: UniversePolicy,
    ) -> UniverseDecision:
        listing_candidates = self._listing_candidates(
            security_id=security_id,
            as_of=as_of,
            listings=listings,
        )
        reasons: list[str] = []
        fact_ids: list[str] = []

        if policy.primary_listing_only:
            eligible_listing_candidates = tuple(
                item for item in listing_candidates if item.is_primary
            )
        else:
            eligible_listing_candidates = listing_candidates

        active = tuple(
            item
            for item in eligible_listing_candidates
            if self._listing_active(item, as_of)
        )
        if not active:
            reasons.append("NO_ACTIVE_LISTING")
            selected_listing = None
        elif len(active) > 1:
            reasons.append("AMBIGUOUS_ACTIVE_LISTING")
            selected_listing = None
            fact_ids.extend(item.fact_id for item in active)
        else:
            selected_listing = active[0]
            fact_ids.append(selected_listing.fact_id)

        selected_market = self._market_fact(
            security_id=security_id,
            as_of=as_of,
            market=market,
        )
        if selected_market is None:
            reasons.append("MISSING_MARKET_DATA")
        else:
            fact_ids.append(selected_market.fact_id)

        known_actions = tuple(
            sorted(
                (
                    action
                    for action in corporate_actions
                    if action.security_id == security_id
                    and action.announced_at <= as_of
                    and action.knowledge_time <= as_of
                ),
                key=lambda item: (
                    item.announced_at,
                    item.knowledge_time,
                    item.fact_id,
                ),
            )
        )
        fact_ids.extend(item.fact_id for item in known_actions)

        if selected_listing is not None:
            self._listing_policy_reasons(
                listing=selected_listing,
                policy=policy,
                reasons=reasons,
            )

        if selected_market is not None:
            self._market_policy_reasons(
                market=selected_market,
                as_of=as_of,
                policy=policy,
                reasons=reasons,
            )

        for action in known_actions:
            if (
                action.effective_at <= as_of
                and action.action_type in TERMINATING_ACTIONS
            ):
                reasons.append(
                    f"EFFECTIVE_TERMINATING_ACTION:{action.action_type.value}"
                )
            elif (
                action.announced_at <= as_of < action.effective_at
                and action.action_type in policy.exclude_pending_actions
            ):
                reasons.append(
                    f"PENDING_ACTION:{action.action_type.value}"
                )

        unique_reasons = tuple(dict.fromkeys(reasons))
        return UniverseDecision(
            security_id=security_id,
            issuer_id=(
                selected_listing.issuer_id
                if selected_listing is not None
                else None
            ),
            listing_id=(
                selected_listing.listing_id
                if selected_listing is not None
                else None
            ),
            ticker=(
                selected_listing.ticker
                if selected_listing is not None
                else None
            ),
            included=not unique_reasons,
            exclusion_reasons=unique_reasons,
            fact_ids=tuple(sorted(set(fact_ids))),
        )

    @staticmethod
    def _listing_candidates(
        *,
        security_id: str,
        as_of: datetime,
        listings: tuple[ListingObservation, ...],
    ) -> tuple[ListingObservation, ...]:
        known = tuple(
            item
            for item in listings
            if item.security_id == security_id
            and item.knowledge_time <= as_of
            and item.effective_from <= as_of.date()
        )
        grouped: dict[str, list[ListingObservation]] = {}
        for item in known:
            grouped.setdefault(item.listing_id, []).append(item)
        selected: list[ListingObservation] = []
        for items in grouped.values():
            selected.append(
                max(
                    items,
                    key=lambda item: (
                        item.effective_from,
                        item.knowledge_time,
                        item.fact_id,
                    ),
                )
            )
        return tuple(sorted(selected, key=lambda item: item.listing_id))

    @staticmethod
    def _listing_active(
        listing: ListingObservation,
        as_of: datetime,
    ) -> bool:
        if listing.status is ListingStatus.DELISTED:
            return False
        if listing.effective_from > as_of.date():
            return False
        if (
            listing.effective_to is not None
            and as_of.date() > listing.effective_to
        ):
            return False
        return True

    @staticmethod
    def _market_fact(
        *,
        security_id: str,
        as_of: datetime,
        market: tuple[MarketEligibilityObservation, ...],
    ) -> MarketEligibilityObservation | None:
        known = tuple(
            item
            for item in market
            if item.security_id == security_id
            and item.market_time <= as_of
            and item.knowledge_time <= as_of
        )
        if not known:
            return None
        return max(
            known,
            key=lambda item: (
                item.market_time,
                item.knowledge_time,
                item.fact_id,
            ),
        )

    @staticmethod
    def _listing_policy_reasons(
        *,
        listing: ListingObservation,
        policy: UniversePolicy,
        reasons: list[str],
    ) -> None:
        if listing.asset_class not in policy.allowed_asset_classes:
            reasons.append("ASSET_CLASS_NOT_ALLOWED")
        if listing.security_type not in policy.allowed_security_types:
            reasons.append("SECURITY_TYPE_NOT_ALLOWED")
        if (
            policy.allowed_exchanges
            and listing.exchange not in policy.allowed_exchanges
        ):
            reasons.append("EXCHANGE_NOT_ALLOWED")
        if (
            policy.allowed_countries
            and listing.country not in policy.allowed_countries
        ):
            reasons.append("COUNTRY_NOT_ALLOWED")
        if (
            policy.allowed_currencies
            and listing.currency not in policy.allowed_currencies
        ):
            reasons.append("CURRENCY_NOT_ALLOWED")
        if policy.exclude_suspended and listing.status is ListingStatus.SUSPENDED:
            reasons.append("LISTING_SUSPENDED")

    @staticmethod
    def _market_policy_reasons(
        *,
        market: MarketEligibilityObservation,
        as_of: datetime,
        policy: UniversePolicy,
        reasons: list[str],
    ) -> None:
        age_days = (as_of.date() - market.market_time.date()).days
        if age_days > policy.maximum_market_age_days:
            reasons.append("STALE_MARKET_DATA")
        if market.close_price < policy.minimum_price:
            reasons.append("PRICE_BELOW_MINIMUM")
        if (
            market.average_daily_dollar_volume
            < policy.minimum_average_daily_dollar_volume
        ):
            reasons.append("LIQUIDITY_BELOW_MINIMUM")
        if market.market_cap < policy.minimum_market_cap:
            reasons.append("MARKET_CAP_BELOW_MINIMUM")
        if market.trading_days_history < policy.minimum_trading_days_history:
            reasons.append("INSUFFICIENT_TRADING_HISTORY")
        if policy.exclude_suspended and market.suspended:
            reasons.append("MARKET_SUSPENDED")


class UniverseStore:
    """Immutable manifest store for exact investable-universe snapshots."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS investable_universes (
                universe_id VARCHAR PRIMARY KEY,
                as_of TIMESTAMPTZ NOT NULL,
                policy_id VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, universe: InvestableUniverse) -> bool:
        payload = json.dumps(
            {
                "universe_id": universe.universe_id,
                "as_of": universe.as_of.isoformat(),
                "policy_id": universe.policy_id,
                "decisions": [
                    {
                        "security_id": item.security_id,
                        "issuer_id": item.issuer_id,
                        "listing_id": item.listing_id,
                        "ticker": item.ticker,
                        "included": item.included,
                        "exclusion_reasons": list(item.exclusion_reasons),
                        "fact_ids": list(item.fact_ids),
                    }
                    for item in universe.decisions
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        existing = self._con.execute(
            "SELECT payload_json FROM investable_universes WHERE universe_id = ?",
            [universe.universe_id],
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != payload:
                raise ValueError("universe identity conflict")
            return False
        self._con.execute(
            "INSERT INTO investable_universes VALUES (?, ?, ?, ?)",
            [
                universe.universe_id,
                universe.as_of,
                universe.policy_id,
                payload,
            ],
        )
        return True

    def get_manifest(self, universe_id: str) -> dict[str, object] | None:
        row = self._con.execute(
            "SELECT payload_json FROM investable_universes WHERE universe_id = ?",
            [universe_id],
        ).fetchone()
        if row is None:
            return None
        return json.loads(str(row[0]))

    def close(self) -> None:
        self._con.close()


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
