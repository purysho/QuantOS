from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb


@dataclass(frozen=True)
class RelationEdge:
    edge_id: str
    from_entity: str
    to_entity: str
    relation_type: str
    knowledge_time: datetime
    effective_from: datetime
    effective_to: datetime | None
    source_claim_ids: tuple[str, ...]
    confidence: float
    attributes: dict[str, object]


def make_edge_id(
    *,
    from_entity: str,
    to_entity: str,
    relation_type: str,
    effective_from: datetime,
    source_claim_ids: tuple[str, ...],
) -> str:
    material = json.dumps(
        {
            "from": from_entity,
            "to": to_entity,
            "relation": relation_type,
            "effective_from": effective_from.isoformat(),
            "claims": sorted(source_claim_ids),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "edge:" + hashlib.sha256(material).hexdigest()


class EntityGraphStore:
    """Time-bounded, provenance-required relationship graph."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS entity_edges (
                edge_id VARCHAR PRIMARY KEY,
                from_entity VARCHAR NOT NULL,
                to_entity VARCHAR NOT NULL,
                relation_type VARCHAR NOT NULL,
                knowledge_time TIMESTAMPTZ NOT NULL,
                effective_from TIMESTAMPTZ NOT NULL,
                effective_to TIMESTAMPTZ,
                source_claim_ids_json VARCHAR NOT NULL,
                confidence DOUBLE NOT NULL,
                attributes_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, edge: RelationEdge) -> None:
        for name in ("knowledge_time", "effective_from"):
            if getattr(edge, name).tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")
        if edge.effective_to is not None:
            if edge.effective_to.tzinfo is None:
                raise ValueError("effective_to must be timezone-aware")
            if edge.effective_to < edge.effective_from:
                raise ValueError("effective_to precedes effective_from")
        if edge.from_entity == edge.to_entity:
            raise ValueError("self-relations are not allowed")
        if not edge.source_claim_ids:
            raise ValueError("entity relationships require source claim provenance")
        if not 0.0 <= edge.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if edge.knowledge_time < edge.effective_from:
            # Future-effective relations may be known in advance, so this is valid.
            pass

        expected_id = make_edge_id(
            from_entity=edge.from_entity,
            to_entity=edge.to_entity,
            relation_type=edge.relation_type,
            effective_from=edge.effective_from,
            source_claim_ids=edge.source_claim_ids,
        )
        if edge.edge_id != expected_id:
            raise ValueError("edge_id does not match edge contents")

        existing = self._con.execute(
            """
            SELECT from_entity, to_entity, relation_type, knowledge_time,
                   effective_from, effective_to, source_claim_ids_json,
                   confidence, attributes_json
            FROM entity_edges WHERE edge_id = ?
            """,
            [edge.edge_id],
        ).fetchone()
        serialized_claims = json.dumps(edge.source_claim_ids)
        serialized_attributes = json.dumps(edge.attributes, sort_keys=True)
        if existing is not None:
            old = (
                str(existing[0]),
                str(existing[1]),
                str(existing[2]),
                existing[3],
                existing[4],
                existing[5],
                str(existing[6]),
                float(existing[7]),
                str(existing[8]),
            )
            new = (
                edge.from_entity,
                edge.to_entity,
                edge.relation_type,
                edge.knowledge_time,
                edge.effective_from,
                edge.effective_to,
                serialized_claims,
                edge.confidence,
                serialized_attributes,
            )
            if old != new:
                raise ValueError("conflicting entity-edge contents")
            return

        self._con.execute(
            "INSERT INTO entity_edges VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                edge.edge_id,
                edge.from_entity,
                edge.to_entity,
                edge.relation_type,
                edge.knowledge_time,
                edge.effective_from,
                edge.effective_to,
                serialized_claims,
                edge.confidence,
                serialized_attributes,
            ],
        )

    def neighbors(
        self,
        entity_id: str,
        *,
        knowledge_time: datetime,
        effective_time: datetime | None = None,
        relation_type: str | None = None,
    ) -> tuple[RelationEdge, ...]:
        if knowledge_time.tzinfo is None:
            raise ValueError("knowledge_time must be timezone-aware")
        when = effective_time or knowledge_time
        if when.tzinfo is None:
            raise ValueError("effective_time must be timezone-aware")

        sql = """
            SELECT edge_id, from_entity, to_entity, relation_type,
                   knowledge_time, effective_from, effective_to,
                   source_claim_ids_json, confidence, attributes_json
            FROM entity_edges
            WHERE knowledge_time <= ?
              AND effective_from <= ?
              AND (effective_to IS NULL OR effective_to >= ?)
              AND (from_entity = ? OR to_entity = ?)
        """
        params: list[object] = [knowledge_time, when, when, entity_id, entity_id]
        if relation_type is not None:
            sql += " AND relation_type = ?"
            params.append(relation_type)
        sql += " ORDER BY relation_type, from_entity, to_entity, edge_id"
        rows = self._con.execute(sql, params).fetchall()
        return tuple(self._row(row) for row in rows)

    def paths(
        self,
        start_entity: str,
        *,
        knowledge_time: datetime,
        effective_time: datetime | None = None,
        max_hops: int = 3,
    ) -> tuple[tuple[RelationEdge, ...], ...]:
        if not 1 <= max_hops <= 4:
            raise ValueError("max_hops must be between 1 and 4")
        results: list[tuple[RelationEdge, ...]] = []
        queue: list[tuple[str, tuple[RelationEdge, ...], frozenset[str]]] = [
            (start_entity, (), frozenset({start_entity}))
        ]

        while queue:
            entity, path, visited = queue.pop(0)
            if len(path) >= max_hops:
                continue
            for edge in self.neighbors(
                entity,
                knowledge_time=knowledge_time,
                effective_time=effective_time,
            ):
                next_entity = (
                    edge.to_entity if edge.from_entity == entity else edge.from_entity
                )
                if next_entity in visited:
                    continue
                new_path = path + (edge,)
                results.append(new_path)
                queue.append((next_entity, new_path, visited | {next_entity}))

        return tuple(results)

    @staticmethod
    def _row(row: tuple[object, ...]) -> RelationEdge:
        return RelationEdge(
            edge_id=str(row[0]),
            from_entity=str(row[1]),
            to_entity=str(row[2]),
            relation_type=str(row[3]),
            knowledge_time=row[4],
            effective_from=row[5],
            effective_to=row[6],
            source_claim_ids=tuple(json.loads(str(row[7]))),
            confidence=float(row[8]),
            attributes=json.loads(str(row[9])),
        )

    def close(self) -> None:
        self._con.close()