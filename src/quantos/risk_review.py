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
from .portfolio_risk_cube import (
    PortfolioRiskCube,
    PortfolioRiskCubeState,
    portfolio_risk_cube_identity,
)
from .var_backtesting import (
    VaRBacktestObservation,
    VaRCalibrationReport,
    VaRCalibrationState,
    var_backtest_observation_identity,
    var_calibration_report_identity,
)


class ExceptionIndependenceState(str, Enum):
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    WITHIN_TEST_TOLERANCE = "WITHIN_TEST_TOLERANCE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


@dataclass(frozen=True)
class ExceptionIndependencePolicy:
    minimum_contiguous_transitions: int
    significance_level: Decimal
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.minimum_contiguous_transitions < 19:
            raise ValueError(
                "exception-independence policy requires at least 19 contiguous transitions"
            )
        if (
            not self.significance_level.is_finite()
            or self.significance_level <= 0
            or self.significance_level >= 1
        ):
            raise ValueError(
                "exception-independence significance level must be between 0 and 1"
            )
        if not self.rationale.strip():
            raise ValueError(
                "exception-independence policy rationale is required"
            )
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError(
                "exception-independence policy requires evidence references"
            )

    @property
    def policy_id(self) -> str:
        return _content_id(
            "exception-independence-policy",
            {
                "minimum_contiguous_transitions": (
                    self.minimum_contiguous_transitions
                ),
                "significance_level": str(
                    self.significance_level
                ),
                "rationale": self.rationale,
                "evidence_references": sorted(
                    self.evidence_references
                ),
            },
        )


@dataclass(frozen=True)
class ExceptionIndependenceDiagnostic:
    diagnostic_id: str
    calibration_report_id: str
    policy_id: str
    observation_ids: tuple[str, ...]
    observation_count: int
    contiguous_transition_count: int
    sequence_segment_count: int
    n00: int
    n01: int
    n10: int
    n11: int
    christoffersen_lr_ind: Decimal | None
    christoffersen_p_value: Decimal | None
    conditional_coverage_lr_cc: Decimal | None
    conditional_coverage_p_value: Decimal | None
    state: ExceptionIndependenceState
    reasons: tuple[str, ...]
    diagnostics: tuple[str, ...]
    approval_authority: str
    order_authority: str
    capital_authority: str
    caveat: str


