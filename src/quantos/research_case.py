from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

import duckdb

from .claims import ClaimStore


class ResearchCaseType(str, Enum):
    FUNDAMENTAL = "FUNDAMENTAL"
    SYSTEMATIC = "SYSTEMATIC"
    HYBRID = "HYBRID"
    RISK = "RISK"


@dataclass(frozen=True)
class ResearchCase:
    case_id: str
    case_type: ResearchCaseType
    subject_ids: tuple[str, ...]
    universe: str
    thesis: str
    mechanism: str
    horizon: str
    as_of: datetime
    supporting_claim_ids: tuple[str, ...]
    limiting_claim_ids: tuple[str, ...]
    contradicting_claim_ids: tuple[str, ...]
    alternative_explanations: tuple[str, ...]
    falsifiers: tuple[str, ...]
    monitoring_conditions: tuple[str, ...]
    assumptions: tuple[str, ...]
    author: str
    created_at: datetime
    supersedes_case_id: str | None


def make_case_id(
    *,
    case_type: ResearchCaseType,
    subject_ids: tuple[str, ...],
    universe: str,
    thesis: str,
    mechanism: str,
    horizon: str,
    as_of: datetime,
    supporting_claim_ids: tuple[str, ...],
    limiting_claim_ids: tuple[str, ...],
    contradicting_claim_ids: tuple[str, ...],
    alternative_explanations: tuple[str, ...],
    falsifiers: tuple[str, ...],
    monitoring_conditions: tuple[str, ...],
    assumptions: tuple[str, ...],
    supersedes_case_id: str | None,
) -> str:
    payload = {
        "case_type": case_type.value,
        "subject_ids": sorted(subject_ids),
        "universe": universe,
        "thesis": thesis,
        "mechanism": mechanism,
        "horizon": horizon,
        "as_of": as_of.isoformat(),
        "supporting_claim_ids": sorted(supporting_claim_ids),
        "limiting_claim_ids": sorted(limiting_claim_ids),
        "contradicting_claim_ids": sorted(contradicting_claim_ids),
        "alternative_explanations": alternative_explanations,
        "falsifiers": falsifiers,
        "monitoring_conditions": monitoring_conditions,
        "assumptions": assumptions,
        "supersedes_case_id": supersedes_case_id,
    }
    material = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "research-case:" + hashlib.sha256(material).hexdigest()


