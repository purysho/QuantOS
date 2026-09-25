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

from .portfolio_risk_cube import (
    PortfolioRiskCube,
    PortfolioRiskCubeState,
    portfolio_risk_cube_identity,
)


class HistoricalSimulationWeighting(str, Enum):
    EQUAL_WEIGHT = "EQUAL_WEIGHT"


class HistoricalQuantileConvention(str, Enum):
    NEAREST_RANK = "NEAREST_RANK"


@dataclass(frozen=True)
class HistoricalScenarioObservation:
    scenario_id: str
    period_start: datetime
    period_end: datetime
    source_fact_ids: tuple[str, ...]
    derivation_evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.scenario_id.strip():
            raise ValueError(
                "historical scenario observation requires scenario_id"
            )
        if (
            self.period_start.tzinfo is None
            or self.period_end.tzinfo is None
        ):
            raise ValueError(
                "historical observation timestamps must be timezone-aware"
            )
        if self.period_end <= self.period_start:
            raise ValueError(
                "historical observation period_end must follow period_start"
            )
        if not self.source_fact_ids or not all(
            item.strip() for item in self.source_fact_ids
        ):
            raise ValueError(
                "historical observation requires source fact IDs"
            )
        if len(self.source_fact_ids) != len(set(self.source_fact_ids)):
            raise ValueError(
                "historical observation source fact IDs must be unique"
            )
        if not self.derivation_evidence_references or not all(
            item.strip()
            for item in self.derivation_evidence_references
        ):
            raise ValueError(
                "historical observation requires derivation evidence"
            )

    @property
    def observation_id(self) -> str:
        return _content_id(
            "historical-scenario-observation",
            {
                "scenario_id": self.scenario_id.strip(),
                "period_start": self.period_start.isoformat(),
                "period_end": self.period_end.isoformat(),
                "source_fact_ids": sorted(self.source_fact_ids),
                "derivation_evidence_references": sorted(
                    self.derivation_evidence_references
                ),
            },
        )


@dataclass(frozen=True)
class HistoricalSimulationRiskPolicy:
    confidence_level: Decimal
    minimum_observations: int
    horizon_seconds: int
    window_start: datetime
    window_end: datetime
    weighting: HistoricalSimulationWeighting
    quantile_convention: HistoricalQuantileConvention
    missing_data_policy: str
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not self.confidence_level.is_finite()
            or self.confidence_level <= 0
            or self.confidence_level >= 1
        ):
            raise ValueError(
                "historical simulation confidence level must be between 0 and 1"
            )
        if self.minimum_observations < 20:
            raise ValueError(
                "historical simulation requires a structural minimum of 20 observations"
            )
        if self.horizon_seconds <= 0:
            raise ValueError(
                "historical simulation horizon_seconds must be positive"
            )
        if (
            self.window_start.tzinfo is None
            or self.window_end.tzinfo is None
        ):
            raise ValueError(
                "historical simulation window must be timezone-aware"
            )
        if self.window_end <= self.window_start:
            raise ValueError(
                "historical simulation window_end must follow window_start"
            )
        if self.weighting is not HistoricalSimulationWeighting.EQUAL_WEIGHT:
            raise ValueError(
                "Stage 11.8 supports EQUAL_WEIGHT historical simulation only"
            )
        if (
            self.quantile_convention
            is not HistoricalQuantileConvention.NEAREST_RANK
        ):
            raise ValueError(
                "Stage 11.8 supports NEAREST_RANK quantiles only"
            )
        if self.missing_data_policy != "FAIL_CLOSED":
            raise ValueError(
                "Stage 11.8 requires missing_data_policy=FAIL_CLOSED"
            )
        if not self.rationale.strip():
            raise ValueError(
                "historical simulation policy rationale is required"
            )
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError(
                "historical simulation policy requires evidence references"
            )

    @property
    def policy_id(self) -> str:
        return _content_id(
            "historical-simulation-risk-policy",
            {
                "confidence_level": str(self.confidence_level),
                "minimum_observations": self.minimum_observations,
                "horizon_seconds": self.horizon_seconds,
                "window_start": self.window_start.isoformat(),
                "window_end": self.window_end.isoformat(),
                "weighting": self.weighting.value,
                "quantile_convention": self.quantile_convention.value,
                "missing_data_policy": self.missing_data_policy,
                "rationale": self.rationale,
                "evidence_references": sorted(
                    self.evidence_references
                ),
            },
        )


