from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path

import duckdb

from .model_registry import (
    ModelLifecycleStage,
    ModelRegistryState,
    ResearchRunManifest,
)
from .portfolio_construction import (
    BaselineAllocator,
    PortfolioSolution,
    PortfolioWeight,
    portfolio_solution_identity,
)
from .portfolio_hierarchical import (
    HierarchicalAllocator,
    HierarchicalPortfolioSolution,
    hierarchical_portfolio_solution_identity,
)
from .portfolio_optimization import (
    OptimizedPortfolioSolution,
    optimized_portfolio_solution_identity,
)
from .portfolio_paper_authorization import (
    PortfolioPaperAuthorization,
    PortfolioPaperExecutionAssumptions,
    PortfolioPaperKillCondition,
    PortfolioPaperMonitoringPolicy,
    SelectedPortfolioSolution,
    portfolio_paper_authorization_identity,
)


class PortfolioPaperAuthorizationState(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    TERMINATED = "TERMINATED"
    EXPIRED = "EXPIRED"
    CLOSED = "CLOSED"


@dataclass(frozen=True)
class PortfolioPaperEnforcementEvent:
    event_id: str
    ordinal: int
    authorization_id: str
    occurred_at: datetime
    prior_state: PortfolioPaperAuthorizationState
    new_state: PortfolioPaperAuthorizationState
    kill_conditions: tuple[PortfolioPaperKillCondition, ...]
    reason: str
    evidence_references: tuple[str, ...]


@dataclass(frozen=True)
class PortfolioPaperShadowObservation:
    observation_id: str
    authorization_id: str
    model_id: str
    manifest_id: str
    selected_solution_id: str
    period_start: datetime
    period_end: datetime
    observed_at: datetime
    gross_return: Decimal
    net_return: Decimal
    implementation_cost_rate: Decimal
    expected_implementation_cost_rate: Decimal
    implementation_cost_assumption_variance: Decimal
    one_way_turnover: Decimal
    weights: tuple[PortfolioWeight, ...]
    net_exposure: Decimal
    gross_exposure: Decimal
    solution_drift_turnover: Decimal
    cumulative_net_return: Decimal
    running_maximum_drawdown: Decimal
    running_average_implementation_cost_rate: Decimal
    source_fact_ids: tuple[str, ...]
    triggered_kill_conditions: tuple[PortfolioPaperKillCondition, ...]
    authorization_state_after_record: PortfolioPaperAuthorizationState
    paper_authority: str
    order_authority: str
    capital_authority: str


class PortfolioPaperShadowLedger:
    """Enforce one finite Stage 10.8 authorization during shadow PAPER."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_paper_shadow_observations (
                observation_id VARCHAR PRIMARY KEY,
                authorization_id VARCHAR NOT NULL,
                model_id VARCHAR NOT NULL,
                manifest_id VARCHAR NOT NULL,
                selected_solution_id VARCHAR NOT NULL,
                period_start TIMESTAMPTZ NOT NULL,
                period_end TIMESTAMPTZ NOT NULL,
                observed_at TIMESTAMPTZ NOT NULL,
                net_return VARCHAR NOT NULL,
                implementation_cost_rate VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_paper_enforcement_events (
                event_id VARCHAR PRIMARY KEY,
                ordinal INTEGER NOT NULL,
                authorization_id VARCHAR NOT NULL,
                occurred_at TIMESTAMPTZ NOT NULL,
                prior_state VARCHAR NOT NULL,
                new_state VARCHAR NOT NULL,
                kill_conditions_json VARCHAR NOT NULL,
                reason VARCHAR NOT NULL,
                evidence_references_json VARCHAR NOT NULL,
                UNIQUE (authorization_id, ordinal)
            )
            """
        )

    def record(
        self,
        *,
        authorization: PortfolioPaperAuthorization,
        manifest: ResearchRunManifest,
        registry_state: ModelRegistryState,
        selected_solution: SelectedPortfolioSolution,
        execution_assumptions: PortfolioPaperExecutionAssumptions,
        monitoring_policy: PortfolioPaperMonitoringPolicy,
        period_start: datetime,
        period_end: datetime,
        observed_at: datetime,
        gross_return: Decimal,
        net_return: Decimal,
        implementation_cost_rate: Decimal,
        one_way_turnover: Decimal,
        weights: tuple[PortfolioWeight, ...],
        source_fact_ids: tuple[str, ...],
        evidence_references: tuple[str, ...],
    ) -> PortfolioPaperShadowObservation:
        if (
            authorization.authorization_id
            != portfolio_paper_authorization_identity(authorization)
        ):
            raise ValueError(
                "portfolio PAPER authorization identity does not match content"
            )

        state = self.state(authorization.authorization_id)
        if state is not PortfolioPaperAuthorizationState.ACTIVE:
            raise ValueError(
                f"portfolio PAPER authorization is not active: {state.value}"
            )

        for name, value in (
            ("period_start", period_start),
            ("period_end", period_end),
            ("observed_at", observed_at),
        ):
            if value.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")
        if period_end <= period_start:
            raise ValueError("period_end must follow period_start")
        if observed_at < period_end:
            raise ValueError("observed_at cannot precede period_end")
        if period_start < authorization.authorized_at:
            raise ValueError(
                "shadow observation cannot begin before PAPER authorization"
            )
        if (
            observed_at > authorization.expires_at
            or period_end > authorization.expires_at
        ):
            self._trip(
                authorization=authorization,
                occurred_at=observed_at,
                new_state=PortfolioPaperAuthorizationState.EXPIRED,
                kill_conditions=(),
                reason="portfolio PAPER authorization expired",
                evidence_references=evidence_references,
            )
            raise ValueError("portfolio PAPER authorization has expired")

        binding_break = self._binding_break_reason(
            authorization=authorization,
            manifest=manifest,
            registry_state=registry_state,
            selected_solution=selected_solution,
            execution_assumptions=execution_assumptions,
            monitoring_policy=monitoring_policy,
        )
        if binding_break is not None:
            self._trip(
                authorization=authorization,
                occurred_at=observed_at,
                new_state=PortfolioPaperAuthorizationState.TERMINATED,
                kill_conditions=(
                    PortfolioPaperKillCondition.MODEL_OR_MANIFEST_CHANGE,
                ),
                reason=binding_break,
                evidence_references=evidence_references,
            )
            raise ValueError(binding_break)

        if (
            not source_fact_ids
            or not all(item.strip() for item in source_fact_ids)
            or len(source_fact_ids) != len(set(source_fact_ids))
        ):
            self._trip(
                authorization=authorization,
                occurred_at=observed_at,
                new_state=PortfolioPaperAuthorizationState.TERMINATED,
                kill_conditions=(
                    PortfolioPaperKillCondition.DATA_LINEAGE_BREAK,
                ),
                reason=(
                    "shadow observation data lineage is missing, blank, "
                    "or duplicated"
                ),
                evidence_references=evidence_references,
            )
            raise ValueError("shadow observation requires intact data lineage")
        if not evidence_references or not all(
            item.strip() for item in evidence_references
        ):
            raise ValueError(
                "shadow observation requires evidence references"
            )

        self._validate_numeric_inputs(
            gross_return=gross_return,
            net_return=net_return,
            implementation_cost_rate=implementation_cost_rate,
            one_way_turnover=one_way_turnover,
        )
        canonical_weights = self._validate_weights(
            weights=weights,
            selected_solution=selected_solution,
        )
        expected_net = gross_return - implementation_cost_rate
        if abs(net_return - expected_net) > Decimal("1e-12"):
            raise ValueError(
                "net_return must equal gross_return minus implementation cost"
            )
        if Decimal("1") + net_return <= 0:
            raise ValueError(
                "shadow observation would make one-period wealth non-positive"
            )

        previous_rows = self._prior_observations(
            authorization.authorization_id
        )
        if previous_rows:
            previous_end = previous_rows[-1]["period_end"]
            if period_start < previous_end:
                raise ValueError(
                    "portfolio PAPER observation periods cannot overlap"
                )

        net_exposure = sum(
            (item.weight for item in canonical_weights),
            Decimal("0"),
        )
        gross_exposure = sum(
            (abs(item.weight) for item in canonical_weights),
            Decimal("0"),
        )
        maximum_absolute_weight = max(
            abs(item.weight) for item in canonical_weights
        )
        solution_drift = _weight_turnover_distance(
            canonical_weights,
            selected_solution.weights,
        )
        expected_cost = _expected_implementation_cost_rate(
            execution_assumptions=execution_assumptions,
            one_way_turnover=one_way_turnover,
            weights=canonical_weights,
            period_start=period_start,
            period_end=period_end,
        )
        cost_variance = implementation_cost_rate - expected_cost

        return_path = tuple(
            row["net_return"] for row in previous_rows
        ) + (net_return,)
        cost_path = tuple(
            row["implementation_cost_rate"]
            for row in previous_rows
        ) + (implementation_cost_rate,)
        cumulative_return, max_drawdown = _wealth_metrics(return_path)
        running_average_cost = _mean(cost_path)

        breaches: list[PortfolioPaperKillCondition] = []
        if (
            maximum_absolute_weight
            > monitoring_policy.maximum_absolute_weight
            or gross_exposure
            > monitoring_policy.maximum_gross_exposure
            or abs(net_exposure - selected_solution.net_exposure)
            > Decimal("1e-10")
        ):
            breaches.append(
                PortfolioPaperKillCondition.PORTFOLIO_CONSTRAINT_BREACH
            )
        if abs(max_drawdown) > monitoring_policy.maximum_drawdown_abs:
            breaches.append(PortfolioPaperKillCondition.MAX_DRAWDOWN_BREACH)
        if one_way_turnover > monitoring_policy.maximum_one_way_turnover:
            breaches.append(PortfolioPaperKillCondition.TURNOVER_BREACH)
        if (
            running_average_cost
            > monitoring_policy.maximum_average_implementation_cost_rate
        ):
            breaches.append(
                PortfolioPaperKillCondition.IMPLEMENTATION_COST_BREACH
            )
        if (
            solution_drift
            > monitoring_policy.maximum_solution_drift_turnover
        ):
            breaches.append(
                PortfolioPaperKillCondition.SOLUTION_DRIFT_BREACH
            )
        triggered = tuple(
            sorted(set(breaches), key=lambda item: item.value)
        )
        state_after = (
            PortfolioPaperAuthorizationState.SUSPENDED
            if triggered
            else PortfolioPaperAuthorizationState.ACTIVE
        )

        payload = {
            "authorization_id": authorization.authorization_id,
            "model_id": authorization.model_id,
            "manifest_id": authorization.manifest_id,
            "selected_solution_id": authorization.selected_solution_id,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "observed_at": observed_at.isoformat(),
            "gross_return": str(gross_return),
            "net_return": str(net_return),
            "implementation_cost_rate": str(implementation_cost_rate),
            "expected_implementation_cost_rate": str(expected_cost),
            "implementation_cost_assumption_variance": str(cost_variance),
            "one_way_turnover": str(one_way_turnover),
            "weights": [
                {
                    "security_id": item.security_id,
                    "weight": str(item.weight),
                }
                for item in canonical_weights
            ],
            "net_exposure": str(net_exposure),
            "gross_exposure": str(gross_exposure),
            "solution_drift_turnover": str(solution_drift),
            "cumulative_net_return": str(cumulative_return),
            "running_maximum_drawdown": str(max_drawdown),
            "running_average_implementation_cost_rate": str(
                running_average_cost
            ),
            "source_fact_ids": sorted(source_fact_ids),
            "triggered_kill_conditions": [
                item.value for item in triggered
            ],
            "authorization_state_after_record": state_after.value,
            "paper_authority": "SHADOW_ONLY",
            "order_authority": "NONE",
            "capital_authority": "NONE",
        }
        observation = PortfolioPaperShadowObservation(
            observation_id=_content_id(
                "portfolio-paper-shadow-observation",
                payload,
            ),
            authorization_id=authorization.authorization_id,
            model_id=authorization.model_id,
            manifest_id=authorization.manifest_id,
            selected_solution_id=authorization.selected_solution_id,
            period_start=period_start,
            period_end=period_end,
            observed_at=observed_at,
            gross_return=gross_return,
            net_return=net_return,
            implementation_cost_rate=implementation_cost_rate,
            expected_implementation_cost_rate=expected_cost,
            implementation_cost_assumption_variance=cost_variance,
            one_way_turnover=one_way_turnover,
            weights=canonical_weights,
            net_exposure=net_exposure,
            gross_exposure=gross_exposure,
            solution_drift_turnover=solution_drift,
            cumulative_net_return=cumulative_return,
            running_maximum_drawdown=max_drawdown,
            running_average_implementation_cost_rate=running_average_cost,
            source_fact_ids=tuple(sorted(source_fact_ids)),
            triggered_kill_conditions=triggered,
            authorization_state_after_record=state_after,
            paper_authority="SHADOW_ONLY",
            order_authority="NONE",
            capital_authority="NONE",
        )

        self._con.execute("BEGIN TRANSACTION")
        try:
            self._insert_observation(observation)
            if triggered:
                self._insert_event(
                    self._build_event(
                        authorization_id=authorization.authorization_id,
                        occurred_at=observed_at,
                        prior_state=PortfolioPaperAuthorizationState.ACTIVE,
                        new_state=PortfolioPaperAuthorizationState.SUSPENDED,
                        kill_conditions=triggered,
                        reason=(
                            "mandatory portfolio PAPER kill condition "
                            "triggered by recorded observation"
                        ),
                        evidence_references=(
                            *evidence_references,
                            observation.observation_id,
                        ),
                    )
                )
            self._con.execute("COMMIT")
        except Exception:
            self._con.execute("ROLLBACK")
            raise
        return observation

    def complete(
        self,
        *,
        authorization: PortfolioPaperAuthorization,
        completed_at: datetime,
        reviewer: str,
        reason: str,
        evidence_references: tuple[str, ...],
    ) -> PortfolioPaperEnforcementEvent:
        if (
            authorization.authorization_id
            != portfolio_paper_authorization_identity(authorization)
        ):
            raise ValueError(
                "portfolio PAPER authorization identity does not match content"
            )
        if self.state(authorization.authorization_id) is not (
            PortfolioPaperAuthorizationState.ACTIVE
        ):
            raise ValueError(
                "only an active portfolio PAPER authorization can be closed"
            )
        if completed_at.tzinfo is None:
            raise ValueError("completed_at must be timezone-aware")
        if (
            completed_at < authorization.authorized_at
            or completed_at > authorization.expires_at
        ):
            raise ValueError(
                "normal PAPER closure must occur inside authorization window"
            )
        if not reviewer.strip():
            raise ValueError("PAPER completion reviewer is required")
        event = self._build_event(
            authorization_id=authorization.authorization_id,
            occurred_at=completed_at,
            prior_state=PortfolioPaperAuthorizationState.ACTIVE,
            new_state=PortfolioPaperAuthorizationState.CLOSED,
            kill_conditions=(),
            reason=reason.strip(),
            evidence_references=(
                *evidence_references,
                f"reviewer:{reviewer.strip()}",
            ),
        )
        self._insert_event(event)
        return event

    def state(
        self,
        authorization_id: str,
    ) -> PortfolioPaperAuthorizationState:
        row = self._con.execute(
            """
            SELECT new_state
            FROM portfolio_paper_enforcement_events
            WHERE authorization_id = ?
            ORDER BY ordinal DESC
            LIMIT 1
            """,
            [authorization_id],
        ).fetchone()
        if row is None:
            return PortfolioPaperAuthorizationState.ACTIVE
        return PortfolioPaperAuthorizationState(str(row[0]))

    def observations(
        self,
        authorization_id: str,
    ) -> tuple[PortfolioPaperShadowObservation, ...]:
        rows = self._con.execute(
            """
            SELECT payload_json
            FROM portfolio_paper_shadow_observations
            WHERE authorization_id = ?
            ORDER BY period_start, observation_id
            """,
            [authorization_id],
        ).fetchall()
        return tuple(
            _observation_from_payload(json.loads(str(row[0])))
            for row in rows
        )

    def events(
        self,
        authorization_id: str,
    ) -> tuple[PortfolioPaperEnforcementEvent, ...]:
        rows = self._con.execute(
            """
            SELECT event_id, ordinal, authorization_id, occurred_at,
                   prior_state, new_state, kill_conditions_json,
                   reason, evidence_references_json
            FROM portfolio_paper_enforcement_events
            WHERE authorization_id = ?
            ORDER BY ordinal
            """,
            [authorization_id],
        ).fetchall()
        return tuple(
            PortfolioPaperEnforcementEvent(
                event_id=str(row[0]),
                ordinal=int(row[1]),
                authorization_id=str(row[2]),
                occurred_at=row[3],
                prior_state=PortfolioPaperAuthorizationState(str(row[4])),
                new_state=PortfolioPaperAuthorizationState(str(row[5])),
                kill_conditions=tuple(
                    PortfolioPaperKillCondition(item)
                    for item in json.loads(str(row[6]))
                ),
                reason=str(row[7]),
                evidence_references=tuple(
                    json.loads(str(row[8]))
                ),
            )
            for row in rows
        )

    def close(self) -> None:
        self._con.close()

    @staticmethod
    def _binding_break_reason(
        *,
        authorization: PortfolioPaperAuthorization,
        manifest: ResearchRunManifest,
        registry_state: ModelRegistryState,
        selected_solution: SelectedPortfolioSolution,
        execution_assumptions: PortfolioPaperExecutionAssumptions,
        monitoring_policy: PortfolioPaperMonitoringPolicy,
    ) -> str | None:
        if (
            authorization.paper_authority != "SHADOW_ONLY"
            or authorization.order_authority != "NONE"
            or authorization.capital_authority != "NONE"
            or authorization.purpose
            != "PROSPECTIVE_PORTFOLIO_SHADOW_ONLY"
        ):
            return "authorization authority boundary changed"
        if (
            manifest.eligible_stage is not ModelLifecycleStage.PAPER
            or manifest.model_id != authorization.model_id
            or manifest.manifest_id != authorization.manifest_id
        ):
            return "Research Run manifest changed from authorized PAPER run"
        if (
            registry_state.stage is not ModelLifecycleStage.PAPER
            or registry_state.model_id != authorization.model_id
            or registry_state.manifest_id != authorization.manifest_id
        ):
            return "Model Registry state changed from authorized PAPER run"
        if (
            selected_solution.solution_id
            != authorization.selected_solution_id
            or selected_solution.dataset_id
            != authorization.selected_dataset_id
            or selected_solution.constraint_policy_id
            != authorization.constraint_policy_id
            or selected_solution.model_id != authorization.model_id
            or selected_solution.manifest_id != authorization.manifest_id
        ):
            return "selected portfolio solution changed from authorization"
        if not _authentic_solution(selected_solution):
            return "selected portfolio solution identity no longer matches content"
        if _solution_method(selected_solution).value != authorization.selected_method.value:
            return "selected portfolio method changed from authorization"
        if (
            execution_assumptions.assumptions_id
            != authorization.execution_assumptions_id
        ):
            return "execution assumptions changed from authorization"
        if monitoring_policy.policy_id != authorization.monitoring_policy_id:
            return "PAPER monitoring policy changed from authorization"
        return None

    @staticmethod
    def _validate_numeric_inputs(
        *,
        gross_return: Decimal,
        net_return: Decimal,
        implementation_cost_rate: Decimal,
        one_way_turnover: Decimal,
    ) -> None:
        for name, value in (
            ("gross_return", gross_return),
            ("net_return", net_return),
        ):
            if not value.is_finite() or value <= Decimal("-1"):
                raise ValueError(
                    f"{name} must be finite and greater than -1"
                )
        for name, value in (
            ("implementation_cost_rate", implementation_cost_rate),
            ("one_way_turnover", one_way_turnover),
        ):
            if not value.is_finite() or value < 0:
                raise ValueError(
                    f"{name} must be finite and non-negative"
                )

    @staticmethod
    def _validate_weights(
        *,
        weights: tuple[PortfolioWeight, ...],
        selected_solution: SelectedPortfolioSolution,
    ) -> tuple[PortfolioWeight, ...]:
        if not weights:
            raise ValueError("shadow portfolio weights are required")
        if len({item.security_id for item in weights}) != len(weights):
            raise ValueError("shadow portfolio weights contain duplicates")
        if any(
            not item.security_id.strip() or not item.weight.is_finite()
            for item in weights
        ):
            raise ValueError(
                "shadow portfolio weights require finite identified values"
            )
        canonical = tuple(
            sorted(weights, key=lambda item: item.security_id)
        )
        authorized_ids = {
            item.security_id for item in selected_solution.weights
        }
        if {item.security_id for item in canonical} != authorized_ids:
            raise ValueError(
                "shadow portfolio security set differs from authorized solution"
            )
        return canonical

    def _prior_observations(
        self,
        authorization_id: str,
    ) -> list[dict[str, object]]:
        rows = self._con.execute(
            """
            SELECT period_end, net_return, implementation_cost_rate
            FROM portfolio_paper_shadow_observations
            WHERE authorization_id = ?
            ORDER BY period_start, observation_id
            """,
            [authorization_id],
        ).fetchall()
        return [
            {
                "period_end": row[0],
                "net_return": Decimal(str(row[1])),
                "implementation_cost_rate": Decimal(str(row[2])),
            }
            for row in rows
        ]

    def _trip(
        self,
        *,
        authorization: PortfolioPaperAuthorization,
        occurred_at: datetime,
        new_state: PortfolioPaperAuthorizationState,
        kill_conditions: tuple[PortfolioPaperKillCondition, ...],
        reason: str,
        evidence_references: tuple[str, ...],
    ) -> None:
        prior = self.state(authorization.authorization_id)
        if prior is not PortfolioPaperAuthorizationState.ACTIVE:
            return
        event = self._build_event(
            authorization_id=authorization.authorization_id,
            occurred_at=occurred_at,
            prior_state=prior,
            new_state=new_state,
            kill_conditions=kill_conditions,
            reason=reason,
            evidence_references=evidence_references,
        )
        self._insert_event(event)

    def _build_event(
        self,
        *,
        authorization_id: str,
        occurred_at: datetime,
        prior_state: PortfolioPaperAuthorizationState,
        new_state: PortfolioPaperAuthorizationState,
        kill_conditions: tuple[PortfolioPaperKillCondition, ...],
        reason: str,
        evidence_references: tuple[str, ...],
    ) -> PortfolioPaperEnforcementEvent:
        if occurred_at.tzinfo is None:
            raise ValueError("enforcement event time must be timezone-aware")
        if not reason.strip():
            raise ValueError("enforcement event reason is required")
        if not evidence_references or not all(
            item.strip() for item in evidence_references
        ):
            raise ValueError(
                "enforcement event requires evidence references"
            )
        ordinal = self._next_event_ordinal(authorization_id)
        canonical_conditions = tuple(
            sorted(set(kill_conditions), key=lambda item: item.value)
        )
        payload = {
            "ordinal": ordinal,
            "authorization_id": authorization_id,
            "occurred_at": occurred_at.isoformat(),
            "prior_state": prior_state.value,
            "new_state": new_state.value,
            "kill_conditions": [
                item.value for item in canonical_conditions
            ],
            "reason": reason.strip(),
            "evidence_references": list(evidence_references),
        }
        return PortfolioPaperEnforcementEvent(
            event_id=_content_id(
                "portfolio-paper-enforcement-event",
                payload,
            ),
            ordinal=ordinal,
            authorization_id=authorization_id,
            occurred_at=occurred_at,
            prior_state=prior_state,
            new_state=new_state,
            kill_conditions=canonical_conditions,
            reason=reason.strip(),
            evidence_references=evidence_references,
        )

    def _insert_observation(
        self,
        observation: PortfolioPaperShadowObservation,
    ) -> None:
        if (
            observation.observation_id
            != portfolio_paper_shadow_observation_identity(observation)
        ):
            raise ValueError(
                "portfolio PAPER observation identity does not match content"
            )
        payload = json.dumps(
            _observation_payload(observation),
            sort_keys=True,
            separators=(",", ":"),
        )
        existing = self._con.execute(
            """
            SELECT payload_json
            FROM portfolio_paper_shadow_observations
            WHERE observation_id = ?
            """,
            [observation.observation_id],
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != payload:
                raise ValueError(
                    "portfolio PAPER observation identity conflict"
                )
            raise ValueError("duplicate portfolio PAPER observation")
        self._con.execute(
            """
            INSERT INTO portfolio_paper_shadow_observations
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                observation.observation_id,
                observation.authorization_id,
                observation.model_id,
                observation.manifest_id,
                observation.selected_solution_id,
                observation.period_start,
                observation.period_end,
                observation.observed_at,
                str(observation.net_return),
                str(observation.implementation_cost_rate),
                payload,
            ],
        )

    def _insert_event(
        self,
        event: PortfolioPaperEnforcementEvent,
    ) -> None:
        if (
            event.event_id
            != portfolio_paper_enforcement_event_identity(event)
        ):
            raise ValueError(
                "portfolio PAPER enforcement event identity does not match content"
            )
        self._con.execute(
            """
            INSERT INTO portfolio_paper_enforcement_events
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                event.event_id,
                event.ordinal,
                event.authorization_id,
                event.occurred_at,
                event.prior_state.value,
                event.new_state.value,
                json.dumps(
                    [item.value for item in event.kill_conditions]
                ),
                event.reason,
                json.dumps(list(event.evidence_references)),
            ],
        )

    def _next_event_ordinal(self, authorization_id: str) -> int:
        row = self._con.execute(
            """
            SELECT COALESCE(MAX(ordinal), 0)
            FROM portfolio_paper_enforcement_events
            WHERE authorization_id = ?
            """,
            [authorization_id],
        ).fetchone()
        return int(row[0]) + 1


