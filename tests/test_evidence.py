import unittest
from datetime import datetime, timezone

from quantos.evidence import ClaimRegistry, EvidenceError
from quantos.models import Claim, EpistemicState


class EvidenceTests(unittest.TestCase):
    def test_observed_claim_requires_source(self):
        claim = Claim(
            text="Revenue was 10",
            epistemic_state=EpistemicState.OBSERVED,
            source_ids=(),
            as_of=datetime.now(timezone.utc),
        )
        with self.assertRaises(EvidenceError):
            ClaimRegistry().add(claim)

    def test_unknown_may_exist_without_source(self):
        claim = Claim(
            text="Supplier private margin is unknown",
            epistemic_state=EpistemicState.UNKNOWN,
            source_ids=(),
            as_of=datetime.now(timezone.utc),
        )
        ClaimRegistry().add(claim)


if __name__ == "__main__":
    unittest.main()
