from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

import duckdb

from .claims import ClaimCard, ClaimStore


class ClaimRelationType(str, Enum):
    SUPPORTS = "SUPPORTS"
    LIMITS = "LIMITS"
    CONTRADICTS = "CONTRADICTS"
    EXTENDS = "EXTENDS"


class ReplicationKind(str, Enum):
    DIRECT = "DIRECT"
    CONCEPTUAL = "CONCEPTUAL"
    REANALYSIS = "REANALYSIS"


class ReplicationOutcome(str, Enum):
    REPLICATES = "REPLICATES"
    PARTIAL = "PARTIAL"
    FAILS_TO_REPLICATE = "FAILS_TO_REPLICATE"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class ClaimRelation:
    relation_id: str
    from_claim_id: str
    to_claim_id: str
    relation_type: ClaimRelationType
    notes: str
    recorded_by: str
    recorded_at: datetime


@dataclass(frozen=True)
class ReplicationRecord:
    replication_id: str
    original_claim_id: str
    replication_claim_id: str
    kind: ReplicationKind
    outcome: ReplicationOutcome
    independent_data: bool
    preregistered: bool | None
    notes: str
    reviewer: str
    recorded_at: datetime


@dataclass(frozen=True)
class ClaimEvidenceContext:
    focal: ClaimCard
    supporting: tuple[ClaimCard, ...]
    limiting: tuple[ClaimCard, ...]
    contradicting: tuple[ClaimCard, ...]
    extensions: tuple[ClaimCard, ...]
    replications: tuple[ReplicationRecord, ...]


