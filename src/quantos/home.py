"""Installation home, configuration and first-run setup.

One directory holds everything a user owns: configuration, private secrets,
data stores, archived source artifacts and terminal exports.

```
$QUANTOS_HOME/
    quantos.toml        configuration (no secrets)
    secrets/            API keys, one file per key, 0600, directory 0700
    data/               DuckDB stores, artifacts, terminal export
```

``QUANTOS_HOME`` selects the home. When it is unset, the current directory is
the home, which keeps the developer workflow (``data/...`` in the checkout)
unchanged. Every CLI calls :func:`activate` first. It changes into the home,
points the secret provider at ``secrets/``, and exports the contact identity
that SEC, Crossref and feed etiquette require. Explicitly set environment
variables always win over the configuration file.
"""

from __future__ import annotations

import getpass
import os
import re
import stat
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = "quantos.toml"
SECRETS_DIR = "secrets"
DATA_DIR = "data"
PROJECT_URL = "https://github.com/purysho/First-Current-Quant-OS-prototype"
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

# Optional personal keys. Every one of them is free for personal use.
OPTIONAL_KEYS: dict[str, str] = {
    "TIINGO_API_KEY": "Tiingo end-of-day stock prices (free personal tier: https://www.tiingo.com)",
    "FRED_API_KEY": "FRED/ALFRED point-in-time macro vintages (free: https://fredaccount.stlouisfed.org)",
    "POLYGON_API_KEY": "Polygon daily aggregates, for a second price source (free tier: https://polygon.io)",
}


class HomeError(ValueError):
    pass


@dataclass(frozen=True)
class QuantosConfig:
    contact_email: str = ""
    organization: str = ""
    universe: tuple[str, ...] = ()
    price_provider: str = "none"
    research_feeds: tuple[str, ...] = ()
    research_themes: tuple[str, ...] = ()
    extra: dict = field(default_factory=dict)

    @property
    def user_agent(self) -> str:
        # SEC's documented format is "Company Name contact@domain"; it rejects
        # agents containing URLs, so none is included here.
        who = " ".join(self.organization.split()) if self.organization else "user"
        return f"First Current Quant OS {who} {self.contact_email}"

    def validate(self) -> None:
        if self.contact_email and not _EMAIL.fullmatch(self.contact_email):
            raise HomeError(f"invalid contact email: {self.contact_email!r}")
        bad = [t for t in self.universe if not _TICKER.fullmatch(t)]
        if bad:
            raise HomeError("invalid tickers: " + ", ".join(bad))
        if self.price_provider not in {"none", "tiingo", "polygon"}:
            raise HomeError("price_provider must be none, tiingo or polygon")


def home_dir() -> Path:
    configured = os.environ.get("QUANTOS_HOME")
    return Path(configured).expanduser().resolve() if configured else Path.cwd().resolve()


def load_config(home: Path | None = None) -> QuantosConfig:
    path = (home or home_dir()) / CONFIG_NAME
    if not path.is_file():
        return QuantosConfig()
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise HomeError(f"{path} is not valid TOML: {exc}") from exc
    identity = document.get("identity", {})
    data = document.get("data", {})
    research = document.get("research", {})
    config = QuantosConfig(
        contact_email=str(identity.get("contact_email", "")).strip(),
        organization=str(identity.get("organization", "")).strip(),
        universe=tuple(str(t).strip().upper() for t in data.get("universe", [])),
        price_provider=str(data.get("price_provider", "none")).strip().lower(),
        research_feeds=tuple(str(f) for f in research.get("feeds", [])),
        research_themes=tuple(str(t) for t in research.get("themes", [])),
        extra={k: v for k, v in document.items() if k not in {"identity", "data", "research"}},
    )
    config.validate()
    return config


def render_config(config: QuantosConfig) -> str:
    config.validate()

    def array(values) -> str:
        return "[" + ", ".join(_toml_string(v) for v in values) + "]"

    return "\n".join(
        [
            "# First Current Quant OS configuration. Secrets never go in this file;",
            f"# they live in {SECRETS_DIR}/ (see `quantos setup`).",
            "",
            "[identity]",
            "# Required by SEC EDGAR and Crossref fair-access policies.",
            f"contact_email = {_toml_string(config.contact_email)}",
            f"organization = {_toml_string(config.organization)}",
            "",
            "[data]",
            "# Tickers you research. Resolved to SEC CIKs by `quantos universe`.",
            f"universe = {array(config.universe)}",
            "# none | tiingo | polygon (needs the matching key in secrets/)",
            f"price_provider = {_toml_string(config.price_provider)}",
            "",
            "[research]",
            "# Feed IDs from `quantos-radar scan-feeds --help`; empty = working papers.",
            f"feeds = {array(config.research_feeds)}",
            f"themes = {array(config.research_themes)}",
            "",
        ]
    )


