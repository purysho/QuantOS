from __future__ import annotations

import argparse
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Protocol

from .adapters.arxiv_radar import ARXIV_FINANCE_CATEGORIES, ArxivRadarAdapter, ArxivRadarFetch
from .adapters.crossref_radar import FINANCE_JOURNAL_ISSNS, CrossrefRadarAdapter
from .adapters.feed_radar import FEEDS_BY_ID, WORKING_PAPER_FEEDS, FeedRadarAdapter
from .artifacts import SourceArtifactStore
from .observability import span
from .radar_triage import RadarTriageEngine, RadarTriageStore, TriageResult
from .research_catalog import ResearchCatalog
from .research_radar import DiscoveryItem, ResearchRadarStore
from .research_review import ResearchReviewService
from .review_queue import ResearchReviewQueue, ReviewStatus


DEFAULT_USER_AGENT = (
    "QuantOS prototype/0.4.3 "
    "(+https://github.com/purysho/QuantOS)"
)


class RadarAdapter(Protocol):
    def fetch(
        self,
        *,
        categories: tuple[str, ...],
        max_results: int,
        start: int,
        artifact_store: SourceArtifactStore | None,
    ) -> ArxivRadarFetch: ...


def scan_arxiv(
    *,
    categories: tuple[str, ...],
    max_results: int,
    radar_db: str,
    artifact_root: str,
    artifact_db: str,
    triage_db: str,
    review_db: str,
    queue_threshold: float = 0.50,
    print_limit: int = 10,
    adapter: RadarAdapter | None = None,
) -> tuple[TriageResult, ...]:
    def fetch(artifacts: SourceArtifactStore):
        client = adapter or ArxivRadarAdapter(
            user_agent=os.environ.get("ARXIV_USER_AGENT", DEFAULT_USER_AGENT)
        )
        return client.fetch(
            categories=categories,
            max_results=max_results,
            start=0,
            artifact_store=artifacts,
        )

    return _scan(
        provider="arxiv",
        label="ARXIV_RADAR",
        fetch=fetch,
        radar_db=radar_db,
        artifact_root=artifact_root,
        artifact_db=artifact_db,
        triage_db=triage_db,
        review_db=review_db,
        queue_threshold=queue_threshold,
        print_limit=print_limit,
    )


def scan_crossref(
    *,
    issns: tuple[str, ...],
    from_index_date: date,
    query: str | None,
    max_results: int,
    radar_db: str,
    artifact_root: str,
    artifact_db: str,
    triage_db: str,
    review_db: str,
    queue_threshold: float = 0.50,
    print_limit: int = 10,
    adapter: CrossrefRadarAdapter | None = None,
) -> tuple[TriageResult, ...]:
    def fetch(artifacts: SourceArtifactStore):
        if adapter is not None:
            client = adapter
        else:
            mailto = os.environ.get("CROSSREF_MAILTO", "")
            if not mailto:
                raise SystemExit(
                    "set CROSSREF_MAILTO to a contact address (Crossref etiquette)"
                )
            client = CrossrefRadarAdapter(
                mailto=mailto,
                user_agent=os.environ.get("CROSSREF_USER_AGENT", DEFAULT_USER_AGENT),
            )
        return client.fetch(
            issns=issns,
            from_index_date=from_index_date,
            query=query,
            max_results=max_results,
            artifact_store=artifacts,
        )

    return _scan(
        provider="crossref",
        label="CROSSREF_RADAR",
        fetch=fetch,
        radar_db=radar_db,
        artifact_root=artifact_root,
        artifact_db=artifact_db,
        triage_db=triage_db,
        review_db=review_db,
        queue_threshold=queue_threshold,
        print_limit=print_limit,
    )


def scan_ssrn(
    *,
    from_posted_date: date,
    query: str,
    max_results: int,
    radar_db: str,
    artifact_root: str,
    artifact_db: str,
    triage_db: str,
    review_db: str,
    queue_threshold: float = 0.50,
    print_limit: int = 10,
    adapter: CrossrefRadarAdapter | None = None,
) -> tuple[TriageResult, ...]:
    def fetch(artifacts: SourceArtifactStore):
        client = adapter or _crossref_client()
        return client.fetch_ssrn(
            from_posted_date=from_posted_date,
            query=query,
            max_results=max_results,
            artifact_store=artifacts,
        )

    return _scan(
        provider="crossref",
        label="SSRN_RADAR",
        fetch=fetch,
        radar_db=radar_db,
        artifact_root=artifact_root,
        artifact_db=artifact_db,
        triage_db=triage_db,
        review_db=review_db,
        queue_threshold=queue_threshold,
        print_limit=print_limit,
    )


