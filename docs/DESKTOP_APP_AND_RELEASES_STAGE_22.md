# Stage 22 — Desktop App and Release Downloads

Modules: `quantos.app` and `quantos/app_static/`.
Packaging: `packaging/desktop/` (spec, entry point, `build.py`, icons) and `.github/workflows/release.yml`.

## The app

`quantos app`, or the packaged `FirstCurrent` executable, starts one **loopback-only** server and opens the user's own browser. No GUI toolkit is involved, so the same code runs on Windows, macOS and Linux.

| Path | What |
|---|---|
| `/` | Control center: setup, Update now (live step progress), Analyze a company, Health check |
| `/terminal/` | The read-only terminal export (same files and CSP as `quantos terminal serve`) |
| `/api/…` | JSON API: `status`, `job`, `setup`, `update`, `analyze`, `doctor`, `quit` |

**Security.**
- A random per-launch token travels in the URL **fragment**, so it never goes over the network or into referrers. Every API call needs it in `X-Quantos-Token`, compared in constant time.
- `Origin`, when present, must be the app's own origin.
- `Host` must be the app's loopback address (421 otherwise), which blocks DNS rebinding.
- Preflights are refused and bodies must be JSON, so another website cannot drive the API.
- Keys can be written but are never returned.
- Static paths cannot escape the app or export directories.
- The CSP is `default-src 'none'`, with only self-hosted scripts and styles.

**Data home.**
- Windows: `%LOCALAPPDATA%\FirstCurrent`.
- macOS: `~/Library/Application Support/FirstCurrent`.
- Linux: `$XDG_DATA_HOME/FirstCurrent`.
- Portable mode: a `FirstCurrent-data` folder next to the executable.

**Single instance.** A private (0600) `app_instance.json` lets a second launch reopen the running control center. Only loopback URLs are trusted.

## Builds

`packaging/desktop/build.py` runs on each OS's native runner:

1. Vendors Perspective, checking the npm integrity hashes.
2. Builds with PyInstaller from the locked `desktop` dependency group.
   - NautilusTrader and ORE are left out: they are used only by the engine differential tests, and ORE has no macOS wheels.
3. Runs `FirstCurrent --version` and `--smoke-test` from a clean profile. The smoke test covers:
   - every lazily imported module;
   - a DuckDB TIMESTAMPTZ round trip, which is how the missing `pytz` import was caught;
   - a terminal export;
   - the exchange-calendar data;
   - the control-center page;
   - token enforcement;
   - the health check.
4. Packages the build:

| Platform | Artifact | Notes |
|---|---|---|
| Windows 10/11 x64 | `FirstCurrent-Windows-x64.zip` | Portable folder plus README; console status window |
| macOS 12+ arm64 | `FirstCurrent-macOS-arm64.dmg` | `First Current.app`, ad-hoc signed by PyInstaller |
| Linux x86-64 | `FirstCurrent-Linux-x86_64.AppImage` | Built on Ubuntu 22.04 (glibc 2.35); `appimagetool` 1.9.0 pinned by SHA-256 |

On a tag `vX.Y.Z` (which must equal `pyproject.toml`'s version), `release.yml` also builds:
- the live-USB ISO plus its source image;
- the container image `ghcr.io/purysho/first-current:{version,latest}`, with a provenance attestation;
- a CycloneDX SBOM and `SHA256SUMS.txt`;
- build-provenance attestations for every file.

It then **publishes** the release with stable asset names, so `releases/latest/download/<asset>` links, which the README buttons use, always point to the newest build.

## Verified

- **Linux:** the frozen app passes a full update (7 of 7 steps, including SPY recognized as an ETF), a JPM analysis, and the terminal rendering with every CDN request blocked. The AppImage passes its smoke test.
- **macOS 14 arm64** (GitHub runner): the build succeeds and the smoke test passes (QuantLib OK, offline terminal assets OK).
- **Windows:** see the Release workflow run on the branch.

## Not yet

- **Code signing.** SmartScreen and Gatekeeper warn on first launch.
  - Windows: SignPath Foundation offers free signing for open-source projects.
  - macOS: notarization needs an Apple Developer account ($99/year).
  The workflow can add signing steps once credentials exist as repository secrets.
- **Intel Macs** (no x86-64 macOS build yet) and **Linux arm64**.
