"""Content-addressed capture of the runtime a result was produced in.

The manifest identity covers everything that can change a numerical result
(interpreter, OS/architecture, libc, exact package versions, native library
builds, solver availability, lockfile fingerprints and code revision) and
nothing that merely describes where it ran (hostname, paths, clock time).
Two machines with identical environments therefore share one manifest id.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
import tomllib
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path

import duckdb

from .license_policy import (
    PROJECT_DISTRIBUTION,
    installed_runtime_closure,
    normalize_distribution_name,
)

MANIFEST_SCHEMA_VERSION = "1"


class LockConsistency(str, Enum):
    LOCKED_MATCH = "LOCKED_MATCH"
    LOCK_DRIFT = "LOCK_DRIFT"
    LOCK_ABSENT = "LOCK_ABSENT"


class CodeRevisionState(str, Enum):
    CLEAN = "CLEAN"
    DIRTY = "DIRTY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class EnvironmentManifest:
    manifest_id: str
    schema_version: str
    project_version: str
    python_implementation: str
    python_version: str
    operating_system: str
    machine: str
    libc: str
    packages: tuple[tuple[str, str], ...]
    native_builds: tuple[tuple[str, str], ...]
    cvxpy_solvers: tuple[str, ...]
    uv_lock_sha256: str | None
    requirements_lock_sha256: str | None
    lock_consistency: LockConsistency
    lock_drift: tuple[str, ...]
    code_revision: str
    code_revision_state: CodeRevisionState
    captured_at: datetime


class EnvironmentManifestBuilder:
    def capture(
        self,
        *,
        project_root: str | Path | None = None,
        captured_at: datetime | None = None,
    ) -> EnvironmentManifest:
        root = (
            Path(project_root)
            if project_root is not None
            else _discover_project_root()
        )
        packages = tuple(
            (name, package_version(name))
            for name in installed_runtime_closure()
        )
        uv_lock = root / "uv.lock" if root is not None else None
        requirements_lock = (
            root / "requirements.lock" if root is not None else None
        )
        consistency, drift = _lock_consistency(packages, uv_lock)
        revision, revision_state = _code_revision(root)
        libc_name, libc_version = platform.libc_ver()
        return self.build(
            project_version=package_version(PROJECT_DISTRIBUTION),
            python_implementation=platform.python_implementation(),
            python_version=platform.python_version(),
            operating_system=platform.system(),
            machine=platform.machine(),
            libc=(
                f"{libc_name} {libc_version}".strip()
                if libc_name
                else "UNKNOWN"
            ),
            packages=packages,
            native_builds=_native_builds(),
            cvxpy_solvers=_cvxpy_solvers(),
            uv_lock_sha256=_sha256_file(uv_lock),
            requirements_lock_sha256=_sha256_file(requirements_lock),
            lock_consistency=consistency,
            lock_drift=drift,
            code_revision=revision,
            code_revision_state=revision_state,
            captured_at=captured_at or datetime.now(timezone.utc),
        )

    def build(
        self,
        *,
        project_version: str,
        python_implementation: str,
        python_version: str,
        operating_system: str,
        machine: str,
        libc: str,
        packages: tuple[tuple[str, str], ...],
        native_builds: tuple[tuple[str, str], ...],
        cvxpy_solvers: tuple[str, ...],
        uv_lock_sha256: str | None,
        requirements_lock_sha256: str | None,
        lock_consistency: LockConsistency,
        lock_drift: tuple[str, ...],
        code_revision: str,
        code_revision_state: CodeRevisionState,
        captured_at: datetime,
    ) -> EnvironmentManifest:
        if captured_at.tzinfo is None:
            raise ValueError("environment capture time must be timezone-aware")
        names = [name for name, _ in packages]
        if len(names) != len(set(names)):
            raise ValueError("environment manifest has duplicate packages")
        if (lock_consistency is LockConsistency.LOCK_DRIFT) != bool(
            lock_drift
        ):
            raise ValueError(
                "lock drift details must accompany LOCK_DRIFT only"
            )
        manifest = EnvironmentManifest(
            manifest_id="",
            schema_version=MANIFEST_SCHEMA_VERSION,
            project_version=project_version,
            python_implementation=python_implementation,
            python_version=python_version,
            operating_system=operating_system,
            machine=machine,
            libc=libc,
            packages=tuple(sorted(packages)),
            native_builds=tuple(sorted(native_builds)),
            cvxpy_solvers=tuple(sorted(cvxpy_solvers)),
            uv_lock_sha256=uv_lock_sha256,
            requirements_lock_sha256=requirements_lock_sha256,
            lock_consistency=lock_consistency,
            lock_drift=tuple(sorted(lock_drift)),
            code_revision=code_revision,
            code_revision_state=code_revision_state,
            captured_at=captured_at,
        )
        return _with_identity(manifest)


class EnvironmentManifestStore:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(
            """
            CREATE TABLE IF NOT EXISTS environment_manifests (
                manifest_id VARCHAR PRIMARY KEY,
                lock_consistency VARCHAR NOT NULL,
                code_revision VARCHAR NOT NULL,
                first_captured_at TIMESTAMPTZ NOT NULL,
                payload_json VARCHAR NOT NULL
            )
            """
        )

    def add(self, manifest: EnvironmentManifest) -> bool:
        if manifest.manifest_id != environment_manifest_identity(manifest):
            raise ValueError("environment manifest identity mismatch")
        payload = json.dumps(
            environment_manifest_payload(manifest),
            sort_keys=True,
            separators=(",", ":"),
        )
        row = self._con.execute(
            "SELECT payload_json FROM environment_manifests "
            "WHERE manifest_id = ?",
            [manifest.manifest_id],
        ).fetchone()
        if row is not None:
            if str(row[0]) != payload:
                raise ValueError("environment manifest identity conflict")
            return False
        self._con.execute(
            "INSERT INTO environment_manifests VALUES (?, ?, ?, ?, ?)",
            [
                manifest.manifest_id,
                manifest.lock_consistency.value,
                manifest.code_revision,
                manifest.captured_at,
                payload,
            ],
        )
        return True

    def close(self) -> None:
        self._con.close()


def environment_manifest_payload(
    manifest: EnvironmentManifest,
) -> dict[str, object]:
    """Identity payload; excludes capture time by design."""

    return {
        "schema_version": manifest.schema_version,
        "project_version": manifest.project_version,
        "python_implementation": manifest.python_implementation,
        "python_version": manifest.python_version,
        "operating_system": manifest.operating_system,
        "machine": manifest.machine,
        "libc": manifest.libc,
        "packages": [list(item) for item in manifest.packages],
        "native_builds": [list(item) for item in manifest.native_builds],
        "cvxpy_solvers": list(manifest.cvxpy_solvers),
        "uv_lock_sha256": manifest.uv_lock_sha256,
        "requirements_lock_sha256": manifest.requirements_lock_sha256,
        "lock_consistency": manifest.lock_consistency.value,
        "lock_drift": list(manifest.lock_drift),
        "code_revision": manifest.code_revision,
        "code_revision_state": manifest.code_revision_state.value,
    }


def environment_manifest_identity(manifest: EnvironmentManifest) -> str:
    material = json.dumps(
        environment_manifest_payload(manifest),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "environment-manifest:" + hashlib.sha256(material).hexdigest()


def environment_manifest_document(
    manifest: EnvironmentManifest,
) -> dict[str, object]:
    """Human/CI-readable form: identity payload plus id and capture time."""

    return {
        "manifest_id": manifest.manifest_id,
        "captured_at": manifest.captured_at.isoformat(),
        **environment_manifest_payload(manifest),
    }


def locked_versions(uv_lock: Path) -> dict[str, frozenset[str]]:
    lock = tomllib.loads(uv_lock.read_text())
    versions: dict[str, set[str]] = {}
    for package in lock.get("package", ()):
        if "version" not in package:
            continue
        versions.setdefault(
            normalize_distribution_name(package["name"]),
            set(),
        ).add(str(package["version"]))
    return {name: frozenset(items) for name, items in versions.items()}


def _with_identity(manifest: EnvironmentManifest) -> EnvironmentManifest:
    return replace(
        manifest,
        manifest_id=environment_manifest_identity(manifest),
    )


def _lock_consistency(
    packages: tuple[tuple[str, str], ...],
    uv_lock: Path | None,
) -> tuple[LockConsistency, tuple[str, ...]]:
    if uv_lock is None or not uv_lock.is_file():
        return LockConsistency.LOCK_ABSENT, ()
    locked = locked_versions(uv_lock)
    drift = tuple(
        f"{name} installed={installed} locked="
        + (",".join(sorted(locked[name])) if name in locked else "ABSENT")
        for name, installed in packages
        if installed not in locked.get(name, frozenset())
    )
    if drift:
        return LockConsistency.LOCK_DRIFT, drift
    return LockConsistency.LOCKED_MATCH, ()


def _native_builds() -> tuple[tuple[str, str], ...]:
    builds: list[tuple[str, str]] = []
    try:
        import QuantLib

        builds.append(("QuantLib.library", str(QuantLib.__version__)))
    except Exception:
        builds.append(("QuantLib.library", "UNAVAILABLE"))
    try:
        import numpy

        config = numpy.show_config(mode="dicts")
        dependencies = config.get("Build Dependencies", {})
        for role in ("blas", "lapack"):
            info = dependencies.get(role, {})
            builds.append(
                (
                    f"numpy.{role}",
                    f"{info.get('name', 'UNKNOWN')} "
                    f"{info.get('version', 'UNKNOWN')}".strip(),
                )
            )
    except Exception:
        builds.append(("numpy.blas", "UNKNOWN"))
    if sys.version_info >= (3, 12):
        try:
            builds.append(
                ("nautilus_trader", package_version("nautilus_trader"))
            )
        except PackageNotFoundError:
            builds.append(("nautilus_trader", "UNAVAILABLE"))
    else:
        builds.append(("nautilus_trader", "UNSUPPORTED_PYTHON"))
    try:
        builds.append(
            ("open-source-risk-engine", package_version("open-source-risk-engine"))
        )
    except PackageNotFoundError:
        builds.append(("open-source-risk-engine", "NOT_INSTALLED"))
    return tuple(builds)


def _cvxpy_solvers() -> tuple[str, ...]:
    try:
        import cvxpy

        return tuple(str(item) for item in cvxpy.installed_solvers())
    except Exception:
        return ("UNAVAILABLE",)


def _code_revision(
    root: Path | None,
) -> tuple[str, CodeRevisionState]:
    if root is None or not (root / ".git").exists():
        return "UNKNOWN", CodeRevisionState.UNKNOWN
    try:
        revision = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        status = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "status",
                "--porcelain",
                "--untracked-files=no",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN", CodeRevisionState.UNKNOWN
    return (
        "git:" + revision,
        CodeRevisionState.DIRTY if status else CodeRevisionState.CLEAN,
    )


def _discover_project_root() -> Path | None:
    candidates = [Path.cwd(), *Path(__file__).resolve().parents]
    for candidate in candidates:
        if (candidate / "pyproject.toml").is_file() and (
            candidate / "src" / "quantos"
        ).is_dir():
            return candidate
    return None


def _sha256_file(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capture_environment_manifest(
    *,
    db: str | None = None,
) -> int:
    manifest = EnvironmentManifestBuilder().capture()
    if db is not None:
        store = EnvironmentManifestStore(db)
        try:
            store.add(manifest)
        finally:
            store.close()
    print(
        json.dumps(
            environment_manifest_document(manifest),
            indent=2,
            sort_keys=True,
        )
    )
    return 0
