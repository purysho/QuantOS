import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from unittest.mock import patch

from quantos.environment_manifest import (
    CodeRevisionState,
    EnvironmentManifestBuilder,
    EnvironmentManifestStore,
    LockConsistency,
    _lock_consistency,
    environment_manifest_identity,
    locked_versions,
)

AT = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def project_installed():
    try:
        distribution("quantos")
    except PackageNotFoundError:
        return False
    return True


def manifest(**overrides):
    values = {
        "project_version": "0.12.4",
        "python_implementation": "CPython",
        "python_version": "3.12.0",
        "operating_system": "Linux",
        "machine": "x86_64",
        "libc": "glibc 2.39",
        "packages": (("numpy", "2.4.6"), ("duckdb", "1.5.5")),
        "native_builds": (("QuantLib.library", "1.43"),),
        "cvxpy_solvers": ("SCS", "CLARABEL"),
        "uv_lock_sha256": "a" * 64,
        "requirements_lock_sha256": "b" * 64,
        "lock_consistency": LockConsistency.LOCKED_MATCH,
        "lock_drift": (),
        "code_revision": "git:" + "c" * 40,
        "code_revision_state": CodeRevisionState.CLEAN,
        "captured_at": AT,
    }
    values.update(overrides)
    return EnvironmentManifestBuilder().build(**values)


class EnvironmentManifestTests(unittest.TestCase):
    def test_identity_ignores_capture_time_and_input_order(self):
        first = manifest()
        later = manifest(
            captured_at=AT + timedelta(days=3),
            packages=(("duckdb", "1.5.5"), ("numpy", "2.4.6")),
        )
        self.assertEqual(first.manifest_id, later.manifest_id)
        self.assertTrue(first.manifest_id.startswith("environment-manifest:"))

    def test_identity_changes_with_any_package_version(self):
        self.assertNotEqual(
            manifest().manifest_id,
            manifest(packages=(("numpy", "2.4.7"), ("duckdb", "1.5.5"))).manifest_id,
        )

    def test_identity_changes_with_native_build_and_revision(self):
        base = manifest().manifest_id
        self.assertNotEqual(
            base,
            manifest(native_builds=(("QuantLib.library", "1.44"),)).manifest_id,
        )
        self.assertNotEqual(
            base,
            manifest(code_revision_state=CodeRevisionState.DIRTY).manifest_id,
        )

    def test_duplicate_packages_fail_closed(self):
        with self.assertRaises(ValueError):
            manifest(packages=(("numpy", "2.4.6"), ("numpy", "2.4.7")))

    def test_drift_state_requires_details(self):
        with self.assertRaises(ValueError):
            manifest(lock_consistency=LockConsistency.LOCK_DRIFT)
        with self.assertRaises(ValueError):
            manifest(lock_drift=("numpy installed=1 locked=2",))

    def test_naive_capture_time_fails_closed(self):
        with self.assertRaises(ValueError):
            manifest(captured_at=datetime(2026, 9, 25, 12))

    def test_lock_consistency_detects_drift_and_absence(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "uv.lock"
            lock.write_text(
                'version = 1\n'
                '[[package]]\nname = "numpy"\nversion = "2.4.6"\n'
                '[[package]]\nname = "numpy"\nversion = "2.5.3"\n'
                '[[package]]\nname = "DuckDB"\nversion = "1.5.5"\n'
            )
            self.assertEqual(
                locked_versions(lock)["numpy"],
                frozenset({"2.4.6", "2.5.3"}),
            )
            state, drift = _lock_consistency(
                (("numpy", "2.5.3"), ("duckdb", "1.5.5")),
                lock,
            )
            self.assertEqual(state, LockConsistency.LOCKED_MATCH)
            self.assertEqual(drift, ())
            state, drift = _lock_consistency(
                (("numpy", "2.6.0"), ("scipy", "1.0")),
                lock,
            )
            self.assertEqual(state, LockConsistency.LOCK_DRIFT)
            self.assertEqual(
                drift,
                (
                    "numpy installed=2.6.0 locked=2.4.6,2.5.3",
                    "scipy installed=1.0 locked=ABSENT",
                ),
            )
            state, _ = _lock_consistency((), Path(tmp) / "missing.lock")
            self.assertEqual(state, LockConsistency.LOCK_ABSENT)

    def test_store_is_idempotent_and_detects_tampering(self):
        item = manifest()
        with tempfile.TemporaryDirectory() as tmp:
            store = EnvironmentManifestStore(Path(tmp) / "env.duckdb")
            self.assertTrue(store.add(item))
            self.assertFalse(store.add(replace(item, captured_at=AT + timedelta(1))))
            with self.assertRaises(ValueError):
                store.add(replace(item, python_version="3.13.0"))
            store.close()

    @unittest.skipUnless(project_installed(), "project is not installed")
    def test_capture_describes_running_environment(self):
        captured = EnvironmentManifestBuilder().capture(
            project_root=ROOT,
            captured_at=AT,
        )
        self.assertEqual(
            captured.manifest_id,
            environment_manifest_identity(captured),
        )
        names = dict(captured.packages)
        self.assertIn("numpy", names)
        self.assertIn("quantlib", names)
        self.assertIsNotNone(captured.uv_lock_sha256)
        self.assertIn(
            captured.lock_consistency,
            {LockConsistency.LOCKED_MATCH, LockConsistency.LOCK_DRIFT},
        )

    def test_cli_routes_to_capture(self):
        from quantos import cli

        with patch("sys.argv", ["quantos", "env-manifest", "--db", "x.db"]), patch.object(
            cli, "capture_environment_manifest", return_value=5
        ) as command:
            self.assertEqual(cli.main(), 5)
            command.assert_called_once_with(db="x.db")


if __name__ == "__main__":
    unittest.main()
