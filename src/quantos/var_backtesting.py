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

from .historical_simulation_risk import (
    HistoricalSimulationRiskEstimate,
    HistoricalSimulationRiskPolicy,
    historical_simulation_risk_estimate_identity,
)


@dataclass(frozen=True)
class ProspectivePortfolioOutcome:
    estimate_id: str
    cube_id: str
    period_start: datetime
    period_end: datetime
    recorded_at: datetime
    realized_pnl: Decimal
    source_fact_ids: tuple[str, ...]
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.estimate_id.strip() or not self.cube_id.strip():
            raise ValueError(
                "prospective outcome requires estimate_id and cube_id"
            )
        for name in (
            "period_start",
            "period_end",
            "recorded_at",
        ):
            if getattr(self, name).tzinfo is None:
                raise ValueError(
                    "prospective outcome timestamps must be timezone-aware"
                )
        if self.period_end <= self.period_start:
            raise ValueError(
                "prospective outcome period_end must follow period_start"
            )
        if self.recorded_at < self.period_end:
            raise ValueError(
                "prospective outcome cannot be recorded before period end"
            )
        if not self.realized_pnl.is_finite():
            raise ValueError(
                "prospective outcome realized_pnl must be finite"
            )
        if not self.source_fact_ids or not all(
            item.strip() for item in self.source_fact_ids
        ):
            raise ValueError(
                "prospective outcome requires source fact IDs"
            )
        if len(self.source_fact_ids) != len(set(self.source_fact_ids)):
            raise ValueError(
                "prospective outcome source fact IDs must be unique"
            )
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError(
                "prospective outcome requires evidence references"
            )

    @property
    def outcome_id(self) -> str:
        return _content_id(
            "prospective-portfolio-outcome",
            {
                "estimate_id": self.estimate_id.strip(),
                "cube_id": self.cube_id.strip(),
                "period_start": self.period_start.isoformat(),
                "period_end": self.period_end.isoformat(),
                "recorded_at": self.recorded_at.isoformat(),
                "realized_pnl": str(self.realized_pnl),
                "source_fact_ids": sorted(self.source_fact_ids),
                "evidence_references": sorted(
                    self.evidence_references
                ),
            },
        )


@dataclass(frozen=True)
class VaRBacktestObservation:
    observation_id: str
    estimate_id: str
    risk_policy_id: str
    cube_id: str
    outcome_id: str
    forecast_time: datetime
    period_start: datetime
    period_end: datetime
    confidence_level: Decimal
    horizon_seconds: int
    value_at_risk_loss: Decimal
    expected_shortfall_loss: Decimal
    realized_pnl: Decimal
    realized_loss: Decimal
    var_exception: bool
    var_exception_magnitude: Decimal
    loss_beyond_expected_shortfall: bool
    expected_shortfall_excess_magnitude: Decimal
    source_fact_ids: tuple[str, ...]
    diagnostics: tuple[str, ...]
    approval_authority: str
    order_authority: str
    capital_authority: str