class ExceptionIndependenceEngine:
    CAVEAT = (
        "Exception-independence diagnostics test only observed VaR exception "
        "clustering and conditional coverage under the stated assumptions. "
        "Non-rejection does not establish model adequacy or future calibration."
    )

    def evaluate(
        self,
        *,
        observations: tuple[VaRBacktestObservation, ...],
        calibration: VaRCalibrationReport,
        policy: ExceptionIndependencePolicy,
    ) -> ExceptionIndependenceDiagnostic:
        if calibration.report_id != var_calibration_report_identity(
            calibration
        ):
            raise ValueError(
                "VaR calibration report identity mismatch"
            )
        if len(observations) != len(
            {item.observation_id for item in observations}
        ):
            raise ValueError(
                "exception-independence input contains duplicate observations"
            )
        for item in observations:
            if item.observation_id != var_backtest_observation_identity(
                item
            ):
                raise ValueError(
                    "VaR backtest observation identity mismatch"
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
        if tuple(
            item.observation_id for item in canonical
        ) != calibration.observation_ids:
            raise ValueError(
                "independence observations differ from calibration report"
            )

        n00 = n01 = n10 = n11 = 0
        contiguous = 0
        segments = 1 if canonical else 0
        for previous, current in zip(
            canonical,
            canonical[1:],
        ):
            if current.period_start != previous.period_end:
                segments += 1
                continue
            contiguous += 1
            before = previous.var_exception
            after = current.var_exception
            if not before and not after:
                n00 += 1
            elif not before and after:
                n01 += 1
            elif before and not after:
                n10 += 1
            else:
                n11 += 1

        lr_ind = None
        p_ind = None
        lr_cc = None
        p_cc = None
        reasons: list[str] = []
        if (
            contiguous
            < policy.minimum_contiguous_transitions
            or calibration.kupiec_lr_uc is None
        ):
            state = (
                ExceptionIndependenceState.INSUFFICIENT_EVIDENCE
            )
            reasons.append(
                "minimum contiguous transition evidence not reached"
            )
        else:
            lr_value, p_value = _christoffersen_independence(
                n00=n00,
                n01=n01,
                n10=n10,
                n11=n11,
            )
            lr_ind = Decimal(str(lr_value))
            p_ind = Decimal(str(p_value))
            lr_cc_float = (
                float(calibration.kupiec_lr_uc)
                + lr_value
            )
            p_cc_float = math.exp(-lr_cc_float / 2.0)
            lr_cc = Decimal(str(lr_cc_float))
            p_cc = Decimal(str(p_cc_float))

            flags = []
            if p_ind < policy.significance_level:
                flags.append(
                    "Christoffersen exception-independence null rejected"
                )
            if p_cc < policy.significance_level:
                flags.append(
                    "combined conditional-coverage null rejected"
                )
            if calibration.state is VaRCalibrationState.REVIEW_REQUIRED:
                flags.append(
                    "upstream unconditional-coverage calibration requires review"
                )
            if flags:
                state = (
                    ExceptionIndependenceState.REVIEW_REQUIRED
                )
                reasons.extend(flags)
            else:
                state = (
                    ExceptionIndependenceState.WITHIN_TEST_TOLERANCE
                )
                reasons.append(
                    "independence and conditional-coverage nulls were not rejected at frozen significance level"
                )

        diagnostics = (
            "only exactly contiguous realized periods form exception transitions",
            "gaps split the sequence and are not treated as no-exception observations",
            "Christoffersen LR_ind tests first-order exception independence",
            "conditional-coverage LR_cc equals upstream Kupiec LR_uc plus LR_ind",
            "chi-square df=1 survival is used for LR_ind; df=2 survival is used for LR_cc",
            "non-rejection is not risk-model approval",
        )
        payload = {
            "calibration_report_id": calibration.report_id,
            "policy_id": policy.policy_id,
            "observation_ids": [
                item.observation_id for item in canonical
            ],
            "observation_count": len(canonical),
            "contiguous_transition_count": contiguous,
            "sequence_segment_count": segments,
            "n00": n00,
            "n01": n01,
            "n10": n10,
            "n11": n11,
            "christoffersen_lr_ind": _optional_str(
                lr_ind
            ),
            "christoffersen_p_value": _optional_str(
                p_ind
            ),
            "conditional_coverage_lr_cc": _optional_str(
                lr_cc
            ),
            "conditional_coverage_p_value": _optional_str(
                p_cc
            ),
            "state": state.value,
            "reasons": list(reasons),
            "diagnostics": list(diagnostics),
            "approval_authority": "NONE",
            "order_authority": "NONE",
            "capital_authority": "NONE",
        }
        return ExceptionIndependenceDiagnostic(
            diagnostic_id=_content_id(
                "exception-independence-diagnostic",
                payload,
            ),
            calibration_report_id=calibration.report_id,
            policy_id=policy.policy_id,
            observation_ids=tuple(
                item.observation_id for item in canonical
            ),
            observation_count=len(canonical),
            contiguous_transition_count=contiguous,
            sequence_segment_count=segments,
            n00=n00,
            n01=n01,
            n10=n10,
            n11=n11,
            christoffersen_lr_ind=lr_ind,
            christoffersen_p_value=p_ind,
            conditional_coverage_lr_cc=lr_cc,
            conditional_coverage_p_value=p_cc,
            state=state,
            reasons=tuple(reasons),
            diagnostics=diagnostics,
            approval_authority="NONE",
            order_authority="NONE",
            capital_authority="NONE",
            caveat=self.CAVEAT,
        )


class RiskReviewState(str, Enum):
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    WITHIN_POLICY = "WITHIN_POLICY"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


@dataclass(frozen=True)
class RiskReviewPolicy:
    maximum_base_value_concentration: Decimal
    maximum_worst_scenario_loss_on_gross_base: Decimal
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not self.maximum_base_value_concentration.is_finite()
            or self.maximum_base_value_concentration <= 0
            or self.maximum_base_value_concentration > 1
        ):
            raise ValueError(
                "maximum_base_value_concentration must be in (0, 1]"
            )
        if (
            not self.maximum_worst_scenario_loss_on_gross_base.is_finite()
            or self.maximum_worst_scenario_loss_on_gross_base < 0
        ):
            raise ValueError(
                "maximum worst scenario loss ratio must be finite and non-negative"
            )
        if not self.rationale.strip():
            raise ValueError(
                "risk review policy rationale is required"
            )
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError(
                "risk review policy requires evidence references"
            )

    @property
    def policy_id(self) -> str:
        return _content_id(
            "risk-review-policy",
            {
                "maximum_base_value_concentration": str(
                    self.maximum_base_value_concentration
                ),
                "maximum_worst_scenario_loss_on_gross_base": str(
                    self.maximum_worst_scenario_loss_on_gross_base
                ),
                "rationale": self.rationale,
                "evidence_references": sorted(
                    self.evidence_references
                ),
            },
        )


