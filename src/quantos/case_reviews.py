from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb

from .case_dossier import CaseDossier
from .professional_reviews import (
    ProfessionalRole,
    ReviewDisposition,
    ReviewPanelState,
)


CASE_ROLE_CHECKS: dict[ProfessionalRole, tuple[str, ...]] = {
    ProfessionalRole.FUNDAMENTAL_ANALYST: (
        "mechanism_and_economics",
        "measurement_accounting_quality",
        "valuation_or_business_model_fit",
    ),
    ProfessionalRole.QUANT_RESEARCHER: (
        "identification_and_statistics",
        "data_leakage_and_multiple_testing",
        "replication_and_regime_stability",
    ),
    ProfessionalRole.PORTFOLIO_MANAGER: (
        "scenario_asymmetry",
        "portfolio_interaction_and_concentration",
        "sizing_and_diversification_assumptions",
    ),
    ProfessionalRole.RISK_OFFICER: (
        "failure_mechanisms",
        "tail_regime_and_liquidity_risk",
        "model_data_and_scenario_coverage",
    ),
    ProfessionalRole.EXECUTION_TRADER: (
        "implementability",
        "costs_slippage_and_capacity",
        "latency_liquidity_and_market_impact",
    ),
    ProfessionalRole.RED_TEAM: (
        "strongest_alternative_explanation",
        "falsifiers_and_disconfirming_evidence",
        "missing_or_conflicting_evidence",
    ),
}


@dataclass(frozen=True)
class CaseRoleReview:
    review_id: str
    case_id: str
    case_dossier_fingerprint: str
    scenario_set_id: str
    role: ProfessionalRole
    reviewer: str
    recorded_at: datetime
    disposition: ReviewDisposition
    check_notes: dict[str, str]
    findings: tuple[str, ...]
    objections: tuple[str, ...]
    required_followups: tuple[str, ...]


@dataclass(frozen=True)
class CaseReviewResolution:
    resolution_id: str
    review_id: str
    resolver: str
    resolved_at: datetime
    notes: str
    evidence_references: tuple[str, ...]


@dataclass(frozen=True)
class CaseReviewPanel:
    case_id: str
    case_dossier_fingerprint: str
    state: ReviewPanelState
    reviews: tuple[CaseRoleReview, ...]
    represented_roles: tuple[ProfessionalRole, ...]
    missing_roles: tuple[ProfessionalRole, ...]
    blocking_review_ids: tuple[str, ...]
    conditional_review_ids: tuple[str, ...]
    resolved_review_ids: tuple[str, ...]
    caveat: str


def make_case_review_id(
    *,
    case_id: str,
    case_dossier_fingerprint: str,
    scenario_set_id: str,
    role: ProfessionalRole,
    reviewer: str,
    disposition: ReviewDisposition,
    check_notes: dict[str, str],
    findings: tuple[str, ...],
    objections: tuple[str, ...],
    required_followups: tuple[str, ...],
) -> str:
    payload = {
        "case_id": case_id,
        "case_dossier_fingerprint": case_dossier_fingerprint,
        "scenario_set_id": scenario_set_id,
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
    ).encode("utf-8")
    return "case-review:" + hashlib.sha256(material).hexdigest()


def make_case_resolution_id(
    *,
    review_id: str,
    resolver: str,
    notes: str,
    evidence_references: tuple[str, ...],
) -> str:
    payload = {
        "review_id": review_id,
        "resolver": resolver,
        "notes": notes,
        "evidence_references": evidence_references,
    }
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "case-review-resolution:" + hashlib.sha256(material).hexdigest()


