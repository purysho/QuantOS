from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .artifacts import ArtifactRef, SourceArtifactStore
from .research_catalog import ResearchCatalog, ResearchReference, VerificationStatus
from .research_radar import DiscoveryItem, ResearchRadarStore
from .review_queue import ResearchReviewQueue, ReviewItem, ReviewStatus


class ResearchReviewError(ValueError):
    pass


@dataclass(frozen=True)
class CatalogAdmission:
    queue_id: str
    discovery_id: str
    source_id: str
    catalog_status: VerificationStatus


@dataclass(frozen=True)
class SourceVerification:
    source_id: str
    artifact: ArtifactRef
    reference: ResearchReference


class ResearchReviewService:
    """Explicit gates from discovery review to source-artifact verification.

    Gate 1: CATALOG_CANDIDATE -> QUARANTINED bibliographic record.
    Gate 2: QUARANTINED record + independently checked exact source bytes ->
            VERIFIED source identity.

    Neither gate creates or validates empirical Claim Cards.
    """

    def admit_catalog_candidate(
        self,
        *,
        queue_id: str,
        review_queue: ResearchReviewQueue,
        radar_store: ResearchRadarStore,
        catalog: ResearchCatalog,
    ) -> CatalogAdmission:
        review = review_queue.get(queue_id)
        if review is None:
            raise ResearchReviewError(f"unknown review item: {queue_id}")
        if review.status is not ReviewStatus.CATALOG_CANDIDATE:
            raise ResearchReviewError(
                "only CATALOG_CANDIDATE items may enter the research catalog"
            )
        if not review.review_notes:
            raise ResearchReviewError("catalog candidate is missing review notes")

        discovery = radar_store.get(review.discovery_id)
        if discovery is None:
            raise ResearchReviewError(
                f"radar discovery not found: {review.discovery_id}"
            )
        self._check_identity(review, discovery)

        source_id = self._source_id(discovery)
        metadata = self._metadata(
            source_id=source_id,
            discovery=discovery,
            review=review,
        )
        result = catalog.import_registry(
            {
                "library": "research-radar",
                "version": "1",
                "source_count": 1,
                "sources": [metadata],
            }
        )
        reference = catalog.get(source_id)
        assert reference is not None
        if reference.status is not VerificationStatus.QUARANTINED:
            # Idempotent re-admission of an already verified reference is allowed,
            # but admission itself must never be what verifies it.
            if result.inserted:
                raise ResearchReviewError(
                    "new radar admission unexpectedly bypassed quarantine"
                )
        return CatalogAdmission(
            queue_id=queue_id,
            discovery_id=discovery.discovery_id,
            source_id=source_id,
            catalog_status=reference.status,
        )

    def verify_source_file(
        self,
        *,
        source_id: str,
        file_path: str | Path,
        source_uri: str,
        verifier: str,
        notes: str,
        catalog: ResearchCatalog,
        artifact_store: SourceArtifactStore,
        verified_at: datetime | None = None,
        media_type: str | None = None,
        max_bytes: int = 100 * 1024 * 1024,
    ) -> SourceVerification:
        current = catalog.get(source_id)
        if current is None:
            raise ResearchReviewError(f"unknown catalog source: {source_id}")
        if current.status is VerificationStatus.REJECTED:
            raise ResearchReviewError("rejected source cannot be verified")
        if not source_uri.strip():
            raise ResearchReviewError("source_uri is required")
        if not verifier.strip():
            raise ResearchReviewError("verifier is required")
        if not notes.strip():
            raise ResearchReviewError(
                "verification notes must describe the source-identity check"
            )
        if max_bytes <= 0:
            raise ResearchReviewError("max_bytes must be positive")

        path = Path(file_path)
        if not path.is_file():
            raise ResearchReviewError(f"source file not found: {path}")
        size = path.stat().st_size
        if size <= 0:
            raise ResearchReviewError("source file is empty")
        if size > max_bytes:
            raise ResearchReviewError(
                f"source file exceeds configured limit of {max_bytes} bytes"
            )

        content = path.read_bytes()
        moment = verified_at or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            raise ResearchReviewError("verified_at must be timezone-aware")
        detected = media_type or mimetypes.guess_type(path.name)[0]
        artifact = artifact_store.put(
            source_uri=source_uri.strip(),
            content=content,
            fetched_at=moment,
            media_type=detected or "application/octet-stream",
        )
        reference = catalog.verify(
            source_id=source_id,
            artifact_id=artifact.artifact_id,
            verified_at=moment,
            verifier=verifier.strip(),
            notes=notes.strip(),
        )
        return SourceVerification(
            source_id=source_id,
            artifact=artifact,
            reference=reference,
        )

    @staticmethod
    def _source_id(discovery: DiscoveryItem) -> str:
        provider = discovery.provider.upper().replace(":", "_")
        external_id = discovery.external_id.replace(":", "_")
        return f"{provider}:{external_id}"

    @staticmethod
    def _check_identity(
        review: ReviewItem,
        discovery: DiscoveryItem,
    ) -> None:
        if review.discovery_id != discovery.discovery_id:
            raise ResearchReviewError("review/discovery identity mismatch")
        if review.provider != discovery.provider:
            raise ResearchReviewError("review/discovery provider mismatch")
        if review.canonical_id != discovery.canonical_id:
            raise ResearchReviewError("review/discovery canonical ID mismatch")
        if review.external_id != discovery.external_id:
            raise ResearchReviewError("review/discovery revision mismatch")
        if review.source_uri != discovery.source_uri:
            raise ResearchReviewError("review/discovery source URI mismatch")

    @staticmethod
    def _metadata(
        *,
        source_id: str,
        discovery: DiscoveryItem,
        review: ReviewItem,
    ) -> dict[str, object]:
        return {
            "id": source_id,
            "domain": "research_discovery",
            "type": "preprint",
            "authority": "UNASSESSED",
            "title": discovery.title,
            "authors": ", ".join(discovery.authors),
            "year": discovery.published_at.year,
            "url": discovery.source_uri,
            "stance": "UNASSESSED",
            "supports": [],
            "do_not_infer": [
                "Do not treat publication, abstract text, or radar attention as verified empirical evidence.",
                "Do not infer replicability, causality, economic significance, or investment alpha before source verification and claim-level review.",
            ],
            "provider": discovery.provider,
            "external_id": discovery.external_id,
            "canonical_id": discovery.canonical_id,
            "categories": list(discovery.categories),
            "abstract_metadata": discovery.summary,
            "published_at": discovery.published_at.isoformat(),
            "updated_at": discovery.updated_at.isoformat(),
            "discovered_at": discovery.discovered_at.isoformat(),
            "feed_artifact_id": discovery.feed_artifact_id,
            "review_queue_id": review.queue_id,
            "reviewer": review.reviewer,
            "review_notes": review.review_notes,
            "attention_score_at_queue": review.initial_attention_score,
            "attention_band_at_queue": review.initial_attention_band,
        }
