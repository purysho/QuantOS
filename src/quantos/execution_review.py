"""Stage 12.9 — two-person execution review (shadow-only recommendation).

The review binds, by identity, one execution schedule and its result, the
replay data-quality report for the same dataset and orders, the TCA reports
for exactly those orders, and optionally a Nautilus schedule differential
for the same dataset and policy. An Execution Trader reviewer and an
independent Red Team challenger must differ. Blocking objections cannot be
outvoted.

The best possible outcome is WITHIN_POLICY with the recommendation
ELIGIBLE_FOR_SHADOW_EXECUTION_DESIGN_REVIEW, which means only that a
separate shadow-execution authority plane may be designed and reviewed. It
grants no approval, network, order, live or capital authority.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

import duckdb

from .execution_analytics import (
    TransactionCostReport,
    transaction_cost_report_identity,
)
from .execution_contracts import (
    ExecutionSimulationRunManifest,
    execution_simulation_run_identity,
)
from .execution_data_quality import (
    ReplayQualityReport,
    ReplayQualityState,
    replay_quality_report_identity,
)
from .execution_nautilus import (
    DifferentialState,
    NautilusScheduleDifferentialResult,
    nautilus_schedule_differential_identity,
)
from .execution_schedule import (
    ExecutionSchedule,
    ExecutionScheduleResult,
    ExecutionScheduleState,
    execution_schedule_identity,
    execution_schedule_result_identity,
)

_BPS = Decimal("10000")


class ExecutionReviewState(str, Enum):
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    WITHIN_POLICY = "WITHIN_POLICY"


class ExecutionReviewRecommendation(str, Enum):
    RESEARCH_ITERATION = "RESEARCH_ITERATION"
    ELIGIBLE_FOR_SHADOW_EXECUTION_DESIGN_REVIEW = (
        "ELIGIBLE_FOR_SHADOW_EXECUTION_DESIGN_REVIEW"
    )


@dataclass(frozen=True)
class ExecutionReviewPolicy:
    maximum_implementation_shortfall_bps: Decimal
    minimum_fill_ratio: Decimal
    require_clean_replay: bool
    require_independent_engine_match: bool
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.maximum_implementation_shortfall_bps.is_finite():
            raise ValueError("maximum shortfall must be finite")
        if (
            not self.minimum_fill_ratio.is_finite()
            or self.minimum_fill_ratio <= 0
            or self.minimum_fill_ratio > 1
        ):
            raise ValueError("minimum_fill_ratio must be in (0, 1]")
        if not self.rationale.strip():
            raise ValueError("execution review policy rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError(
                "execution review policy requires evidence references"
            )

    @property
    def policy_id(self) -> str:
        return _content_id(
            "execution-review-policy",
            {
                "maximum_implementation_shortfall_bps": str(
                    self.maximum_implementation_shortfall_bps
                ),
                "minimum_fill_ratio": str(self.minimum_fill_ratio),
                "require_clean_replay": self.require_clean_replay,
                "require_independent_engine_match": (
                    self.require_independent_engine_match
                ),
                "rationale": self.rationale,
                "evidence_references": sorted(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class ExecutionReviewDossier:
    dossier_id: str
    run_id: str
    schedule_id: str
    schedule_result_id: str
    replay_quality_report_id: str
    transaction_cost_report_ids: tuple[str, ...]
    schedule_differential_id: str | None
    review_policy_id: str
    reviewed_at: datetime
    reviewer: str
    independent_challenger: str
    state: ExecutionReviewState
    recommendation: ExecutionReviewRecommendation
    schedule_state: ExecutionScheduleState
    replay_quality_state: ReplayQualityState
    engine_differential_state: DifferentialState | None
    implementation_shortfall_bps: Decimal
    fill_ratio: Decimal
    limitations: tuple[str, ...]
    challenger_objections: tuple[str, ...]
    unresolved_objections: tuple[str, ...]
    reasons: tuple[str, ...]
    evidence_references: tuple[str, ...]
    approval_authority: str
    network_authority: str
    order_authority: str
    live_authority: str
    capital_authority: str
    caveat: str


class ExecutionReviewEngine:
    CAVEAT = (
        "A WITHIN_POLICY execution review means only that historical replay "
        "evidence did not breach the frozen execution review rules. It permits "
        "designing a separate shadow-execution authority plane for review; it "
        "is not approval, not a network or order permit, and not authority to "
        "trade."
    )

    def review(
        self,
        *,
        run: ExecutionSimulationRunManifest,
        schedule: ExecutionSchedule,
        schedule_result: ExecutionScheduleResult,
        replay_quality: ReplayQualityReport,
        transaction_costs: tuple[TransactionCostReport, ...],
        schedule_differential: NautilusScheduleDifferentialResult | None,
        policy: ExecutionReviewPolicy,
        reviewed_at: datetime,
        reviewer: str,
        independent_challenger: str,
        limitations: tuple[str, ...],
        challenger_objections: tuple[str, ...],
        unresolved_objections: tuple[str, ...],
        evidence_references: tuple[str, ...],
    ) -> ExecutionReviewDossier:
        self._validate_lineage(
            run=run,
            schedule=schedule,
            schedule_result=schedule_result,
            replay_quality=replay_quality,
            transaction_costs=transaction_costs,
            schedule_differential=schedule_differential,
        )
        if reviewed_at.tzinfo is None:
            raise ValueError("execution review timestamp must be timezone-aware")
        if reviewed_at < run.created_at:
            raise ValueError("execution review cannot predate the simulation run")
        if not reviewer.strip() or not independent_challenger.strip():
            raise ValueError(
                "execution reviewer and independent challenger are required"
            )
        if reviewer.strip() == independent_challenger.strip():
            raise ValueError(
                "execution reviewer and independent challenger must differ"
            )
        if not limitations or not all(item.strip() for item in limitations):
            raise ValueError("execution review requires explicit limitations")
        if not evidence_references or not all(
            item.strip() for item in evidence_references
        ):
            raise ValueError("execution review requires evidence references")
        objections = tuple(item.strip() for item in challenger_objections)
        unresolved = tuple(item.strip() for item in unresolved_objections)
        if any(not item for item in objections + unresolved):
            raise ValueError("execution-review objections cannot be blank")
        if any(item not in objections for item in unresolved):
            raise ValueError(
                "every unresolved objection must appear in challenger objections"
            )

        paper = sum((item.paper_notional for item in transaction_costs), Decimal("0"))
        shortfall = sum(
            (item.implementation_shortfall for item in transaction_costs),
            Decimal("0"),
        )
        shortfall_bps = shortfall / paper * _BPS
        ordered_value = sum(
            (item.ordered_quantity * item.arrival_mid for item in transaction_costs),
            Decimal("0"),
        )
        filled_value = sum(
            (item.filled_quantity * item.arrival_mid for item in transaction_costs),
            Decimal("0"),
        )
        fill_ratio = filled_value / ordered_value

        insufficient: list[str] = []
        flags: list[str] = []
        if replay_quality.state is ReplayQualityState.UNUSABLE:
            insufficient.append("replay data quality is UNUSABLE")
        elif (
            replay_quality.state is ReplayQualityState.DEGRADED
            and policy.require_clean_replay
        ):
            flags.append("replay data quality is DEGRADED")
        if schedule_differential is None:
            if policy.require_independent_engine_match:
                insufficient.append(
                    "independent engine differential is required but absent"
                )
        elif schedule_differential.state is not DifferentialState.MATCH:
            flags.append("independent engine differential does not match")
        if schedule_result.state is ExecutionScheduleState.CONSTRAINT_BREACH:
            flags.append("schedule breached inventory/cash policy")
        elif schedule_result.state is ExecutionScheduleState.INCOMPLETE:
            flags.append("schedule left targets incomplete")
        if shortfall_bps > policy.maximum_implementation_shortfall_bps:
            flags.append("implementation shortfall exceeds frozen threshold")
        if fill_ratio < policy.minimum_fill_ratio:
            flags.append("fill ratio below frozen minimum")
        if unresolved:
            flags.append("independent challenger has unresolved objections")

        if insufficient:
            state = ExecutionReviewState.INSUFFICIENT_EVIDENCE
            reasons = tuple(insufficient + flags)
        elif flags:
            state = ExecutionReviewState.REVIEW_REQUIRED
            reasons = tuple(flags)
        else:
            state = ExecutionReviewState.WITHIN_POLICY
            reasons = (
                "historical replay execution evidence remains within frozen "
                "execution review policy",
            )
        recommendation = (
            ExecutionReviewRecommendation.ELIGIBLE_FOR_SHADOW_EXECUTION_DESIGN_REVIEW
            if state is ExecutionReviewState.WITHIN_POLICY
            else ExecutionReviewRecommendation.RESEARCH_ITERATION
        )
        dossier = ExecutionReviewDossier(
            dossier_id="",
            run_id=run.run_id,
            schedule_id=schedule.schedule_id,
            schedule_result_id=schedule_result.result_id,
            replay_quality_report_id=replay_quality.report_id,
            transaction_cost_report_ids=tuple(
                sorted(item.report_id for item in transaction_costs)
            ),
            schedule_differential_id=(
                schedule_differential.schedule_differential_id
                if schedule_differential is not None
                else None
            ),
            review_policy_id=policy.policy_id,
            reviewed_at=reviewed_at,
            reviewer=reviewer.strip(),
            independent_challenger=independent_challenger.strip(),
            state=state,
            recommendation=recommendation,
            schedule_state=schedule_result.state,
            replay_quality_state=replay_quality.state,
            engine_differential_state=(
                schedule_differential.state
                if schedule_differential is not None
                else None
            ),
            implementation_shortfall_bps=shortfall_bps,
            fill_ratio=fill_ratio,
            limitations=tuple(limitations),
            challenger_objections=objections,
            unresolved_objections=unresolved,
            reasons=reasons,
            evidence_references=tuple(evidence_references),
            approval_authority="NONE",
            network_authority="NONE",
            order_authority="NONE",
            live_authority="NONE",
            capital_authority="NONE",
            caveat=self.CAVEAT,
        )
        return replace(dossier, dossier_id=execution_review_dossier_identity(dossier))

    @staticmethod
    def _validate_lineage(
        *,
        run: ExecutionSimulationRunManifest,
        schedule: ExecutionSchedule,
        schedule_result: ExecutionScheduleResult,
        replay_quality: ReplayQualityReport,
        transaction_costs: tuple[TransactionCostReport, ...],
        schedule_differential: NautilusScheduleDifferentialResult | None,
    ) -> None:
        if run.run_id != execution_simulation_run_identity(run):
            raise ValueError("execution simulation run identity mismatch")
        if schedule.schedule_id != execution_schedule_identity(schedule):
            raise ValueError("execution schedule identity mismatch")
        if schedule_result.result_id != execution_schedule_result_identity(
            schedule_result
        ):
            raise ValueError("execution schedule result identity mismatch")
        if schedule.run_id != run.run_id or schedule_result.run_id != run.run_id:
            raise ValueError("schedule evidence belongs to another run")
        if schedule_result.schedule_id != schedule.schedule_id:
            raise ValueError("schedule result belongs to another schedule")
        if replay_quality.report_id != replay_quality_report_identity(
            replay_quality
        ):
            raise ValueError("replay quality report identity mismatch")
        if replay_quality.dataset_id != run.replay_dataset_id:
            raise ValueError("replay quality report covers another dataset")
        if not set(schedule.intent_ids) <= set(replay_quality.intent_ids):
            raise ValueError(
                "replay quality report did not check every schedule order"
            )
        for report in transaction_costs:
            if report.report_id != transaction_cost_report_identity(report):
                raise ValueError("transaction cost report identity mismatch")
            if report.dataset_id != run.replay_dataset_id:
                raise ValueError("transaction cost report covers another dataset")
        if sorted(item.intent_id for item in transaction_costs) != sorted(
            schedule.intent_ids
        ):
            raise ValueError(
                "transaction cost reports must cover exactly the schedule orders"
            )
        if not set(item.reference_result_id for item in transaction_costs) <= set(
            schedule_result.order_result_ids
        ):
            raise ValueError(
                "transaction cost reports reference results outside the schedule"
            )
        if schedule_differential is not None:
            if (
                schedule_differential.schedule_differential_id
                != nautilus_schedule_differential_identity(schedule_differential)
            ):
                raise ValueError("schedule differential identity mismatch")
            if (
                schedule_differential.replay_dataset_id != run.replay_dataset_id
                or schedule_differential.simulation_policy_id
                != run.simulation_policy_id
            ):
                raise ValueError(
                    "schedule differential covers another dataset or policy"
                )
        if (
            schedule_result.external_order_authority != "NONE"
            or schedule_result.capital_authority != "NONE"
            or replay_quality.capital_authority != "NONE"
            or any(item.capital_authority != "NONE" for item in transaction_costs)
            or (
                schedule_differential is not None
                and (
                    schedule_differential.external_order_authority != "NONE"
                    or schedule_differential.capital_authority != "NONE"
                )
            )
        ):
            raise ValueError(
                "upstream execution evidence unexpectedly carries authority"
            )


class ExecutionReviewStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_review_dossiers (
                dossier_id VARCHAR PRIMARY KEY,
                schedule_id VARCHAR NOT NULL,
                state VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, dossier: ExecutionReviewDossier) -> bool:
        if dossier.dossier_id != execution_review_dossier_identity(dossier):
            raise ValueError("execution review dossier identity mismatch")
        payload = json.dumps(
            execution_review_dossier_payload(dossier),
            sort_keys=True,
            separators=(",", ":"),
        )
        row = self._con.execute(
            "SELECT payload_json FROM execution_review_dossiers WHERE dossier_id = ?",
            [dossier.dossier_id],
        ).fetchone()
        if row is not None:
            if str(row[0]) != payload:
                raise ValueError("execution review dossier identity conflict")
            return False
        self._con.execute(
            "INSERT INTO execution_review_dossiers VALUES (?, ?, ?, ?)",
            [dossier.dossier_id, dossier.schedule_id, dossier.state.value, payload],
        )
        return True

    def close(self) -> None:
        self._con.close()


def execution_review_dossier_payload(
    dossier: ExecutionReviewDossier,
) -> dict[str, object]:
    def text(value: object) -> object:
        if value is None or isinstance(value, (str, bool)):
            return value
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, tuple):
            return list(value)
        return str(value)

    return {
        name: text(getattr(dossier, name))
        for name in dossier.__dataclass_fields__
        if name != "dossier_id"
    }


def execution_review_dossier_identity(dossier: ExecutionReviewDossier) -> str:
    return _content_id(
        "execution-review-dossier",
        execution_review_dossier_payload(dossier),
    )


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
