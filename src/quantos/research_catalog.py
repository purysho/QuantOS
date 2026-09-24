from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

import duckdb

from .claims import ClaimCard, ClaimStore


class ResearchCatalogError(ValueError):
    pass


class VerificationStatus(str, Enum):
    QUARANTINED = "QUARANTINED"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class ResearchReference:
    source_id: str
    metadata: dict[str, Any]
    status: VerificationStatus
    artifact_id: str | None
    verified_at: datetime | None
    verifier: str | None
    notes: str | None


@dataclass(frozen=True)
class CatalogImportResult:
    inserted: int
    skipped_identical: int


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class ResearchCatalog:
    """Quarantine catalog for bibliographic/source-card metadata.

    Imported summaries are reference metadata, not trusted evidence. Claims may
    enter the trusted ClaimStore through this catalog only after their source
    artifacts have been explicitly verified.
    """

    REQUIRED_FIELDS = (
        "id",
        "domain",
        "type",
        "authority",
        "title",
        "authors",
        "year",
        "url",
        "stance",
        "supports",
        "do_not_infer",
    )

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS research_sources (
                source_id VARCHAR PRIMARY KEY,
                metadata_json VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                artifact_id VARCHAR,
                verified_at TIMESTAMPTZ,
                verifier VARCHAR,
                notes VARCHAR
            )
            """
        )

    def import_registry(self, payload: dict[str, Any]) -> CatalogImportResult:
        sources = payload.get("sources")
        if not isinstance(sources, list):
            raise ResearchCatalogError("registry must contain a sources list")
        declared = payload.get("source_count")
        if declared is not None and int(declared) != len(sources):
            raise ResearchCatalogError("source_count does not match sources length")

        inserted = 0
        skipped = 0
        for raw in sources:
            metadata = self._validate_metadata(raw)
            source_id = str(metadata["id"])
            canonical = json.dumps(
                metadata,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            existing = self._con.execute(
                "SELECT metadata_json FROM research_sources WHERE source_id = ?",
                [source_id],
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != canonical:
                    raise ResearchCatalogError(
                        f"source metadata conflict for {source_id}"
                    )
                skipped += 1
                continue

            self._con.execute(
                """
                INSERT INTO research_sources
                VALUES (?, ?, ?, NULL, NULL, NULL, NULL)
                """,
                [source_id, canonical, VerificationStatus.QUARANTINED.value],
            )
            inserted += 1

        return CatalogImportResult(
            inserted=inserted,
            skipped_identical=skipped,
        )

    def verify(
        self,
        *,
        source_id: str,
        artifact_id: str,
        verified_at: datetime,
        verifier: str,
        notes: str | None = None,
    ) -> ResearchReference:
        if not _SHA256.fullmatch(artifact_id):
            raise ResearchCatalogError("verified artifact must be a SHA-256 artifact ID")
        if verified_at.tzinfo is None:
            raise ResearchCatalogError("verified_at must be timezone-aware")
        if not verifier.strip():
            raise ResearchCatalogError("verifier is required")

        current = self.get(source_id)
        if current is None:
            raise ResearchCatalogError(f"unknown source: {source_id}")
        if current.status is VerificationStatus.REJECTED:
            raise ResearchCatalogError("rejected source cannot be verified implicitly")
        if current.status is VerificationStatus.VERIFIED:
            if current.artifact_id != artifact_id:
                raise ResearchCatalogError(
                    "verified source already points to a different artifact"
                )
            return current

        self._con.execute(
            """
            UPDATE research_sources
            SET status = ?, artifact_id = ?, verified_at = ?, verifier = ?, notes = ?
            WHERE source_id = ?
            """,
            [
                VerificationStatus.VERIFIED.value,
                artifact_id,
                verified_at,
                verifier.strip(),
                notes,
                source_id,
            ],
        )
        result = self.get(source_id)
        assert result is not None
        return result

    def reject(
        self,
        *,
        source_id: str,
        notes: str,
    ) -> ResearchReference:
        if not notes.strip():
            raise ResearchCatalogError("rejection notes are required")
        current = self.get(source_id)
        if current is None:
            raise ResearchCatalogError(f"unknown source: {source_id}")
        if current.status is VerificationStatus.VERIFIED:
            raise ResearchCatalogError(
                "verified source cannot be rejected without an explicit review workflow"
            )

        self._con.execute(
            """
            UPDATE research_sources
            SET status = ?, notes = ?
            WHERE source_id = ?
            """,
            [VerificationStatus.REJECTED.value, notes.strip(), source_id],
        )
        result = self.get(source_id)
        assert result is not None
        return result

    def promote_research_claim(
        self,
        card: ClaimCard,
        *,
        claim_store: ClaimStore,
    ) -> None:
        if not card.source_artifact_ids:
            raise ResearchCatalogError("research claim requires source artifacts")
        verified = {
            str(row[0])
            for row in self._con.execute(
                """
                SELECT artifact_id
                FROM research_sources
                WHERE status = ? AND artifact_id IS NOT NULL
                """,
                [VerificationStatus.VERIFIED.value],
            ).fetchall()
        }
        missing = [
            artifact_id
            for artifact_id in card.source_artifact_ids
            if artifact_id not in verified
        ]
        if missing:
            raise ResearchCatalogError(
                "claim references unverified research artifacts: "
                + ", ".join(missing)
            )
        claim_store.add(card)

    def get(self, source_id: str) -> ResearchReference | None:
        row = self._con.execute(
            """
            SELECT source_id, metadata_json, status, artifact_id,
                   verified_at, verifier, notes
            FROM research_sources
            WHERE source_id = ?
            """,
            [source_id],
        ).fetchone()
        if row is None:
            return None
        return ResearchReference(
            source_id=str(row[0]),
            metadata=json.loads(str(row[1])),
            status=VerificationStatus(str(row[2])),
            artifact_id=str(row[3]) if row[3] is not None else None,
            verified_at=row[4],
            verifier=str(row[5]) if row[5] is not None else None,
            notes=str(row[6]) if row[6] is not None else None,
        )

    def list_status(
        self,
        status: VerificationStatus,
    ) -> tuple[ResearchReference, ...]:
        ids = self._con.execute(
            """
            SELECT source_id
            FROM research_sources
            WHERE status = ?
            ORDER BY source_id
            """,
            [status.value],
        ).fetchall()
        return tuple(
            reference
            for source_id, in ids
            if (reference := self.get(str(source_id))) is not None
        )

    @classmethod
    def _validate_metadata(cls, raw: object) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ResearchCatalogError("source record must be an object")
        missing = [field for field in cls.REQUIRED_FIELDS if field not in raw]
        if missing:
            raise ResearchCatalogError(
                "source record missing required fields: " + ", ".join(missing)
            )
        source_id = raw.get("id")
        if not isinstance(source_id, str) or not source_id.strip():
            raise ResearchCatalogError("source id must be a non-empty string")
        for list_field in ("supports", "do_not_infer"):
            value = raw.get(list_field)
            if not isinstance(value, list) or not all(
                isinstance(item, str) and item.strip() for item in value
            ):
                raise ResearchCatalogError(
                    f"{source_id}: {list_field} must be a list of strings"
                )
        return dict(raw)

    def close(self) -> None:
        self._con.close()
