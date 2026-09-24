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
from quantos.scenarios import (
    OutcomeRange,
    ProbabilityBand,
    ScenarioSetStore,
    make_scenario,
)


UTC = timezone.utc


def build_case(root):
    claims = ClaimStore(root / "claims.duckdb")
    cases = ResearchCaseStore(root / "cases.duckdb")
    when = datetime(2026, 9, 24, 12, tzinfo=UTC)
    text = "Historical signal evidence exists."
    sources = ("sha256:a",)
    evidence = ClaimCard(
        text=text,
        claim_type=ClaimType.EMPIRICAL,
        stance=ClaimStance.SUPPORTS,
        epistemic_state=EpistemicState.OBSERVED,
        topic="signal evidence",
        source_artifact_ids=sources,
        locator="Table 1",
        scope={"sample": "historical"},
        assumptions=(),
        limitations=("May not persist.",),
        as_of=when,
        claim_id=make_claim_id(
            text=text,
            source_artifact_ids=sources,
            locator="Table 1",
        ),
    )
    claims.add(evidence)
    case = cases.create(
        case_type=ResearchCaseType.SYSTEMATIC,
        subject_ids=("signal:test",),
        universe="Test equity universe",
        thesis="The signal merits prospective evaluation.",
        mechanism="A documented historical relation may persist under constrained conditions.",
        horizon="12 months",
        as_of=when,
        supporting_claim_ids=(evidence.claim_id,),
        alternative_explanations=("Data mining may explain the result.",),
        falsifiers=("Prospective net performance is materially negative.",),
        monitoring_conditions=("Monitor costs and breadth.",),
        author="researcher",
        created_at=when + timedelta(minutes=1),
        claims=claims,
    )
    return claims, cases, case


def base_scenarios():
    bear = make_scenario(
        name="bear",
        description="Signal degrades after publication and costs dominate.",
        probability=ProbabilityBand(0.15, 0.25, 0.40),
        probability_rationale="Historical factor decay and implementation uncertainty justify a material downside band.",
        assumptions=("Crowding rises.",),
        conditions=("Breadth declines and turnover costs rise.",),
        outcomes=(
            OutcomeRange(
                "net_relative_return",
                "decimal",
                -0.12,
                -0.06,
                -0.01,
            ),
        ),
    )
    base = make_scenario(
        name="base",
        description="Signal persists weakly after realistic costs.",
        probability=ProbabilityBand(0.30, 0.50, 0.65),
        probability_rationale="Central case reflects partial persistence with conservative implementation drag.",
        assumptions=("Signal efficacy decays but does not disappear.",),
        conditions=("Breadth remains stable and realized costs match the model.",),
        outcomes=(
            OutcomeRange(
                "net_relative_return",
                "decimal",
                -0.01,
                0.03,
                0.07,
            ),
        ),
    )
    bull = make_scenario(
        name="bull",
        description="Signal persists broadly with lower-than-feared decay.",
        probability=ProbabilityBand(0.10, 0.25, 0.45),
        probability_rationale="Upside remains plausible but is bounded by publication and crowding risk.",
        assumptions=("Implementation capacity remains sufficient.",),
        conditions=("Breadth and hit rate remain stable across subperiods.",),
        outcomes=(
            OutcomeRange(
                "net_relative_return",
                "decimal",
                0.05,
                0.10,
                0.18,
            ),
        ),
    )
    return bear, base, bull


