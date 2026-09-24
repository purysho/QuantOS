from __future__ import annotations

from datetime import datetime, timezone

from .models import EpistemicState, Event, Hypothesis


class SurpriseEngine:
    @staticmethod
    def surprise(observed: float, expected: float) -> float:
        return observed - expected

    @staticmethod
    def relative_surprise(observed: float, expected: float) -> float | None:
        if expected == 0:
            return None
        return (observed - expected) / abs(expected)


class HypothesisEngine:
    """Deterministic v0 generator.

    We deliberately do not use an LLM here yet. This lets us test event-time,
    evidence and promotion semantics independently from language-model quality.
    """

    def from_observation(
        self,
        *,
        event: Event,
        metric: str,
        expected: float,
        observed: float,
    ) -> Hypothesis:
        delta = SurpriseEngine.surprise(observed, expected)
        direction = "above" if delta > 0 else "below" if delta < 0 else "in line with"
        statement = (
            f"{event.entity_id} {metric} was {direction} the stored expectation; "
            "test whether this surprise contains incremental information after controlling "
            "for prior expectations, market regime, costs, and known factors."
        )
        return Hypothesis(
            entity_id=event.entity_id,
            statement=statement,
            generated_at=datetime.now(timezone.utc),
            evidence_event_ids=(event.event_id,),
            expected_value=expected,
            observed_value=observed,
            surprise=delta,
            epistemic_state=EpistemicState.INFERRED,
        )