def portfolio_paper_shadow_observation_identity(
    observation: PortfolioPaperShadowObservation,
) -> str:
    payload = {
        "authorization_id": observation.authorization_id,
        "model_id": observation.model_id,
        "manifest_id": observation.manifest_id,
        "selected_solution_id": observation.selected_solution_id,
        "period_start": observation.period_start.isoformat(),
        "period_end": observation.period_end.isoformat(),
        "observed_at": observation.observed_at.isoformat(),
        "gross_return": str(observation.gross_return),
        "net_return": str(observation.net_return),
        "implementation_cost_rate": str(
            observation.implementation_cost_rate
        ),
        "expected_implementation_cost_rate": str(
            observation.expected_implementation_cost_rate
        ),
        "implementation_cost_assumption_variance": str(
            observation.implementation_cost_assumption_variance
        ),
        "one_way_turnover": str(observation.one_way_turnover),
        "weights": [
            {
                "security_id": item.security_id,
                "weight": str(item.weight),
            }
            for item in observation.weights
        ],
        "net_exposure": str(observation.net_exposure),
        "gross_exposure": str(observation.gross_exposure),
        "solution_drift_turnover": str(
            observation.solution_drift_turnover
        ),
        "cumulative_net_return": str(
            observation.cumulative_net_return
        ),
        "running_maximum_drawdown": str(
            observation.running_maximum_drawdown
        ),
        "running_average_implementation_cost_rate": str(
            observation.running_average_implementation_cost_rate
        ),
        "source_fact_ids": list(observation.source_fact_ids),
        "triggered_kill_conditions": [
            item.value for item in observation.triggered_kill_conditions
        ],
        "authorization_state_after_record": (
            observation.authorization_state_after_record.value
        ),
        "paper_authority": observation.paper_authority,
        "order_authority": observation.order_authority,
        "capital_authority": observation.capital_authority,
    }
    return _content_id("portfolio-paper-shadow-observation", payload)


