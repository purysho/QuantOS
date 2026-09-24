from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import duckdb


class ArtifactIntegrityError(IOError):
    pass


@dataclass(frozen=True)
class ArtifactRef:
    observation_id: str
    artifact_id: str
    source_uri: str
    fetched_at: datetime
    media_type: str
    byte_length: int


class SourceArtifactStore:
    """Content-addressed immutable source storage with fetch provenance."""

    def __init__(self, root: str | Path, metadata_db: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        Path(metadata_db).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(metadata_db))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS source_artifacts (
                artifact_id VARCHAR PRIMARY KEY,
                sha256 VARCHAR NOT NULL,
                byte_length BIGINT NOT NULL,
                storage_path VARCHAR NOT NULL
            )
            """
        )
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS artifact_observations (
                observation_id VARCHAR PRIMARY KEY,
                artifact_id VARCHAR NOT NULL,
                source_uri VARCHAR NOT NULL,
                fetched_at TIMESTAMPTZ NOT NULL,
                media_type VARCHAR NOT NULL
            )
            """
        )

    def put(
        self,
        *,
        source_uri: str,
        content: bytes,
        fetched_at: datetime,
        media_type: str = "application/octet-stream",
    ) -> ArtifactRef:
        if not source_uri:
            raise ValueError("source_uri is required")
        if fetched_at.tzinfo is None:
            raise ValueError("fetched_at must be timezone-aware")
        if not isinstance(content, bytes):
            raise TypeError("content must be bytes")

        digest = hashlib.sha256(content).hexdigest()
        artifact_id = f"sha256:{digest}"
        relative = Path(digest[:2]) / digest
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)

        if target.exists():
            current = target.read_bytes()
            if hashlib.sha256(current).hexdigest() != digest:
                raise ArtifactIntegrityError("existing artifact failed hash verification")
        else:
            tmp = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
            tmp.write_bytes(content)
            os.replace(tmp, target)

        existing = self._con.execute(
            "SELECT byte_length, storage_path FROM source_artifacts WHERE artifact_id = ?",
            [artifact_id],
        ).fetchone()
        if existing is None:
            self._con.execute(
                "INSERT INTO source_artifacts VALUES (?, ?, ?, ?)",
                [artifact_id, digest, len(content), str(relative)],
            )
        elif int(existing[0]) != len(content):
            raise ArtifactIntegrityError("artifact metadata length mismatch")

        obs_seed = (
            f"{artifact_id}\n{source_uri}\n{fetched_at.isoformat()}\n{media_type}"
        ).encode("utf-8")
        observation_id = "obs:" + hashlib.sha256(obs_seed).hexdigest()
        exists = self._con.execute(
            "SELECT 1 FROM artifact_observations WHERE observation_id = ?",
            [observation_id],
        ).fetchone()
        if exists is None:
            self._con.execute(
                "INSERT INTO artifact_observations VALUES (?, ?, ?, ?, ?)",
                [observation_id, artifact_id, source_uri, fetched_at, media_type],
            )

        return ArtifactRef(
            observation_id=observation_id,
            artifact_id=artifact_id,
            source_uri=source_uri,
            fetched_at=fetched_at,
            media_type=media_type,
            byte_length=len(content),
        )

    def get(self, artifact_id: str) -> bytes:
        row = self._con.execute(
            "SELECT sha256, storage_path FROM source_artifacts WHERE artifact_id = ?",
            [artifact_id],
        ).fetchone()
        if row is None:
            raise KeyError(artifact_id)
        digest, relative = str(row[0]), str(row[1])
        content = (self.root / relative).read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise ArtifactIntegrityError(f"artifact corrupted: {artifact_id}")
        return content

    def observations_for(self, artifact_id: str) -> tuple[ArtifactRef, ...]:
        rows = self._con.execute(
            """
            SELECT o.observation_id, o.artifact_id, o.source_uri, o.fetched_at,
                   o.media_type, a.byte_length
            FROM artifact_observations o
            JOIN source_artifacts a USING (artifact_id)
            WHERE o.artifact_id = ?
            ORDER BY o.fetched_at, o.observation_id
            """,
            [artifact_id],
        ).fetchall()
        return tuple(
            ArtifactRef(
                observation_id=str(row[0]),
                artifact_id=str(row[1]),
                source_uri=str(row[2]),
                fetched_at=row[3],
                media_type=str(row[4]),
                byte_length=int(row[5]),
            )
            for row in rows
        )

    def close(self) -> None:
        self._con.close()