class VaRBacktestObservationEngine:
    def observe(
        self,
        *,
        estimate: HistoricalSimulationRiskEstimate,
        risk_policy: HistoricalSimulationRiskPolicy,
        outcome: ProspectivePortfolioOutcome,
    ) -> VaRBacktestObservation:
        if (
            estimate.estimate_id
            != historical_simulation_risk_estimate_identity(
                estimate
            )
        ):
            raise ValueError(
                "historical simulation estimate identity mismatch"
            )
        if estimate.policy_id != risk_policy.policy_id:
            raise ValueError(
                "risk estimate belongs to another historical-simulation policy"
            )
        if outcome.estimate_id != estimate.estimate_id:
            raise ValueError(
                "prospective outcome belongs to another risk estimate"
            )
        if outcome.cube_id != estimate.cube_id:
            raise ValueError(
                "prospective outcome belongs to another portfolio risk cube"
            )
        if outcome.period_start != estimate.valuation_time:
            raise ValueError(
                "prospective backtest period must start at the frozen risk valuation time"
            )
        elapsed = (
            outcome.period_end - outcome.period_start
        ).total_seconds()
        if elapsed != risk_policy.horizon_seconds:
            raise ValueError(
                "prospective outcome horizon differs from frozen risk policy"
            )
        if outcome.recorded_at <= estimate.valuation_time:
            raise ValueError(
                "prospective outcome must be recorded after the risk forecast"
            )
        if (
            estimate.var_authority != "RESEARCH_ONLY"
            or estimate.order_authority != "NONE"
            or estimate.capital_authority != "NONE"
        ):
            raise ValueError(
                "risk estimate unexpectedly carries operational authority"
            )

        realized_loss = -outcome.realized_pnl
        var_exception = (
            realized_loss > estimate.value_at_risk_loss
        )
        var_magnitude = max(
            Decimal("0"),
            realized_loss - estimate.value_at_risk_loss,
        )
        es_breach = (
            realized_loss > estimate.expected_shortfall_loss
        )
        es_magnitude = max(
            Decimal("0"),
            realized_loss
            - estimate.expected_shortfall_loss,
        )
        diagnostics = (
            "exception occurs only when realized loss is strictly greater than frozen VaR loss",
            "realized loss equals negative realized portfolio P&L",
            "VaR equality is not counted as an exception",
            "expected-shortfall exceedance is descriptive only; no ES calibration claim is inferred",
        )
        payload = {
            "estimate_id": estimate.estimate_id,
            "risk_policy_id": risk_policy.policy_id,
            "cube_id": estimate.cube_id,
            "outcome_id": outcome.outcome_id,
            "forecast_time": estimate.valuation_time.isoformat(),
            "period_start": outcome.period_start.isoformat(),
            "period_end": outcome.period_end.isoformat(),
            "confidence_level": str(
                estimate.confidence_level
            ),
            "horizon_seconds": risk_policy.horizon_seconds,
            "value_at_risk_loss": str(
                estimate.value_at_risk_loss
            ),
            "expected_shortfall_loss": str(
                estimate.expected_shortfall_loss
            ),
            "realized_pnl": str(outcome.realized_pnl),
            "realized_loss": str(realized_loss),
            "var_exception": var_exception,
            "var_exception_magnitude": str(var_magnitude),
            "loss_beyond_expected_shortfall": es_breach,
            "expected_shortfall_excess_magnitude": str(
                es_magnitude
            ),
            "source_fact_ids": sorted(
                outcome.source_fact_ids
            ),
            "diagnostics": list(diagnostics),
            "approval_authority": "NONE",
            "order_authority": "NONE",
            "capital_authority": "NONE",
        }
        return VaRBacktestObservation(
            observation_id=_content_id(
                "var-backtest-observation",
                payload,
            ),
            estimate_id=estimate.estimate_id,
            risk_policy_id=risk_policy.policy_id,
            cube_id=estimate.cube_id,
            outcome_id=outcome.outcome_id,
            forecast_time=estimate.valuation_time,
            period_start=outcome.period_start,
            period_end=outcome.period_end,
            confidence_level=estimate.confidence_level,
            horizon_seconds=risk_policy.horizon_seconds,
            value_at_risk_loss=estimate.value_at_risk_loss,
            expected_shortfall_loss=(
                estimate.expected_shortfall_loss
            ),
            realized_pnl=outcome.realized_pnl,
            realized_loss=realized_loss,
            var_exception=var_exception,
            var_exception_magnitude=var_magnitude,
            loss_beyond_expected_shortfall=es_breach,
            expected_shortfall_excess_magnitude=es_magnitude,
            source_fact_ids=tuple(
                sorted(outcome.source_fact_ids)
            ),
            diagnostics=diagnostics,
            approval_authority="NONE",
            order_authority="NONE",
            capital_authority="NONE",
        )


class VaRCalibrationState(str, Enum):
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    WITHIN_TEST_TOLERANCE = "WITHIN_TEST_TOLERANCE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


