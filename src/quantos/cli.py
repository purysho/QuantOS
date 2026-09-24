from __future__ import annotations

import argparse
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .adapters.fred import FREDVintageAdapter
from .adapters.sec import SECSubmissionsAdapter
from .artifacts import SourceArtifactStore
from .claims import (
    ClaimCard,
    ClaimStance,
    ClaimStore,
    ClaimType,
    EvidenceRetriever,
    make_claim_id,
)
from .gates import CapitalFirewall, LiveTradingDisabled
from .ingestion import IngestionEngine
from .models import EpistemicState, Event, OrderProposal
from .persistent import DuckDBEventStore
from .reactions import MarketReactionEngine
from .service import QuantOS
from .shadow import ShadowLedger


def demo() -> int:
    osys = QuantOS()
    event = Event(
        entity_id="DEMO",
        event_type="earnings.release",
        event_time=datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc),
        knowledge_time=datetime(2026, 9, 24, 12, 0, 2, tzinfo=timezone.utc),
        source_id="demo://earnings",
        payload={"metric": "eps", "actual": 1.18, "expected": 1.05},
    )
    osys.ingest(event)
    hypothesis, decision = osys.analyze_numeric_surprise(
        event=event, metric="EPS", expected=1.05, observed=1.18
    )

    print("EVENT", event.event_id, event.event_type)
    print("HYPOTHESIS", hypothesis.epistemic_state.value, hypothesis.statement)
    print("SURPRISE", round(hypothesis.surprise or 0.0, 4))
    print(
        "RESEARCH_GATE",
        "PASS" if decision.approved_for_shadow else "FAIL",
        decision.reasons,
    )

    try:
        CapitalFirewall().authorize_live_order(
            OrderProposal(
                security_id="DEMO",
                side="BUY",
                quantity=100,
                reason_hypothesis_id=hypothesis.hypothesis_id,
            )
        )
    except LiveTradingDisabled as exc:
        print("CAPITAL_FIREWALL", "BLOCKED", str(exc))
        return 0

    raise RuntimeError("capital firewall unexpectedly allowed a live order")


def edge_demo() -> int:
    now = datetime.now(timezone.utc)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        artifacts = SourceArtifactStore(root / "artifacts", root / "artifacts.duckdb")
        claims = ClaimStore(root / "claims.duckdb")
        shadow = ShadowLedger(root / "shadow.duckdb")
        try:
            support_ref = artifacts.put(
                source_uri="demo://research/support",
                content=b"Historical sample documents an earnings-surprise relation.",
                fetched_at=now,
                media_type="text/plain",
            )
            limit_ref = artifacts.put(
                source_uri="demo://research/limit",
                content=b"Publication, costs, regime shifts and crowding can reduce predictability.",
                fetched_at=now,
                media_type="text/plain",
            )
            support_text = "Historical earnings surprises showed return continuation in the studied sample."
            limit_text = "Earnings-surprise predictability can decay and requires out-of-sample validation."
            for text, stance, ref in (
                (support_text, ClaimStance.SUPPORTS, support_ref),
                (limit_text, ClaimStance.LIMITS, limit_ref),
            ):
                sources = (ref.artifact_id,)
                claims.add(
                    ClaimCard(
                        text=text,
                        claim_type=ClaimType.EMPIRICAL,
                        stance=stance,
                        epistemic_state=EpistemicState.OBSERVED,
                        topic="earnings surprise",
                        source_artifact_ids=sources,
                        locator="demo",
                        scope={"mode": "synthetic demonstration"},
                        assumptions=(),
                        limitations=(),
                        as_of=now,
                        claim_id=make_claim_id(
                            text=text,
                            source_artifact_ids=sources,
                            locator="demo",
                        ),
                    )
                )

            bundle = EvidenceRetriever(claims).bundle("earnings surprise")
            reaction = MarketReactionEngine.measure(
                event_id="demo-event",
                security_id="DEMO",
                measured_at=now,
                horizon="1d",
                security_pre=100.0,
                security_post=102.0,
                benchmark_pre=100.0,
                benchmark_post=101.0,
            )
            shadow.record(
                signal_id="earnings-surprise-demo",
                hypothesis_id="demo-hypothesis",
                measured_at=now,
                expected_direction=1,
                residual_return=reaction.residual_return,
            )
            health = shadow.health("earnings-surprise-demo")

            print("ARTIFACTS", support_ref.artifact_id, limit_ref.artifact_id)
            print(
                "EVIDENCE",
                f"support={len(bundle.supporting)}",
                f"limits={len(bundle.limiting)}",
                f"contradictions={len(bundle.contradicting)}",
            )
            print("REACTION_RESIDUAL", round(reaction.residual_return, 6))
            print("EDGE_HEALTH", health.state, f"observations={health.observations}")
            return 0
        finally:
            shadow.close()
            claims.close()
            artifacts.close()


def _store(path: str) -> DuckDBEventStore:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return DuckDBEventStore(path)


