from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

import duckdb

from .portfolio_comparison import (
    PortfolioComparisonDossier,
    PortfolioMethod,
    portfolio_comparison_dossier_identity,
)
from .portfolio_robustness import (
    PortfolioRobustnessDossier,
    PortfolioRobustnessState,
    portfolio_robustness_dossier_identity,
)


class PortfolioDecisionDisposition(str, Enum):
    RECOMMEND_FOR_PAPER_REVIEW = "RECOMMEND_FOR_PAPER_REVIEW"
    DEFER = "DEFER"
    REJECT = "REJECT"


class MethodAssessmentState(str, Enum):
    SELECTED = "SELECTED"
    NOT_SELECTED = "NOT_SELECTED"


@dataclass(frozen=True)
class MethodAssessment:
    method: PortfolioMethod
    state: MethodAssessmentState
    rationale: str
    evidence_references: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.rationale.strip():
            raise ValueError("method assessment rationale is required")
        if not self.evidence_references or not all(
            item.strip() for item in self.evidence_references
        ):
            raise ValueError(
                "method assessment requires evidence references"
            )


@dataclass(frozen=True)
class PortfolioResearchDecision:
    decision_id: str
    model_id: str
    manifest_id: str
    comparison_dossier_id: str
    robustness_dossier_id: str
    reviewed_at: datetime
    reviewer: str
    independent_challenger: str
    disposition: PortfolioDecisionDisposition
    selected_method: PortfolioMethod | None
    method_assessments: tuple[MethodAssessment, ...]
    tradeoffs: tuple[str, ...]
    challenger_objections: tuple[str, ...]
    unresolved_objections: tuple[str, ...]
    rationale: str
    evidence_references: tuple[str, ...]
    method_selection_origin: str
    paper_authority: str
    capital_authority: str
    caveat: str


