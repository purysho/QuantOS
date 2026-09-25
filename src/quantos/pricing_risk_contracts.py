from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import TypeAlias

import duckdb


class Currency(str, Enum):
    USD = "USD"
    GBP = "GBP"
    EUR = "EUR"
    CNY = "CNY"
    JPY = "JPY"
    CHF = "CHF"
    CAD = "CAD"
    AUD = "AUD"
    HKD = "HKD"


class MarketQuoteType(str, Enum):
    EQUITY_SPOT = "EQUITY_SPOT"
    FX_SPOT = "FX_SPOT"
    DISCOUNT_FACTOR = "DISCOUNT_FACTOR"
    ZERO_RATE = "ZERO_RATE"
    FORWARD_RATE = "FORWARD_RATE"
    VOLATILITY = "VOLATILITY"
    FIXING = "FIXING"


class QuoteUnit(str, Enum):
    DECIMAL = "DECIMAL"
    PRICE = "PRICE"
    FX_RATE = "FX_RATE"
    DISCOUNT_FACTOR = "DISCOUNT_FACTOR"


@dataclass(frozen=True)
class MarketQuote:
    quote_type: MarketQuoteType
    market_key: str
    value: Decimal
    unit: QuoteUnit
    event_time: datetime
    knowledge_time: datetime
    source_fact_ids: tuple[str, ...]
    tenor: str | None = None
    currency: Currency | None = None

    def __post_init__(self) -> None:
        if not self.market_key.strip():
            raise ValueError("market quote requires market_key")
        if not self.value.is_finite():
            raise ValueError("market quote value must be finite")
        if self.event_time.tzinfo is None or self.knowledge_time.tzinfo is None:
            raise ValueError("market quote timestamps must be timezone-aware")
        if self.knowledge_time < self.event_time:
            raise ValueError("market quote cannot be known before event_time")
        if not self.source_fact_ids or not all(
            item.strip() for item in self.source_fact_ids
        ):
            raise ValueError("market quote requires source fact IDs")
        if len(self.source_fact_ids) != len(set(self.source_fact_ids)):
            raise ValueError("market quote source fact IDs must be unique")
        if self.tenor is not None and not self.tenor.strip():
            raise ValueError("market quote tenor cannot be blank")
        if (
            self.quote_type is MarketQuoteType.DISCOUNT_FACTOR
            and self.value <= 0
        ):
            raise ValueError(
                "discount factor must be strictly positive; values above 1 "
                "are valid when rates are negative"
            )
        if (
            self.quote_type in {
                MarketQuoteType.EQUITY_SPOT,
                MarketQuoteType.FX_SPOT,
                MarketQuoteType.VOLATILITY,
            }
            and self.value <= 0
        ):
            raise ValueError(
                f"{self.quote_type.value} quote must be positive"
            )

    @property
    def semantic_key(self) -> tuple[str, str, str | None, str | None]:
        return (
            self.quote_type.value,
            self.market_key.strip(),
            self.tenor.strip() if self.tenor is not None else None,
            self.currency.value if self.currency is not None else None,
        )

    @property
    def quote_id(self) -> str:
        return _content_id(
            "market-quote",
            {
                "quote_type": self.quote_type.value,
                "market_key": self.market_key.strip(),
                "value": str(self.value),
                "unit": self.unit.value,
                "event_time": self.event_time.isoformat(),
                "knowledge_time": self.knowledge_time.isoformat(),
                "source_fact_ids": sorted(self.source_fact_ids),
                "tenor": (
                    self.tenor.strip()
                    if self.tenor is not None
                    else None
                ),
                "currency": (
                    self.currency.value
                    if self.currency is not None
                    else None
                ),
            },
        )


@dataclass(frozen=True)
class MarketDataSnapshot:
    snapshot_id: str
    valuation_time: datetime
    base_currency: Currency
    quote_ids: tuple[str, ...]
    quotes: tuple[MarketQuote, ...]
    lineage_fingerprint: str


class MarketDataSnapshotBuilder:
    def build(
        self,
        *,
        valuation_time: datetime,
        base_currency: Currency,
        quotes: tuple[MarketQuote, ...],
    ) -> MarketDataSnapshot:
        if valuation_time.tzinfo is None:
            raise ValueError("valuation_time must be timezone-aware")
        if not quotes:
            raise ValueError("market snapshot requires at least one quote")
        if len({item.quote_id for item in quotes}) != len(quotes):
            raise ValueError("duplicate market quote identities")
        semantic_keys = [item.semantic_key for item in quotes]
        if len(set(semantic_keys)) != len(semantic_keys):
            raise ValueError(
                "market snapshot contains duplicate semantic quote keys"
            )
        if any(item.knowledge_time > valuation_time for item in quotes):
            raise ValueError(
                "market snapshot contains future-known market data"
            )

        canonical = tuple(
            sorted(
                quotes,
                key=lambda item: (
                    item.semantic_key,
                    item.quote_id,
                ),
            )
        )
        quote_ids = tuple(item.quote_id for item in canonical)
        lineage_fingerprint = _content_id(
            "market-lineage",
            {
                "quote_ids": list(quote_ids),
                "source_fact_ids": sorted(
                    {
                        source_id
                        for quote in canonical
                        for source_id in quote.source_fact_ids
                    }
                ),
            },
        )
        payload = {
            "valuation_time": valuation_time.isoformat(),
            "base_currency": base_currency.value,
            "quote_ids": list(quote_ids),
            "lineage_fingerprint": lineage_fingerprint,
        }
        return MarketDataSnapshot(
            snapshot_id=_content_id("market-data-snapshot", payload),
            valuation_time=valuation_time,
            base_currency=base_currency,
            quote_ids=quote_ids,
            quotes=canonical,
            lineage_fingerprint=lineage_fingerprint,
        )


class DayCountConvention(str, Enum):
    ACT_360 = "ACT_360"
    ACT_365_FIXED = "ACT_365_FIXED"
    THIRTY_360 = "THIRTY_360"


class OptionType(str, Enum):
    CALL = "CALL"
    PUT = "PUT"


class PayReceive(str, Enum):
    PAY = "PAY"
    RECEIVE = "RECEIVE"


@dataclass(frozen=True)
class EquityInstrument:
    security_id: str
    currency: Currency