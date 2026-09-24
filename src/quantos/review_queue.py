from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

import duckdb

from .radar_triage import TriageResult
from .research_radar import DiscoveryItem


class ReviewStatus(str, Enum):
    QUEUED = "QUEUED"
    UNDER_REVIEW = "UNDER_REVIEW"
    DISMISSED = "DISMISSED"
    CATALOG_CANDIDATE = "CATALOG_CANDIDATE"


@dataclass(frozen=True)
class ReviewItem:
    queue_id: str
    discovery_id: str
    provider: str
    canonical_id: str
    external_id: str
    source_uri: str
    feed_artifact_id: str | None
    profile_id: str
    initial_attention_score: float
    initial_attention_band: str
    queued_at: datetime
    status: ReviewStatus
    reviewer: str | None
    reviewed_at: datetime | None
    review_notes: str | None


def make_queue_id(*, discovery_id: str, profile_id: str) -> str:
    material = f"{discovery_id}\n{profile_id}".encode("utf-8")
    return "review:" + hashlib.sha256(material).hexdigest()


class ResearchReviewQueue:
    """Human/research review quarantine between discovery and trusted catalog.

    Enqueueing means only "worth investigating." It cannot verify a source,
    create a Claim Card, or promote anything into the trusted research corpus.
    """

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS research_review_queue (
                queue_id VARCHAR PRIMARY KEY,
                discovery_id VARCHAR NOT NULL,
                provider VARCHAR NOT NULL,
                canonical_id VARCHAR NOT NULL,
                external_id VARCHAR NOT NULL,
                source_uri VARCHAR NOT NULL,
                feed_artifact_id VARCHAR,
                profile_id VARCHAR NOT NULL,
                initial_attention_score DOUBLE NOT NULL,
                initial_attention_band VARCHAR NOT NULL,
                queued_at TIMESTAMPTZ NOT NULL,
                status VARCHAR NOT NULL,
                reviewer VARCHAR,
                reviewed_at TIMESTAMPTZ,
                review_notes VARCHAR
            )
            """
        )

    def enqueue(
        self,
        *,
        discovery: DiscoveryItem,
        triage: TriageResult,
        queued_at: datetime,
    ) -> ReviewItem:
        if queued_at.tzinfo is None:
            raise ValueError("queued_at must be timezone-aware")
        if discovery.discovery_id != triage.discovery_id:
            raise ValueError("discovery and triage IDs do not match")
        if not 0.0 <= triage.attention_score <= 1.0:
            raise ValueError("attention score must be between 0 and 1")

        queue_id = make_queue_id(
            discovery_id=discovery.discovery_id,
            profile_id=triage.profile_id,
        )
        existing = self.get(queue_id)
        if existing is not None:
            if (
                existing.discovery_id != discovery.discovery_id
                or existing.provider != discovery.provider
                or existing.canonical_id != discovery.canonical_id
                or existing.external_id != discovery.external_id
                or existing.source_uri != discovery.source_uri
                or existing.feed_artifact_id != discovery.feed_artifact_id
                or existing.profile_id != triage.profile_id
            ):
                raise ValueError("review queue identity conflict")
            return existing

        self._con.execute(
            """
            INSERT INTO research_review_queue
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
            """,
            [
                queue_id,
                discovery.discovery_id,
                discovery.provider,
                discovery.canonical_id,
                discovery.external_id,
                discovery.source_uri,
                discovery.feed_artifact_id,
                triage.profile_id,
                triage.attention_score,
                triage.attention_band,
                queued_at,
                ReviewStatus.QUEUED.value,
            ],
        )
        result = self.get(queue_id)
        assert result is not None
        return result

    def start_review(
        self,
        *,
        queue_id: str,
        reviewer: str,
        reviewed_at: datetime,
    ) -> ReviewItem:
        if not reviewer.strip():
            raise ValueError("reviewer is required")
        if reviewed_at.tzinfo is None:
            raise ValueError("reviewed_at must be timezone-aware")
        current = self._required(queue_id)
        if current.status is ReviewStatus.UNDER_REVIEW:
            if current.reviewer != reviewer.strip():
                raise ValueError("review item is already assigned to another reviewer")
            return current
        if current.status is not ReviewStatus.QUEUED:
            raise ValueError(f"cannot start review from {current.status.value}")

        self._con.execute(
            """
            UPDATE research_review_queue
            SET status = ?, reviewer = ?, reviewed_at = ?
            WHERE queue_id = ?
            """,
            [
                ReviewStatus.UNDER_REVIEW.value,
                reviewer.strip(),
                reviewed_at,
                queue_id,
            ],
        )
        return self._required(queue_id)

    def dismiss(
        self,
        *,
        queue_id: str,
        reviewer: str,
        reviewed_at: datetime,
        notes: str,
    ) -> ReviewItem:
        return self._complete(
            queue_id=queue_id,
            reviewer=reviewer,
            reviewed_at=reviewed_at,
            notes=notes,
            target=ReviewStatus.DISMISSED,
        )

    def mark_catalog_candidate(
        self,
        *,
        queue_id: str,
        reviewer: str,
        reviewed_at: datetime,
        notes: str,
    ) -> ReviewItem:
        return self._complete(
            queue_id=queue_id,
            reviewer=reviewer,
            reviewed_at=reviewed_at,
            notes=notes,
            target=ReviewStatus.CATALOG_CANDIDATE,
        )

    def _complete(
        self,
        *,
        queue_id: str,
        reviewer: str,
        reviewed_at: datetime,
        notes: str,
        target: ReviewStatus,
    ) -> ReviewItem:
        if not reviewer.strip():
            raise ValueError("reviewer is required")
        if reviewed_at.tzinfo is None:
            raise ValueError("reviewed_at must be timezone-aware")
        if not notes.strip():
            raise ValueError("review notes are required")
        current = self._required(queue_id)
        if current.status is not ReviewStatus.UNDER_REVIEW:
            raise ValueError(
                f"review must be UNDER_REVIEW before {target.value}"
            )
        if current.reviewer != reviewer.strip():
            raise ValueError("only the assigned reviewer may complete review")

        self._con.execute(
            """
            UPDATE research_review_queue
            SET status = ?, reviewed_at = ?, review_notes = ?
            WHERE queue_id = ?
            """,
            [target.value, reviewed_at, notes.strip(), queue_id],
        )
        return self._required(queue_id)

    def get(self, queue_id: str) -> ReviewItem | None:
        row = self._con.execute(
            """
            SELECT queue_id, discovery_id, provider, canonical_id, external_id,
                   source_uri, feed_artifact_id, profile_id,
                   initial_attention_score, initial_attention_band, queued_at,
                   status, reviewer, reviewed_at, review_notes
            FROM research_review_queue
            WHERE queue_id = ?
            """,
            [queue_id],
        ).fetchone()
        return None if row is None else self._row(row)

    def list_status(
        self,
        status: ReviewStatus,
        *,
        limit: int = 100,
    ) -> tuple[ReviewItem, ...]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        rows = self._con.execute(
            """
            SELECT queue_id, discovery_id, provider, canonical_id, external_id,
                   source_uri, feed_artifact_id, profile_id,
                   initial_attention_score, initial_attention_band, queued_at,
                   status, reviewer, reviewed_at, review_notes
            FROM research_review_queue
            WHERE status = ?
            ORDER BY initial_attention_score DESC, queued_at, queue_id
            LIMIT ?
            """,
            [status.value, limit],
        ).fetchall()
        return tuple(self._row(row) for row in rows)

    def _required(self, queue_id: str) -> ReviewItem:
        item = self.get(queue_id)
        if item is None:
            raise KeyError(queue_id)
        return item

    @staticmethod
    def _row(row: tuple[object, ...]) -> ReviewItem:
        return ReviewItem(
            queue_id=str(row[0]),
            discovery_id=str(row[1]),
            provider=str(row[2]),
            canonical_id=str(row[3]),
            external_id=str(row[4]),
            source_uri=str(row[5]),
            feed_artifact_id=str(row[6]) if row[6] is not None else None,
            profile_id=str(row[7]),
            initial_attention_score=float(row[8]),
            initial_attention_band=str(row[9]),
            queued_at=row[10],
            status=ReviewStatus(str(row[11])),
            reviewer=str(row[12]) if row[12] is not None else None,
            reviewed_at=row[13],
            review_notes=str(row[14]) if row[14] is not None else None,
        )

    def close(self) -> None:
        self._con.close()
