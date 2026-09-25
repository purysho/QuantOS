"""Dependency license policy for First Current Quant OS (Apache-2.0).

Every distribution in the locked runtime closure must be listed here with
its reviewed SPDX license expression, and that expression must be allowed.
A new transitive dependency therefore fails the license gate until someone
reviews its license and records it, instead of entering silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from importlib.metadata import PackageNotFoundError, distribution

PROJECT_DISTRIBUTION = "first-current-quant-os"


class LicenseTreatment(str, Enum):
    PERMISSIVE = "PERMISSIVE"
    # Allowed only while the package stays unmodified, separately installed
    # and dynamically imported, so users can replace it.
    WEAK_COPYLEFT_UNMODIFIED = "WEAK_COPYLEFT_UNMODIFIED"


# SPDX identifiers the core may depend on, and how each must be treated.
ALLOWED_LICENSES: dict[str, LicenseTreatment] = {
    "0BSD": LicenseTreatment.PERMISSIVE,
    "Apache-2.0": LicenseTreatment.PERMISSIVE,
    "BSD-2-Clause": LicenseTreatment.PERMISSIVE,
    "BSD-3-Clause": LicenseTreatment.PERMISSIVE,
    "CC0-1.0": LicenseTreatment.PERMISSIVE,
    "MIT": LicenseTreatment.PERMISSIVE,
    "MIT-0": LicenseTreatment.PERMISSIVE,
    "Zlib": LicenseTreatment.PERMISSIVE,
    "LGPL-3.0-only": LicenseTreatment.WEAK_COPYLEFT_UNMODIFIED,
    "MPL-2.0": LicenseTreatment.WEAK_COPYLEFT_UNMODIFIED,
}

# Licenses researched for this project and rejected for the core.
PROHIBITED_LICENSES: dict[str, str] = {
    "AGPL-3.0-only": "network copyleft would force the combined work off Apache-2.0",
    "AGPL-3.0-or-later": "network copyleft would force the combined work off Apache-2.0",
    "GPL-2.0-only": "strong copyleft would force the combined work off Apache-2.0",
    "GPL-3.0-only": "strong copyleft would force the combined work off Apache-2.0",
    "GPL-3.0-or-later": "strong copyleft would force the combined work off Apache-2.0",
    "PolyForm-Noncommercial-1.0.0": "forbids commercial use",
    "BUSL-1.1": "restricts production use",
    "SSPL-1.0": "service-source copyleft",
}


@dataclass(frozen=True)
class ReviewedDependencyLicense:
    distribution: str
    spdx_expression: str
    note: str = ""


# Reviewed licenses for the locked runtime closure (uv.lock), all platforms
# and all supported Python versions. Keys are normalized distribution names.
REVIEWED_DEPENDENCY_LICENSES: dict[str, ReviewedDependencyLicense] = {
    item.distribution: item
    for item in (
        ReviewedDependencyLicense("certifi", "MPL-2.0", "CA bundle data, unmodified"),
        ReviewedDependencyLicense("cffi", "MIT-0"),
        ReviewedDependencyLicense("charset-normalizer", "MIT"),
        ReviewedDependencyLicense("clarabel", "Apache-2.0"),
        ReviewedDependencyLicense("cloudpickle", "BSD-3-Clause"),
        ReviewedDependencyLicense("cvxpy", "Apache-2.0"),
        ReviewedDependencyLicense("cvxpy-base", "Apache-2.0"),
        ReviewedDependencyLicense("duckdb", "MIT"),
        ReviewedDependencyLicense("exchange-calendars", "Apache-2.0", "external calendar engine behind First Current rules"),
        ReviewedDependencyLicense("highspy", "MIT"),
        ReviewedDependencyLicense("idna", "BSD-3-Clause"),
        ReviewedDependencyLicense("jinja2", "BSD-3-Clause"),
        ReviewedDependencyLicense("joblib", "BSD-3-Clause"),
        ReviewedDependencyLicense("korean-lunar-calendar", "MIT"),
        ReviewedDependencyLicense("markupsafe", "BSD-3-Clause"),
        ReviewedDependencyLicense("narwhals", "MIT"),
        ReviewedDependencyLicense(
            "nautilus-trader",
            "LGPL-3.0-only",
            "Python 3.12+ only; historical BacktestEngine; never vendored or modified",
        ),
        ReviewedDependencyLicense(
            "numpy",
            "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0",
        ),
        ReviewedDependencyLicense(
            "open-source-risk-engine",
            "BSD-3-Clause",
            "optional 'ore' extra; ORE-SWIG modified BSD; bundles QuantLib (BSD)",
        ),
        ReviewedDependencyLicense("osqp", "Apache-2.0"),
        ReviewedDependencyLicense("packaging", "Apache-2.0 OR BSD-2-Clause"),
        ReviewedDependencyLicense("pandas", "BSD-3-Clause"),
        ReviewedDependencyLicense("plotly", "MIT"),
        ReviewedDependencyLicense("pycparser", "BSD-3-Clause"),
        ReviewedDependencyLicense(
            "python-dateutil",
            "Apache-2.0 AND BSD-3-Clause",
            "dual-licensed; both terms are permissive",
        ),
        ReviewedDependencyLicense("pyluach", "MIT"),
        ReviewedDependencyLicense("pytz", "MIT"),
        ReviewedDependencyLicense("qdldl", "Apache-2.0"),
        ReviewedDependencyLicense("quantlib", "BSD-3-Clause", "QuantLib modified BSD"),
        ReviewedDependencyLicense("requests", "Apache-2.0"),
        ReviewedDependencyLicense("scikit-learn", "BSD-3-Clause"),
        ReviewedDependencyLicense("scipy", "BSD-3-Clause"),
        ReviewedDependencyLicense("scs", "MIT"),
        ReviewedDependencyLicense("setuptools", "MIT"),
        ReviewedDependencyLicense("six", "MIT"),
        ReviewedDependencyLicense("skfolio", "BSD-3-Clause"),
        ReviewedDependencyLicense("sparsediffpy", "Apache-2.0"),
        ReviewedDependencyLicense("threadpoolctl", "BSD-3-Clause"),
        ReviewedDependencyLicense("toolz", "BSD-3-Clause"),
        ReviewedDependencyLicense("tzdata", "Apache-2.0", "IANA time zone data"),
        ReviewedDependencyLicense("urllib3", "MIT"),
    )
}

# Build-only tools (the locked ``desktop`` dependency group). They create the
# desktop downloads but are never imported at runtime; only PyInstaller's
# bootloader ends up in a bundle, which its exception explicitly permits.
REVIEWED_BUILD_TOOL_LICENSES: dict[str, ReviewedDependencyLicense] = {
    item.distribution: item
    for item in (
        ReviewedDependencyLicense("altgraph", "MIT"),
        ReviewedDependencyLicense("macholib", "MIT", "macOS builds only"),
        ReviewedDependencyLicense("pefile", "MIT", "Windows builds only"),
        ReviewedDependencyLicense(
            "pyinstaller",
            "GPL-2.0-or-later WITH Bootloader-exception",
            "the exception allows distributing bundled apps under any license",
        ),
        ReviewedDependencyLicense("pyinstaller-hooks-contrib", "Apache-2.0 OR GPL-2.0-or-later"),
        ReviewedDependencyLicense("pywin32-ctypes", "BSD-3-Clause", "Windows builds only"),
    )
}


def normalize_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def spdx_identifiers(expression: str) -> tuple[str, ...]:
    tokens = re.split(r"\s+|\(|\)", expression)
    return tuple(
        token
        for token in tokens
        if token and token not in {"AND", "OR", "WITH"}
    )


def license_violations() -> tuple[str, ...]:
    """Problems in the reviewed table itself."""

    problems: list[str] = []
    for name, reviewed in REVIEWED_DEPENDENCY_LICENSES.items():
        if name != normalize_distribution_name(name):
            problems.append(f"{name}: distribution name is not normalized")
        identifiers = spdx_identifiers(reviewed.spdx_expression)
        if not identifiers:
            problems.append(f"{name}: empty license expression")
        for identifier in identifiers:
            if identifier in PROHIBITED_LICENSES:
                problems.append(
                    f"{name}: {identifier} is prohibited "
                    f"({PROHIBITED_LICENSES[identifier]})"
                )
            elif identifier not in ALLOWED_LICENSES:
                problems.append(
                    f"{name}: {identifier} has not been reviewed"
                )
    return tuple(problems)


def installed_runtime_closure(
    root: str = PROJECT_DISTRIBUTION,
) -> tuple[str, ...]:
    """Normalized names of installed runtime dependencies of ``root``.

    Requirement markers are evaluated for the running interpreter, and
    extras are not followed, so this is exactly what the runtime imports.
    """

    from packaging.requirements import Requirement

    seen: set[str] = set()
    pending = [normalize_distribution_name(root)]
    while pending:
        name = pending.pop()
        try:
            dist = distribution(name)
        except PackageNotFoundError as exc:
            raise ValueError(
                f"required distribution {name} is not installed"
            ) from exc
        for raw in dist.requires or ():
            requirement = Requirement(raw)
            if requirement.marker is not None and not requirement.marker.evaluate(
                {"extra": ""}
            ):
                continue
            child = normalize_distribution_name(requirement.name)
            if child not in seen:
                seen.add(child)
                pending.append(child)
    seen.discard(normalize_distribution_name(root))
    return tuple(sorted(seen))


def unreviewed_runtime_dependencies(
    root: str = PROJECT_DISTRIBUTION,
) -> tuple[str, ...]:
    return tuple(
        name
        for name in installed_runtime_closure(root)
        if name not in REVIEWED_DEPENDENCY_LICENSES
    )


def render_third_party_notices() -> str:
    """Render THIRD_PARTY_NOTICES.md from the reviewed table."""

    lines = [
        "# Third-Party Notices",
        "",
        "First Current Quant OS is licensed under Apache-2.0 (see LICENSE "
        "and NOTICE). The source repository does not vendor or modify the "
        "packages below; they are installed from their own distributions "
        "under their own licenses. Container and live-USB images do "
        "redistribute them unmodified, with their license files kept in "
        "place inside each installed package. The versions in use are "
        "pinned in `uv.lock` and `requirements.lock`.",
        "",
        "This file is generated from `quantos.license_policy` "
        "(`python -m quantos.license_policy > THIRD_PARTY_NOTICES.md`), "
        "and a test keeps the two identical.",
        "",
        "## Weak-copyleft components",
        "",
        "These are allowed only while unmodified, installed as separate "
        "packages and imported dynamically, so a user can replace them with "
        "another compatible version. If any of them is ever modified, "
        "vendored, statically linked or shipped inside a First Current "
        "distribution, the obligations of its license (source availability "
        "of the modified component, license text, replaceability) must be "
        "reviewed before release.",
        "",
        "| Distribution | License | Note |",
        "| --- | --- | --- |",
    ]
    permissive: list[str] = []
    for name in sorted(REVIEWED_DEPENDENCY_LICENSES):
        reviewed = REVIEWED_DEPENDENCY_LICENSES[name]
        row = (
            f"| {name} | {reviewed.spdx_expression} | {reviewed.note} |"
        )
        treatments = {
            ALLOWED_LICENSES.get(identifier)
            for identifier in spdx_identifiers(reviewed.spdx_expression)
        }
        if LicenseTreatment.WEAK_COPYLEFT_UNMODIFIED in treatments:
            lines.append(row)
        else:
            permissive.append(row)
    lines += [
        "",
        "## Permissive components",
        "",
        "| Distribution | License | Note |",
        "| --- | --- | --- |",
        *permissive,
        "",
        "## Build-only tools",
        "",
        "Used to build the desktop downloads; not imported at runtime and "
        "not part of the core.",
        "",
        "| Distribution | License | Note |",
        "| --- | --- | --- |",
        *(
            f"| {name} | {tool.spdx_expression} | {tool.note} |"
            for name, tool in sorted(REVIEWED_BUILD_TOOL_LICENSES.items())
        ),
        "",
        "## Prohibited for the core",
        "",
        "| License | Reason |",
        "| --- | --- |",
        *(
            f"| {identifier} | {reason} |"
            for identifier, reason in sorted(PROHIBITED_LICENSES.items())
        ),
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    print(render_third_party_notices(), end="")
