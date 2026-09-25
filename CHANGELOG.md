# Changelog

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