class ScenarioTests(unittest.TestCase):
    def test_probability_band_requires_ordered_bounded_values(self):
        with self.assertRaises(ValueError):
            make_scenario(
                name="bad",
                description="Bad probability.",
                probability=ProbabilityBand(0.5, 0.4, 0.6),
                probability_rationale="test",
                assumptions=("a",),
                conditions=("c",),
                outcomes=(OutcomeRange("x", "u", 0, 1, 2),),
            )

    def test_central_probabilities_must_sum_to_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claims, cases, case = build_case(root)
            store = ScenarioSetStore(root / "scenarios.duckdb")
            bear, base, bull = base_scenarios()
            bad_bull = make_scenario(
                name=bull.name,
                description=bull.description,
                probability=ProbabilityBand(0.10, 0.20, 0.45),
                probability_rationale=bull.probability_rationale,
                assumptions=bull.assumptions,
                conditions=bull.conditions,
                outcomes=bull.outcomes,
            )
            with self.assertRaises(ValueError):
                store.create(
                    case_id=case.case_id,
                    scenarios=(bear, base, bad_bull),
                    author="researcher",
                    created_at=datetime(2026, 9, 24, 13, tzinfo=UTC),
                    cases=cases,
                )
            store.close()
            cases.close()
            claims.close()

    def test_probability_intervals_must_be_jointly_feasible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claims, cases, case = build_case(root)
            store = ScenarioSetStore(root / "scenarios.duckdb")
            scenarios = tuple(
                make_scenario(
                    name=f"s{i}",
                    description="Scenario",
                    probability=ProbabilityBand(0.40, 1 / 3, 0.50),
                    probability_rationale="test",
                    assumptions=("a",),
                    conditions=("c",),
                    outcomes=(OutcomeRange("x", "u", 0, 1, 2),),
                )
                for i in range(3)
            )
            with self.assertRaises(ValueError):
                store.create(
                    case_id=case.case_id,
                    scenarios=scenarios,
                    author="researcher",
                    created_at=datetime(2026, 9, 24, 13, tzinfo=UTC),
                    cases=cases,
                )
            store.close()
            cases.close()
            claims.close()

    def test_valid_set_persists_and_expected_central_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claims, cases, case = build_case(root)
            store = ScenarioSetStore(root / "scenarios.duckdb")
            scenarios = base_scenarios()
            result = store.create(
                case_id=case.case_id,
                scenarios=scenarios,
                author="researcher",
                created_at=datetime(2026, 9, 24, 13, tzinfo=UTC),
                cases=cases,
            )
            self.assertEqual(store.get(result.scenario_set_id), result)
            expected = (
                0.25 * -0.06
                + 0.50 * 0.03
                + 0.25 * 0.10
            )
            self.assertAlmostEqual(
                ScenarioSetStore.expected_central(
                    result,
                    metric="net_relative_return",
                    unit="decimal",
                ),
                expected,
            )
            store.close()
            cases.close()
            claims.close()

    def test_expected_central_fails_when_metric_is_not_common(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claims, cases, case = build_case(root)
            store = ScenarioSetStore(root / "scenarios.duckdb")
            result = store.create(
                case_id=case.case_id,
                scenarios=base_scenarios(),
                author="researcher",
                created_at=datetime(2026, 9, 24, 13, tzinfo=UTC),
                cases=cases,
            )
            with self.assertRaises(ValueError):
                ScenarioSetStore.expected_central(
                    result,
                    metric="not_present",
                    unit="decimal",
                )
            store.close()
            cases.close()
            claims.close()

    def test_scenario_revision_cannot_move_to_different_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            claims, cases, case = build_case(root)
            store = ScenarioSetStore(root / "scenarios.duckdb")
            first = store.create(
                case_id=case.case_id,
                scenarios=base_scenarios(),
                author="researcher",
                created_at=datetime(2026, 9, 24, 13, tzinfo=UTC),
                cases=cases,
            )
            with self.assertRaises(ValueError):
                store.create(
                    case_id="research-case:missing",
                    scenarios=base_scenarios(),
                    author="researcher",
                    created_at=datetime(2026, 9, 24, 14, tzinfo=UTC),
                    cases=cases,
                    supersedes_scenario_set_id=first.scenario_set_id,
                )
            store.close()
            cases.close()
            claims.close()


if __name__ == "__main__":
    unittest.main()
