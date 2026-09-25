# User Guide

This guide takes you from nothing to a working research terminal. You can use no API keys at all.

- [1. Choose how to run it](#1-choose-how-to-run-it)
- [2. First-run setup](#2-first-run-setup)
- [3. Check the installation](#3-check-the-installation)
- [4. Fetch data](#4-fetch-data)
- [5. Use the terminal](#5-use-the-terminal)
- [6. Research radar](#6-research-radar)
- [7. Where your data lives](#7-where-your-data-lives)
- [8. Keeping it updated automatically](#8-keeping-it-updated-automatically)
- [9. Troubleshooting](#9-troubleshooting)

## 1. Choose how to run it

### Live USB

This option needs no installation. Boot any 64-bit PC from a USB stick.

1. Get the ISO:
   - download `first-current-quant-os-<version>-amd64.iso` and its `.sha256` file from the project's releases, **or**
   - build it yourself with `packaging/live-usb/build.sh` (see [packaging/live-usb/README.md](../packaging/live-usb/README.md)).
2. Verify the ISO: `sha256sum -c first-current-quant-os-<version>-amd64.iso.sha256`.
3. Write it to a USB stick of 16 GB or more, with an encrypted persistence partition:
   ```bash
   sudo ./make-first-current-usb first-current-quant-os-<version>-amd64.iso /dev/sdX
   ```
   This erases the stick. You choose a passphrase for the persistence partition.
4. Boot from the stick. Choose the USB device in your PC's boot menu, often F12, F11, Esc or F2. Enter the passphrase when asked.
5. On first login, the welcome window runs setup and the first data fetch. Then the terminal opens in Firefox.

The live user is `quantos` with the standard Debian live password, `live`. Your home folder, including configuration, keys and data, is on the encrypted partition. Without persistence, everything is lost at shutdown.

### Container (Linux, macOS, Windows)

```bash
git clone https://github.com/purysho/First-Current-Quant-OS-prototype.git first-current
cd first-current
docker compose build
docker compose run --rm quantos setup
docker compose run --rm quantos daily
docker compose up -d terminal          # then open http://127.0.0.1:8765/
```

Your data lives in the `quantos-data` Docker volume. The terminal is published on your machine's loopback address only. The containers run with a read-only root filesystem and no Linux capabilities.

### Python (developers)

On Linux with Python 3.11+ and [uv](https://docs.astral.sh/uv/):

```bash
uv sync --locked                     # add --extra ore on x86-64 for the ORE engine
export QUANTOS_HOME=~/FirstCurrent   # where configuration and data will live
uv run quantos setup
```

## 2. First-run setup

`quantos setup` asks for three things:

1. **Contact email (required).**
   - SEC EDGAR and Crossref require every client to identify itself.
   - The email is sent only as the contact identity in requests to public sources that require one.
2. **Tickers (optional).** For example, `AAPL, MSFT, JPM, BRK.B`. You can change them any time by running `quantos setup` again.
3. **Optional free personal API keys.** Press Enter to skip any of them.

   | Key | Adds | Get it |
   |---|---|---|
   | `TIINGO_API_KEY` | daily stock prices | https://www.tiingo.com (free personal tier) |
   | `FRED_API_KEY` | macro series *as known on past dates* | https://fredaccount.stlouisfed.org |
   | `POLYGON_API_KEY` | a second price source for cross-checks | https://polygon.io (free tier) |

Keys are written to `$QUANTOS_HOME/secrets/`. Each key is a file readable only by you (mode 0600). Keys never go into `quantos.toml`, URLs or logs.

Setup can also run non-interactively, for scripts:

```bash
quantos setup --non-interactive --email you@example.org --universe AAPL,MSFT \
    --key-file TIINGO_API_KEY=/path/to/tiingo.key
```

## 3. Check the installation

```bash
quantos doctor --online
```

Every line is `ok`, `warn` or `FAIL`, with a one-line explanation:
- `warn` lines are optional features, such as keyless mode without prices;
- `FAIL` lines stop something from working, and each tells you how to fix it.

## 4. Fetch data

```bash
quantos daily                     # everything
quantos daily --only rates        # one step: universe, fundamentals, rates, prices, research, terminal
quantos daily --backfill-from 2015  # load Treasury/ECB history once
```

| Step | What it does |
|---|---|
| universe | Resolves your tickers through SEC's directory and records the company, security, listing and CIK in the Security Master. |
| fundamentals | Every XBRL fact your companies reported, with filing dates. Restatements are kept as later versions. |
| rates | Treasury par curve, ECB FX (USD, GBP, JPY, CHF) and key FRED series (10y and 2y yields, fed funds, CPI, unemployment, breakevens). |
| prices | Recent daily bars from your configured provider (skipped in keyless mode). |
| research | New working papers from NBER, Fed, BIS and ECB, triaged into the review queue. |
| terminal | Refreshes the terminal export. |

Every step is safe to repeat: identical data is not stored twice, and changed data becomes a new version. If one step fails, the others still run, and the summary tells you which step failed.

**How knowledge time is recorded.**
- SEC facts are known at the end of their filing day, New York time.
- Everything else is known when First Current fetched it.
- For Treasury and ECB history, a publication-schedule assumption can be chosen instead (Treasury 18:00 New York, ECB 16:00 Frankfurt). It is recorded on every row.
- FRED CSV values are latest revisions, so they are always stamped with their fetch time; with a FRED key, use real vintages instead.

## 5. Use the terminal

```bash
quantos terminal serve     # http://127.0.0.1:8765/
```

- **Workspaces** (left) group tables: Markets, Research Radar, Company / Financials, Portfolio, Risk, Execution, Audit / Lineage and more. A workspace shows a count once it has data.
- **Tables** (top) open in a Perspective grid. Use **configure** to pivot, group, filter, sort and chart. Changes are views only, and nothing is written back.
- The header shows the export ID and how many tables passed their **SHA-256 check**. The browser verifies every table before displaying it.
- Large tables keep the most recent 200,000 rows and are marked *truncated*.
- For offline use, run `quantos terminal vendor` once. The USB and container images already include it.

## 6. Research radar

```bash
quantos-radar scan-feeds                                  # NBER, Fed, BIS, ECB working papers
quantos-radar scan-feeds --source sec-press --source fed-press
quantos-radar scan-ssrn --query "factor momentum" --from-posted-date 2026-01-01
quantos-radar scan-arxiv --max-results 20
quantos-radar review-list --status QUEUED
```

Radar items are *leads*, not facts. They enter triage and a human review queue and never become claims automatically.

## 7. Where your data lives

```
$QUANTOS_HOME/              (live USB: ~/FirstCurrent; container: /quantos)
    quantos.toml            configuration — safe to share
    secrets/                API keys — private (0700/0600)
    data/                   DuckDB stores, archived source files, terminal export
```

Back up the whole folder to keep your history. Every archived source file is content-addressed (SHA-256), so a backup can be verified.

## 8. Keeping it updated automatically

- **Live USB:** a systemd user timer runs `quantos daily` after the US close on weekdays. Check it with `systemctl --user list-timers`.
- **Linux:** add a cron entry, e.g. `30 22 * * 1-5 QUANTOS_HOME=$HOME/FirstCurrent quantos daily`.
- **Container:** schedule `docker compose run --rm quantos daily` with cron, launchd or Task Scheduler.

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| `no contact email configured` | Run `quantos setup`. |
| `secret TIINGO_API_KEY is not configured` | Run `quantos setup` and enter the key, or use keyless mode. |
| `host … is not on the egress allowlist` | The request went to an unregistered host. Add it with `QUANTOS_EGRESS_EXTRA_HOSTS=host` only if you trust it. |
| `KillSwitchEngaged` | Run `quantos kill-switch status`, then `quantos kill-switch release --actor you`. |
| A ticker is "unresolved" | It is not in SEC's directory (for example, a non-US listing). Check the spelling: `BRK.B` and `BRK-B` both work. |
| `no XBRL financials (funds/trusts)` | ETFs and trusts do not file XBRL financial statements. This is expected. |
| Terminal page is blank offline | Run `quantos terminal vendor`, then `quantos terminal export`. |
| Anything else | Rerun with `QUANTOS_DEBUG=1` for a full traceback, and report it via GitHub issues. |
