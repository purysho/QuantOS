from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .claim_evidence_graph import (
    ClaimEvidenceContext,
    ClaimEvidenceGraph,
    ReplicationOutcome,
)
from .claims import ClaimStore


class EvidencePosture(str, Enum):
    SPARSE_EVIDENCE = "SPARSE_EVIDENCE"
    SUPPORTING_EVIDENCE_PRESENT = "SUPPORTING_EVIDENCE_PRESENT"
    SUPPORTED_WITH_LIMITATIONS = "SUPPORTED_WITH_LIMITATIONS"
    LIMITED_EVIDENCE = "LIMITED_EVIDENCE"
    DISPUTED = "DISPUTED"
    REPLICATION_SUPPORTED = "REPLICATION_SUPPORTED"
    FAILED_REPLICATION_PRESENT = "FAILED_REPLICATION_PRESENT"
    REPLICATION_CONFLICT = "REPLICATION_CONFLICT"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class DossierFlag:
    code: str
    detail: str


@dataclass(frozen=True)
class EvidenceDossier:
    claim_id: str
    posture: EvidencePosture
    context: ClaimEvidenceContext
    supporting_count: int
    limiting_count: int
    contradicting_count: int
    extension_count: int
    replication_count: int
    independent_replication_count: int
    flags: tuple[DossierFlag, ...]
    caveat: str


class EvidenceDossierBuilder:
    """Summarize evidence structure without producing a truth/confidence score."""

    CAVEAT = (
        "Evidence posture is a structural summary, not a probability of truth, "
        "forecast, expected return, or capital recommendation."
    )

    def build(
        self,
        claim_id: str,
        *,
        claims: ClaimStore,
        evidence_graph: ClaimEvidenceGraph,
    ) -> EvidenceDossier:
        context = evidence_graph.context(claim_id, claims=claims)
        reps = context.replications

        replicate = tuple(
            record
            for record in reps
            if record.outcome is ReplicationOutcome.REPLICATES
        )
        failed = tuple(
            record
            for record in reps
            if record.outcome is ReplicationOutcome.FAILS_TO_REPLICATE
        )
        partial = tuple(
            record
            for record in reps
            if record.outcome is ReplicationOutcome.PARTIAL
        )
        inconclusive = tuple(
            record
            for record in reps
            if record.outcome is ReplicationOutcome.INCONCLUSIVE
        )
        independent = tuple(record for record in reps if record.independent_data)

        posture = self._posture(
            context=context,
            replicates=replicate,
            failed=failed,
            partial=partial,
            inconclusive=inconclusive,
        )
        flags = self._flags(
            context=context,
            replicates=replicate,
            failed=failed,
            partial=partial,
            inconclusive=inconclusive,
            independent=independent,
        )

        return EvidenceDossier(
            claim_id=claim_id,
            posture=posture,
            context=context,
            supporting_count=len(context.supporting),
            limiting_count=len(context.limiting),
            contradicting_count=len(context.contradicting),
            extension_count=len(context.extensions),
            replication_count=len(reps),
            independent_replication_count=len(independent),
            flags=flags,
            caveat=self.CAVEAT,
        )

    @staticmethod
    def _posture(
        *,
        context: ClaimEvidenceContext,
        replicates: tuple[object, ...],
        failed: tuple[object, ...],
        partial: tuple[object, ...],
        inconclusive: tuple[object, ...],
    ) -> EvidencePosture:
        if replicates and failed:
            return EvidencePosture.REPLICATION_CONFLICT
        if failed:
            return EvidencePosture.FAILED_REPLICATION_PRESENT
        if context.contradicting:
            return EvidencePosture.DISPUTED
        if replicates:
            return EvidencePosture.REPLICATION_SUPPORTED
        if context.supporting and context.limiting:
            return EvidencePosture.SUPPORTED_WITH_LIMITATIONS
        if context.supporting:
            return EvidencePosture.SUPPORTING_EVIDENCE_PRESENT
        if context.limiting:
            return EvidencePosture.LIMITED_EVIDENCE
        if partial or inconclusive or context.extensions:
            return EvidencePosture.INCONCLUSIVE
        return EvidencePosture.SPARSE_EVIDENCE

    @staticmethod
    def _flags(
        *,
        context: ClaimEvidenceContext,
        replicates: tuple[object, ...],
        failed: tuple[object, ...],
        partial: tuple[object, ...],
        inconclusive: tuple[object, ...],
        independent: tuple[object, ...],
    ) -> tuple[DossierFlag, ...]:
        flags: list[DossierFlag] = []
        if not (
            context.supporting
            or context.limiting
            or context.contradicting
            or context.extensions
            or context.replications
        ):
            flags.append(
                DossierFlag(
                    "NO_EXTERNAL_CONTEXT",
                    "No reviewed claim relationship or replication is recorded.",
                )
            )
        if context.contradicting:
            flags.append(
                DossierFlag(
                    "CONTRADICTORY_CLAIMS_PRESENT",
                    f"{len(context.contradicting)} reviewed contradicting claim(s) are recorded.",
                )
            )
        if context.limiting:
            flags.append(
                DossierFlag(
                    "LIMITATIONS_PRESENT",
                    f"{len(context.limiting)} reviewed limiting claim(s) are recorded.",
                )
            )
        if failed:
            flags.append(
                DossierFlag(
                    "FAILED_REPLICATION_PRESENT",
                    f"{len(failed)} failed replication record(s) are preserved.",
                )
            )
        if replicates and failed:
            flags.append(
                DossierFlag(
                    "REPLICATION_RESULTS_CONFLICT",
                    "Both successful and failed replication outcomes are recorded.",
                )
            )
        if partial:
            flags.append(
                DossierFlag(
                    "PARTIAL_REPLICATION_PRESENT",
                    f"{len(partial)} partial replication record(s) are recorded.",
                )
            )
        if inconclusive:
            flags.append(
                DossierFlag(
                    "INCONCLUSIVE_REPLICATION_PRESENT",
                    f"{len(inconclusive)} inconclusive replication record(s) are recorded.",
                )
            )
        if context.replications and not independent:
            flags.append(
                DossierFlag(
                    "NO_INDEPENDENT_DATA_REPLICATION",
                    "Replication records exist, but none use independently identified data.",
                )
            )
        if not context.replications:
            flags.append(
                DossierFlag(
                    "NO_REPLICATION_RECORD",
                    "No replication record has been attached to this claim.",
                )
            )
        return tuple(flags)
