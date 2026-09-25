"""``quantos doctor``: explains, in plain terms, what works on this machine.

Every check reports OK, WARN or FAIL with one line of advice. The doctor only
reads state; with ``--online`` it also makes one small request to each
keyless public source, through the same egress guard as real fetches.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import __version__
from .home import (
    CONFIG_NAME,
    DATA_DIR,
    OPTIONAL_KEYS,
    HomeError,
    configured_secrets,
    home_dir,
    load_config,
    secrets_dir,
)


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # OK | WARN | FAIL
    detail: str


def run_checks(*, online: bool = False, probe: Callable[[str], int] | None = None) -> tuple[Check, ...]:
    home = home_dir()
    checks: list[Check] = []

    version = sys.version_info
    checks.append(Check("python", "OK" if version >= (3, 11) else "FAIL", f"{sys.version.split()[0]} (needs 3.11+)"))
    checks.append(Check("quantos", "OK", f"version {__version__}, home {home}"))

    try:
        config = load_config(home)
        exists = (home / CONFIG_NAME).is_file()
        checks.append(Check("config", "OK" if exists else "WARN",
                            f"{home / CONFIG_NAME}" if exists else "no quantos.toml: run `quantos setup`"))
    except HomeError as exc:
        config = None
        checks.append(Check("config", "FAIL", str(exc)))

    if config is not None:
        checks.append(Check("contact identity", "OK" if config.contact_email else "FAIL",
                            config.contact_email or "missing: SEC and Crossref refuse anonymous clients (`quantos setup`)"))
        checks.append(Check("universe", "OK" if config.universe else "WARN",
                            f"{len(config.universe)} tickers" if config.universe else "empty: add tickers with `quantos setup`"))

    directory = secrets_dir(home)
    if directory.is_dir() and os.name == "posix":
        loose = [p.name for p in directory.iterdir() if p.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO)]
        mode = directory.stat().st_mode & 0o777
        if loose or mode & 0o077:
            checks.append(Check("secrets permissions", "FAIL",
                                f"group/other access on {directory} {loose or ''}: run `chmod -R go-rwx {directory}`"))
        else:
            checks.append(Check("secrets permissions", "OK", f"{directory} is private"))
    present = set(configured_secrets(home))
    for name, purpose in OPTIONAL_KEYS.items():
        checks.append(Check(f"key {name}", "OK" if name in present else "WARN",
                            "configured" if name in present else f"not set (optional): {purpose}"))
    if config is not None:
        needed = f"{config.price_provider.upper()}_API_KEY"
        if config.price_provider != "none" and needed not in present:
            checks.append(Check("price provider", "FAIL", f"{config.price_provider} selected but {needed} is missing"))
        else:
            checks.append(Check("price provider", "OK" if config.price_provider != "none" else "WARN",
                                config.price_provider if config.price_provider != "none"
                                else "keyless mode: no stock prices (fundamentals, macro, rates, FX, research still work)"))

    data = home / DATA_DIR
    try:
        data.mkdir(parents=True, exist_ok=True)
        probe_file = data / ".write-test"
        probe_file.write_text("ok")
        probe_file.unlink()
        free = shutil.disk_usage(data).free / 1e9
        checks.append(Check("data directory", "OK" if free > 2 else "WARN", f"{data} writable, {free:.1f} GB free"))
    except OSError as exc:
        checks.append(Check("data directory", "FAIL", f"{data}: {exc}"))

    from .security import KillSwitch

    status = KillSwitch().status()
    checks.append(Check("kill switch", "WARN" if status.engaged else "OK",
                        f"ENGAGED ({status.reason}): outbound fetches blocked" if status.engaged else "released"))

    for module, label, note in (
        ("QuantLib", "QuantLib", "pricing engine"),
        ("nautilus_trader", "NautilusTrader", "historical execution differential (Python 3.12+)"),
        ("ORE", "OpenSourceRisk/Engine", "optional `ore` extra, x86-64 Linux/Windows"),
    ):
        found = importlib.util.find_spec(module) is not None
        required = module == "QuantLib"
        checks.append(Check(label, "OK" if found else ("FAIL" if required else "WARN"),
                            note + ("" if found else " — not installed")))

    from .terminal_vendor import vendored_assets_dir

    vendored = vendored_assets_dir()
    checks.append(Check("terminal assets", "OK" if vendored else "WARN",
                        f"offline bundle at {vendored}" if vendored
                        else "loaded from jsDelivr at view time (run `quantos terminal vendor` for offline use)"))

    if online:
        from .keyless import KEYLESS_HEALTH_URLS

        run_probe = probe or _probe
        for label, url in KEYLESS_HEALTH_URLS.items():
            try:
                code = run_probe(url)
                checks.append(Check(f"online {label}", "OK" if code == 200 else "FAIL", f"HTTP {code}"))
            except Exception as exc:  # report, never crash the doctor
                checks.append(Check(f"online {label}", "FAIL", f"{type(exc).__name__}: {exc}"[:160]))
    return tuple(checks)


def _probe(url: str) -> int:
    import requests

    from .security import guarded

    guarded(url, "doctor")
    agent = os.environ.get("SEC_USER_AGENT", "First Current Quant OS doctor")
    for attempt in range(2):
        try:
            response = requests.get(url, headers={"User-Agent": agent}, timeout=30, stream=True)
            response.close()
            return response.status_code
        except requests.Timeout:
            if attempt:
                raise
    raise AssertionError("unreachable")


def doctor_command(*, online: bool) -> int:
    checks = run_checks(online=online)
    width = max(len(c.name) for c in checks)
    symbols = {"OK": "ok  ", "WARN": "warn", "FAIL": "FAIL"}
    for check in checks:
        print(f"[{symbols[check.status]}] {check.name.ljust(width)}  {check.detail}")
    failures = sum(c.status == "FAIL" for c in checks)
    warnings = sum(c.status == "WARN" for c in checks)
    print(f"\n{failures} failing, {warnings} warnings. " + ("Ready." if not failures else "Fix the FAIL lines first."))
    return 1 if failures else 0
