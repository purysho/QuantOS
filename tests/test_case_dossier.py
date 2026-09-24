import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from quantos.case_dossier import CaseDossierBuilder
from quantos.claim_evidence_graph import ClaimEvidenceGraph, ClaimRelationType
from quantos.claims import (
    ClaimCard,
    ClaimStance,
    ClaimStore,
    ClaimType,
    make_claim_id,
)
from quantos.models import EpistemicState
from quantos.research_case import ResearchCaseStore, ResearchCaseType
from quantos.scenarios import (
    OutcomeRange,
    ProbabilityBand,
    ScenarioSetStore,
    make_scenario,
)


UTC = timezone.utc


def claim(text, artifact, as_of, stance=ClaimStance.SUPPORTS):
    sources = (artifact,)
    return ClaimCard(
        text=text,
        claim_type=ClaimType.EMPIRICAL,
        stance=stance,
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


def fixture(root):
    claims = ClaimStore(root / "claims.duckdb")
    evidence_graph = ClaimEvidenceGraph(root / "evidence.duckdb")
    cases = ResearchCaseStore(root / "cases.duckdb")
    scenarios = ScenarioSetStore(root / "scenarios.duckdb")
    when = datetime(2026, 9, 24, 12, tzinfo=UTC)

    support = claim("Historical support.", "sha256:a", when)
    limit = claim(
        "Effect weakens in illiquid names.",
        "sha256:b",
        when,
        ClaimStance.LIMITS,
    )
    for item in (support, limit):
        claims.add(item)

    case = cases.create(
        case_type=ResearchCaseType.SYSTEMATIC,
        subject_ids=("signal:test",),
        universe="Test universe",
        thesis="Signal merits prospective evaluation.",
        mechanism="Historical relation may persist under bounded conditions.",
        horizon="12 months",
        as_of=when,
        supporting_claim_ids=(support.claim_id,),
        limiting_claim_ids=(limit.claim_id,),
        alternative_explanations=("Data mining may explain the result.",),
        falsifiers=("Prospective net result is materially negative.",),
        monitoring_conditions=("Monitor breadth and costs.",),
        author="researcher",
        created_at=when + timedelta(minutes=1),
        claims=claims,
    )

    bear = make_scenario(
        name="bear",
        description="Decay dominates.",
        probability=ProbabilityBand(0.2, 0.4, 0.6),
        probability_rationale="Material decay risk.",
        assumptions=("Crowding increases.",),
        conditions=("Breadth falls.",),
        outcomes=(OutcomeRange("net_return", "decimal", -0.1, -0.05, 0.0),),
    )
    base = make_scenario(
        name="base",
        description="Weak persistence.",
        probability=ProbabilityBand(0.2, 0.4, 0.6),
        probability_rationale="Partial persistence.",
        assumptions=("Costs remain bounded.",),
        conditions=("Breadth is stable.",),
        outcomes=(OutcomeRange("net_return", "decimal", -0.01, 0.02, 0.05),),
    )
    bull = make_scenario(
        name="bull",
        description="Broader persistence.",
        probability=ProbabilityBand(0.1, 0.2, 0.4),
        probability_rationale="Upside is possible but bounded.",
        assumptions=("Capacity remains adequate.",),
        conditions=("Breadth improves.",),
        outcomes=(OutcomeRange("net_return", "decimal", 0.03, 0.08, 0.15),),
    )
    scenario_set = scenarios.create(
        case_id=case.case_id,
        scenarios=(bear, base, bull),
        author="researcher",
        created_at=when + timedelta(minutes=2),
        cases=cases,
    )
    return claims, evidence_graph, cases, scenarios, case, scenario_set, support, limit


class CaseDossierTests(unittest.TestCase):
    def test_case_dossier_binds_case_scenarios_and_claim_dossiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claims, graph, cases, scenarios, case, scenario_set, support, limit = fixture(root)
            dossier = CaseDossierBuilder().build(
                case_id=case.case_id,
                scenario_set_id=scenario_set.scenario_set_id,
                cases=cases,
                scenarios=scenarios,
                claims=claims,
                evidence_graph=graph,
            )
            self.assertEqual(dossier.case, case)
            self.assertEqual(dossier.scenario_set, scenario_set)
            self.assertEqual(len(dossier.claim_dossiers), 2)
            self.assertTrue(
                dossier.case_dossier_fingerprint.startswith("case-dossier:")
            )
            scenarios.close()
            cases.close()
            graph.close()
            claims.close()

    def test_new_claim_evidence_changes_case_dossier_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claims, graph, cases, scenarios, case, scenario_set, support, limit = fixture(root)
            builder = CaseDossierBuilder()
            before = builder.build(
                case_id=case.case_id,
                scenario_set_id=scenario_set.scenario_set_id,
                cases=cases,
                scenarios=scenarios,
                claims=claims,
                evidence_graph=graph,
            )
            contradiction = claim(
                "Later sample found no comparable effect.",
                "sha256:c",
                case.as_of,
                ClaimStance.CONTRADICTS,
            )
            claims.add(contradiction)
            graph.relate(
                from_claim_id=contradiction.claim_id,
                to_claim_id=support.claim_id,
                relation_type=ClaimRelationType.CONTRADICTS,
                notes="Later contradictory evidence.",
                recorded_by="reviewer",
                recorded_at=case.as_of + timedelta(hours=1),
                claims=claims,
            )
            after = builder.build(
                case_id=case.case_id,
                scenario_set_id=scenario_set.scenario_set_id,
                cases=cases,
                scenarios=scenarios,
                claims=claims,
                evidence_graph=graph,
            )
            self.assertNotEqual(
                before.case_dossier_fingerprint,
                after.case_dossier_fingerprint,
            )
            scenarios.close()
            cases.close()
            graph.close()
            claims.close()

    def test_changed_scenario_set_changes_case_dossier_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claims, graph, cases, scenarios, case, scenario_set, support, limit = fixture(root)
            builder = CaseDossierBuilder()
            before = builder.build(
                case_id=case.case_id,
                scenario_set_id=scenario_set.scenario_set_id,
                cases=cases,
                scenarios=scenarios,
                claims=claims,
                evidence_graph=graph,
            )
            old = scenario_set.scenarios
            revised_bear = make_scenario(
                name=old[0].name,
                description=old[0].description,
                probability=ProbabilityBand(0.2, 0.35, 0.55),
                probability_rationale="Updated after new calibration evidence.",
                assumptions=old[0].assumptions,
                conditions=old[0].conditions,
                outcomes=old[0].outcomes,
            )
            revised_base = make_scenario(
                name=old[1].name,
                description=old[1].description,
                probability=ProbabilityBand(0.25, 0.45, 0.65),
                probability_rationale="Updated after new calibration evidence.",
                assumptions=old[1].assumptions,
                conditions=old[1].conditions,
                outcomes=old[1].outcomes,
            )
            revised_bull = make_scenario(
                name=old[2].name,
                description=old[2].description,
                probability=ProbabilityBand(0.1, 0.20, 0.4),
                probability_rationale=old[2].probability_rationale,
                assumptions=old[2].assumptions,
                conditions=old[2].conditions,
                outcomes=old[2].outcomes,
            )
            revised = scenarios.create(
                case_id=case.case_id,
                scenarios=(revised_bear, revised_base, revised_bull),
                author="researcher",
                created_at=case.created_at + timedelta(hours=1),
                cases=cases,
                supersedes_scenario_set_id=scenario_set.scenario_set_id,
            )
            after = builder.build(
                case_id=case.case_id,
                scenario_set_id=revised.scenario_set_id,
                cases=cases,
                scenarios=scenarios,
                claims=claims,
                evidence_graph=graph,
            )
            self.assertNotEqual(
                before.case_dossier_fingerprint,
                after.case_dossier_fingerprint,
            )
            scenarios.close()
            cases.close()
            graph.close()
            claims.close()

    def test_scenario_set_from_other_case_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claims, graph, cases, scenarios, case, scenario_set, support, limit = fixture(root)
            when = case.as_of + timedelta(days=1)
            other_case = cases.create(
                case_type=ResearchCaseType.SYSTEMATIC,
                subject_ids=("signal:other",),
                universe="Other universe",
                thesis="Different case.",
                mechanism="Different mechanism.",
                horizon="6 months",
                as_of=when,
                supporting_claim_ids=(support.claim_id,),
                alternative_explanations=("Alternative.",),
                falsifiers=("Falsifier.",),
                monitoring_conditions=("Monitor.",),
                author="researcher",
                created_at=when + timedelta(minutes=1),
                claims=claims,
            )
            with self.assertRaises(ValueError):
                CaseDossierBuilder().build(
                    case_id=other_case.case_id,
                    scenario_set_id=scenario_set.scenario_set_id,
                    cases=cases,
                    scenarios=scenarios,
                    claims=claims,
                    evidence_graph=graph,
                )
            scenarios.close()
            cases.close()
            graph.close()
            claims.close()


if __name__ == "__main__":
    unittest.main()
