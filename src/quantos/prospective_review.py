from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

import duckdb

from .backtest_economics import BenchmarkKind
from .calibration import ForecastRecord, OutcomeRecord
from .model_registry import (
    ModelLifecycleStage,
    ResearchRunManifest,
)
from .paper_monitoring import (
    PaperPortfolioObservation,
    PaperPostmortem,
)
from .readiness import ProspectiveShadowPermit
from .research_case import ResearchCase


class ExpectationComparisonState(str, Enum):
    WITHIN_FROZEN_EXPECTATIONS = "WITHIN_FROZEN_EXPECTATIONS"
    DEVIATION_PRESENT = "DEVIATION_PRESENT"


class ForecastCalibrationState(str, Enum):
    SCORED = "SCORED"
    ZERO_PROBABILITY_REALIZED = "ZERO_PROBABILITY_REALIZED"


@dataclass(frozen=True)
class ProspectiveBehaviorExpectation:
    expectation_id: str
    model_id: str
    manifest_id: str
    permit_id: str
    case_id: str
    case_dossier_fingerprint: str
    forecast_id: str
    frozen_at: datetime
    horizon_end: datetime
    cumulative_net_return_lower: Decimal
    cumulative_net_return_upper: Decimal
    market_relative_return_lower: Decimal
    market_relative_return_upper: Decimal
    maximum_average_implementation_cost_rate: Decimal
    maximum_average_one_way_turnover: Decimal
    rationale: str
    evidence_references: tuple[str, ...]


@dataclass(frozen=True)
class ForecastCalibrationComparison:
    forecast_id: str
    outcome_id: str
    realized_scenario_id: str
    realized_probability: Decimal
    brier_score: Decimal
    log_loss: Decimal | None
    state: ForecastCalibrationState


@dataclass(frozen=True)
class ProspectiveComparison:
    comparison_id: str
    model_id: str
    manifest_id: str
    expectation_id: str
    postmortem_id: str
    reviewed_at: datetime
    reviewer: str
    state: ExpectationComparisonState
    calibration: ForecastCalibrationComparison
    observations: int
    realized_cumulative_net_return: Decimal
    realized_market_relative_wealth_return: Decimal
    realized_average_implementation_cost_rate: Decimal
    realized_average_one_way_turnover: Decimal
    breached_conditions: tuple[str, ...]
    deviations: tuple[str, ...]
    lessons: tuple[str, ...]
    evidence_references: tuple[str, ...]
    caveat: str


@dataclass(frozen=True)
class ResearchRevisionSeed:
    seed_id: str
    prior_case_id: str
    source_model_id: str
    source_manifest_id: str
    source_comparison_id: str
    created_at: datetime
    author: str
    lessons: tuple[str, ...]
    evidence_references: tuple[str, ...]
    instruction: str


