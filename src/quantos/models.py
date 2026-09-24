from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


class EpistemicState(str, Enum):
    OBSERVED = "OBSERVED"
    DERIVED = "DERIVED"
    ESTIMATED = "ESTIMATED"
    INFERRED = "INFERRED"
    SPECULATIVE = "SPECULATIVE"
    UNKNOWN = "UNKNOWN"


class HypothesisStatus(str, Enum):
    DISCOVERED = "DISCOVERED"
    RESEARCH = "RESEARCH"
    SHADOW_ONLY = "SHADOW_ONLY"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class Event:
    entity_id: str
    event_type: str
    event_time: datetime
    knowledge_time: datetime
    source_id: str
    payload: dict[str, Any]
    event_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        for name in ("event_time", "knowledge_time"):
            value = getattr(self, name)
            if value.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.knowledge_time < self.event_time:
            raise ValueError("knowledge_time cannot precede event_time")
        if not self.entity_id or not self.event_type or not self.source_id:
            raise ValueError("entity_id, event_type, and source_id are required")


@dataclass(frozen=True)
class Claim:
    text: str
    epistemic_state: EpistemicState
    source_ids: tuple[str, ...]
    as_of: datetime
    limitations: tuple[str, ...] = ()
    claim_id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True)
class Hypothesis:
    entity_id: str
    statement: str
    generated_at: datetime
    evidence_event_ids: tuple[str, ...]
    expected_value: float | None
    observed_value: float | None
    surprise: float | None
    epistemic_state: EpistemicState = EpistemicState.INFERRED
    status: HypothesisStatus = HypothesisStatus.DISCOVERED
    hypothesis_id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True)
class ResearchDecision:
    hypothesis_id: str
    approved_for_shadow: bool
    reasons: tuple[str, ...]
    decision_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class OrderProposal:
    security_id: str
    side: str
    quantity: float
    reason_hypothesis_id: str
