from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

from .models import Event


class EventLookupStore(Protocol):
    def append(self, event: Event) -> None: ...
    def get(self, event_id: str) -> Event | None: ...


class EventConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class IngestionResult:
    inserted: int
    skipped_identical: int


class IngestionEngine:
    """Idempotent live-ingestion boundary.

    Repeated identical provider events are safe no-ops. Reusing an event ID with
    changed contents is a hard data conflict and stops ingestion.
    """

    def __init__(self, store: EventLookupStore) -> None:
        self.store = store

    def ingest(self, events: Iterable[Event]) -> IngestionResult:
        inserted = 0
        skipped = 0

        for event in events:
            existing = self.store.get(event.event_id)
            if existing is None:
                self.store.append(event)
                inserted += 1
                continue

            if existing == event:
                skipped += 1
                continue

            raise EventConflictError(
                f"event_id {event.event_id} already exists with different contents"
            )

        return IngestionResult(inserted=inserted, skipped_identical=skipped)
