from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

import duckdb

from .claims import (
    ClaimCard,
    ClaimStance,
    ClaimStore,
    ClaimType,
    make_claim_id,
)
from .models import EpistemicState
from .research_catalog import ResearchCatalog, VerificationStatus


class ClaimWorkbenchError(ValueError):
    pass


class ClaimDraftStatus(str, Enum):
    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PROMOTED = "PROMOTED"


@dataclass(frozen=True)
class ClaimDraft:
    draft_id: str
    source_id: str
    source_artifact_id: str
    text: str
    claim_type: ClaimType
    stance: ClaimStance
    epistemic_state: EpistemicState
    topic: str
    locator: str
    scope: dict[str, object]
    assumptions: tuple[str, ...]
    limitations: tuple[str, ...]
    as_of: datetime
    drafter: str
    created_at: datetime
    status: ClaimDraftStatus
    reviewer: str | None
    reviewed_at: datetime | None
    counter_evidence_notes: str | None
    review_notes: str | None
    promoted_claim_id: str | None


def make_draft_id(
    *,
    source_id: str,
    source_artifact_id: str,
    text: str,
    locator: str,
    claim_type: ClaimType,
    stance: ClaimStance,
) -> str:
    material = json.dumps(
        {
            "source_id": source_id,
            "source_artifact_id": source_artifact_id,
            "text": " ".join(text.split()),
            "locator": locator.strip(),
            "claim_type": claim_type.value,
            "stance": stance.value,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "claim-draft:" + hashlib.sha256(material).hexdigest()


class ClaimWorkbench:
    """Independent-review workflow between verified sources and ClaimStore."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS claim_drafts (
                draft_id VARCHAR PRIMARY KEY,
                source_id VARCHAR NOT NULL,
                source_artifact_id VARCHAR NOT NULL,
                text VARCHAR NOT NULL,
                claim_type VARCHAR NOT NULL,
                stance VARCHAR NOT NULL,
                epistemic_state VARCHAR NOT NULL,
                topic VARCHAR NOT NULL,
                locator VARCHAR NOT NULL,
                scope_json VARCHAR NOT NULL,
                assumptions_json VARCHAR NOT NULL,
                limitations_json VARCHAR NOT NULL,
                as_of TIMESTAMPTZ NOT NULL,
                drafter VARCHAR NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                status VARCHAR NOT NULL,
                reviewer VARCHAR,
                reviewed_at TIMESTAMPTZ,
                counter_evidence_notes VARCHAR,
                review_notes VARCHAR,
                promoted_claim_id VARCHAR
            )
            """
        )

    def create_draft(
        self,
        *,
        source_id: str,
        text: str,
        claim_type: ClaimType,
        stance: ClaimStance,
        epistemic_state: EpistemicState,
        topic: str,
        locator: str,
        scope: dict[str, object],
        assumptions: tuple[str, ...],
        limitations: tuple[str, ...],
        as_of: datetime,
        drafter: str,
        created_at: datetime,
        catalog: ResearchCatalog,
    ) -> ClaimDraft:
        source = catalog.get(source_id)
        if source is None:
            raise ClaimWorkbenchError(f"unknown research source: {source_id}")
        if source.status is not VerificationStatus.VERIFIED or not source.artifact_id:
            raise ClaimWorkbenchError(
                "claim drafting requires a VERIFIED source artifact"
            )
        self._validate_claim_fields(
            text=text,
            claim_type=claim_type,
            topic=topic,
            locator=locator,
            scope=scope,
            limitations=limitations,
            as_of=as_of,
        )
        if not drafter.strip():
            raise ClaimWorkbenchError("drafter is required")
        if created_at.tzinfo is None:
            raise ClaimWorkbenchError("created_at must be timezone-aware")

        draft_id = make_draft_id(
            source_id=source_id,
            source_artifact_id=source.artifact_id,
            text=text,
            locator=locator,
            claim_type=claim_type,
            stance=stance,
        )
        existing = self.get(draft_id)
        if existing is not None:
            canonical = (
                existing.source_id,
                existing.source_artifact_id,
                existing.text,
                existing.claim_type,
                existing.stance,
                existing.epistemic_state,
                existing.topic,
                existing.locator,
                existing.scope,
                existing.assumptions,
                existing.limitations,
                existing.as_of,
                existing.drafter,
            )
            requested = (
                source_id,
                source.artifact_id,
                text,
                claim_type,
                stance,
                epistemic_state,
                topic,
                locator.strip(),
                scope,
                assumptions,
                limitations,
                as_of,
                drafter.strip(),
            )
            if canonical != requested:
                raise ClaimWorkbenchError("claim draft identity conflict")
            return existing

        self._con.execute(
            """
            INSERT INTO claim_drafts
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL)
            """,
            [
                draft_id,
                source_id,
                source.artifact_id,
                text,
                claim_type.value,
                stance.value,
                epistemic_state.value,
                topic.strip(),
                locator.strip(),
                json.dumps(scope, sort_keys=True),
                json.dumps(assumptions),
                json.dumps(limitations),
                as_of,
                drafter.strip(),
                created_at,
                ClaimDraftStatus.DRAFT.value,
            ],
        )
        result = self.get(draft_id)
        assert result is not None
        return result

    def submit_for_review(
        self,
        *,
        draft_id: str,
        reviewer: str,
        reviewed_at: datetime,
    ) -> ClaimDraft:
        current = self._required(draft_id)
        if current.status is not ClaimDraftStatus.DRAFT:
            raise ClaimWorkbenchError(
                f"cannot submit {current.status.value} draft for review"
            )
        if not reviewer.strip():
            raise ClaimWorkbenchError("reviewer is required")
        if reviewer.strip() == current.drafter:
            raise ClaimWorkbenchError("drafter and reviewer must differ")
        if reviewed_at.tzinfo is None:
            raise ClaimWorkbenchError("reviewed_at must be timezone-aware")

        self._con.execute(
            """
            UPDATE claim_drafts
            SET status = ?, reviewer = ?, reviewed_at = ?
            WHERE draft_id = ?
            """,
            [
                ClaimDraftStatus.IN_REVIEW.value,
                reviewer.strip(),
                reviewed_at,
                draft_id,
            ],
        )
        return self._required(draft_id)

    def approve(
        self,
        *,
        draft_id: str,
        reviewer: str,
        reviewed_at: datetime,
        counter_evidence_notes: str,
        review_notes: str,
    ) -> ClaimDraft:
        return self._complete_review(
            draft_id=draft_id,
            reviewer=reviewer,
            reviewed_at=reviewed_at,
            counter_evidence_notes=counter_evidence_notes,
            review_notes=review_notes,
            target=ClaimDraftStatus.APPROVED,
        )

    def reject(
        self,
        *,
        draft_id: str,
        reviewer: str,
        reviewed_at: datetime,
        counter_evidence_notes: str,
        review_notes: str,
    ) -> ClaimDraft:
        return self._complete_review(
            draft_id=draft_id,
            reviewer=reviewer,
            reviewed_at=reviewed_at,
            counter_evidence_notes=counter_evidence_notes,
            review_notes=review_notes,
            target=ClaimDraftStatus.REJECTED,
        )

    def promote(
        self,
        *,
        draft_id: str,
        catalog: ResearchCatalog,
        claim_store: ClaimStore,
    ) -> ClaimCard:
        current = self._required(draft_id)
        if current.status is ClaimDraftStatus.PROMOTED:
            return self._to_card(current)
        if current.status is not ClaimDraftStatus.APPROVED:
            raise ClaimWorkbenchError("only APPROVED claim drafts may be promoted")

        source = catalog.get(current.source_id)
        if source is None:
            raise ClaimWorkbenchError("claim source no longer exists in catalog")
        if source.status is not VerificationStatus.VERIFIED:
            raise ClaimWorkbenchError("claim source is no longer VERIFIED")
        if source.artifact_id != current.source_artifact_id:
            raise ClaimWorkbenchError(
                "verified source artifact changed after claim review"
            )

        card = self._to_card(current)
        catalog.promote_research_claim(card, claim_store=claim_store)
        self._con.execute(
            """
            UPDATE claim_drafts
            SET status = ?, promoted_claim_id = ?
            WHERE draft_id = ?
            """,
            [ClaimDraftStatus.PROMOTED.value, card.claim_id, draft_id],
        )
        return card

    def get(self, draft_id: str) -> ClaimDraft | None:
        row = self._con.execute(
            """
            SELECT draft_id, source_id, source_artifact_id, text,
                   claim_type, stance, epistemic_state, topic, locator,
                   scope_json, assumptions_json, limitations_json, as_of,
                   drafter, created_at, status, reviewer, reviewed_at,
                   counter_evidence_notes, review_notes, promoted_claim_id
            FROM claim_drafts
            WHERE draft_id = ?
            """,
            [draft_id],
        ).fetchone()
        return None if row is None else self._row(row)

    def list_status(
        self,
        status: ClaimDraftStatus,
        *,
        limit: int = 100,
    ) -> tuple[ClaimDraft, ...]:
        if not 1 <= limit <= 1000:
            raise ClaimWorkbenchError("limit must be between 1 and 1000")
        rows = self._con.execute(
            """
            SELECT draft_id, source_id, source_artifact_id, text,
                   claim_type, stance, epistemic_state, topic, locator,
                   scope_json, assumptions_json, limitations_json, as_of,
                   drafter, created_at, status, reviewer, reviewed_at,
                   counter_evidence_notes, review_notes, promoted_claim_id
            FROM claim_drafts
            WHERE status = ?
            ORDER BY created_at, draft_id
            LIMIT ?
            """,
            [status.value, limit],
        ).fetchall()
        return tuple(self._row(row) for row in rows)

    def _complete_review(
        self,
        *,
        draft_id: str,
        reviewer: str,
        reviewed_at: datetime,
        counter_evidence_notes: str,
        review_notes: str,
        target: ClaimDraftStatus,
    ) -> ClaimDraft:
        current = self._required(draft_id)
        if current.status is not ClaimDraftStatus.IN_REVIEW:
            raise ClaimWorkbenchError(
                "claim must be IN_REVIEW before reviewer disposition"
            )
        if current.reviewer != reviewer.strip():
            raise ClaimWorkbenchError(
                "only the assigned independent reviewer may decide the claim"
            )
        if reviewed_at.tzinfo is None:
            raise ClaimWorkbenchError("reviewed_at must be timezone-aware")
        if not counter_evidence_notes.strip():
            raise ClaimWorkbenchError(
                "counter-evidence search notes are required"
            )
        if not review_notes.strip():
            raise ClaimWorkbenchError("review notes are required")

        self._con.execute(
            """
            UPDATE claim_drafts
            SET status = ?, reviewed_at = ?,
                counter_evidence_notes = ?, review_notes = ?
            WHERE draft_id = ?
            """,
            [
                target.value,
                reviewed_at,
                counter_evidence_notes.strip(),
                review_notes.strip(),
                draft_id,
            ],
        )
        return self._required(draft_id)

    @staticmethod
    def _validate_claim_fields(
        *,
        text: str,
        claim_type: ClaimType,
        topic: str,
        locator: str,
        scope: dict[str, object],
        limitations: tuple[str, ...],
        as_of: datetime,
    ) -> None:
        if not text.strip():
            raise ClaimWorkbenchError("claim text is required")
        if not topic.strip():
            raise ClaimWorkbenchError("claim topic is required")
        if not locator.strip():
            raise ClaimWorkbenchError("exact source locator is required")
        if as_of.tzinfo is None:
            raise ClaimWorkbenchError("claim as_of must be timezone-aware")
        if claim_type in {ClaimType.EMPIRICAL, ClaimType.INFERENCE}:
            if not scope:
                raise ClaimWorkbenchError(
                    "empirical/inference claims require explicit scope"
                )
            if not limitations or not all(item.strip() for item in limitations):
                raise ClaimWorkbenchError(
                    "empirical/inference claims require explicit limitations"
                )

    @staticmethod
    def _to_card(draft: ClaimDraft) -> ClaimCard:
        sources = (draft.source_artifact_id,)
        return ClaimCard(
            text=draft.text,
            claim_type=draft.claim_type,
            stance=draft.stance,
            epistemic_state=draft.epistemic_state,
            topic=draft.topic,
            source_artifact_ids=sources,
            locator=draft.locator,
            scope=draft.scope,
            assumptions=draft.assumptions,
            limitations=draft.limitations,
            as_of=draft.as_of,
            claim_id=make_claim_id(
                text=draft.text,
                source_artifact_ids=sources,
                locator=draft.locator,
            ),
        )

    def _required(self, draft_id: str) -> ClaimDraft:
        draft = self.get(draft_id)
        if draft is None:
            raise KeyError(draft_id)
        return draft

    @staticmethod
    def _row(row: tuple[object, ...]) -> ClaimDraft:
        return ClaimDraft(
            draft_id=str(row[0]),
            source_id=str(row[1]),
            source_artifact_id=str(row[2]),
            text=str(row[3]),
            claim_type=ClaimType(str(row[4])),
            stance=ClaimStance(str(row[5])),
            epistemic_state=EpistemicState(str(row[6])),
            topic=str(row[7]),
            locator=str(row[8]),
            scope=json.loads(str(row[9])),
            assumptions=tuple(json.loads(str(row[10]))),
            limitations=tuple(json.loads(str(row[11]))),
            as_of=row[12],
            drafter=str(row[13]),
            created_at=row[14],
            status=ClaimDraftStatus(str(row[15])),
            reviewer=str(row[16]) if row[16] is not None else None,
            reviewed_at=row[17],
            counter_evidence_notes=(
                str(row[18]) if row[18] is not None else None
            ),
            review_notes=str(row[19]) if row[19] is not None else None,
            promoted_claim_id=str(row[20]) if row[20] is not None else None,
        )

    def close(self) -> None:
        self._con.close()