class PortfolioResearchDecisionEngine:
    CAVEAT = (
        "This is a human research-method decision packet. A recommendation for "
        "PAPER review does not create a PAPER permit, change model lifecycle state, "
        "authorize trading, or imply future investment performance."
    )

    def decide(
        self,
        *,
        comparison: PortfolioComparisonDossier,
        robustness: PortfolioRobustnessDossier,
        reviewed_at: datetime,
        reviewer: str,
        independent_challenger: str,
        disposition: PortfolioDecisionDisposition,
        method_assessments: tuple[MethodAssessment, ...],
        tradeoffs: tuple[str, ...],
        challenger_objections: tuple[str, ...],
        unresolved_objections: tuple[str, ...],
        rationale: str,
        evidence_references: tuple[str, ...],
    ) -> PortfolioResearchDecision:
        if reviewed_at.tzinfo is None:
            raise ValueError("decision reviewed_at must be timezone-aware")
        if not reviewer.strip() or not independent_challenger.strip():
            raise ValueError("reviewer and independent challenger are required")
        if reviewer.strip() == independent_challenger.strip():
            raise ValueError(
                "reviewer and independent challenger must be different people"
            )
        if not rationale.strip():
            raise ValueError("portfolio decision rationale is required")
        if not tradeoffs or not all(item.strip() for item in tradeoffs):
            raise ValueError("portfolio decision requires explicit trade-offs")
        if not evidence_references or not all(
            item.strip() for item in evidence_references
        ):
            raise ValueError("portfolio decision requires evidence references")
        if comparison.dossier_id != portfolio_comparison_dossier_identity(
            comparison
        ):
            raise ValueError("comparison dossier identity does not match content")
        if robustness.dossier_id != portfolio_robustness_dossier_identity(
            robustness
        ):
            raise ValueError("robustness dossier identity does not match content")
        if robustness.comparison_dossier_id != comparison.dossier_id:
            raise ValueError("robustness dossier belongs to another comparison")
        if (
            robustness.model_id != comparison.model_id
            or robustness.manifest_id != comparison.manifest_id
            or robustness.constraint_policy_id
            != comparison.constraint_policy_id
        ):
            raise ValueError(
                "robustness and comparison lineage do not match"
            )
        if (
            comparison.selection_authority != "NONE"
            or comparison.capital_authority != "NONE"
            or robustness.selection_authority != "NONE"
            or robustness.capital_authority != "NONE"
        ):
            raise ValueError(
                "upstream portfolio artifacts unexpectedly carry authority"
            )

        evaluated_methods = {
            item.method for item in comparison.evaluations
        }
        assessed_methods = {item.method for item in method_assessments}
        if len(method_assessments) != len(assessed_methods):
            raise ValueError("each portfolio method must be assessed once")
        if assessed_methods != evaluated_methods:
            raise ValueError(
                "decision must assess every and only evaluated portfolio method"
            )

        selected = tuple(
            item.method
            for item in method_assessments
            if item.state is MethodAssessmentState.SELECTED
        )
        objections = tuple(item.strip() for item in challenger_objections)
        unresolved = tuple(item.strip() for item in unresolved_objections)
        if any(not item for item in objections + unresolved):
            raise ValueError("challenger objections cannot be blank")
        if any(item not in objections for item in unresolved):
            raise ValueError(
                "every unresolved objection must appear in challenger objections"
            )

        selected_method: PortfolioMethod | None = None
        if (
            disposition
            is PortfolioDecisionDisposition.RECOMMEND_FOR_PAPER_REVIEW
        ):
            if robustness.state is not PortfolioRobustnessState.WITHIN_POLICY:
                raise ValueError(
                    "PAPER review recommendation requires robustness WITHIN_POLICY"
                )
            if len(selected) != 1:
                raise ValueError(
                    "PAPER review recommendation requires exactly one selected method"
                )
            if unresolved:
                raise ValueError(
                    "PAPER review recommendation cannot carry unresolved objections"
                )
            selected_method = selected[0]
        else:
            if selected:
                raise ValueError(
                    "DEFER/REJECT decisions cannot silently select a method"
                )
            if (
                disposition is PortfolioDecisionDisposition.DEFER
                and not unresolved
            ):
                raise ValueError(
                    "DEFER requires at least one unresolved challenger objection"
                )

        payload = {
            "model_id": comparison.model_id,
            "manifest_id": comparison.manifest_id,
            "comparison_dossier_id": comparison.dossier_id,
            "robustness_dossier_id": robustness.dossier_id,
            "reviewed_at": reviewed_at.isoformat(),
            "reviewer": reviewer.strip(),
            "independent_challenger": independent_challenger.strip(),
            "disposition": disposition.value,
            "selected_method": (
                selected_method.value
                if selected_method is not None
                else None
            ),
            "method_assessments": [
                {
                    "method": item.method.value,
                    "state": item.state.value,
                    "rationale": item.rationale,
                    "evidence_references": list(
                        item.evidence_references
                    ),
                }
                for item in sorted(
                    method_assessments,
                    key=lambda item: item.method.value,
                )
            ],
            "tradeoffs": list(tradeoffs),
            "challenger_objections": list(objections),
            "unresolved_objections": list(unresolved),
            "rationale": rationale.strip(),
            "evidence_references": list(evidence_references),
            "method_selection_origin": "HUMAN_REVIEW",
            "paper_authority": "NONE",
            "capital_authority": "NONE",
        }
        return PortfolioResearchDecision(
            decision_id=_content_id(
                "portfolio-research-decision",
                payload,
            ),
            model_id=comparison.model_id,
            manifest_id=comparison.manifest_id,
            comparison_dossier_id=comparison.dossier_id,
            robustness_dossier_id=robustness.dossier_id,
            reviewed_at=reviewed_at,
            reviewer=reviewer.strip(),
            independent_challenger=independent_challenger.strip(),
            disposition=disposition,
            selected_method=selected_method,
            method_assessments=tuple(
                sorted(
                    method_assessments,
                    key=lambda item: item.method.value,
                )
            ),
            tradeoffs=tradeoffs,
            challenger_objections=objections,
            unresolved_objections=unresolved,
            rationale=rationale.strip(),
            evidence_references=evidence_references,
            method_selection_origin="HUMAN_REVIEW",
            paper_authority="NONE",
            capital_authority="NONE",
            caveat=self.CAVEAT,
        )


