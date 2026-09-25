# Third-Party Notices

QuantOS is licensed under Apache-2.0 (see LICENSE and NOTICE). The source repository does not vendor or modify the packages below; they are installed from their own distributions under their own licenses. Container and live-USB images do redistribute them unmodified, with their license files kept in place inside each installed package. The versions in use are pinned in `uv.lock` and `requirements.lock`.

This file is generated from `quantos.license_policy` (`python -m quantos.license_policy > THIRD_PARTY_NOTICES.md`), and a test keeps the two identical.

## Weak-copyleft components

These are allowed only while unmodified, installed as separate packages and imported dynamically, so a user can replace them with another compatible version. If any of them is ever modified, vendored, statically linked or shipped inside a QuantOS distribution, the obligations of its license (source availability of the modified component, license text, replaceability) must be reviewed before release.

| Distribution | License | Note |
| --- | --- | --- |
| certifi | MPL-2.0 | CA bundle data, unmodified |
| nautilus-trader | LGPL-3.0-only | Python 3.12+ only; historical BacktestEngine; never vendored or modified |

## Permissive components

| Distribution | License | Note |
| --- | --- | --- |
| cffi | MIT-0 |  |
| charset-normalizer | MIT |  |
| clarabel | Apache-2.0 |  |
| cloudpickle | BSD-3-Clause |  |
| cvxpy | Apache-2.0 |  |
| cvxpy-base | Apache-2.0 |  |
| duckdb | MIT |  |
| exchange-calendars | Apache-2.0 | external calendar engine behind QuantOS rules |
| highspy | MIT |  |
| idna | BSD-3-Clause |  |
| jinja2 | BSD-3-Clause |  |
| joblib | BSD-3-Clause |  |
| korean-lunar-calendar | MIT |  |
| markupsafe | BSD-3-Clause |  |
| narwhals | MIT |  |
| numpy | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |  |
| open-source-risk-engine | BSD-3-Clause | optional 'ore' extra; ORE-SWIG modified BSD; bundles QuantLib (BSD) |
| osqp | Apache-2.0 |  |
| packaging | Apache-2.0 OR BSD-2-Clause |  |
| pandas | BSD-3-Clause |  |
| plotly | MIT |  |
| pycparser | BSD-3-Clause |  |
| pyluach | MIT |  |
| python-dateutil | Apache-2.0 AND BSD-3-Clause | dual-licensed; both terms are permissive |
| pytz | MIT |  |
| qdldl | Apache-2.0 |  |
| quantlib | BSD-3-Clause | QuantLib modified BSD |
| requests | Apache-2.0 |  |
| scikit-learn | BSD-3-Clause |  |
| scipy | BSD-3-Clause |  |
| scs | MIT |  |
| setuptools | MIT |  |
| six | MIT |  |
| skfolio | BSD-3-Clause |  |
| sparsediffpy | Apache-2.0 |  |
| threadpoolctl | BSD-3-Clause |  |
| toolz | BSD-3-Clause |  |
| tzdata | Apache-2.0 | IANA time zone data |
| urllib3 | MIT |  |

## Build-only tools

Used to build the desktop downloads; not imported at runtime and not part of the core.

| Distribution | License | Note |
| --- | --- | --- |
| altgraph | MIT |  |
| macholib | MIT | macOS builds only |
| pefile | MIT | Windows builds only |
| pyinstaller | GPL-2.0-or-later WITH Bootloader-exception | the exception allows distributing bundled apps under any license |
| pyinstaller-hooks-contrib | Apache-2.0 OR GPL-2.0-or-later |  |
| pywin32-ctypes | BSD-3-Clause | Windows builds only |

## Prohibited for the core

| License | Reason |
| --- | --- |
| AGPL-3.0-only | network copyleft would force the combined work off Apache-2.0 |
| AGPL-3.0-or-later | network copyleft would force the combined work off Apache-2.0 |
| BUSL-1.1 | restricts production use |
| GPL-2.0-only | strong copyleft would force the combined work off Apache-2.0 |
| GPL-3.0-only | strong copyleft would force the combined work off Apache-2.0 |
| GPL-3.0-or-later | strong copyleft would force the combined work off Apache-2.0 |
| PolyForm-Noncommercial-1.0.0 | forbids commercial use |
| SSPL-1.0 | service-source copyleft |