@dataclass(frozen=True)
class HistoricalLossObservation:
    observation_id: str
    scenario_id: str
    period_start: datetime
    period_end: datetime
    portfolio_pnl: Decimal
    loss: Decimal


@dataclass(frozen=True)
class HistoricalSimulationRiskEstimate:
    estimate_id: str
    cube_id: str
    policy_id: str
    base_snapshot_id: str
    valuation_time: datetime
    reporting_currency: str
    observation_ids: tuple[str, ...]
    observation_count: int
    confidence_level: Decimal
    weighting: HistoricalSimulationWeighting
    quantile_convention: HistoricalQuantileConvention
    tail_count: int
    ordered_losses: tuple[HistoricalLossObservation, ...]
    value_at_risk_loss: Decimal
    expected_shortfall_loss: Decimal
    worst_loss: Decimal
    best_loss: Decimal
    mean_loss: Decimal
    value_at_risk_on_gross_base: Decimal | None
    expected_shortfall_on_gross_base: Decimal | None
    diagnostics: tuple[str, ...]
    var_authority: str
    order_authority: str
    capital_authority: str
    caveat: str


class HistoricalSimulationRiskEngine:
    CAVEAT = (
        "Historical-simulation VaR and expected shortfall are backward-looking "
        "empirical loss summaries over the frozen scenario history. They are not "
        "guarantees, probabilities of future loss, capital requirements, or "
        "trading authority."
    )

    def estimate(
        self,
        *,
        cube: PortfolioRiskCube,
        observations: tuple[HistoricalScenarioObservation, ...],
        policy: HistoricalSimulationRiskPolicy,
    ) -> HistoricalSimulationRiskEstimate:
        if cube.cube_id != portfolio_risk_cube_identity(cube):
            raise ValueError("portfolio risk cube identity mismatch")
        if cube.state is not PortfolioRiskCubeState.COMPLETE:
            raise ValueError(
                "historical simulation requires a COMPLETE portfolio risk cube"
            )
        if cube.valuation_time.tzinfo is None:
            raise ValueError(
                "portfolio risk cube valuation time must be timezone-aware"
            )
        if policy.window_end >= cube.valuation_time:
            raise ValueError(
                "historical simulation window must end before valuation time"
            )
        if len(observations) < policy.minimum_observations:
            raise ValueError(
                "historical simulation minimum observation count not reached"
            )

        scenario_ids = set(cube.scenario_ids)
        observation_by_scenario: dict[
            str,
            HistoricalScenarioObservation,
        ] = {}
        period_keys: set[tuple[datetime, datetime]] = set()
        for observation in observations:
            if observation.scenario_id not in scenario_ids:
                raise ValueError(
                    "historical observation references a scenario outside the risk cube"
                )
            if observation.scenario_id in observation_by_scenario:
                raise ValueError(
                    "historical simulation requires exactly one observation per scenario"
                )
            if (
                observation.period_start < policy.window_start
                or observation.period_end > policy.window_end
            ):
                raise ValueError(
                    "historical observation lies outside frozen policy window"
                )
            if observation.period_end >= cube.valuation_time:
                raise ValueError(
                    "historical observation must end before risk valuation time"
                )
            elapsed = (
                observation.period_end - observation.period_start
            ).total_seconds()
            if elapsed != policy.horizon_seconds:
                raise ValueError(
                    "historical observation horizon differs from frozen policy"
                )
            period_key = (
                observation.period_start,
                observation.period_end,
            )
            if period_key in period_keys:
                raise ValueError(
                    "historical simulation cannot duplicate observation periods"
                )
            period_keys.add(period_key)
            observation_by_scenario[observation.scenario_id] = observation

        if set(observation_by_scenario) != scenario_ids:
            missing = sorted(
                scenario_ids - set(observation_by_scenario)
            )
            raise ValueError(
                "historical simulation scenario coverage is incomplete: "
                f"missing={missing}"
            )
        if len(observations) != len(scenario_ids):
            raise ValueError(
                "historical simulation observation set must exactly match cube scenarios"
            )

        summary_by_scenario = {
            item.scenario_id: item
            for item in cube.scenario_summaries
        }
        if set(summary_by_scenario) != scenario_ids:
            raise ValueError(
                "risk cube scenario summaries do not match scenario IDs"
            )

        losses: list[HistoricalLossObservation] = []
        for scenario_id in sorted(scenario_ids):
            summary = summary_by_scenario[scenario_id]
            if not summary.complete or summary.portfolio_pnl is None:
                raise ValueError(
                    "historical simulation cannot consume incomplete scenario P&L"
                )
            observation = observation_by_scenario[scenario_id]
            loss = -summary.portfolio_pnl
            losses.append(
                HistoricalLossObservation(
                    observation_id=observation.observation_id,
                    scenario_id=scenario_id,
                    period_start=observation.period_start,
                    period_end=observation.period_end,
                    portfolio_pnl=summary.portfolio_pnl,
                    loss=loss,
                )
            )

        ordered = tuple(
            sorted(
                losses,
                key=lambda item: (
                    item.loss,
                    item.period_end,
                    item.observation_id,
                ),
            )
        )
        n = len(ordered)
        rank = math.ceil(float(policy.confidence_level) * n)
        rank = min(max(rank, 1), n)
        var_loss = ordered[rank - 1].loss
        tail_count = max(
            1,
            math.ceil(
                float(
                    (Decimal("1") - policy.confidence_level)
                    * Decimal(n)
                )
            ),
        )
        worst_tail = tuple(
            item.loss
            for item in sorted(
                ordered,
                key=lambda item: (
                    item.loss,
                    item.period_end,
                    item.observation_id,
                ),
                reverse=True,
            )[:tail_count]
        )
        expected_shortfall = (
            sum(worst_tail, Decimal("0"))
            / Decimal(len(worst_tail))
        )
        mean_loss = (
            sum((item.loss for item in ordered), Decimal("0"))
            / Decimal(n)
        )
        worst_loss = ordered[-1].loss
        best_loss = ordered[0].loss

        var_on_gross = None
        es_on_gross = None
        if (
            cube.gross_base_value is not None
            and cube.gross_base_value > 0
        ):
            var_on_gross = var_loss / cube.gross_base_value
            es_on_gross = (
                expected_shortfall / cube.gross_base_value
            )

        diagnostics = (
            "loss is defined as negative portfolio scenario P&L",
            "nearest-rank empirical VaR; no interpolation",
            "expected shortfall is mean of worst ceil((1-alpha)*N) observed losses",
            "equal observation weights only",
            "negative VaR is preserved when the empirical quantile is a gain",
            "historical observation periods are exact, unique, pre-valuation, and policy-window bound",
            "no volatility scaling, decay weighting, distribution fitting, or scenario probability inference",
        )
        payload = {
            "cube_id": cube.cube_id,
            "policy_id": policy.policy_id,
            "base_snapshot_id": cube.base_snapshot_id,
            "valuation_time": cube.valuation_time.isoformat(),
            "reporting_currency": cube.reporting_currency,
            "observation_ids": [
                item.observation_id for item in ordered
            ],
            "observation_count": n,
            "confidence_level": str(policy.confidence_level),
            "weighting": policy.weighting.value,
            "quantile_convention": (
                policy.quantile_convention.value
            ),
            "tail_count": tail_count,
            "ordered_losses": [
                _loss_payload(item) for item in ordered
            ],
            "value_at_risk_loss": str(var_loss),
            "expected_shortfall_loss": str(
                expected_shortfall
            ),
            "worst_loss": str(worst_loss),
            "best_loss": str(best_loss),
            "mean_loss": str(mean_loss),
            "value_at_risk_on_gross_base": _optional_str(
                var_on_gross
            ),
            "expected_shortfall_on_gross_base": _optional_str(
                es_on_gross
            ),
            "diagnostics": list(diagnostics),
            "var_authority": "RESEARCH_ONLY",
            "order_authority": "NONE",
            "capital_authority": "NONE",
        }
        return HistoricalSimulationRiskEstimate(
            estimate_id=_content_id(
                "historical-simulation-risk-estimate",
                payload,
            ),
            cube_id=cube.cube_id,
            policy_id=policy.policy_id,
            base_snapshot_id=cube.base_snapshot_id,
            valuation_time=cube.valuation_time,
            reporting_currency=cube.reporting_currency,
            observation_ids=tuple(
                item.observation_id for item in ordered
            ),
            observation_count=n,
            confidence_level=policy.confidence_level,
            weighting=policy.weighting,
            quantile_convention=policy.quantile_convention,
            tail_count=tail_count,
            ordered_losses=ordered,
            value_at_risk_loss=var_loss,
            expected_shortfall_loss=expected_shortfall,
            worst_loss=worst_loss,
            best_loss=best_loss,
            mean_loss=mean_loss,
            value_at_risk_on_gross_base=var_on_gross,
            expected_shortfall_on_gross_base=es_on_gross,
            diagnostics=diagnostics,
            var_authority="RESEARCH_ONLY",
            order_authority="NONE",
            capital_authority="NONE",
            caveat=self.CAVEAT,
        )


class HistoricalSimulationRiskStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS historical_simulation_risk (
                estimate_id VARCHAR PRIMARY KEY,
                cube_id VARCHAR NOT NULL,
                policy_id VARCHAR NOT NULL,
                valuation_time TIMESTAMPTZ NOT NULL,
                confidence_level VARCHAR NOT NULL,
                observation_count INTEGER NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(
        self,
        estimate: HistoricalSimulationRiskEstimate,
    ) -> bool:
        if (
            estimate.estimate_id
            != historical_simulation_risk_estimate_identity(
                estimate
            )
        ):
            raise ValueError(
                "historical simulation estimate content does not match estimate_id"
            )
        payload = json.dumps(
            historical_simulation_risk_estimate_payload(
                estimate
            ),
            sort_keys=True,
            separators=(",", ":"),
        )
        row = self._con.execute(
            """
            SELECT payload_json
            FROM historical_simulation_risk
            WHERE estimate_id = ?
            """,
            [estimate.estimate_id],
        ).fetchone()
        if row is not None:
            if str(row[0]) != payload:
                raise ValueError(
                    "historical simulation risk identity conflict"
                )
            return False
        self._con.execute(
            """
            INSERT INTO historical_simulation_risk
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                estimate.estimate_id,
                estimate.cube_id,
                estimate.policy_id,
                estimate.valuation_time,
                str(estimate.confidence_level),
                estimate.observation_count,
                payload,
            ],
        )
        return True

    def close(self) -> None:
        self._con.close()


def historical_simulation_risk_estimate_identity(
    estimate: HistoricalSimulationRiskEstimate,
) -> str:
    return _content_id(
        "historical-simulation-risk-estimate",
        {
            key: value
            for key, value in historical_simulation_risk_estimate_payload(
                estimate
            ).items()
            if key not in {"estimate_id", "caveat"}
        },
    )


def historical_simulation_risk_estimate_payload(
    estimate: HistoricalSimulationRiskEstimate,
) -> dict[str, object]:
    return {
        "estimate_id": estimate.estimate_id,
        "cube_id": estimate.cube_id,
        "policy_id": estimate.policy_id,
        "base_snapshot_id": estimate.base_snapshot_id,
        "valuation_time": estimate.valuation_time.isoformat(),
        "reporting_currency": estimate.reporting_currency,
        "observation_ids": list(estimate.observation_ids),
        "observation_count": estimate.observation_count,
        "confidence_level": str(estimate.confidence_level),
        "weighting": estimate.weighting.value,
        "quantile_convention": (
            estimate.quantile_convention.value
        ),
        "tail_count": estimate.tail_count,
        "ordered_losses": [
            _loss_payload(item)
            for item in estimate.ordered_losses
        ],
        "value_at_risk_loss": str(
            estimate.value_at_risk_loss
        ),
        "expected_shortfall_loss": str(
            estimate.expected_shortfall_loss
        ),
        "worst_loss": str(estimate.worst_loss),
        "best_loss": str(estimate.best_loss),
        "mean_loss": str(estimate.mean_loss),
        "value_at_risk_on_gross_base": _optional_str(
            estimate.value_at_risk_on_gross_base
        ),
        "expected_shortfall_on_gross_base": _optional_str(
            estimate.expected_shortfall_on_gross_base
        ),
        "diagnostics": list(estimate.diagnostics),
        "var_authority": estimate.var_authority,
        "order_authority": estimate.order_authority,
        "capital_authority": estimate.capital_authority,
        "caveat": estimate.caveat,
    }


def _loss_payload(
    item: HistoricalLossObservation,
) -> dict[str, object]:
    return {
        "observation_id": item.observation_id,
        "scenario_id": item.scenario_id,
        "period_start": item.period_start.isoformat(),
        "period_end": item.period_end.isoformat(),
        "portfolio_pnl": str(item.portfolio_pnl),
        "loss": str(item.loss),
    }


def _optional_str(
    value: Decimal | None,
) -> str | None:
    return str(value) if value is not None else None


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
