from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

import duckdb

from .reasoning_dossier import EvidenceDossier


class ProfessionalRole(str, Enum):
    FUNDAMENTAL_ANALYST = "FUNDAMENTAL_ANALYST"
    QUANT_RESEARCHER = "QUANT_RESEARCHER"
    PORTFOLIO_MANAGER = "PORTFOLIO_MANAGER"
    RISK_OFFICER = "RISK_OFFICER"
    EXECUTION_TRADER = "EXECUTION_TRADER"
    RED_TEAM = "RED_TEAM"


class ReviewDisposition(str, Enum):
    NO_OBJECTION = "NO_OBJECTION"
    CONDITIONAL = "CONDITIONAL"
    BLOCKING_OBJECTION = "BLOCKING_OBJECTION"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ReviewPanelState(str, Enum):
    BLOCKING_OBJECTION_PRESENT = "BLOCKING_OBJECTION_PRESENT"
    INCOMPLETE = "INCOMPLETE"
    CONDITIONS_OPEN = "CONDITIONS_OPEN"
    REVIEW_SET_COMPLETE = "REVIEW_SET_COMPLETE"


ROLE_CHECKS: dict[ProfessionalRole, tuple[str, ...]] = {
    ProfessionalRole.FUNDAMENTAL_ANALYST: (
        "economic_interpretation",
        "accounting_or_measurement_risk",
        "scope_and_alternative_explanations",
    ),
    ProfessionalRole.QUANT_RESEARCHER: (
        "identification_and_statistics",
        "data_leakage_and_multiple_testing",
        "replication_and_regime_stability",
    ),
    ProfessionalRole.PORTFOLIO_MANAGER: (
        "portfolio_interaction",
        "sizing_and_concentration_assumptions",
        "diversification_and_correlation_risk",
    ),
    ProfessionalRole.RISK_OFFICER: (
        "failure_mechanisms",
        "tail_regime_and_liquidity_risk",
        "model_and_data_risk",
    ),
    ProfessionalRole.EXECUTION_TRADER: (
        "implementability",
        "cost_slippage_and_capacity",
        "latency_liquidity_and_market_impact",
    ),
    ProfessionalRole.RED_TEAM: (
        "strongest_counterargument",
        "hidden_assumptions_and_falsifiers",
        "missing_or_conflicting_evidence",
    ),
}


@dataclass(frozen=True)
class RoleReview:
    review_id: str
    claim_id: str
    dossier_fingerprint: str
    dossier_posture: str
    role: ProfessionalRole
    reviewer: str
    recorded_at: datetime
    disposition: ReviewDisposition
    check_notes: dict[str, str]
    findings: tuple[str, ...]
    objections: tuple[str, ...]
    required_followups: tuple[str, ...]


@dataclass(frozen=True)
class ReviewPanel:
    claim_id: str
    dossier_fingerprint: str
    state: ReviewPanelState
    reviews: tuple[RoleReview, ...]
    represented_roles: tuple[ProfessionalRole, ...]
    missing_roles: tuple[ProfessionalRole, ...]
    blocking_review_ids: tuple[str, ...]
    conditional_review_ids: tuple[str, ...]
    caveat: str


def dossier_fingerprint(dossier: EvidenceDossier) -> str:
    payload = {
        "claim_id": dossier.claim_id,
        "posture": dossier.posture.value,
        "supporting": sorted(card.claim_id for card in dossier.context.supporting),
        "limiting": sorted(card.claim_id for card in dossier.context.limiting),
        "contradicting": sorted(card.claim_id for card in dossier.context.contradicting),
        "extensions": sorted(card.claim_id for card in dossier.context.extensions),
        "replications": sorted(
            record.replication_id for record in dossier.context.replications
        ),
        "flags": sorted(flag.code for flag in dossier.flags),
    }
    material = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "dossier:" + hashlib.sha256(material).hexdigest()


