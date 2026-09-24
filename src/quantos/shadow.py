from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb


@dataclass(frozen=True)
class ShadowObservation:
    signal_id: str
    hypothesis_id: str
    measured_at: datetime
    expected_direction: int
    residual_return: float
    score: float


@dataclass(frozen=True)
class EdgeHealth:
    signal_id: str
    observations: int
    mean_signed_residual: float | None
    hit_rate: float | None
    standard_error: float | None
    state: str


class ShadowLedger:
    """Prospective signal ledger. It measures; it never authorizes capital."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS shadow_observations (
                signal_id VARCHAR NOT NULL,
                hypothesis_id VARCHAR NOT NULL,
                measured_at TIMESTAMPTZ NOT NULL,
                expected_direction INTEGER NOT NULL,
                residual_return DOUBLE NOT NULL,
                score DOUBLE NOT NULL,
                PRIMARY KEY (signal_id, hypothesis_id)
            )
            """
        )

    def record(
        self,
        *,
        signal_id: str,
        hypothesis_id: str,
        measured_at: datetime,
        expected_direction: int,
        residual_return: float,
    ) -> ShadowObservation:
        if measured_at.tzinfo is None:
            raise ValueError("measured_at must be timezone-aware")
        if expected_direction not in {-1, 1}:
            raise ValueError("expected_direction must be -1 or 1")
        score = expected_direction * residual_return
        observation = ShadowObservation(
            signal_id=signal_id,
            hypothesis_id=hypothesis_id,
            measured_at=measured_at,
            expected_direction=expected_direction,
            residual_return=float(residual_return),
            score=float(score),
        )
        existing = self._con.execute(
            """
            SELECT expected_direction, residual_return, score
            FROM shadow_observations
            WHERE signal_id = ? AND hypothesis_id = ?
            """,
            [signal_id, hypothesis_id],
        ).fetchone()
        if existing is not None:
            old = (int(existing[0]), float(existing[1]), float(existing[2]))
            new = (
                observation.expected_direction,
                observation.residual_return,
                observation.score,
            )
            if old != new:
                raise ValueError("conflicting shadow observation")
            return observation

        self._con.execute(
            "INSERT INTO shadow_observations VALUES (?, ?, ?, ?, ?, ?)",
            [
                signal_id,
                hypothesis_id,
                measured_at,
                expected_direction,
                residual_return,
                score,
            ],
        )
        return observation

    def health(
        self,
        signal_id: str,
        *,
        minimum_observations: int = 20,
    ) -> EdgeHealth:
        rows = self._con.execute(
            """
            SELECT score
            FROM shadow_observations
            WHERE signal_id = ?
            ORDER BY measured_at, hypothesis_id
            """,
            [signal_id],
        ).fetchall()
        scores = [float(row[0]) for row in rows]
        n = len(scores)
        if n < minimum_observations:
            return EdgeHealth(
                signal_id=signal_id,
                observations=n,
                mean_signed_residual=None,
                hit_rate=None,
                standard_error=None,
                state="INSUFFICIENT_EVIDENCE",
            )

        mean = sum(scores) / n
        hits = sum(1 for score in scores if score > 0) / n
        variance = sum((score - mean) ** 2 for score in scores) / max(n - 1, 1)
        se = math.sqrt(variance / n)
        return EdgeHealth(
            signal_id=signal_id,
            observations=n,
            mean_signed_residual=mean,
            hit_rate=hits,
            standard_error=se,
            state="MEASURED",
        )

    def close(self) -> None:
        self._con.close()