def scan_feeds(
    *,
    source_ids: tuple[str, ...],
    max_items: int,
    radar_db: str,
    artifact_root: str,
    artifact_db: str,
    triage_db: str,
    review_db: str,
    queue_threshold: float = 0.50,
    print_limit: int = 10,
    adapter: FeedRadarAdapter | None = None,
) -> dict[str, tuple[TriageResult, ...]]:
    unknown = [s for s in source_ids if s not in FEEDS_BY_ID]
    if unknown:
        raise ValueError("unregistered feeds: " + ", ".join(unknown))
    client = adapter or FeedRadarAdapter(
        user_agent=os.environ.get("FEED_USER_AGENT", DEFAULT_USER_AGENT)
    )
    results: dict[str, tuple[TriageResult, ...]] = {}
    for source_id in source_ids:
        first_seen: dict[str, datetime] = {}
        if Path(radar_db).exists():
            store = ResearchRadarStore(radar_db)
            try:
                for item in store.latest(provider=source_id, limit=1000):
                    if "published-precision:first-seen" in item.categories:
                        first_seen.setdefault(item.canonical_id, item.published_at)
            finally:
                store.close()

        def fetch(artifacts: SourceArtifactStore, source_id=source_id, first_seen=first_seen):
            return client.fetch(
                source_id=source_id,
                max_items=max_items,
                first_seen=first_seen,
                artifact_store=artifacts,
            )

        results[source_id] = _scan(
            provider=source_id,
            label=f"FEED_RADAR[{source_id}]",
            fetch=fetch,
            radar_db=radar_db,
            artifact_root=artifact_root,
            artifact_db=artifact_db,
            triage_db=triage_db,
            review_db=review_db,
            queue_threshold=queue_threshold,
            print_limit=print_limit,
        )
    return results


def _crossref_client() -> CrossrefRadarAdapter:
    mailto = os.environ.get("CROSSREF_MAILTO", "")
    if not mailto:
        raise SystemExit("set CROSSREF_MAILTO to a contact address (Crossref etiquette)")
    return CrossrefRadarAdapter(
        mailto=mailto,
        user_agent=os.environ.get("CROSSREF_USER_AGENT", DEFAULT_USER_AGENT),
    )


def _scan(
    *,
    provider: str,
    label: str,
    fetch,
    radar_db: str,
    artifact_root: str,
    artifact_db: str,
    triage_db: str,
    review_db: str,
    queue_threshold: float,
    print_limit: int,
) -> tuple[TriageResult, ...]:
    if not 1 <= print_limit <= 100:
        raise ValueError("print_limit must be between 1 and 100")
    if not 0.0 <= queue_threshold <= 1.0:
        raise ValueError("queue_threshold must be between 0 and 1")

    Path(radar_db).parent.mkdir(parents=True, exist_ok=True)
    radar = ResearchRadarStore(radar_db)
    artifacts = SourceArtifactStore(artifact_root, artifact_db)
    triage = RadarTriageStore(triage_db)
    review = ResearchReviewQueue(review_db)
    try:
        prior = list(radar.latest(provider=provider, limit=1000))
        with span("radar", f"{provider}-fetch"):
            fetched = fetch(artifacts)
        ingest = radar.ingest(fetched.items)
        # Continue with the stored items so triage and review see each item's
        # first sighting (and its first feed snapshot) on every rescan.
        items = tuple(radar.get(item.discovery_id) or item for item in fetched.items)

        engine = RadarTriageEngine()
        scored: list[TriageResult] = []
        context = list(prior)
        triaged_at = datetime.now(timezone.utc)
        for item in items:
            result = engine.score(
                item,
                triaged_at=triaged_at,
                prior_items=tuple(context),
            )
            triage.record(result)
            scored.append(result)
            context.append(item)

        ranked = tuple(
            sorted(
                scored,
                key=lambda result: (
                    -result.attention_score,
                    result.discovery_id,
                ),
            )
        )

        artifact_id = (
            fetched.feed_artifact.artifact_id
            if fetched.feed_artifact is not None
            else "none"
        )
        print(
            label,
            f"fetched={len(fetched.items)}",
            f"inserted={ingest.inserted}",
            f"skipped={ingest.skipped_identical}",
            f"feed_artifact={artifact_id}",
        )
        by_id = {item.discovery_id: item for item in items}
        queued = 0
        queued_at = datetime.now(timezone.utc)
        for result in ranked:
            if result.attention_score >= queue_threshold:
                review.enqueue(
                    discovery=by_id[result.discovery_id],
                    triage=result,
                    queued_at=queued_at,
                )
                queued += 1

        for result in ranked[:print_limit]:
            item = by_id[result.discovery_id]
            themes = ",".join(match.theme for match in result.theme_matches) or "none"
            print(
                result.attention_band,
                f"score={result.attention_score:.3f}",
                f"relevance={result.relevance:.3f}",
                f"novelty={result.lexical_novelty:.3f}",
                f"freshness={result.freshness:.3f}",
                f"themes={themes}",
                item.canonical_id,
                item.title,
            )
        print(
            "REVIEW_QUEUE",
            f"queued_this_scan={queued}",
            f"queue_threshold={queue_threshold:.3f}",
            f"open={len(review.list_status(ReviewStatus.QUEUED, limit=1000))}",
        )
        print("RADAR_TRUST", "DISCOVERY_ONLY", "no claims promoted")
        return ranked
    finally:
        review.close()
        triage.close()
        artifacts.close()
        radar.close()