class ProspectiveReviewEngine:
    CAVEAT = (
        "Prospective comparison describes calibration and shadow-paper behavior for "
        "one frozen research configuration. It does not authorize capital or predict "
        "future profitability."
    )
    REVISION_INSTRUCTION = (
        "Create a new ResearchCase with supersedes_case_id equal to prior_case_id. "
        "Do not mutate the prior ResearchCase or Research Run Manifest."
    )

    def freeze_expectation(
        self,
        *,
        manifest: ResearchRunManifest,
        permit: ProspectiveShadowPermit,
        forecast: ForecastRecord,
        cumulative_net_return_lower: Decimal,
        cumulative_net_return_upper: Decimal,
        market_relative_return_lower: Decimal,
        market_relative_return_upper: Decimal,
        maximum_average_implementation_cost_rate: Decimal,
        maximum_average_one_way_turnover: Decimal,
        rationale: str,
        evidence_references: tuple[str, ...],
    ) -> ProspectiveBehaviorExpectation:
        if manifest.eligible_stage is not ModelLifecycleStage.PAPER:
            raise ValueError("prospective expectation requires PAPER-eligible manifest")
        if manifest.shadow_permit_id != permit.permit_id:
            raise ValueError("manifest is bound to another shadow permit")
        if forecast.permit_id != permit.permit_id:
            raise ValueError("forecast is bound to another shadow permit")
        if forecast.case_id != manifest.research_case_id:
            raise ValueError("forecast belongs to another Research Case")
        if (
            forecast.case_dossier_fingerprint
            != manifest.case_dossier_fingerprint
        ):
            raise ValueError("forecast and manifest dossier fingerprints differ")
        if forecast.forecast_at < permit.issued_at:
            raise ValueError("forecast predates shadow permit")
        for low_name, low, high_name, high in (
            (
                "cumulative_net_return_lower",
                cumulative_net_return_lower,
                "cumulative_net_return_upper",
                cumulative_net_return_upper,
            ),
            (
                "market_relative_return_lower",
                market_relative_return_lower,
                "market_relative_return_upper",
                market_relative_return_upper,
            ),
        ):
            if not low.is_finite() or not high.is_finite():
                raise ValueError(f"{low_name}/{high_name} must be finite")
            if low > high:
                raise ValueError(f"{low_name} cannot exceed {high_name}")
            if low <= Decimal("-1"):
                raise ValueError(f"{low_name} must be greater than -1")
        for name, value in (
            (
                "maximum_average_implementation_cost_rate",
                maximum_average_implementation_cost_rate,
            ),
            (
                "maximum_average_one_way_turnover",
                maximum_average_one_way_turnover,
            ),
        ):
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if not rationale.strip():
            raise ValueError("expectation rationale is required")
        if not evidence_references or not all(
            item.strip() for item in evidence_references
        ):
            raise ValueError("expectation requires evidence references")

        payload = {
            "model_id": manifest.model_id,
            "manifest_id": manifest.manifest_id,
            "permit_id": permit.permit_id,
            "case_id": manifest.research_case_id,
            "case_dossier_fingerprint": manifest.case_dossier_fingerprint,
            "forecast_id": forecast.forecast_id,
            "frozen_at": forecast.forecast_at.isoformat(),
            "horizon_end": forecast.horizon_end.isoformat(),
            "cumulative_net_return_lower": str(cumulative_net_return_lower),
            "cumulative_net_return_upper": str(cumulative_net_return_upper),
            "market_relative_return_lower": str(market_relative_return_lower),
            "market_relative_return_upper": str(market_relative_return_upper),
            "maximum_average_implementation_cost_rate": str(
                maximum_average_implementation_cost_rate
            ),
            "maximum_average_one_way_turnover": str(
                maximum_average_one_way_turnover
            ),
            "rationale": rationale.strip(),
            "evidence_references": list(evidence_references),
        }
        return ProspectiveBehaviorExpectation(
            expectation_id=_content_id("prospective-expectation", payload),
            model_id=manifest.model_id,
            manifest_id=manifest.manifest_id,
            permit_id=permit.permit_id,
            case_id=manifest.research_case_id,
            case_dossier_fingerprint=manifest.case_dossier_fingerprint or "",
            forecast_id=forecast.forecast_id,
            frozen_at=forecast.forecast_at,
            horizon_end=forecast.horizon_end,
            cumulative_net_return_lower=cumulative_net_return_lower,
            cumulative_net_return_upper=cumulative_net_return_upper,
            market_relative_return_lower=market_relative_return_lower,
            market_relative_return_upper=market_relative_return_upper,
            maximum_average_implementation_cost_rate=(
                maximum_average_implementation_cost_rate
            ),
            maximum_average_one_way_turnover=(
                maximum_average_one_way_turnover
            ),
            rationale=rationale.strip(),
            evidence_references=evidence_references,
        )

    def compare(
        self,
        *,
        manifest: ResearchRunManifest,
        expectation: ProspectiveBehaviorExpectation,
        forecast: ForecastRecord,
        outcome: OutcomeRecord,
        observations: tuple[PaperPortfolioObservation, ...],
        postmortem: PaperPostmortem,
        reviewed_at: datetime,
        reviewer: str,
        lessons: tuple[str, ...],
        evidence_references: tuple[str, ...],
    ) -> ProspectiveComparison:
        if reviewed_at.tzinfo is None:
            raise ValueError("reviewed_at must be timezone-aware")
        if not reviewer.strip():
            raise ValueError("prospective comparison reviewer is required")
        if not lessons or not all(item.strip() for item in lessons):
            raise ValueError("prospective comparison requires explicit lessons")
        if not evidence_references or not all(
            item.strip() for item in evidence_references
        ):
            raise ValueError("prospective comparison requires evidence references")
        if expectation.manifest_id != manifest.manifest_id:
            raise ValueError("expectation belongs to another manifest")
        if expectation.model_id != manifest.model_id:
            raise ValueError("expectation belongs to another model")
        if expectation.forecast_id != forecast.forecast_id:
            raise ValueError("expectation belongs to another forecast")
        if outcome.forecast_id != forecast.forecast_id:
            raise ValueError("outcome belongs to another forecast")
        if postmortem.manifest_id != manifest.manifest_id:
            raise ValueError("postmortem belongs to another manifest")
        if postmortem.model_id != manifest.model_id:
            raise ValueError("postmortem belongs to another model")
        if reviewed_at < outcome.observed_at:
            raise ValueError("comparison cannot predate forecast outcome")
        if reviewed_at < postmortem.closed_at:
            raise ValueError("comparison cannot predate PAPER postmortem")
        if not observations:
            raise ValueError("comparison requires prospective PAPER observations")
        if any(item.manifest_id != manifest.manifest_id for item in observations):
            raise ValueError("observation belongs to another manifest")
        ordered = tuple(sorted(observations, key=lambda item: item.period_start))
        if ordered != observations:
            raise ValueError("PAPER observations must be supplied chronologically")
        if observations[0].period_start < expectation.frozen_at:
            raise ValueError("prospective observations cannot predate expectation")
        if observations[-1].period_end > expectation.horizon_end:
            raise ValueError("prospective observations exceed frozen horizon")

        realized_scenario_ids = {
            item.scenario_id for item in forecast.probabilities
        }
        if outcome.realized_scenario_id not in realized_scenario_ids:
            raise ValueError("outcome scenario absent from frozen forecast")
        calibration = _score_forecast(forecast, outcome)

        wealth = Decimal("1")
        market_wealth = Decimal("1")
        total_cost = Decimal("0")
        total_turnover = Decimal("0")
        breached_conditions: set[str] = set()
        for item in observations:
            wealth *= Decimal("1") + item.net_return
            market_return = next(
                benchmark.total_return
                for benchmark in item.benchmarks
                if benchmark.kind is BenchmarkKind.MARKET_CAP
            )
            market_wealth *= Decimal("1") + market_return
            total_cost += (
                item.transaction_cost_rate + item.borrow_cost_rate
            )
            total_turnover += item.one_way_turnover_ratio
            breached_conditions.update(
                check.condition
                for check in item.condition_checks
                if check.breached
            )
        realized_net = wealth - Decimal("1")
        realized_relative = wealth / market_wealth - Decimal("1")
        count = Decimal(len(observations))
        average_cost = total_cost / count
        average_turnover = total_turnover / count

        deviations: list[str] = []
        if not (
            expectation.cumulative_net_return_lower
            <= realized_net
            <= expectation.cumulative_net_return_upper
        ):
            deviations.append("cumulative net return outside frozen range")
        if not (
            expectation.market_relative_return_lower
            <= realized_relative
            <= expectation.market_relative_return_upper
        ):
            deviations.append("market-relative wealth return outside frozen range")
        if (
            average_cost
            > expectation.maximum_average_implementation_cost_rate
        ):
            deviations.append("average implementation cost above frozen maximum")
        if average_turnover > expectation.maximum_average_one_way_turnover:
            deviations.append("average one-way turnover above frozen maximum")
        if breached_conditions:
            deviations.append("Research Case monitoring condition breach observed")

        state = (
            ExpectationComparisonState.DEVIATION_PRESENT
            if deviations
            else ExpectationComparisonState.WITHIN_FROZEN_EXPECTATIONS
        )
        payload = {
            "model_id": manifest.model_id,
            "manifest_id": manifest.manifest_id,
            "expectation_id": expectation.expectation_id,
            "postmortem_id": postmortem.postmortem_id,
            "reviewed_at": reviewed_at.isoformat(),
            "reviewer": reviewer.strip(),
            "state": state.value,
            "calibration": {
                "forecast_id": calibration.forecast_id,
                "outcome_id": calibration.outcome_id,
                "realized_scenario_id": calibration.realized_scenario_id,
                "realized_probability": str(calibration.realized_probability),
                "brier_score": str(calibration.brier_score),
                "log_loss": (
                    str(calibration.log_loss)
                    if calibration.log_loss is not None
                    else None
                ),
                "state": calibration.state.value,
            },
            "observation_ids": [item.observation_id for item in observations],
            "realized_cumulative_net_return": str(realized_net),
            "realized_market_relative_wealth_return": str(realized_relative),
            "realized_average_implementation_cost_rate": str(average_cost),
            "realized_average_one_way_turnover": str(average_turnover),
            "breached_conditions": sorted(breached_conditions),
            "deviations": deviations,
            "lessons": list(lessons),
            "evidence_references": list(evidence_references),
        }
        return ProspectiveComparison(
            comparison_id=_content_id("prospective-comparison", payload),
            model_id=manifest.model_id,
            manifest_id=manifest.manifest_id,
            expectation_id=expectation.expectation_id,
            postmortem_id=postmortem.postmortem_id,
            reviewed_at=reviewed_at,
            reviewer=reviewer.strip(),
            state=state,
            calibration=calibration,
            observations=len(observations),
            realized_cumulative_net_return=realized_net,
            realized_market_relative_wealth_return=realized_relative,
            realized_average_implementation_cost_rate=average_cost,
            realized_average_one_way_turnover=average_turnover,
            breached_conditions=tuple(sorted(breached_conditions)),
            deviations=tuple(deviations),
            lessons=lessons,
            evidence_references=evidence_references,
            caveat=self.CAVEAT,
        )

    def make_revision_seed(
        self,
        *,
        prior_case: ResearchCase,
        comparison: ProspectiveComparison,
        created_at: datetime,
        author: str,
        evidence_references: tuple[str, ...],
    ) -> ResearchRevisionSeed:
        if created_at.tzinfo is None:
            raise ValueError("revision seed created_at must be timezone-aware")
        if created_at < comparison.reviewed_at:
            raise ValueError("revision seed cannot predate prospective comparison")
        if not author.strip():
            raise ValueError("revision seed author is required")
        if prior_case.case_id != comparison.model_id and False:
            raise AssertionError("unreachable")
        if not evidence_references or not all(
            item.strip() for item in evidence_references
        ):
            raise ValueError("revision seed requires evidence references")
        payload = {
            "prior_case_id": prior_case.case_id,
            "source_model_id": comparison.model_id,
            "source_manifest_id": comparison.manifest_id,
            "source_comparison_id": comparison.comparison_id,
            "created_at": created_at.isoformat(),
            "author": author.strip(),
            "lessons": list(comparison.lessons),
            "evidence_references": list(evidence_references),
            "instruction": self.REVISION_INSTRUCTION,
        }
        return ResearchRevisionSeed(
            seed_id=_content_id("research-revision-seed", payload),
            prior_case_id=prior_case.case_id,
            source_model_id=comparison.model_id,
            source_manifest_id=comparison.manifest_id,
            source_comparison_id=comparison.comparison_id,
            created_at=created_at,
            author=author.strip(),
            lessons=comparison.lessons,
            evidence_references=evidence_references,
            instruction=self.REVISION_INSTRUCTION,
        )

    @staticmethod
    def validate_case_revision(
        *,
        seed: ResearchRevisionSeed,
        prior_case: ResearchCase,
        revised_case: ResearchCase,
    ) -> None:
        if seed.prior_case_id != prior_case.case_id:
            raise ValueError("revision seed belongs to another prior Research Case")
        if revised_case.supersedes_case_id != prior_case.case_id:
            raise ValueError(
                "new Research Case must explicitly supersede the prior case"
            )
        if revised_case.case_id == prior_case.case_id:
            raise ValueError("Research Case revision must have a new immutable ID")
        if revised_case.case_type is not prior_case.case_type:
            raise ValueError("Research Case revision cannot silently change case type")
        if tuple(sorted(revised_case.subject_ids)) != tuple(
            sorted(prior_case.subject_ids)
        ):
            raise ValueError("Research Case revision cannot silently change subjects")
        if revised_case.created_at < seed.created_at:
            raise ValueError("Research Case revision cannot predate revision seed")


