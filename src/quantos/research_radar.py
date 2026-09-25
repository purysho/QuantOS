from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

import duckdb


class DiscoveryStatus(str, Enum):
    DISCOVERED = "DISCOVERED"
    TRIAGED = "TRIAGED"
    DISMISSED = "DISMISSED"


@dataclass(frozen=True)
class DiscoveryItem:
    discovery_id: str
    provider: str
    external_id: str
    canonical_id: str
    title: str
    summary: str
    authors: tuple[str, ...]
    categories: tuple[str, ...]
    published_at: datetime
    updated_at: datetime
    discovered_at: datetime
    source_uri: str
    feed_artifact_id: str | None
    status: DiscoveryStatus = DiscoveryStatus.DISCOVERED


@dataclass(frozen=True)
class RadarIngestResult:
    inserted: int
    skipped_identical: int


def make_discovery_id(
    *,
    provider: str,
    canonical_id: str,
    updated_at: datetime,
) -> str:
    material = json.dumps(
        {
            "provider": provider,
            "canonical_id": canonical_id,
            "updated_at": updated_at.isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "discovery:" + hashlib.sha256(material).hexdigest()


class ResearchRadarStore:
    """Append-oriented store for externally discovered research metadata."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS radar_items (
                discovery_id VARCHAR PRIMARY KEY,
                provider VARCHAR NOT NULL,
                external_id VARCHAR NOT NULL,
                canonical_id VARCHAR NOT NULL,
                title VARCHAR NOT NULL,
                summary VARCHAR NOT NULL,
                authors_json VARCHAR NOT NULL,
                categories_json VARCHAR NOT NULL,
                published_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                discovered_at TIMESTAMPTZ NOT NULL,
                source_uri VARCHAR NOT NULL,
                feed_artifact_id VARCHAR,
                status VARCHAR NOT NULL
            )
            """
        )

    def ingest(self, items: tuple[DiscoveryItem, ...]) -> RadarIngestResult:
        inserted = 0
        skipped = 0
        for item in items:
            self._validate(item)
            canonical = self._canonical_payload(item)
            existing = self._con.execute(
                """
                SELECT provider, external_id, canonical_id, title, summary,
                       authors_json, categories_json, published_at, updated_at,
                       source_uri, feed_artifact_id, status
                FROM radar_items
                WHERE discovery_id = ?
                """,
                [item.discovery_id],
            ).fetchone()
            if existing is not None:
                # The feed snapshot is provenance of the first sighting, not
                # identity: a feed re-published with new bytes (another item,
                # a new build date) still carries the same item. Every fetch
                # stays archived; the stored item keeps its first snapshot.
                old = self._canonical_row(existing)
                if old[:10] + old[11:] != canonical[:10] + canonical[11:]:
                    raise ValueError(
                        f"discovery identity conflict: {item.discovery_id}"
                    )
                skipped += 1
                continue

            self._con.execute(
                """
                INSERT INTO radar_items
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    item.discovery_id,
                    item.provider,
                    item.external_id,
                    item.canonical_id,
                    item.title,
                    item.summary,
                    json.dumps(item.authors, ensure_ascii=False),
                    json.dumps(item.categories),
                    item.published_at,
                    item.updated_at,
                    item.discovered_at,
                    item.source_uri,
                    item.feed_artifact_id,
                    item.status.value,
                ],
            )
            inserted += 1
        return RadarIngestResult(inserted=inserted, skipped_identical=skipped)

    def get(self, discovery_id: str) -> DiscoveryItem | None:
        row = self._con.execute(
            """
            SELECT discovery_id, provider, external_id, canonical_id,
                   title, summary, authors_json, categories_json,
                   published_at, updated_at, discovered_at, source_uri,
                   feed_artifact_id, status
            FROM radar_items
            WHERE discovery_id = ?
            """,
            [discovery_id],
        ).fetchone()
        return None if row is None else self._row(row)

    def latest(
        self,
        *,
        provider: str | None = None,
        limit: int = 50,
    ) -> tuple[DiscoveryItem, ...]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        sql = """
            SELECT discovery_id, provider, external_id, canonical_id,
                   title, summary, authors_json, categories_json,
                   published_at, updated_at, discovered_at, source_uri,
                   feed_artifact_id, status
            FROM radar_items
        """
        params: list[object] = []
        if provider is not None:
            sql += " WHERE provider = ?"
            params.append(provider)
        sql += " ORDER BY discovered_at DESC, updated_at DESC, discovery_id LIMIT ?"
        params.append(limit)
        rows = self._con.execute(sql, params).fetchall()
        return tuple(self._row(row) for row in rows)

    def versions(
        self,
        *,
        provider: str,
        canonical_id: str,
    ) -> tuple[DiscoveryItem, ...]:
        rows = self._con.execute(
            """
            SELECT discovery_id, provider, external_id, canonical_id,
                   title, summary, authors_json, categories_json,
                   published_at, updated_at, discovered_at, source_uri,
                   feed_artifact_id, status
            FROM radar_items
            WHERE provider = ? AND canonical_id = ?
            ORDER BY updated_at, discovery_id
            """,
            [provider, canonical_id],
        ).fetchall()
        return tuple(self._row(row) for row in rows)

    @staticmethod
    def _validate(item: DiscoveryItem) -> None:
        for name in ("published_at", "updated_at", "discovered_at"):
            if getattr(item, name).tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")
        if item.updated_at < item.published_at:
            raise ValueError("updated_at precedes published_at")
        expected = make_discovery_id(
            provider=item.provider,
            canonical_id=item.canonical_id,
            updated_at=item.updated_at,
        )
        if item.discovery_id != expected:
            raise ValueError("discovery_id does not match discovery contents")
        if not item.provider or not item.external_id or not item.canonical_id:
            raise ValueError("provider and external IDs are required")
        if not item.title.strip():
            raise ValueError("title is required")

    @staticmethod
    def _canonical_payload(item: DiscoveryItem) -> tuple[object, ...]:
        return (
            item.provider,
            item.external_id,
            item.canonical_id,
            item.title,
            item.summary,
            json.dumps(item.authors, ensure_ascii=False),
            json.dumps(item.categories),
            item.published_at,
            item.updated_at,
            item.source_uri,
            item.feed_artifact_id,
            item.status.value,
        )

    @staticmethod
    def _canonical_row(row: tuple[object, ...]) -> tuple[object, ...]:
        return (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            str(row[3]),
            str(row[4]),
            str(row[5]),
            str(row[6]),
            row[7],
            row[8],
            str(row[9]),
            str(row[10]) if row[10] is not None else None,
            str(row[11]),
        )

    @staticmethod
    def _row(row: tuple[object, ...]) -> DiscoveryItem:
        return DiscoveryItem(
            discovery_id=str(row[0]),
            provider=str(row[1]),
            external_id=str(row[2]),
            canonical_id=str(row[3]),
            title=str(row[4]),
            summary=str(row[5]),
            authors=tuple(json.loads(str(row[6]))),
            categories=tuple(json.loads(str(row[7]))),
            published_at=row[8],
            updated_at=row[9],
            discovered_at=row[10],
            source_uri=str(row[11]),
            feed_artifact_id=str(row[12]) if row[12] is not None else None,
            status=DiscoveryStatus(str(row[13])),
        )

    def close(self) -> None:
        self._con.close()