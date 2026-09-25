import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from quantos.app import AppServer, desktop_home


class AppServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"QUANTOS_HOME": self.tmp.name})
        self.env.start()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        for name in ("SEC_USER_AGENT", "CROSSREF_MAILTO", "QUANTOS_SECRETS_DIR"):
            os.environ.pop(name, None)
        self.server = AppServer(0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        os.chdir(self.cwd)
        self.env.stop()
        self.tmp.cleanup()

    def call(self, path, body=None, *, token=True, headers=None):
        request = urllib.request.Request(self.server.origin + path, method="POST" if body is not None else "GET",
                                         data=json.dumps(body).encode() if body is not None else None)
        if body is not None:
            request.add_header("Content-Type", "application/json")
        if token:
            request.add_header("X-Quantos-Token", self.server.token)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        with self.opener.open(request) as response:
            return response.status, json.loads(response.read() or b"{}"), response.headers

    def status_code(self, *args, **kwargs):
        try:
            return self.call(*args, **kwargs)[0]
        except urllib.error.HTTPError as exc:
            return exc.code

    def test_control_center_is_served_with_strict_headers(self):
        with self.opener.open(self.server.origin + "/") as response:
            page = response.read().decode()
            csp = response.headers["Content-Security-Policy"]
        self.assertIn("QuantOS", page)
        self.assertNotIn("<script>", page, "no inline scripts")
        self.assertIn("script-src 'self'", csp)
        self.assertIn("#token=", self.server.launch_url)

    def test_api_requires_token_same_origin_and_loopback_host(self):
        self.assertEqual(self.status_code("/api/status", token=False), 403)
        self.assertEqual(self.status_code("/api/status", headers={"X-Quantos-Token": "wrong"}, token=False), 403)
        self.assertEqual(self.status_code("/api/status", headers={"Origin": "https://evil.example"}), 403)
        self.assertEqual(self.status_code("/api/status", headers={"Host": "evil.example"}), 421)
        self.assertEqual(self.status_code("/api/status"), 200)

    def test_setup_stores_keys_privately_and_never_returns_them(self):
        code, result, _ = self.call("/api/setup", {"contact_email": "a@b.org", "organization": "Desk",
                                                   "universe": "aapl, msft", "keys": {"TIINGO_API_KEY": "secret-value-123"}})
        self.assertEqual((code, result["price_provider"]), (200, "tiingo"))
        _, status, _ = self.call("/api/status")
        self.assertTrue(status["configured"])
        self.assertEqual(status["config"]["universe"], ["AAPL", "MSFT"])
        self.assertTrue(status["keys"]["TIINGO_API_KEY"]["configured"])
        self.assertNotIn("secret-value-123", json.dumps(status))
        secret = Path(self.tmp.name) / "secrets" / "TIINGO_API_KEY"
        self.assertEqual(secret.stat().st_mode & 0o777, 0o600)

    def test_bad_requests_are_refused_cleanly(self):
        self.assertEqual(self.status_code("/api/setup", {"contact_email": "not-an-email"}), 400)
        self.assertEqual(self.status_code("/api/update", {"steps": ["rm -rf"]}), 400)
        self.assertEqual(self.status_code("/api/nope"), 404)
        self.assertEqual(self.status_code("/api/analyze", {"ticker": ""}), 400)
        request = urllib.request.Request(self.server.origin + "/api/setup", data=b"contact_email=a@b.org", method="POST",
                                         headers={"X-Quantos-Token": self.server.token})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.opener.open(request)
        self.assertEqual(caught.exception.code, 400, "form posts are not accepted, only JSON")

    def test_static_and_terminal_paths_cannot_escape(self):
        for path in ("/../pyproject.toml", "/%2e%2e/secrets/X", "/terminal/../../quantos.toml", "/app.js/../../x"):
            with self.subTest(path=path):
                self.assertIn(self.status_code(path, token=False), {400, 404})
        self.assertEqual(self.status_code("/terminal/", token=False), 404, "no export yet")
        export = Path(self.tmp.name) / "data" / "terminal"
        export.mkdir(parents=True)
        (export / "index.html").write_text("<html>terminal</html>")
        with self.opener.open(self.server.origin + "/terminal/") as response:
            self.assertEqual(response.read(), b"<html>terminal</html>")
            self.assertIn("cdn.jsdelivr.net", response.headers["Content-Security-Policy"])

    def test_second_launch_finds_the_running_instance(self):
        from quantos.app import _record_instance, _running_instance

        home = Path(self.tmp.name)
        self.assertIsNone(_running_instance(home))
        path = _record_instance(home, self.server)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(_running_instance(home), self.server.launch_url)
        path.write_text(json.dumps({"url": "http://evil.example/#token=x"}))
        self.assertIsNone(_running_instance(home), "only loopback instances are trusted")

    def test_preflight_and_writes_to_static_are_refused(self):
        request = urllib.request.Request(self.server.origin + "/api/setup", method="OPTIONS")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.opener.open(request)
        self.assertEqual(caught.exception.code, 403)
        self.assertEqual(self.status_code("/index.html", {}, token=True), 405)


class DesktopHomeTests(unittest.TestCase):
    def test_platform_home_and_portable_mode(self):
        with mock.patch("sys.platform", "linux"), mock.patch.dict(os.environ, {"XDG_DATA_HOME": "/xdg"}):
            self.assertEqual(desktop_home(), Path("/xdg/QuantOS"))
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "QuantOS.exe"
            (Path(tmp) / "QuantOS-data").mkdir()
            with mock.patch("sys.frozen", True, create=True), mock.patch("sys.executable", str(exe)):
                self.assertEqual(desktop_home(), Path(tmp).resolve() / "QuantOS-data")


if __name__ == "__main__":
    unittest.main()