class ResearchCaseStore:
    """Immutable, evidence-linked research cases with explicit falsifiers."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS research_cases (
                case_id VARCHAR PRIMARY KEY,
                case_type VARCHAR NOT NULL,
                subject_ids_json VARCHAR NOT NULL,
                universe VARCHAR NOT NULL,
                thesis VARCHAR NOT NULL,
                mechanism VARCHAR NOT NULL,
                horizon VARCHAR NOT NULL,
                as_of TIMESTAMPTZ NOT NULL,
                supporting_claim_ids_json VARCHAR NOT NULL,
                limiting_claim_ids_json VARCHAR NOT NULL,
                contradicting_claim_ids_json VARCHAR NOT NULL,
                alternative_explanations_json VARCHAR NOT NULL,
                falsifiers_json VARCHAR NOT NULL,
                monitoring_conditions_json VARCHAR NOT NULL,
                assumptions_json VARCHAR NOT NULL,
                author VARCHAR NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                supersedes_case_id VARCHAR
            )
            """
        )

    def create(
        self,
        *,
        case_type: ResearchCaseType,
        subject_ids: tuple[str, ...],
        universe: str,
        thesis: str,
        mechanism: str,
        horizon: str,
        as_of: datetime,
        supporting_claim_ids: tuple[str, ...],
        limiting_claim_ids: tuple[str, ...] = (),
        contradicting_claim_ids: tuple[str, ...] = (),
        alternative_explanations: tuple[str, ...],
        falsifiers: tuple[str, ...],
        monitoring_conditions: tuple[str, ...],
        assumptions: tuple[str, ...] = (),
        author: str,
        created_at: datetime,
        claims: ClaimStore,
        supersedes_case_id: str | None = None,
    ) -> ResearchCase:
        self._validate_texts(
            subject_ids=subject_ids,
            universe=universe,
            thesis=thesis,
            mechanism=mechanism,
            horizon=horizon,
            alternative_explanations=alternative_explanations,
            falsifiers=falsifiers,
            monitoring_conditions=monitoring_conditions,
            author=author,
        )
        if as_of.tzinfo is None or created_at.tzinfo is None:
            raise ValueError("as_of and created_at must be timezone-aware")
        if created_at < as_of:
            raise ValueError("created_at cannot precede case as_of")
        if not supporting_claim_ids:
            raise ValueError("research case requires at least one supporting trusted claim")

        groups = {
            "supporting": supporting_claim_ids,
            "limiting": limiting_claim_ids,
            "contradicting": contradicting_claim_ids,
        }
        for name, claim_ids in groups.items():
            if len(set(claim_ids)) != len(claim_ids):
                raise ValueError(f"duplicate {name} claim IDs are not allowed")
        role_sets = [set(ids) for ids in groups.values()]
        if (role_sets[0] & role_sets[1]) or (role_sets[0] & role_sets[2]) or (role_sets[1] & role_sets[2]):
            raise ValueError("one claim cannot occupy multiple case evidence roles")

        for claim_id in (
            supporting_claim_ids + limiting_claim_ids + contradicting_claim_ids
        ):
            card = claims.get(claim_id)
            if card is None:
                raise ValueError(f"research case references unknown trusted claim: {claim_id}")
            if card.as_of > as_of:
                raise ValueError(
                    f"claim {claim_id} was not available by the case as_of timestamp"
                )

        if supersedes_case_id is not None:
            prior = self.get(supersedes_case_id)
            if prior is None:
                raise ValueError("superseded research case does not exist")
            if tuple(sorted(prior.subject_ids)) != tuple(sorted(subject_ids)):
                raise ValueError("case revision cannot silently change subjects")
            if prior.case_type is not case_type:
                raise ValueError("case revision cannot silently change case type")

        clean_subjects = tuple(item.strip() for item in subject_ids)
        clean_alternatives = tuple(item.strip() for item in alternative_explanations)
        clean_falsifiers = tuple(item.strip() for item in falsifiers)
        clean_monitoring = tuple(item.strip() for item in monitoring_conditions)
        clean_assumptions = tuple(item.strip() for item in assumptions if item.strip())
        case_id = make_case_id(
            case_type=case_type,
            subject_ids=clean_subjects,
            universe=universe.strip(),
            thesis=thesis.strip(),
            mechanism=mechanism.strip(),
            horizon=horizon.strip(),
            as_of=as_of,
            supporting_claim_ids=supporting_claim_ids,
            limiting_claim_ids=limiting_claim_ids,
            contradicting_claim_ids=contradicting_claim_ids,
            alternative_explanations=clean_alternatives,
            falsifiers=clean_falsifiers,
            monitoring_conditions=clean_monitoring,
            assumptions=clean_assumptions,
            supersedes_case_id=supersedes_case_id,
        )
        case = ResearchCase(
            case_id=case_id,
            case_type=case_type,
            subject_ids=clean_subjects,
            universe=universe.strip(),
            thesis=thesis.strip(),
            mechanism=mechanism.strip(),
            horizon=horizon.strip(),
            as_of=as_of,
            supporting_claim_ids=supporting_claim_ids,
            limiting_claim_ids=limiting_claim_ids,
            contradicting_claim_ids=contradicting_claim_ids,
            alternative_explanations=clean_alternatives,
            falsifiers=clean_falsifiers,
            monitoring_conditions=clean_monitoring,
            assumptions=clean_assumptions,
            author=author.strip(),
            created_at=created_at,
            supersedes_case_id=supersedes_case_id,
        )

        existing = self.get(case_id)
        if existing is not None:
            if existing != case:
                raise ValueError("research case identity conflict")
            return existing

        self._con.execute(
            """
            INSERT INTO research_cases
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                case.case_id,
                case.case_type.value,
                json.dumps(case.subject_ids),
                case.universe,
                case.thesis,
                case.mechanism,
                case.horizon,
                case.as_of,
                json.dumps(case.supporting_claim_ids),
                json.dumps(case.limiting_claim_ids),
                json.dumps(case.contradicting_claim_ids),
                json.dumps(case.alternative_explanations),
                json.dumps(case.falsifiers),
                json.dumps(case.monitoring_conditions),
                json.dumps(case.assumptions),
                case.author,
                case.created_at,
                case.supersedes_case_id,
            ],
        )
        return case

    def get(self, case_id: str) -> ResearchCase | None:
        row = self._con.execute(
            """
            SELECT case_id, case_type, subject_ids_json, universe, thesis,
                   mechanism, horizon, as_of, supporting_claim_ids_json,
                   limiting_claim_ids_json, contradicting_claim_ids_json,
                   alternative_explanations_json, falsifiers_json,
                   monitoring_conditions_json, assumptions_json, author,
                   created_at, supersedes_case_id
            FROM research_cases
            WHERE case_id = ?
            """,
            [case_id],
        ).fetchone()
        return None if row is None else self._row(row)

    def lineage(self, case_id: str) -> tuple[ResearchCase, ...]:
        current = self.get(case_id)
        if current is None:
            raise KeyError(case_id)
        chain = [current]
        seen = {current.case_id}
        while current.supersedes_case_id is not None:
            prior = self.get(current.supersedes_case_id)
            if prior is None:
                raise ValueError("research case lineage is broken")
            if prior.case_id in seen:
                raise ValueError("research case lineage contains a cycle")
            chain.append(prior)
            seen.add(prior.case_id)
            current = prior
        chain.reverse()
        return tuple(chain)

    @staticmethod
    def _validate_texts(
        *,
        subject_ids: tuple[str, ...],
        universe: str,
        thesis: str,
        mechanism: str,
        horizon: str,
        alternative_explanations: tuple[str, ...],
        falsifiers: tuple[str, ...],
        monitoring_conditions: tuple[str, ...],
        author: str,
    ) -> None:
        if not subject_ids or not all(item.strip() for item in subject_ids):
            raise ValueError("at least one non-empty subject ID is required")
        if len(set(subject_ids)) != len(subject_ids):
            raise ValueError("duplicate subject IDs are not allowed")
        for name, value in {
            "universe": universe,
            "thesis": thesis,
            "mechanism": mechanism,
            "horizon": horizon,
            "author": author,
        }.items():
            if not value.strip():
                raise ValueError(f"{name} is required")
        for name, values in {
            "alternative_explanations": alternative_explanations,
            "falsifiers": falsifiers,
            "monitoring_conditions": monitoring_conditions,
        }.items():
            if not values or not all(value.strip() for value in values):
                raise ValueError(f"{name} requires at least one non-empty entry")

    @staticmethod
    def _row(row: tuple[object, ...]) -> ResearchCase:
        return ResearchCase(
            case_id=str(row[0]),
            case_type=ResearchCaseType(str(row[1])),
            subject_ids=tuple(json.loads(str(row[2]))),
            universe=str(row[3]),
            thesis=str(row[4]),
            mechanism=str(row[5]),
            horizon=str(row[6]),
            as_of=row[7],
            supporting_claim_ids=tuple(json.loads(str(row[8]))),
            limiting_claim_ids=tuple(json.loads(str(row[9]))),
            contradicting_claim_ids=tuple(json.loads(str(row[10]))),
            alternative_explanations=tuple(json.loads(str(row[11]))),
            falsifiers=tuple(json.loads(str(row[12]))),
            monitoring_conditions=tuple(json.loads(str(row[13]))),
            assumptions=tuple(json.loads(str(row[14]))),
            author=str(row[15]),
            created_at=row[16],
            supersedes_case_id=str(row[17]) if row[17] is not None else None,
        )

    def close(self) -> None:
        self._con.close()
