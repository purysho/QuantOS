from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

import duckdb

from .backtest_economics import BenchmarkKind
from .model_registry import (
    ModelLifecycleStage,
    ModelRegistryState,
    ResearchRunManifest,
)
from .readiness import ProspectiveShadowPermit
from .research_case import ResearchCase


class PaperHealthState(str, Enum):
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    MEASURED = "MEASURED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    CLOSED = "CLOSED"


class PostmortemReason(str, Enum):
    COMPLETED_EVALUATION = "COMPLETED_EVALUATION"
    THESIS_INVALIDATED = "THESIS_INVALIDATED"
    UNDERPERFORMANCE = "UNDERPERFORMANCE"
    RISK_BREACH = "RISK_BREACH"
    IMPLEMENTATION_COST = "IMPLEMENTATION_COST"
    DATA_QUALITY = "DATA_QUALITY"
    MODEL_CHANGE_REQUIRED = "MODEL_CHANGE_REQUIRED"


@dataclass(frozen=True)
class PaperMonitoringPolicy:
    minimum_observations: int
    maximum_drawdown_abs: Decimal
    maximum_average_one_way_turnover: Decimal
    maximum_average_implementation_cost_rate: Decimal
    minimum_market_cap_relative_wealth_return: Decimal
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.minimum_observations < 2:
            raise ValueError("minimum_observations must be at least 2")
        for name in (
            "maximum_drawdown_abs",
            "maximum_average_one_way_turnover",
            "maximum_average_implementation_cost_rate",
        ):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.maximum_drawdown_abs > 1:
            raise ValueError("maximum_drawdown_abs cannot exceed 1")
        if not self.minimum_market_cap_relative_wealth_return.is_finite():
            raise ValueError(
                "minimum_market_cap_relative_wealth_return must be finite"
            )
        if not self.rationale.strip():
            raise ValueError("paper monitoring policy rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("paper monitoring policy requires evidence references")

    @property
    def policy_id(self) -> str:
        return _content_id(
            "paper-monitoring-policy",
            {
                "minimum_observations": self.minimum_observations,
                "maximum_drawdown_abs": str(self.maximum_drawdown_abs),
                "maximum_average_one_way_turnover": str(
                    self.maximum_average_one_way_turnover
                ),
                "maximum_average_implementation_cost_rate": str(
                    self.maximum_average_implementation_cost_rate
                ),
                "minimum_market_cap_relative_wealth_return": str(
                    self.minimum_market_cap_relative_wealth_return
                ),
                "rationale": self.rationale,
                "evidence_references": list(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class PaperBenchmarkObservation:
    kind: BenchmarkKind
    total_return: Decimal
    source_fact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.total_return.is_finite() or self.total_return <= Decimal("-1"):
            raise ValueError("benchmark return must be finite and greater than -1")
        if not self.source_fact_ids or not all(
            item.strip() for item in self.source_fact_ids
        ):
            raise ValueError("benchmark observation requires source fact IDs")


@dataclass(frozen=True)
class MonitoringConditionCheck:
    condition: str
    breached: bool
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.condition.strip():
            raise ValueError("monitoring condition is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError("monitoring condition check requires evidence references")


@dataclass(frozen=True)
class PaperPortfolioObservation:
    observation_id: str
    model_id: str
    manifest_id: str
    permit_id: str
    case_id: str
    period_start: datetime
    period_end: datetime
    observed_at: datetime
    gross_return: Decimal
    net_return: Decimal
    transaction_cost_rate: Decimal
    borrow_cost_rate: Decimal
    one_way_turnover_ratio: Decimal
    benchmarks: tuple[PaperBenchmarkObservation, ...]
    condition_checks: tuple[MonitoringConditionCheck, ...]
    evidence_references: tuple[str, ...]


@dataclass(frozen=True)
class PaperHealth:
    model_id: str
    manifest_id: str
    policy_id: str
    state: PaperHealthState
    observations: int
    compounded_net_return: Decimal | None
    maximum_drawdown: Decimal | None
    average_one_way_turnover: Decimal | None
    average_implementation_cost_rate: Decimal | None
    market_cap_relative_wealth_return: Decimal | None
    breached_conditions: tuple[str, ...]
    reasons: tuple[str, ...]
    caveat: str


@dataclass(frozen=True)
class PaperPostmortem:
    postmortem_id: str
    model_id: str
    manifest_id: str
    closed_at: datetime
    reviewer: str
    reason: PostmortemReason
    notes: str
    evidence_references: tuple[str, ...]
    final_health_state: PaperHealthState


class PaperMonitoringLedger:
    """Prospective portfolio observation ledger with no live-capital authority."""

    CAVEAT = (
        "PAPER monitoring measures one exact shadow-only Research Run prospectively. "
        "It does not approve live trading, recommend investment, or authorize capital."
    )

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_portfolio_observations (
                observation_id VARCHAR PRIMARY KEY,
                model_id VARCHAR NOT NULL,
                manifest_id VARCHAR NOT NULL,
                permit_id VARCHAR NOT NULL,
                case_id VARCHAR NOT NULL,
                period_start TIMESTAMPTZ NOT NULL,
                period_end TIMESTAMPTZ NOT NULL,
                observed_at TIMESTAMPTZ NOT NULL,
                gross_return VARCHAR NOT NULL,
                net_return VARCHAR NOT NULL,
                transaction_cost_rate VARCHAR NOT NULL,
                borrow_cost_rate VARCHAR NOT NULL,
                one_way_turnover_ratio VARCHAR NOT NULL,
                benchmarks_json VARCHAR NOT NULL,
                condition_checks_json VARCHAR NOT NULL,
                evidence_references_json VARCHAR NOT NULL
            )
            """
        )
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_postmortems (
                postmortem_id VARCHAR PRIMARY KEY,
                model_id VARCHAR NOT NULL,
                manifest_id VARCHAR NOT NULL UNIQUE,
                closed_at TIMESTAMPTZ NOT NULL,
                reviewer VARCHAR NOT NULL,
                reason VARCHAR NOT NULL,
                notes VARCHAR NOT NULL,
                evidence_references_json VARCHAR NOT NULL,
                final_health_state VARCHAR NOT NULL
            )
            """
        )

    def record(
        self,
        *,
        manifest: ResearchRunManifest,
        registry_state: ModelRegistryState,
        permit: ProspectiveShadowPermit,
        research_case: ResearchCase,
        period_start: datetime,
        period_end: datetime,
        observed_at: datetime,
        gross_return: Decimal,
        net_return: Decimal,
        transaction_cost_rate: Decimal,
        borrow_cost_rate: Decimal,
        one_way_turnover_ratio: Decimal,
        benchmarks: tuple[PaperBenchmarkObservation, ...],
        condition_checks: tuple[MonitoringConditionCheck, ...],
        evidence_references: tuple[str, ...],
    ) -> PaperPortfolioObservation:
        self._validate_authority(
            manifest=manifest,
            registry_state=registry_state,
            permit=permit,
            research_case=research_case,
        )
        if self.postmortem_for(manifest.manifest_id) is not None:
            raise ValueError("closed PAPER manifest cannot accept new observations")
        for name in ("period_start", "period_end", "observed_at"):
            if getattr(locals()[name], "tzinfo", None) is None:
                raise ValueError(f"{name} must be timezone-aware")
        if period_start < permit.issued_at:
            raise ValueError("paper observation cannot begin before shadow permit")
        if period_end <= period_start:
            raise ValueError("period_end must follow period_start")
        if observed_at < period_end:
            raise ValueError("observation cannot be recorded before period_end")
        for name, value in (
            ("gross_return", gross_return),
            ("net_return", net_return),
        ):
            if not value.is_finite() or value <= Decimal("-1"):
                raise ValueError(f"{name} must be finite and greater than -1")
        for name, value in (
            ("transaction_cost_rate", transaction_cost_rate),
            ("borrow_cost_rate", borrow_cost_rate),
            ("one_way_turnover_ratio", one_way_turnover_ratio),
        ):
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        expected_net = gross_return - transaction_cost_rate - borrow_cost_rate
        if net_return != expected_net:
            raise ValueError(
                "net_return must reconcile to gross return less explicit costs"
            )

        required_benchmarks = {
            BenchmarkKind.CASH,
            BenchmarkKind.MARKET_CAP,
            BenchmarkKind.EQUAL_WEIGHT,
            BenchmarkKind.INVERSE_VOL,
        }
        actual_benchmarks = {item.kind for item in benchmarks}
        if actual_benchmarks != required_benchmarks:
            raise ValueError(
                "paper observation requires exactly cash, market-cap, "
                "equal-weight, and inverse-vol benchmarks"
            )
        if len(actual_benchmarks) != len(benchmarks):
            raise ValueError("duplicate paper benchmark kinds are not allowed")

        expected_conditions = tuple(research_case.monitoring_conditions)
        actual_conditions = tuple(item.condition for item in condition_checks)
        if len(actual_conditions) != len(set(actual_conditions)):
            raise ValueError("duplicate monitoring-condition checks")
        if set(actual_conditions) != set(expected_conditions):
            raise ValueError(
                "paper observation must check every frozen Research Case "
                "monitoring condition exactly once"
            )
        if not evidence_references or not all(
            item.strip() for item in evidence_references
        ):
            raise ValueError("paper observation requires evidence references")

        prior = self.observations(manifest.manifest_id)
        if prior and period_start < prior[-1].period_end:
            raise ValueError("paper observation periods cannot overlap")

        payload = {
            "model_id": manifest.model_id,
            "manifest_id": manifest.manifest_id,
            "permit_id": permit.permit_id,
            "case_id": research_case.case_id,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "observed_at": observed_at.isoformat(),
            "gross_return": str(gross_return),
            "net_return": str(net_return),
            "transaction_cost_rate": str(transaction_cost_rate),
            "borrow_cost_rate": str(borrow_cost_rate),
            "one_way_turnover_ratio": str(one_way_turnover_ratio),
            "benchmarks": [
                {
                    "kind": item.kind.value,
                    "total_return": str(item.total_return),
                    "source_fact_ids": list(item.source_fact_ids),
                }
                for item in sorted(benchmarks, key=lambda item: item.kind.value)
            ],
            "condition_checks": [
                {
                    "condition": item.condition,
                    "breached": item.breached,
                    "evidence_references": list(item.evidence_references),
                }
                for item in sorted(
                    condition_checks,
                    key=lambda item: item.condition,
                )
            ],
            "evidence_references": list(evidence_references),
        }
        observation = PaperPortfolioObservation(
            observation_id=_content_id("paper-observation", payload),
            model_id=manifest.model_id,
            manifest_id=manifest.manifest_id,
            permit_id=permit.permit_id,
            case_id=research_case.case_id,
            period_start=period_start,
            period_end=period_end,
            observed_at=observed_at,
            gross_return=gross_return,
            net_return=net_return,
            transaction_cost_rate=transaction_cost_rate,
            borrow_cost_rate=borrow_cost_rate,
            one_way_turnover_ratio=one_way_turnover_ratio,
            benchmarks=tuple(
                sorted(benchmarks, key=lambda item: item.kind.value)
            ),
            condition_checks=tuple(
                sorted(condition_checks, key=lambda item: item.condition)
            ),
            evidence_references=evidence_references,
        )

        existing = self._get_observation(observation.observation_id)
        if existing is not None:
            return existing
        self._con.execute(
            """
            INSERT INTO paper_portfolio_observations
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                observation.observation_id,
                observation.model_id,
                observation.manifest_id,
                observation.permit_id,
                observation.case_id,
                observation.period_start,
                observation.period_end,
                observation.observed_at,
                str(observation.gross_return),
                str(observation.net_return),
                str(observation.transaction_cost_rate),
                str(observation.borrow_cost_rate),
                str(observation.one_way_turnover_ratio),
                json.dumps(payload["benchmarks"], sort_keys=True),
                json.dumps(payload["condition_checks"], sort_keys=True),
                json.dumps(list(observation.evidence_references)),
            ],
        )
        return observation

    def health(
        self,
        *,
        manifest: ResearchRunManifest,
        registry_state: ModelRegistryState,
        policy: PaperMonitoringPolicy,
    ) -> PaperHealth:
        if registry_state.model_id != manifest.model_id:
            raise ValueError("registry state belongs to another model")
        if registry_state.manifest_id != manifest.manifest_id:
            raise ValueError("registry state belongs to another manifest")
        if registry_state.stage is not ModelLifecycleStage.PAPER:
            raise ValueError("paper health requires current PAPER registry state")
        if self.postmortem_for(manifest.manifest_id) is not None:
            observations = self.observations(manifest.manifest_id)
            return self._health_from_observations(
                manifest=manifest,
                observations=observations,
                policy=policy,
                force_closed=True,
            )
        return self._health_from_observations(
            manifest=manifest,
            observations=self.observations(manifest.manifest_id),
            policy=policy,
            force_closed=False,
        )

    def close_with_postmortem(
        self,
        *,
        manifest: ResearchRunManifest,
        registry_state: ModelRegistryState,
        policy: PaperMonitoringPolicy,
        closed_at: datetime,
        reviewer: str,
        reason: PostmortemReason,
        notes: str,
        evidence_references: tuple[str, ...],
    ) -> PaperPostmortem:
        if closed_at.tzinfo is None:
            raise ValueError("closed_at must be timezone-aware")
        if not reviewer.strip() or not notes.strip():
            raise ValueError("postmortem reviewer and notes are required")
        if not evidence_references or not all(
            item.strip() for item in evidence_references
        ):
            raise ValueError("postmortem requires evidence references")
        current = self.postmortem_for(manifest.manifest_id)
        if current is not None:
            requested = (
                reviewer.strip(),
                reason,
                notes.strip(),
                evidence_references,
            )
            existing = (
                current.reviewer,
                current.reason,
                current.notes,
                current.evidence_references,
            )
            if requested != existing:
                raise ValueError("PAPER manifest already has another postmortem")
            return current

        health = self.health(
            manifest=manifest,
            registry_state=registry_state,
            policy=policy,
        )
        observations = self.observations(manifest.manifest_id)
        if observations and closed_at < observations[-1].observed_at:
            raise ValueError("postmortem cannot predate latest observation")
        payload = {
            "model_id": manifest.model_id,
            "manifest_id": manifest.manifest_id,
            "closed_at": closed_at.isoformat(),
            "reviewer": reviewer.strip(),
            "reason": reason.value,
            "notes": notes.strip(),
            "evidence_references": list(evidence_references),
            "final_health_state": health.state.value,
        }
        postmortem = PaperPostmortem(
            postmortem_id=_content_id("paper-postmortem", payload),
            model_id=manifest.model_id,
            manifest_id=manifest.manifest_id,
            closed_at=closed_at,
            reviewer=reviewer.strip(),
            reason=reason,
            notes=notes.strip(),
            evidence_references=evidence_references,
            final_health_state=health.state,
        )
        self._con.execute(
            """
            INSERT INTO paper_postmortems
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                postmortem.postmortem_id,
                postmortem.model_id,
                postmortem.manifest_id,
                postmortem.closed_at,
                postmortem.reviewer,
                postmortem.reason.value,
                postmortem.notes,
                json.dumps(list(postmortem.evidence_references)),
                postmortem.final_health_state.value,
            ],
        )
        return postmortem

    def observations(
        self,
        manifest_id: str,
    ) -> tuple[PaperPortfolioObservation, ...]:
        rows = self._con.execute(
            """
            SELECT observation_id, model_id, manifest_id, permit_id, case_id,
                   period_start, period_end, observed_at, gross_return, net_return,
                   transaction_cost_rate, borrow_cost_rate, one_way_turnover_ratio,
                   benchmarks_json, condition_checks_json,
                   evidence_references_json
            FROM paper_portfolio_observations
            WHERE manifest_id = ?
            ORDER BY period_start, observation_id
            """,
            [manifest_id],
        ).fetchall()
        return tuple(self._observation_from_row(row) for row in rows)

    def postmortem_for(self, manifest_id: str) -> PaperPostmortem | None:
        row = self._con.execute(
            """
            SELECT postmortem_id, model_id, manifest_id, closed_at, reviewer,
                   reason, notes, evidence_references_json, final_health_state
            FROM paper_postmortems
            WHERE manifest_id = ?
            """,
            [manifest_id],
        ).fetchone()
        if row is None:
            return None
        return PaperPostmortem(
            postmortem_id=str(row[0]),
            model_id=str(row[1]),
            manifest_id=str(row[2]),
            closed_at=row[3],
            reviewer=str(row[4]),
            reason=PostmortemReason(str(row[5])),
            notes=str(row[6]),
            evidence_references=tuple(json.loads(str(row[7]))),
            final_health_state=PaperHealthState(str(row[8])),
        )

    def _health_from_observations(
        self,
        *,
        manifest: ResearchRunManifest,
        observations: tuple[PaperPortfolioObservation, ...],
        policy: PaperMonitoringPolicy,
        force_closed: bool,
    ) -> PaperHealth:
        if not observations:
            return PaperHealth(
                model_id=manifest.model_id,
                manifest_id=manifest.manifest_id,
                policy_id=policy.policy_id,
                state=(
                    PaperHealthState.CLOSED
                    if force_closed
                    else PaperHealthState.INSUFFICIENT_EVIDENCE
                ),
                observations=0,
                compounded_net_return=None,
                maximum_drawdown=None,
                average_one_way_turnover=None,
                average_implementation_cost_rate=None,
                market_cap_relative_wealth_return=None,
                breached_conditions=(),
                reasons=("no prospective PAPER observations",),
                caveat=self.CAVEAT,
            )

        wealth = Decimal("1")
        peak = Decimal("1")
        max_drawdown = Decimal("0")
        market_wealth = Decimal("1")
        breached_conditions: set[str] = set()
        total_turnover = Decimal("0")
        total_cost = Decimal("0")
        for item in observations:
            wealth *= Decimal("1") + item.net_return
            if wealth > peak:
                peak = wealth
            drawdown = wealth / peak - Decimal("1")
            if drawdown < max_drawdown:
                max_drawdown = drawdown
            market_return = next(
                benchmark.total_return
                for benchmark in item.benchmarks
                if benchmark.kind is BenchmarkKind.MARKET_CAP
            )
            market_wealth *= Decimal("1") + market_return
            total_turnover += item.one_way_turnover_ratio
            total_cost += (
                item.transaction_cost_rate + item.borrow_cost_rate
            )
            breached_conditions.update(
                check.condition
                for check in item.condition_checks
                if check.breached
            )

        count = Decimal(len(observations))
        avg_turnover = total_turnover / count
        avg_cost = total_cost / count
        market_relative = wealth / market_wealth - Decimal("1")
        reasons: list[str] = []
        if abs(max_drawdown) > policy.maximum_drawdown_abs:
            reasons.append("maximum drawdown exceeded monitoring policy")
        if avg_turnover > policy.maximum_average_one_way_turnover:
            reasons.append("average turnover exceeded monitoring policy")
        if avg_cost > policy.maximum_average_implementation_cost_rate:
            reasons.append("average implementation cost exceeded monitoring policy")
        if (
            market_relative
            < policy.minimum_market_cap_relative_wealth_return
        ):
            reasons.append(
                "market-cap relative wealth fell below monitoring policy"
            )
        if breached_conditions:
            reasons.append("one or more Research Case monitoring conditions breached")

        if force_closed:
            state = PaperHealthState.CLOSED
            reasons.append("prospective PAPER evaluation is closed by postmortem")
        elif reasons:
            state = PaperHealthState.REVIEW_REQUIRED
        elif len(observations) < policy.minimum_observations:
            state = PaperHealthState.INSUFFICIENT_EVIDENCE
            reasons.append("minimum prospective observation count not reached")
        else:
            state = PaperHealthState.MEASURED
            reasons.append("prospective observations remain within monitoring policy")

        return PaperHealth(
            model_id=manifest.model_id,
            manifest_id=manifest.manifest_id,
            policy_id=policy.policy_id,
            state=state,
            observations=len(observations),
            compounded_net_return=wealth - Decimal("1"),
            maximum_drawdown=max_drawdown,
            average_one_way_turnover=avg_turnover,
            average_implementation_cost_rate=avg_cost,
            market_cap_relative_wealth_return=market_relative,
            breached_conditions=tuple(sorted(breached_conditions)),
            reasons=tuple(reasons),
            caveat=self.CAVEAT,
        )

    @staticmethod
    def _validate_authority(
        *,
        manifest: ResearchRunManifest,
        registry_state: ModelRegistryState,
        permit: ProspectiveShadowPermit,
        research_case: ResearchCase,
    ) -> None:
        if manifest.eligible_stage is not ModelLifecycleStage.PAPER:
            raise ValueError("manifest is not PAPER-eligible")
        if registry_state.stage is not ModelLifecycleStage.PAPER:
            raise ValueError("registry state is not PAPER")
        if registry_state.model_id != manifest.model_id:
            raise ValueError("registry state belongs to another model")
        if registry_state.manifest_id != manifest.manifest_id:
            raise ValueError("registry state belongs to another manifest")
        if manifest.shadow_permit_id != permit.permit_id:
            raise ValueError("manifest is bound to another shadow permit")
        if manifest.research_case_id != research_case.case_id:
            raise ValueError("manifest belongs to another Research Case")
        if permit.case_id != research_case.case_id:
            raise ValueError("shadow permit belongs to another Research Case")
        if (
            manifest.case_dossier_fingerprint
            != permit.case_dossier_fingerprint
        ):
            raise ValueError("manifest and permit dossier fingerprints differ")
        if permit.purpose != "PROSPECTIVE_SHADOW_ONLY":
            raise ValueError("paper monitoring requires shadow-only permit")

    def _get_observation(
        self,
        observation_id: str,
    ) -> PaperPortfolioObservation | None:
        row = self._con.execute(
            """
            SELECT observation_id, model_id, manifest_id, permit_id, case_id,
                   period_start, period_end, observed_at, gross_return, net_return,
                   transaction_cost_rate, borrow_cost_rate, one_way_turnover_ratio,
                   benchmarks_json, condition_checks_json,
                   evidence_references_json
            FROM paper_portfolio_observations
            WHERE observation_id = ?
            """,
            [observation_id],
        ).fetchone()
        if row is None:
            return None
        return self._observation_from_row(row)

    @staticmethod
    def _observation_from_row(row) -> PaperPortfolioObservation:
        benchmarks = tuple(
            PaperBenchmarkObservation(
                kind=BenchmarkKind(str(item["kind"])),
                total_return=Decimal(str(item["total_return"])),
                source_fact_ids=tuple(item["source_fact_ids"]),
            )
            for item in json.loads(str(row[13]))
        )
        checks = tuple(
            MonitoringConditionCheck(
                condition=str(item["condition"]),
                breached=bool(item["breached"]),
                evidence_references=tuple(item["evidence_references"]),
            )
            for item in json.loads(str(row[14]))
        )
        return PaperPortfolioObservation(
            observation_id=str(row[0]),
            model_id=str(row[1]),
            manifest_id=str(row[2]),
            permit_id=str(row[3]),
            case_id=str(row[4]),
            period_start=row[5],
            period_end=row[6],
            observed_at=row[7],
            gross_return=Decimal(str(row[8])),
            net_return=Decimal(str(row[9])),
            transaction_cost_rate=Decimal(str(row[10])),
            borrow_cost_rate=Decimal(str(row[11])),
            one_way_turnover_ratio=Decimal(str(row[12])),
            benchmarks=benchmarks,
            condition_checks=checks,
            evidence_references=tuple(json.loads(str(row[15]))),
        )

    def close(self) -> None:
        self._con.close()


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
