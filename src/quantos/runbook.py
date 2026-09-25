"""Stage 19 — operational runbook: `quantos universe | fundamentals | rates | prices | daily`.

Each step is idempotent and safe to repeat: re-fetching identical data
stores nothing new, and a changed value is kept as a revision. ``daily``
runs every step. A failing step is reported and does not stop the others,
and the exit code is non-zero if any step failed. All network access goes
through the egress guard and the kill switch.
"""

from __future__ import annotations

import os
import time
import traceback
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .artifacts import SourceArtifactStore
from .home import QuantosConfig, load_config
from .keyless import (
    ECBReferenceRateAdapter,
    FRENCH_DATASETS,
    FamaFrenchAdapter,
    FredCsvAdapter,
    KeylessError,
    KnowledgeTimePolicy,
    NasdaqSymbolDirectoryAdapter,
    NotFound,
    PublicObservationStore,
    SECCompanyFactsAdapter,
    SECTickerDirectoryAdapter,
    TreasuryYieldCurveAdapter,
    XbrlFactStore,
    seed_security_master,
)
from .observability import span

SECURITY_MASTER_DB = "data/security_master.duckdb"
FUNDAMENTALS_DB = "data/fundamentals.duckdb"
PUBLIC_DATA_DB = "data/public_data.duckdb"
MARKET_DATA_DB = "data/market_data.duckdb"
ARTIFACT_ROOT = "data/artifacts"
ARTIFACT_DB = "data/artifacts.duckdb"
CACHE_DIR = "data/cache"

STEPS = ("universe", "fundamentals", "analysis", "rates", "factors", "prices", "research", "terminal")
STATEMENTS_DB = "data/financial_statements.duckdb"
ANALYSIS_DB = "data/company_analysis.duckdb"
DEFAULT_FX = ("USD", "GBP", "JPY", "CHF")
DEFAULT_FRED = ("DGS10", "DGS2", "DFF", "CPIAUCSL", "UNRATE", "T10YIE")


@dataclass(frozen=True)
class StepResult:
    step: str
    ok: bool
    summary: str


def _config() -> QuantosConfig:
    config = load_config()
    if not config.contact_email:
        raise SystemExit("quantos: no contact email configured. Run `quantos setup` first.")
    return config


def _artifacts() -> SourceArtifactStore:
    return SourceArtifactStore(ARTIFACT_ROOT, ARTIFACT_DB)


def step_universe(config: QuantosConfig, *, transport=None) -> tuple[str, dict[str, tuple[int, str]]]:
    """Seeds the Security Master; returns a summary and ticker -> (CIK, security_id)."""

    from .security_master import SecurityMaster

    artifacts = _artifacts()
    directory = SECTickerDirectoryAdapter(user_agent=config.user_agent, transport=transport,
                                          artifacts=artifacts).cached(CACHE_DIR)
    if not config.universe:
        return f"SEC directory has {len(directory.entries)} tickers; universe is empty (add tickers with `quantos setup`)", {}
    master = SecurityMaster(SECURITY_MASTER_DB)
    try:
        try:
            symbols = NasdaqSymbolDirectoryAdapter(user_agent=config.user_agent, transport=transport,
                                                   artifacts=artifacts).fetch()
        except KeylessError:
            symbols = None  # kinds are then recorded as assumptions, never guessed
        seeded = seed_security_master(master=master, directory=directory, tickers=config.universe, symbols=symbols)
    finally:
        master.close()
    resolved = {
        t: ((directory.lookup(t).cik if directory.lookup(t) else None), sid)
        for t, sid in seeded.security_ids.items()
    }
    summary = f"{len(resolved)}/{len(config.universe)} tickers resolved, {seeded.added} new records"
    etfs = sorted(t for t, k in (seeded.kinds or {}).items() if k.startswith("ETF"))
    unverified = sorted(t for t, k in (seeded.kinds or {}).items() if k.endswith("?"))
    if etfs:
        summary += "; ETFs: " + ", ".join(etfs)
    if unverified:
        summary += "; kind unverified: " + ", ".join(unverified)
    if seeded.unresolved:
        summary += "; unresolved: " + ", ".join(seeded.unresolved)
    return summary, resolved


