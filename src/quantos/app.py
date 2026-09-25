"""Stage 22 — the First Current desktop app (browser-based control center).

``quantos app`` starts one loopback-only HTTP server and opens the user's
own browser:

* ``/`` is the control center: first-run setup, "Update data", company
  analysis, a health check, and a link to the terminal;
* ``/terminal/`` is the read-only terminal export, exactly as served by
  ``quantos terminal serve``;
* ``/api/...`` is a small JSON API used by the control center.

Security model (it binds 127.0.0.1 only, and every API call is checked):

* a random per-launch token is handed to the browser in the URL
  *fragment*, which is never sent over the network or in referrers. Every
  ``/api`` request must carry it in the ``X-Quantos-Token`` header;
* other websites cannot send that header cross-origin without a CORS
  preflight, which this server never approves. ``Origin``, when present,
  must be this server's own origin;
* the ``Host`` header must be this server's loopback address, which defeats
  DNS rebinding;
* API keys can be written but are never returned: status only says whether
  each key is configured.

The same code runs from source and from the packaged Windows, macOS and
Linux builds (``--smoke-test`` exercises it headlessly for CI).
"""

from __future__ import annotations

import contextlib
import hmac
import io
import json
import mimetypes
import os
import secrets
import sys
import threading
import traceback
import webbrowser
from dataclasses import asdict
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import unquote, urlsplit

from . import __version__

APP_NAME = "FirstCurrent"
MAX_BODY = 64_000
CONTROL_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


# ------------------------------------------------------------- app home


def desktop_home() -> Path:
    """Where the desktop app keeps its data when QUANTOS_HOME is not set.

    Portable mode: a ``FirstCurrent-data`` folder next to the executable
    (e.g. on a USB stick) is used when it exists. Otherwise the OS's
    per-user application-data folder.
    """

    if getattr(sys, "frozen", False):
        portable = Path(sys.executable).resolve().parent / f"{APP_NAME}-data"
        if portable.is_dir():
            return portable
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_NAME


def _prepare_environment() -> Path:
    if not os.environ.get("QUANTOS_HOME"):
        os.environ["QUANTOS_HOME"] = str(desktop_home())
    bundled = Path(getattr(sys, "_MEIPASS", "")) / "vendor"
    if not os.environ.get("QUANTOS_VENDOR_DIR") and bundled.is_dir():
        os.environ["QUANTOS_VENDOR_DIR"] = str(bundled)
    from .home import activate

    return activate()


# ---------------------------------------------------------------- jobs


