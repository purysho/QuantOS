from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Protocol

from .models import Event
from .reactions import MarketReaction, MarketReactionEngine


class ReactionWindowUnavailable(ValueError):
    pass


class ReactionAnchor(str, Enum):
    EVENT_TIME = "event_time"
    KNOWLEDGE_TIME = "knowledge_time"


class EventStore(Protocol):
    def known_as_of(
        self,
        knowledge_time: datetime,
        *,
        entity_id: str | None = None,
        event_type: str | None = None,
    ) -> tuple[Event, ...]: ...


@dataclass(frozen=True)
class ReactionWindowMeasurement:
    reaction: MarketReaction
    anchor: ReactionAnchor
    anchor_time: datetime
    target_time: datetime
    security_pre_event_id: str
    security_post_event_id: str
    benchmark_pre_event_id: str
    benchmark_post_event_id: str


@dataclass(frozen=True)
class _PricePoint:
    event_id: str
    event_time: datetime
    knowledge_time: datetime
    price: float


class ReactionWindowEngine:
    """Measure event reactions from stored market events.

    KNOWLEDGE_TIME is the default anchor because it measures from when this
    system could actually know the event, not merely from the public timestamp.
    """

    def __init__(self, event_store: EventStore) -> None:
        self.events = event_store

    def measure(
        self,
        *,
        event: Event,
        security_id: str,
        benchmark_id: str,
        horizon: timedelta,
        measured_at: datetime,
        anchor: ReactionAnchor = ReactionAnchor.KNOWLEDGE_TIME,
        max_pre_age: timedelta = timedelta(minutes=5),
        max_post_lag: timedelta = timedelta(minutes=5),
        horizon_label: str | None = None,
    ) -> ReactionWindowMeasurement:
        if measured_at.tzinfo is None:
            raise ReactionWindowUnavailable("measured_at must be timezone-aware")
        if horizon <= timedelta(0):
            raise ReactionWindowUnavailable("horizon must be positive")
        if max_pre_age < timedelta(0) or max_post_lag < timedelta(0):
            raise ReactionWindowUnavailable("window tolerances cannot be negative")

        anchor_time = (
            event.knowledge_time
            if anchor is ReactionAnchor.KNOWLEDGE_TIME
            else event.event_time
        )
        target_time = anchor_time + horizon
        if measured_at < target_time:
            raise ReactionWindowUnavailable(
                "measurement attempted before the reaction horizon completed"
            )

        security = self._points(security_id, measured_at)
        benchmark = self._points(benchmark_id, measured_at)

        require_pre_known = (
            anchor_time if anchor is ReactionAnchor.KNOWLEDGE_TIME else None
        )
        sec_pre = self._pre(
            security,
            anchor_time,
            max_pre_age,
            known_by=require_pre_known,
            label=security_id,
        )
        bench_pre = self._pre(
            benchmark,
            anchor_time,
            max_pre_age,
            known_by=require_pre_known,
            label=benchmark_id,
        )
        sec_post = self._post(
            security,
            target_time,
            max_post_lag,
            label=security_id,
        )
        bench_post = self._post(
            benchmark,
            target_time,
            max_post_lag,
            label=benchmark_id,
        )

        label = horizon_label or self._label(horizon)
        reaction = MarketReactionEngine.measure(
            event_id=event.event_id,
            security_id=security_id,
            measured_at=measured_at,
            horizon=label,
            security_pre=sec_pre.price,
            security_post=sec_post.price,
            benchmark_pre=bench_pre.price,
            benchmark_post=bench_post.price,
        )
        return ReactionWindowMeasurement(
            reaction=reaction,
            anchor=anchor,
            anchor_time=anchor_time,
            target_time=target_time,
            security_pre_event_id=sec_pre.event_id,
            security_post_event_id=sec_post.event_id,
            benchmark_pre_event_id=bench_pre.event_id,
            benchmark_post_event_id=bench_post.event_id,
        )

    def _points(self, entity_id: str, measured_at: datetime) -> tuple[_PricePoint, ...]:
        rows = self.events.known_as_of(
            measured_at,
            entity_id=entity_id,
            event_type="market.trade",
        )
        points: list[_PricePoint] = []
        for event in rows:
            raw = event.payload.get("price")
            try:
                price = float(raw)
            except (TypeError, ValueError) as exc:
                raise ReactionWindowUnavailable(
                    f"market event {event.event_id} has invalid price"
                ) from exc
            if price <= 0:
                raise ReactionWindowUnavailable(
                    f"market event {event.event_id} has non-positive price"
                )
            points.append(
                _PricePoint(
                    event_id=event.event_id,
                    event_time=event.event_time,
                    knowledge_time=event.knowledge_time,
                    price=price,
                )
            )
        return tuple(points)

    @staticmethod
    def _pre(
        points: tuple[_PricePoint, ...],
        anchor_time: datetime,
        max_age: timedelta,
        *,
        known_by: datetime | None,
        label: str,
    ) -> _PricePoint:
        candidates = [
            point
            for point in points
            if point.event_time <= anchor_time
            and (known_by is None or point.knowledge_time <= known_by)
        ]
        if not candidates:
            raise ReactionWindowUnavailable(f"no pre-event market price for {label}")
        point = max(
            candidates,
            key=lambda item: (item.event_time, item.knowledge_time, item.event_id),
        )
        if anchor_time - point.event_time > max_age:
            raise ReactionWindowUnavailable(f"pre-event market price is stale for {label}")
        return point

    @staticmethod
    def _post(
        points: tuple[_PricePoint, ...],
        target_time: datetime,
        max_lag: timedelta,
        *,
        label: str,
    ) -> _PricePoint:
        candidates = [point for point in points if point.event_time >= target_time]
        if not candidates:
            raise ReactionWindowUnavailable(f"no post-event market price for {label}")
        point = min(
            candidates,
            key=lambda item: (item.event_time, item.knowledge_time, item.event_id),
        )
        if point.event_time - target_time > max_lag:
            raise ReactionWindowUnavailable(f"post-event market price is too late for {label}")
        return point

    @staticmethod
    def _label(horizon: timedelta) -> str:
        seconds = int(horizon.total_seconds())
        if seconds % 86400 == 0:
            return f"{seconds // 86400}d"
        if seconds % 3600 == 0:
            return f"{seconds // 3600}h"
        if seconds % 60 == 0:
            return f"{seconds // 60}m"
        return f"{seconds}s"