def portfolio_research_decision_identity(
    decision: PortfolioResearchDecision,
) -> str:
    payload = {
        "model_id": decision.model_id,
        "manifest_id": decision.manifest_id,
        "comparison_dossier_id": decision.comparison_dossier_id,
        "robustness_dossier_id": decision.robustness_dossier_id,
        "reviewed_at": decision.reviewed_at.isoformat(),
        "reviewer": decision.reviewer,
        "independent_challenger": decision.independent_challenger,
        "disposition": decision.disposition.value,
        "selected_method": (
            decision.selected_method.value
            if decision.selected_method is not None
            else None
        ),
        "method_assessments": [
            {
                "method": item.method.value,
                "state": item.state.value,
                "rationale": item.rationale,
                "evidence_references": list(item.evidence_references),
            }
            for item in decision.method_assessments
        ],
        "tradeoffs": list(decision.tradeoffs),
        "challenger_objections": list(decision.challenger_objections),
        "unresolved_objections": list(decision.unresolved_objections),
        "rationale": decision.rationale,
        "evidence_references": list(decision.evidence_references),
        "method_selection_origin": decision.method_selection_origin,
        "paper_authority": decision.paper_authority,
        "capital_authority": decision.capital_authority,
    }
    return _content_id("portfolio-research-decision", payload)


class PortfolioResearchDecisionStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS portfolio_research_decisions (
                decision_id VARCHAR PRIMARY KEY,
                model_id VARCHAR NOT NULL,
                manifest_id VARCHAR NOT NULL,
                comparison_dossier_id VARCHAR NOT NULL,
                robustness_dossier_id VARCHAR NOT NULL,
                disposition VARCHAR NOT NULL,
                selected_method VARCHAR,
                reviewed_at TIMESTAMPTZ NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, decision: PortfolioResearchDecision) -> bool:
        if (
            decision.decision_id
            != portfolio_research_decision_identity(decision)
        ):
            raise ValueError(
                "portfolio research decision identity does not match content"
            )
        payload = json.dumps(
            {
                "decision_id": decision.decision_id,
                "model_id": decision.model_id,
                "manifest_id": decision.manifest_id,
                "comparison_dossier_id": decision.comparison_dossier_id,
                "robustness_dossier_id": decision.robustness_dossier_id,
                "reviewed_at": decision.reviewed_at.isoformat(),
                "reviewer": decision.reviewer,
                "independent_challenger": decision.independent_challenger,
                "disposition": decision.disposition.value,
                "selected_method": (
                    decision.selected_method.value
                    if decision.selected_method is not None
                    else None
                ),
                "method_assessments": [
                    {
                        "method": item.method.value,
                        "state": item.state.value,
                        "rationale": item.rationale,
                        "evidence_references": list(
                            item.evidence_references
                        ),
                    }
                    for item in decision.method_assessments
                ],
                "tradeoffs": list(decision.tradeoffs),
                "challenger_objections": list(
                    decision.challenger_objections
                ),
                "unresolved_objections": list(
                    decision.unresolved_objections
                ),
                "rationale": decision.rationale,
                "evidence_references": list(
                    decision.evidence_references
                ),
                "method_selection_origin": (
                    decision.method_selection_origin
                ),
                "paper_authority": decision.paper_authority,
                "capital_authority": decision.capital_authority,
                "caveat": decision.caveat,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        row = self._con.execute(
            """
            SELECT payload_json
            FROM portfolio_research_decisions
            WHERE decision_id = ?
            """,
            [decision.decision_id],
        ).fetchone()
        if row is not None:
            if str(row[0]) != payload:
                raise ValueError(
                    "portfolio research decision identity conflict"
                )
            return False
        self._con.execute(
            """
            INSERT INTO portfolio_research_decisions
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                decision.decision_id,
                decision.model_id,
                decision.manifest_id,
                decision.comparison_dossier_id,
                decision.robustness_dossier_id,
                decision.disposition.value,
                (
                    decision.selected_method.value
                    if decision.selected_method is not None
                    else None
                ),
                decision.reviewed_at,
                payload,
            ],
        )
        return True

    def close(self) -> None:
        self._con.close()


def _content_id(prefix: str, payload: object) -> str:
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{prefix}:" + hashlib.sha256(material).hexdigest()