class JobRunner:
    """One background pipeline run at a time, with step-by-step progress."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.state: dict = {"running": False, "steps": [], "started_at": None, "finished_at": None, "error": None}

    def start(self, steps: tuple[str, ...] | None) -> bool:
        with self._lock:
            if self.state["running"]:
                return False
            self.state = {"running": True, "steps": [], "started_at": _now(), "finished_at": None, "error": None}
        threading.Thread(target=self._run, args=(steps,), daemon=True).start()
        return True

    def _run(self, steps) -> None:
        from .runbook import run_daily

        def on_step(name: str, state: str, summary: str) -> None:
            with self._lock:
                existing = [s for s in self.state["steps"] if s["step"] != name]
                self.state["steps"] = existing + [{"step": name, "state": state, "summary": summary}]

        try:
            with contextlib.redirect_stdout(io.StringIO()):  # step chatter is for the CLI
                run_daily(steps=steps, on_step=on_step)
        except BaseException as exc:  # reported to the UI, never crashes the server
            with self._lock:
                self.state["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                self.state["running"] = False
                self.state["finished_at"] = _now()

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self.state))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------- server


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int = 0) -> None:
        super().__init__(("127.0.0.1", port), AppHandler)
        self.token = secrets.token_urlsafe(32)
        self.jobs = JobRunner()
        self.static = resources.files("quantos") / "app_static"

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    @property
    def launch_url(self) -> str:
        return f"{self.origin}/#token={self.token}"


class AppHandler(BaseHTTPRequestHandler):
    server: AppServer
    server_version = "FirstCurrent"
    sys_version = ""

    def log_message(self, format, *args):  # quiet
        pass

    # ---- dispatch

    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("GET", head=True)

    def do_POST(self):
        self._dispatch("POST")

    def do_OPTIONS(self):  # no CORS preflight is ever approved
        self._send_json(HTTPStatus.FORBIDDEN, {"error": "cross-origin requests are not allowed"})

    def _dispatch(self, method: str, head: bool = False) -> None:
        if not self._host_ok():
            return self._send_json(HTTPStatus.MISDIRECTED_REQUEST, {"error": "unexpected Host header"})
        path = unquote(urlsplit(self.path).path)
        try:
            if path.startswith("/api/"):
                if not self._authorized():
                    return self._send_json(HTTPStatus.FORBIDDEN, {"error": "missing or invalid app token"})
                return self._api(method, path[len("/api/"):])
            if method != "GET":
                return self._send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "read-only"})
            if path == "/terminal":
                self.send_response(HTTPStatus.MOVED_PERMANENTLY)
                self.send_header("Location", "/terminal/")
                self.end_headers()
                return
            if path.startswith("/terminal/"):
                return self._serve_terminal(path[len("/terminal/"):], head)
            return self._serve_static(path.lstrip("/") or "index.html", head)
        except Exception as exc:
            if os.environ.get("QUANTOS_DEBUG") == "1":
                traceback.print_exc()
            return self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(exc).__name__}: {exc}"})

    # ---- checks

    def _host_ok(self) -> bool:
        return self.headers.get("Host") in {f"127.0.0.1:{self.server.server_address[1]}",
                                            f"localhost:{self.server.server_address[1]}"}

    def _authorized(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {self.server.origin,
                                                 f"http://localhost:{self.server.server_address[1]}"}:
            return False
        return hmac.compare_digest(self.headers.get("X-Quantos-Token", ""), self.server.token)

    # ---- static files

    def _serve_static(self, relative: str, head: bool) -> None:
        if "/" in relative or relative.startswith("."):
            return self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        resource = self.server.static / relative
        if not resource.is_file():
            return self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        self._send_bytes(resource.read_bytes(), _mime(relative), CONTROL_CSP, head)

    def _serve_terminal(self, relative: str, head: bool) -> None:
        from .terminal import CONTENT_SECURITY_POLICY

        root = (Path.cwd() / "data" / "terminal").resolve()
        target = (root / (relative or "index.html")).resolve()
        if root not in target.parents and target != root or not target.is_file():
            return self._send_json(HTTPStatus.NOT_FOUND, {"error": "no terminal export yet: run an update"})
        self._send_bytes(target.read_bytes(), _mime(target.name), CONTENT_SECURITY_POLICY, head)

    # ---- API

    def _api(self, method: str, name: str) -> None:
        body = self._read_json() if method == "POST" else {}
        if body is None:
            return self._send_json(HTTPStatus.BAD_REQUEST, {"error": "request body must be a JSON object"})
        routes = {
            ("GET", "status"): self._status,
            ("GET", "job"): lambda _: self.server.jobs.snapshot(),
            ("POST", "setup"): self._setup,
            ("POST", "update"): self._update,
            ("POST", "analyze"): self._analyze,
            ("POST", "doctor"): self._doctor,
            ("POST", "quit"): self._quit,
        }
        handler = routes.get((method, name))
        if handler is None:
            return self._send_json(HTTPStatus.NOT_FOUND, {"error": "unknown endpoint"})
        try:
            result = handler(body)
        except (ValueError, SystemExit) as exc:
            return self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        self._send_json(HTTPStatus.OK, result)

    def _status(self, _body) -> dict:
        from .home import OPTIONAL_KEYS, configured_secrets, home_dir, load_config
        from .runbook import last_run

        config = load_config()
        present = set(configured_secrets())
        return {
            "version": __version__,
            "home": str(home_dir()),
            "configured": bool(config.contact_email),
            "config": {"contact_email": config.contact_email, "organization": config.organization,
                       "universe": list(config.universe), "price_provider": config.price_provider},
            "keys": {name: {"configured": name in present, "purpose": purpose} for name, purpose in OPTIONAL_KEYS.items()},
            "last_run": last_run(),
            "terminal_ready": (Path.cwd() / "data" / "terminal" / "terminal_manifest.json").is_file(),
            "job": self.server.jobs.snapshot(),
        }

    def _setup(self, body: dict) -> dict:
        from .home import HomeError, run_setup

        universe = body.get("universe", "")
        if isinstance(universe, str):
            universe = [t for t in (part.strip().upper() for part in universe.replace(" ", ",").split(",")) if t]
        keys = {k: str(v) for k, v in (body.get("keys") or {}).items() if str(v).strip()}
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                config = run_setup(email=str(body.get("contact_email", "")), organization=str(body.get("organization", "")),
                                   universe=tuple(universe), keys=keys, interactive=False)
        except HomeError as exc:
            raise ValueError(str(exc)) from None
        return {"ok": True, "price_provider": config.price_provider}

    def _update(self, body: dict) -> dict:
        from .runbook import STEPS

        steps = body.get("steps")
        if steps is not None and (not isinstance(steps, list) or not set(steps) <= set(STEPS)):
            raise ValueError("unknown update step")
        started = self.server.jobs.start(tuple(steps) if steps else None)
        return {"started": started, "job": self.server.jobs.snapshot()}

    def _analyze(self, body: dict) -> dict:
        from .company_analysis import AnalysisError
        from .home import load_config
        from .keyless import KeylessError
        from .runbook import analyze_ticker

        ticker = str(body.get("ticker", "")).strip().upper()
        if not ticker or len(ticker) > 12:
            raise ValueError("enter a ticker such as AAPL")
        config = load_config()
        if not config.contact_email:
            raise ValueError("finish setup first (a contact email is required by SEC EDGAR)")
        try:
            analysis = analyze_ticker(config, ticker)
        except (AnalysisError, KeylessError) as exc:
            raise ValueError(str(exc)) from None
        rows = [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()} for row in analysis.metrics]
        issues = [{"fiscal_year_end": r.fiscal_year_end.isoformat(), "severity": s, "code": c, "message": m}
                  for r in analysis.reports for s, c, m in r.issues]
        return {"ticker": analysis.ticker, "cik": analysis.cik, "known_at": analysis.known_at.isoformat(),
                "metrics": rows, "issues": issues}

    def _doctor(self, body: dict) -> dict:
        from .doctor import run_checks

        return {"checks": [asdict(c) for c in run_checks(online=bool(body.get("online")))]}

    def _quit(self, _body) -> dict:
        threading.Thread(target=self.server.shutdown, daemon=True).start()
        return {"ok": True}

    # ---- plumbing

    def _read_json(self) -> dict | None:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return None
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return None
        try:
            value = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    def _send_json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload, default=str).encode()
        self._send_bytes(body, "application/json", CONTROL_CSP, False, status)

    def _send_bytes(self, body: bytes, content_type: str, csp: str, head: bool, status=HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Security-Policy", csp)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.end_headers()
        if not head:
            self.wfile.write(body)


def _mime(name: str) -> str:
    if name.endswith(".js"):
        return "text/javascript"
    if name.endswith(".wasm"):
        return "application/wasm"
    guessed = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return guessed + ("; charset=utf-8" if guessed.startswith("text/") or guessed.endswith("json") else "")


# ---------------------------------------------------------------- entry


def app_command(*, port: int = 0, open_browser: bool = True, smoke_test: bool = False) -> int:
    home = _prepare_environment()
    if not smoke_test and port == 0:
        existing = _running_instance(home)
        if existing:
            print(f"First Current is already running: {existing.split('#')[0]}", flush=True)
            if open_browser:
                webbrowser.open(existing)
            else:
                print(f"Open: {existing}", flush=True)
            return 0
    try:
        server = AppServer(port)
    except OSError as exc:
        raise SystemExit(f"quantos app: port {port} is not available ({exc.strerror}); "
                         "another First Current may already be running, or omit --port") from None
    if smoke_test:
        return _smoke_test(server)
    print(f"First Current {__version__} — home: {home}", flush=True)
    print(f"Control center: {server.origin}/  (this window must stay open; press Ctrl+C to quit)", flush=True)
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(server.launch_url)).start()
    else:
        print(f"Open: {server.launch_url}", flush=True)
    instance = _record_instance(home, server)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        with contextlib.suppress(OSError):
            instance.unlink()
    return 0


INSTANCE_FILE = "app_instance.json"


def _record_instance(home: Path, server: AppServer) -> Path:
    """Lets a second launch reopen this server instead of starting another.
    Private to the user (0600): it holds this launch's token."""

    path = home / INSTANCE_FILE
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump({"url": server.launch_url, "pid": os.getpid()}, handle)
    return path


