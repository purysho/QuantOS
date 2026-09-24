from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import duckdb


@dataclass(frozen=True)
class MarketReaction:
    event_id: str
    security_id: str
    measured_at: datetime
    horizon: str
    security_return: float
    benchmark_return: float
    residual_return: float


class MarketReactionEngine:
    @staticmethod
    def measure(
        *,
        event_id: str,
        security_id: str,
        measured_at: datetime,
        horizon: str,
        security_pre: float,
        security_post: float,
        benchmark_pre: float,
        benchmark_post: float,
    ) -> MarketReaction:
        if measured_at.tzinfo is None:
            raise ValueError("measured_at must be timezone-aware")
        for name, value in {
            "security_pre": security_pre,
            "security_post": security_post,
            "benchmark_pre": benchmark_pre,
            "benchmark_post": benchmark_post,
        }.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")

        security_return = security_post / security_pre - 1.0
        benchmark_return = benchmark_post / benchmark_pre - 1.0
        return MarketReaction(
            event_id=event_id,
            security_id=security_id,
            measured_at=measured_at,
            horizon=horizon,
            security_return=security_return,
            benchmark_return=benchmark_return,
            residual_return=security_return - benchmark_return,
        )


class ReactionLedger:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS market_reactions (
                event_id VARCHAR NOT NULL,
                security_id VARCHAR NOT NULL,
                measured_at TIMESTAMPTZ NOT NULL,
                horizon VARCHAR NOT NULL,
                security_return DOUBLE NOT NULL,
                benchmark_return DOUBLE NOT NULL,
                residual_return DOUBLE NOT NULL,
                PRIMARY KEY (event_id, security_id, horizon)
            )
            """
        )

    def record(self, reaction: MarketReaction) -> None:
        existing = self._con.execute(
            """
            SELECT security_return, benchmark_return, residual_return
            FROM market_reactions
            WHERE event_id = ? AND security_id = ? AND horizon = ?
            """,
            [reaction.event_id, reaction.security_id, reaction.horizon],
        ).fetchone()
        if existing is not None:
            old = tuple(float(x) for x in existing)
            new = (
                reaction.security_return,
                reaction.benchmark_return,
                reaction.residual_return,
            )
            if old != new:
                raise ValueError("conflicting market-reaction measurement")
            return

        self._con.execute(
            "INSERT INTO market_reactions VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                reaction.event_id,
                reaction.security_id,
                reaction.measured_at,
                reaction.horizon,
                reaction.security_return,
                reaction.benchmark_return,
                reaction.residual_return,
            ],
        )

    def close(self) -> None:
        self._con.close()