@dataclass(frozen=True)
class VaRCalibrationPolicy:
    minimum_observations: int
    kupiec_significance_level: Decimal
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.minimum_observations < 20:
            raise ValueError(
                "VaR calibration requires a structural minimum of 20 observations"
            )
        if (
            not self.kupiec_significance_level.is_finite()
            or self.kupiec_significance_level <= 0
            or self.kupiec_significance_level >= 1
        ):
            raise ValueError(
                "Kupiec significance level must be between 0 and 1"
            )
        if not self.rationale.strip():
            raise ValueError(
                "VaR calibration policy rationale is required"
            )
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError(
                "VaR calibration policy requires evidence references"
            )

    @property
    def policy_id(self) -> str:
        return _content_id(
            "var-calibration-policy",
            {
                "minimum_observations": self.minimum_observations,
                "kupiec_significance_level": str(
                    self.kupiec_significance_level
                ),
                "rationale": self.rationale,
                "evidence_references": sorted(
                    self.evidence_references
                ),
            },
        )


@dataclass(frozen=True)
class VaRCalibrationReport:
    report_id: str
    calibration_policy_id: str
    confidence_level: Decimal | None
    horizon_seconds: int | None
    observation_ids: tuple[str, ...]
    observation_count: int
    exception_count: int
    exception_rate: Decimal | None
    expected_exception_rate: Decimal | None
    expected_exception_count: Decimal | None
    exception_rate_difference: Decimal | None
    mean_exception_magnitude: Decimal | None
    maximum_exception_magnitude: Decimal | None
    expected_shortfall_exceedance_count: int
    kupiec_lr_uc: Decimal | None
    kupiec_p_value: Decimal | None
    state: VaRCalibrationState
    reasons: tuple[str, ...]
    diagnostics: tuple[str, ...]
    approval_authority: str
    order_authority: str
    capital_authority: str
    caveat: str


