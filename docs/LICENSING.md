# Licensing Decision

## Decision

First Current Quant OS is **proprietary, all rights reserved** (see `LICENSE`). The package metadata declares `LicenseRef-Proprietary` and the `Private :: Do Not Upload` classifier, so PyPI refuses an accidental upload.

## Why proprietary rather than open source

- The repository is private, and the handoff consistently describes a commercial-capable core. Earlier dependency research rejected noncommercial (PolyForm), AGPL and BSL components specifically to keep that option open.
- The decision can be reversed. A proprietary codebase can be relicensed as open source later. Once code is published under an open-source license, that grant cannot be withdrawn.
- The core encodes investment process, controls and evidence discipline, which is where any commercial value sits.

If the project is ever opened, **Apache-2.0** is the recommended license. It includes an explicit patent grant and is compatible with every current dependency. Relicensing would need only an edit to `LICENSE` and the `license` field in `pyproject.toml`.

The copyright line names the GitHub owner (`purysho`). Replace it with a legal person's or entity's name if the work belongs to a company.

## Dependency license policy

`quantos.license_policy` is the gate for third-party licenses:

- Every distribution in `uv.lock` must be listed there with its reviewed SPDX license. Any new transitive dependency fails `tests/test_license_policy.py` until it is reviewed.
- Only permissive licenses (MIT, BSD, Apache-2.0, 0BSD, Zlib, CC0, MIT-0) are allowed without conditions.
- `LGPL-3.0-only` (NautilusTrader) and `MPL-2.0` (certifi) are allowed only while the package stays unmodified, separately installed and dynamically imported. Vendoring, modifying or statically bundling either one requires a fresh license review before any distribution.
- AGPL, GPL, PolyForm Noncommercial, BUSL and SSPL are prohibited for the core. That matches the earlier research on OpenBB, SilvioBaratto/optimizer and ArcticDB.
- `THIRD_PARTY_NOTICES.md` is generated from the policy table, and a test keeps the two identical.

OpenSourceRisk/Engine (modified BSD) and exchange_calendars (Apache-2.0) are now in the lockfile and listed in the table. Future integrations such as Polars (MIT) and Pandera (MIT) must be added to the table when they enter the lockfile. Re-verify each license at that time.

Browser assets (Stage 18): the terminal loads FINOS Perspective 3.8.0 (`@finos/perspective`, `-viewer`, `-viewer-datagrid`, `-viewer-d3fc`; all Apache-2.0) from jsDelivr at runtime. Nothing from it is vendored into the repository or the Python distribution. The files are pinned by exact version and SRI hash, so a version bump requires re-verifying the license and regenerating the hashes. Vendoring the assets for offline use would make them redistributed code and require including their NOTICE files.

This is an engineering policy, not legal advice. Have counsel review it before any commercial distribution.
