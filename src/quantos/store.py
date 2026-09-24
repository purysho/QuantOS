from __future__ import annotations

from datetime import datetime

from .models import Event


class PointInTimeEventStore:
    """Append-only in-memory store for prototype testing."""

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._ids: set[str] = set()

    def append(self, event: Event) -> None:
        if event.event_id in self._ids:
            raise ValueError(f"duplicate event_id: {event.event_id}")
        self._events.append(event)
        self._ids.add(event.event_id)

    def get(self, event_id: str) -> Event | None:
        for event in self._events:
            if event.event_id == event_id:
                return event
        return None

    def all(self) -> tuple[Event, ...]:
        return tuple(self._events)

    def known_as_of(
        self,
        knowledge_time: datetime,
        *,
        entity_id: str | None = None,
        event_type: str | None = None,
    ) -> tuple[Event, ...]:
        if knowledge_time.tzinfo is None:
            raise ValueError("knowledge_time must be timezone-aware")
        rows = [e for e in self._events if e.knowledge_time <= knowledge_time]
        if entity_id is not None:
            rows = [e for e in rows if e.entity_id == entity_id]
        if event_type is not None:
            rows = [e for e in rows if e.event_type == event_type]
        return tuple(sorted(rows, key=lambda e: (e.knowledge_time, e.event_time, e.event_id)))

    def latest_known(
        self,
        knowledge_time: datetime,
        *,
        entity_id: str,
        event_type: str,
    ) -> Event | None:
        rows = self.known_as_of(
            knowledge_time, entity_id=entity_id, event_type=event_type
        )
        return rows[-1] if rows else None
