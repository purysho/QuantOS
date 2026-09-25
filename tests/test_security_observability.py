import io
import json
import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb

from quantos import observability
from quantos.observability import (
    METRICS,
    SINK,
    ErrorCategory,
    ObservabilityStore,
    classify_exception,
    current_run_id,
    emit,
    span,
)
from quantos.security import (
    REDACTOR,
    AuditLog,
    EgressGuard,
    EgressPolicy,
    KillSwitch,
    KillSwitchEngaged,
    NetworkDenied,
    Secret,
    SecretProvider,
    SecretUnavailable,
)

ROOT = Path(__file__).resolve().parents[1]


class KillSwitchAndEgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.switch = KillSwitch(path=Path(self.tmp.name) / "KILL", environ={})
        self.guard = EgressGuard(policy=EgressPolicy.default(environ={}), kill_switch=self.switch)

    def tearDown(self):
        self.tmp.cleanup()

    def test_allowlisted_https_passes(self):
        self.guard.check("https://api.crossref.org/works?rows=1", purpose="test")

    def test_non_allowlisted_host_and_plain_http_are_denied(self):
        with self.assertRaises(NetworkDenied):
            self.guard.check("https://evil.example.com/x", purpose="test")
        with self.assertRaises(NetworkDenied):
            self.guard.check("http://api.crossref.org/works", purpose="test")

    def test_kill_switch_blocks_all_egress_and_is_audited(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = AuditLog(Path(tmp) / "audit.duckdb")
            self.switch.engage(reason="incident drill", actor="ops", audit=log)
            with self.assertRaises(KillSwitchEngaged):
                self.guard.check("https://api.crossref.org/works", purpose="test")
            self.switch.release(actor="ops", audit=log)
            self.guard.check("https://api.crossref.org/works", purpose="test")
            self.assertEqual([r.action for r in log.records()], ["KILL_SWITCH_ENGAGED", "KILL_SWITCH_RELEASED"])
            log.close()

    def test_environment_kill_switch(self):
        switch = KillSwitch(path=Path(self.tmp.name) / "none", environ={"QUANTOS_KILL_SWITCH": "1"})
        with self.assertRaises(KillSwitchEngaged):
            switch.require_released("order routing")

    def test_adapters_route_through_the_guard(self):
        from quantos.adapters import crossref_radar

        with patch.dict(os.environ, {"QUANTOS_KILL_SWITCH": "1"}):
            with self.assertRaises(KillSwitchEngaged):
                crossref_radar._default_transport("https://api.crossref.org/works", {}, 1.0)
        with self.assertRaises(NetworkDenied):
            crossref_radar._default_transport("https://example.com/works", {}, 1.0)


class SecretTests(unittest.TestCase):
    def test_secret_never_prints_and_is_redacted(self):
        secret = Secret("TIINGO_API_KEY", "sk_live_super_secret_value")
        self.assertNotIn("super_secret", repr(secret))
        self.assertNotIn("super_secret", str(secret))
        self.assertEqual(REDACTOR.redact("token sk_live_super_secret_value leaked"), "token *** leaked")
        self.assertIn("api_key=***", REDACTOR.redact_url("https://api.x.org/p?api_key=abc123&x=1"))
        with self.assertRaises(TypeError):
            import pickle
            pickle.dumps(secret)

    def test_provider_resolves_env_then_permission_checked_directory(self):
        provider = SecretProvider(environ={"QUANTOS_SECRET_FRED_API_KEY": "abcd1234"})
        self.assertEqual(provider.get("FRED_API_KEY").reveal(), "abcd1234")
        with self.assertRaises(SecretUnavailable):
            provider.get("MISSING_KEY")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "POLYGON_API_KEY"
            path.write_text("poly-secret-9\n")
            os.chmod(path, 0o600)
            audit = AuditLog(Path(tmp) / "a.duckdb")
            provider = SecretProvider(environ={}, secrets_dir=tmp, audit=audit)
            self.assertEqual(provider.get("POLYGON_API_KEY").reveal(), "poly-secret-9")
            self.assertEqual(audit.records()[0].action, "SECRET_RESOLVED")
            self.assertNotIn("poly-secret-9", json.dumps(audit.records()[0].details))
            if os.name == "posix":
                os.chmod(path, 0o644)
                with self.assertRaises(SecretUnavailable):
                    provider.get("POLYGON_API_KEY")
            audit.close()


class AuditLogTests(unittest.TestCase):
    def test_hash_chain_detects_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "audit.duckdb"
            log = AuditLog(db)
            for i in range(3):
                log.append(actor="ops", action="TEST", subject=f"s{i}", details={"i": i})
            self.assertTrue(log.verify().valid)
            log.close()
            con = duckdb.connect(str(db))
            con.execute("UPDATE audit_log SET subject='forged' WHERE sequence=2")
            con.close()
            log = AuditLog(db)
            result = log.verify()
            self.assertFalse(result.valid)
            self.assertEqual(result.first_bad_sequence, 2)
            log.close()
            con = duckdb.connect(str(db))
            con.execute("DELETE FROM audit_log WHERE sequence=2")
            con.close()
            log = AuditLog(db)
            self.assertFalse(log.verify().valid)
            log.close()


class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        self.buffer = io.StringIO()
        self.previous = (SINK.stream, SINK.store)
        SINK.stream = self.buffer
        METRICS.reset()

    def tearDown(self):
        SINK.stream, SINK.store = self.previous

    def events(self):
        return [json.loads(line) for line in self.buffer.getvalue().splitlines()]

    def test_spans_propagate_run_and_parent_and_record_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ObservabilityStore(Path(tmp) / "obs.duckdb")
            SINK.store = store
            with span("research", "panel-build") as outer:
                run_id = current_run_id()
                with span("ore", "npv"):
                    emit("ore", "note", value=1)
            with self.assertRaises(ValueError):
                with span("execution", "simulate"):
                    raise ValueError("Nautilus execution result identity mismatch")
            report = store.report()
            store.close()
        events = self.events()
        inner_start = next(e for e in events if e["event"] == "npv.start")
        self.assertEqual(inner_start["run_id"], run_id)
        self.assertEqual(inner_start["parent_span_id"], outer)
        self.assertEqual(report["errors_by_category"], {"IDENTITY_MISMATCH": 1})
        self.assertEqual(report["runs"], 2)
        snapshot = METRICS.snapshot()
        self.assertIn("errors{category=IDENTITY_MISMATCH,component=execution}", snapshot["counters"])
        self.assertIn("ore.npv", snapshot["durations"])

    def test_events_are_redacted(self):
        Secret("PROVIDER_TOKEN", "tok_abcdef123456")
        emit("provider", "request", url="https://api.tiingo.com/x?token=tok_abcdef123456")
        self.assertNotIn("tok_abcdef123456", self.buffer.getvalue())

    def test_error_taxonomy(self):
        self.assertEqual(classify_exception(KillSwitchEngaged("x")), ErrorCategory.KILL_SWITCH_ENGAGED)
        self.assertEqual(classify_exception(NetworkDenied("x")), ErrorCategory.NETWORK_DENIED)
        self.assertEqual(classify_exception(ValueError("partial fills are outside the X equivalence contract")), ErrorCategory.SCOPE_REFUSED)
        self.assertEqual(classify_exception(ValueError("ORE worker failed")), ErrorCategory.EXTERNAL_ENGINE_FAILURE)
        self.assertEqual(classify_exception(ValueError("close must be positive")), ErrorCategory.FAIL_CLOSED_VALIDATION)
        self.assertEqual(classify_exception(RuntimeError("boom")), ErrorCategory.INTERNAL_ERROR)


class RepositorySecretLintTests(unittest.TestCase):
    PATTERNS = (
        re.compile(r"AKIA[0-9A-Z]{16}"),
        re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        re.compile(r"ghp_[A-Za-z0-9]{36}"),
        re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
        re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    )

    def test_tracked_files_contain_no_credentials(self):
        try:
            files = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, check=True).stdout.split()
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("not a git checkout")
        offenders = []
        for name in files:
            path = ROOT / name
            if path.suffix in {".lock", ".png", ".duckdb"} or not path.is_file():
                continue
            text = path.read_text(errors="ignore")
            for pattern in self.PATTERNS:
                if pattern.search(text):
                    offenders.append(f"{name}: {pattern.pattern}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
