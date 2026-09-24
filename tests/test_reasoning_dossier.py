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
from quantos.reasoning_dossier import EvidenceDossierBuilder, EvidencePosture


UTC = timezone.utc


def card(text, artifact, stance=ClaimStance.SUPPORTS):
    sources = (artifact,)
    return ClaimCard(
        text=text,
        claim_type=ClaimType.EMPIRICAL,
        stance=stance,
        epistemic_state=EpistemicState.OBSERVED,
        topic="factor evidence",
        source_artifact_ids=sources,
        locator="Table 2",
        scope={"sample": "historical"},
        assumptions=(),
        limitations=("Historical evidence may not persist.",),
        as_of=datetime(2026, 9, 24, tzinfo=UTC),
        claim_id=make_claim_id(
            text=text,
            source_artifact_ids=sources,
            locator="Table 2",
        ),
    )


class EvidenceDossierTests(unittest.TestCase):
    def test_no_context_is_sparse_and_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            claims = ClaimStore(Path(tmp) / "claims.duckdb")
            graph = ClaimEvidenceGraph(Path(tmp) / "evidence.duckdb")
            focal = card("Original result.", "sha256:a")
            claims.add(focal)
            dossier = EvidenceDossierBuilder().build(
                focal.claim_id,
                claims=claims,
                evidence_graph=graph,
            )
            self.assertEqual(dossier.posture, EvidencePosture.SPARSE_EVIDENCE)
            codes = {flag.code for flag in dossier.flags}
            self.assertIn("NO_EXTERNAL_CONTEXT", codes)
            self.assertIn("NO_REPLICATION_RECORD", codes)
            graph.close()
            claims.close()

    def test_support_and_limits_remain_supported_with_limitations(self):
        with tempfile.TemporaryDirectory() as tmp:
            claims = ClaimStore(Path(tmp) / "claims.duckdb")
            graph = ClaimEvidenceGraph(Path(tmp) / "evidence.duckdb")
            focal = card("Original result.", "sha256:a")
            support = card("Supporting result.", "sha256:b")
            limit = card(
                "Effect was not present in illiquid securities.",
                "sha256:c",
                ClaimStance.LIMITS,
            )
            for item in (focal, support, limit):
                claims.add(item)
            graph.relate(
                from_claim_id=support.claim_id,
                to_claim_id=focal.claim_id,
                relation_type=ClaimRelationType.SUPPORTS,
                notes="Independent support.",
                recorded_by="reviewer",
                recorded_at=datetime(2026, 9, 24, tzinfo=UTC),
                claims=claims,
            )
            graph.relate(
                from_claim_id=limit.claim_id,
                to_claim_id=focal.claim_id,
                relation_type=ClaimRelationType.LIMITS,
                notes="Scope limitation.",
                recorded_by="reviewer",
                recorded_at=datetime(2026, 9, 24, tzinfo=UTC),
                claims=claims,
            )
            dossier = EvidenceDossierBuilder().build(
                focal.claim_id,
                claims=claims,
                evidence_graph=graph,
            )
            self.assertEqual(
                dossier.posture,
                EvidencePosture.SUPPORTED_WITH_LIMITATIONS,
            )
            self.assertEqual(dossier.supporting_count, 1)
            self.assertEqual(dossier.limiting_count, 1)
            graph.close()
            claims.close()

    def test_contradiction_takes_precedence_over_generic_support(self):
        with tempfile.TemporaryDirectory() as tmp:
            claims = ClaimStore(Path(tmp) / "claims.duckdb")
            graph = ClaimEvidenceGraph(Path(tmp) / "evidence.duckdb")
            focal = card("Original result.", "sha256:a")
            support = card("Supporting result.", "sha256:b")
            contradict = card(
                "Later sample found no comparable effect.",
                "sha256:c",
                ClaimStance.CONTRADICTS,
            )
            for item in (focal, support, contradict):
                claims.add(item)
            for item, relation in (
                (support, ClaimRelationType.SUPPORTS),
                (contradict, ClaimRelationType.CONTRADICTS),
            ):
                graph.relate(
                    from_claim_id=item.claim_id,
                    to_claim_id=focal.claim_id,
                    relation_type=relation,
                    notes="Reviewed relationship.",
                    recorded_by="reviewer",
                    recorded_at=datetime(2026, 9, 24, tzinfo=UTC),
                    claims=claims,
                )
            dossier = EvidenceDossierBuilder().build(
                focal.claim_id,
                claims=claims,
                evidence_graph=graph,
            )
            self.assertEqual(dossier.posture, EvidencePosture.DISPUTED)
            self.assertIn(
                "CONTRADICTORY_CLAIMS_PRESENT",
                {flag.code for flag in dossier.flags},
            )
            graph.close()
            claims.close()

    def test_failed_replication_is_not_hidden_by_support(self):
        with tempfile.TemporaryDirectory() as tmp:
            claims = ClaimStore(Path(tmp) / "claims.duckdb")
            graph = ClaimEvidenceGraph(Path(tmp) / "evidence.duckdb")
            focal = card("Original result.", "sha256:a")
            support = card("Supporting result.", "sha256:b")
            failed = card(
                "Independent direct replication failed.",
                "sha256:c",
                ClaimStance.CONTRADICTS,
            )
            for item in (focal, support, failed):
                claims.add(item)
            graph.relate(
                from_claim_id=support.claim_id,
                to_claim_id=focal.claim_id,
                relation_type=ClaimRelationType.SUPPORTS,
                notes="Support.",
                recorded_by="reviewer",
                recorded_at=datetime(2026, 9, 24, tzinfo=UTC),
                claims=claims,
            )
            graph.record_replication(
                original_claim_id=focal.claim_id,
                replication_claim_id=failed.claim_id,
                kind=ReplicationKind.DIRECT,
                outcome=ReplicationOutcome.FAILS_TO_REPLICATE,
                independent_data=True,
                preregistered=True,
                notes="Independent direct replication.",
                reviewer="reviewer-b",
                recorded_at=datetime(2026, 9, 24, 16, tzinfo=UTC),
                claims=claims,
            )
            dossier = EvidenceDossierBuilder().build(
                focal.claim_id,
                claims=claims,
                evidence_graph=graph,
            )
            self.assertEqual(
                dossier.posture,
                EvidencePosture.FAILED_REPLICATION_PRESENT,
            )
            self.assertEqual(dossier.independent_replication_count, 1)
            graph.close()
            claims.close()

    def test_conflicting_replications_have_dedicated_posture(self):
        with tempfile.TemporaryDirectory() as tmp:
            claims = ClaimStore(Path(tmp) / "claims.duckdb")
            graph = ClaimEvidenceGraph(Path(tmp) / "evidence.duckdb")
            focal = card("Original result.", "sha256:a")
            rep_ok = card("Replication reproduced result.", "sha256:b")
            rep_fail = card(
                "Replication did not reproduce result.",
                "sha256:c",
                ClaimStance.CONTRADICTS,
            )
            for item in (focal, rep_ok, rep_fail):
                claims.add(item)
            for rep, outcome in (
                (rep_ok, ReplicationOutcome.REPLICATES),
                (rep_fail, ReplicationOutcome.FAILS_TO_REPLICATE),
            ):
                graph.record_replication(
                    original_claim_id=focal.claim_id,
                    replication_claim_id=rep.claim_id,
                    kind=ReplicationKind.DIRECT,
                    outcome=outcome,
                    independent_data=True,
                    preregistered=None,
                    notes="Replication record.",
                    reviewer="reviewer",
                    recorded_at=datetime(2026, 9, 24, 16, tzinfo=UTC),
                    claims=claims,
                )
            dossier = EvidenceDossierBuilder().build(
                focal.claim_id,
                claims=claims,
                evidence_graph=graph,
            )
            self.assertEqual(
                dossier.posture,
                EvidencePosture.REPLICATION_CONFLICT,
            )
            self.assertIn(
                "REPLICATION_RESULTS_CONFLICT",
                {flag.code for flag in dossier.flags},
            )
            graph.close()
            claims.close()

    def test_dossier_exposes_no_numeric_truth_score(self):
        fields = set(EvidenceDossierBuilder().CAVEAT.lower().split())
        self.assertIn("not", fields)
        self.assertIn("probability", fields)


if __name__ == "__main__":
    unittest.main()
