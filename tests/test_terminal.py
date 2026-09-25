import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from quantos.market_data import BarStore
from quantos.security import REDACTOR, AuditLog
from quantos.terminal import (
    OTHER_WORKSPACE,
    TABLE_WORKSPACE,
    WORKSPACES,
    TerminalError,
    TerminalExporter,
    make_server,
)
from tests.test_market_data import capture


def build_data(root: Path) -> None:
    store = BarStore(root / "market_data.duckdb")
    store.add(capture("tiingo", [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0]))
    store.close()
    audit = AuditLog(root / "audit.duckdb")
    audit.append(actor="ops", action="KILL_SWITCH_ENGAGED", subject="drill",
                 details={"note": "operator drill with terminal-secret-value-xyz"})
    (root / "nested").mkdir()
    con = duckdb.connect(str(root / "nested" / "custom.duckdb"))
    con.execute("CREATE TABLE experiment_notes (id INTEGER, weight DOUBLE, happened TIMESTAMPTZ, bad_payload_json VARCHAR)")
    con.execute("INSERT INTO experiment_notes VALUES (1, 0.5, TIMESTAMPTZ '2026-09-01 12:00:00+00', 'not json')")
    con.close()


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.data.mkdir()
        build_data(self.data)
        REDACTOR.register("terminal-secret-value-xyz")

    def tearDown(self):
        self.tmp.cleanup()

    def export(self, **kw):
        return TerminalExporter(**kw).export(data_dir=self.data, out_dir=self.data / "terminal",
                                             exported_at=datetime(2026, 9, 25, tzinfo=timezone.utc))

    def test_every_stored_table_maps_to_a_workspace(self):
        import re

        declared = set()
        for path in Path("src/quantos").glob("*.py"):
            declared |= set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", path.read_text()))
        self.assertEqual(declared - set(TABLE_WORKSPACE), set())
        self.assertEqual(len(TABLE_WORKSPACE), sum(len(t) for t in WORKSPACES.values()), "table listed twice")

    def test_export_is_read_only_content_addressed_and_typed(self):
        before = {p: p.read_bytes() for p in self.data.rglob("*.duckdb")}
        export = self.export()
        self.assertEqual({p: p.read_bytes() for p in self.data.rglob("*.duckdb")}, before)
        manifest = json.loads((export.out_dir / "terminal_manifest.json").read_text())
        self.assertEqual(manifest["authority"], "NONE")
        self.assertTrue(manifest["read_only"])
        self.assertEqual(manifest["export_id"], export.export_id)
        by_name = {t["name"]: t for t in manifest["tables"]}
        self.assertEqual(by_name["daily_bars"]["workspace"], "Markets")
        self.assertEqual(by_name["audit_log"]["workspace"], "Audit / Lineage")
        self.assertEqual(by_name["experiment_notes"]["workspace"], OTHER_WORKSPACE)
        self.assertEqual(by_name["experiment_notes"]["source"], "nested/custom.duckdb")
        for entry in manifest["tables"]:
            body = (export.out_dir / entry["file"]).read_bytes()
            self.assertEqual(hashlib.sha256(body).hexdigest(), entry["sha256"])
        bars = json.loads((export.out_dir / by_name["daily_bars"]["file"]).read_text())
        self.assertEqual(bars["schema"]["close"], "float")
        self.assertEqual(bars["schema"]["session_date"], "date")
        self.assertEqual(bars["schema"]["knowledge_time"], "datetime")
        self.assertNotIn("payload_json", bars["schema"])
        self.assertEqual(bars["rows"][0]["close"], 100.0)
        notes = json.loads((export.out_dir / by_name["experiment_notes"]["file"]).read_text())
        self.assertEqual(notes["schema"]["bad_payload_json"], "string")  # not an object: kept raw
        self.assertEqual(notes["rows"][0]["happened"], 1788264000000)
        for name in ("index.html", "terminal.js", "terminal.css"):
            self.assertTrue((export.out_dir / name).is_file())
        self.assertEqual(self.export().export_id, export.export_id, "export is deterministic")

    def test_secrets_are_scrubbed_and_row_limit_marks_truncation(self):
        export = self.export(row_limit=3)
        text = "".join(p.read_text() for p in (export.out_dir / "tables").iterdir())
        self.assertNotIn("terminal-secret-value-xyz", text)
        bars = next(t for t in export.tables if t.name == "daily_bars")
        self.assertEqual((bars.rows, bars.truncated), (3, True))

    def test_locked_store_is_reported_not_skipped_silently(self):
        writer = duckdb.connect(str(self.data / "market_data.duckdb"))
        try:
            export = self.export()
        finally:
            writer.close()
        # DuckDB allows concurrent readers only across processes without a writer; in-process
        # a second connection may succeed. Either the table is exported or the source is listed.
        names = {t.source for t in export.tables} | {u["source"] for u in export.unreadable}
        self.assertIn("market_data.duckdb", names)

    def test_html_pins_perspective_with_subresource_integrity(self):
        html = (Path("src/quantos/terminal_static") / "index.html").read_text()
        cdn = [line for line in html.splitlines() if "cdn.jsdelivr.net" in line]
        self.assertTrue(cdn)
        self.assertTrue(all("@3.8.0/" in line for line in cdn))
        self.assertEqual(html.count("integrity=\"sha384-"), 5)
        self.assertNotIn("<script>", html, "no inline scripts (CSP)")


class ServerTests(unittest.TestCase):
    def test_serve_is_loopback_only_read_only_and_sends_security_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            data.mkdir()
            build_data(data)
            export = TerminalExporter().export(data_dir=data, out_dir=data / "terminal")
            with self.assertRaises(TerminalError):
                make_server(directory=export.out_dir, host="0.0.0.0", port=0)
            with self.assertRaises(TerminalError):
                make_server(directory=data, port=0)
            server = make_server(directory=export.out_dir, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                response = opener.open(base + "/terminal_manifest.json")
                self.assertEqual(json.loads(response.read())["authority"], "NONE")
                self.assertIn("default-src 'none'", response.headers["Content-Security-Policy"])
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                for path, method in (("/tables/", "GET"), ("/", "POST"), ("/../audit.duckdb", "GET")):
                    with self.subTest(path=path, method=method), self.assertRaises(urllib.error.HTTPError):
                        opener.open(urllib.request.Request(base + path, method=method, data=b"" if method == "POST" else None))
            finally:
                server.shutdown()
                server.server_close()


@unittest.skipUnless(os.environ.get("QUANTOS_TERMINAL_SMOKE") == "1" and shutil.which("node"),
                     "set QUANTOS_TERMINAL_SMOKE=1 (needs node + playwright + chromium)")
class BrowserSmokeTests(unittest.TestCase):
    def test_terminal_renders_a_verified_table_in_chromium(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            data.mkdir()
            build_data(data)
            export = TerminalExporter().export(data_dir=data, out_dir=data / "terminal")
            server = make_server(directory=export.out_dir, port=0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                result = subprocess.run(
                    ["node", "tests/terminal_smoke.mjs", f"http://127.0.0.1:{server.server_address[1]}/",
                     str(Path(tmp) / "shot.png")],
                    capture_output=True, text=True, timeout=240,
                    env={**os.environ, "NODE_PATH": os.environ.get("NODE_PATH", "/opt/node22/lib/node_modules")},
                )
            finally:
                server.shutdown()
                server.server_close()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertIn("Markets", report["workspaces"])
        self.assertGreaterEqual(report["rows"], 1)
        self.assertIn("verified", report["integrity"])


if __name__ == "__main__":
    unittest.main()
