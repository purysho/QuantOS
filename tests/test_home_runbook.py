import base64
import hashlib
import io
import os
import stat
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from quantos.doctor import run_checks
from quantos.home import (
    HomeError,
    QuantosConfig,
    activate,
    configured_secrets,
    load_config,
    render_config,
    run_setup,
    save_config,
    store_secret,
)
from quantos.terminal_vendor import (
    VendorError,
    install_into_export,
    localize,
    vendor_perspective,
    vendored_assets_dir,
)


class HomeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.env = mock.patch.dict(os.environ, {"QUANTOS_HOME": str(self.home)}, clear=False)
        self.env.start()
        for name in ("SEC_USER_AGENT", "CROSSREF_MAILTO", "FEED_USER_AGENT", "QUANTOS_SECRETS_DIR",
                     "QUANTOS_SECRET_TIINGO_API_KEY"):
            os.environ.pop(name, None)
        self.cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self.cwd)
        self.env.stop()
        self.tmp.cleanup()

    def setup_home(self, **kw):
        with redirect_stdout(io.StringIO()):
            return run_setup(interactive=False, home=self.home, **kw)

    def test_config_round_trip_and_validation(self):
        config = QuantosConfig(contact_email="a@b.org", organization='Desk "Q"', universe=("AAPL", "BRK.B"))
        save_config(config, self.home)
        loaded = load_config(self.home)
        self.assertEqual((loaded.contact_email, loaded.organization, loaded.universe),
                         ("a@b.org", 'Desk "Q"', ("AAPL", "BRK.B")))
        for bad in (QuantosConfig(contact_email="nope"), QuantosConfig(universe=("aapl ok",)),
                    QuantosConfig(price_provider="yahoo")):
            with self.subTest(bad=bad), self.assertRaises(HomeError):
                render_config(bad)

    def test_user_agent_has_no_url(self):
        agent = QuantosConfig(contact_email="a@b.org", organization="Desk").user_agent
        self.assertEqual(agent, "First Current Quant OS Desk a@b.org")
        self.assertNotIn("http", agent)

    def test_setup_requires_email_and_stores_private_keys(self):
        with self.assertRaises(HomeError):
            self.setup_home(email="", organization="", universe=())
        config = self.setup_home(email="a@b.org", organization="", universe=("AAPL",),
                                 keys={"TIINGO_API_KEY": "abcdef123456"})
        self.assertEqual(config.price_provider, "tiingo")
        secret = self.home / "secrets" / "TIINGO_API_KEY"
        self.assertEqual(stat.S_IMODE(secret.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(secret.parent.stat().st_mode), 0o700)
        self.assertNotIn("abcdef123456", (self.home / "quantos.toml").read_text())
        self.assertEqual(configured_secrets(self.home), ("TIINGO_API_KEY",))
        with self.assertRaises(HomeError):
            store_secret("TIINGO_API_KEY", "short", self.home)
        with self.assertRaises(HomeError):
            store_secret("SOMETHING_ELSE", "abcdef123456", self.home)

    def test_interactive_setup_uses_prompts(self):
        answers = iter(["me@example.org", "Solo", "aapl, msft"])
        with redirect_stdout(io.StringIO()):
            config = run_setup(home=self.home, prompt=lambda _: next(answers), secret_prompt=lambda _: "")
        self.assertEqual(config.universe, ("AAPL", "MSFT"))
        self.assertEqual(config.price_provider, "none")

    def test_activate_enters_home_and_exports_identity_without_overriding(self):
        self.setup_home(email="a@b.org", organization="Desk", universe=(), keys={"FRED_API_KEY": "fredkey123456"})
        os.environ["CROSSREF_MAILTO"] = "explicit@example.org"
        activate()
        self.assertEqual(Path.cwd().resolve(), self.home.resolve())
        self.assertEqual(os.environ["SEC_USER_AGENT"], "First Current Quant OS Desk a@b.org")
        self.assertEqual(os.environ["CROSSREF_MAILTO"], "explicit@example.org")
        self.assertEqual(os.environ["QUANTOS_SECRETS_DIR"], str(self.home / "secrets"))
        from quantos.security import SecretProvider

        self.assertEqual(SecretProvider().get("FRED_API_KEY").reveal(), "fredkey123456")

    def test_doctor_reports_missing_setup_then_ready(self):
        checks = {c.name: c for c in run_checks()}
        self.assertEqual(checks["contact identity"].status, "FAIL")
        self.setup_home(email="a@b.org", organization="", universe=("AAPL",))
        checks = {c.name: c for c in run_checks()}
        self.assertEqual(checks["contact identity"].status, "OK")
        self.assertEqual(checks["price provider"].status, "WARN")  # keyless mode is valid
        self.assertEqual(checks["QuantLib"].status, "OK")
        online = {c.name: c for c in run_checks(online=True, probe=lambda url: 200 if "sec.gov" in url else 503)}
        self.assertEqual(online["online SEC tickers"].status, "OK")
        self.assertEqual(online["online US Treasury"].status, "FAIL")

    def test_doctor_flags_loose_secret_permissions(self):
        self.setup_home(email="a@b.org", organization="", universe=(), keys={"FRED_API_KEY": "fredkey123456"})
        os.chmod(self.home / "secrets" / "FRED_API_KEY", 0o644)
        checks = {c.name: c for c in run_checks()}
        self.assertEqual(checks["secrets permissions"].status, "FAIL")


def fake_tarball(package: str, *, license_file=True, evil=False) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        files = {
            "package/dist/cdn/entry.js": b"export default 1;",
            "package/dist/cdn/entry.js.map": b"{}",
            "package/src/ignored.ts": b"x",
            "package/package.json": b'{"name": "%s"}' % package.encode(),
        }
        if license_file:
            files["package/LICENSE.md"] = b"Apache-2.0"
        if evil:
            files["package/../../escape.txt"] = b"bad"
        for name, body in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
    return buffer.getvalue()


def integrity(body: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(body).digest()).decode()


class VendorTests(unittest.TestCase):
    def test_vendor_verifies_integrity_extracts_runtime_and_license_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            bodies = {p: fake_tarball(p) for p in ("perspective", "perspective-viewer", "perspective-viewer-datagrid", "perspective-viewer-d3fc")}
            fetch = lambda url: next(b for p, b in bodies.items() if f"/{p}-3.8.0.tgz" in url)
            root = vendor_perspective(home=home, fetch=fetch, tarballs={p: integrity(b) for p, b in bodies.items()})
            package = root / "perspective@3.8.0"
            self.assertTrue((package / "dist/cdn/entry.js").is_file())
            self.assertTrue((package / "LICENSE.md").is_file())
            self.assertFalse((package / "dist/cdn/entry.js.map").exists())
            self.assertFalse((package / "src").exists())
            self.assertEqual(vendored_assets_dir(home), root)
            out = home / "export"
            out.mkdir()
            (out / "index.html").write_text('<script src="https://cdn.jsdelivr.net/npm/@finos/perspective@3.8.0/x.js">')
            (out / "terminal.js").write_text('import p from "https://cdn.jsdelivr.net/npm/@finos/perspective@3.8.0/x.js";')
            self.assertTrue(install_into_export(out, home=home))
            self.assertIn('"./vendor/npm/@finos/perspective@3.8.0/x.js"', (out / "terminal.js").read_text())
            self.assertTrue((out / "vendor/npm/@finos/perspective@3.8.0/LICENSE.md").is_file())

    def test_vendor_refuses_tampered_unlicensed_or_unsafe_tarballs(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            good = fake_tarball("perspective")
            cases = {
                "tampered": (good, integrity(b"other")),
                "no license": (fake_tarball("perspective", license_file=False), None),
                "path escape": (fake_tarball("perspective", evil=True), None),
            }
            for name, (body, expected) in cases.items():
                with self.subTest(name), self.assertRaises(VendorError):
                    vendor_perspective(home=home, fetch=lambda url, body=body: body,
                                       tarballs={"perspective": expected or integrity(body)})
            self.assertIsNone(vendored_assets_dir(home))

    def test_localize_only_touches_the_pinned_cdn(self):
        self.assertEqual(localize("https://cdn.jsdelivr.net/npm/@finos/a"), "./vendor/npm/@finos/a")
        self.assertEqual(localize("https://example.org/x"), "https://example.org/x")


class RunbookTests(unittest.TestCase):
    def test_daily_isolates_failures_and_reports_every_step(self):
        from quantos import runbook

        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"QUANTOS_HOME": tmp}):
            cwd = os.getcwd()
            try:
                os.chdir(tmp)
                with redirect_stdout(io.StringIO()):
                    run_setup(email="a@b.org", organization="", universe=("AAPL",), interactive=False, home=Path(tmp))
                with mock.patch.object(runbook, "step_universe", return_value=("1/1 tickers", {"AAPL": (320193, "SEC:0000320193:AAPL")})), \
                     mock.patch.object(runbook, "step_fundamentals", side_effect=RuntimeError("SEC down")), \
                     mock.patch.object(runbook, "step_rates", return_value="inserted=1"), \
                     mock.patch.object(runbook, "step_research", return_value="0 items"), \
                     mock.patch.object(runbook, "step_terminal", return_value="exported"):
                    results = runbook.run_daily()
                    with redirect_stdout(io.StringIO()) as out:
                        code = runbook.daily_command(steps=("rates",), backfill_from=None, price_days=5)
            finally:
                os.chdir(cwd)
        by_step = {r.step: r for r in results}
        self.assertEqual(list(by_step), ["universe", "fundamentals", "rates", "prices", "research", "terminal"])
        self.assertFalse(by_step["fundamentals"].ok)
        self.assertTrue(by_step["terminal"].ok)
        self.assertIn("keyless mode", by_step["prices"].summary)
        self.assertEqual(code, 0)
        self.assertIn("rates", out.getvalue())


if __name__ == "__main__":
    unittest.main()
