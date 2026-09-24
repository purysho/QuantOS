from __future__ import annotations

from .models import EpistemicState, Hypothesis, OrderProposal, ResearchDecision


class LiveTradingDisabled(PermissionError):
    pass


class ResearchGate:
    """Minimal promotion rules for v0 shadow research."""

    def evaluate(self, hypothesis: Hypothesis) -> ResearchDecision:
        reasons: list[str] = []
        if hypothesis.epistemic_state is not EpistemicState.INFERRED:
            reasons.append("hypothesis must be explicitly labeled INFERRED")
        if not hypothesis.evidence_event_ids:
            reasons.append("hypothesis has no evidence events")
        if hypothesis.expected_value is None or hypothesis.observed_value is None:
            reasons.append("expectation/observation pair is incomplete")
        if hypothesis.surprise is None:
            reasons.append("no measurable surprise")

        return ResearchDecision(
            hypothesis_id=hypothesis.hypothesis_id,
            approved_for_shadow=not reasons,
            reasons=tuple(reasons) if reasons else ("eligible for shadow research only",),
        )


class CapitalFirewall:
    """v0 capital boundary: live execution is impossible by construction."""

    def authorize_live_order(self, proposal: OrderProposal) -> None:
        raise LiveTradingDisabled(
            "Quant OS prototype v0.1 is research-only; live order authorization is disabled"
        )
