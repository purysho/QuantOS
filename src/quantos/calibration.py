from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb

from .case_dossier import CaseDossier
from .readiness import ProspectiveShadowPermit, ResearchReadinessLedger


@dataclass(frozen=True)
class ScenarioProbability:
    scenario_id: str
    scenario_name: str
    probability: float


@dataclass(frozen=True)
class ForecastRecord:
    forecast_id: str
    permit_id: str
    case_id: str
    case_dossier_fingerprint: str
    scenario_set_id: str
    forecaster: str
    forecast_at: datetime
    horizon_end: datetime
    probabilities: tuple[ScenarioProbability, ...]


@dataclass(frozen=True)
class OutcomeRecord:
    outcome_id: str
    forecast_id: str
    realized_scenario_id: str
    observed_at: datetime
    adjudicator: str
    notes: str
    evidence_references: tuple[str, ...]


@dataclass(frozen=True)
class ForecastScore:
    forecast_id: str
    scenario_count: int
    brier_score: float
    log_loss: float


@dataclass(frozen=True)
class CalibrationSummary:
    forecaster: str
    scenario_count: int
    observations: int
    mean_brier_score: float | None
    mean_log_loss: float | None
    state: str
    caveat: str


def make_forecast_id(
    *,
    permit_id: str,
    case_dossier_fingerprint: str,
    forecaster: str,
    forecast_at: datetime,
    horizon_end: datetime,
    probabilities: tuple[ScenarioProbability, ...],
) -> str:
    payload = {
        "permit_id": permit_id,
        "case_dossier_fingerprint": case_dossier_fingerprint,
        "forecaster": forecaster,
        "forecast_at": forecast_at.isoformat(),
        "horizon_end": horizon_end.isoformat(),
        "probabilities": [
            {
                "scenario_id": item.scenario_id,
                "scenario_name": item.scenario_name,
                "probability": item.probability,
            }
            for item in probabilities
        ],
    }
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "forecast:" + hashlib.sha256(material).hexdigest()


def make_outcome_id(
    *,
    forecast_id: str,
    realized_scenario_id: str,
    adjudicator: str,
    notes: str,
    evidence_references: tuple[str, ...],
) -> str:
    payload = {
        "forecast_id": forecast_id,
        "realized_scenario_id": realized_scenario_id,
        "adjudicator": adjudicator,
        "notes": notes,
        "evidence_references": evidence_references,
    }
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "forecast-outcome:" + hashlib.sha256(material).hexdigest()