def portfolio_paper_enforcement_event_identity(
    event: PortfolioPaperEnforcementEvent,
) -> str:
    payload = {
        "ordinal": event.ordinal,
        "authorization_id": event.authorization_id,
        "occurred_at": event.occurred_at.isoformat(),
        "prior_state": event.prior_state.value,
        "new_state": event.new_state.value,
        "kill_conditions": [
            item.value for item in event.kill_conditions
        ],
        "reason": event.reason,
        "evidence_references": list(event.evidence_references),
    }
    return _content_id("portfolio-paper-enforcement-event", payload)


def _solution_method(
    solution: SelectedPortfolioSolution,
):
    from .portfolio_comparison import PortfolioMethod

    if isinstance(solution, PortfolioSolution):
        if solution.allocator is BaselineAllocator.EQUAL_WEIGHT:
            return PortfolioMethod.EQUAL_WEIGHT
        if solution.allocator is BaselineAllocator.INVERSE_VOLATILITY:
            return PortfolioMethod.INVERSE_VOLATILITY
    elif isinstance(solution, OptimizedPortfolioSolution):
        return PortfolioMethod.MINIMUM_VARIANCE
    elif isinstance(solution, HierarchicalPortfolioSolution):
        if solution.allocator is HierarchicalAllocator.HRP:
            return PortfolioMethod.HRP
        if solution.allocator is HierarchicalAllocator.HERC:
            return PortfolioMethod.HERC
    raise ValueError("unsupported portfolio solution type")


