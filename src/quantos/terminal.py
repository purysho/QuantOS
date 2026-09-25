"""Stage 18 — read-only Perspective terminal.

The terminal is a *view*. It owns no business logic and cannot write back.

``TerminalExporter`` opens every DuckDB store in a data directory
**read-only**, maps each known table to a terminal workspace, and writes an
immutable, content-addressed export:

* one JSON file per table, holding a Perspective schema and rows. Secrets
  are scrubbed, and the top-level scalar fields of ``payload_json`` columns
  are flattened into columns;
* ``terminal_manifest.json``: export ID, per-file SHA-256, row counts,
  truncation flags, sources that could not be read, and ``authority: NONE``;
* the static viewer (``index.html``, ``terminal.js``, ``terminal.css``).
  The browser recomputes every file's SHA-256 against the manifest before
  loading it.

``serve`` binds to loopback only, answers GET/HEAD only, disables directory
listings, and sends a strict Content-Security-Policy that allows scripts
only from itself and the pinned Perspective CDN build.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path

import duckdb

from .security import REDACTOR

PERSPECTIVE_VERSION = "3.8.0"
TERMINAL_VERSION = "18.1"
DEFAULT_ROW_LIMIT = 200_000
MAX_FLATTENED_KEYS = 40

WORKSPACES: dict[str, tuple[str, ...]] = {
    "Markets": (
        "daily_bars", "public_observations", "security_master_records", "corporate_action_events", "discount_curves",
        "market_reactions", "pricing_market_snapshots",
    ),
    "Research Radar": ("radar_items", "radar_triage", "research_review_queue", "research_sources"),
    "Evidence / Claims": (
        "claim_cards", "claim_drafts", "claim_relations", "replication_records", "events",
        "entity_edges", "hypotheses", "research_decisions",
    ),
    "Company / Financials": ("financial_statements", "xbrl_facts"),
    "Valuation": ("fundamental_model_runs", "research_cases", "case_reviews", "case_review_resolutions"),
    "Portfolio": (
        "investable_universes", "covariance_artifacts", "portfolio_solutions",
        "optimized_portfolio_solutions", "hierarchical_portfolio_solutions",
        "portfolio_research_decisions", "portfolio_comparison_dossiers", "portfolio_robustness_dossiers",
    ),
    "Risk": (
        "portfolio_risk_cubes", "historical_simulation_risk", "var_backtest_observations",
        "var_calibration_reports", "risk_scenarios", "scenario_sets", "scenario_revaluations",
        "scenario_forecasts", "scenario_outcomes", "risk_review_dossiers", "pricing_instruments",
        "pricing_requests", "pricing_results", "bond_pricing_results", "swap_pricing_results",
        "ore_differentials",
    ),
    "Strategy Lab": (
        "research_model_state", "research_model_transitions", "validation_plans",
        "prospective_review_artifacts", "professional_reviews", "review_resolutions",
    ),
    "PAPER Monitoring": (
        "paper_portfolio_observations", "paper_postmortems", "portfolio_paper_authorizations",
        "portfolio_paper_shadow_observations", "portfolio_paper_enforcement_events",
        "portfolio_paper_review_dossiers", "portfolio_paper_postmortems", "shadow_observations",
        "shadow_research_permits",
    ),
    "Execution": (
        "simulation_order_intents", "simulation_order_transitions", "simulated_fills",
        "reference_execution_results", "execution_schedule_results", "nautilus_execution_differentials",
        "replay_quality_reports", "execution_review_dossiers",
    ),
    "P&L / TCA": ("transaction_cost_reports",),
    "Audit / Lineage": (
        "audit_log", "source_artifacts", "artifact_observations", "event_artifacts",
        "environment_manifests", "spans",
    ),
}
TABLE_WORKSPACE = {table: ws for ws, tables in WORKSPACES.items() for table in tables}
OTHER_WORKSPACE = "Other"

_NUMERIC = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_HASHLIKE = re.compile(r"(?:^|:)[0-9a-f]{32,}$")
RECENCY_COLUMNS = (  # business dates first: a backfill shares one capture time
    "session_date", "observation_date", "period_end", "filed", "published_at", "event_time",
    "knowledge_time", "discovered_at", "triaged_at", "queued_at", "recorded_at", "started_at", "fetched_at",
)
_INTEGER_TYPES = {"TINYINT", "SMALLINT", "INTEGER", "UTINYINT", "USMALLINT"}
_FLOAT_PREFIXES = ("BIGINT", "UBIGINT", "UINTEGER", "HUGEINT", "DOUBLE", "FLOAT", "REAL", "DECIMAL")


class TerminalError(ValueError):
    pass


@dataclass(frozen=True)
class TerminalTable:
    workspace: str
    name: str
    file: str
    source: str
    rows: int
    truncated: bool
    sha256: str


@dataclass(frozen=True)
class TerminalExport:
    export_id: str
    out_dir: Path
    exported_at: datetime
    tables: tuple[TerminalTable, ...]
    unreadable: tuple[dict, ...]


class TerminalExporter:
    def __init__(self, *, row_limit: int = DEFAULT_ROW_LIMIT) -> None:
        if not 1 <= row_limit <= 1_000_000:
            raise TerminalError("row_limit must be between 1 and 1,000,000")
        self.row_limit = row_limit

    def export(self, *, data_dir: str | Path, out_dir: str | Path, exported_at: datetime | None = None) -> TerminalExport:
        data_dir, out_dir = Path(data_dir).resolve(), Path(out_dir).resolve()
        if not data_dir.is_dir():
            raise TerminalError(f"data directory {data_dir} does not exist")
        if out_dir == data_dir:
            raise TerminalError("export directory must not be the data directory")
        exported_at = exported_at or datetime.now(timezone.utc)
        tables_dir = out_dir / "tables"
        if tables_dir.exists():
            shutil.rmtree(tables_dir)
        tables_dir.mkdir(parents=True)
        tables: list[TerminalTable] = []
        unreadable: list[dict] = []
        for db_path in sorted(p for p in data_dir.rglob("*.duckdb") if out_dir not in p.parents):
            relative = db_path.relative_to(data_dir).as_posix()
            try:
                con = duckdb.connect(str(db_path), read_only=True)
            except duckdb.Error as exc:
                unreadable.append({"source": relative, "error": f"{type(exc).__name__}: {str(exc)[:200]}"})
                continue
            try:
                names = [row[0] for row in con.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' ORDER BY 1"
                ).fetchall()]
                for name in names:
                    tables.append(self._export_table(con, name, relative, tables_dir))
            finally:
                con.close()
        tables.sort(key=lambda t: (t.workspace, t.name, t.source))
        export_id = "terminal-export:" + hashlib.sha256(
            json.dumps([[t.file, t.sha256] for t in tables], sort_keys=True).encode()
        ).hexdigest()
        manifest = {
            "export_id": export_id,
            "terminal_version": TERMINAL_VERSION,
            "perspective_version": PERSPECTIVE_VERSION,
            "exported_at": exported_at.isoformat(),
            "authority": "NONE",
            "read_only": True,
            "workspaces": list(WORKSPACES) + [OTHER_WORKSPACE],
            "tables": [t.__dict__ for t in tables],
            "unreadable_sources": unreadable,
        }
        _install_static(out_dir)
        from .terminal_vendor import install_into_export

        manifest["terminal_assets"] = "vendored-offline" if install_into_export(out_dir) else "cdn-jsdelivr"
        (out_dir / "terminal_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
        return TerminalExport(export_id, out_dir, exported_at, tuple(tables), tuple(unreadable))

    def _export_table(self, con, name: str, source: str, tables_dir: Path) -> TerminalTable:
        columns = con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'main' AND table_name = ? ORDER BY ordinal_position",
            [name],
        ).fetchall()
        total = con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
        names = [col for col, _ in columns]
        recency = next((c for c in RECENCY_COLUMNS if c in names), None)
        order = f' ORDER BY "{recency}" DESC NULLS LAST' if recency else ""
        # When a table is truncated, the most recent rows are the ones kept.
        cursor = con.execute(f'SELECT * FROM "{name}"{order} LIMIT {self.row_limit}')
        raw_rows = cursor.fetchall()
        schema: dict[str, str] = {
            col: "string" if _is_code(col) else _perspective_type(kind) for col, kind in columns
        }
        rows = [
            {col: (str(v) if v is not None and _is_code(col) else _value(v)) for (col, _), v in zip(columns, row)}
            for row in raw_rows
        ]
        _flatten_payloads(schema, rows)
        document = {"table": name, "source": source, "schema": schema, "columns": _column_order(schema, rows), "rows": rows}
        body = REDACTOR.redact(json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)).encode()
        stem = source.removesuffix(".duckdb").replace("/", "__")
        file = f"tables/{stem}__{name}.json"
        (tables_dir.parent / file).write_bytes(body)
        return TerminalTable(
            workspace=TABLE_WORKSPACE.get(name, OTHER_WORKSPACE),
            name=name,
            file=file,
            source=source,
            rows=len(rows),
            truncated=total > len(rows),
            sha256=hashlib.sha256(body).hexdigest(),
        )


def _is_code(column: str) -> bool:
    """Numbers that are labels, not quantities (years, CIKs, sequence numbers):
    shown as text so they are never formatted as "2,026" or summed."""

    return column in {"cik", "sequence"} or column.endswith(("_year", "_cik")) or column == "year"


def _column_order(schema: dict[str, str], rows: list[dict]) -> list[str]:
    """Descriptive columns first; content hashes and long opaque identifiers last."""

    def opaque(column: str) -> bool:
        sample = next((r[column] for r in rows if r.get(column) is not None), None)
        if isinstance(sample, str):
            return bool(_HASHLIKE.search(sample)) or len(sample) > 80 and " " not in sample
        return column.endswith(("_hash", "sha256"))

    return [c for c in schema if not opaque(c)] + [c for c in schema if opaque(c)]


def _perspective_type(kind: str) -> str:
    kind = kind.upper()
    if kind == "BOOLEAN":
        return "boolean"
    if kind in _INTEGER_TYPES:
        return "integer"
    if kind.startswith(_FLOAT_PREFIXES):
        return "float"
    if kind == "DATE":
        return "date"
    if kind.startswith("TIMESTAMP"):
        return "datetime"
    return "string"


def _value(value):
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return float(value) if value.is_finite() else None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return int(value.timestamp() * 1000)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _flatten_payloads(schema: dict[str, str], rows: list[dict]) -> None:
    """Promotes top-level payload fields to columns; nested values stay JSON text."""

    for column in [c for c in schema if c.endswith("payload_json")]:
        parsed = []
        for row in rows:
            try:
                value = json.loads(row.get(column) or "null")
            except (TypeError, json.JSONDecodeError):
                value = None
            parsed.append(value if isinstance(value, dict) else None)
        if not rows or any(p is None for p in parsed):
            continue  # not uniformly a JSON object: keep the raw column
        keys = sorted({k for payload in parsed for k in payload if k not in schema})[:MAX_FLATTENED_KEYS]
        for key in keys:
            values = [payload.get(key) for payload in parsed]
            present = [v for v in values if v is not None]
            if present and all(isinstance(v, bool) for v in present):
                kind = "boolean"
            elif present and all(
                (isinstance(v, (int, float)) and not isinstance(v, bool)) or (isinstance(v, str) and _NUMERIC.fullmatch(v))
                for v in present
            ):
                kind = "float"
            else:
                kind = "string"
            schema[key] = kind
            for row, value in zip(rows, values):
                if value is None:
                    row[key] = None
                elif kind == "float":
                    number = float(value)
                    row[key] = number if math.isfinite(number) else None
                elif kind == "string":
                    row[key] = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
                else:
                    row[key] = value
        del schema[column]
        for row in rows:
            row.pop(column, None)


def _install_static(out_dir: Path) -> None:
    static = resources.files("quantos") / "terminal_static"
    for name in ("index.html", "terminal.js", "terminal.css"):
        (out_dir / name).write_bytes((static / name).read_bytes())


CONTENT_SECURITY_POLICY = (
    "default-src 'none'; "
    "script-src 'self' https://cdn.jsdelivr.net 'wasm-unsafe-eval' blob:; "
    "worker-src 'self' blob:; "
    "connect-src 'self' https://cdn.jsdelivr.net; "
    "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
    "font-src https://cdn.jsdelivr.net data:; "
    "img-src 'self' data:; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


class _ReadOnlyHandler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        super().end_headers()

    def list_directory(self, path):  # noqa: D401 - no directory listings
        self.send_error(404, "Not Found")
        return None

    def log_message(self, format, *args):  # quiet by default
        pass


LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _container_bind_allowed(host: str) -> bool:
    """Inside the official container the server must listen on the container
    interface; the image sets ``QUANTOS_CONTAINER=1`` and the compose file
    publishes the port on the host's 127.0.0.1 only."""

    in_container = Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()
    return host == "0.0.0.0" and os.environ.get("QUANTOS_CONTAINER") == "1" and in_container


