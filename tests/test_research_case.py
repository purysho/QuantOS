import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quantos.claims import (
    ClaimCard,
    ClaimStance,
    ClaimStore,
    ClaimType,
    make_claim_id,
)
from quantos.models import EpistemicState
from quantos.research_case import ResearchCaseStore, ResearchCaseType


UTC = timezone.utc


def claim(text, artifact, as_of):
    sources = (artifact,)
    return ClaimCard(
        text=text,
        claim_type=ClaimType.EMPIRICAL,
        stance=ClaimStance.SUPPORTS,
        epistemic_state=EpistemicState.OBSERVED,
        topic="case evidence",
        source_artifact_ids=sources,
        locator="Table 1",
        scope={"sample": "historical"},
        assumptions=(),
        limitations=("May not persist.",),
        as_of=as_of,
        claim_id=make_claim_id(
            text=text,
            source_artifact_ids=sources,
            locator="Table 1",
        ),
    )


def case_kwargs(supporting_id, as_of):
    return {
        "case_type": ResearchCaseType.SYSTEMATIC,
        "subject_ids": ("signal:quality-value-momentum-v1",),
        "universe": "US listed common equities meeting the documented liquidity screen",
        "thesis": "The signal may contain a persistent cross-sectional return relation worth prospective shadow evaluation.",
        "mechanism": "The case combines valuation, quality and momentum effects while requiring costs and decay to be measured prospectively.",
        "horizon": "12 months research horizon; monthly rebalance hypothesis",
        "as_of": as_of,
        "supporting_claim_ids": (supporting_id,),
        "limiting_claim_ids": (),
        "contradicting_claim_ids": (),
        "alternative_explanations": (
            "The historical relation may reflect data mining or publication selection.",
            "Observed returns may be compensation for omitted risk.",
        ),
        "falsifiers": (
            "Prospective benchmark-adjusted performance is materially negative after costs.",
            "Signal behavior disappears outside the construction sample or regime.",
        ),
        "monitoring_conditions": (
            "Track turnover, slippage, breadth, concentration and factor crowding.",
        ),
        "assumptions": (
            "Point-in-time universe and corporate-action handling remain correct.",
        ),
        "author": "researcher-a",
        "created_at": as_of + timedelta(minutes=5),
    }


class ResearchCaseTests(unittest.TestCase):
    def test_case_requires_trusted_claim_and_explicit_falsifier(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claims = ClaimStore(root / "claims.duckdb")
            store = ResearchCaseStore(root / "cases.duckdb")
            when = datetime(2026, 9, 24, 12, tzinfo=UTC)
            with self.assertRaises(ValueError):
                store.create(
                    claims=claims,
                    **case_kwargs("claim:missing", when),
                )

            evidence = claim("Historical evidence.", "sha256:a", when)
            claims.add(evidence)
            kwargs = case_kwargs(evidence.claim_id, when)
            kwargs["falsifiers"] = ()
            with self.assertRaises(ValueError):
                store.create(claims=claims, **kwargs)
            store.close()
            claims.close()

    def test_future_claim_cannot_leak_into_earlier_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claims = ClaimStore(root / "claims.duckdb")
            store = ResearchCaseStore(root / "cases.duckdb")
            case_time = datetime(2026, 9, 24, 12, tzinfo=UTC)
            future = claim(
                "Future evidence.",
                "sha256:a",
                case_time + timedelta(days=1),
            )
            claims.add(future)
            with self.assertRaises(ValueError):
                store.create(
                    claims=claims,
                    **case_kwargs(future.claim_id, case_time),
                )
            store.close()
            claims.close()

    def test_case_is_content_addressed_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claims = ClaimStore(root / "claims.duckdb")
            store = ResearchCaseStore(root / "cases.duckdb")
            when = datetime(2026, 9, 24, 12, tzinfo=UTC)
            evidence = claim("Historical evidence.", "sha256:a", when)
            claims.add(evidence)
            kwargs = case_kwargs(evidence.claim_id, when)
            first = store.create(claims=claims, **kwargs)
            second = store.create(claims=claims, **kwargs)
            self.assertEqual(first, second)
            self.assertTrue(first.case_id.startswith("research-case:"))
            store.close()
            claims.close()

    def test_case_evidence_roles_are_mutually_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claims = ClaimStore(root / "claims.duckdb")
            store = ResearchCaseStore(root / "cases.duckdb")
            when = datetime(2026, 9, 24, 12, tzinfo=UTC)
            evidence = claim("Historical evidence.", "sha256:a", when)
            claims.add(evidence)
            kwargs = case_kwargs(evidence.claim_id, when)
            kwargs["limiting_claim_ids"] = (evidence.claim_id,)
            with self.assertRaises(ValueError):
                store.create(claims=claims, **kwargs)
            store.close()
            claims.close()

    def test_revision_preserves_old_case_and_lineage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claims = ClaimStore(root / "claims.duckdb")
            store = ResearchCaseStore(root / "cases.duckdb")
            when = datetime(2026, 9, 24, 12, tzinfo=UTC)
            evidence = claim("Historical evidence.", "sha256:a", when)
            claims.add(evidence)
            first = store.create(
                claims=claims,
                **case_kwargs(evidence.claim_id, when),
            )
            revised_kwargs = case_kwargs(
                evidence.claim_id,
                when + timedelta(days=1),
            )
            revised_kwargs["created_at"] = when + timedelta(days=1, minutes=5)
            revised_kwargs["thesis"] = (
                "New information changes the scoped thesis, while preserving the old case."
            )
            revised = store.create(
                claims=claims,
                supersedes_case_id=first.case_id,
                **revised_kwargs,
            )
            self.assertNotEqual(first.case_id, revised.case_id)
            self.assertEqual(
                [item.case_id for item in store.lineage(revised.case_id)],
                [first.case_id, revised.case_id],
            )
            self.assertEqual(store.get(first.case_id), first)
            store.close()
            claims.close()

    def test_revision_cannot_silently_change_subject(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claims = ClaimStore(root / "claims.duckdb")
            store = ResearchCaseStore(root / "cases.duckdb")
            when = datetime(2026, 9, 24, 12, tzinfo=UTC)
            evidence = claim("Historical evidence.", "sha256:a", when)
            claims.add(evidence)
            first = store.create(
                claims=claims,
                **case_kwargs(evidence.claim_id, when),
            )
            revised_kwargs = case_kwargs(
                evidence.claim_id,
                when + timedelta(days=1),
            )
            revised_kwargs["created_at"] = when + timedelta(days=1, minutes=5)
            revised_kwargs["subject_ids"] = ("different-subject",)
            with self.assertRaises(ValueError):
                store.create(
                    claims=claims,
                    supersedes_case_id=first.case_id,
                    **revised_kwargs,
                )
            store.close()
            claims.close()


if __name__ == "__main__":
    unittest.main()