@dataclass(frozen=True)
class RiskReviewDossier:
    dossier_id: str
    cube_id: str
    historical_risk_estimate_id: str
    calibration_report_id: str
    independence_diagnostic_id: str
    historical_risk_policy_id: str
    review_policy_id: str
    reviewed_at: datetime
    reviewer: str
    independent_challenger: str
    state: RiskReviewState
    base_snapshot_id: str
    valuation_time: datetime
    reporting_currency: str
    maximum_base_value_concentration: Decimal | None
    worst_scenario_loss: Decimal | None
    worst_scenario_loss_on_gross_base: Decimal | None
    value_at_risk_loss: Decimal
    expected_shortfall_loss: Decimal
    calibration_state: VaRCalibrationState
    independence_state: ExceptionIndependenceState
    limitations: tuple[str, ...]
    challenger_objections: tuple[str, ...]
    unresolved_objections: tuple[str, ...]
    reasons: tuple[str, ...]
    evidence_references: tuple[str, ...]
    approval_authority: str
    live_authority: str
    order_authority: str
    capital_authority: str
    caveat: str


class RiskReviewEngine:
    CAVEAT = (
        "A WITHIN_POLICY risk-review state means only that the supplied evidence "
        "did not breach the frozen research review rules. It is not approval, "
        "not a LIVE permit, not regulatory validation, and not authority to trade."
    )

    def review(
        self,
        *,
        cube: PortfolioRiskCube,
        historical_risk: HistoricalSimulationRiskEstimate,
        historical_risk_policy: HistoricalSimulationRiskPolicy,
        calibration: VaRCalibrationReport,
        independence: ExceptionIndependenceDiagnostic,
        policy: RiskReviewPolicy,
        reviewed_at: datetime,
        reviewer: str,
        independent_challenger: str,
        limitations: tuple[str, ...],
        challenger_objections: tuple[str, ...],
        unresolved_objections: tuple[str, ...],
        evidence_references: tuple[str, ...],
    ) -> RiskReviewDossier:
        self._validate_lineage(
            cube=cube,
            historical_risk=historical_risk,
            historical_risk_policy=historical_risk_policy,
            calibration=calibration,
            independence=independence,
        )
        if reviewed_at.tzinfo is None:
            raise ValueError(
                "risk review timestamp must be timezone-aware"
            )
        if reviewed_at < cube.valuation_time:
            raise ValueError(
                "risk review cannot predate the current risk snapshot"
            )
        if not reviewer.strip() or not independent_challenger.strip():
            raise ValueError(
                "risk reviewer and independent challenger are required"
            )
        if reviewer.strip() == independent_challenger.strip():
            raise ValueError(
                "risk reviewer and independent challenger must differ"
            )
        if not limitations or not all(
            item.strip() for item in limitations
        ):
            raise ValueError(
                "risk review requires explicit limitations"
            )
        if not evidence_references or not all(
            item.strip() for item in evidence_references
        ):
            raise ValueError(
                "risk review requires evidence references"
            )
        objections = tuple(
            item.strip() for item in challenger_objections
        )
        unresolved = tuple(
            item.strip() for item in unresolved_objections
        )
        if any(not item for item in objections + unresolved):
            raise ValueError(
                "risk-review objections cannot be blank"
            )
        if any(item not in objections for item in unresolved):
            raise ValueError(
                "every unresolved risk objection must appear in challenger objections"
            )

        worst_loss = None
        worst_loss_ratio = None
        if cube.scenario_summaries:
            complete_pnls = tuple(
                item.portfolio_pnl
                for item in cube.scenario_summaries
                if item.complete
                and item.portfolio_pnl is not None
            )
            if len(complete_pnls) != len(cube.scenario_summaries):
                raise ValueError(
                    "COMPLETE risk cube contains incomplete scenario summaries"
                )
            if complete_pnls:
                worst_loss = max(
                    Decimal("0"),
                    max(-item for item in complete_pnls),
                )
                if (
                    cube.gross_base_value is not None
                    and cube.gross_base_value > 0
                ):
                    worst_loss_ratio = (
                        worst_loss / cube.gross_base_value
                    )

        reasons: list[str] = []
        insufficient = False
        if calibration.state is (
            VaRCalibrationState.INSUFFICIENT_EVIDENCE
        ):
            insufficient = True
            reasons.append(
                "prospective unconditional-coverage evidence is insufficient"
            )
        if independence.state is (
            ExceptionIndependenceState.INSUFFICIENT_EVIDENCE
        ):
            insufficient = True
            reasons.append(
                "exception-independence evidence is insufficient"
            )

        review_flags: list[str] = []
        if calibration.state is VaRCalibrationState.REVIEW_REQUIRED:
            review_flags.append(
                "prospective unconditional-coverage calibration requires review"
            )
        if independence.state is (
            ExceptionIndependenceState.REVIEW_REQUIRED
        ):
            review_flags.append(
                "exception independence or conditional coverage requires review"
            )
        if (
            cube.maximum_base_value_concentration is None
        ):
            review_flags.append(
                "base-value concentration is unavailable"
            )
        elif (
            cube.maximum_base_value_concentration
            > policy.maximum_base_value_concentration
        ):
            review_flags.append(
                "base-value concentration exceeds frozen risk-review policy"
            )
        if worst_loss_ratio is None:
            review_flags.append(
                "worst deterministic scenario loss ratio is unavailable"
            )
        elif (
            worst_loss_ratio
            > policy.maximum_worst_scenario_loss_on_gross_base
        ):
            review_flags.append(
                "worst deterministic scenario loss exceeds frozen gross-base threshold"
            )
        if unresolved:
            review_flags.append(
                "independent challenger has unresolved risk objections"
            )

        if insufficient:
            state = RiskReviewState.INSUFFICIENT_EVIDENCE
            reasons.extend(review_flags)
        elif review_flags:
            state = RiskReviewState.REVIEW_REQUIRED
            reasons.extend(review_flags)
        else:
            state = RiskReviewState.WITHIN_POLICY
            reasons.append(
                "supplied deterministic stress and prospective calibration evidence remains within frozen research review policy"
            )

        payload = {
            "cube_id": cube.cube_id,
            "historical_risk_estimate_id": (
                historical_risk.estimate_id
            ),
            "calibration_report_id": calibration.report_id,
            "independence_diagnostic_id": (
                independence.diagnostic_id
            ),
            "historical_risk_policy_id": (
                historical_risk_policy.policy_id
            ),
            "review_policy_id": policy.policy_id,
            "reviewed_at": reviewed_at.isoformat(),
            "reviewer": reviewer.strip(),
            "independent_challenger": (
                independent_challenger.strip()
            ),
            "state": state.value,
            "base_snapshot_id": cube.base_snapshot_id,
            "valuation_time": cube.valuation_time.isoformat(),
            "reporting_currency": cube.reporting_currency,
            "maximum_base_value_concentration": _optional_str(
                cube.maximum_base_value_concentration
            ),
            "worst_scenario_loss": _optional_str(worst_loss),
            "worst_scenario_loss_on_gross_base": _optional_str(
                worst_loss_ratio
            ),
            "value_at_risk_loss": str(
                historical_risk.value_at_risk_loss
            ),
            "expected_shortfall_loss": str(
                historical_risk.expected_shortfall_loss
            ),
            "calibration_state": calibration.state.value,
            "independence_state": independence.state.value,
            "limitations": list(limitations),
            "challenger_objections": list(objections),
            "unresolved_objections": list(unresolved),
            "reasons": list(reasons),
            "evidence_references": list(evidence_references),
            "approval_authority": "NONE",
            "live_authority": "NONE",
            "order_authority": "NONE",
            "capital_authority": "NONE",
        }
        return RiskReviewDossier(
            dossier_id=_content_id(
                "risk-review-dossier",
                payload,
            ),
            cube_id=cube.cube_id,
            historical_risk_estimate_id=(
                historical_risk.estimate_id
            ),
            calibration_report_id=calibration.report_id,
            independence_diagnostic_id=(
                independence.diagnostic_id
            ),
            historical_risk_policy_id=(
                historical_risk_policy.policy_id
            ),
            review_policy_id=policy.policy_id,
            reviewed_at=reviewed_at,
            reviewer=reviewer.strip(),
            independent_challenger=(
                independent_challenger.strip()
            ),
            state=state,
            base_snapshot_id=cube.base_snapshot_id,
            valuation_time=cube.valuation_time,
            reporting_currency=cube.reporting_currency,
            maximum_base_value_concentration=(
                cube.maximum_base_value_concentration
            ),
            worst_scenario_loss=worst_loss,
            worst_scenario_loss_on_gross_base=(
                worst_loss_ratio
            ),
            value_at_risk_loss=(
                historical_risk.value_at_risk_loss
            ),
            expected_shortfall_loss=(
                historical_risk.expected_shortfall_loss
            ),
            calibration_state=calibration.state,
            independence_state=independence.state,
            limitations=limitations,
            challenger_objections=objections,
            unresolved_objections=unresolved,
            reasons=tuple(reasons),
            evidence_references=evidence_references,
            approval_authority="NONE",
            live_authority="NONE",
            order_authority="NONE",
            capital_authority="NONE",
            caveat=self.CAVEAT,
        )

    @staticmethod
    def _validate_lineage(
        *,
        cube: PortfolioRiskCube,
        historical_risk: HistoricalSimulationRiskEstimate,
        historical_risk_policy: HistoricalSimulationRiskPolicy,
        calibration: VaRCalibrationReport,
        independence: ExceptionIndependenceDiagnostic,
    ) -> None:
        if cube.cube_id != portfolio_risk_cube_identity(cube):
            raise ValueError("portfolio risk cube identity mismatch")
        if cube.state is not PortfolioRiskCubeState.COMPLETE:
            raise ValueError(
                "risk review requires COMPLETE deterministic stress coverage"
            )
        if cube.missing_coverage:
            raise ValueError(
                "COMPLETE risk cube cannot carry missing coverage"
            )
        if (
            historical_risk.estimate_id
            != historical_simulation_risk_estimate_identity(
                historical_risk
            )
        ):
            raise ValueError(
                "historical simulation risk estimate identity mismatch"
            )
        if historical_risk.policy_id != (
            historical_risk_policy.policy_id
        ):
            raise ValueError(
                "historical risk estimate belongs to another policy"
            )
        if historical_risk.cube_id != cube.cube_id:
            raise ValueError(
                "historical risk estimate belongs to another risk cube"
            )
        if (
            historical_risk.base_snapshot_id
            != cube.base_snapshot_id
            or historical_risk.valuation_time
            != cube.valuation_time
            or historical_risk.reporting_currency
            != cube.reporting_currency
        ):
            raise ValueError(
                "historical risk estimate and deterministic cube lineage differ"
            )
        if (
            historical_risk.observation_count
            != len(historical_risk.observation_ids)
            or historical_risk.observation_count
            != len(historical_risk.ordered_losses)
        ):
            raise ValueError(
                "historical risk estimate observation lineage is incomplete"
            )
        if calibration.report_id != var_calibration_report_identity(
            calibration
        ):
            raise ValueError(
                "VaR calibration report identity mismatch"
            )
        if (
            independence.diagnostic_id
            != exception_independence_diagnostic_identity(
                independence
            )
        ):
            raise ValueError(
                "exception-independence diagnostic identity mismatch"
            )
        if independence.calibration_report_id != calibration.report_id:
            raise ValueError(
                "exception-independence diagnostic belongs to another calibration report"
            )
        if calibration.confidence_level != (
            historical_risk.confidence_level
        ):
            raise ValueError(
                "current historical risk confidence differs from calibration history"
            )
        if calibration.horizon_seconds != (
            historical_risk_policy.horizon_seconds
        ):
            raise ValueError(
                "current historical risk horizon differs from calibration history"
            )
        if (
            historical_risk.approval_authority
            if hasattr(historical_risk, "approval_authority")
            else "NONE"
        ) != "NONE":
            raise ValueError(
                "historical risk unexpectedly carries approval authority"
            )
        if (
            historical_risk.order_authority != "NONE"
            or historical_risk.capital_authority != "NONE"
            or calibration.approval_authority != "NONE"
            or calibration.order_authority != "NONE"
            or calibration.capital_authority != "NONE"
            or independence.approval_authority != "NONE"
            or independence.order_authority != "NONE"
            or independence.capital_authority != "NONE"
        ):
            raise ValueError(
                "upstream risk artifacts unexpectedly carry operational authority"
            )


class RiskReviewStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS risk_review_dossiers (
                dossier_id VARCHAR PRIMARY KEY,
                cube_id VARCHAR NOT NULL,
                historical_risk_estimate_id VARCHAR NOT NULL,
                calibration_report_id VARCHAR NOT NULL,
                independence_diagnostic_id VARCHAR NOT NULL,
                state VARCHAR NOT NULL,
                reviewed_at TIMESTAMPTZ NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, dossier: RiskReviewDossier) -> bool:
        if dossier.dossier_id != risk_review_dossier_identity(
            dossier
        ):
            raise ValueError(
                "risk review dossier identity mismatch"
            )
        payload = json.dumps(
            risk_review_dossier_payload(dossier),
            sort_keys=True,
            separators=(",", ":"),
        )
        row = self._con.execute(
            """
            SELECT payload_json
            FROM risk_review_dossiers
            WHERE dossier_id = ?
            """,
            [dossier.dossier_id],
        ).fetchone()
        if row is not None:
            if str(row[0]) != payload:
                raise ValueError(
                    "risk review dossier identity conflict"
                )
            return False
        self._con.execute(
            """
            INSERT INTO risk_review_dossiers
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                dossier.dossier_id,
                dossier.cube_id,
                dossier.historical_risk_estimate_id,
                dossier.calibration_report_id,
                dossier.independence_diagnostic_id,
                dossier.state.value,
                dossier.reviewed_at,
                payload,
            ],
        )
        return True

    def close(self) -> None:
        self._con.close()


def exception_independence_diagnostic_identity(
    diagnostic: ExceptionIndependenceDiagnostic,
) -> str:
    return _content_id(
        "exception-independence-diagnostic",
        {
            key: value
            for key, value in exception_independence_diagnostic_payload(
                diagnostic
            ).items()
            if key not in {"diagnostic_id", "caveat"}
        },
    )


def exception_independence_diagnostic_payload(
    diagnostic: ExceptionIndependenceDiagnostic,
) -> dict[str, object]:
    return {
        "diagnostic_id": diagnostic.diagnostic_id,
        "calibration_report_id": diagnostic.calibration_report_id,
        "policy_id": diagnostic.policy_id,
        "observation_ids": list(diagnostic.observation_ids),
        "observation_count": diagnostic.observation_count,
        "contiguous_transition_count": (
            diagnostic.contiguous_transition_count
        ),
        "sequence_segment_count": diagnostic.sequence_segment_count,
        "n00": diagnostic.n00,
        "n01": diagnostic.n01,
        "n10": diagnostic.n10,
        "n11": diagnostic.n11,
        "christoffersen_lr_ind": _optional_str(
            diagnostic.christoffersen_lr_ind
        ),
        "christoffersen_p_value": _optional_str(
            diagnostic.christoffersen_p_value
        ),
        "conditional_coverage_lr_cc": _optional_str(
            diagnostic.conditional_coverage_lr_cc
        ),
        "conditional_coverage_p_value": _optional_str(
            diagnostic.conditional_coverage_p_value
        ),
        "state": diagnostic.state.value,
        "reasons": list(diagnostic.reasons),
        "diagnostics": list(diagnostic.diagnostics),
        "approval_authority": diagnostic.approval_authority,
        "order_authority": diagnostic.order_authority,
        "capital_authority": diagnostic.capital_authority,
        "caveat": diagnostic.caveat,
    }


def risk_review_dossier_identity(
    dossier: RiskReviewDossier,
) -> str:
    return _content_id(
        "risk-review-dossier",
        {
            key: value
            for key, value in risk_review_dossier_payload(
                dossier
            ).items()
            if key not in {"dossier_id", "caveat"}
        },
    )


def risk_review_dossier_payload(
    dossier: RiskReviewDossier,
) -> dict[str, object]:
    return {
        "dossier_id": dossier.dossier_id,
        "cube_id": dossier.cube_id,
        "historical_risk_estimate_id": (
            dossier.historical_risk_estimate_id
        ),
        "calibration_report_id": dossier.calibration_report_id,
        "independence_diagnostic_id": (
            dossier.independence_diagnostic_id
        ),
        "historical_risk_policy_id": (
            dossier.historical_risk_policy_id
        ),
        "review_policy_id": dossier.review_policy_id,
        "reviewed_at": dossier.reviewed_at.isoformat(),
        "reviewer": dossier.reviewer,
        "independent_challenger": (
            dossier.independent_challenger
        ),
        "state": dossier.state.value,
        "base_snapshot_id": dossier.base_snapshot_id,
        "valuation_time": dossier.valuation_time.isoformat(),
        "reporting_currency": dossier.reporting_currency,
        "maximum_base_value_concentration": _optional_str(
            dossier.maximum_base_value_concentration
        ),
        "worst_scenario_loss": _optional_str(
            dossier.worst_scenario_loss
        ),
        "worst_scenario_loss_on_gross_base": _optional_str(
            dossier.worst_scenario_loss_on_gross_base
        ),
        "value_at_risk_loss": str(
            dossier.value_at_risk_loss
        ),
        "expected_shortfall_loss": str(
            dossier.expected_shortfall_loss
        ),
        "calibration_state": dossier.calibration_state.value,
        "independence_state": dossier.independence_state.value,
        "limitations": list(dossier.limitations),
        "challenger_objections": list(
            dossier.challenger_objections
        ),
        "unresolved_objections": list(
            dossier.unresolved_objections
        ),
        "reasons": list(dossier.reasons),
        "evidence_references": list(
            dossier.evidence_references
        ),
        "approval_authority": dossier.approval_authority,
        "live_authority": dossier.live_authority,
        "order_authority": dossier.order_authority,
        "capital_authority": dossier.capital_authority,
        "caveat": dossier.caveat,
    }


def _christoffersen_independence(
    *,
    n00: int,
    n01: int,
    n10: int,
    n11: int,
) -> tuple[float, float]:
    total = n00 + n01 + n10 + n11
    if total <= 0:
        raise ValueError(
            "Christoffersen independence test requires transitions"
        )
    exceptions_after = n01 + n11
    pi = exceptions_after / total
    row0 = n00 + n01
    row1 = n10 + n11
    pi0 = n01 / row0 if row0 else 0.0
    pi1 = n11 / row1 if row1 else 0.0
    log_null = _bernoulli_log_likelihood(
        successes=exceptions_after,
        trials=total,
        probability=pi,
    )
    log_alt = (
        _bernoulli_log_likelihood(
            successes=n01,
            trials=row0,
            probability=pi0,
        )
        + _bernoulli_log_likelihood(
            successes=n11,
            trials=row1,
            probability=pi1,
        )
    )
    lr = max(0.0, -2.0 * (log_null - log_alt))
    p_value = math.erfc(math.sqrt(lr / 2.0))
    return lr, p_value


def _bernoulli_log_likelihood(
    *,
    successes: int,
    trials: int,
    probability: float,
) -> float:
    if trials == 0:
        return 0.0
    if probability == 0.0:
        return 0.0 if successes == 0 else float("-inf")
    if probability == 1.0:
        return 0.0 if successes == trials else float("-inf")
    return (
        successes * math.log(probability)
        + (trials - successes)
        * math.log1p(-probability)
    )


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
