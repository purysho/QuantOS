from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import duckdb

from .models import Event


class DuckDBEventStore:
    """Durable point-in-time event store for the prototype."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._con = duckdb.connect(self.path)
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                event_id VARCHAR PRIMARY KEY,
                entity_id VARCHAR NOT NULL,
                event_type VARCHAR NOT NULL,
                event_time TIMESTAMPTZ NOT NULL,
                knowledge_time TIMESTAMPTZ NOT NULL,
                source_id VARCHAR NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def append(self, event: Event) -> None:
        exists = self._con.execute(
            "SELECT 1 FROM events WHERE event_id = ?", [event.event_id]
        ).fetchone()
        if exists:
            raise ValueError(f"duplicate event_id: {event.event_id}")
        self._con.execute(
            """
            INSERT INTO events
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                event.event_id,
                event.entity_id,
                event.event_type,
                event.event_time,
                event.knowledge_time,
                event.source_id,
                json.dumps(event.payload, sort_keys=True, separators=(",", ":")),
            ],
        )

    def get(self, event_id: str) -> Event | None:
        row = self._con.execute(
            """
            SELECT event_id, entity_id, event_type, event_time,
                   knowledge_time, source_id, payload_json
            FROM events
            WHERE event_id = ?
            """,
            [event_id],
        ).fetchone()
        return self._row_to_event(row) if row else None

    def all(self) -> tuple[Event, ...]:
        rows = self._con.execute(
            """
            SELECT event_id, entity_id, event_type, event_time,
                   knowledge_time, source_id, payload_json
            FROM events
            ORDER BY knowledge_time, event_time, event_id
            """
        ).fetchall()
        return tuple(self._row_to_event(row) for row in rows)

    def known_as_of(
        self,
        knowledge_time: datetime,
        *,
        entity_id: str | None = None,
        event_type: str | None = None,
    ) -> tuple[Event, ...]:
        if knowledge_time.tzinfo is None:
            raise ValueError("knowledge_time must be timezone-aware")

        sql = """
            SELECT event_id, entity_id, event_type, event_time,
                   knowledge_time, source_id, payload_json
            FROM events
            WHERE knowledge_time <= ?
        """
        params: list[object] = [knowledge_time]
        if entity_id is not None:
            sql += " AND entity_id = ?"
            params.append(entity_id)
        if event_type is not None:
            sql += " AND event_type = ?"
            params.append(event_type)
        sql += " ORDER BY knowledge_time, event_time, event_id"
        rows = self._con.execute(sql, params).fetchall()
        return tuple(self._row_to_event(row) for row in rows)

    def latest_known(
        self,
        knowledge_time: datetime,
        *,
        entity_id: str,
        event_type: str,
    ) -> Event | None:
        rows = self.known_as_of(
            knowledge_time,
            entity_id=entity_id,
            event_type=event_type,
        )
        return rows[-1] if rows else None

    def export_parquet(self, path: str | Path) -> None:
        target = str(path).replace("'", "''")
        self._con.execute(
            f"COPY (SELECT * FROM events ORDER BY knowledge_time, event_time, event_id) "
            f"TO '{target}' (FORMAT PARQUET)"
        )

    def close(self) -> None:
        self._con.close()

    @staticmethod
    def _row_to_event(row: tuple[object, ...]) -> Event:
        return Event(
            event_id=str(row[0]),
            entity_id=str(row[1]),
            event_type=str(row[2]),
            event_time=row[3],
            knowledge_time=row[4],
            source_id=str(row[5]),
            payload=json.loads(str(row[6])),
        )
