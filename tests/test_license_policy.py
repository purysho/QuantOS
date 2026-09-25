import tomllib
import unittest
from dataclasses import replace
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from unittest import mock

from quantos import license_policy
from quantos.license_policy import (
    PROJECT_DISTRIBUTION,
    REVIEWED_DEPENDENCY_LICENSES,
    installed_runtime_closure,
    license_violations,
    normalize_distribution_name,
    render_third_party_notices,
    unreviewed_runtime_dependencies,
)

ROOT = Path(__file__).resolve().parents[1]


def locked_distributions():
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    return {
        normalize_distribution_name(package["name"])
        for package in lock["package"]
        if normalize_distribution_name(package["name"])
        != PROJECT_DISTRIBUTION
    }


def project_installed():
    try:
        distribution(PROJECT_DISTRIBUTION)
    except PackageNotFoundError:
        return False
    return True


class LicensePolicyTests(unittest.TestCase):
    def test_reviewed_table_uses_only_allowed_licenses(self):
        self.assertEqual(license_violations(), ())

    def test_every_locked_distribution_has_a_reviewed_license(self):
        missing = locked_distributions() - set(REVIEWED_DEPENDENCY_LICENSES)
        self.assertEqual(missing, set(), "review and record these licenses")

    def test_reviewed_table_has_no_stale_entries(self):
        stale = set(REVIEWED_DEPENDENCY_LICENSES) - locked_distributions()
        self.assertEqual(stale, set())

    def test_third_party_notices_match_reviewed_table(self):
        self.assertEqual(
            (ROOT / "THIRD_PARTY_NOTICES.md").read_text(),
            render_third_party_notices(),
            "regenerate with: python -m quantos.license_policy "
            "> THIRD_PARTY_NOTICES.md",
        )

    def test_prohibited_license_is_reported(self):
        poisoned = dict(REVIEWED_DEPENDENCY_LICENSES)
        poisoned["duckdb"] = replace(
            poisoned["duckdb"],
            spdx_expression="AGPL-3.0-only",
        )
        with mock.patch.object(
            license_policy,
            "REVIEWED_DEPENDENCY_LICENSES",
            poisoned,
        ):
            self.assertTrue(
                any("prohibited" in item for item in license_violations())
            )

    def test_unreviewed_license_is_reported(self):
        poisoned = dict(REVIEWED_DEPENDENCY_LICENSES)
        poisoned["duckdb"] = replace(
            poisoned["duckdb"],
            spdx_expression="LicenseRef-Unknown",
        )
        with mock.patch.object(
            license_policy,
            "REVIEWED_DEPENDENCY_LICENSES",
            poisoned,
        ):
            self.assertTrue(
                any("not been reviewed" in item for item in license_violations())
            )

    def test_project_declares_apache_license(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        self.assertEqual(project["license"], "Apache-2.0")
        self.assertIn("NOTICE", project["license-files"])
        text = (ROOT / "LICENSE").read_text()
        self.assertIn("Apache License", text)
        self.assertIn("Version 2.0, January 2004", text)
        self.assertIn("Copyright 2026", (ROOT / "NOTICE").read_text())

    @unittest.skipUnless(project_installed(), "project is not installed")
    def test_installed_runtime_closure_is_reviewed(self):
        self.assertIn("numpy", installed_runtime_closure())
        self.assertEqual(unreviewed_runtime_dependencies(), ())


if __name__ == "__main__":
    unittest.main()
