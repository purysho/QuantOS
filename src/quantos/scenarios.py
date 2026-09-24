from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb

from .research_case import ResearchCaseStore


@dataclass(frozen=True)
class ProbabilityBand:
    low: float
    central: float
    high: float


@dataclass(frozen=True)
class OutcomeRange:
    metric: str
    unit: str
    low: float
    central: float
    high: float


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    name: str
    description: str
    probability: ProbabilityBand
    probability_rationale: str
    assumptions: tuple[str, ...]
    conditions: tuple[str, ...]
    outcomes: tuple[OutcomeRange, ...]


@dataclass(frozen=True)
class ScenarioSet:
    scenario_set_id: str
    case_id: str
    scenarios: tuple[Scenario, ...]
    author: str
    created_at: datetime
    supersedes_scenario_set_id: str | None


def make_scenario(
    *,
    name: str,
    description: str,
    probability: ProbabilityBand,
    probability_rationale: str,
    assumptions: tuple[str, ...],
    conditions: tuple[str, ...],
    outcomes: tuple[OutcomeRange, ...],
) -> Scenario:
    _validate_probability(probability)
    if not name.strip() or not description.strip():
        raise ValueError("scenario name and description are required")
    if not probability_rationale.strip():
        raise ValueError("scenario probability rationale is required")
    if not assumptions or not all(item.strip() for item in assumptions):
        raise ValueError("scenario requires explicit assumptions")
    if not conditions or not all(item.strip() for item in conditions):
        raise ValueError("scenario requires observable conditions")
    if not outcomes:
        raise ValueError("scenario requires at least one outcome range")

    seen: set[tuple[str, str]] = set()
    clean_outcomes: list[OutcomeRange] = []
    for outcome in outcomes:
        if not outcome.metric.strip() or not outcome.unit.strip():
            raise ValueError("outcome metric and unit are required")
        if not outcome.low <= outcome.central <= outcome.high:
            raise ValueError("outcome range must satisfy low <= central <= high")
        key = (outcome.metric.strip(), outcome.unit.strip())
        if key in seen:
            raise ValueError("duplicate scenario outcome metric/unit")
        seen.add(key)
        clean_outcomes.append(
            OutcomeRange(
                metric=key[0],
                unit=key[1],
                low=float(outcome.low),
                central=float(outcome.central),
                high=float(outcome.high),
            )
        )

    clean_assumptions = tuple(item.strip() for item in assumptions)
    clean_conditions = tuple(item.strip() for item in conditions)
    payload = {
        "name": name.strip(),
        "description": description.strip(),
        "probability": {
            "low": probability.low,
            "central": probability.central,
            "high": probability.high,
        },
        "probability_rationale": probability_rationale.strip(),
        "assumptions": clean_assumptions,
        "conditions": clean_conditions,
        "outcomes": [
            {
                "metric": outcome.metric,
                "unit": outcome.unit,
                "low": outcome.low,
                "central": outcome.central,
                "high": outcome.high,
            }
            for outcome in clean_outcomes
        ],
    }
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return Scenario(
        scenario_id="scenario:" + hashlib.sha256(material).hexdigest(),
        name=payload["name"],
        description=payload["description"],
        probability=ProbabilityBand(
            low=float(probability.low),
            central=float(probability.central),
            high=float(probability.high),
        ),
        probability_rationale=payload["probability_rationale"],
        assumptions=clean_assumptions,
        conditions=clean_conditions,
        outcomes=tuple(clean_outcomes),
    )


def make_scenario_set_id(
    *,
    case_id: str,
    scenarios: tuple[Scenario, ...],
    supersedes_scenario_set_id: str | None,
) -> str:
    payload = {
        "case_id": case_id,
        "scenario_ids": [scenario.scenario_id for scenario in scenarios],
        "supersedes": supersedes_scenario_set_id,
    }
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "scenario-set:" + hashlib.sha256(material).hexdigest()


