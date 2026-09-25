from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import TypeAlias

import duckdb

from .model_registry import (
    ModelLifecycleStage,
    ModelRegistryState,
    ResearchRunManifest,
)
from .portfolio_comparison import (
    PortfolioComparisonDossier,
    PortfolioMethod,
    portfolio_comparison_dossier_identity,
)
from .portfolio_construction import (
    BaselineAllocator,
    PortfolioSolution,
    portfolio_solution_identity,
)
from .portfolio_decision import (
    PortfolioDecisionDisposition,
    PortfolioResearchDecision,
    portfolio_research_decision_identity,
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
from .portfolio_robustness import (
    PortfolioRobustnessDossier,
    PortfolioRobustnessState,
    portfolio_robustness_dossier_identity,
)
from .readiness import ProspectiveShadowPermit


SelectedPortfolioSolution: TypeAlias = (
    PortfolioSolution
    | OptimizedPortfolioSolution
    | HierarchicalPortfolioSolution
)


class PortfolioPaperKillCondition(str, Enum):
    DATA_LINEAGE_BREAK = "DATA_LINEAGE_BREAK"
    MODEL_OR_MANIFEST_CHANGE = "MODEL_OR_MANIFEST_CHANGE"
    PORTFOLIO_CONSTRAINT_BREACH = "PORTFOLIO_CONSTRAINT_BREACH"
    MAX_DRAWDOWN_BREACH = "MAX_DRAWDOWN_BREACH"
    TURNOVER_BREACH = "TURNOVER_BREACH"
    IMPLEMENTATION_COST_BREACH = "IMPLEMENTATION_COST_BREACH"
    SOLUTION_DRIFT_BREACH = "SOLUTION_DRIFT_BREACH"


MANDATORY_KILL_CONDITIONS = frozenset(PortfolioPaperKillCondition)


@dataclass(frozen=True)
class PortfolioPaperExecutionAssumptions:
    commission_bps: Decimal
    half_spread_bps: Decimal
    slippage_bps: Decimal
    market_impact_bps: Decimal
    annual_borrow_bps: Decimal
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "commission_bps",
            "half_spread_bps",
            "slippage_bps",
            "market_impact_bps",
            "annual_borrow_bps",
        ):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if not self.rationale.strip():
            raise ValueError("execution-assumption rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError(
                "execution assumptions require evidence references"
            )

    @property
    def assumptions_id(self) -> str:
        return _content_id(
            "portfolio-paper-execution-assumptions",
            {
                "commission_bps": str(self.commission_bps),
                "half_spread_bps": str(self.half_spread_bps),
                "slippage_bps": str(self.slippage_bps),
                "market_impact_bps": str(self.market_impact_bps),
                "annual_borrow_bps": str(self.annual_borrow_bps),
                "rationale": self.rationale,
                "evidence_references": list(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class PortfolioPaperMonitoringPolicy:
    minimum_observations: int
    maximum_drawdown_abs: Decimal
    maximum_one_way_turnover: Decimal
    maximum_average_implementation_cost_rate: Decimal
    maximum_absolute_weight: Decimal
    maximum_gross_exposure: Decimal
    maximum_solution_drift_turnover: Decimal
    kill_conditions: tuple[PortfolioPaperKillCondition, ...]
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.minimum_observations < 2:
            raise ValueError("minimum_observations must be at least 2")
        for name in (
            "maximum_drawdown_abs",
            "maximum_one_way_turnover",
            "maximum_average_implementation_cost_rate",
            "maximum_absolute_weight",
            "maximum_gross_exposure",
            "maximum_solution_drift_turnover",
        ):
            value = getattr(self, name)
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.maximum_drawdown_abs > 1:
            raise ValueError("maximum_drawdown_abs cannot exceed 1")
        if self.maximum_absolute_weight > 1:
            raise ValueError("maximum_absolute_weight cannot exceed 1")
        if self.maximum_solution_drift_turnover > 1:
            raise ValueError(
                "maximum_solution_drift_turnover cannot exceed 1"
            )
        if self.maximum_gross_exposure < 1:
            raise ValueError(
                "shadow PAPER gross-exposure limit must be at least 1"
            )
        supplied = set(self.kill_conditions)
        if len(supplied) != len(self.kill_conditions):
            raise ValueError("duplicate PAPER kill conditions are not allowed")
        if supplied != MANDATORY_KILL_CONDITIONS:
            missing = sorted(
                item.value
                for item in MANDATORY_KILL_CONDITIONS - supplied
            )
            extra = sorted(
                item.value
                for item in supplied - MANDATORY_KILL_CONDITIONS
            )
            raise ValueError(
                "PAPER monitoring must contain the full mandatory kill set; "
                f"missing={missing}, extra={extra}"
            )
        if not self.rationale.strip():
            raise ValueError("PAPER monitoring rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError(
                "PAPER monitoring policy requires evidence references"
            )

    @property
    def policy_id(self) -> str:
        return _content_id(
            "portfolio-paper-monitoring-policy",
            {
                "minimum_observations": self.minimum_observations,
                "maximum_drawdown_abs": str(self.maximum_drawdown_abs),
                "maximum_one_way_turnover": str(
                    self.maximum_one_way_turnover
                ),
                "maximum_average_implementation_cost_rate": str(
                    self.maximum_average_implementation_cost_rate
                ),
                "maximum_absolute_weight": str(
                    self.maximum_absolute_weight
                ),
                "maximum_gross_exposure": str(
                    self.maximum_gross_exposure
                ),
                "maximum_solution_drift_turnover": str(
                    self.maximum_solution_drift_turnover
                ),
                "kill_conditions": sorted(
                    item.value for item in self.kill_conditions
                ),
                "rationale": self.rationale,
                "evidence_references": list(self.evidence_references),
            },
        )


@dataclass(frozen=True)
class PortfolioPaperAuthorization:
    authorization_id: str
    model_id: str
    manifest_id: str
    research_case_id: str
    decision_id: str
    comparison_dossier_id: str
    robustness_dossier_id: str
    permit_id: str
    selected_method: PortfolioMethod
    selected_solution_id: str
    selected_dataset_id: str
    constraint_policy_id: str
    execution_assumptions_id: str
    monitoring_policy_id: str
    authorized_at: datetime
    expires_at: datetime
    portfolio_reviewer: str
    independent_risk_reviewer: str
    rationale: str
    evidence_references: tuple[str, ...]
    lifecycle_stage: str
    paper_authority: str
    order_authority: str
    capital_authority: str
    purpose: str
    caveat: str


class PortfolioPaperAuthorizationEngine:
    PURPOSE = "PROSPECTIVE_PORTFOLIO_SHADOW_ONLY"
    CAVEAT = (
        "This authorization permits only prospective shadow-paper observation of "
        "one exact portfolio method and solution. It does not authorize orders, "
        "broker connectivity, live execution, or real-capital exposure."
    )

    def authorize(
        self,
        *,
        manifest: ResearchRunManifest,
        registry_state: ModelRegistryState,
        permit: ProspectiveShadowPermit,
        comparison: PortfolioComparisonDossier,
        robustness: PortfolioRobustnessDossier,
        decision: PortfolioResearchDecision,
        selected_solution: SelectedPortfolioSolution,
        execution_assumptions: PortfolioPaperExecutionAssumptions,
        monitoring_policy: PortfolioPaperMonitoringPolicy,
        authorized_at: datetime,
        expires_at: datetime,
        portfolio_reviewer: str,
        independent_risk_reviewer: str,
        rationale: str,
        evidence_references: tuple[str, ...],
    ) -> PortfolioPaperAuthorization:
        self._validate_lineage(
            manifest=manifest,
            registry_state=registry_state,
            permit=permit,
            comparison=comparison,
            robustness=robustness,
            decision=decision,
            selected_solution=selected_solution,
        )
        if authorized_at.tzinfo is None or expires_at.tzinfo is None:
            raise ValueError(
                "authorization timestamps must be timezone-aware"
            )
        if expires_at <= authorized_at:
            raise ValueError("PAPER authorization must have a future expiry")
        if (
            authorized_at < decision.reviewed_at
            or authorized_at < permit.issued_at
            or authorized_at < registry_state.updated_at
            or authorized_at < selected_solution.decision_time
        ):
            raise ValueError(
                "PAPER authorization cannot predate its evidence or solution"
            )
        if not portfolio_reviewer.strip() or not independent_risk_reviewer.strip():
            raise ValueError(
                "portfolio reviewer and independent risk reviewer are required"
            )
        if portfolio_reviewer.strip() == independent_risk_reviewer.strip():
            raise ValueError(
                "portfolio reviewer and independent risk reviewer must differ"
            )
        if not rationale.strip():
            raise ValueError("PAPER authorization rationale is required")
        if not evidence_references or not all(
            item.strip() for item in evidence_references
        ):
            raise ValueError(
                "PAPER authorization requires evidence references"
            )

        max_solution_weight = max(
            abs(item.weight) for item in selected_solution.weights
        )
        if max_solution_weight > monitoring_policy.maximum_absolute_weight:
            raise ValueError(
                "selected solution already exceeds PAPER weight threshold"
            )
        if (
            selected_solution.gross_exposure
            > monitoring_policy.maximum_gross_exposure
        ):
            raise ValueError(
                "selected solution already exceeds PAPER gross-exposure threshold"
            )
        if (
            selected_solution.one_way_turnover
            > monitoring_policy.maximum_one_way_turnover
        ):
            raise ValueError(
                "selected solution already exceeds PAPER turnover threshold"
            )

        payload = {
            "model_id": manifest.model_id,
            "manifest_id": manifest.manifest_id,
            "research_case_id": manifest.research_case_id,
            "decision_id": decision.decision_id,
            "comparison_dossier_id": comparison.dossier_id,
            "robustness_dossier_id": robustness.dossier_id,
            "permit_id": permit.permit_id,
            "selected_method": decision.selected_method.value,
            "selected_solution_id": selected_solution.solution_id,
            "selected_dataset_id": selected_solution.dataset_id,
            "constraint_policy_id": selected_solution.constraint_policy_id,
            "execution_assumptions_id": execution_assumptions.assumptions_id,
            "monitoring_policy_id": monitoring_policy.policy_id,
            "authorized_at": authorized_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "portfolio_reviewer": portfolio_reviewer.strip(),
            "independent_risk_reviewer": independent_risk_reviewer.strip(),
            "rationale": rationale.strip(),
            "evidence_references": list(evidence_references),
            "lifecycle_stage": ModelLifecycleStage.PAPER.value,
            "paper_authority": "SHADOW_ONLY",
            "order_authority": "NONE",
            "capital_authority": "NONE",
            "purpose": self.PURPOSE,
        }
        return PortfolioPaperAuthorization(
            authorization_id=_content_id(
                "portfolio-paper-authorization",
                payload,
            ),
            model_id=manifest.model_id,
            manifest_id=manifest.manifest_id,
            research_case_id=manifest.research_case_id,
            decision_id=decision.decision_id,
            comparison_dossier_id=comparison.dossier_id,
            robustness_dossier_id=robustness.dossier_id,
            permit_id=permit.permit_id,
            selected_method=decision.selected_method,
            selected_solution_id=selected_solution.solution_id,
            selected_dataset_id=selected_solution.dataset_id,
            constraint_policy_id=selected_solution.constraint_policy_id,
            execution_assumptions_id=execution_assumptions.assumptions_id,
            monitoring_policy_id=monitoring_policy.policy_id,
            authorized_at=authorized_at,
            expires_at=expires_at,
            portfolio_reviewer=portfolio_reviewer.strip(),
            independent_risk_reviewer=independent_risk_reviewer.strip(),
            rationale=rationale.strip(),
            evidence_references=evidence_references,
            lifecycle_stage=ModelLifecycleStage.PAPER.value,
            paper_authority="SHADOW_ONLY",
            order_authority="NONE",
            capital_authority="NONE",
            purpose=self.PURPOSE,
            caveat=self.CAVEAT,
        )

    @staticmethod
    def _validate_lineage(
        *,
        manifest: ResearchRunManifest,
        registry_state: ModelRegistryState,
        permit: ProspectiveShadowPermit,
        comparison: PortfolioComparisonDossier,
        robustness: PortfolioRobustnessDossier,
        decision: PortfolioResearchDecision,
        selected_solution: SelectedPortfolioSolution,
    ) -> None:
        if manifest.eligible_stage is not ModelLifecycleStage.PAPER:
            raise ValueError(
                "portfolio PAPER authorization requires PAPER-eligible manifest"
            )
        if registry_state.stage is not ModelLifecycleStage.PAPER:
            raise ValueError(
                "portfolio PAPER authorization requires current PAPER registry state"
            )
        if (
            registry_state.model_id != manifest.model_id
            or registry_state.manifest_id != manifest.manifest_id
        ):
            raise ValueError("registry state does not match exact manifest")
        if manifest.shadow_permit_id != permit.permit_id:
            raise ValueError("manifest is bound to another shadow permit")
        if permit.purpose != "PROSPECTIVE_SHADOW_ONLY":
            raise ValueError("unsupported research shadow permit purpose")
        if (
            permit.case_id != manifest.research_case_id
            or permit.case_dossier_fingerprint
            != manifest.case_dossier_fingerprint
        ):
            raise ValueError(
                "shadow permit does not match manifest Research Case lineage"
            )

        if comparison.dossier_id != portfolio_comparison_dossier_identity(
            comparison
        ):
            raise ValueError("comparison dossier identity mismatch")
        if robustness.dossier_id != portfolio_robustness_dossier_identity(
            robustness
        ):
            raise ValueError("robustness dossier identity mismatch")
        if decision.decision_id != portfolio_research_decision_identity(
            decision
        ):
            raise ValueError("portfolio decision identity mismatch")
        if (
            comparison.model_id != manifest.model_id
            or comparison.manifest_id != manifest.manifest_id
            or robustness.model_id != manifest.model_id
            or robustness.manifest_id != manifest.manifest_id
            or decision.model_id != manifest.model_id
            or decision.manifest_id != manifest.manifest_id
        ):
            raise ValueError(
                "portfolio research artifacts belong to another Research Run"
            )
        if robustness.comparison_dossier_id != comparison.dossier_id:
            raise ValueError(
                "robustness dossier belongs to another comparison"
            )
        if (
            decision.comparison_dossier_id != comparison.dossier_id
            or decision.robustness_dossier_id != robustness.dossier_id
        ):
            raise ValueError(
                "portfolio decision does not bind supplied dossiers"
            )
        if robustness.state is not PortfolioRobustnessState.WITHIN_POLICY:
            raise ValueError(
                "portfolio PAPER authorization requires robustness WITHIN_POLICY"
            )
        if (
            decision.disposition
            is not PortfolioDecisionDisposition.RECOMMEND_FOR_PAPER_REVIEW
        ):
            raise ValueError(
                "portfolio decision did not recommend PAPER review"
            )
        if decision.selected_method is None:
            raise ValueError("portfolio decision has no selected method")
        if decision.paper_authority != "NONE" or decision.capital_authority != "NONE":
            raise ValueError(
                "portfolio research decision unexpectedly carries authority"
            )
        if decision.unresolved_objections:
            raise ValueError(
                "portfolio PAPER authorization cannot carry unresolved objections"
            )
        if selected_solution.model_id != manifest.model_id:
            raise ValueError("selected solution belongs to another model")
        if selected_solution.manifest_id != manifest.manifest_id:
            raise ValueError("selected solution belongs to another manifest")
        if (
            selected_solution.constraint_policy_id
            != comparison.constraint_policy_id
        ):
            raise ValueError(
                "selected solution uses another portfolio constraint policy"
            )
        if selected_solution.capital_authority != "NONE":
            raise ValueError(
                "selected research solution unexpectedly carries capital authority"
            )
        method = _solution_method(selected_solution)
        if method is not decision.selected_method:
            raise ValueError(
                "selected solution method differs from human research decision"
            )
        if not _authentic_solution(selected_solution):
            raise ValueError(
                "selected portfolio solution identity does not match content"
            )
        evaluation = next(
            (
                item
                for item in comparison.evaluations
                if item.method is decision.selected_method
            ),
            None,
        )
        if evaluation is None:
            raise ValueError(
                "selected method was not evaluated in common OOS dossier"
            )


def portfolio_paper_authorization_identity(
    authorization: PortfolioPaperAuthorization,
) -> str:
    payload = {
        "model_id": authorization.model_id,
        "manifest_id": authorization.manifest_id,
        "research_case_id": authorization.research_case_id,
        "decision_id": authorization.decision_id,
        "comparison_dossier_id": authorization.comparison_dossier_id,
        "robustness_dossier_id": authorization.robustness_dossier_id,
        "permit_id": authorization.permit_id,
        "selected_method": authorization.selected_method.value,
        "selected_solution_id": authorization.selected_solution_id,
        "selected_dataset_id": authorization.selected_dataset_id,
        "constraint_policy_id": authorization.constraint_policy_id,
        "execution_assumptions_id": authorization.execution_assumptions_id,
        "monitoring_policy_id": authorization.monitoring_policy_id,
        "authorized_at": authorization.authorized_at.isoformat(),
        "expires_at": authorization.expires_at.isoformat(),
        "portfolio_reviewer": authorization.portfolio_reviewer,
        "independent_risk_reviewer": authorization.independent_risk_reviewer,
        "rationale": authorization.rationale,
        "evidence_references": list(authorization.evidence_references),
        "lifecycle_stage": authorization.lifecycle_stage,
        "paper_authority": authorization.paper_authority,
        "order_authority": authorization.order_authority,
        "capital_authority": authorization.capital_authority,
        "purpose": authorization.purpose,
    }
    return _content_id("portfolio-paper-authorization", payload)


class PortfolioPaperAuthorizationStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_paper_authorizations (
                authorization_id VARCHAR PRIMARY KEY,
                model_id VARCHAR NOT NULL,
                manifest_id VARCHAR NOT NULL,
                decision_id VARCHAR NOT NULL,
                permit_id VARCHAR NOT NULL,
                selected_method VARCHAR NOT NULL,
                selected_solution_id VARCHAR NOT NULL,
                authorized_at TIMESTAMPTZ NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                purpose VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, authorization: PortfolioPaperAuthorization) -> bool:
        if (
            authorization.authorization_id
            != portfolio_paper_authorization_identity(authorization)
        ):
            raise ValueError(
                "portfolio PAPER authorization identity does not match content"
            )
        payload = json.dumps(
            {
                "authorization_id": authorization.authorization_id,
                "model_id": authorization.model_id,
                "manifest_id": authorization.manifest_id,
                "research_case_id": authorization.research_case_id,
                "decision_id": authorization.decision_id,
                "comparison_dossier_id": authorization.comparison_dossier_id,
                "robustness_dossier_id": authorization.robustness_dossier_id,
                "permit_id": authorization.permit_id,
                "selected_method": authorization.selected_method.value,
                "selected_solution_id": authorization.selected_solution_id,
                "selected_dataset_id": authorization.selected_dataset_id,
                "constraint_policy_id": authorization.constraint_policy_id,
                "execution_assumptions_id": authorization.execution_assumptions_id,
                "monitoring_policy_id": authorization.monitoring_policy_id,
                "authorized_at": authorization.authorized_at.isoformat(),
                "expires_at": authorization.expires_at.isoformat(),
                "portfolio_reviewer": authorization.portfolio_reviewer,
                "independent_risk_reviewer": authorization.independent_risk_reviewer,
                "rationale": authorization.rationale,
                "evidence_references": list(
                    authorization.evidence_references
                ),
                "lifecycle_stage": authorization.lifecycle_stage,
                "paper_authority": authorization.paper_authority,
                "order_authority": authorization.order_authority,
                "capital_authority": authorization.capital_authority,
                "purpose": authorization.purpose,
                "caveat": authorization.caveat,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        row = self._con.execute(
            """
            SELECT payload_json
            FROM portfolio_paper_authorizations
            WHERE authorization_id = ?
            """,
            [authorization.authorization_id],
        ).fetchone()
        if row is not None:
            if str(row[0]) != payload:
                raise ValueError(
                    "portfolio PAPER authorization identity conflict"
                )
            return False
        self._con.execute(
            """
            INSERT INTO portfolio_paper_authorizations
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                authorization.authorization_id,
                authorization.model_id,
                authorization.manifest_id,
                authorization.decision_id,
                authorization.permit_id,
                authorization.selected_method.value,
                authorization.selected_solution_id,
                authorization.authorized_at,
                authorization.expires_at,
                authorization.purpose,
                payload,
            ],
        )
        return True

    def close(self) -> None:
        self._con.close()


def _solution_method(
    solution: SelectedPortfolioSolution,
) -> PortfolioMethod:
    if isinstance(solution, PortfolioSolution):
        if solution.allocator is BaselineAllocator.EQUAL_WEIGHT:
            return PortfolioMethod.EQUAL_WEIGHT
        if solution.allocator is BaselineAllocator.INVERSE_VOLATILITY:
            return PortfolioMethod.INVERSE_VOLATILITY
        raise ValueError("unsupported baseline portfolio allocator")
    if isinstance(solution, OptimizedPortfolioSolution):
        return PortfolioMethod.MINIMUM_VARIANCE
    if isinstance(solution, HierarchicalPortfolioSolution):
        if solution.allocator is HierarchicalAllocator.HRP:
            return PortfolioMethod.HRP
        if solution.allocator is HierarchicalAllocator.HERC:
            return PortfolioMethod.HERC
    raise ValueError("unsupported selected portfolio solution type")


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


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