def _authentic_solution(
    solution: SelectedPortfolioSolution,
) -> bool:
    if isinstance(solution, PortfolioSolution):
        return solution.solution_id == portfolio_solution_identity(solution)
    if isinstance(solution, OptimizedPortfolioSolution):
        return (
            solution.solution_id
            == optimized_portfolio_solution_identity(solution)
        )
    if isinstance(solution, HierarchicalPortfolioSolution):
        return (
            solution.solution_id
            == hierarchical_portfolio_solution_identity(solution)
        )
    return False


def _weight_turnover_distance(
    left: tuple[PortfolioWeight, ...],
    right: tuple[PortfolioWeight, ...],
) -> Decimal:
    left_map = {item.security_id: item.weight for item in left}
    right_map = {item.security_id: item.weight for item in right}
    return (
        sum(
            (
                abs(
                    left_map.get(security_id, Decimal("0"))
                    - right_map.get(security_id, Decimal("0"))
                )
                for security_id in set(left_map) | set(right_map)
            ),
            Decimal("0"),
        )
        / Decimal("2")
    )


def _expected_implementation_cost_rate(
    *,
    execution_assumptions: PortfolioPaperExecutionAssumptions,
    one_way_turnover: Decimal,
    weights: tuple[PortfolioWeight, ...],
    period_start: datetime,
    period_end: datetime,
) -> Decimal:
    trading_bps = (
        execution_assumptions.commission_bps
        + execution_assumptions.half_spread_bps
        + execution_assumptions.slippage_bps
        + execution_assumptions.market_impact_bps
    )
    traded_notional = Decimal("2") * one_way_turnover
    trading_cost = traded_notional * trading_bps / Decimal("10000")
    short_exposure = sum(
        (
            -item.weight
            for item in weights
            if item.weight < 0
        ),
        Decimal("0"),
    )
    seconds = Decimal(
        str((period_end - period_start).total_seconds())
    )
    year_fraction = seconds / Decimal("31536000")
    borrow_cost = (
        short_exposure
        * execution_assumptions.annual_borrow_bps
        / Decimal("10000")
        * year_fraction
    )
    return trading_cost + borrow_cost