def step_fundamentals(config: QuantosConfig, resolved: dict[str, tuple[int, str]], *, transport=None,
                      pause: float = 0.2) -> str:
    if not resolved:
        return "no resolved tickers"
    adapter = SECCompanyFactsAdapter(user_agent=config.user_agent, transport=transport, artifacts=_artifacts())
    store = XbrlFactStore(FUNDAMENTALS_DB)
    added = total = 0
    without_facts = []
    by_cik = {cik: ticker for ticker, (cik, _) in resolved.items() if cik is not None}
    try:
        for cik in sorted(by_cik):
            try:
                facts, artifact_id = adapter.fetch(cik)
            except NotFound:
                without_facts.append(by_cik[cik])  # funds and trusts file no XBRL financials
                continue
            finally:
                time.sleep(pause)  # SEC fair access: stay far below 10 requests/second
            total += len(facts)
            added += store.add(facts, source_artifact_id=artifact_id)
    finally:
        store.close()
    summary = f"{total} XBRL facts checked, {added} new"
    if without_facts:
        summary += "; no XBRL financials (funds/trusts): " + ", ".join(sorted(without_facts))
    return summary


def analyze_ticker(config: QuantosConfig, ticker: str, *, known_at: datetime | None = None, years: int = 10,
                   transport=None):
    """Resolves a ticker, fetches its facts if needed, and builds the analysis."""

    from .company_analysis import CompanyAnalysisStore, analyze_company, load_facts, store_statements

    directory = SECTickerDirectoryAdapter(user_agent=config.user_agent, transport=transport,
                                          artifacts=_artifacts()).cached(CACHE_DIR)
    entry = directory.lookup(ticker)
    if entry is None:
        raise KeylessError(f"{ticker.upper()} is not in SEC's company directory (funds and non-US listings are not)")
    known_at = known_at or datetime.now(timezone.utc)
    if not Path(FUNDAMENTALS_DB).exists() or not load_facts(FUNDAMENTALS_DB, cik=entry.cik, known_at=known_at):
        step_fundamentals(config, {entry.ticker: (entry.cik, "")}, transport=transport)
    analysis = analyze_company(cik=entry.cik, ticker=entry.ticker,
                               facts=load_facts(FUNDAMENTALS_DB, cik=entry.cik, known_at=known_at),
                               known_at=known_at, years=years)
    store_statements(STATEMENTS_DB, analysis)
    store = CompanyAnalysisStore(ANALYSIS_DB)
    try:
        store.add(analysis)
    finally:
        store.close()
    return analysis


def step_analysis(config: QuantosConfig, resolved: dict[str, tuple[int, str]]) -> str:
    from .company_analysis import AnalysisError, CompanyAnalysisStore, analyze_company, load_facts, store_statements

    companies = sorted({(cik, t) for t, (cik, _) in resolved.items() if cik is not None})
    if not companies or not Path(FUNDAMENTALS_DB).exists():
        return "no companies with XBRL facts"
    known_at = datetime.now(timezone.utc)
    analyzed, skipped, statements, errors = [], [], 0, 0
    store = CompanyAnalysisStore(ANALYSIS_DB)
    try:
        for cik, ticker in companies:
            try:
                analysis = analyze_company(cik=cik, ticker=ticker, facts=load_facts(FUNDAMENTALS_DB, cik=cik, known_at=known_at),
                                           known_at=known_at)
            except AnalysisError:
                skipped.append(ticker)  # funds and trusts file no 10-K balance sheets
                continue
            statements += store_statements(STATEMENTS_DB, analysis)
            store.add(analysis)
            analyzed.append(ticker)
            errors += sum(1 for r in analysis.reports for sev, _, _ in r.issues if sev == "ERROR")
    finally:
        store.close()
    summary = f"{len(analyzed)} companies, {statements} new statements, {errors} validation issues reported"
    if skipped:
        summary += "; no 10-K statements: " + ", ".join(skipped)
    return summary


