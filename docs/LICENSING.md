# Licensing

## Decision

First Current Quant OS is free and open source under the **Apache License 2.0** (`LICENSE`, `NOTICE`). Anyone may use, study, modify and redistribute it, including commercially. Apache-2.0 includes an explicit patent grant. It also asks that redistributions keep the `LICENSE` and `NOTICE` files and mark modified files.

The project was proprietary until v0.19.0 and was relicensed by its owner. The copyright line names the GitHub owner (`purysho`) and the project's contributors. Contributions are accepted under the same license (see `CONTRIBUTING.md`).

## Dependency license policy

`quantos.license_policy` is the gate for third-party licenses:

- Every distribution in `uv.lock` must be listed there with its reviewed SPDX license. Any new transitive dependency fails `tests/test_license_policy.py` until it is reviewed.
- Only permissive licenses (MIT, BSD, Apache-2.0, 0BSD, Zlib, CC0, MIT-0) are allowed without conditions.
- `LGPL-3.0-only` (NautilusTrader) and `MPL-2.0` (certifi) are allowed only while the package stays unmodified, separately installed and dynamically imported. Vendoring, modifying or statically bundling either one requires a fresh license review before any distribution.
- AGPL, GPL, PolyForm Noncommercial, BUSL and SSPL are prohibited for runtime dependencies. GPL/AGPL would force the combined Python application off Apache-2.0; the others restrict use. That matches the earlier research on OpenBB, SilvioBaratto/optimizer and ArcticDB. (System programs in the Debian image, such as the Linux kernel, are separate works and are not affected.)
- `THIRD_PARTY_NOTICES.md` is generated from the policy table, and a test keeps the two identical.

OpenSourceRisk/Engine (modified BSD) and exchange_calendars (Apache-2.0) are now in the lockfile and listed in the table. Future integrations such as Polars (MIT) and Pandera (MIT) must be added to the table when they enter the lockfile. Re-verify each license at that time.

Browser assets (Stage 18): the terminal loads FINOS Perspective 3.8.0 (`@finos/perspective`, `-viewer`, `-viewer-datagrid`, `-viewer-d3fc`; all Apache-2.0) from jsDelivr at runtime. Nothing from it is vendored into the repository or the Python distribution. The files are pinned by exact version and SRI hash, so a version bump requires re-verifying the license and regenerating the hashes. Offline bundles are covered below.


## Redistributed images (container and live USB)

The source repository vendors nothing. The container image and the live-USB image, however, **do redistribute** third-party software, and that brings obligations that go beyond the source tree:

- **Python packages** are installed unmodified from their wheels. Each keeps its license files inside its installed `*.dist-info` directory, and `THIRD_PARTY_NOTICES.md` is copied into the image.
- **LGPL-3.0 (NautilusTrader) and MPL-2.0 (certifi)** stay unmodified and replaceable: a user can `uv pip install` another version inside the image. The corresponding source is the package's published source distribution on PyPI; the image build records the exact versions (`requirements.lock`), so the sources can be located.
- **Debian base system (live USB and container base).** It contains GPL and other copyleft packages. Anyone who publishes a built ISO must also make the matching Debian sources available. The live-build configuration can produce a source image alongside the ISO (`LB_SOURCE=true`; see `packaging/live-usb/README.md`). Pointing to `snapshot.debian.org` for the exact package versions also satisfies this.
- **Desktop downloads** (PyInstaller bundles) contain the Python runtime (PSF license) and the locked runtime packages unmodified, each with its license files. `THIRD_PARTY_NOTICES.md`, `LICENSE` and `NOTICE` ship inside every bundle. PyInstaller itself is GPL-2.0-or-later **with a bootloader exception** that explicitly allows distributing the bundled app under any license; it is a build-only dependency (the `desktop` group). The Linux AppImage is assembled with `appimagetool` (MIT), pinned by SHA-256; its runtime is embedded in the AppImage under its own license.
- **FINOS Perspective (Apache-2.0)** is bundled only when offline terminal assets are vendored (`quantos terminal vendor`). The bundle includes each package's `LICENSE` (and `NOTICE`, where present).

This is an engineering policy, not legal advice. Have counsel review it before commercial distribution of built images.