def _toml_string(value: str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    if any(ord(ch) < 32 for ch in escaped):
        raise HomeError("configuration values cannot contain control characters")
    return f'"{escaped}"'


def save_config(config: QuantosConfig, home: Path | None = None) -> Path:
    home = home or home_dir()
    home.mkdir(parents=True, exist_ok=True)
    path = home / CONFIG_NAME
    temporary = path.with_suffix(".tmp")
    temporary.write_text(render_config(config), encoding="utf-8")
    temporary.replace(path)
    return path


def secrets_dir(home: Path | None = None) -> Path:
    return (home or home_dir()) / SECRETS_DIR


def store_secret(name: str, value: str, home: Path | None = None) -> Path:
    if name not in OPTIONAL_KEYS:
        raise HomeError(f"unknown secret {name}")
    value = value.strip()
    if len(value) < 8 or any(ch.isspace() for ch in value):
        raise HomeError(f"{name} does not look like an API key")
    directory = secrets_dir(home)
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, stat.S_IRWXU)
    path = directory / name
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(descriptor, "w") as handle:
        handle.write(value + "\n")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return path


def configured_secrets(home: Path | None = None) -> tuple[str, ...]:
    directory = secrets_dir(home)
    present = {n for n in OPTIONAL_KEYS if (directory / n).is_file()}
    present |= {n for n in OPTIONAL_KEYS if os.environ.get(f"QUANTOS_SECRET_{n}")}
    return tuple(sorted(present))


def activate() -> Path:
    """Enter the home and export configuration defaults for this process."""

    home = home_dir()
    if os.environ.get("QUANTOS_HOME"):
        home.mkdir(parents=True, exist_ok=True)
        os.chdir(home)
    try:
        config = load_config(home)
    except HomeError as exc:
        raise SystemExit(f"quantos: {exc}") from exc
    if secrets_dir(home).is_dir():
        os.environ.setdefault("QUANTOS_SECRETS_DIR", str(secrets_dir(home)))
    if config.contact_email:
        agent = config.user_agent
        os.environ.setdefault("SEC_USER_AGENT", agent)
        os.environ.setdefault("CROSSREF_MAILTO", config.contact_email)
        os.environ.setdefault("CROSSREF_USER_AGENT", agent)
        os.environ.setdefault("FEED_USER_AGENT", agent)
        os.environ.setdefault("ARXIV_USER_AGENT", agent)
    return home


def run_setup(
    *,
    email: str | None = None,
    organization: str | None = None,
    universe: tuple[str, ...] | None = None,
    keys: dict[str, str] | None = None,
    interactive: bool = True,
    home: Path | None = None,
    prompt=input,
    secret_prompt=getpass.getpass,
    out=None,
) -> QuantosConfig:
    """Creates or updates the home. Interactive by default; flags skip prompts."""

    home = home or home_dir()
    out = out or sys.stdout
    current = load_config(home)

    def ask(label: str, default: str) -> str:
        if not interactive:
            return default
        answer = prompt(f"{label}{f' [{default}]' if default else ''}: ").strip()
        return answer or default

    print("First Current Quant OS setup", file=out)
    print(f"Home: {home}", file=out)
    print("Nothing here is sent anywhere except as the contact identity in requests to", file=out)
    print("public data sources that require one (SEC EDGAR, Crossref, feed hosts).", file=out)
    email = email if email is not None else ask("Contact email (required by SEC and Crossref)", current.contact_email)
    organization = organization if organization is not None else ask("Name or organization (optional)", current.organization)
    if universe is None:
        default = ",".join(current.universe)
        universe = tuple(t.strip().upper() for t in ask("Tickers to research, comma-separated (optional)", default).split(",") if t.strip())
    keys = dict(keys or {})
    if interactive:
        print("\nOptional free personal API keys (press Enter to skip; stored in secrets/, mode 0600):", file=out)
        for name, purpose in OPTIONAL_KEYS.items():
            if name in keys:
                continue
            value = secret_prompt(f"  {name} ({purpose}): ").strip()
            if value:
                keys[name] = value
    for name, value in keys.items():
        store_secret(name, value, home)
    present = set(configured_secrets(home)) | set(keys)
    provider = current.price_provider
    if provider == "none" or f"{provider.upper()}_API_KEY" not in present:
        provider = "tiingo" if "TIINGO_API_KEY" in present else "polygon" if "POLYGON_API_KEY" in present else "none"
    config = QuantosConfig(
        contact_email=email.strip(),
        organization=organization.strip(),
        universe=universe,
        price_provider=provider,
        research_feeds=current.research_feeds,
        research_themes=current.research_themes,
        extra=current.extra,
    )
    if not config.contact_email:
        raise HomeError("a contact email is required: SEC EDGAR and Crossref refuse anonymous clients")
    path = save_config(config, home)
    (home / DATA_DIR).mkdir(parents=True, exist_ok=True)
    print(f"\nSaved {path}", file=out)
    print(f"Price provider: {config.price_provider}" + (" (keyless mode: fundamentals, macro, rates, FX and research only)" if config.price_provider == "none" else ""), file=out)
    print("Next: `quantos doctor`, then `quantos daily`.", file=out)
    return config