def _wealth_metrics(
    returns: tuple[Decimal, ...],
) -> tuple[Decimal, Decimal]:
    wealth = Decimal("1")
    peak = Decimal("1")
    maximum_drawdown = Decimal("0")
    for value in returns:
        wealth *= Decimal("1") + value
        if wealth > peak:
            peak = wealth
        drawdown = wealth / peak - Decimal("1")
        if drawdown < maximum_drawdown:
            maximum_drawdown = drawdown
    return wealth - Decimal("1"), maximum_drawdown


def _mean(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        raise ValueError("mean requires values")
    return sum(values, Decimal("0")) / Decimal(len(values))


def _observation_payload(
    observation: PortfolioPaperShadowObservation,
) -> dict[str, object]:
    return {
        "observation_id": observation.observation_id,
        "authorization_id": observation.authorization_id,
        "model_id": observation.model_id,
        "manifest_id": observation.manifest_id,
        "selected_solution_id": observation.selected_solution_id,
        "period_start": observation.period_start.isoformat(),
        "period_end": observation.period_end.isoformat(),
        "observed_at": observation.observed_at.isoformat(),
        "gross_return": str(observation.gross_return),
        "net_return": str(observation.net_return),
        "implementation_cost_rate": str(
            observation.implementation_cost_rate
        ),
        "expected_implementation_cost_rate": str(
            observation.expected_implementation_cost_rate
        ),
        "implementation_cost_assumption_variance": str(
            observation.implementation_cost_assumption_variance
        ),
        "one_way_turnover": str(observation.one_way_turnover),
        "weights": [
            {
                "security_id": item.security_id,
                "weight": str(item.weight),
            }
            for item in observation.weights
        ],
        "net_exposure": str(observation.net_exposure),
        "gross_exposure": str(observation.gross_exposure),
        "solution_drift_turnover": str(
            observation.solution_drift_turnover
        ),
        "cumulative_net_return": str(
            observation.cumulative_net_return
        ),
        "running_maximum_drawdown": str(
            observation.running_maximum_drawdown
        ),
        "running_average_implementation_cost_rate": str(
            observation.running_average_implementation_cost_rate
        ),
        "source_fact_ids": list(observation.source_fact_ids),
        "triggered_kill_conditions": [
            item.value
            for item in observation.triggered_kill_conditions
        ],
        "authorization_state_after_record": (
            observation.authorization_state_after_record.value
        ),
        "paper_authority": observation.paper_authority,
        "order_authority": observation.order_authority,
        "capital_authority": observation.capital_authority,
    }


def _observation_from_payload(
    payload: dict[str, object],
) -> PortfolioPaperShadowObservation:
    return PortfolioPaperShadowObservation(
        observation_id=str(payload["observation_id"]),
        authorization_id=str(payload["authorization_id"]),
        model_id=str(payload["model_id"]),
        manifest_id=str(payload["manifest_id"]),
        selected_solution_id=str(payload["selected_solution_id"]),
        period_start=datetime.fromisoformat(str(payload["period_start"])),
        period_end=datetime.fromisoformat(str(payload["period_end"])),
        observed_at=datetime.fromisoformat(str(payload["observed_at"])),
        gross_return=Decimal(str(payload["gross_return"])),
        net_return=Decimal(str(payload["net_return"])),
        implementation_cost_rate=Decimal(
            str(payload["implementation_cost_rate"])
        ),
        expected_implementation_cost_rate=Decimal(
            str(payload["expected_implementation_cost_rate"])
        ),
        implementation_cost_assumption_variance=Decimal(
            str(payload["implementation_cost_assumption_variance"])
        ),
        one_way_turnover=Decimal(str(payload["one_way_turnover"])),
        weights=tuple(
            PortfolioWeight(
                security_id=str(item["security_id"]),
                weight=Decimal(str(item["weight"])),
            )
            for item in payload["weights"]
        ),
        net_exposure=Decimal(str(payload["net_exposure"])),
        gross_exposure=Decimal(str(payload["gross_exposure"])),
        solution_drift_turnover=Decimal(
            str(payload["solution_drift_turnover"])
        ),
        cumulative_net_return=Decimal(
            str(payload["cumulative_net_return"])
        ),
        running_maximum_drawdown=Decimal(
            str(payload["running_maximum_drawdown"])
        ),
        running_average_implementation_cost_rate=Decimal(
            str(payload["running_average_implementation_cost_rate"])
        ),
        source_fact_ids=tuple(payload["source_fact_ids"]),
        triggered_kill_conditions=tuple(
            PortfolioPaperKillCondition(item)
            for item in payload["triggered_kill_conditions"]
        ),
        authorization_state_after_record=(
            PortfolioPaperAuthorizationState(
                str(payload["authorization_state_after_record"])
            )
        ),
        paper_authority=str(payload["paper_authority"]),
        order_authority=str(payload["order_authority"]),
        capital_authority=str(payload["capital_authority"]),
    )


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()