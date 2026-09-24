from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .expectations import ExpectationBook
from .gates import ResearchGate
from .intelligence import HypothesisEngine
from .ledger import ResearchLedger
from .models import Event, Hypothesis, ResearchDecision
from .store import PointInTimeEventStore


class EventStore(Protocol):
    def append(self, event: Event) -> None: ...
    def all(self) -> tuple[Event, ...]: ...
    def known_as_of(
        self,
        knowledge_time: datetime,
        *,
        entity_id: str | None = None,
        event_type: str | None = None,
    ) -> tuple[Event, ...]: ...


class QuantOS:
    def __init__(
        self,
        *,
        event_store: EventStore | None = None,
        ledger: ResearchLedger | None = None,
    ) -> None:
        self.events = event_store or PointInTimeEventStore()
        self.hypotheses = HypothesisEngine()
        self.research_gate = ResearchGate()
        self.ledger = ledger
        self.expectations = ExpectationBook(self.events)

    def ingest(self, event: Event) -> None:
        self.events.append(event)

    def analyze_numeric_surprise(
        self,
        *,
        event: Event,
        metric: str,
        expected: float,
        observed: float,
    ) -> tuple[Hypothesis, ResearchDecision]:
        if event.event_id not in {row.event_id for row in self.events.all()}:
            raise ValueError("event must be ingested before analysis")
        hypothesis = self.hypotheses.from_observation(
            event=event, metric=metric, expected=expected, observed=observed
        )
        decision = self.research_gate.evaluate(hypothesis)
        if self.ledger is not None:
            self.ledger.record_hypothesis(hypothesis)
            self.ledger.record_decision(decision)
        return hypothesis, decision

    def analyze_against_latest_expectation(
        self,
        *,
        event: Event,
        metric: str,
        observed: float,
    ) -> tuple[Hypothesis, ResearchDecision]:
        expected = self.expectations.latest_value(
            entity_id=event.entity_id,
            metric=metric,
            knowledge_time=event.knowledge_time,
        )
        if expected is None:
            raise ValueError(
                f"no point-in-time expectation for {event.entity_id} {metric}"
            )
        return self.analyze_numeric_surprise(
            event=event, metric=metric, expected=expected, observed=observed
        )

    def state_as_of(self, *, entity_id: str, knowledge_time: datetime):
        return self.events.known_as_of(knowledge_time, entity_id=entity_id)