class ScenarioSetStore:
    """Immutable case scenarios with interval probabilities and explicit rationale."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS scenario_sets (
                scenario_set_id VARCHAR PRIMARY KEY,
                case_id VARCHAR NOT NULL,
                scenarios_json VARCHAR NOT NULL,
                author VARCHAR NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                supersedes_scenario_set_id VARCHAR
            )
            """
        )

    def create(
        self,
        *,
        case_id: str,
        scenarios: tuple[Scenario, ...],
        author: str,
        created_at: datetime,
        cases: ResearchCaseStore,
        supersedes_scenario_set_id: str | None = None,
    ) -> ScenarioSet:
        if cases.get(case_id) is None:
            raise ValueError("scenario set requires an existing research case")
        if created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        if not author.strip():
            raise ValueError("author is required")
        if len(scenarios) < 2:
            raise ValueError("scenario set requires at least two scenarios")

        names = [scenario.name for scenario in scenarios]
        if len(set(names)) != len(names):
            raise ValueError("scenario names must be unique")
        ids = [scenario.scenario_id for scenario in scenarios]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate scenarios are not allowed")

        for scenario in scenarios:
            expected = make_scenario(
                name=scenario.name,
                description=scenario.description,
                probability=scenario.probability,
                probability_rationale=scenario.probability_rationale,
                assumptions=scenario.assumptions,
                conditions=scenario.conditions,
                outcomes=scenario.outcomes,
            )
            if expected != scenario:
                raise ValueError("scenario ID/content mismatch")

        central_sum = sum(item.probability.central for item in scenarios)
        low_sum = sum(item.probability.low for item in scenarios)
        high_sum = sum(item.probability.high for item in scenarios)
        if not math.isclose(central_sum, 1.0, abs_tol=1e-9):
            raise ValueError("central scenario probabilities must sum to 1")
        if low_sum > 1.0 + 1e-9:
            raise ValueError("scenario probability lower bounds are jointly infeasible")
        if high_sum < 1.0 - 1e-9:
            raise ValueError("scenario probability upper bounds are jointly infeasible")

        if supersedes_scenario_set_id is not None:
            prior = self.get(supersedes_scenario_set_id)
            if prior is None:
                raise ValueError("superseded scenario set does not exist")
            if prior.case_id != case_id:
                raise ValueError(
                    "scenario-set revision cannot silently move to another research case"
                )

        scenario_set_id = make_scenario_set_id(
            case_id=case_id,
            scenarios=scenarios,
            supersedes_scenario_set_id=supersedes_scenario_set_id,
        )
        result = ScenarioSet(
            scenario_set_id=scenario_set_id,
            case_id=case_id,
            scenarios=scenarios,
            author=author.strip(),
            created_at=created_at,
            supersedes_scenario_set_id=supersedes_scenario_set_id,
        )
        existing = self.get(scenario_set_id)
        if existing is not None:
            if existing != result:
                raise ValueError("scenario-set identity conflict")
            return existing

        self._con.execute(
            "INSERT INTO scenario_sets VALUES (?, ?, ?, ?, ?, ?)",
            [
                result.scenario_set_id,
                result.case_id,
                json.dumps(
                    [self._scenario_to_dict(item) for item in result.scenarios],
                    sort_keys=True,
                ),
                result.author,
                result.created_at,
                result.supersedes_scenario_set_id,
            ],
        )
        return result

    def get(self, scenario_set_id: str) -> ScenarioSet | None:
        row = self._con.execute(
            """
            SELECT scenario_set_id, case_id, scenarios_json, author,
                   created_at, supersedes_scenario_set_id
            FROM scenario_sets
            WHERE scenario_set_id = ?
            """,
            [scenario_set_id],
        ).fetchone()
        if row is None:
            return None
        scenarios = tuple(
            self._scenario_from_dict(item)
            for item in json.loads(str(row[2]))
        )
        return ScenarioSet(
            scenario_set_id=str(row[0]),
            case_id=str(row[1]),
            scenarios=scenarios,
            author=str(row[3]),
            created_at=row[4],
            supersedes_scenario_set_id=(
                str(row[5]) if row[5] is not None else None
            ),
        )

    @staticmethod
    def expected_central(
        scenario_set: ScenarioSet,
        *,
        metric: str,
        unit: str,
    ) -> float:
        values: list[tuple[float, float]] = []
        for scenario in scenario_set.scenarios:
            matches = [
                outcome
                for outcome in scenario.outcomes
                if outcome.metric == metric and outcome.unit == unit
            ]
            if len(matches) != 1:
                raise ValueError(
                    "expected-central calculation requires exactly one matching "
                    "outcome in every scenario"
                )
            values.append(
                (scenario.probability.central, matches[0].central)
            )
        return sum(probability * value for probability, value in values)

    @staticmethod
    def _scenario_to_dict(scenario: Scenario) -> dict[str, object]:
        return {
            "scenario_id": scenario.scenario_id,
            "name": scenario.name,
            "description": scenario.description,
            "probability": {
                "low": scenario.probability.low,
                "central": scenario.probability.central,
                "high": scenario.probability.high,
            },
            "probability_rationale": scenario.probability_rationale,
            "assumptions": scenario.assumptions,
            "conditions": scenario.conditions,
            "outcomes": [
                {
                    "metric": outcome.metric,
                    "unit": outcome.unit,
                    "low": outcome.low,
                    "central": outcome.central,
                    "high": outcome.high,
                }
                for outcome in scenario.outcomes
            ],
        }

    @staticmethod
    def _scenario_from_dict(payload: dict[str, object]) -> Scenario:
        probability = payload["probability"]
        assert isinstance(probability, dict)
        outcomes = payload["outcomes"]
        assert isinstance(outcomes, list)
        return Scenario(
            scenario_id=str(payload["scenario_id"]),
            name=str(payload["name"]),
            description=str(payload["description"]),
            probability=ProbabilityBand(
                low=float(probability["low"]),
                central=float(probability["central"]),
                high=float(probability["high"]),
            ),
            probability_rationale=str(payload["probability_rationale"]),
            assumptions=tuple(payload["assumptions"]),
            conditions=tuple(payload["conditions"]),
            outcomes=tuple(
                OutcomeRange(
                    metric=str(outcome["metric"]),
                    unit=str(outcome["unit"]),
                    low=float(outcome["low"]),
                    central=float(outcome["central"]),
                    high=float(outcome["high"]),
                )
                for outcome in outcomes
            ),
        )

    def close(self) -> None:
        self._con.close()


def _validate_probability(probability: ProbabilityBand) -> None:
    values = (probability.low, probability.central, probability.high)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("scenario probabilities must be finite")
    if not 0.0 <= probability.low <= probability.central <= probability.high <= 1.0:
        raise ValueError(
            "probability band must satisfy 0 <= low <= central <= high <= 1"
        )
