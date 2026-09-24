import unittest
from datetime import datetime, timezone

from quantos.gates import CapitalFirewall, LiveTradingDisabled, ResearchGate
from quantos.models import EpistemicState, Hypothesis, OrderProposal


class GateTests(unittest.TestCase):
    def test_valid_hypothesis_can_only_reach_shadow(self):
        h = Hypothesis(
            entity_id="ABC",
            statement="test hypothesis",
            generated_at=datetime.now(timezone.utc),
            evidence_event_ids=("event-1",),
            expected_value=1.0,
            observed_value=1.2,
            surprise=0.2,
            epistemic_state=EpistemicState.INFERRED,
        )
        decision = ResearchGate().evaluate(h)
        self.assertTrue(decision.approved_for_shadow)

    def test_live_order_is_unconditionally_blocked(self):
        proposal = OrderProposal("ABC", "BUY", 10, "h-1")
        with self.assertRaises(LiveTradingDisabled):
            CapitalFirewall().authorize_live_order(proposal)


if __name__ == "__main__":
    unittest.main()
