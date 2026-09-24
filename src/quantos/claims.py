from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

import duckdb

from .models import EpistemicState


class ClaimType(str, Enum):
    THEORY = "THEORY"
    EMPIRICAL = "EMPIRICAL"
    CURRENT_FACT = "CURRENT_FACT"
    INFERENCE = "INFERENCE"
    IMPLEMENTATION = "IMPLEMENTATION"


class ClaimStance(str, Enum):
    SUPPORTS = "SUPPORTS"
    LIMITS = "LIMITS"
    CONTRADICTS = "CONTRADICTS"
    NEUTRAL = "NEUTRAL"


@dataclass(frozen=True)
class ClaimCard:
    text: str
    claim_type: ClaimType
    stance: ClaimStance
    epistemic_state: EpistemicState
    topic: str
    source_artifact_ids: tuple[str, ...]
    locator: str | None
    scope: dict[str, object]
    assumptions: tuple[str, ...]
    limitations: tuple[str, ...]
    as_of: datetime
    claim_id: str


@dataclass(frozen=True)
class EvidenceBundle:
    query: str
    supporting: tuple[ClaimCard, ...]
    limiting: tuple[ClaimCard, ...]
    contradicting: tuple[ClaimCard, ...]


def make_claim_id(
    *,
    text: str,
    source_artifact_ids: tuple[str, ...],
    locator: str | None,
) -> str:
    material = json.dumps(
        {
            "text": " ".join(text.split()),
            "sources": sorted(source_artifact_ids),
            "locator": locator,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "claim:" + hashlib.sha256(material).hexdigest()


class ClaimStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS claim_cards (
                claim_id VARCHAR PRIMARY KEY,
                text VARCHAR NOT NULL,
                claim_type VARCHAR NOT NULL,
                stance VARCHAR NOT NULL,
                epistemic_state VARCHAR NOT NULL,
                topic VARCHAR NOT NULL,
                source_artifact_ids_json VARCHAR NOT NULL,
                locator VARCHAR,
                scope_json VARCHAR NOT NULL,
                assumptions_json VARCHAR NOT NULL,
                limitations_json VARCHAR NOT NULL,
                as_of TIMESTAMPTZ NOT NULL
            )
            """
        )

    def add(self, card: ClaimCard) -> None:
        if card.as_of.tzinfo is None:
            raise ValueError("claim as_of must be timezone-aware")
        if card.epistemic_state is not EpistemicState.UNKNOWN and not card.source_artifact_ids:
            raise ValueError("material claim cards require source artifacts")

        expected_id = make_claim_id(
            text=card.text,
            source_artifact_ids=card.source_artifact_ids,
            locator=card.locator,
        )
        if card.claim_id != expected_id:
            raise ValueError("claim_id does not match claim contents")

        existing = self._con.execute(
            "SELECT text FROM claim_cards WHERE claim_id = ?", [card.claim_id]
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != card.text:
                raise ValueError("claim ID collision/conflict")
            return

        self._con.execute(
            """
            INSERT INTO claim_cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                card.claim_id,
                card.text,
                card.claim_type.value,
                card.stance.value,
                card.epistemic_state.value,
                card.topic,
                json.dumps(card.source_artifact_ids),
                card.locator,
                json.dumps(card.scope, sort_keys=True),
                json.dumps(card.assumptions),
                json.dumps(card.limitations),
                card.as_of,
            ],
        )

    def all(self) -> tuple[ClaimCard, ...]:
        rows = self._con.execute(
            """
            SELECT claim_id, text, claim_type, stance, epistemic_state, topic,
                   source_artifact_ids_json, locator, scope_json,
                   assumptions_json, limitations_json, as_of
            FROM claim_cards
            ORDER BY as_of, claim_id
            """
        ).fetchall()
        return tuple(self._row(row) for row in rows)

    @staticmethod
    def _row(row: tuple[object, ...]) -> ClaimCard:
        return ClaimCard(
            claim_id=str(row[0]),
            text=str(row[1]),
            claim_type=ClaimType(str(row[2])),
            stance=ClaimStance(str(row[3])),
            epistemic_state=EpistemicState(str(row[4])),
            topic=str(row[5]),
            source_artifact_ids=tuple(json.loads(str(row[6]))),
            locator=str(row[7]) if row[7] is not None else None,
            scope=json.loads(str(row[8])),
            assumptions=tuple(json.loads(str(row[9]))),
            limitations=tuple(json.loads(str(row[10]))),
            as_of=row[11],
        )

    def close(self) -> None:
        self._con.close()


class EvidenceRetriever:
    """Deterministic lexical retrieval with mandatory counter-evidence buckets."""

    _token = re.compile(r"[A-Za-z0-9_]+")

    def __init__(self, store: ClaimStore) -> None:
        self.store = store

    @classmethod
    def _tokens(cls, text: str) -> set[str]:
        return {m.group(0).lower() for m in cls._token.finditer(text)}

    def bundle(self, query: str, *, limit_per_bucket: int = 5) -> EvidenceBundle:
        query_tokens = self._tokens(query)
        scored: list[tuple[int, ClaimCard]] = []
        for card in self.store.all():
            haystack = f"{card.topic} {card.text} " + " ".join(card.limitations)
            score = len(query_tokens & self._tokens(haystack))
            if score:
                scored.append((score, card))
        scored.sort(key=lambda item: (-item[0], item[1].claim_id))

        def bucket(*stances: ClaimStance) -> tuple[ClaimCard, ...]:
            return tuple(
                card
                for _, card in scored
                if card.stance in stances
            )[:limit_per_bucket]

        return EvidenceBundle(
            query=query,
            supporting=bucket(ClaimStance.SUPPORTS, ClaimStance.NEUTRAL),
            limiting=bucket(ClaimStance.LIMITS),
            contradicting=bucket(ClaimStance.CONTRADICTS),
        )