def make_relation_id(
    *,
    from_claim_id: str,
    to_claim_id: str,
    relation_type: ClaimRelationType,
) -> str:
    material = json.dumps(
        {
            "from": from_claim_id,
            "to": to_claim_id,
            "type": relation_type.value,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return "claim-rel:" + hashlib.sha256(material).hexdigest()


def make_replication_id(
    *,
    original_claim_id: str,
    replication_claim_id: str,
    kind: ReplicationKind,
) -> str:
    material = json.dumps(
        {
            "original": original_claim_id,
            "replication": replication_claim_id,
            "kind": kind.value,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return "replication:" + hashlib.sha256(material).hexdigest()


class ClaimEvidenceGraph:
    """Explicit disagreement/evidence graph over already trusted ClaimCards."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS claim_relations (
                relation_id VARCHAR PRIMARY KEY,
                from_claim_id VARCHAR NOT NULL,
                to_claim_id VARCHAR NOT NULL,
                relation_type VARCHAR NOT NULL,
                notes VARCHAR NOT NULL,
                recorded_by VARCHAR NOT NULL,
                recorded_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS replication_records (
                replication_id VARCHAR PRIMARY KEY,
                original_claim_id VARCHAR NOT NULL,
                replication_claim_id VARCHAR NOT NULL,
                kind VARCHAR NOT NULL,
                outcome VARCHAR NOT NULL,
                independent_data BOOLEAN NOT NULL,
                preregistered BOOLEAN,
                notes VARCHAR NOT NULL,
                reviewer VARCHAR NOT NULL,
                recorded_at TIMESTAMPTZ NOT NULL
            )
            """
        )

    def relate(
        self,
        *,
        from_claim_id: str,
        to_claim_id: str,
        relation_type: ClaimRelationType,
        notes: str,
        recorded_by: str,
        recorded_at: datetime,
        claims: ClaimStore,
    ) -> ClaimRelation:
        source = claims.get(from_claim_id)
        target = claims.get(to_claim_id)
        if source is None or target is None:
            raise ValueError("claim relations require two existing trusted claims")
        if from_claim_id == to_claim_id:
            raise ValueError("a claim cannot relate to itself")
        if not notes.strip() or not recorded_by.strip():
            raise ValueError("relation notes and recorder are required")
        if recorded_at.tzinfo is None:
            raise ValueError("recorded_at must be timezone-aware")

        relation = ClaimRelation(
            relation_id=make_relation_id(
                from_claim_id=from_claim_id,
                to_claim_id=to_claim_id,
                relation_type=relation_type,
            ),
            from_claim_id=from_claim_id,
            to_claim_id=to_claim_id,
            relation_type=relation_type,
            notes=notes.strip(),
            recorded_by=recorded_by.strip(),
            recorded_at=recorded_at,
        )
        existing = self._con.execute(
            """
            SELECT from_claim_id, to_claim_id, relation_type, notes, recorded_by
            FROM claim_relations
            WHERE relation_id = ?
            """,
            [relation.relation_id],
        ).fetchone()
        if existing is not None:
            old = tuple(str(value) for value in existing)
            new = (
                relation.from_claim_id,
                relation.to_claim_id,
                relation.relation_type.value,
                relation.notes,
                relation.recorded_by,
            )
            if old != new:
                raise ValueError("claim relation identity conflict")
            return relation

        self._con.execute(
            "INSERT INTO claim_relations VALUES (?, ?, ?, ?, ?, ?)",
            [
                relation.relation_id,
                relation.from_claim_id,
                relation.to_claim_id,
                relation.relation_type.value,
                relation.notes,
                relation.recorded_by,
                relation.recorded_at,
            ],
        )
        return relation

    def record_replication(
        self,
        *,
        original_claim_id: str,
        replication_claim_id: str,
        kind: ReplicationKind,
        outcome: ReplicationOutcome,
        independent_data: bool,
        preregistered: bool | None,
        notes: str,
        reviewer: str,
        recorded_at: datetime,
        claims: ClaimStore,
    ) -> ReplicationRecord:
        original = claims.get(original_claim_id)
        replication = claims.get(replication_claim_id)
        if original is None or replication is None:
            raise ValueError("replication requires two existing trusted claims")
        if original_claim_id == replication_claim_id:
            raise ValueError("a claim cannot replicate itself")
        if not notes.strip() or not reviewer.strip():
            raise ValueError("replication notes and reviewer are required")
        if recorded_at.tzinfo is None:
            raise ValueError("recorded_at must be timezone-aware")
        if set(original.source_artifact_ids) == set(replication.source_artifact_ids):
            raise ValueError(
                "replication record must reference a distinct source artifact set"
            )

        record = ReplicationRecord(
            replication_id=make_replication_id(
                original_claim_id=original_claim_id,
                replication_claim_id=replication_claim_id,
                kind=kind,
            ),
            original_claim_id=original_claim_id,
            replication_claim_id=replication_claim_id,
            kind=kind,
            outcome=outcome,
            independent_data=independent_data,
            preregistered=preregistered,
            notes=notes.strip(),
            reviewer=reviewer.strip(),
            recorded_at=recorded_at,
        )
        existing = self._con.execute(
            """
            SELECT original_claim_id, replication_claim_id, kind, outcome,
                   independent_data, preregistered, notes, reviewer
            FROM replication_records
            WHERE replication_id = ?
            """,
            [record.replication_id],
        ).fetchone()
        if existing is not None:
            old = (
                str(existing[0]),
                str(existing[1]),
                str(existing[2]),
                str(existing[3]),
                bool(existing[4]),
                existing[5],
                str(existing[6]),
                str(existing[7]),
            )
            new = (
                record.original_claim_id,
                record.replication_claim_id,
                record.kind.value,
                record.outcome.value,
                record.independent_data,
                record.preregistered,
                record.notes,
                record.reviewer,
            )
            if old != new:
                raise ValueError("replication identity conflict")
            return record

        self._con.execute(
            "INSERT INTO replication_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                record.replication_id,
                record.original_claim_id,
                record.replication_claim_id,
                record.kind.value,
                record.outcome.value,
                record.independent_data,
                record.preregistered,
                record.notes,
                record.reviewer,
                record.recorded_at,
            ],
        )
        return record

    def context(
        self,
        claim_id: str,
        *,
        claims: ClaimStore,
    ) -> ClaimEvidenceContext:
        focal = claims.get(claim_id)
        if focal is None:
            raise KeyError(claim_id)
        rows = self._con.execute(
            """
            SELECT from_claim_id, relation_type
            FROM claim_relations
            WHERE to_claim_id = ?
            ORDER BY relation_type, from_claim_id
            """,
            [claim_id],
        ).fetchall()

        grouped: dict[ClaimRelationType, list[ClaimCard]] = {
            relation_type: [] for relation_type in ClaimRelationType
        }
        for from_id, relation_type in rows:
            card = claims.get(str(from_id))
            if card is not None:
                grouped[ClaimRelationType(str(relation_type))].append(card)

        rep_rows = self._con.execute(
            """
            SELECT replication_id, original_claim_id, replication_claim_id,
                   kind, outcome, independent_data, preregistered,
                   notes, reviewer, recorded_at
            FROM replication_records
            WHERE original_claim_id = ?
            ORDER BY recorded_at, replication_id
            """,
            [claim_id],
        ).fetchall()
        replications = tuple(self._replication_row(row) for row in rep_rows)
        return ClaimEvidenceContext(
            focal=focal,
            supporting=tuple(grouped[ClaimRelationType.SUPPORTS]),
            limiting=tuple(grouped[ClaimRelationType.LIMITS]),
            contradicting=tuple(grouped[ClaimRelationType.CONTRADICTS]),
            extensions=tuple(grouped[ClaimRelationType.EXTENDS]),
            replications=replications,
        )

    @staticmethod
    def _replication_row(row: tuple[object, ...]) -> ReplicationRecord:
        return ReplicationRecord(
            replication_id=str(row[0]),
            original_claim_id=str(row[1]),
            replication_claim_id=str(row[2]),
            kind=ReplicationKind(str(row[3])),
            outcome=ReplicationOutcome(str(row[4])),
            independent_data=bool(row[5]),
            preregistered=row[6] if row[6] is None else bool(row[6]),
            notes=str(row[7]),
            reviewer=str(row[8]),
            recorded_at=row[9],
        )

    def close(self) -> None:
        self._con.close()