def make_server(*, directory: str | Path, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    directory = Path(directory).resolve()
    if host not in LOOPBACK_HOSTS and not _container_bind_allowed(host):
        raise TerminalError("the terminal binds to loopback only")
    if not (directory / "terminal_manifest.json").is_file():
        raise TerminalError(f"{directory} is not a terminal export (run `quantos terminal export`)")

    def handler(*args, **kwargs):
        return _ReadOnlyHandler(*args, directory=str(directory), **kwargs)

    return ThreadingHTTPServer((host, port), handler)


def terminal_command(*, action: str, data_dir: str, out_dir: str, host: str, port: int, row_limit: int) -> int:
    if action == "vendor":
        from .terminal_vendor import vendor_perspective

        root = vendor_perspective()
        print(f"TERMINAL_VENDOR Perspective {PERSPECTIVE_VERSION} verified and stored at {root}")
        print("Re-run `quantos terminal export` to use it offline.")
        return 0
    if action == "export":
        export = TerminalExporter(row_limit=row_limit).export(data_dir=data_dir, out_dir=out_dir)
        print(f"TERMINAL_EXPORT {export.export_id}")
        print(f"tables={len(export.tables)} unreadable={len(export.unreadable)} out={export.out_dir}")
        for item in export.unreadable:
            print(f"UNREADABLE {item['source']}: {item['error']}")
        print("TERMINAL_AUTHORITY NONE read-only")
        return 0
    server = make_server(directory=out_dir, host=host, port=port)
    shown = "127.0.0.1" if host == "0.0.0.0" else host
    print(f"TERMINAL http://{shown}:{server.server_address[1]}/  (read-only, Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