def step_rates(config: QuantosConfig, *, transport=None, backfill_from: int | None = None,
               policy: KnowledgeTimePolicy = KnowledgeTimePolicy.CAPTURE_TIME) -> str:
    agent = config.user_agent
    store = PublicObservationStore(PUBLIC_DATA_DB)
    counts = {"inserted": 0, "unchanged": 0, "revisions": 0}

    def merge(result):
        for key in counts:
            counts[key] += result[key]

    try:
        this_year = datetime.now(timezone.utc).year
        treasury = TreasuryYieldCurveAdapter(user_agent=agent, transport=transport)
        for year in range(backfill_from or this_year, this_year + 1):
            merge(store.add(treasury.fetch(year=year, policy=policy)))
        ecb = ECBReferenceRateAdapter(user_agent=agent, transport=transport)
        start = date(backfill_from, 1, 1) if backfill_from else date.today() - timedelta(days=45)
        for currency in DEFAULT_FX:
            merge(store.add(ecb.fetch(currency=currency, start=start, policy=policy)))
        fred = FredCsvAdapter(user_agent=agent, transport=transport)
        for series in DEFAULT_FRED:
            merge(store.add(fred.fetch(series_id=series)))
    finally:
        store.close()
    return ", ".join(f"{k}={v}" for k, v in counts.items())


def step_factors(config: QuantosConfig, *, transport=None) -> str:
    """Fama-French 5 factors and momentum (daily), from the French Data Library."""

    store = PublicObservationStore(PUBLIC_DATA_DB)
    counts = {"inserted": 0, "unchanged": 0, "revisions": 0}
    try:
        adapter = FamaFrenchAdapter(user_agent=config.user_agent, transport=transport)
        for dataset in FRENCH_DATASETS:
            for key, value in store.add(adapter.fetch(dataset=dataset)).items():
                counts[key] += value
    finally:
        store.close()
    return ", ".join(f"{k}={v}" for k, v in counts.items())


def step_prices(config: QuantosConfig, resolved: dict[str, tuple[int, str]], *, days: int = 10,
                adapter_factory: Callable | None = None, pause: float = 1.0) -> str:
    if config.price_provider == "none":
        return "skipped: keyless mode (add a free Tiingo key with `quantos setup` for stock prices)"
    if not resolved:
        return "no resolved tickers"
    from .market_data import BarStore, PolygonDailyAdapter, TiingoEodAdapter

    factory = adapter_factory or {"tiingo": TiingoEodAdapter, "polygon": PolygonDailyAdapter}[config.price_provider]
    adapter = factory()
    store = BarStore(MARKET_DATA_DB)
    artifacts = _artifacts()
    end = date.today()
    start = end - timedelta(days=days)
    totals = {"inserted": 0, "unchanged": 0, "revisions": 0}
    failures = []
    try:
        for ticker, (_, security_id) in sorted(resolved.items()):
            try:
                capture = adapter.capture(symbol=ticker, security_id=security_id, start=start, end=end, artifacts=artifacts)
                for key, value in store.add(capture).items():
                    totals[key] += value
            except Exception as exc:  # one bad symbol must not stop the rest
                failures.append(f"{ticker}: {exc}")
            time.sleep(pause)
    finally:
        store.close()
    summary = ", ".join(f"{k}={v}" for k, v in totals.items())
    if failures:
        raise RuntimeError(summary + "; failed: " + "; ".join(failures[:5]))
    return summary