class ProspectiveReviewStore:
    """Immutable storage for expectations, comparisons, and revision seeds."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS prospective_review_artifacts (
                artifact_id VARCHAR PRIMARY KEY,
                artifact_kind VARCHAR NOT NULL,
                model_id VARCHAR NOT NULL,
                manifest_id VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add_expectation(self, item: ProspectiveBehaviorExpectation) -> bool:
        return self._add(
            artifact_id=item.expectation_id,
            artifact_kind="EXPECTATION",
            model_id=item.model_id,
            manifest_id=item.manifest_id,
            payload=_jsonable(item),
        )

    def add_comparison(self, item: ProspectiveComparison) -> bool:
        return self._add(
            artifact_id=item.comparison_id,
            artifact_kind="COMPARISON",
            model_id=item.model_id,
            manifest_id=item.manifest_id,
            payload=_jsonable(item),
        )

    def add_revision_seed(self, item: ResearchRevisionSeed) -> bool:
        return self._add(
            artifact_id=item.seed_id,
            artifact_kind="REVISION_SEED",
            model_id=item.source_model_id,
            manifest_id=item.source_manifest_id,
            payload=_jsonable(item),
        )

    def _add(
        self,
        *,
        artifact_id: str,
        artifact_kind: str,
        model_id: str,
        manifest_id: str,
        payload: dict[str, object],
    ) -> bool:
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        row = self._con.execute(
            """
            SELECT artifact_kind, model_id, manifest_id, payload_json
            FROM prospective_review_artifacts
            WHERE artifact_id = ?
            """,
            [artifact_id],
        ).fetchone()
        if row is not None:
            existing = (
                str(row[0]),
                str(row[1]),
                str(row[2]),
                str(row[3]),
            )
            requested = (
                artifact_kind,
                model_id,
                manifest_id,
                serialized,
            )
            if existing != requested:
                raise ValueError("prospective review artifact identity conflict")
            return False
        self._con.execute(
            """
            INSERT INTO prospective_review_artifacts
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                artifact_id,
                artifact_kind,
                model_id,
                manifest_id,
                serialized,
            ],
        )
        return True

    def close(self) -> None:
        self._con.close()