class ForecastCalibrationLedger:
    """Prospective scenario forecasts and later evidence-backed outcome labels."""

    SUMMARY_CAVEAT = (
        "Calibration metrics describe recorded forecast behavior only. They do not "
        "establish market skill, causal validity, future alpha, or capital authority."
    )

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS scenario_forecasts (
                forecast_id VARCHAR PRIMARY KEY,
                permit_id VARCHAR NOT NULL,
                case_id VARCHAR NOT NULL,
                case_dossier_fingerprint VARCHAR NOT NULL,
                scenario_set_id VARCHAR NOT NULL,
                forecaster VARCHAR NOT NULL,
                forecast_at TIMESTAMPTZ NOT NULL,
                horizon_end TIMESTAMPTZ NOT NULL,
                probabilities_json VARCHAR NOT NULL
            )
            """
        )
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS scenario_outcomes (
                outcome_id VARCHAR PRIMARY KEY,
                forecast_id VARCHAR NOT NULL UNIQUE,
                realized_scenario_id VARCHAR NOT NULL,
                observed_at TIMESTAMPTZ NOT NULL,
                adjudicator VARCHAR NOT NULL,
                notes VARCHAR NOT NULL,
                evidence_references_json VARCHAR NOT NULL
            )
            """
        )

    def record_forecast(
        self,
        *,
        dossier: CaseDossier,
        permit: ProspectiveShadowPermit,
        forecaster: str,
        forecast_at: datetime,
        horizon_end: datetime,
    ) -> ForecastRecord:
        if not ResearchReadinessLedger.applies_to(permit, dossier):
            raise ValueError(
                "forecast requires a prospective-shadow permit for this exact case dossier"
            )
        if not forecaster.strip():
            raise ValueError("forecaster is required")
        if forecast_at.tzinfo is None or horizon_end.tzinfo is None:
            raise ValueError("forecast_at and horizon_end must be timezone-aware")
        if forecast_at < permit.issued_at:
            raise ValueError("forecast cannot predate its shadow permit")
        if horizon_end <= forecast_at:
            raise ValueError("horizon_end must be after forecast_at")

        probabilities = tuple(
            ScenarioProbability(
                scenario_id=scenario.scenario_id,
                scenario_name=scenario.name,
                probability=float(scenario.probability.central),
            )
            for scenario in dossier.scenario_set.scenarios
        )
        if len(probabilities) < 2:
            raise ValueError("forecast requires at least two scenarios")
        total = sum(item.probability for item in probabilities)
        if not math.isclose(total, 1.0, abs_tol=1e-9):
            raise ValueError("scenario central probabilities must sum to 1")
        if any(
            not math.isfinite(item.probability)
            or not 0.0 <= item.probability <= 1.0
            for item in probabilities
        ):
            raise ValueError("forecast probabilities must be finite and bounded")

        forecast_id = make_forecast_id(
            permit_id=permit.permit_id,
            case_dossier_fingerprint=dossier.case_dossier_fingerprint,
            forecaster=forecaster.strip(),
            forecast_at=forecast_at,
            horizon_end=horizon_end,
            probabilities=probabilities,
        )
        forecast = ForecastRecord(
            forecast_id=forecast_id,
            permit_id=permit.permit_id,
            case_id=dossier.case.case_id,
            case_dossier_fingerprint=dossier.case_dossier_fingerprint,
            scenario_set_id=dossier.scenario_set.scenario_set_id,
            forecaster=forecaster.strip(),
            forecast_at=forecast_at,
            horizon_end=horizon_end,
            probabilities=probabilities,
        )
        existing = self.get_forecast(forecast_id)
        if existing is not None:
            return existing

        self._con.execute(
            """
            INSERT INTO scenario_forecasts
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                forecast.forecast_id,
                forecast.permit_id,
                forecast.case_id,
                forecast.case_dossier_fingerprint,
                forecast.scenario_set_id,
                forecast.forecaster,
                forecast.forecast_at,
                forecast.horizon_end,
                json.dumps(
                    [
                        {
                            "scenario_id": item.scenario_id,
                            "scenario_name": item.scenario_name,
                            "probability": item.probability,
                        }
                        for item in forecast.probabilities
                    ],
                    sort_keys=True,
                ),
            ],
        )
        return forecast

    def record_outcome(
        self,
        *,
        forecast_id: str,
        realized_scenario_id: str,
        observed_at: datetime,
        adjudicator: str,
        notes: str,
        evidence_references: tuple[str, ...],
    ) -> OutcomeRecord:
        forecast = self.get_forecast(forecast_id)
        if forecast is None:
            raise KeyError(forecast_id)
        if observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        if observed_at < forecast.horizon_end:
            raise ValueError(
                "outcome classification cannot occur before the forecast horizon ends"
            )
        if not adjudicator.strip():
            raise ValueError("outcome adjudicator is required")
        if adjudicator.strip() == forecast.forecaster:
            raise ValueError(
                "outcome adjudicator must differ from the original forecaster"
            )
        if not notes.strip():
            raise ValueError("outcome classification notes are required")
        clean_refs = tuple(
            reference.strip()
            for reference in evidence_references
            if reference.strip()
        )
        if not clean_refs:
            raise ValueError("outcome classification requires evidence references")
        scenario_ids = {item.scenario_id for item in forecast.probabilities}
        if realized_scenario_id not in scenario_ids:
            raise ValueError(
                "realized scenario must be one of the frozen forecast scenarios"
            )

        existing = self.outcome_for(forecast_id)
        if existing is not None:
            requested = (
                realized_scenario_id,
                adjudicator.strip(),
                notes.strip(),
                clean_refs,
            )
            current = (
                existing.realized_scenario_id,
                existing.adjudicator,
                existing.notes,
                existing.evidence_references,
            )
            if requested != current:
                raise ValueError("forecast already has a different outcome classification")
            return existing

        outcome_id = make_outcome_id(
            forecast_id=forecast_id,
            realized_scenario_id=realized_scenario_id,
            adjudicator=adjudicator.strip(),
            notes=notes.strip(),
            evidence_references=clean_refs,
        )
        outcome = OutcomeRecord(
            outcome_id=outcome_id,
            forecast_id=forecast_id,
            realized_scenario_id=realized_scenario_id,
            observed_at=observed_at,
            adjudicator=adjudicator.strip(),
            notes=notes.strip(),
            evidence_references=clean_refs,
        )
        self._con.execute(
            """
            INSERT INTO scenario_outcomes
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                outcome.outcome_id,
                outcome.forecast_id,
                outcome.realized_scenario_id,
                outcome.observed_at,
                outcome.adjudicator,
                outcome.notes,
                json.dumps(outcome.evidence_references),
            ],
        )
        return outcome

    def score(self, forecast_id: str) -> ForecastScore:
        forecast = self.get_forecast(forecast_id)
        if forecast is None:
            raise KeyError(forecast_id)
        outcome = self.outcome_for(forecast_id)
        if outcome is None:
            raise ValueError("forecast has no classified outcome")

        brier = 0.0
        realized_probability: float | None = None
        for item in forecast.probabilities:
            observed = 1.0 if item.scenario_id == outcome.realized_scenario_id else 0.0
            brier += (item.probability - observed) ** 2
            if observed == 1.0:
                realized_probability = item.probability
        assert realized_probability is not None
        log_loss = (
            math.inf
            if realized_probability == 0.0
            else -math.log(realized_probability)
        )
        return ForecastScore(
            forecast_id=forecast_id,
            scenario_count=len(forecast.probabilities),
            brier_score=brier,
            log_loss=log_loss,
        )

    def summary(
        self,
        *,
        forecaster: str,
        scenario_count: int,
        minimum_observations: int = 20,
    ) -> CalibrationSummary:
        if not forecaster.strip():
            raise ValueError("forecaster is required")
        if scenario_count < 2:
            raise ValueError("scenario_count must be at least 2")
        if minimum_observations < 2:
            raise ValueError("minimum_observations must be at least 2")

        rows = self._con.execute(
            """
            SELECT forecast_id
            FROM scenario_forecasts
            WHERE forecaster = ?
            ORDER BY forecast_at, forecast_id
            """,
            [forecaster.strip()],
        ).fetchall()

        scores: list[ForecastScore] = []
        for row in rows:
            forecast_id = str(row[0])
            forecast = self.get_forecast(forecast_id)
            assert forecast is not None
            if len(forecast.probabilities) != scenario_count:
                continue
            if self.outcome_for(forecast_id) is None:
                continue
            scores.append(self.score(forecast_id))

        if len(scores) < minimum_observations:
            return CalibrationSummary(
                forecaster=forecaster.strip(),
                scenario_count=scenario_count,
                observations=len(scores),
                mean_brier_score=None,
                mean_log_loss=None,
                state="INSUFFICIENT_EVIDENCE",
                caveat=self.SUMMARY_CAVEAT,
            )

        return CalibrationSummary(
            forecaster=forecaster.strip(),
            scenario_count=scenario_count,
            observations=len(scores),
            mean_brier_score=sum(item.brier_score for item in scores) / len(scores),
            mean_log_loss=sum(item.log_loss for item in scores) / len(scores),
            state="MEASURED",
            caveat=self.SUMMARY_CAVEAT,
        )

    def get_forecast(self, forecast_id: str) -> ForecastRecord | None:
        row = self._con.execute(
            """
            SELECT forecast_id, permit_id, case_id, case_dossier_fingerprint,
                   scenario_set_id, forecaster, forecast_at, horizon_end,
                   probabilities_json
            FROM scenario_forecasts
            WHERE forecast_id = ?
            """,
            [forecast_id],
        ).fetchone()
        if row is None:
            return None
        probabilities = tuple(
            ScenarioProbability(
                scenario_id=str(item["scenario_id"]),
                scenario_name=str(item["scenario_name"]),
                probability=float(item["probability"]),
            )
            for item in json.loads(str(row[8]))
        )
        return ForecastRecord(
            forecast_id=str(row[0]),
            permit_id=str(row[1]),
            case_id=str(row[2]),
            case_dossier_fingerprint=str(row[3]),
            scenario_set_id=str(row[4]),
            forecaster=str(row[5]),
            forecast_at=row[6],
            horizon_end=row[7],
            probabilities=probabilities,
        )

    def outcome_for(self, forecast_id: str) -> OutcomeRecord | None:
        row = self._con.execute(
            """
            SELECT outcome_id, forecast_id, realized_scenario_id, observed_at,
                   adjudicator, notes, evidence_references_json
            FROM scenario_outcomes
            WHERE forecast_id = ?
            """,
            [forecast_id],
        ).fetchone()
        if row is None:
            return None
        return OutcomeRecord(
            outcome_id=str(row[0]),
            forecast_id=str(row[1]),
            realized_scenario_id=str(row[2]),
            observed_at=row[3],
            adjudicator=str(row[4]),
            notes=str(row[5]),
            evidence_references=tuple(json.loads(str(row[6]))),
        )

    def close(self) -> None:
        self._con.close()