def _running_instance(home: Path) -> str | None:
    import urllib.error
    import urllib.request

    try:
        record = json.loads((home / INSTANCE_FILE).read_text())
        url = str(record["url"])
        base, token = url.split("/#token=", 1)
        if not base.startswith("http://127.0.0.1:"):
            return None
        request = urllib.request.Request(base + "/api/status", headers={"X-Quantos-Token": token})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=2) as response:
            return url if response.status == 200 else None
    except (OSError, ValueError, KeyError, urllib.error.URLError):
        return None


def _exercise_runtime_paths() -> None:
    """Imports and paths that packaging can silently break (lazy imports,
    package data), exercised offline so a bad build fails its smoke test."""

    import importlib
    import tempfile

    import duckdb

    for module in ("quantos.runbook", "quantos.keyless", "quantos.company_analysis", "quantos.market_data",
                   "quantos.adapters.feed_radar", "quantos.radar_cli", "quantos.terminal", "quantos.doctor"):
        importlib.import_module(module)
    con = duckdb.connect()
    try:  # TIMESTAMPTZ values make DuckDB import pytz lazily
        value = con.execute("SELECT TIMESTAMPTZ '2026-01-02 03:04:05+00'").fetchone()[0]
        assert value.tzinfo is not None, "DuckDB returned a naive timestamp"
    finally:
        con.close()
    from .terminal import TerminalExporter

    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        data.mkdir()
        store = duckdb.connect(str(data / "probe.duckdb"))
        store.execute("CREATE TABLE probe (t TIMESTAMPTZ, v DOUBLE)")
        store.execute("INSERT INTO probe VALUES (TIMESTAMPTZ '2026-01-02 03:04:05+00', 1.5)")
        store.close()
        export = TerminalExporter().export(data_dir=data, out_dir=data / "terminal")
        assert export.tables and (export.out_dir / "index.html").is_file(), "terminal export incomplete"
    from .company_analysis import render  # noqa: F401 - import check
    import exchange_calendars  # noqa: F401 - package data check

    exchange_calendars.get_calendar("XNYS")


def _smoke_test(server: AppServer) -> int:
    """Headless self-test for packaged builds: serves, authenticates, reports."""

    import urllib.request

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        _exercise_runtime_paths()
        page = opener.open(server.origin + "/").read()
        assert b"First Current" in page, "control center page missing"
        request = urllib.request.Request(server.origin + "/api/status", headers={"X-Quantos-Token": server.token})
        status = json.loads(opener.open(request).read())
        unauthorized = urllib.request.Request(server.origin + "/api/status")
        try:
            opener.open(unauthorized)
            raise AssertionError("API answered without a token")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        doctor = urllib.request.Request(server.origin + "/api/doctor", data=b"{}", method="POST",
                                        headers={"X-Quantos-Token": server.token, "Content-Type": "application/json"})
        checks = json.loads(opener.open(doctor).read())["checks"]
        engines = {c["name"]: c["status"] for c in checks}
        print(json.dumps({"smoke_test": "ok", "version": status["version"], "home": status["home"],
                          "QuantLib": engines.get("QuantLib"), "terminal_assets": engines.get("terminal assets")}))
        return 0
    except Exception as exc:
        print(json.dumps({"smoke_test": "failed", "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    finally:
        server.shutdown()
        server.server_close()