def list_reviews(*, review_db: str, status: str, limit: int) -> int:
    try:
        review_status = ReviewStatus(status)
    except ValueError as exc:
        raise SystemExit(f"invalid review status: {status}") from exc
    queue = ResearchReviewQueue(review_db)
    try:
        items = queue.list_status(review_status, limit=limit)
        for item in items:
            print(
                item.queue_id,
                item.status.value,
                f"score={item.initial_attention_score:.3f}",
                item.external_id,
                item.source_uri,
                item.reviewer or "-",
            )
        print("REVIEW_COUNT", len(items), review_status.value)
        return 0
    finally:
        queue.close()


def start_review(*, review_db: str, queue_id: str, reviewer: str) -> int:
    queue = ResearchReviewQueue(review_db)
    try:
        item = queue.start_review(
            queue_id=queue_id,
            reviewer=reviewer,
            reviewed_at=datetime.now(timezone.utc),
        )
        print("REVIEW_STATUS", item.queue_id, item.status.value, item.reviewer)
        return 0
    finally:
        queue.close()


def complete_review(
    *,
    review_db: str,
    queue_id: str,
    reviewer: str,
    notes: str,
    candidate: bool,
) -> int:
    queue = ResearchReviewQueue(review_db)
    try:
        now = datetime.now(timezone.utc)
        if candidate:
            item = queue.mark_catalog_candidate(
                queue_id=queue_id,
                reviewer=reviewer,
                reviewed_at=now,
                notes=notes,
            )
        else:
            item = queue.dismiss(
                queue_id=queue_id,
                reviewer=reviewer,
                reviewed_at=now,
                notes=notes,
            )
        print("REVIEW_STATUS", item.queue_id, item.status.value)
        return 0
    finally:
        queue.close()


def admit_catalog_candidate(
    *,
    review_db: str,
    radar_db: str,
    catalog_db: str,
    queue_id: str,
) -> int:
    queue = ResearchReviewQueue(review_db)
    radar = ResearchRadarStore(radar_db)
    catalog = ResearchCatalog(catalog_db)
    try:
        admission = ResearchReviewService().admit_catalog_candidate(
            queue_id=queue_id,
            review_queue=queue,
            radar_store=radar,
            catalog=catalog,
        )
        print(
            "CATALOG_ADMISSION",
            admission.source_id,
            admission.catalog_status.value,
            f"queue={admission.queue_id}",
        )
        return 0
    finally:
        catalog.close()
        radar.close()
        queue.close()


def verify_catalog_file(
    *,
    catalog_db: str,
    artifact_root: str,
    artifact_db: str,
    source_id: str,
    path: str,
    source_uri: str,
    verifier: str,
    notes: str,
) -> int:
    catalog = ResearchCatalog(catalog_db)
    artifacts = SourceArtifactStore(artifact_root, artifact_db)
    try:
        result = ResearchReviewService().verify_source_file(
            source_id=source_id,
            file_path=path,
            source_uri=source_uri,
            verifier=verifier,
            notes=notes,
            catalog=catalog,
            artifact_store=artifacts,
        )
        print(
            "CATALOG_VERIFIED_SOURCE",
            result.source_id,
            result.reference.status.value,
            result.artifact.artifact_id,
            f"bytes={result.artifact.byte_length}",
        )
        print("CLAIM_TRUST", "UNCHANGED", "no claims created")
        return 0
    finally:
        artifacts.close()
        catalog.close()


