from __future__ import annotations

from datetime import datetime

from .gates import ResearchGate
from .intelligence import HypothesisEngine
from .models import Event, Hypothesis, ResearchDecision
from .store import PointInTimeEventStore


class QuantOS:
    def __init__(self) -> None:
        self.events = PointInTimeEventStore()
        self.hypotheses = HypothesisEngine()
        self.research_gate = ResearchGate()

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
        return hypothesis, self.research_gate.evaluate(hypothesis)

    def state_as_of(self, *, entity_id: str, knowledge_time: datetime):
        return self.events.known_as_of(knowledge_time, entity_id=entity_id)
