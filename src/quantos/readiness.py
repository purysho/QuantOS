from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

import duckdb

from .case_dossier import CaseDossier
from .case_reviews import CaseReviewLedger
from .professional_reviews import ReviewPanelState


class ResearchReadinessState(str, Enum):
    INCOMPLETE = "INCOMPLETE"
    BLOCKED = "BLOCKED"
    CONDITIONS_OPEN = "CONDITIONS_OPEN"
    READY_FOR_PROSPECTIVE_SHADOW = "READY_FOR_PROSPECTIVE_SHADOW"


@dataclass(frozen=True)
class ReadinessAssessment:
    case_id: str
    case_dossier_fingerprint: str
    scenario_set_id: str
    state: ResearchReadinessState
    panel_state: ReviewPanelState
    reasons: tuple[str, ...]
    caveat: str


@dataclass(frozen=True)
class ProspectiveShadowPermit:
    permit_id: str
    case_id: str
    case_dossier_fingerprint: str
    scenario_set_id: str
    issued_at: datetime
    issued_by: str
    purpose: str


class ResearchReadinessGate:
    """Map exact case-review state to research-only shadow readiness."""

    CAVEAT = (
        "Research readiness is a workflow state for prospective shadow evaluation "
        "only. It is not an investment recommendation, expected-return claim, "
        "trade authorization, portfolio instruction, or permission to allocate "
        "real capital."
    )

    def assess(
        self,
        dossier: CaseDossier,
        *,
        reviews: CaseReviewLedger,
    ) -> ReadinessAssessment:
        panel = reviews.panel(dossier)
        reasons: list[str] = []

        if panel.state is ReviewPanelState.BLOCKING_OBJECTION_PRESENT:
            state = ResearchReadinessState.BLOCKED
            reasons.extend(
                f"unresolved blocking case review: {review_id}"
                for review_id in panel.blocking_review_ids
            )
        elif panel.state is ReviewPanelState.INCOMPLETE:
            state = ResearchReadinessState.INCOMPLETE
            reasons.extend(
                f"missing professional role review: {role.value}"
                for role in panel.missing_roles
            )
        elif panel.state is ReviewPanelState.CONDITIONS_OPEN:
            state = ResearchReadinessState.CONDITIONS_OPEN
            reasons.extend(
                f"unresolved conditional case review: {review_id}"
                for review_id in panel.conditional_review_ids
            )
        elif panel.state is ReviewPanelState.REVIEW_SET_COMPLETE:
            state = ResearchReadinessState.READY_FOR_PROSPECTIVE_SHADOW
            reasons.append(
                "all six professional roles are represented for this exact "
                "case-dossier fingerprint with no unresolved blocking or "
                "conditional reviews"
            )
        else:
            raise ValueError(f"unsupported review panel state: {panel.state}")

        return ReadinessAssessment(
            case_id=dossier.case.case_id,
            case_dossier_fingerprint=dossier.case_dossier_fingerprint,
            scenario_set_id=dossier.scenario_set.scenario_set_id,
            state=state,
            panel_state=panel.state,
            reasons=tuple(reasons),
            caveat=self.CAVEAT,
        )


def make_shadow_permit_id(
    *,
    case_id: str,
    case_dossier_fingerprint: str,
    scenario_set_id: str,
    issued_by: str,
) -> str:
    payload = {
        "case_id": case_id,
        "case_dossier_fingerprint": case_dossier_fingerprint,
        "scenario_set_id": scenario_set_id,
        "issued_by": issued_by,
        "purpose": "PROSPECTIVE_SHADOW_ONLY",
    }
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "shadow-permit:" + hashlib.sha256(material).hexdigest()


class ResearchReadinessLedger:
    """Persist research-only permits. No live-execution authority exists here."""

    PURPOSE = "PROSPECTIVE_SHADOW_ONLY"

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS shadow_research_permits (
                permit_id VARCHAR PRIMARY KEY,
                case_id VARCHAR NOT NULL,
                case_dossier_fingerprint VARCHAR NOT NULL,
                scenario_set_id VARCHAR NOT NULL,
                issued_at TIMESTAMPTZ NOT NULL,
                issued_by VARCHAR NOT NULL,
                purpose VARCHAR NOT NULL
            )
            """
        )

    def issue(
        self,
        *,
        assessment: ReadinessAssessment,
        issued_at: datetime,
        issued_by: str,
    ) -> ProspectiveShadowPermit:
        if assessment.state is not ResearchReadinessState.READY_FOR_PROSPECTIVE_SHADOW:
            raise ValueError(
                "shadow permit requires READY_FOR_PROSPECTIVE_SHADOW assessment"
            )
        if issued_at.tzinfo is None:
            raise ValueError("issued_at must be timezone-aware")
        if not issued_by.strip():
            raise ValueError("issued_by is required")
        if not assessment.case_dossier_fingerprint.startswith("case-dossier:"):
            raise ValueError("invalid case dossier fingerprint")

        permit_id = make_shadow_permit_id(
            case_id=assessment.case_id,
            case_dossier_fingerprint=assessment.case_dossier_fingerprint,
            scenario_set_id=assessment.scenario_set_id,
            issued_by=issued_by.strip(),
        )
        permit = ProspectiveShadowPermit(
            permit_id=permit_id,
            case_id=assessment.case_id,
            case_dossier_fingerprint=assessment.case_dossier_fingerprint,
            scenario_set_id=assessment.scenario_set_id,
            issued_at=issued_at,
            issued_by=issued_by.strip(),
            purpose=self.PURPOSE,
        )
        existing = self.get(permit_id)
        if existing is not None:
            return existing

        self._con.execute(
            """
            INSERT INTO shadow_research_permits
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                permit.permit_id,
                permit.case_id,
                permit.case_dossier_fingerprint,
                permit.scenario_set_id,
                permit.issued_at,
                permit.issued_by,
                permit.purpose,
            ],
        )
        return permit

    def get(self, permit_id: str) -> ProspectiveShadowPermit | None:
        row = self._con.execute(
            """
            SELECT permit_id, case_id, case_dossier_fingerprint,
                   scenario_set_id, issued_at, issued_by, purpose
            FROM shadow_research_permits
            WHERE permit_id = ?
            """,
            [permit_id],
        ).fetchone()
        if row is None:
            return None
        return ProspectiveShadowPermit(
            permit_id=str(row[0]),
            case_id=str(row[1]),
            case_dossier_fingerprint=str(row[2]),
            scenario_set_id=str(row[3]),
            issued_at=row[4],
            issued_by=str(row[5]),
            purpose=str(row[6]),
        )

    @staticmethod
    def applies_to(
        permit: ProspectiveShadowPermit,
        dossier: CaseDossier,
    ) -> bool:
        return (
            permit.purpose == ResearchReadinessLedger.PURPOSE
            and permit.case_id == dossier.case.case_id
            and permit.case_dossier_fingerprint
            == dossier.case_dossier_fingerprint
            and permit.scenario_set_id
            == dossier.scenario_set.scenario_set_id
        )

    def close(self) -> None:
        self._con.close()