def _main() -> int:
    parser = argparse.ArgumentParser(
        prog="quantos-radar",
        description="Research discovery radar; never promotes claims automatically.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan-arxiv", help="scan arXiv descriptive metadata")
    scan.add_argument(
        "--category",
        action="append",
        dest="categories",
        help="explicit arXiv category; repeat for multiple categories",
    )
    scan.add_argument("--max-results", type=int, default=50)
    scan.add_argument("--radar-db", default="data/research-radar.duckdb")
    scan.add_argument("--artifact-root", default="data/artifacts")
    scan.add_argument("--artifact-db", default="data/artifacts.duckdb")
    scan.add_argument("--triage-db", default="data/radar-triage.duckdb")
    scan.add_argument("--review-db", default="data/research-review.duckdb")
    scan.add_argument("--queue-threshold", type=float, default=0.50)
    scan.add_argument("--print-limit", type=int, default=10)


    crossref = sub.add_parser(
        "scan-crossref",
        help="scan Crossref DOI metadata for journal articles (needs CROSSREF_MAILTO)",
    )
    crossref.add_argument(
        "--issn",
        action="append",
        dest="issns",
        help="explicit journal ISSN; repeat for multiple journals",
    )
    crossref.add_argument(
        "--from-index-date",
        required=True,
        help="only works (re)indexed by Crossref on or after this date (YYYY-MM-DD)",
    )
    crossref.add_argument("--query", default=None)
    crossref.add_argument("--max-results", type=int, default=50)
    crossref.add_argument("--radar-db", default="data/research-radar.duckdb")
    crossref.add_argument("--artifact-root", default="data/artifacts")
    crossref.add_argument("--artifact-db", default="data/artifacts.duckdb")
    crossref.add_argument("--triage-db", default="data/radar-triage.duckdb")
    crossref.add_argument("--review-db", default="data/research-review.duckdb")
    crossref.add_argument("--queue-threshold", type=float, default=0.50)
    crossref.add_argument("--print-limit", type=int, default=10)

    ssrn = sub.add_parser("scan-ssrn", help="scan SSRN working papers via Crossref (prefix 10.2139)")
    ssrn.add_argument("--query", required=True)
    ssrn.add_argument("--from-posted-date", required=True)
    feeds = sub.add_parser("scan-feeds", help="scan registered NBER / central-bank / regulator feeds")
    feeds.add_argument(
        "--source",
        dest="sources",
        action="append",
        choices=sorted(FEEDS_BY_ID),
        help="repeatable; default: all working-paper feeds",
    )
    feeds.add_argument("--max-items", type=int, default=50)
    for extra in (ssrn, feeds):
        if extra is ssrn:
            extra.add_argument("--max-results", type=int, default=50)
        extra.add_argument("--radar-db", default="data/research-radar.duckdb")
        extra.add_argument("--artifact-root", default="data/artifacts")
        extra.add_argument("--artifact-db", default="data/artifacts.duckdb")
        extra.add_argument("--triage-db", default="data/radar-triage.duckdb")
        extra.add_argument("--review-db", default="data/research-review.duckdb")
        extra.add_argument("--queue-threshold", type=float, default=0.50)
        extra.add_argument("--print-limit", type=int, default=10)

    review_list = sub.add_parser("review-list", help="list research review queue items")
    review_list.add_argument("--review-db", default="data/research-review.duckdb")
    review_list.add_argument("--status", default="QUEUED")
    review_list.add_argument("--limit", type=int, default=50)

    review_start = sub.add_parser("review-start", help="assign and start one review")
    review_start.add_argument("--review-db", default="data/research-review.duckdb")
    review_start.add_argument("--queue-id", required=True)
    review_start.add_argument("--reviewer", required=True)

    review_dismiss = sub.add_parser("review-dismiss", help="dismiss an item after review")
    review_dismiss.add_argument("--review-db", default="data/research-review.duckdb")
    review_dismiss.add_argument("--queue-id", required=True)
    review_dismiss.add_argument("--reviewer", required=True)
    review_dismiss.add_argument("--notes", required=True)

    review_candidate = sub.add_parser(
        "review-candidate",
        help="mark a reviewed item as eligible for quarantined catalog admission",
    )
    review_candidate.add_argument("--review-db", default="data/research-review.duckdb")
    review_candidate.add_argument("--queue-id", required=True)
    review_candidate.add_argument("--reviewer", required=True)
    review_candidate.add_argument("--notes", required=True)

    catalog_admit = sub.add_parser(
        "catalog-admit",
        help="admit a reviewed candidate to the research catalog as QUARANTINED",
    )
    catalog_admit.add_argument("--review-db", default="data/research-review.duckdb")
    catalog_admit.add_argument("--radar-db", default="data/research-radar.duckdb")
    catalog_admit.add_argument("--catalog-db", default="data/research-catalog.duckdb")
    catalog_admit.add_argument("--queue-id", required=True)

    catalog_verify = sub.add_parser(
        "catalog-verify-file",
        help="attach exact source bytes and verify source identity; creates no claims",
    )
    catalog_verify.add_argument("--catalog-db", default="data/research-catalog.duckdb")
    catalog_verify.add_argument("--artifact-root", default="data/artifacts")
    catalog_verify.add_argument("--artifact-db", default="data/artifacts.duckdb")
    catalog_verify.add_argument("--source-id", required=True)
    catalog_verify.add_argument("--path", required=True)
    catalog_verify.add_argument("--source-uri", required=True)
    catalog_verify.add_argument("--verifier", required=True)
    catalog_verify.add_argument("--notes", required=True)

    args = parser.parse_args()
    from .home import activate

    activate()

    if args.command == "review-list":
        return list_reviews(
            review_db=args.review_db,
            status=args.status,
            limit=args.limit,
        )
    if args.command == "review-start":
        return start_review(
            review_db=args.review_db,
            queue_id=args.queue_id,
            reviewer=args.reviewer,
        )
    if args.command == "review-dismiss":
        return complete_review(
            review_db=args.review_db,
            queue_id=args.queue_id,
            reviewer=args.reviewer,
            notes=args.notes,
            candidate=False,
        )
    if args.command == "review-candidate":
        return complete_review(
            review_db=args.review_db,
            queue_id=args.queue_id,
            reviewer=args.reviewer,
            notes=args.notes,
            candidate=True,
        )
    if args.command == "catalog-admit":
        return admit_catalog_candidate(
            review_db=args.review_db,
            radar_db=args.radar_db,
            catalog_db=args.catalog_db,
            queue_id=args.queue_id,
        )
    if args.command == "catalog-verify-file":
        return verify_catalog_file(
            catalog_db=args.catalog_db,
            artifact_root=args.artifact_root,
            artifact_db=args.artifact_db,
            source_id=args.source_id,
            path=args.path,
            source_uri=args.source_uri,
            verifier=args.verifier,
            notes=args.notes,
        )
    if args.command in {"scan-ssrn", "scan-feeds"}:
        common = dict(
            radar_db=args.radar_db,
            artifact_root=args.artifact_root,
            artifact_db=args.artifact_db,
            triage_db=args.triage_db,
            review_db=args.review_db,
            queue_threshold=args.queue_threshold,
            print_limit=args.print_limit,
        )
        if args.command == "scan-ssrn":
            scan_ssrn(
                from_posted_date=date.fromisoformat(args.from_posted_date),
                query=args.query,
                max_results=args.max_results,
                **common,
            )
        else:
            scan_feeds(
                source_ids=tuple(args.sources) if args.sources else WORKING_PAPER_FEEDS,
                max_items=args.max_items,
                **common,
            )
        return 0
    if args.command == "scan-crossref":
        scan_crossref(
            issns=tuple(args.issns) if args.issns else FINANCE_JOURNAL_ISSNS,
            from_index_date=date.fromisoformat(args.from_index_date),
            query=args.query,
            max_results=args.max_results,
            radar_db=args.radar_db,
            artifact_root=args.artifact_root,
            artifact_db=args.artifact_db,
            triage_db=args.triage_db,
            review_db=args.review_db,
            queue_threshold=args.queue_threshold,
            print_limit=args.print_limit,
        )
        return 0
    if args.command == "scan-arxiv":
        categories = (
            tuple(args.categories)
            if args.categories
            else ARXIV_FINANCE_CATEGORIES
        )
        scan_arxiv(
            categories=categories,
            max_results=args.max_results,
            radar_db=args.radar_db,
            artifact_root=args.artifact_root,
            artifact_db=args.artifact_db,
            triage_db=args.triage_db,
            review_db=args.review_db,
            queue_threshold=args.queue_threshold,
            print_limit=args.print_limit,
        )
        return 0
    return 2


def main() -> int:
    from .cli_support import friendly

    return friendly(_main, "quantos-radar")


if __name__ == "__main__":
    raise SystemExit(main())
