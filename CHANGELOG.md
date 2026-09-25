# Changelog

## 0.20.0 — Desktop app and one-click downloads

- **Desktop app for Windows, macOS and Linux.** It opens a control center in your browser: first-run setup, one-button data updates with live progress, company analysis, a health check, and the terminal. Everything stays on your computer (loopback only, per-launch token). It is single-instance, and portable mode keeps data in a `FirstCurrent-data` folder next to the program.
- **Download buttons.** Each tagged release publishes a portable Windows zip, a macOS dmg (Apple Silicon), a Linux AppImage, the live-USB ISO and a container image. SHA-256 checksums, an SBOM and build-provenance attestations come with every release. Every build must pass its own self-test on its own OS before it is published.
- **Company analysis** (`quantos analyze TICKER`, and in the app). Standardized annual statements from SEC XBRL, as originally filed, validated against accounting identities. The validator now handles FX effects on cash, noncontrolling interests and temporary equity. Metrics include growth, margins, free cash flow, returns and leverage.
- **Security kinds from the exchange listing.** ETFs, ADRs and preferreds come from Nasdaq's symbol directory, with the precise listing venue. Exchange-listed funds without an SEC company filing now resolve too.
- **Fama-French factors.** Daily five factors and momentum from the Kenneth R. French Data Library, keyless, with revisions kept.
- **Terminal.** Workspaces open on useful views (the Treasury curve chart, formatted company metrics). Exact-decimal values chart as numbers, and years and CIKs display as labels.
- **Reliability.** The SEC ticker directory is cached for a day. The health check sends light, spaced probes and explains rate limiting.
- **Licensing.** The build-only tools that make the desktop downloads (PyInstaller and its helpers) are reviewed in their own license table. A test keeps them out of the runtime.

## 0.19.0 — Open source and usable by anyone

- **Relicensed under Apache-2.0** (LICENSE, NOTICE, CONTRIBUTING, SECURITY).
- **Keyless public data:** SEC ticker directory and XBRL company facts (point-in-time fundamentals with restatements), US Treasury par curve, ECB FX, and FRED CSV. There are explicit knowledge-time policies.
- **First-run experience:** `quantos setup`, `quantos doctor [--online]` and `quantos daily`, with `QUANTOS_HOME` and a private secrets directory. Errors print as one friendly line.
- **Offline terminal:** `quantos terminal vendor` provides integrity-verified Perspective assets. Exports keep the newest rows, put hashes last, and show years and CIKs as text.
- **Container image:** non-root, read-only root filesystem, loopback-only terminal, offline assets included.
- **Live USB:** Debian 13 live-build recipe with encrypted persistence, a setup wizard, a daily timer and desktop launchers.
- A new user guide and a rewritten README.

## 0.18.0

Stages 12.10–18: Nautilus divergence resolution, exchange calendars and research return panels, ORE bonds, options, sensitivities and stress, NBER/SSRN/regulator research sources, observability and security controls, the market-data provider pipeline, and the read-only Perspective terminal. See HANDOFF.md.
