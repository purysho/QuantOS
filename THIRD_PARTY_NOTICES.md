# Third-Party Notices

First Current Quant OS is proprietary (see LICENSE). It does not vendor, modify or redistribute the packages below; they are installed separately from their own distributions under their own licenses. The versions in use are pinned in `uv.lock` and `requirements.lock`.

This file is generated from `quantos.license_policy` (`python -m quantos.license_policy > THIRD_PARTY_NOTICES.md`), and a test keeps the two identical.

## Weak-copyleft components

These are allowed only while unmodified, installed as separate packages and imported dynamically, so a user can replace them with another compatible version. If any of them is ever modified, vendored, statically linked or shipped inside a First Current distribution, the obligations of its license (source availability of the modified component, license text, replaceability) must be reviewed before release.

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
| highspy | MIT |  |
| idna | BSD-3-Clause |  |
| jinja2 | BSD-3-Clause |  |
| joblib | BSD-3-Clause |  |
| markupsafe | BSD-3-Clause |  |
| narwhals | MIT |  |
| numpy | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |  |
| open-source-risk-engine | BSD-3-Clause | optional 'ore' extra; ORE-SWIG modified BSD; bundles QuantLib (BSD) |
| osqp | Apache-2.0 |  |
| packaging | Apache-2.0 OR BSD-2-Clause |  |
| pandas | BSD-3-Clause |  |
| plotly | MIT |  |
| pycparser | BSD-3-Clause |  |
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
| tzdata | Apache-2.0 | Windows-only transitive |
| urllib3 | MIT |  |

## Prohibited for the core

| License | Reason |
| --- | --- |
| AGPL-3.0-only | network copyleft would reach the proprietary core |
| AGPL-3.0-or-later | network copyleft would reach the proprietary core |
| BUSL-1.1 | restricts production use |
| GPL-2.0-only | strong copyleft would reach the proprietary core |
| GPL-3.0-only | strong copyleft would reach the proprietary core |
| GPL-3.0-or-later | strong copyleft would reach the proprietary core |
| PolyForm-Noncommercial-1.0.0 | forbids commercial use |
| SSPL-1.0 | service-source copyleft |