def step_research(config: QuantosConfig) -> str:
    from .adapters.feed_radar import WORKING_PAPER_FEEDS
    from .radar_cli import scan_feeds

    sources = config.research_feeds or WORKING_PAPER_FEEDS
    triaged, failed = 0, []
    for source in sources:
        try:
            results = scan_feeds(
                source_ids=(source,), max_items=50, radar_db="data/research-radar.duckdb",
                artifact_root=ARTIFACT_ROOT, artifact_db=ARTIFACT_DB, triage_db="data/radar-triage.duckdb",
                review_db="data/research-review.duckdb", print_limit=3,
            )
            triaged += sum(len(r) for r in results.values())
        except Exception as exc:  # one unavailable feed must not hide the others
            failed.append(f"{source}: {exc}")
    summary = f"{triaged} items triaged from {len(sources) - len(failed)}/{len(sources)} feeds"
    if failed:
        raise RuntimeError(summary + "; failed: " + "; ".join(failed))
    return summary


def step_terminal() -> str:
    from .terminal import TerminalExporter

    export = TerminalExporter().export(data_dir="data", out_dir="data/terminal")
    return f"{len(export.tables)} tables exported ({export.export_id[:32]}…)"


LAST_RUN_FILE = "data/last_daily_run.json"


def run_daily(*, steps: tuple[str, ...] | None = None, backfill_from: int | None = None,
              price_days: int = 10, on_step: Callable[[str, str, str], None] | None = None) -> tuple[StepResult, ...]:
    """Runs the pipeline. ``on_step(name, state, summary)`` reports progress
    (state is RUNNING, OK or FAILED) for interactive front ends."""

    config = _config()
    started = datetime.now(timezone.utc)
    notify = on_step or (lambda *_: None)
    wanted = steps or STEPS
    results: list[StepResult] = []
    resolved: dict[str, tuple[int, str]] = {}

    def run(name: str, action: Callable[[], str]) -> None:
        if name not in wanted:
            return
        notify(name, "RUNNING", "")
        try:
            with span("runbook", name):
                results.append(StepResult(name, True, action()))
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            if os.environ.get("QUANTOS_DEBUG"):
                detail += "\n" + traceback.format_exc()
            results.append(StepResult(name, False, detail))
        notify(name, "OK" if results[-1].ok else "FAILED", results[-1].summary)

    def universe() -> str:
        nonlocal resolved
        summary, resolved = step_universe(config)
        return summary

    run("universe", universe)
    run("fundamentals", lambda: step_fundamentals(config, resolved))
    run("analysis", lambda: step_analysis(config, resolved))
    run("rates", lambda: step_rates(config, backfill_from=backfill_from))
    run("factors", lambda: step_factors(config))
    run("prices", lambda: step_prices(config, resolved, days=price_days))
    run("research", lambda: step_research(config))
    run("terminal", step_terminal)
    _record_last_run(started, results)
    return tuple(results)


def _record_last_run(started: datetime, results: list[StepResult]) -> None:
    import json

    record = {
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "results": [{"step": r.step, "ok": r.ok, "summary": r.summary} for r in results],
    }
    path = Path(LAST_RUN_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, indent=2))
    temporary.replace(path)


def last_run() -> dict | None:
    import json

    try:
        return json.loads(Path(LAST_RUN_FILE).read_text())
    except (OSError, ValueError):
        return None


def daily_command(*, steps: tuple[str, ...] | None, backfill_from: int | None, price_days: int) -> int:
    results = run_daily(steps=steps, backfill_from=backfill_from, price_days=price_days)
    print("\nQuantOS daily run")
    width = max((len(r.step) for r in results), default=4)
    for result in results:
        print(f"  [{'ok  ' if result.ok else 'FAIL'}] {result.step.ljust(width)}  {result.summary}")
    failed = [r.step for r in results if not r.ok]
    print("\nAll steps completed." if not failed else f"\nFailed: {', '.join(failed)} (other steps still ran).")
    print("View: `quantos terminal serve` → http://127.0.0.1:8765/")
    return 1 if failed else 0
