from __future__ import annotations

from datetime import datetime, timezone

from .models import Event


class ExpectationBook:
    """Point-in-time expectations stored in the same event stream as observations."""

    def __init__(self, event_store) -> None:
        self._events = event_store

    @staticmethod
    def event_type(metric: str) -> str:
        normalized = metric.strip().lower().replace(" ", "_")
        if not normalized:
            raise ValueError("metric is required")
        return f"expectation.{normalized}"

    def record(
        self,
        *,
        entity_id: str,
        metric: str,
        value: float,
        source_id: str,
        knowledge_time: datetime | None = None,
        model_id: str = "manual",
        horizon: str | None = None,
    ) -> Event:
        when = knowledge_time or datetime.now(timezone.utc)
        if when.tzinfo is None:
            raise ValueError("knowledge_time must be timezone-aware")
        event = Event(
            entity_id=entity_id,
            event_type=self.event_type(metric),
            event_time=when,
            knowledge_time=when,
            source_id=source_id,
            payload={
                "metric": metric,
                "value": float(value),
                "model_id": model_id,
                "horizon": horizon,
            },
        )
        self._events.append(event)
        return event

    def latest_value(
        self,
        *,
        entity_id: str,
        metric: str,
        knowledge_time: datetime,
    ) -> float | None:
        rows = self._events.known_as_of(
            knowledge_time,
            entity_id=entity_id,
            event_type=self.event_type(metric),
        )
        if not rows:
            return None
        return float(rows[-1].payload["value"])
