from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Protocol

from quantos.models import Event


@dataclass(frozen=True)
class MarketRecord:
    security_id: str
    event_time: datetime
    received_at: datetime
    price: float
    size: float
    venue: str
    source: str
    sequence: int | None = None


class MarketDataAdapter(Protocol):
    def stream(self) -> Iterable[MarketRecord]: ...


class MarketEventNormalizer:
    """Normalize provider records into the shared point-in-time event contract."""

    @staticmethod
    def normalize(record: MarketRecord) -> Event:
        if record.event_time.tzinfo is None or record.received_at.tzinfo is None:
            raise ValueError("market timestamps must be timezone-aware")
        if record.received_at < record.event_time:
            raise ValueError(
                "received_at precedes event_time; reject clock-skewed record"
            )
        if record.price <= 0 or record.size < 0:
            raise ValueError("market record has invalid price/size")

        deterministic_id = (
            f"market:{record.source}:{record.security_id}:{record.venue}:{record.sequence}"
            if record.sequence is not None
            else None
        )
        kwargs = {"event_id": deterministic_id} if deterministic_id else {}

        return Event(
            **kwargs,
            entity_id=record.security_id,
            event_type="market.trade",
            event_time=record.event_time,
            knowledge_time=record.received_at,
            source_id=record.source,
            payload={
                "price": float(record.price),
                "size": float(record.size),
                "venue": record.venue,
                "sequence": record.sequence,
            },
        )