def _score_forecast(
    forecast: ForecastRecord,
    outcome: OutcomeRecord,
) -> ForecastCalibrationComparison:
    realized_probability: Decimal | None = None
    brier = Decimal("0")
    for item in forecast.probabilities:
        probability = Decimal(str(item.probability))
        realized = (
            Decimal("1")
            if item.scenario_id == outcome.realized_scenario_id
            else Decimal("0")
        )
        brier += (probability - realized) ** 2
        if realized == 1:
            realized_probability = probability
    if realized_probability is None:
        raise ValueError("realized scenario absent from forecast")
    if realized_probability == 0:
        state = ForecastCalibrationState.ZERO_PROBABILITY_REALIZED
        log_loss = None
    else:
        state = ForecastCalibrationState.SCORED
        log_loss = Decimal(
            str(-math.log(float(realized_probability)))
        )
    return ForecastCalibrationComparison(
        forecast_id=forecast.forecast_id,
        outcome_id=outcome.outcome_id,
        realized_scenario_id=outcome.realized_scenario_id,
        realized_probability=realized_probability,
        brier_score=brier,
        log_loss=log_loss,
        state=state,
    )


def _jsonable(item) -> dict[str, object]:
    output: dict[str, object] = {}
    for key, value in item.__dict__.items():
        if isinstance(value, Enum):
            output[key] = value.value
        elif isinstance(value, Decimal):
            output[key] = str(value)
        elif isinstance(value, datetime):
            output[key] = value.isoformat()
        elif isinstance(value, tuple):
            converted = []
            for member in value:
                if isinstance(member, Enum):
                    converted.append(member.value)
                elif isinstance(member, Decimal):
                    converted.append(str(member))
                elif hasattr(member, "__dict__"):
                    converted.append(_jsonable(member))
                else:
                    converted.append(member)
            output[key] = converted
        elif hasattr(value, "__dict__"):
            output[key] = _jsonable(value)
        else:
            output[key] = value
    return output


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