class VaRCalibrationEngine:
    CAVEAT = (
        "This report measures prospective exception behavior for a frozen VaR "
        "methodology. A statistical non-rejection does not prove model adequacy, "
        "future calibration, regulatory compliance, or permission to trade."
    )

    def evaluate(
        self,
        *,
        observations: tuple[VaRBacktestObservation, ...],
        policy: VaRCalibrationPolicy,
    ) -> VaRCalibrationReport:
        if len(observations) != len(
            {item.observation_id for item in observations}
        ):
            raise ValueError(
                "VaR calibration cannot contain duplicate observation identities"
            )
        for item in observations:
            if item.observation_id != var_backtest_observation_identity(
                item
            ):
                raise ValueError(
                    "VaR backtest observation identity mismatch"
                )
            if (
                item.approval_authority != "NONE"
                or item.order_authority != "NONE"
                or item.capital_authority != "NONE"
            ):
                raise ValueError(
                    "VaR backtest observation unexpectedly carries authority"
                )

        canonical = tuple(
            sorted(
                observations,
                key=lambda item: (
                    item.period_start,
                    item.period_end,
                    item.observation_id,
                ),
            )
        )
        period_keys = [
            (item.period_start, item.period_end)
            for item in canonical
        ]
        if len(period_keys) != len(set(period_keys)):
            raise ValueError(
                "VaR calibration cannot duplicate realized periods"
            )
        estimate_ids = [item.estimate_id for item in canonical]
        if len(estimate_ids) != len(set(estimate_ids)):
            raise ValueError(
                "VaR calibration cannot backtest one risk estimate twice"
            )

        n = len(canonical)
        confidence: Decimal | None = None
        horizon: int | None = None
        if canonical:
            confidences = {
                item.confidence_level for item in canonical
            }
            horizons = {
                item.horizon_seconds for item in canonical
            }
            if len(confidences) != 1:
                raise ValueError(
                    "VaR calibration cannot mix confidence levels"
                )
            if len(horizons) != 1:
                raise ValueError(
                    "VaR calibration cannot mix forecast horizons"
                )
            confidence = next(iter(confidences))
            horizon = next(iter(horizons))

        exceptions = tuple(
            item for item in canonical if item.var_exception
        )
        es_exceedances = tuple(
            item
            for item in canonical
            if item.loss_beyond_expected_shortfall
        )
        exception_count = len(exceptions)

        exception_rate = None
        expected_rate = None
        expected_count = None
        rate_difference = None
        mean_magnitude = None
        max_magnitude = None
        lr_uc = None
        p_value = None
        reasons: list[str] = []

        if n < policy.minimum_observations:
            state = VaRCalibrationState.INSUFFICIENT_EVIDENCE
            reasons.append(
                "minimum prospective VaR backtest observation count not reached"
            )
        else:
            assert confidence is not None
            expected_rate = Decimal("1") - confidence
            exception_rate = (
                Decimal(exception_count) / Decimal(n)
            )
            expected_count = (
                expected_rate * Decimal(n)
            )
            rate_difference = (
                exception_rate - expected_rate
            )
            if exceptions:
                mean_magnitude = (
                    sum(
                        (
                            item.var_exception_magnitude
                            for item in exceptions
                        ),
                        Decimal("0"),
                    )
                    / Decimal(len(exceptions))
                )
                max_magnitude = max(
                    item.var_exception_magnitude
                    for item in exceptions
                )
            else:
                mean_magnitude = Decimal("0")
                max_magnitude = Decimal("0")

            lr_value, p_value_float = _kupiec_unconditional_coverage(
                observations=n,
                exceptions=exception_count,
                expected_exception_probability=float(
                    expected_rate
                ),
            )
            lr_uc = Decimal(str(lr_value))
            p_value = Decimal(str(p_value_float))
            if p_value < policy.kupiec_significance_level:
                state = VaRCalibrationState.REVIEW_REQUIRED
                reasons.append(
                    "Kupiec unconditional-coverage null rejected at frozen significance level"
                )
            else:
                state = (
                    VaRCalibrationState.WITHIN_TEST_TOLERANCE
                )
                reasons.append(
                    "Kupiec unconditional-coverage null was not rejected at frozen significance level"
                )

        diagnostics = (
            "VaR exception is realized loss strictly greater than the frozen VaR loss",
            "expected exception rate equals one minus frozen confidence level",
            "Kupiec LR_uc tests unconditional exception frequency only",
            "Kupiec non-rejection is not model approval and does not test exception independence or tail severity",
            "expected-shortfall exceedance count is descriptive only",
            "no Basel or other regulatory traffic-light classification is inferred",
        )
        payload = {
            "calibration_policy_id": policy.policy_id,
            "confidence_level": _optional_str(confidence),
            "horizon_seconds": horizon,
            "observation_ids": [
                item.observation_id for item in canonical
            ],
            "observation_count": n,
            "exception_count": exception_count,
            "exception_rate": _optional_str(exception_rate),
            "expected_exception_rate": _optional_str(
                expected_rate
            ),
            "expected_exception_count": _optional_str(
                expected_count
            ),
            "exception_rate_difference": _optional_str(
                rate_difference
            ),
            "mean_exception_magnitude": _optional_str(
                mean_magnitude
            ),
            "maximum_exception_magnitude": _optional_str(
                max_magnitude
            ),
            "expected_shortfall_exceedance_count": len(
                es_exceedances
            ),
            "kupiec_lr_uc": _optional_str(lr_uc),
            "kupiec_p_value": _optional_str(p_value),
            "state": state.value,
            "reasons": list(reasons),
            "diagnostics": list(diagnostics),
            "approval_authority": "NONE",
            "order_authority": "NONE",
            "capital_authority": "NONE",
        }
        return VaRCalibrationReport(
            report_id=_content_id(
                "var-calibration-report",
                payload,
            ),
            calibration_policy_id=policy.policy_id,
            confidence_level=confidence,
            horizon_seconds=horizon,
            observation_ids=tuple(
                item.observation_id for item in canonical
            ),
            observation_count=n,
            exception_count=exception_count,
            exception_rate=exception_rate,
            expected_exception_rate=expected_rate,
            expected_exception_count=expected_count,
            exception_rate_difference=rate_difference,
            mean_exception_magnitude=mean_magnitude,
            maximum_exception_magnitude=max_magnitude,
            expected_shortfall_exceedance_count=len(
                es_exceedances
            ),
            kupiec_lr_uc=lr_uc,
            kupiec_p_value=p_value,
            state=state,
            reasons=tuple(reasons),
            diagnostics=diagnostics,
            approval_authority="NONE",
            order_authority="NONE",
            capital_authority="NONE",
            caveat=self.CAVEAT,
        )


class VaRBacktestStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS var_backtest_observations (
                observation_id VARCHAR PRIMARY KEY,
                estimate_id VARCHAR NOT NULL UNIQUE,
                cube_id VARCHAR NOT NULL,
                forecast_time TIMESTAMPTZ NOT NULL,
                period_start TIMESTAMPTZ NOT NULL,
                period_end TIMESTAMPTZ NOT NULL,
                var_exception BOOLEAN NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS var_calibration_reports (
                report_id VARCHAR PRIMARY KEY,
                calibration_policy_id VARCHAR NOT NULL,
                state VARCHAR NOT NULL,
                observation_count INTEGER NOT NULL,
                exception_count INTEGER NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add_observation(
        self,
        observation: VaRBacktestObservation,
    ) -> bool:
        if (
            observation.observation_id
            != var_backtest_observation_identity(observation)
        ):
            raise ValueError(
                "VaR backtest observation identity mismatch"
            )
        payload = json.dumps(
            var_backtest_observation_payload(observation),
            sort_keys=True,
            separators=(",", ":"),
        )
        row = self._con.execute(
            """
            SELECT payload_json
            FROM var_backtest_observations
            WHERE observation_id = ?
            """,
            [observation.observation_id],
        ).fetchone()
        if row is not None:
            if str(row[0]) != payload:
                raise ValueError(
                    "VaR backtest observation identity conflict"
                )
            return False
        if self._con.execute(
            """
            SELECT 1
            FROM var_backtest_observations
            WHERE estimate_id = ?
            """,
            [observation.estimate_id],
        ).fetchone() is not None:
            raise ValueError(
                "risk estimate already has a prospective backtest outcome"
            )
        self._con.execute(
            """
            INSERT INTO var_backtest_observations
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                observation.observation_id,
                observation.estimate_id,
                observation.cube_id,
                observation.forecast_time,
                observation.period_start,
                observation.period_end,
                observation.var_exception,
                payload,
            ],
        )
        return True

    def add_report(
        self,
        report: VaRCalibrationReport,
    ) -> bool:
        if (
            report.report_id
            != var_calibration_report_identity(report)
        ):
            raise ValueError(
                "VaR calibration report identity mismatch"
            )
        payload = json.dumps(
            var_calibration_report_payload(report),
            sort_keys=True,
            separators=(",", ":"),
        )
        row = self._con.execute(
            """
            SELECT payload_json
            FROM var_calibration_reports
            WHERE report_id = ?
            """,
            [report.report_id],
        ).fetchone()
        if row is not None:
            if str(row[0]) != payload:
                raise ValueError(
                    "VaR calibration report identity conflict"
                )
            return False
        self._con.execute(
            """
            INSERT INTO var_calibration_reports
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                report.report_id,
                report.calibration_policy_id,
                report.state.value,
                report.observation_count,
                report.exception_count,
                payload,
            ],
        )
        return True

    def close(self) -> None:
        self._con.close()


def var_backtest_observation_identity(
    observation: VaRBacktestObservation,
) -> str:
    return _content_id(
        "var-backtest-observation",
        {
            key: value
            for key, value in var_backtest_observation_payload(
                observation
            ).items()
            if key != "observation_id"
        },
    )


def var_backtest_observation_payload(
    observation: VaRBacktestObservation,
) -> dict[str, object]:
    return {
        "observation_id": observation.observation_id,
        "estimate_id": observation.estimate_id,
        "risk_policy_id": observation.risk_policy_id,
        "cube_id": observation.cube_id,
        "outcome_id": observation.outcome_id,
        "forecast_time": observation.forecast_time.isoformat(),
        "period_start": observation.period_start.isoformat(),
        "period_end": observation.period_end.isoformat(),
        "confidence_level": str(
            observation.confidence_level
        ),
        "horizon_seconds": observation.horizon_seconds,
        "value_at_risk_loss": str(
            observation.value_at_risk_loss
        ),
        "expected_shortfall_loss": str(
            observation.expected_shortfall_loss
        ),
        "realized_pnl": str(observation.realized_pnl),
        "realized_loss": str(observation.realized_loss),
        "var_exception": observation.var_exception,
        "var_exception_magnitude": str(
            observation.var_exception_magnitude
        ),
        "loss_beyond_expected_shortfall": (
            observation.loss_beyond_expected_shortfall
        ),
        "expected_shortfall_excess_magnitude": str(
            observation.expected_shortfall_excess_magnitude
        ),
        "source_fact_ids": list(
            observation.source_fact_ids
        ),
        "diagnostics": list(observation.diagnostics),
        "approval_authority": (
            observation.approval_authority
        ),
        "order_authority": observation.order_authority,
        "capital_authority": observation.capital_authority,
    }


def var_calibration_report_identity(
    report: VaRCalibrationReport,
) -> str:
    return _content_id(
        "var-calibration-report",
        {
            key: value
            for key, value in var_calibration_report_payload(
                report
            ).items()
            if key not in {"report_id", "caveat"}
        },
    )


def var_calibration_report_payload(
    report: VaRCalibrationReport,
) -> dict[str, object]:
    return {
        "report_id": report.report_id,
        "calibration_policy_id": (
            report.calibration_policy_id
        ),
        "confidence_level": _optional_str(
            report.confidence_level
        ),
        "horizon_seconds": report.horizon_seconds,
        "observation_ids": list(report.observation_ids),
        "observation_count": report.observation_count,
        "exception_count": report.exception_count,
        "exception_rate": _optional_str(
            report.exception_rate
        ),
        "expected_exception_rate": _optional_str(
            report.expected_exception_rate
        ),
        "expected_exception_count": _optional_str(
            report.expected_exception_count
        ),
        "exception_rate_difference": _optional_str(
            report.exception_rate_difference
        ),
        "mean_exception_magnitude": _optional_str(
            report.mean_exception_magnitude
        ),
        "maximum_exception_magnitude": _optional_str(
            report.maximum_exception_magnitude
        ),
        "expected_shortfall_exceedance_count": (
            report.expected_shortfall_exceedance_count
        ),
        "kupiec_lr_uc": _optional_str(report.kupiec_lr_uc),
        "kupiec_p_value": _optional_str(
            report.kupiec_p_value
        ),
        "state": report.state.value,
        "reasons": list(report.reasons),
        "diagnostics": list(report.diagnostics),
        "approval_authority": report.approval_authority,
        "order_authority": report.order_authority,
        "capital_authority": report.capital_authority,
        "caveat": report.caveat,
    }


def _kupiec_unconditional_coverage(
    *,
    observations: int,
    exceptions: int,
    expected_exception_probability: float,
) -> tuple[float, float]:
    if observations <= 0:
        raise ValueError(
            "Kupiec test requires positive observation count"
        )
    if exceptions < 0 or exceptions > observations:
        raise ValueError(
            "Kupiec exception count outside valid range"
        )
    p = expected_exception_probability
    if p <= 0 or p >= 1:
        raise ValueError(
            "Kupiec expected exception probability must be between 0 and 1"
        )
    x = exceptions
    n = observations
    q = x / n
    log_null = (
        (n - x) * math.log1p(-p)
        + x * math.log(p)
    )
    if x == 0 or x == n:
        log_mle = 0.0
    else:
        log_mle = (
            (n - x) * math.log1p(-q)
            + x * math.log(q)
        )
    lr = max(0.0, -2.0 * (log_null - log_mle))
    p_value = math.erfc(math.sqrt(lr / 2.0))
    return lr, p_value


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
