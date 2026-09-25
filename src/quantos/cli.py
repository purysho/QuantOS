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
from .environment_manifest import capture_environment_manifest
from .gates import CapitalFirewall, LiveTradingDisabled
from .observability import ops_report
from .security import AuditLog, KillSwitch
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
        from .security import SecretProvider, SecretUnavailable

        try:
            api_key = SecretProvider().get("FRED_API_KEY").reveal()
        except SecretUnavailable:
            raise SystemExit(
                "a free FRED API key is required for ALFRED vintages: add it with `quantos setup`"
            ) from None
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



def import_research_registry(*, registry: str, catalog_db: str) -> int:
    payload = json.loads(Path(registry).read_text(encoding="utf-8"))
    catalog = ResearchCatalog(catalog_db)
    try:
        result = catalog.import_registry(payload)
        quarantined = len(catalog.list_status(VerificationStatus.QUARANTINED))
        verified = len(catalog.list_status(VerificationStatus.VERIFIED))
        rejected = len(catalog.list_status(VerificationStatus.REJECTED))
        print(
            "RESEARCH_CATALOG",
            f"inserted={result.inserted}",
            f"skipped={result.skipped_identical}",
            f"quarantined={quarantined}",
            f"verified={verified}",
            f"rejected={rejected}",
        )
        return 0
    finally:
        catalog.close()


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


def kill_switch_command(*, action: str, actor: str, reason: str, audit_db: str) -> int:
    switch = KillSwitch()
    if action == "status":
        status = switch.status()
        print("KILL_SWITCH", "ENGAGED" if status.engaged else "RELEASED", status.reason)
        return 0
    log = AuditLog(audit_db)
    try:
        if action == "engage":
            switch.engage(reason=reason, actor=actor, audit=log)
        else:
            switch.release(actor=actor, audit=log)
    finally:
        log.close()
    print("KILL_SWITCH", switch.status().reason)
    return 0


def audit_verify(*, db: str) -> int:
    log = AuditLog(db)
    try:
        result = log.verify()
    finally:
        log.close()
    print("AUDIT", "VALID" if result.valid else "INVALID", result.records, result.reason)
    return 0 if result.valid else 1


