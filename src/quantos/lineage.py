from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb


@dataclass(frozen=True)
class ArtifactLineage:
    event_id: str
    role: str
    artifact_id: str
    linked_at: datetime


class LineageStore:
    """Bind normalized events to immutable source artifacts.

    One event/role may point to exactly one artifact. If the bytes later differ,
    callers must create an explicit new role/revision rather than silently
    rewriting provenance.
    """

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS event_artifacts (
                event_id VARCHAR NOT NULL,
                role VARCHAR NOT NULL,
                artifact_id VARCHAR NOT NULL,
                linked_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (event_id, role)
            )
            """
        )

    def link(
        self,
        *,
        event_id: str,
        role: str,
        artifact_id: str,
        linked_at: datetime,
    ) -> ArtifactLineage:
        if not event_id or not role or not artifact_id:
            raise ValueError("event_id, role and artifact_id are required")
        if linked_at.tzinfo is None:
            raise ValueError("linked_at must be timezone-aware")

        existing = self._con.execute(
            """
            SELECT artifact_id, linked_at
            FROM event_artifacts
            WHERE event_id = ? AND role = ?
            """,
            [event_id, role],
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != artifact_id:
                raise ValueError(
                    "lineage conflict: event/role already points to a different artifact"
                )
            return ArtifactLineage(
                event_id=event_id,
                role=role,
                artifact_id=str(existing[0]),
                linked_at=existing[1],
            )

        self._con.execute(
            "INSERT INTO event_artifacts VALUES (?, ?, ?, ?)",
            [event_id, role, artifact_id, linked_at],
        )
        return ArtifactLineage(
            event_id=event_id,
            role=role,
            artifact_id=artifact_id,
            linked_at=linked_at,
        )

    def artifacts_for(self, event_id: str) -> tuple[ArtifactLineage, ...]:
        rows = self._con.execute(
            """
            SELECT event_id, role, artifact_id, linked_at
            FROM event_artifacts
            WHERE event_id = ?
            ORDER BY role
            """,
            [event_id],
        ).fetchall()
        return tuple(
            ArtifactLineage(
                event_id=str(row[0]),
                role=str(row[1]),
                artifact_id=str(row[2]),
                linked_at=row[3],
            )
            for row in rows
        )

    def close(self) -> None:
        self._con.close()