def ingest_sec(*, cik: int, db: str) -> int:
    user_agent = os.environ.get("SEC_USER_AGENT")
    if not user_agent:
        raise SystemExit(
            "SEC_USER_AGENT is required, e.g. 'First Current Quant OS contact@example.com'"
        )
    store = _store(db)
    try:
        events = SECSubmissionsAdapter(user_agent=user_agent).fetch(cik)
        result = IngestionEngine(store).ingest(events)
        print(
            "SEC_INGEST",
            f"cik={cik}",
            f"inserted={result.inserted}",
            f"skipped={result.skipped_identical}",
        )
        return 0
    finally:
        store.close()



def capture_sec_document(
    *,
    event_id: str,
    db: str,
    artifact_root: str,
    artifact_db: str,
    lineage_db: str,
) -> int:
    user_agent = os.environ.get("SEC_USER_AGENT")
    if not user_agent:
        raise SystemExit(
            "SEC_USER_AGENT is required, e.g. 'First Current Quant OS contact@example.com'"
        )

    store = _store(db)
    artifacts = SourceArtifactStore(artifact_root, artifact_db)
    lineage = LineageStore(lineage_db)
    try:
        event = store.get(event_id)
        if event is None:
            raise SystemExit(f"event not found: {event_id}")
        ref = SECFilingArtifactFetcher(
            user_agent=user_agent
        ).capture_primary_document(
            event=event,
            artifact_store=artifacts,
            lineage_store=lineage,
        )
        print(
            "SEC_DOCUMENT",
            f"event={event_id}",
            f"artifact={ref.artifact_id}",
            f"bytes={ref.byte_length}",
        )
        return 0
    finally:
        lineage.close()
        artifacts.close()
        store.close()


def ingest_fred(*, series_id: str, vintage_date: str, db: str) -> int:
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        raise SystemExit("FRED_API_KEY is required for FRED/ALFRED API ingestion")
    store = _store(db)
    try:
        events = FREDVintageAdapter(api_key=api_key).fetch_series_as_of(
            series_id=series_id,
            vintage_date=vintage_date,
        )
        result = IngestionEngine(store).ingest(events)
        print(
            "FRED_INGEST",
            f"series={series_id}",
            f"vintage={vintage_date}",
            f"inserted={result.inserted}",
            f"skipped={result.skipped_identical}",
        )
        return 0
    finally:
        store.close()


def export_events(*, db: str, parquet: str) -> int:
    store = _store(db)
    try:
        store.export_parquet(parquet)
        print("PARQUET_EXPORT", parquet)
        return 0
    finally:
        store.close()


def show_state(*, db: str, entity_id: str, as_of: str) -> int:
    moment = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise SystemExit("--as-of must include a timezone or Z")
    store = _store(db)
    try:
        rows = store.known_as_of(moment, entity_id=entity_id)
        for event in rows:
            print(
                event.knowledge_time.isoformat(),
                event.event_type,
                event.source_id,
                event.payload,
            )
        print("AS_OF_COUNT", len(rows))
        return 0
    finally:
        store.close()


def main() -> int:
    parser = argparse.ArgumentParser(prog="quantos")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("demo", help="run the fail-closed synthetic intelligence demo")
    sub.add_parser("edge-demo", help="run the provenance-to-shadow edge demo")

    sec = sub.add_parser("sec", help="ingest current SEC submissions for a CIK")
    sec.add_argument("--cik", type=int, required=True)
    sec.add_argument("--db", default="data/events.duckdb")


    sec_document = sub.add_parser(
        "sec-document",
        help="archive the primary document for one already-ingested SEC filing event",
    )
    sec_document.add_argument("--event-id", required=True)
    sec_document.add_argument("--db", default="data/events.duckdb")
    sec_document.add_argument("--artifact-root", default="data/artifacts")
    sec_document.add_argument("--artifact-db", default="data/artifacts.duckdb")
    sec_document.add_argument("--lineage-db", default="data/lineage.duckdb")
    fred = sub.add_parser("fred", help="ingest a FRED series as of one vintage date")
    fred.add_argument("--series", required=True)
    fred.add_argument("--vintage", required=True)
    fred.add_argument("--db", default="data/events.duckdb")

    export = sub.add_parser("export", help="export the event ledger to Parquet")
    export.add_argument("--db", default="data/events.duckdb")
    export.add_argument("--parquet", default="data/events.parquet")

    state = sub.add_parser("asof", help="reconstruct one entity as-of a knowledge time")
    state.add_argument("--db", default="data/events.duckdb")
    state.add_argument("--entity", required=True)
    state.add_argument("--as-of", required=True)

    args = parser.parse_args()
    if args.command == "demo":
        return demo()
    if args.command == "edge-demo":
        return edge_demo()
    if args.command == "sec":
        return ingest_sec(cik=args.cik, db=args.db)
    if args.command == "fred":
        return ingest_fred(
            series_id=args.series,
            vintage_date=args.vintage,
            db=args.db,
        )
    if args.command == "export":
        return export_events(db=args.db, parquet=args.parquet)
    if args.command == "asof":
        return show_state(db=args.db, entity_id=args.entity, as_of=args.as_of)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())