def make_review_id(
    *,
    claim_id: str,
    dossier_fingerprint: str,
    role: ProfessionalRole,
    reviewer: str,
    disposition: ReviewDisposition,
    check_notes: dict[str, str],
    findings: tuple[str, ...],
    objections: tuple[str, ...],
    required_followups: tuple[str, ...],
) -> str:
    payload = {
        "claim_id": claim_id,
        "dossier": dossier_fingerprint,
        "role": role.value,
        "reviewer": reviewer,
        "disposition": disposition.value,
        "check_notes": check_notes,
        "findings": findings,
        "objections": objections,
        "required_followups": required_followups,
    }
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return "role-review:" + hashlib.sha256(material).hexdigest()


class ProfessionalReviewLedger:
    """Append-only role reviews. No majority vote and no objection averaging."""

    PANEL_CAVEAT = (
        "Panel state is a workflow/readiness summary, not an investment decision, "
        "truth score, expected-return estimate, or capital authorization."
    )

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS professional_reviews (
                review_id VARCHAR PRIMARY KEY,
                claim_id VARCHAR NOT NULL,
                dossier_fingerprint VARCHAR NOT NULL,
                dossier_posture VARCHAR NOT NULL,
                role VARCHAR NOT NULL,
                reviewer VARCHAR NOT NULL,
                recorded_at TIMESTAMPTZ NOT NULL,
                disposition VARCHAR NOT NULL,
                check_notes_json VARCHAR NOT NULL,
                findings_json VARCHAR NOT NULL,
                objections_json VARCHAR NOT NULL,
                required_followups_json VARCHAR NOT NULL
            )
            """
        )

    def record(
        self,
        *,
        dossier: EvidenceDossier,
        role: ProfessionalRole,
        reviewer: str,
        recorded_at: datetime,
        disposition: ReviewDisposition,
        check_notes: dict[str, str],
        findings: tuple[str, ...],
        objections: tuple[str, ...] = (),
        required_followups: tuple[str, ...] = (),
    ) -> RoleReview:
        if not reviewer.strip():
            raise ValueError("reviewer is required")
        if recorded_at.tzinfo is None:
            raise ValueError("recorded_at must be timezone-aware")
        self._validate_role_checks(role, check_notes)

        if not findings or not all(item.strip() for item in findings):
            raise ValueError("at least one non-empty finding is required")
        if disposition is ReviewDisposition.BLOCKING_OBJECTION:
            if not objections or not all(item.strip() for item in objections):
                raise ValueError("blocking reviews require explicit objections")
        if disposition is ReviewDisposition.CONDITIONAL:
            if not required_followups or not all(
                item.strip() for item in required_followups
            ):
                raise ValueError("conditional reviews require follow-up actions")
        if disposition is ReviewDisposition.NOT_APPLICABLE:
            if objections or required_followups:
                raise ValueError(
                    "NOT_APPLICABLE review cannot carry objections or follow-ups"
                )

        fingerprint = dossier_fingerprint(dossier)
        clean_checks = {key: value.strip() for key, value in check_notes.items()}
        clean_findings = tuple(item.strip() for item in findings)
        clean_objections = tuple(item.strip() for item in objections)
        clean_followups = tuple(item.strip() for item in required_followups)
        review_id = make_review_id(
            claim_id=dossier.claim_id,
            dossier_fingerprint=fingerprint,
            role=role,
            reviewer=reviewer.strip(),
            disposition=disposition,
            check_notes=clean_checks,
            findings=clean_findings,
            objections=clean_objections,
            required_followups=clean_followups,
        )
        review = RoleReview(
            review_id=review_id,
            claim_id=dossier.claim_id,
            dossier_fingerprint=fingerprint,
            dossier_posture=dossier.posture.value,
            role=role,
            reviewer=reviewer.strip(),
            recorded_at=recorded_at,
            disposition=disposition,
            check_notes=clean_checks,
            findings=clean_findings,
            objections=clean_objections,
            required_followups=clean_followups,
        )
        existing = self.get(review_id)
        if existing is not None:
            return existing

        self._con.execute(
            """
            INSERT INTO professional_reviews
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                review.review_id,
                review.claim_id,
                review.dossier_fingerprint,
                review.dossier_posture,
                review.role.value,
                review.reviewer,
                review.recorded_at,
                review.disposition.value,
                json.dumps(review.check_notes, sort_keys=True),
                json.dumps(review.findings),
                json.dumps(review.objections),
                json.dumps(review.required_followups),
            ],
        )
        return review

    def get(self, review_id: str) -> RoleReview | None:
        row = self._con.execute(
            """
            SELECT review_id, claim_id, dossier_fingerprint, dossier_posture,
                   role, reviewer, recorded_at, disposition,
                   check_notes_json, findings_json, objections_json,
                   required_followups_json
            FROM professional_reviews
            WHERE review_id = ?
            """,
            [review_id],
        ).fetchone()
        return None if row is None else self._row(row)

    def reviews_for(
        self,
        *,
        claim_id: str,
        dossier_fingerprint: str,
    ) -> tuple[RoleReview, ...]:
        rows = self._con.execute(
            """
            SELECT review_id, claim_id, dossier_fingerprint, dossier_posture,
                   role, reviewer, recorded_at, disposition,
                   check_notes_json, findings_json, objections_json,
                   required_followups_json
            FROM professional_reviews
            WHERE claim_id = ? AND dossier_fingerprint = ?
            ORDER BY recorded_at, role, review_id
            """,
            [claim_id, dossier_fingerprint],
        ).fetchall()
        return tuple(self._row(row) for row in rows)

    def panel(self, dossier: EvidenceDossier) -> ReviewPanel:
        fingerprint = dossier_fingerprint(dossier)
        reviews = self.reviews_for(
            claim_id=dossier.claim_id,
            dossier_fingerprint=fingerprint,
        )
        represented = tuple(
            sorted(
                {review.role for review in reviews},
                key=lambda role: role.value,
            )
        )
        missing = tuple(
            role for role in ProfessionalRole if role not in represented
        )
        blocking = tuple(
            review.review_id
            for review in reviews
            if review.disposition is ReviewDisposition.BLOCKING_OBJECTION
        )
        conditional = tuple(
            review.review_id
            for review in reviews
            if review.disposition is ReviewDisposition.CONDITIONAL
        )

        if blocking:
            state = ReviewPanelState.BLOCKING_OBJECTION_PRESENT
        elif missing:
            state = ReviewPanelState.INCOMPLETE
        elif conditional:
            state = ReviewPanelState.CONDITIONS_OPEN
        else:
            state = ReviewPanelState.REVIEW_SET_COMPLETE

        return ReviewPanel(
            claim_id=dossier.claim_id,
            dossier_fingerprint=fingerprint,
            state=state,
            reviews=reviews,
            represented_roles=represented,
            missing_roles=missing,
            blocking_review_ids=blocking,
            conditional_review_ids=conditional,
            caveat=self.PANEL_CAVEAT,
        )

    @staticmethod
    def _validate_role_checks(
        role: ProfessionalRole,
        check_notes: dict[str, str],
    ) -> None:
        required = ROLE_CHECKS[role]
        missing = [key for key in required if not check_notes.get(key, "").strip()]
        extras = [key for key in check_notes if key not in required]
        if missing:
            raise ValueError(
                f"{role.value} review is missing required checks: "
                + ", ".join(missing)
            )
        if extras:
            raise ValueError(
                f"{role.value} review contains unknown checks: "
                + ", ".join(sorted(extras))
            )

    @staticmethod
    def _row(row: tuple[object, ...]) -> RoleReview:
        return RoleReview(
            review_id=str(row[0]),
            claim_id=str(row[1]),
            dossier_fingerprint=str(row[2]),
            dossier_posture=str(row[3]),
            role=ProfessionalRole(str(row[4])),
            reviewer=str(row[5]),
            recorded_at=row[6],
            disposition=ReviewDisposition(str(row[7])),
            check_notes=json.loads(str(row[8])),
            findings=tuple(json.loads(str(row[9]))),
            objections=tuple(json.loads(str(row[10]))),
            required_followups=tuple(json.loads(str(row[11]))),
        )

    def close(self) -> None:
        self._con.close()