class CaseReviewLedger:
    """Six-role reviews bound to one exact whole-case dossier fingerprint."""

    PANEL_CAVEAT = (
        "Case review state is a research-governance workflow status only. "
        "REVIEW_SET_COMPLETE is not an investment recommendation, position-size "
        "decision, trade authorization, or permission to allocate capital."
    )

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS case_reviews (
                review_id VARCHAR PRIMARY KEY,
                case_id VARCHAR NOT NULL,
                case_dossier_fingerprint VARCHAR NOT NULL,
                scenario_set_id VARCHAR NOT NULL,
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
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS case_review_resolutions (
                resolution_id VARCHAR PRIMARY KEY,
                review_id VARCHAR NOT NULL UNIQUE,
                resolver VARCHAR NOT NULL,
                resolved_at TIMESTAMPTZ NOT NULL,
                notes VARCHAR NOT NULL,
                evidence_references_json VARCHAR NOT NULL
            )
            """
        )

    def record(
        self,
        *,
        dossier: CaseDossier,
        role: ProfessionalRole,
        reviewer: str,
        recorded_at: datetime,
        disposition: ReviewDisposition,
        check_notes: dict[str, str],
        findings: tuple[str, ...],
        objections: tuple[str, ...] = (),
        required_followups: tuple[str, ...] = (),
    ) -> CaseRoleReview:
        if not dossier.case_dossier_fingerprint.startswith("case-dossier:"):
            raise ValueError("invalid case dossier fingerprint")
        if not reviewer.strip():
            raise ValueError("reviewer is required")
        if recorded_at.tzinfo is None:
            raise ValueError("recorded_at must be timezone-aware")

        self._validate_role_checks(role, check_notes)
        if not findings or not all(item.strip() for item in findings):
            raise ValueError("at least one non-empty finding is required")

        if disposition is ReviewDisposition.BLOCKING_OBJECTION:
            if not objections or not all(item.strip() for item in objections):
                raise ValueError("blocking case review requires explicit objections")
        if disposition is ReviewDisposition.CONDITIONAL:
            if not required_followups or not all(
                item.strip() for item in required_followups
            ):
                raise ValueError("conditional case review requires follow-up actions")
        if disposition is ReviewDisposition.NOT_APPLICABLE:
            if objections or required_followups:
                raise ValueError(
                    "NOT_APPLICABLE review cannot carry objections or follow-ups"
                )

        clean_checks = {
            key: check_notes[key].strip()
            for key in CASE_ROLE_CHECKS[role]
        }
        clean_findings = tuple(item.strip() for item in findings)
        clean_objections = tuple(item.strip() for item in objections)
        clean_followups = tuple(item.strip() for item in required_followups)
        review_id = make_case_review_id(
            case_id=dossier.case.case_id,
            case_dossier_fingerprint=dossier.case_dossier_fingerprint,
            scenario_set_id=dossier.scenario_set.scenario_set_id,
            role=role,
            reviewer=reviewer.strip(),
            disposition=disposition,
            check_notes=clean_checks,
            findings=clean_findings,
            objections=clean_objections,
            required_followups=clean_followups,
        )
        review = CaseRoleReview(
            review_id=review_id,
            case_id=dossier.case.case_id,
            case_dossier_fingerprint=dossier.case_dossier_fingerprint,
            scenario_set_id=dossier.scenario_set.scenario_set_id,
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
            INSERT INTO case_reviews
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                review.review_id,
                review.case_id,
                review.case_dossier_fingerprint,
                review.scenario_set_id,
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

    def resolve_review(
        self,
        *,
        review_id: str,
        resolver: str,
        resolved_at: datetime,
        notes: str,
        evidence_references: tuple[str, ...],
    ) -> CaseReviewResolution:
        review = self.get(review_id)
        if review is None:
            raise KeyError(review_id)
        if review.disposition not in {
            ReviewDisposition.BLOCKING_OBJECTION,
            ReviewDisposition.CONDITIONAL,
        }:
            raise ValueError(
                "only blocking or conditional case reviews can be resolved"
            )
        if resolver.strip() != review.reviewer:
            raise ValueError(
                "only the reviewer who raised the issue may resolve it"
            )
        if resolved_at.tzinfo is None:
            raise ValueError("resolved_at must be timezone-aware")
        if not notes.strip():
            raise ValueError("resolution notes are required")
        clean_refs = tuple(
            reference.strip()
            for reference in evidence_references
            if reference.strip()
        )
        if not clean_refs:
            raise ValueError("at least one resolution evidence reference is required")

        existing = self.resolution_for(review_id)
        if existing is not None:
            requested = (resolver.strip(), notes.strip(), clean_refs)
            current = (
                existing.resolver,
                existing.notes,
                existing.evidence_references,
            )
            if requested != current:
                raise ValueError("case review already has a different resolution")
            return existing

        resolution_id = make_case_resolution_id(
            review_id=review_id,
            resolver=resolver.strip(),
            notes=notes.strip(),
            evidence_references=clean_refs,
        )
        self._con.execute(
            """
            INSERT INTO case_review_resolutions
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                resolution_id,
                review_id,
                resolver.strip(),
                resolved_at,
                notes.strip(),
                json.dumps(clean_refs),
            ],
        )
        result = self.resolution_for(review_id)
        assert result is not None
        return result

    def get(self, review_id: str) -> CaseRoleReview | None:
        row = self._con.execute(
            """
            SELECT review_id, case_id, case_dossier_fingerprint,
                   scenario_set_id, role, reviewer, recorded_at, disposition,
                   check_notes_json, findings_json, objections_json,
                   required_followups_json
            FROM case_reviews
            WHERE review_id = ?
            """,
            [review_id],
        ).fetchone()
        return None if row is None else self._review_row(row)

    def resolution_for(self, review_id: str) -> CaseReviewResolution | None:
        row = self._con.execute(
            """
            SELECT resolution_id, review_id, resolver, resolved_at,
                   notes, evidence_references_json
            FROM case_review_resolutions
            WHERE review_id = ?
            """,
            [review_id],
        ).fetchone()
        if row is None:
            return None
        return CaseReviewResolution(
            resolution_id=str(row[0]),
            review_id=str(row[1]),
            resolver=str(row[2]),
            resolved_at=row[3],
            notes=str(row[4]),
            evidence_references=tuple(json.loads(str(row[5]))),
        )

    def reviews_for(self, dossier: CaseDossier) -> tuple[CaseRoleReview, ...]:
        rows = self._con.execute(
            """
            SELECT review_id, case_id, case_dossier_fingerprint,
                   scenario_set_id, role, reviewer, recorded_at, disposition,
                   check_notes_json, findings_json, objections_json,
                   required_followups_json
            FROM case_reviews
            WHERE case_id = ? AND case_dossier_fingerprint = ?
            ORDER BY recorded_at, role, review_id
            """,
            [
                dossier.case.case_id,
                dossier.case_dossier_fingerprint,
            ],
        ).fetchall()
        return tuple(self._review_row(row) for row in rows)

    def panel(self, dossier: CaseDossier) -> CaseReviewPanel:
        reviews = self.reviews_for(dossier)
        represented = tuple(
            sorted(
                {review.role for review in reviews},
                key=lambda role: role.value,
            )
        )
        missing = tuple(
            role for role in ProfessionalRole if role not in represented
        )
        resolved = tuple(
            review.review_id
            for review in reviews
            if self.resolution_for(review.review_id) is not None
        )
        resolved_set = set(resolved)
        blocking = tuple(
            review.review_id
            for review in reviews
            if review.disposition is ReviewDisposition.BLOCKING_OBJECTION
            and review.review_id not in resolved_set
        )
        conditional = tuple(
            review.review_id
            for review in reviews
            if review.disposition is ReviewDisposition.CONDITIONAL
            and review.review_id not in resolved_set
        )

        if blocking:
            state = ReviewPanelState.BLOCKING_OBJECTION_PRESENT
        elif missing:
            state = ReviewPanelState.INCOMPLETE
        elif conditional:
            state = ReviewPanelState.CONDITIONS_OPEN
        else:
            state = ReviewPanelState.REVIEW_SET_COMPLETE

        return CaseReviewPanel(
            case_id=dossier.case.case_id,
            case_dossier_fingerprint=dossier.case_dossier_fingerprint,
            state=state,
            reviews=reviews,
            represented_roles=represented,
            missing_roles=missing,
            blocking_review_ids=blocking,
            conditional_review_ids=conditional,
            resolved_review_ids=resolved,
            caveat=self.PANEL_CAVEAT,
        )

    @staticmethod
    def _validate_role_checks(
        role: ProfessionalRole,
        check_notes: dict[str, str],
    ) -> None:
        required = CASE_ROLE_CHECKS[role]
        missing = [
            key for key in required if not check_notes.get(key, "").strip()
        ]
        extras = [key for key in check_notes if key not in required]
        if missing:
            raise ValueError(
                f"{role.value} case review is missing required checks: "
                + ", ".join(missing)
            )
        if extras:
            raise ValueError(
                f"{role.value} case review contains unknown checks: "
                + ", ".join(sorted(extras))
            )

    @staticmethod
    def _review_row(row: tuple[object, ...]) -> CaseRoleReview:
        return CaseRoleReview(
            review_id=str(row[0]),
            case_id=str(row[1]),
            case_dossier_fingerprint=str(row[2]),
            scenario_set_id=str(row[3]),
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