def capture_market_data(
    *,
    provider: str,
    symbol: str,
    security_id: str,
    start: str,
    end: str,
    db: str,
    artifact_root: str,
    artifact_db: str,
) -> int:
    from datetime import date

    from .market_data import BarStore, PolygonDailyAdapter, TiingoEodAdapter
    from .observability import span

    adapter = {"tiingo": TiingoEodAdapter, "polygon": PolygonDailyAdapter}[provider]()
    artifacts = SourceArtifactStore(artifact_root, artifact_db)
    store = BarStore(db)
    try:
        with span("market-data", f"{provider}-capture", symbol=symbol):
            capture = adapter.capture(
                symbol=symbol,
                security_id=security_id,
                start=date.fromisoformat(start),
                end=date.fromisoformat(end),
                artifacts=artifacts,
            )
        counts = store.add(capture)
    finally:
        store.close()
    print(f"capture_id={capture.capture_id}")
    print(f"raw_artifact_id={capture.raw_artifact_id}")
    print(f"bars={len(capture.bars)} " + " ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


def _main() -> int:
    from . import __version__
    from .home import activate

    parser = argparse.ArgumentParser(
        prog="quantos",
        description="First Current Quant OS: point-in-time investment research. "
        "Start with `quantos setup`, then `quantos doctor` and `quantos daily`.",
    )
    parser.add_argument("--version", action="version", version=f"First Current Quant OS {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="first-run setup: contact email, universe, optional free API keys")
    setup.add_argument("--email", default=None)
    setup.add_argument("--organization", default=None)
    setup.add_argument("--universe", default=None, help="comma-separated tickers")
    setup.add_argument("--non-interactive", action="store_true", help="use flags and existing values only")
    setup.add_argument(
        "--key-file",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="read an optional key from a file, e.g. TIINGO_API_KEY=/path/to/file (repeatable)",
    )

    analyze = sub.add_parser("analyze", help="standardized point-in-time statements and metrics for a company")
    analyze.add_argument("ticker")
    analyze.add_argument("--as-of", default=None, help="knowledge time (ISO date or datetime, UTC); default now")
    analyze.add_argument("--years", type=int, default=10)

    doctor = sub.add_parser("doctor", help="check this installation and explain what works")
    doctor.add_argument("--online", action="store_true", help="also probe each keyless public source")

    daily = sub.add_parser("daily", help="run the daily pipeline: universe, fundamentals, rates, prices, research, terminal")
    daily.add_argument(
        "--only",
        default=None,
        help="comma-separated subset of: universe,fundamentals,analysis,rates,factors,prices,research,terminal",
    )
    daily.add_argument("--backfill-from", type=int, default=None, help="first year of Treasury/ECB history to load")
    daily.add_argument("--price-days", type=int, default=10, help="calendar days of prices to (re)capture")

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


    research_import = sub.add_parser(
        "research-import",
        help="import research source cards into quarantine; does not verify them",
    )
    research_import.add_argument("--registry", required=True)
    research_import.add_argument("--catalog-db", default="data/research-catalog.duckdb")
    export = sub.add_parser("export", help="export the event ledger to Parquet")
    export.add_argument("--db", default="data/events.duckdb")
    export.add_argument("--parquet", default="data/events.parquet")

    state = sub.add_parser("asof", help="reconstruct one entity as-of a knowledge time")
    state.add_argument("--db", default="data/events.duckdb")
    state.add_argument("--entity", required=True)
    state.add_argument("--as-of", required=True)

    env_manifest = sub.add_parser(
        "env-manifest",
        help="print the content-addressed runtime environment manifest",
    )
    env_manifest.add_argument(
        "--db",
        default=None,
        help="optionally persist the manifest to this DuckDB store",
    )

    kill = sub.add_parser("kill-switch", help="inspect, engage or release the outbound kill switch")
    kill.add_argument("action", choices=("status", "engage", "release"))
    kill.add_argument("--actor", default="")
    kill.add_argument("--reason", default="")
    kill.add_argument("--audit-db", default="data/audit.duckdb")

    ops = sub.add_parser("ops-report", help="summarize recorded spans and error categories")
    ops.add_argument("--db", default="data/observability.duckdb")

    audit = sub.add_parser("audit-verify", help="verify the hash chain of the audit log")
    audit.add_argument("--db", default="data/audit.duckdb")

    market = sub.add_parser(
        "market-data", help="capture raw daily bars from a licensed provider"
    )
    market.add_argument("--provider", choices=("tiingo", "polygon"), required=True)
    market.add_argument("--symbol", required=True)
    market.add_argument("--security-id", required=True)
    market.add_argument("--start", required=True)
    market.add_argument("--end", required=True)
    market.add_argument("--db", default="data/market_data.duckdb")
    market.add_argument("--artifact-root", default="data/artifacts")
    market.add_argument("--artifact-db", default="data/artifacts.duckdb")

    terminal = sub.add_parser("terminal", help="export or serve the read-only Perspective terminal")
    terminal.add_argument("action", choices=("export", "serve", "vendor"))
    terminal.add_argument("--data-dir", default="data")
    terminal.add_argument("--out", default="data/terminal")
    terminal.add_argument("--host", default="127.0.0.1")
    terminal.add_argument("--port", type=int, default=8765)
    terminal.add_argument("--row-limit", type=int, default=200_000)

    args = parser.parse_args()
    activate()
    if args.command == "setup":
        from .home import HomeError, run_setup

        keys = {}
        for item in args.key_file:
            name, _, path = item.partition("=")
            if not path:
                raise SystemExit("--key-file expects NAME=PATH")
            keys[name.strip()] = Path(path).expanduser().read_text().strip()
        try:
            run_setup(
                email=args.email,
                organization=args.organization,
                universe=tuple(t.strip().upper() for t in args.universe.split(",") if t.strip())
                if args.universe is not None
                else None,
                keys=keys,
                interactive=not args.non_interactive,
            )
        except HomeError as exc:
            raise SystemExit(f"quantos setup: {exc}") from None
        return 0
    if args.command == "analyze":
        from .company_analysis import render
        from .home import load_config
        from .runbook import analyze_ticker

        config = load_config()
        if not config.contact_email:
            raise SystemExit("quantos: no contact email configured. Run `quantos setup` first.")
        known_at = None
        if args.as_of:
            known_at = datetime.fromisoformat(args.as_of)
            known_at = known_at if known_at.tzinfo else known_at.replace(tzinfo=timezone.utc)
        print(render(analyze_ticker(config, args.ticker, known_at=known_at, years=args.years)))
        return 0
    if args.command == "doctor":
        from .doctor import doctor_command

        return doctor_command(online=args.online)
    if args.command == "daily":
        from .runbook import daily_command

        steps = tuple(s.strip() for s in args.only.split(",")) if args.only else None
        from .runbook import STEPS

        valid = set(STEPS)
        if steps and set(steps) - valid:
            raise SystemExit("unknown step(s): " + ", ".join(sorted(set(steps) - valid)))
        return daily_command(steps=steps, backfill_from=args.backfill_from, price_days=args.price_days)
    if args.command == "terminal":
        from .terminal import terminal_command

        return terminal_command(
            action=args.action,
            data_dir=args.data_dir,
            out_dir=args.out,
            host=args.host,
            port=args.port,
            row_limit=args.row_limit,
        )
    if args.command == "market-data":
        return capture_market_data(
            provider=args.provider,
            symbol=args.symbol,
            security_id=args.security_id,
            start=args.start,
            end=args.end,
            db=args.db,
            artifact_root=args.artifact_root,
            artifact_db=args.artifact_db,
        )
    if args.command == "kill-switch":
        return kill_switch_command(action=args.action, actor=args.actor, reason=args.reason, audit_db=args.audit_db)
    if args.command == "ops-report":
        return ops_report(db=args.db)
    if args.command == "audit-verify":
        return audit_verify(db=args.db)
    if args.command == "env-manifest":
        return capture_environment_manifest(db=args.db)
    if args.command == "demo":
        return demo()
    if args.command == "edge-demo":
        return edge_demo()
    if args.command == "sec":
        return ingest_sec(cik=args.cik, db=args.db)
    if args.command == "sec-document":
        return capture_sec_document(
            event_id=args.event_id,
            db=args.db,
            artifact_root=args.artifact_root,
            artifact_db=args.artifact_db,
            lineage_db=args.lineage_db,
        )
    if args.command == "fred":
        return ingest_fred(
            series_id=args.series,
            vintage_date=args.vintage,
            db=args.db,
        )
    if args.command == "research-import":
        return import_research_registry(
            registry=args.registry,
            catalog_db=args.catalog_db,
        )
    if args.command == "export":
        return export_events(db=args.db, parquet=args.parquet)
    if args.command == "asof":
        return show_state(db=args.db, entity_id=args.entity, as_of=args.as_of)
    return 2


def main() -> int:
    from .cli_support import friendly

    return friendly(_main, "quantos")


if __name__ == "__main__":
    raise SystemExit(main())
