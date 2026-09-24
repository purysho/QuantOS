from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .adapters.arxiv_radar import ARXIV_FINANCE_CATEGORIES, ArxivRadarAdapter, ArxivRadarFetch
from .artifacts import SourceArtifactStore
from .radar_triage import RadarTriageEngine, RadarTriageStore, TriageResult
from .research_radar import DiscoveryItem, ResearchRadarStore


DEFAULT_USER_AGENT = (
    "First Current Quant OS prototype/0.4.3 "
    "(+https://github.com/purysho/First-Current-Quant-OS-prototype)"
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
    print_limit: int = 10,
    adapter: RadarAdapter | None = None,
) -> tuple[TriageResult, ...]:
    if not 1 <= print_limit <= 100:
        raise ValueError("print_limit must be between 1 and 100")

    Path(radar_db).parent.mkdir(parents=True, exist_ok=True)
    radar = ResearchRadarStore(radar_db)
    artifacts = SourceArtifactStore(artifact_root, artifact_db)
    triage = RadarTriageStore(triage_db)
    try:
        prior = list(radar.latest(provider="arxiv", limit=1000))
        client = adapter or ArxivRadarAdapter(
            user_agent=os.environ.get("ARXIV_USER_AGENT", DEFAULT_USER_AGENT)
        )
        fetched = client.fetch(
            categories=categories,
            max_results=max_results,
            start=0,
            artifact_store=artifacts,
        )
        ingest = radar.ingest(fetched.items)

        engine = RadarTriageEngine()
        scored: list[TriageResult] = []
        context = list(prior)
        triaged_at = datetime.now(timezone.utc)
        for item in fetched.items:
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
            "ARXIV_RADAR",
            f"fetched={len(fetched.items)}",
            f"inserted={ingest.inserted}",
            f"skipped={ingest.skipped_identical}",
            f"feed_artifact={artifact_id}",
        )
        by_id = {item.discovery_id: item for item in fetched.items}
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
        print("RADAR_TRUST", "DISCOVERY_ONLY", "no claims promoted")
        return ranked
    finally:
        triage.close()
        artifacts.close()
        radar.close()


def main() -> int:
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
    scan.add_argument("--print-limit", type=int, default=10)

    args = parser.parse_args()
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
            print_limit=args.print_limit,
        )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
