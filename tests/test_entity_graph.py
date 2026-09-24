import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from quantos.entity_graph import EntityGraphStore, RelationEdge, make_edge_id


UTC = timezone.utc


def edge(
    *,
    source: str,
    target: str,
    relation: str,
    knowledge: datetime,
    effective: datetime,
    claim: str,
) -> RelationEdge:
    claims = (claim,)
    return RelationEdge(
        edge_id=make_edge_id(
            from_entity=source,
            to_entity=target,
            relation_type=relation,
            effective_from=effective,
            source_claim_ids=claims,
        ),
        from_entity=source,
        to_entity=target,
        relation_type=relation,
        knowledge_time=knowledge,
        effective_from=effective,
        effective_to=None,
        source_claim_ids=claims,
        confidence=0.9,
        attributes={},
    )


class EntityGraphTests(unittest.TestCase):
    def test_future_known_relationship_does_not_leak_backwards(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = EntityGraphStore(Path(tmp) / "graph.duckdb")
            graph.add(
                edge(
                    source="SUPPLIER",
                    target="CUSTOMER",
                    relation="supplies",
                    knowledge=datetime(2026, 6, 1, tzinfo=UTC),
                    effective=datetime(2026, 1, 1, tzinfo=UTC),
                    claim="claim:1",
                )
            )
            before = graph.neighbors(
                "SUPPLIER",
                knowledge_time=datetime(2026, 5, 31, tzinfo=UTC),
                effective_time=datetime(2026, 5, 31, tzinfo=UTC),
            )
            after = graph.neighbors(
                "SUPPLIER",
                knowledge_time=datetime(2026, 6, 2, tzinfo=UTC),
                effective_time=datetime(2026, 6, 2, tzinfo=UTC),
            )
            self.assertEqual(before, ())
            self.assertEqual(len(after), 1)
            graph.close()

    def test_relationship_without_claim_provenance_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = EntityGraphStore(Path(tmp) / "graph.duckdb")
            now = datetime(2026, 1, 1, tzinfo=UTC)
            bad = RelationEdge(
                edge_id=make_edge_id(
                    from_entity="A",
                    to_entity="B",
                    relation_type="supplies",
                    effective_from=now,
                    source_claim_ids=(),
                ),
                from_entity="A",
                to_entity="B",
                relation_type="supplies",
                knowledge_time=now,
                effective_from=now,
                effective_to=None,
                source_claim_ids=(),
                confidence=0.5,
                attributes={},
            )
            with self.assertRaises(ValueError):
                graph.add(bad)
            graph.close()

    def test_paths_are_bounded_and_cycle_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = EntityGraphStore(Path(tmp) / "graph.duckdb")
            now = datetime(2026, 1, 1, tzinfo=UTC)
            graph.add(edge(source="A", target="B", relation="supplies", knowledge=now, effective=now, claim="c1"))
            graph.add(edge(source="B", target="C", relation="supplies", knowledge=now, effective=now, claim="c2"))
            graph.add(edge(source="C", target="A", relation="supplies", knowledge=now, effective=now, claim="c3"))
            paths = graph.paths("A", knowledge_time=now, max_hops=2)
            self.assertTrue(paths)
            self.assertTrue(all(len(path) <= 2 for path in paths))
            graph.close()


if __name__ == "__main__":
    unittest.main()
