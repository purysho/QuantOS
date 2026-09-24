import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from quantos.claim_evidence_graph import (
    ClaimEvidenceGraph,
    ClaimRelationType,
    ReplicationKind,
    ReplicationOutcome,
)
from quantos.claims import (
    ClaimCard,
    ClaimStance,
    ClaimStore,
    ClaimType,
    make_claim_id,
)
from quantos.models import EpistemicState


UTC = timezone.utc


def card(text, artifact, *, stance=ClaimStance.SUPPORTS):
    sources = (artifact,)
    return ClaimCard(
        text=text,
        claim_type=ClaimType.EMPIRICAL,
        stance=stance,
        epistemic_state=EpistemicState.OBSERVED,
        topic="factor research",
        source_artifact_ids=sources,
        locator="Table 1",
        scope={"sample": "historical"},
        assumptions=(),
        limitations=("Historical evidence may not persist.",),
        as_of=datetime(2026, 9, 24, tzinfo=UTC),
        claim_id=make_claim_id(
            text=text,
            source_artifact_ids=sources,
            locator="Table 1",
        ),
    )


class ClaimEvidenceGraphTests(unittest.TestCase):
    def test_unknown_claim_relation_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            claims = ClaimStore(Path(tmp) / "claims.duckdb")
            graph = ClaimEvidenceGraph(Path(tmp) / "graph.duckdb")
            known = card("Known claim.", "sha256:a")
            claims.add(known)
            with self.assertRaises(ValueError):
                graph.relate(
                    from_claim_id=known.claim_id,
                    to_claim_id="claim:missing",
                    relation_type=ClaimRelationType.SUPPORTS,
                    notes="test",
                    recorded_by="reviewer",
                    recorded_at=datetime(2026, 9, 24, tzinfo=UTC),
                    claims=claims,
                )
            graph.close()
            claims.close()

    def test_context_keeps_support_limits_and_contradictions_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            claims = ClaimStore(Path(tmp) / "claims.duckdb")
            graph = ClaimEvidenceGraph(Path(tmp) / "graph.duckdb")
            focal = card("Original finding.", "sha256:a")
            support = card("Independent supporting evidence.", "sha256:b")
            limit = card("Effect is limited to liquid large-cap names.", "sha256:c", stance=ClaimStance.LIMITS)
            contradict = card("No effect in the later sample.", "sha256:d", stance=ClaimStance.CONTRADICTS)
            for item in (focal, support, limit, contradict):
                claims.add(item)
            for source, relation in (
                (support, ClaimRelationType.SUPPORTS),
                (limit, ClaimRelationType.LIMITS),
                (contradict, ClaimRelationType.CONTRADICTS),
            ):
                graph.relate(
                    from_claim_id=source.claim_id,
                    to_claim_id=focal.claim_id,
                    relation_type=relation,
                    notes="Explicit reviewed relationship.",
                    recorded_by="reviewer",
                    recorded_at=datetime(2026, 9, 24, tzinfo=UTC),
                    claims=claims,
                )
            context = graph.context(focal.claim_id, claims=claims)
            self.assertEqual(context.supporting, (support,))
            self.assertEqual(context.limiting, (limit,))
            self.assertEqual(context.contradicting, (contradict,))
            graph.close()
            claims.close()

    def test_replication_requires_distinct_source_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            claims = ClaimStore(Path(tmp) / "claims.duckdb")
            graph = ClaimEvidenceGraph(Path(tmp) / "graph.duckdb")
            original = card("Original finding.", "sha256:a")
            copy = card("Restated finding.", "sha256:a")
            claims.add(original)
            claims.add(copy)
            with self.assertRaises(ValueError):
                graph.record_replication(
                    original_claim_id=original.claim_id,
                    replication_claim_id=copy.claim_id,
                    kind=ReplicationKind.DIRECT,
                    outcome=ReplicationOutcome.REPLICATES,
                    independent_data=False,
                    preregistered=None,
                    notes="Same source cannot count as replication.",
                    reviewer="reviewer",
                    recorded_at=datetime(2026, 9, 24, tzinfo=UTC),
                    claims=claims,
                )
            graph.close()
            claims.close()

    def test_failed_independent_replication_is_preserved_not_averaged_away(self):
        with tempfile.TemporaryDirectory() as tmp:
            claims = ClaimStore(Path(tmp) / "claims.duckdb")
            graph = ClaimEvidenceGraph(Path(tmp) / "graph.duckdb")
            original = card("Original finding.", "sha256:a")
            replication = card(
                "Independent later sample did not reproduce the original effect.",
                "sha256:b",
                stance=ClaimStance.CONTRADICTS,
            )
            claims.add(original)
            claims.add(replication)
            record = graph.record_replication(
                original_claim_id=original.claim_id,
                replication_claim_id=replication.claim_id,
                kind=ReplicationKind.DIRECT,
                outcome=ReplicationOutcome.FAILS_TO_REPLICATE,
                independent_data=True,
                preregistered=True,
                notes="Later independent sample; same target specification.",
                reviewer="reviewer-b",
                recorded_at=datetime(2026, 9, 24, 16, tzinfo=UTC),
                claims=claims,
            )
            context = graph.context(original.claim_id, claims=claims)
            self.assertEqual(context.replications, (record,))
            self.assertEqual(
                context.replications[0].outcome,
                ReplicationOutcome.FAILS_TO_REPLICATE,
            )
            graph.close()
            claims.close()


if __name__ == "__main__":
    unittest.main()
