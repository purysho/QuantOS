from __future__ import annotations

from .models import Claim, EpistemicState


class EvidenceError(ValueError):
    pass


class ClaimRegistry:
    def __init__(self) -> None:
        self._claims: dict[str, Claim] = {}

    def add(self, claim: Claim) -> None:
        if claim.epistemic_state in {
            EpistemicState.OBSERVED,
            EpistemicState.DERIVED,
            EpistemicState.ESTIMATED,
            EpistemicState.INFERRED,
        } and not claim.source_ids:
            raise EvidenceError(
                f"{claim.epistemic_state.value} material claims require provenance"
            )
        self._claims[claim.claim_id] = claim

    def get(self, claim_id: str) -> Claim:
        return self._claims[claim_id]
