from __future__ import annotations

import json
from pathlib import Path

import duckdb

from .models import Hypothesis, HypothesisStatus, EpistemicState, ResearchDecision


class ResearchLedger:
    """Append-only ledger for generated hypotheses and gate decisions."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._con = duckdb.connect(self.path)
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS hypotheses (
                hypothesis_id VARCHAR PRIMARY KEY,
                entity_id VARCHAR NOT NULL,
                statement VARCHAR NOT NULL,
                generated_at TIMESTAMPTZ NOT NULL,
                evidence_event_ids_json VARCHAR NOT NULL,
                expected_value DOUBLE,
                observed_value DOUBLE,
                surprise DOUBLE,
                epistemic_state VARCHAR NOT NULL,
                status VARCHAR NOT NULL
            )
            """
        )
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS research_decisions (
                hypothesis_id VARCHAR NOT NULL,
                approved_for_shadow BOOLEAN NOT NULL,
                reasons_json VARCHAR NOT NULL,
                decision_time TIMESTAMPTZ NOT NULL
            )
            """
        )

    def record_hypothesis(self, hypothesis: Hypothesis) -> None:
        exists = self._con.execute(
            "SELECT 1 FROM hypotheses WHERE hypothesis_id = ?",
            [hypothesis.hypothesis_id],
        ).fetchone()
        if exists:
            raise ValueError(f"duplicate hypothesis_id: {hypothesis.hypothesis_id}")
        self._con.execute(
            """
            INSERT INTO hypotheses VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                hypothesis.hypothesis_id,
                hypothesis.entity_id,
                hypothesis.statement,
                hypothesis.generated_at,
                json.dumps(hypothesis.evidence_event_ids),
                hypothesis.expected_value,
                hypothesis.observed_value,
                hypothesis.surprise,
                hypothesis.epistemic_state.value,
                hypothesis.status.value,
            ],
        )

    def record_decision(self, decision: ResearchDecision) -> None:
        self._con.execute(
            """
            INSERT INTO research_decisions VALUES (?, ?, ?, ?)
            """,
            [
                decision.hypothesis_id,
                decision.approved_for_shadow,
                json.dumps(decision.reasons),
                decision.decision_time,
            ],
        )

    def hypotheses(self) -> tuple[Hypothesis, ...]:
        rows = self._con.execute(
            """
            SELECT hypothesis_id, entity_id, statement, generated_at,
                   evidence_event_ids_json, expected_value, observed_value,
                   surprise, epistemic_state, status
            FROM hypotheses
            ORDER BY generated_at, hypothesis_id
            """
        ).fetchall()
        return tuple(
            Hypothesis(
                hypothesis_id=str(row[0]),
                entity_id=str(row[1]),
                statement=str(row[2]),
                generated_at=row[3],
                evidence_event_ids=tuple(json.loads(str(row[4]))),
                expected_value=row[5],
                observed_value=row[6],
                surprise=row[7],
                epistemic_state=EpistemicState(str(row[8])),
                status=HypothesisStatus(str(row[9])),
            )
            for row in rows
        )

    def close(self) -> None:
        self._con.close()
